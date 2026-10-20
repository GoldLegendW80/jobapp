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
   and for VIE one `browser_click` on "Show more offers"). Pacing is per host across people and
   leases (Pacer): before every page, the first of a lease included, the source waits until a
   pause drawn from PAGE_PAUSE_S (uniform) has passed since the previous page of that host,
   whoever opened it. The last page time of each host is kept in Redis (RedisPacer, one script,
   so two workers never take the same slot); a host idle for longer than the pause is opened at
   once. A page is not started when the lease's remaining active time cannot hold its wait plus
   PAGE_LOAD_S (`lease_budget`), so a source ends its lease itself instead of running it out
   and stopping the sweep's other sources. The residue: a page that takes longer than
   PAGE_LOAD_S (browserd's 30 s navigation limit plus the snapshot) could still cross the
   maximum; it then reports `lease_ended` as before.
3. Store. The postings go through the sweep's own store (sweep.store_postings: the search's
   filters, duplicates by URL or identity, status `found`, scored, never apply rules applied,
   the posting text saved), as source `<kind>:<query>`, in one transaction per source.
4. End. The lease is released whatever happened.

What a source reports in the sweep's `stats.sources` (one entry each, `error` None when it read
everything it was allowed to): `layout_changed` (a page did not have the structure the extractor
knows, or the reader failed in any other way: nothing of that source is stored, the sweep goes
on), `store_failed` (the store of the source's postings failed: nothing of it is stored, the
next source runs), `lease_budget` (the lease's remaining time could not hold another page: what
was read is stored, the next source runs on a lease of its own), `lease_ended` (the lease ended
under the source: a person asked for their browser, the one preemption of SPEC-browser "The
service", or a timer; what was read completely is stored, the sweep's other sources do not run,
and the next sweep starts again), `time_budget` (the sweep's time limit is near: what was read is
stored), `page_failed` (a page did not load), `browser_busy`, `browser_unavailable` (browserd or
the lease store did not answer, also in the middle of a source). A company career site (B9,
site.py) adds `bad_address`, `no_credential` (skipped before any lease), `ats_board` (the site's
postings sit on Ashby, Greenhouse or Lever: the entry's `board` names the API board to add
instead), `token_budget` and `agent_failed` (what the finder reported and code kept is stored).

BROWSER_SOURCES_BASE_URL (tests only, refused in production) points every source at a fixture
server instead of the real host (tests/browser_pages/b8/).

Declared open point: browserd cuts a snapshot at 40,000 characters (browserd/tools/policy.py);
a Google results page of 20 cards can be longer, and the cards past the cut are not read (the
next page is still read). A snapshot of one element (`target`) would close it.
"""

import asyncio
import contextlib
import logging
import math
import random
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import SplitResult, urlsplit

from redis.exceptions import RedisError

from app.browser.client import BrowserError
from app.browser.leases import Leases, StoreUnavailableError
from app.jobs import ats
from app.profile.schemas import BrowserSource

logger = logging.getLogger("app.browser.sources")

RESULT_PAGES_MAX = 10  # result pages per source per sweep (tasks/browser/todo.md, B8)
# Posting pages per source per sweep. The sweep lease's 900 s of active time binds first: a page
# costs its pause (5 to 15 s) plus its load (PAGE_LOAD_S at worst, about 3 s usually), so a lease
# holds 15 pages at worst and about 60 usually; Pages stops before the lease runs out.
POSTING_PAGES_MAX = 40
PAGE_PAUSE_S = (5.0, 15.0)
PAGE_BUDGET_S = 60.0  # a page is not started this close to the sweep's time limit
# What one page may take once started: browserd's 30 s navigation, the snapshot, a margin.
PAGE_LOAD_S = 45.0
PACE_TTL_S = 3600  # a host's last page time is forgotten after an hour without a page
TEXT_MAX = 200_000  # sweep.POSTING_TEXT_MAX

ERRORS = (
    "layout_changed",
    "store_failed",
    "lease_budget",
    "lease_ended",
    "time_budget",
    "page_failed",
    "browser_busy",
    "browser_unavailable",
    # Company career sites (B9, site.py).
    "bad_address",
    "no_credential",
    "ats_board",
    "token_budget",
    "agent_failed",
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


def same_title(heading: str, title: str) -> bool:
    """Whether a posting page's heading is the card's title: equal once both are NFKC normalised,
    casefolded and their whitespace collapsed. An empty title is never one."""

    def norm(text: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", text).casefold().split())

    return bool(norm(title)) and norm(heading) == norm(title)


def same_site(parts: SplitResult, page_url: str) -> bool:
    """Whether a link (split) is on the page's own site: same scheme, host and port, no user."""
    page = urlsplit(page_url)
    try:
        ports = parts.port, page.port
    except ValueError:
        return False
    return (
        parts.username is None
        and parts.password is None
        and bool(parts.hostname)
        and (parts.scheme, parts.hostname, ports[0]) == (page.scheme, page.hostname, ports[1])
    )


