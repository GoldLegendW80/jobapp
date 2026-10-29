"""The job websites of the sweep (SPEC-browser "The second use", jobs J9 to J18): the websites
the person ticked in Find jobs, each searched by the interactive finder in the person's browser
(app/browser/sources/interactive.py, `run_selected`).

`Runner` is the hook of app/jobs/sweep.py's `run_sweep`: it holds what a website's search needs
(the lease store, browserd's tools, the pacer, the finder agent, the platform policy and the
websites the person signed in to) and hands over to `run_selected`, which takes a `sweep` lease
of the person's browser per website, paces its pages and stores the verified findings through
the run's commits (sweep.Commits, jobs J11).

This module also holds what the website search, the finder and Apply share: the snapshot parser
(`parse_snapshot`, `Node`, `visible_lines`), the page budget (`Pages`) and the pacing per host
across people and leases (`Pacer`): before every page, the first of a lease included, the
search waits until a pause drawn from PAGE_PAUSE_S (uniform, a platform's own range on its key,
platform_policy.pause_for and pace_key) has passed since the previous page of that host ended,
whoever opened it. A page ends when browserd's calls for it (the action, then its snapshot) have
returned, and browserd sends the page's request before its call returns, after its own dwell of
1 to 3 s (and a hover before a click): so the website receives two pages of a host at least the
pause apart (tests/browser/test_pace_received.py). Counting the pause between the starts of the
calls, as it was until 2026-10-29, let browserd's dwell eat into it: the website once saw 3.56 s.
One page of a host runs at a time: while another page of the host has not ended (a page that
outlives its lease, a preempted sweep's, or a concurrent tool call of one finder session), a page
waits (PACE_POLL_S between asks) and draws its slot from that page's end; it gives up
(`lease_budget`) before its own lease would end idle (`lease_ended` would stop the sweep, a wait
that outlasts the lease ends this website only). The last page time and the page that holds each
host are kept in Redis (RedisPacer, one script each to take and give back, so two workers never
take the same slot). A page gives its host back when it ends, a failed or a cancelled one too,
a cancel in the store's reply or in the release included (tests/browser/test_pace_received.py);
a refused release is asked once more, and a hold still refused is given back by the same
lease's next page (`Pages.unreleased`); a hold a dead worker left ends after PACE_HOLD_S. A host
idle for longer than the pause is opened at once. A page is not started when the lease's remaining
active time cannot hold its wait plus PAGE_LOAD_S (`lease_budget`), nor this close to the sweep's
time limit (PAGE_BUDGET_S, `time_budget`).

The fixed readers of Google Careers, Business France VIE, company career pages (`site`) and
LinkedIn's own reader were deleted with the company boards and the freelance feeds (owner,
2026-10-28: "Delete it"). A sweep row they wrote keeps their entries and codes in its stored
`stats.sources`, and the history reads them as stored.

BROWSER_SOURCES_BASE_URL (tests only, refused in production) points every website at a fixture
server instead of the real host (tests/browser_pages/).
"""

import asyncio
import contextlib
import logging
import math
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from redis.exceptions import RedisError

from app.browser.client import BrowserError
from app.browser.leases import Leases, StoreUnavailableError

logger = logging.getLogger("app.browser.sources")

if TYPE_CHECKING:
    from app.browser.sources.interactive import InteractivePolicy

PAGE_PAUSE_S = (5.0, 15.0)
PAGE_BUDGET_S = 60.0  # a page is not started this close to the sweep's time limit
# What one page may take once started: browserd's 30 s navigation, the snapshot, a margin.
PAGE_LOAD_S = 45.0
PACE_TTL_S = 3600  # a host's last page time is forgotten after an hour without a page
PACE_POLL_S = 1.0  # how often a page asks again while another page of its host has not ended
# The longest a website's search runs, its wait for the pace included (SPEC-browser: the sweep's
# outer limit of 900 s, jobs J18): interactive._search cancels a search at this age of its lease.
WEBSITE_CAP_S = 900
# How long a page may hold its host in Redis. A live worker's page is cancelled at the latest
# when its website's search is (WEBSITE_CAP_S from the lease's start, the wait included), and a
# cancelled page gives its host back, so the hold needs that cap plus a margin for the release
# and the clocks. What the hold covers is a page that outlives its lease, concurrent tool calls
# of one finder session, and a cancel that lands in the store's reply (the hold taken, the page
# never told). A page gives the host back whether it ends, fails or is cancelled, so a hold
# stays when a worker died with its page, Redis refused the release twice, or a second cancel
# landed in the release itself: then for this long.
PACE_HOLD_S = WEBSITE_CAP_S + 100.0

