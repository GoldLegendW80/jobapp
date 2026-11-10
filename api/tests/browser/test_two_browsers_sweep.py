"""Two browsers at once (owner, 2026-11-06): the sweep's websites read in the browser run side by
side, as many as the person's browser has free slots (interactive.run_selected, `_Slots`). They
start in run order, each on its own lease naming its website; one the lease store makes wait
while another website of the sweep holds a lease asks again once that one ends; with none
holding one and someone else holding the browser it is `browser_busy` and the rest do not start
(test_b23_slot_wait.py: the browser being freed is asked again); a stop lets the running ones
end and keeps the waiting one from starting. The searches are stand ins that end
when the test says (no clock, no sleep).
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import sources
from app.browser.leases import Acquired, Lease, View, Waiting
from app.browser.sources import interactive
from app.jobs import sweep
from app.profile.schemas import SearchSection

USER = "00000000-0000-4000-8000-0000000000d1"
SITES = [f"https://jobs{n}.example.org/search" for n in range(1, 5)]


class SlotLeases:
    """The lease store as it answers one person's sweep: a lease per website while one of
    `slots` is free, else a place in the queue."""

    def __init__(self, slots: int) -> None:
        self.slots = slots
        self.held: dict[str, str | None] = {}
        self.asked: list[str | None] = []
        self.cancelled: list[str | None] = []
        self.most = 0

    async def acquire(
        self, user: str, purpose: str, automation: bool, *, hosts: Any = (), source_id: Any = None
    ) -> Acquired:
        self.asked.append(source_id)
        if len(self.held) >= self.slots:
            return Acquired(waiting=Waiting(purpose, 1, 60))
        lease_id = f"lease-{len(self.asked)}"
        self.held[lease_id] = source_id
        self.most = max(self.most, len(self.held))
        lease = Lease(
            lease_id=lease_id, user_id=user, purpose=purpose, automation=True, profile_id="p",
            max_s=86_400, idle_s=300, started_at=0.0, source_id=source_id,
        )  # fmt: skip
        return Acquired(lease=lease, token="invented-token")

    async def release(self, user: str, lease_id: Any = None, reason: str = "", **_: Any) -> bool:
        return self.held.pop(lease_id, None) is not None

    async def cancel(self, user: str, purposes: Any = None, key: Any = None) -> int:
        self.cancelled.append(key)
        return 1

    async def current(self, user: str) -> View:
        """With no slot at all, someone else holds the browser."""
        return View(lease=None, held_by_other=self.slots == 0, waiting=())


class Searches:
    """Each website's search waits until the test ends it with its code."""

    def __init__(self) -> None:
        self.started: asyncio.Queue[str] = asyncio.Queue()
        self.ends: dict[str, asyncio.Future[str | None]] = {}

    async def __call__(self, finder: Any, pages: Any, scope: Any, *_: Any) -> str | None:
        done = self.ends[scope.start] = asyncio.get_running_loop().create_future()
        await self.started.put(scope.start)
        return await done

    def end(self, url: str, code: str | None = None) -> None:
        self.ends[url].set_result(code)


def search_of(urls: list[str]) -> SearchSection:
    return SearchSection.model_validate(
        {
            "title_include": {"terms": ["robot"]},
            "interactive_sites": [
                {"platform": "custom", "url": url, "enabled": True, "mode": "llm_browser"}
                for url in urls
            ],
        }
    )


async def started(searches: Searches, task: asyncio.Task[None], entries: Any) -> str:
    """The next website whose search started once every task that can run ran: none fails the
    test (never a wait that could hang)."""
    await quiet()
    if searches.started.empty():
        raise AssertionError([entry.get("error") for entry in entries.values()])
    return searches.started.get_nowait()


async def quiet() -> None:
    """Lets every task that can run, run (no clock)."""
    for _ in range(200):
        await asyncio.sleep(0)


