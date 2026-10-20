"""Browser sources of the sweep (browser B8, SPEC-browser "The second use"): job sites without an
API, read in the person's own browser by deterministic extractors, no model.

After the boards, `run_sources` (the hook of app/jobs/sweep.py's `run_sweep`) takes each enabled
source of the person's `search.browser_sources` in turn:
1. Lease. A `sweep` lease of the person's browser (app/browser/leases.py), opening only the
   source's host. A browser in use (another lease holds it) is not waited for: the place in the
   queue is given back and the source reports `browser_busy`; it runs at the next sweep.
2. Read. The source's module (google_careers.py, vie.py) reads at most RESULT_PAGES_MAX result
   pages and the posting page of each posting the search keeps and the person does not have yet
   (POSTING_PAGES_MAX at most), through the tools of B5 (`browser_navigate`, `browser_snapshot`,
   and for VIE one `browser_click` on "Show more offers"). Every page but the first of a lease
   waits PAGE_PAUSE_S first (uniform, injected sleep and random source): with one browser for
   everyone, one lease at a time, that is the pace per host across people too.
3. Store. The postings go through the sweep's own store (sweep.store_postings: the search's
   filters, duplicates by URL or identity, status `found`, scored, never apply rules applied,
   the posting text saved), as source `<kind>:<query>`, in one transaction per source.
4. End. The lease is released whatever happened.

What a source reports in the sweep's `stats.sources` (one entry each, `error` None when it read
everything it was allowed to): `layout_changed` (a page did not have the structure the extractor
knows: nothing of that source is stored, the sweep goes on), `lease_ended` (the lease ended under
the source: a person asked for their browser, the one preemption of SPEC-browser "The service",
or a timer; what was read completely is stored, the sweep's other sources do not run, and the
next sweep starts again), `time_budget` (the sweep's time limit is near: what was read is
stored), `page_failed` (a page did not load), `browser_busy`, `browser_unavailable`.

BROWSER_SOURCES_BASE_URL (tests only, refused in production) points every source at a fixture
server instead of the real host (tests/browser_pages/b8/).

Declared open point: browserd cuts a snapshot at 40,000 characters (browserd/tools/policy.py);
a Google results page of 20 cards can be longer, and the cards past the cut are not read (the
next page is still read). A snapshot of one element (`target`) would close it.
"""

import asyncio
import contextlib
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.browser.client import BrowserError
from app.browser.leases import Leases, StoreUnavailableError
from app.jobs import ats
from app.profile.schemas import BrowserSource

logger = logging.getLogger("app.browser.sources")

RESULT_PAGES_MAX = 10  # result pages per source per sweep (tasks/browser/todo.md, B8)
POSTING_PAGES_MAX = 40  # posting pages per source per sweep: 40 pages of 5 to 15 s fit a lease
PAGE_PAUSE_S = (5.0, 15.0)
PAGE_BUDGET_S = 60.0  # a page is not started this close to the sweep's time limit
TEXT_MAX = 200_000  # sweep.POSTING_TEXT_MAX

ERRORS = (
    "layout_changed",
    "lease_ended",
    "time_budget",
    "page_failed",
    "browser_busy",
    "browser_unavailable",
)
# The codes after which no other source of this sweep starts: the browser is wanted elsewhere.
STOPS_SWEEP = ("lease_ended", "browser_busy", "browser_unavailable")


class SourceError(Exception):
    def __init__(self, code: str) -> None:
        if code not in ERRORS:
            raise ValueError(code)
        super().__init__(code)
        self.code = code


# --- the snapshot ---------------------------------------------------------------------------------


@dataclass
class Node:
    """One element of a Playwright MCP snapshot (`- role "name" [attr] [ref=e1]: text`)."""

    role: str
    name: str = ""
    attrs: frozenset[str] = frozenset()
    text: str = ""
    url: str = ""
    ref: str = ""
    children: list["Node"] = field(default_factory=list)

    @property
    def hidden(self) -> bool:
        return "aria-hidden" in self.attrs

    def level(self) -> int | None:
        for attr in self.attrs:
            if attr.startswith("level="):
                return int(attr[6:]) if attr[6:].isdigit() else None
        return None

    def label(self) -> str:
        """The name, else the text."""
        return self.name or self.text

    def walk(self) -> "list[Node]":
        """This node and every node below it, in document order (iterative: no recursion limit)."""
        found: list[Node] = []
        stack = [self]
        while stack:
            node = stack.pop()
            found.append(node)
            stack.extend(reversed(node.children))
        return found

    def find(self, role: str, name: str | None = None) -> "Node | None":
        for node in self.walk():
            if node.role == role and (name is None or node.label() == name):
                return node
        return None