ERRORS = (
    "layout_changed",
    "store_failed",
    "lease_budget",
    "lease_ended",
    "time_budget",
    "page_failed",
    "browser_busy",
    "browser_unavailable",
    # The finder agent of a website (app/browser/finder.py); `bad_address` was the career page
    # reader's, kept so a code stored on an old row stays a known one.
    "bad_address",
    "no_credential",
    "token_budget",
    "agent_failed",
    "permission_unavailable",
    "permission_checkpoint",
    "permission_revoked",
    # The job websites' platform guard (browser B14, app/browser/platform_policy.py).
    "page_budget",
    "platform_daily",
    "platform_signin",
    # LinkedIn (browser B10, app/browser/linkedin_guard.py)
    "linkedin_off",
    "linkedin_paused",
    "linkedin_daily",
    "linkedin_signin",
    "linkedin_checkpoint",
)
# The codes after which no other source of this sweep starts: the browser is wanted elsewhere.
STOPS_SWEEP = ("lease_ended", "browser_busy", "browser_unavailable")
# What a source reports when an earlier source stopped the sweep's sources before it began: it
# never asked for the browser and read nothing (never raised, so not in ERRORS).
NOT_STARTED = "not_started"


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


# --- running the websites ----------------------------------------------------------------------


class Tools(Protocol):
    """What a source calls: app/browser/tools.py's LeaseTools."""

    async def navigate(self, url: str) -> dict[str, Any]: ...

    async def snapshot(self) -> dict[str, Any]: ...

    async def click(self, ref: str, element: str = "") -> dict[str, Any]: ...


Sleep = Callable[[float], Awaitable[None]]


class Pacer(Protocol):
    async def reserve(self, host: str, pause: float, holder: str) -> float | None:
        """Takes the host's next page slot for `holder`, `pause` after its previous page ended
        (now when that is past), and holds the host until `release`: answers the seconds to
        wait for the slot, or None while another holder's page of the host has not ended."""
        ...

    async def release(self, host: str, holder: str) -> None:
        """The holder's page ended: the host's next pause counts from now."""
        ...


class MemoryPacer:
    """The pacer of one process (tests, a runner without Redis): RedisPacer's rule in memory."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.last: dict[str, float] = {}
        self.holders: dict[str, str] = {}

    async def reserve(self, host: str, pause: float, holder: str) -> float | None:
        if self.holders.get(host, holder) != holder:
            return None
        now = self.clock()
        start = max(now, self.last.get(host, -math.inf) + pause)
        self.last[host], self.holders[host] = start, holder
        return start - now

    async def release(self, host: str, holder: str) -> None:
        self.last[host] = max(self.last.get(host, -math.inf), self.clock())
        if self.holders.get(host) == holder:
            del self.holders[host]


# KEYS: the host's last page time, the host's holder. ARGV: now, pause, PACE_TTL_S, the holder,
# PACE_HOLD_S. A nil answer: another holder's page of the host has not ended.
PACE = """
local holder = redis.call('GET', KEYS[2])
if holder and holder ~= ARGV[4] then return false end
local last = tonumber(redis.call('GET', KEYS[1]) or '')
local now = tonumber(ARGV[1])
local start = now
if last ~= nil and last + tonumber(ARGV[2]) > start then start = last + tonumber(ARGV[2]) end
redis.call('SET', KEYS[1], string.format('%.3f', start), 'EX', tonumber(ARGV[3]))
redis.call('SET', KEYS[2], ARGV[4], 'EX', tonumber(ARGV[5]))
return string.format('%.3f', start)
"""
# KEYS as PACE's. ARGV: now, PACE_TTL_S, the holder.
RELEASE = """
local last = tonumber(redis.call('GET', KEYS[1]) or '')
local now = tonumber(ARGV[1])
if last == nil or last < now then
  redis.call('SET', KEYS[1], string.format('%.3f', now), 'EX', tonumber(ARGV[2]))