def same_page(opened: str, wanted: str) -> bool:
    """Whether the page the browser ended on (after redirects) is the card's address: same scheme,
    host (case ignored) and path, a trailing slash and the query ignored, as the parsers drop the
    query. An empty address is never the page."""
    a, b = urlsplit(opened), urlsplit(wanted)
    if not a.scheme or not a.netloc:
        return False

    def key(parts: SplitResult) -> tuple[str, str, str]:
        return parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/")

    return key(a) == key(b)


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


class Pacer(Protocol):
    async def reserve(self, host: str, pause: float) -> float:
        """Takes the host's next page slot, `pause` after its previous page (now when that is
        past), and answers the seconds to wait for it."""
        ...


class MemoryPacer:
    """The pacer of one process (tests, a runner without Redis): RedisPacer's rule in memory."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.last: dict[str, float] = {}

    async def reserve(self, host: str, pause: float) -> float:
        now = self.clock()
        start = max(now, self.last.get(host, -math.inf) + pause)
        self.last[host] = start
        return start - now


PACE = """
local last = tonumber(redis.call('GET', KEYS[1]) or '')
local now = tonumber(ARGV[1])
local start = now
if last ~= nil and last + tonumber(ARGV[2]) > start then start = last + tonumber(ARGV[2]) end
redis.call('SET', KEYS[1], string.format('%.3f', start), 'EX', tonumber(ARGV[3]))
return string.format('%.3f', start)
"""
PACE_KEY = "jobapp:browser:pace:"


class RedisPacer:
    """The pace per host across people, leases and workers: the host's last page time in Redis,
    read and moved by one script. Raises StoreUnavailableError without Redis."""

    def __init__(
        self, pool: Callable[[], Awaitable[Any]], clock: Callable[[], float] = time.time
    ) -> None:
        self.pool, self.clock = pool, clock

    async def reserve(self, host: str, pause: float) -> float:
        now = self.clock()
        try:
            r = await self.pool()
            start = await r.eval(PACE, 1, PACE_KEY + host, f"{now:.3f}", f"{pause:.3f}", PACE_TTL_S)
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None
        raw = start.decode() if isinstance(start, bytes) else str(start)
        return max(0.0, float(raw) - now)


class Pages:
    """One lease's pages: paced per host, within the sweep's deadline and the lease's maximum, a
    lease that ended is SourceError."""

    def __init__(
        self,
        tools: Tools,
        deadline: float,
        *,
        sleep: Sleep,
        rng: random.Random,
        monotonic: Callable[[], float],
        host: str = "",
        pacer: Pacer | None = None,
        lease_until: float = math.inf,
    ) -> None:
        self.tools, self.deadline = tools, deadline
        self.sleep, self.rng, self.monotonic = sleep, rng, monotonic
        self.host, self.pacer = host, pacer or MemoryPacer(monotonic)
        self.lease_until = lease_until  # monotonic time at which the lease's active time ends
        self.count = 0
        self.pauses: list[float] = []
        # The last page's snapshot text and address (the finder agent of B9 reads them).
        self.last_text, self.last_url = "", ""

    async def _act(self, action: Callable[[], Awaitable[dict[str, Any]]]) -> tuple[Node, str]:
        if self.monotonic() + PAGE_BUDGET_S >= self.deadline:
            raise SourceError("time_budget")
        wait = await self.pacer.reserve(self.host, self.rng.uniform(*PAGE_PAUSE_S))
        if self.monotonic() + wait + PAGE_LOAD_S > self.lease_until:
            raise SourceError("lease_budget")
        self.pauses.append(wait)
        if wait > 0:
            await self.sleep(wait)
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
        self.last_text, self.last_url = shot["snapshot"], str(shot.get("url") or "")
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
    user_id: str = ""
    # What the source adds to its entry in the sweep's stats (B9: the board, the refusals).
    extra: dict[str, Any] = field(default_factory=dict)


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
        pacer: Pacer | None = None,
        finder: Any = None,
    ) -> None:
        self.leases, self.tools_for, self.base_url = leases, tools_for, base_url
        self.finder = finder  # B9: the finder agent of `site` sources (finder.FinderAgent)
        self.sleep, self.rng = sleep, rng or random.SystemRandom()
        self.monotonic = monotonic
        self.pacer = pacer or MemoryPacer()

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
                "postings": 0,
                "error": stop,
            }
            stats.sources.append(entry)
            if stop is not None:
                continue
            stats.current_board = source_name(source)
            await progress()
            entry["error"], ended = await self._one(plan, stats, source, entry, store, deadline)
            if ended in STOPS_SWEEP:
                stop = ended
            await progress()

    async def _one(
        self,
        plan: Any,
        stats: Any,
        source: BrowserSource,
        entry: dict[str, Any],
        store: StoreFound,
        deadline: float,
    ) -> tuple[str | None, str | None]:
        """The code the source reports, and the code of how its read ended (they differ only when
        the store failed after a read that ended the lease: the sweep's other sources then still
        do not start)."""
        user = str(plan.user_id)
        if source.kind == "site":
            from app.browser.sources import site

            reader: Reader = site.Site(self.finder)
            host, skip = await reader.check(source, user, entry)  # type: ignore[attr-defined]
            if skip is not None:
                return skip, None
        else:
            reader = readers()[source.kind]
            host = reader.host(self.base_url)
        asked = self.monotonic()  # the lease's active time counts from its start, after this
        try:
            got = await self.leases.acquire(user, "sweep", automation=True, hosts=[host])
        except BrowserError:
            return "browser_unavailable", "browser_unavailable"
        except StoreUnavailableError:
            return "browser_unavailable", "browser_unavailable"
        if got.lease is None or got.token is None:
            # Waiting, or a sweep lease of this person already held by another run: never wait.
            await self._give_back(user, None if got.lease is None else "")
            return "browser_busy", "browser_busy"
        lease_id = got.lease.lease_id
        pages = Pages(
            self.tools_for(user, lease_id, got.token),
            deadline,
            sleep=self.sleep,
            rng=self.rng,
            monotonic=self.monotonic,
            host=host,
            pacer=self.pacer,
            lease_until=asked + got.lease.max_s,
        )
        read = Read(user_id=user)
        code: str | None = None

        def wanted(card: Card) -> bool:
            # The posting pages opened so far: every page of the lease but the result pages
            # (a skipped posting's page counts too).
            return self._wanted(plan, card, pages.count - read.result_pages)

        try:
            await reader.read(pages, source, wanted, read, self.base_url)
        except SourceError as exc:
            code = exc.code
        except StoreUnavailableError:
            code = "browser_unavailable"  # the lease store went away under the source
        except Exception as exc:
            # A reader's failure on a page it did not expect: fail closed, store nothing.
            logger.warning(
                "browser source failed: kind=%s error=%s", source.kind, type(exc).__name__
            )
            code = "layout_changed"
        finally:
            entry["result_pages"] = read.result_pages
            entry.update(read.extra)
            try:
                await self.leases.release(user, lease_id, reason="sweep_done")
            except StoreUnavailableError:
                logger.warning("a sweep lease could not be released: the store is unavailable")
        if code == "layout_changed":
            logger.warning("browser source layout changed: kind=%s", source.kind)
            return code, code
        if read.postings:
            new_before = stats.new
            try:
                await store(source_name(source), read.postings)
            except Exception as exc:
                # sweep._store_found puts the stats and the known postings back: nothing stored.
                logger.warning("browser source not stored: error=%s", type(exc).__name__)
                return "store_failed", code
            entry["postings"] = stats.new - new_before
        return code, code

    async def _give_back(self, user: str, held: str | None) -> None:
        if held is None:
            with contextlib.suppress(StoreUnavailableError):
                await self.leases.cancel(user, ["sweep"])

    @staticmethod
    def _wanted(plan: Any, card: Card, opened: int) -> bool:
        """Whether the posting page is worth a visit: the person does not have it yet, and the
        page budget is not spent. The search's filters decide again at the store."""
        if opened >= POSTING_PAGES_MAX:
            return False
        from app.jobs import slug as slugs

        return not plan.known.has(
            slugs.PostingKey.of(card.company, card.title, card.location, card.url)
        )


def runner_for(settings: Any, redis: Any, sessions: Any, agent_context: Any = None) -> Runner:
    """The hook as the worker runs it: the lease store and the tools of B3 and B5 on its Redis,
    and the finder agent of B9 on the runner's context (`agent_context`, worker.agent_context)."""
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
        pacer=RedisPacer(pool),
        finder=None if agent_context is None else _finder(agent_context),
    )


def _finder(agent_context: Any) -> Any:
    from app.browser import finder

    return finder.RuntimeFinder(agent_context)