async def start(
    leases: SlotLeases, searches: Searches, urls: list[str]
) -> tuple[asyncio.Task[None], dict[str, dict[str, Any]]]:
    search = search_of(urls)
    scope = sweep.freeze_scope(
        SimpleNamespace(sections={"search": search.model_dump(mode="json")}, revision=3),
        sweep.SweepTrigger.manual,
        datetime.fromisoformat("2026-11-07T00:00:00+00:00"),
    )
    stats = sweep.Stats(sources=sweep.initial_sources(scope))
    finder = SimpleNamespace(available=_yes, profile=_nothing)
    runner = sources.Runner(leases, lambda *_: object(), finder=finder, monotonic=lambda: 0.0)
    plan = SimpleNamespace(search=search, user_id=USER, search_scope=scope, sweep_id=None)

    async def progress() -> None:
        pass

    task = asyncio.ensure_future(
        interactive.run_selected(runner, plan, stats, progress, 1000.0, None)
    )
    return task, {entry["source_id"]: entry for entry in stats.sources}


async def _yes(_: str) -> bool:
    return True


async def _nothing(_: str) -> str:
    return ""


def source_id(url: str) -> str:
    return sweep._source_id("llm_browser", url)


@pytest.mark.parametrize("slots", [1, 2])
def test_the_websites_run_side_by_side_up_to_the_free_slots(monkeypatch, slots: int) -> None:
    searches = Searches()
    monkeypatch.setattr(interactive, "_search", searches)

    async def go() -> None:
        leases = SlotLeases(slots)
        task, entries = await start(leases, searches, SITES)
        running = [await started(searches, task, entries) for _ in range(slots)]
        await quiet()
        assert running == SITES[:slots]  # in run order
        assert searches.started.empty()  # no more than the free slots
        assert sorted(leases.held.values()) == sorted(source_id(url) for url in running)
        for url in SITES:
            if url not in running:
                running.append(await started(searches, task, entries))
            await quiet()
            searches.end(url)
        await task
        assert running == SITES
        assert leases.most == slots
        assert leases.held == {}
        found = [entries[source_id(url)] for url in SITES]
        assert [entry["error"] for entry in found] == [None] * len(SITES)

    asyncio.run(go())


def test_a_website_that_waited_for_a_slot_leaves_its_place_and_asks_again(monkeypatch) -> None:
    searches = Searches()
    monkeypatch.setattr(interactive, "_search", searches)

    async def go() -> None:
        leases = SlotLeases(1)  # the person's fill holds the other slot
        task, entries = await start(leases, searches, SITES[:2])
        assert await started(searches, task, entries) == SITES[0]
        await quiet()
        assert leases.cancelled == [source_id(SITES[1])]  # its own place only
        searches.end(SITES[0])
        assert await started(searches, task, entries) == SITES[1]
        searches.end(SITES[1])
        await task
        assert leases.asked == [source_id(SITES[0]), source_id(SITES[1]), source_id(SITES[1])]

    asyncio.run(go())


def test_a_browser_held_by_someone_else_is_busy_and_the_rest_do_not_start(monkeypatch) -> None:
    searches = Searches()
    monkeypatch.setattr(interactive, "_search", searches)

    async def go() -> None:
        leases = SlotLeases(0)
        task, entries = await start(leases, searches, SITES[:3])
        await task
        errors = [entries[source_id(url)]["error"] for url in SITES[:3]]
        assert errors == ["browser_busy", "not_started", "not_started"]
        assert leases.asked == [source_id(SITES[0])]

    asyncio.run(go())


def test_a_stop_lets_the_running_website_end_and_the_waiting_one_never_starts(
    monkeypatch,
) -> None:
    searches = Searches()
    monkeypatch.setattr(interactive, "_search", searches)

    async def go() -> None:
        leases = SlotLeases(2)
        task, entries = await start(leases, searches, SITES)
        assert [await started(searches, task, entries) for _ in range(2)] == SITES[:2]
        await quiet()
        searches.end(SITES[0], "lease_ended")  # the person took their browser back
        await quiet()
        assert not task.done()  # the second website still runs
        searches.end(SITES[1])
        await task
        errors = [entries[source_id(url)]["error"] for url in SITES]
        assert errors == ["lease_ended", None, "not_started", "not_started"]
        assert searches.started.empty()

    asyncio.run(go())


def test_a_cancelled_sweep_leaves_no_website_running(monkeypatch) -> None:
    searches = Searches()
    monkeypatch.setattr(interactive, "_search", searches)

    async def go() -> None:
        leases = SlotLeases(2)
        task, entries = await start(leases, searches, SITES)
        assert [await started(searches, task, entries) for _ in range(2)] == SITES[:2]
        await quiet()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await quiet()
        assert leases.held == {}  # every lease given back
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert pending == []

    asyncio.run(go())
