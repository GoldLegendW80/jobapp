"""The company boards' part of a sweep (sweep.BrowserHook): every enabled board of the frozen
search, read through its ATS's public API (client.py), filtered (filters.py) and stored through
the run's commits (sweep.Commits), before the job websites, which then get the time left.

- Reading: the boards of one ATS one after the other, paced (client.py); different ATSs side by
  side. Storing: one board at a time, in the saved order, since the commits admit one source.
- Each board is one source of the sweep's stats: `mode` "api", `kind` "board", `host` the ATS's
  API host, `query` "<ats>:<board>" (also the stored job's `source`), `result_pages` the
  requests made, `listed` the postings the board lists, `left_out` the postings the filters left
  out by reason, then the store's `found`, `postings`, `duplicates`, `skipped_by_rule` and the
  `outcome` and `reason` of sweep.finish_source. A board not asked ends `not_attempted` with its
  reason: host_rate_limited (its ATS answered 429 earlier in the sweep), time_budget (the
  boards' share of the sweep, BUDGET_S, was used), no_titles (the search has no wanted title: a
  board is never read whole).
- The sweep's `seen` counts every posting listed, `matched` and the rest what was stored, as for
  a website; `invalid` the postings without a title or an address.
- A posting is stored with the board's company name, the ATS's date, the text the API gives,
  and in its `sweep` event's discovery the time the API was read (`detail_read_at`) and no page
  evidence.
"""

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.jobs import sweep
from app.jobs.boards import ats, catalog
from app.jobs.boards.client import PAUSE_S, BoardClient, Read
from app.jobs.boards.filters import BoardFilter
from app.profile.schemas import CompanyBoard

BUDGET_S = 300.0  # the longest the boards may take, out of the sweep's 900 s
DEADLINE_MARGIN_S = 30.0  # never closer than this to the sweep's own limit
BATCH = 50  # postings per commit


@dataclass(frozen=True)
class BoardFinding:
    """A board's posting as the store reads it (the fields of finder.VerifiedFinding)."""

    title: str
    location: str
    url: str
    text: str
    company: str | None
    published_on: str | None
    detail_read_at: str
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Batch:
    findings: tuple[BoardFinding, ...]


def label(board: CompanyBoard) -> str:
    return f"{board.ats}:{board.board}"


def entry_of(board: CompanyBoard) -> dict[str, Any]:
    """A board's stats entry: the one sweep.freeze_scope froze for it, bound when it runs."""
    return {
        "source_id": sweep._source_id("api", board.ats, board.board),
        "mode": "api",
        "kind": "board",
        "host": catalog.api_host(board.ats, board.board),
        "query": label(board),
    }


Hook = Callable[[Any, sweep.Stats, Callable[[], Awaitable[None]], float], Awaitable[None]]


def chain(*hooks: Hook | None) -> Hook:
    """One sweep hook running `hooks` in order (None skipped): the boards, then the websites."""

    async def run(
        plan: Any, stats: sweep.Stats, progress: Callable[[], Awaitable[None]], deadline: float
    ) -> None:
        for hook in hooks:
            if hook is not None:
                await hook(plan, stats, progress, deadline)

    return run