_LINE = re.compile(
    r'(?P<role>[a-z]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?: \[[^\]]*\])*)'
    r"(?::(?: (?P<text>.*))?)?"
)
_ATTR = re.compile(r"\[([^\]]*)\]")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse_snapshot(text: str) -> Node:
    """The tree of a snapshot. Lines it does not understand are left out (the extractors then
    find the structure missing and answer layout_changed). Linear in the text's size."""
    root = Node(role="root")
    stack: list[tuple[int, Node]] = [(-1, root)]
    for raw in text.splitlines():
        stripped = raw.lstrip(" ")
        if not stripped.startswith("- "):
            continue
        indent = len(raw) - len(stripped)
        body = stripped[2:].rstrip()
        if body.startswith("'") and (body.endswith("'") or body.endswith("':")):
            colon = body.endswith("':")
            body = body[1 : -2 if colon else -1].replace("''", "'") + (":" if colon else "")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if body.startswith("/url:"):
            parent.url = _unquote(body[5:])
            continue
        if body.startswith("text:"):
            parent.children.append(Node(role="text", text=_unquote(body[5:])))
            continue
        match = _LINE.fullmatch(body)
        if match is None:
            continue
        attrs = frozenset(_ATTR.findall(match["attrs"] or ""))
        ref = next((a[4:] for a in attrs if a.startswith("ref=")), "")
        name = re.sub(r"\\(.)", r"\1", match["name"] or "")
        node = Node(
            role=match["role"],
            name=name,
            attrs=attrs,
            text=_unquote(match["text"] or ""),
            ref=ref,
        )
        parent.children.append(node)
        stack.append((indent, node))
    return root


SKIPPED_ROLES = frozenset({"button", "tooltip", "img", "figure", "switch", "combobox", "option"})


def visible_lines(node: Node, stop: Callable[[Node], bool] = lambda n: False) -> list[str]:
    """The text a reader sees under `node`, one line per element, without hidden icons, buttons,
    tooltips and images; reading ends at the first node `stop` accepts."""
    lines: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if stop(current):
            break
        if current.hidden or current.role in SKIPPED_ROLES:
            continue
        line = current.label().strip()
        if line and current.role != "link":
            lines.append(line)
        stack.extend(reversed(current.children))
    return lines


def own_texts(node: Node) -> list[str]:
    """The visible texts of `node` and of its direct children, in order."""
    found = [node.label()] if node.label() and not node.hidden else []
    found += [c.label() for c in node.children if c.label() and not c.hidden]
    return [text.strip() for text in found if text.strip()]


# --- what an extractor answers ----------------------------------------------------------------


@dataclass(frozen=True)
class Card:
    """A posting as a result page shows it."""

    title: str
    company: str
    location: str
    url: str


def posting(card: Card, text: str, published: str) -> ats.Posting:
    return ats.Posting(
        title=ats.storable(card.title),
        location=ats.storable(card.location),
        url=card.url,
        published=published,
        text=ats.storable(text)[:TEXT_MAX],
        remote=False,
    )


# --- running the sources -------------------------------------------------------------------------


class Tools(Protocol):
    """What a source calls: app/browser/tools.py's LeaseTools."""

    async def navigate(self, url: str) -> dict[str, Any]: ...

    async def snapshot(self) -> dict[str, Any]: ...

    async def click(self, ref: str, element: str = "") -> dict[str, Any]: ...


Sleep = Callable[[float], Awaitable[None]]


class Pages:
    """One lease's pages: paced, within the sweep's deadline, a lease that ended is SourceError."""

    def __init__(
        self,
        tools: Tools,
        deadline: float,
        *,
        sleep: Sleep,
        rng: random.Random,
        monotonic: Callable[[], float],
    ) -> None:
        self.tools, self.deadline = tools, deadline
        self.sleep, self.rng, self.monotonic = sleep, rng, monotonic
        self.count = 0
        self.pauses: list[float] = []

    async def _act(self, action: Callable[[], Awaitable[dict[str, Any]]]) -> tuple[Node, str]:
        if self.monotonic() + PAGE_BUDGET_S >= self.deadline:
            raise SourceError("time_budget")
        if self.count:
            pause = self.rng.uniform(*PAGE_PAUSE_S)
            self.pauses.append(pause)
            await self.sleep(pause)
        self.count += 1
        try:
            done = await action()
            if not done.get("ok"):
                raise SourceError("page_failed")
            shot = await self.tools.snapshot()
        except BrowserError as exc:
            raise SourceError("lease_ended" if exc.code == "ended" else "page_failed") from None
        if not shot.get("ok") or not isinstance(shot.get("snapshot"), str):
            raise SourceError("page_failed")
        return parse_snapshot(shot["snapshot"]), str(shot.get("url") or "")

    async def open(self, url: str) -> tuple[Node, str]:
        return await self._act(lambda: self.tools.navigate(url))

    async def click(self, ref: str, element: str) -> tuple[Node, str]:
        return await self._act(lambda: self.tools.click(ref, element))


Wanted = Callable[[Card], bool]


@dataclass
class Read:
    """What one source read: the postings complete so far, the result pages read."""

    postings: list[tuple[str, ats.Posting]] = field(default_factory=list)
    result_pages: int = 0


class Reader(Protocol):
    def host(self, base_url: str | None) -> str: ...

    async def read(
        self, pages: Pages, source: BrowserSource, wanted: Wanted, read: Read, base: str | None
    ) -> None: ...