end
if redis.call('GET', KEYS[2]) == ARGV[3] then redis.call('DEL', KEYS[2]) end
return 1
"""
PACE_KEY = "jobapp:browser:pace:"
HOLD_KEY = "jobapp:browser:pace-hold:"


class RedisPacer:
    """The pace per host across people, leases and workers: the host's last page time and the
    page that holds it in Redis, read and moved by one script. Raises StoreUnavailableError
    without Redis."""

    def __init__(
        self, pool: Callable[[], Awaitable[Any]], clock: Callable[[], float] = time.time
    ) -> None:
        self.pool, self.clock = pool, clock

    async def reserve(self, host: str, pause: float, holder: str) -> float | None:
        now = self.clock()
        start = await self._eval(
            PACE,
            host,
            f"{now:.3f}",
            f"{pause:.3f}",
            PACE_TTL_S,
            holder,
            math.ceil(PACE_HOLD_S),
        )
        if start is None:
            return None
        raw = start.decode() if isinstance(start, bytes) else str(start)
        return max(0.0, float(raw) - now)

    async def release(self, host: str, holder: str) -> None:
        await self._eval(RELEASE, host, f"{self.clock():.3f}", PACE_TTL_S, holder)

    async def _eval(self, script: str, host: str, *args: Any) -> Any:
        try:
            r = await self.pool()
            return await r.eval(script, 2, PACE_KEY + host, HOLD_KEY + host, *args)
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None


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
        pause: tuple[float, float] = PAGE_PAUSE_S,
        idle_s: float = math.inf,
    ) -> None:
        self.tools, self.deadline = tools, deadline
        self.pause = pause
        self.sleep, self.rng, self.monotonic = sleep, rng, monotonic
        self.host, self.pacer = host, pacer or MemoryPacer(monotonic)
        self.lease_until = lease_until  # monotonic time at which the lease's active time ends
        # The lease ends after idle_s without a tool call: counted from its start, then from the
        # end of each page.
        self.idle_s, self.idle_from = idle_s, monotonic()
        self.count = 0
        self.pauses: list[float] = []
        # The holders of this lease's pages whose host Redis would not take back.
        self.unreleased: set[str] = set()
        # The last page's snapshot text and address (the finder agent of B9 reads them).
        self.last_text, self.last_url = "", ""

    async def _act(
        self, action: Callable[[], Awaitable[dict[str, Any]]], tool: str = "browser_navigate"
    ) -> tuple[Node, str]:
        if self.monotonic() + PAGE_BUDGET_S >= self.deadline:
            raise SourceError("time_budget")
        pause, holder = self.rng.uniform(*self.pause), uuid.uuid4().hex
        # The page may hold its host from its first ask to its release (a cancel can land after
        # the store took the hold and before its reply, or inside the release), so a failure or
        # a cancel anywhere in the page gives the host back. A release for a hold the page did
        # not get only moves the host's last page time on.
        try:
            await self._give_back_left()
            while (wait := await self.pacer.reserve(self.host, pause, holder)) is None:
                # Another page of this host has not ended: its end starts our pause, so ask
                # again until it gives the host back, while the lease can wait.
                now = self.monotonic()
                if now + pause + PAGE_LOAD_S > min(self.lease_until, self.idle_from + self.idle_s):
                    raise SourceError("lease_budget")
                await self.sleep(PACE_POLL_S)
                if self.monotonic() + PAGE_BUDGET_S >= self.deadline:
                    raise SourceError("time_budget")
            page = await self._page(action, tool, wait)
            released = await self._release(holder)
        except BaseException:
            # The first error wins; a hold Redis would not take back is given back by the next
            # page of this lease, or ends after PACE_HOLD_S.
            if not await self._release(holder):
                self.unreleased.add(holder)
            raise
        if not released:
            self.unreleased.add(holder)
            raise StoreUnavailableError
        return page

    async def _release(self, holder: str) -> bool:
        """Gives the host back; a store that refuses is asked once more. False when it refused
        both times."""
        for _ in range(2):
            try:
                await self.pacer.release(self.host, holder)
            except StoreUnavailableError:
                continue
            return True
        return False

    async def _give_back_left(self) -> None:
        """The holds an earlier page of this lease could not give back are this lease's own: they
        go back before the next page asks, or it would wait on its own lease's earlier hold. One
        holder per page, so two concurrent tool calls of one finder session still pass the host
        one at a time."""
        for holder in tuple(self.unreleased):
            await self.pacer.release(self.host, holder)
            self.unreleased.discard(holder)

    async def _page(
        self, action: Callable[[], Awaitable[dict[str, Any]]], tool: str, wait: float
    ) -> tuple[Node, str]:
        if self.monotonic() + wait + PAGE_LOAD_S > self.lease_until:
            raise SourceError("lease_budget")
        self.pauses.append(wait)
        if wait > 0:
            await self.sleep(wait)
        self.count += 1
        try:
            done = await action()
            if not done.get("ok"):
                logger.warning("browser %s failed: error=%s", tool, failure_code(done))
                raise SourceError("page_failed")
            shot = await self.tools.snapshot()
        except BrowserError as exc:
            raise SourceError("lease_ended" if exc.code == "ended" else "page_failed") from None
        finally:
            self.idle_from = self.monotonic()  # browserd's idle counts from its last tool call
        if not shot.get("ok") or not isinstance(shot.get("snapshot"), str):
            logger.warning("browser browser_snapshot failed: error=%s", failure_code(shot))
            raise SourceError("page_failed")
        self.last_text, self.last_url = shot["snapshot"], str(shot.get("url") or "")
        return parse_snapshot(shot["snapshot"]), str(shot.get("url") or "")

    async def open(self, url: str) -> tuple[Node, str]:
        return await self._act(lambda: self.tools.navigate(url))

    async def click(self, ref: str, element: str) -> tuple[Node, str]:
        return await self._act(lambda: self.tools.click(ref, element), "browser_click")


_CODE = re.compile(r"[a-z][a-z_]{0,39}")


def failure_code(result: object) -> str:
    """A browserd failure's code as a log may hold it (`host_not_allowed`, `navigation_failed`):
    a short lower case word, else "other". Never its message, its page text nor an address."""
    code = result.get("error") if isinstance(result, dict) else None
    return code if isinstance(code, str) and _CODE.fullmatch(code) else "other"


class Runner:
    """The sweep's hook (sweep.BrowserHook): every ticked website, through `run_selected`."""

    def __init__(
        self,
        leases: Leases,
        tools_for: Callable[[str, str, str], Tools],
        *,
        base_url: str | None = None,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        pacer: Pacer | None = None,
        finder: Any = None,
        interactive_policy: "InteractivePolicy | None" = None,
        site_logins: Any = None,
    ) -> None:
        self.leases, self.tools_for, self.base_url = leases, tools_for, base_url
        self.finder = finder  # the finder agent of the websites (finder.RuntimeFinder)
        self.interactive_policy = interactive_policy
        self.site_logins = site_logins  # B17: the websites the person signed in to (site_logins)
        self.sleep, self.rng = sleep, rng or random.SystemRandom()
        self.monotonic = monotonic
        self.pacer = pacer or MemoryPacer()

    async def __call__(
        self,
        plan: Any,
        stats: Any,
        progress: Callable[[], Awaitable[None]],
        deadline: float,
    ) -> None:
        from app.browser.sources.interactive import run_selected

        await run_selected(self, plan, stats, progress, deadline, None)

    async def _give_back(self, user: str, held: str | None) -> None:
        if held is None:
            with contextlib.suppress(StoreUnavailableError):
                await self.leases.cancel(user, ["sweep"])


def runner_for(settings: Any, redis: Any, sessions: Any, agent_context: Any = None) -> Runner:
    """The hook as the worker runs it: the lease store and the tools of B3 and B5 on its Redis,
    and the finder agent on the runner's context (`agent_context`, worker.agent_context)."""
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
    from app.browser import platform_policy
    from app.browser.site_logins import SiteLogins

    return Runner(
        leases,
        lambda user, lease, token: LeaseTools(leases, client, user, lease, token),
        base_url=settings.browser_sources_base_url,
        pacer=RedisPacer(pool),
        finder=None if agent_context is None else _finder(agent_context),
        interactive_policy=platform_policy.runtime_policy(settings, sessions),
        site_logins=SiteLogins(sessions),
    )


def _finder(agent_context: Any) -> Any:
    from app.browser import finder

    return finder.RuntimeFinder(agent_context)