class Boards:
    """The hook. `transport`, `sleep` and `monotonic` replace the network and the clocks in
    tests."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        pause_s: float = PAUSE_S,
        budget_s: float = BUDGET_S,
    ) -> None:
        self.transport, self.sleep, self.monotonic = transport, sleep, monotonic
        self.pause_s, self.budget_s = pause_s, budget_s

    async def __call__(
        self,
        plan: Any,
        stats: sweep.Stats,
        progress: Callable[[], Awaitable[None]],
        deadline: float,
    ) -> None:
        search = getattr(plan, "search", None)
        boards = catalog.enabled_boards(search)
        commits = getattr(plan, "commits", None)
        if search is None or not boards or commits is None:
            return
        end = min(self.monotonic() + self.budget_s, deadline - DEADLINE_MARGIN_S)
        if not search.title_include.terms:
            for board in boards:
                await self._skip(board, stats, progress, "no_titles")
            return
        today = commits.clock().astimezone(sweep.SWEEP_TZ).date()
        wanted = BoardFilter.of(search, today)
        client = BoardClient(
            self.transport, pause_s=self.pause_s, sleep=self.sleep, monotonic=self.monotonic
        )
        async with client:
            reads, tasks = self._read_all(client, boards)
            try:
                stop: str | None = None
                for board, read in zip(boards, reads, strict=True):
                    if stop is not None:
                        await self._skip(board, stats, progress, stop)
                        continue
                    stop = await self._one(plan, board, read, wanted, stats, progress, end)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                for read in reads:
                    if read.done() and not read.cancelled():
                        read.exception()  # retrieved: a board never stored is not an error
                    else:
                        read.cancel()

    def _read_all(
        self, client: BoardClient, boards: list[CompanyBoard]
    ) -> tuple[list["asyncio.Future[Read]"], list["asyncio.Task[None]"]]:
        """One future per board, and the tasks that fill them: the boards of one ATS in order."""
        loop = asyncio.get_running_loop()
        futures: list[asyncio.Future[Read]] = [loop.create_future() for _ in boards]
        groups: dict[str, list[int]] = {}
        for index, board in enumerate(boards):
            groups.setdefault(ats.pace_key(board.ats), []).append(index)

        async def read_group(indexes: list[int]) -> None:
            for index in indexes:
                board = boards[index]
                try:
                    futures[index].set_result(await client.read(board.ats, board.board))
                except ats.BoardError as exc:
                    futures[index].set_exception(exc)
                except Exception:  # a bug on one board's answer ends that board only
                    futures[index].set_exception(ats.BoardError("invalid_response"))

        return futures, [loop.create_task(read_group(indexes)) for indexes in groups.values()]

    async def _skip(
        self, board: CompanyBoard, stats: sweep.Stats, progress: Callable[[], Awaitable[None]],
        reason: str,
    ) -> None:  # fmt: skip
        entry = sweep.bind_source(
            stats, "api", (board.ats, board.board), {**entry_of(board), "found": 0, "postings": 0}
        )
        entry["error"] = "not_started"
        sweep.finish_source(entry)
        entry["reason"] = reason
        await progress()

    async def _one(
        self,
        plan: Any,
        board: CompanyBoard,
        pending: "asyncio.Future[Read]",
        wanted: BoardFilter,
        stats: sweep.Stats,
        progress: Callable[[], Awaitable[None]],
        end: float,
    ) -> str | None:
        """Store one board; the reason every later board is not asked, if any."""
        commits = plan.commits
        entry = sweep.bind_source(
            stats,
            "api",
            (board.ats, board.board),
            {**entry_of(board), "result_pages": 0, "found": 0, "postings": 0, "error": None},
        )
        stats.current_board = entry["source_id"]
        await progress()
        left = end - self.monotonic()
        try:
            if left <= 0:
                raise TimeoutError
            read = await asyncio.wait_for(asyncio.shield(pending), left)
        except TimeoutError:
            entry["error"] = "not_started"
            sweep.finish_source(entry)
            entry["reason"] = "time_budget"
            await progress()
            return "time_budget"
        except ats.BoardError as exc:
            entry["error"] = exc.code
            sweep.finish_source(entry)
            if exc.code == "host_rate_limited":
                entry.update(outcome="not_attempted")
            await progress()
            return None
        entry["result_pages"] = read.requests
        entry["listed"] = len(read.parsed.postings)
        left_out: Counter[str] = Counter()
        kept: list[BoardFinding] = []
        at = commits.clock().isoformat()
        for posting in read.parsed.postings:
            reason = wanted.reason(posting)
            if reason is not None:
                left_out[reason] += 1
                continue
            kept.append(
                BoardFinding(
                    title=posting.title,
                    location=posting.location,
                    url=posting.url,
                    text=posting.text,
                    company=board.company,
                    published_on=posting.published or None,
                    detail_read_at=at,
                )
            )
        entry["left_out"] = dict(sorted(left_out.items()))
        # The store counts what it is given as seen and matched; the rest is counted here.
        stats.seen += len(read.parsed.postings) - len(kept)
        stats.invalid += read.parsed.invalid
        stop: str | None = None
        commits.admit(entry["source_id"])
        try:
            for first in range(0, len(kept), BATCH):
                await commits.save(
                    entry["source_id"], label(board), Batch(tuple(kept[first : first + BATCH]))
                )
        except sweep.StoreRefusedError:
            entry["error"] = "store_refused"
            stop = "store_refused"
        except Exception:
            entry["error"] = "store_failed"
        if not await commits.settle():
            entry["error"] = entry["error"] or "store_failed"
            stop = stop or "store_failed"
        sweep.finish_source(entry)
        await progress()
        return stop