def readers() -> dict[str, Reader]:
    from app.browser.sources import google_careers, vie

    return {"google_careers": google_careers.READER, "vie": vie.READER}


def source_name(source: BrowserSource) -> str:
    """The jobs' `source`: "<kind>:<query>", and the location after a slash when there is one."""
    name = f"{source.kind}:{source.query}"
    return f"{name}/{source.location}" if source.location else name


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""


StoreFound = Callable[[str, Sequence[tuple[str, ats.Posting]]], Awaitable[None]]
ToolsFor = Callable[[str, str, str], Tools]


class Runner:
    """The sweep's hook (sweep.BrowserHook)."""

    def __init__(
        self,
        leases: Leases,
        tools_for: ToolsFor,
        *,
        base_url: str | None = None,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.leases, self.tools_for, self.base_url = leases, tools_for, base_url
        self.sleep, self.rng = sleep, rng or random.SystemRandom()
        self.monotonic = monotonic

    async def __call__(
        self,
        plan: Any,
        stats: Any,
        store: StoreFound,
        progress: Callable[[], Awaitable[None]],
        deadline: float,
    ) -> None:
        stop: str | None = None
        for source in plan.sources:
            entry: dict[str, Any] = {
                "kind": source.kind,
                "query": source.query,
                "location": source.location,
                "result_pages": 0,
                "found": 0,
                "postings": 0,
                "error": stop,
            }
            stats.sources.append(entry)
            if stop is not None:
                continue
            stats.current_board = source_name(source)
            await progress()
            entry["error"] = await self._one(plan, stats, source, entry, store, deadline)
            if entry["error"] in STOPS_SWEEP:
                stop = entry["error"]
            await progress()

    async def _one(
        self,
        plan: Any,
        stats: Any,
        source: BrowserSource,
        entry: dict[str, Any],
        store: StoreFound,
        deadline: float,
    ) -> str | None:
        reader = readers()[source.kind]
        user = str(plan.user_id)
        try:
            got = await self.leases.acquire(
                user, "sweep", automation=True, hosts=[reader.host(self.base_url)]
            )
        except BrowserError:
            return "browser_unavailable"
        except StoreUnavailableError:
            return "browser_unavailable"
        if got.lease is None or got.token is None:
            # Waiting, or a sweep lease of this person already held by another run: never wait.
            await self._give_back(user, None if got.lease is None else "")
            return "browser_busy"
        lease_id = got.lease.lease_id
        pages = Pages(
            self.tools_for(user, lease_id, got.token),
            deadline,
            sleep=self.sleep,
            rng=self.rng,
            monotonic=self.monotonic,
        )
        read = Read()
        code: str | None = None

        def wanted(card: Card) -> bool:
            return self._wanted(plan, card, len(read.postings))

        try:
            await reader.read(pages, source, wanted, read, self.base_url)
        except SourceError as exc:
            code = exc.code
        finally:
            entry["result_pages"] = read.result_pages
            try:
                await self.leases.release(user, lease_id, reason="sweep_done")
            except StoreUnavailableError:
                logger.warning("a sweep lease could not be released: the store is unavailable")
        if code == "layout_changed":
            logger.warning("browser source layout changed: kind=%s", source.kind)
            return code
        entry["found"] = len(read.postings)
        if read.postings:
            new_before = stats.new
            await store(source_name(source), read.postings)
            entry["postings"] = stats.new - new_before
        return code

    async def _give_back(self, user: str, held: str | None) -> None:
        if held is None:
            with contextlib.suppress(StoreUnavailableError):
                await self.leases.cancel(user, ["sweep"])

    @staticmethod
    def _wanted(plan: Any, card: Card, read_so_far: int) -> bool:
        """Whether the posting page is worth a visit: the person does not have it yet, and the
        page budget is not spent. The search's filters decide again at the store."""
        if read_so_far >= POSTING_PAGES_MAX:
            return False
        from app.jobs import slug as slugs

        return not plan.known.has(
            slugs.PostingKey.of(card.company, card.title, card.location, card.url)
        )


def runner_for(settings: Any, redis: Any, sessions: Any) -> Runner:
    """The hook as the worker runs it: the lease store and the tools of B3 and B5 on its Redis."""
    from app.browser.client import BrowserdClient
    from app.browser.leases import DbProfiles, lease_max_table
    from app.browser.tools import LeaseTools

    async def pool() -> Any:
        return redis

    secret = settings.browser_control_secret
    client = BrowserdClient(
        settings.browser_url, secret.get_secret_value() if secret is not None else None
    )
    leases = Leases(
        pool,
        client,
        DbProfiles(sessions),
        idle_s=settings.browser_idle_s,
        session_max_s=settings.browser_session_max_s,
        max_table=lease_max_table(settings.browser_lease_max_s),
    )
    return Runner(
        leases,
        lambda user, lease, token: LeaseTools(leases, client, user, lease, token),
        base_url=settings.browser_sources_base_url,
    )
