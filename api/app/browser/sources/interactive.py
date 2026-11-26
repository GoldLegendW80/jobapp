"""Selected native search sites. Protected platforms need the person's current permission,
checked by the runner's InteractivePolicy (app/browser/platform_policy.py, browser B14).

The findings the finder verified on their detail pages are stored as they are reported, through
the run's commits (sweep.Commits, jobs J11 and J11b), never through the legacy company/date
store: a source admits batches only while it runs, and its admitted commits settle before the
next source starts. A source's `found` counts the findings stored or found duplicate, `postings`
the new jobs. Without the run's commits (a caller that is not a sweep) findings are only
counted, as `finding_persistence_pending`.

One agent per website (owner, 2026-10-27, jobs J18): "An LLM agent for each website that will
search intelligently, just like a human, using all the parameters provided to it." A website is
one lease and one finder run a sweep; the websites run side by side, as many as the person's
browser has free slots (two browsers at once, owner 2026-11-06, `_Slots`), and start in run
order. Its scope (`searches_for`) holds every saved title and
place, or the first ones that fit when the person saved more than it carries (100 of each,
32 KiB: `_carried`), the rest counted in the summary as left out, and every other criterion:
all of it is the agent's context. Since B20 (owner, 2026-11-04: the search works as Claude Code
with Playwright CLI on the owner's Mac) the agent types what it judges right (a saved title, a
translation, a synonym) and opens any public address; browserd gets the start alone and holds
only the line (browser/browserd/tools/line.py: never apply, submit a form that is no search,
write to anyone, pay, sign out or change the account) and its egress proxy. How the website
ends is what the agent read and said, never which tools it used (`outcome`, 2026-10-28): a
click the page did not let finish (a cookie banner over it) is told to it to recover from. Its
budget a website is "no limit" (owner): no cap on its turns, pages, tokens or verified postings,
and no time cap (B20: "as long as it takes"): no share of the sweep's time, no lease maximum, no
page pause and no 7 day pause; the run's Cancel stops it and the lease's idle timer ends one
nobody uses. LinkedIn still runs last (`run_order`).
Its postings merge into the website's one result, counted once per canonical address, in one
place only: an unwanted reading wins over a kept one (`_Site.place`). Unwanted titles and
unwanted places are dropped after reading, and the person's never apply rules mark a stored one
skipped (sweep.commit_verified). A posting of another contract, work mode or age is kept (jobs
J30, owner 2026-11-26: "Garder avec un badge"): the store marks what is outside the search's
criteria (app/jobs/criteria_badge.py).
The agent's first message gets the summary of the person's profile (B21,
app/profile/fit_summary.py, `FinderAgent.profile`): it reports what fits the profile, the saved
titles being where it starts searching.
With the run's commits, the verified batches go through the website's `_Site.verified` on their way
to the store: a posting given before and an unwanted one are counted there and never stored, so a
website stores a posting once. An address stored stays stored and counted once, whatever a later
reading of it says. The summary names the searches the agent ran and counts them, from its own tool
calls (`FinderSession.searches`): what it typed into boxes once it submitted them (browser_type with
submit, or a click or Enter next), named by the texts typed and the options chosen with them, or an
address it opened whose query holds a saved title.

A website the person signed in to (B17, app/browser/site_logins.py) is read signed in: the
scope says so and browserd runs it in the person's own persisted context, the lease being that
person's as every lease is. A sign in wall no longer ends the website by itself (B20): the agent
asks the person (browser_ask_person). When the search still ends on a sign in wall nobody
solved, the login is marked expired, so Account and Find jobs ask the person to sign in again,
and the website ends `signin_wall` when it found nothing (`_signed_out`).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from app.browser import finder
from app.browser.client import BrowserError, held_in
from app.browser.leases import StoreUnavailableError
from app.browser.sources import (
    ERRORS,
    PAGE_BUDGET_S,
    STOPS_SWEEP,
    Pages,
    SourceError,
)
from app.jobs import sweep
from app.jobs.schemas import SEARCHES_LISTED
from app.profile import matching
from app.profile.plain_terms import typed_words
from app.profile.schemas import InteractiveSite, SearchSection, TermList

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.browser.sources import Runner

logger = logging.getLogger("app.browser.sources.interactive")

FREEING_POLL_S = 5.0
# 150 s in all: browserd's longest end of a lease, its profile measured within MEASURE_S (120 s).
FREEING_TRIES = 30
SIGNIN_CODE = "platform_signin"  # guard.SIGNIN, the sources' code of a sign in page
# What a sign in page is told by (platform_policy.wall), and the code of a website whose search
# ended on one nobody solved while it was read signed in (`_signed_out`).
SIGNIN_WALLS = frozenset({SIGNIN_CODE, "linkedin_signin"})
SIGNIN_WALL = "signin_wall"
# All the person's criteria, the agent's context (jobs J18); since B20 it types what it judges
# right.
CRITERIA = frozenset(
    {
        "title_include",
        "title_exclude",
        "location",
        "location_exclude",
        "remote_stance",
        "contract_types",
        "seniority",
        "industries",
        "company_types",
        "days",
        "compensation",
    }
)


@dataclass(frozen=True)
class SourceContext:
    user_id: str
    sweep_id: str | None
    source_id: str
    platform: str
    url: str


class InteractivePolicy(Protocol):
    """B14 callbacks complete their durable transactions before returning a refusal."""

    async def check(self, context: SourceContext) -> str | None: ...

    async def reserve(self, context: SourceContext) -> str | None: ...

    async def observe(self, context: SourceContext, url: str, snapshot: str) -> str | None: ...


async def _permit(policy: InteractivePolicy | None, context: SourceContext) -> None:
    if context.platform != "custom" and (policy is None or context.sweep_id is None):
        raise SourceError("permission_unavailable")
    if policy:
        _refuse(await policy.check(context))


def _refuse(code: str | None) -> None:
    if code is not None:
        raise SourceError(code if code in ERRORS else "permission_unavailable")


class GuardedTools:
    """Only code creates this binding. Check after page pacing, before each browser call."""

    def __init__(
        self,
        tools: Any,
        policy: InteractivePolicy | None,
        context: SourceContext,
        logins: Any = None,
    ):
        self.tools, self.policy, self.context = tools, policy, context
        self.logins = logins  # B17: set only when the website is read signed in
        self.reserved = False
        # The last page a snapshot read, its address and its text; forgotten when the person
        # had the browser (ask_person).
        self.page = ("", "")
        self.helped = False  # B20: the person had the browser through browser_ask_person

    async def _before(self, name: str) -> None:
        await _permit(self.policy, self.context)
        if not self.reserved:
            # The first call opens the start page; "Anywhere on the web" (jobs J21) has none,
            # its first may be a fetch (browserd's FIRST_CALLS).
            if name not in ("browser_navigate", "web_fetch"):
                raise SourceError("permission_unavailable")
            if self.policy:
                _refuse(await self.policy.reserve(self.context))
            self.reserved = True

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        await self._before(name)
        return await self.tools.call(name, args)

    async def snapshot(self) -> dict[str, Any]:
        await self._before("browser_snapshot")
        shot = await self.tools.snapshot()
        if shot.get("ok"):
            url, text = shot.get("url"), shot.get("snapshot")
            # Any public address (B20): browserd's egress proxy holds it. A security check or a
            # sign in wall no longer ends the website: the agent asks the person (B20).
            if (
                not isinstance(url, str)
                or len(url) > 2000
                or not isinstance(text, str)
                or len(text) > finder.PAGE_MAX
                or not finder.canonical(url)
            ):
                raise SourceError("page_failed")
            self.page = (url, text)
        return shot

    async def wait_for(self, **args: Any) -> dict[str, Any]:
        await self._before("browser_wait_for")
        return await self.tools.wait_for(**args)

    def searching(self) -> None:
        """The web source's agent searches the web (jobs J21): its lease may go (`WebLease`)."""
        searching = getattr(self.tools, "searching", None)
        if searching is not None:
            searching()


def _plain(terms: TermList) -> tuple[list[str], int]:
    """The saved terms as typed words, and how many saved terms gave none: a list saved as
    regular expressions gives the plain words of each one that converts safely
    (plain_terms.typed_words, owner 2026-10-28), the others stay in the criteria the agent
    reads. A list that holds terms and gives no word refuses the search, never widens it."""
    words, dropped = typed_words(terms)
    if terms.terms and not words:
        raise ValueError("unsupported_criteria")
    return words, dropped


@dataclass(frozen=True)
class Searches:
    """The search of one website, frozen from the sweep's frozen criteria."""

    scope: finder.InteractiveScope
    unwanted_titles: re.Pattern[str] | None = None
    unwanted_places: re.Pattern[str] | None = None
    # Saved titles and places the scope could not carry (`_carried`), told in the summary.
    titles_left_out: int = 0
    places_left_out: int = 0


def _carried(
    titles: list[str], places: list[str], start: str, filters: list[tuple[str, str]]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The saved titles and places a website's scope carries into the agent's context (jobs
    J18; since B20 browserd gets the start alone): the first ones that fit, in saved order,
    taken a title then a place in turn, so a long list of one leaves room for the other. A value
    fits when it is at most finder.SCOPE_VALUE_MAX characters, its list holds fewer than
    finder.SCOPE_VALUES_MAX, and the scope measured as JSON stays within
    finder.SCOPE_BYTES_MAX, room kept for the signed in flag (B17). The rest is left out."""
    size = len(
        json.dumps(
            {
                "start": start,
                "titles": [],
                "places": [],
                "filters": dict(filters),
                "signed_in": True,
            }
        )
    )
    kept: tuple[list[str], list[str]] = ([], [])
    for index in range(max(len(titles), len(places))):
        for values, held in zip((titles, places), kept, strict=True):
            if index >= len(values) or len(held) >= finder.SCOPE_VALUES_MAX:
                continue
            value = values[index]
            cost = len(json.dumps(value)) + (2 if held else 0)  # ", " between two values
            if len(value) <= finder.SCOPE_VALUE_MAX and size + cost <= finder.SCOPE_BYTES_MAX:
                held.append(value)
                size += cost
    return tuple(kept[0]), tuple(kept[1])


def _unwanted(terms: Any) -> re.Pattern[str] | None:
    """A term list read defensively: a TermList, a plain list of words, or absent."""
    if isinstance(terms, list):
        return matching.compile_terms([t for t in terms if isinstance(t, str) and t], False)
    words = getattr(terms, "terms", None)
    if not isinstance(words, list):
        return None
    return matching.compile_terms(words, getattr(terms, "advanced_regex", False) is True)


def searches_for(search: SearchSection, start: str = "") -> Searches:
    """The website's search from the saved values, with no inferred website-specific filter
    encoding: the saved titles and places the scope carries (`_carried`: all of them, or the
    first ones that fit, the rest counted as left out), the filters' saved values and every
    criterion, all the agent's context (B20: it types what it judges right). ValueError
    (`unsupported_criteria` before a lease) when a list holds terms and gives no plain word,
    or no title fits.
    """
    titles, titles_dropped = _plain(search.title_include)
    places, places_dropped = _plain(search.location)
    if not titles:
        raise ValueError("unsupported_criteria")
    filters: list[tuple[str, str]] = []
    if search.remote_stance not in (None, "any"):
        filters.append(("remote", search.remote_stance))
    contracts = list(search.contract_types)
    if len(contracts) == 1:
        filters.append(("contract", contracts[0]))
    start = start or search.interactive_sites[0].url
    carried_titles, carried_places = _carried(titles, places, start, filters)
    return Searches(
        finder.InteractiveScope(
            start,
            carried_titles,
            carried_places,
            tuple(filters),
            criteria=json.dumps(search.model_dump(mode="json", include=CRITERIA)),
            instructions=search.interactive_instructions or "",
        ),
        _unwanted(search.title_exclude),
        _unwanted(getattr(search, "location_exclude", None)),
        titles_dropped + len(titles) - len(carried_titles),
        places_dropped + len(places) - len(carried_places),
    )


@dataclass
class _Site:
    """What the agent of one website read: its pages, tokens, searches and postings."""

    planned: Searches
    pages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    started: bool = False  # the start page opened and the agent ran
    searches: dict[tuple[str, str | None], None] = field(default_factory=dict)
    found: set[str] = field(default_factory=set)
    unwanted: set[str] = field(default_factory=set)
    refused: Counter[str] = field(default_factory=Counter)
    refusals: Counter[str] = field(default_factory=Counter)  # by browserd's rule (B25)
    tokens_seen: bool = False
    # With the run's commits (sweep.Commits): the website's source, and the addresses given to
    # the store (once each; `found` then stays empty, the commits count the source's postings).
    commits: Any = None
    source_id: str = ""
    host: str = ""
    sent: set[str] = field(default_factory=set)
    # The sweep's run in Activity (agents A10), recorded by the website's agent run.
    flow: Any = None
    # B27: the routes of the search the agent tried, and the reported postings the app opened
    # itself (FinderSession.routes, opened_by_app).
    routes: dict[str, None] = field(default_factory=dict)
    opened_by_app: int = 0
    # B28: the web searches the agent made, and its fetches that met a security check or a sign
    # in page, by reason (FinderSession.web_searches, fetch_blocked).
    web_searches: int = 0
    fetch_blocked: Counter[str] = field(default_factory=Counter)
    # B29: the closes of its tab browserd opened again, by reason, the working set in MiB at
    # each close for memory, and whether images, media and fonts stopped loading after one.
    tab_closed: Counter[str] = field(default_factory=Counter)
    tab_closed_mib: list[int] = field(default_factory=list)
    media_blocked: bool = False
    # Jobs J30: how many results the agent said it read, its own count (the largest it gave).
    results_read: int | None = None
    # Jobs J31: the cards report_listing refused, by reason (the evidence rule).
    cards_refused: Counter[str] = field(default_factory=Counter)

    def _sort(self, title: str, location: str, url: str) -> set[str]:
        """Where a posting read goes: unwanted (the person's unwanted titles and places), or kept,
        whatever its contract (jobs J30: the store gives it its badge)."""
        titles, places = self.planned.unwanted_titles, self.planned.unwanted_places
        if (titles and titles.search(title)) or (places and places.search(location)):
            return self.unwanted
        return self.found

    def place(self, url: str, into: set[str]) -> bool:
        """Put an address in one set only: unwanted wins over found (fail closed). False when
        a stronger reading already holds it."""
        order = (self.unwanted, self.found)
        rank = next(index for index, held in enumerate(order) if held is into)
        if any(url in held for held in order[:rank]):
            return False
        for weaker in order[rank + 1 :]:
            weaker.discard(url)
        into.add(url)
        return True

    def keep(self, session: finder.FinderSession) -> None:
        for posting in session.found:
            url = finder.canonical(posting.url) or posting.url
            self.place(url, self._sort(posting.title, posting.location, url))
        self.refused.update(session.refused)
        self.refusals.update(session.refusals)
        self.searches.update(session.searches)
        self.routes.update(session.routes)
        self.opened_by_app += session.opened_by_app
        self.web_searches += session.web_searches
        self.fetch_blocked.update(session.fetch_blocked)
        self.tab_closed.update(session.tab_closed)
        self.tab_closed_mib = [*self.tab_closed_mib, *session.tab_closed_mib][
            : finder.TAB_CLOSED_MIB_MAX
        ]
        self.media_blocked = self.media_blocked or session.media_blocked
        if session.results_read is not None:
            self.results_read = max(self.results_read or 0, session.results_read)
        self.cards_refused.update(session.cards_refused)

    async def verified(self, batch: finder.VerifiedBatch) -> None:
        """A verified batch on its way to the store (module docstring)."""
        kept: list[finder.VerifiedFinding] = []
        for finding in batch.findings:
            url = finding.url  # canonical (finder.FinderSession._verified)
            if url in self.sent:
                continue  # stored already: counted once, by the commits
            place = self._sort(finding.title, finding.location, url)
            if place is self.found:
                if url not in self.unwanted:
                    kept.append(finding)
            else:
                self.place(url, place)
        if kept:
            # Marked before the commit: a commit whose caller is cancelled still lands.
            self.sent.update(finding.url for finding in kept)
            await self.commits.save(self.source_id, self.host, finder.VerifiedBatch(tuple(kept)))

    def write(self, entry: dict[str, Any]) -> None:
        entry["result_pages"] = self.pages
        entry["searches"] = len(self.searches)
        # Saved titles and places the website's scope could not carry (searches_for).
        for name in ("titles_left_out", "places_left_out"):
            if getattr(self.planned, name):
                entry[name] = getattr(self.planned, name)
        # The searches the agent ran (FinderSession.searches), as the summary names them.
        entry["searched"] = [
            {"title": title, "place": place}
            for title, place in list(self.searches)[:SEARCHES_LISTED]
        ]
        if self.tokens_seen:
            entry["tokens"] = {"input": self.input_tokens, "output": self.output_tokens}
        if self.started:
            if self.commits is None:
                entry["found"] = len(self.found)
            entry["refused"] = dict(self.refused)
            entry["opened_by_app"] = self.opened_by_app
            entry["routes"] = list(self.routes)
            entry["web_searches"] = self.web_searches
            entry["fetch_blocked"] = dict(self.fetch_blocked)
            if self.refusals:  # browserd's refusals by rule (browser B25)
                entry["refusals"] = dict(self.refusals)
            if self.tab_closed:  # B29: its tab closed and opened again
                entry["tab_closed"] = sum(self.tab_closed.values())
                entry["tab_closed_reasons"] = dict(self.tab_closed)
                entry["tab_closed_mib"] = self.tab_closed_mib
                entry["media_blocked"] = self.media_blocked
            if self.results_read is not None:
                entry["results_read"] = self.results_read
            entry["cards_refused"] = dict(self.cards_refused)
        if self.unwanted:
            entry["unwanted"] = len(self.unwanted)


def run_order[T](sites: list[T]) -> list[T]:
    """The order the websites of a sweep run in. Round 3 (sweep of 2026-10-30, 16:08): LinkedIn
    ran first with a third of the 900 s, about 300 s, and each of its actions waits its pace (10
    to 30 s), so its agent ran out of time after one title of three. A website with a pace of
    its own (platform_policy.PAUSES: LinkedIn) runs after the others, in saved order otherwise:
    its share is never smaller than before (a share is the time left divided by the websites
    left, so the first websites' shares are the same), and the time the others leave is its.
    Since B20 no website has a share of the time nor a page pause; the order stays."""
    from app.browser import platform_policy

    return sorted(
        sites, key=lambda site: getattr(site, "effective_platform", "") in platform_policy.PAUSES
    )


class _Slots:
    """The sweep's websites in the person's browser at once, one lease (one slot) each (two
    browsers at once, owner 2026-11-06): the next website starts once the one before it holds
    its lease or ended, so they start in run order and as many run as browserd has free slots.
    A website the lease store makes wait while another website of the sweep holds a lease asks
    again once one of them ends; with none holding one it is `browser_busy` when a lease that is
    none of the sweep's holds the browser (a fill, a sign in, another person), else it asks again
    while the browser is freed (`_website`, browser B23). A stop (STOPS_SWEEP) lets the websites
    that run end, and those still waiting for a slot do not start (`not_started`)."""

    def __init__(self, stop: str | None = None) -> None:
        self.holding = 0
        self.ended = 0  # the sweep's websites that held a lease and ended, commits settled
        self.stop = stop
        self.changed = asyncio.Condition()
        self._took: set[asyncio.Event] = set()

    def took(self, ready: asyncio.Event) -> None:
        self.holding += 1
        self._took.add(ready)
        ready.set()

    async def gave_back(self, ready: asyncio.Event) -> None:
        """The website of `ready` ended, its lease released and its commits settled."""
        if ready not in self._took:
            return
        async with self.changed:
            self._took.discard(ready)
            self.holding -= 1
            self.ended += 1
            self.changed.notify_all()

    async def halt(self, code: str) -> None:
        async with self.changed:
            self.stop = self.stop or code
            self.changed.notify_all()

    async def wait_end(self, seen: int) -> bool:
        """After a wait answered while `seen` leases had ended: True once another ended (ask
        again), False at once when no website of the sweep holds a lease."""
        async with self.changed:
            if self.ended == seen and self.holding == 0:
                return False
            await self.changed.wait_for(lambda: self.ended != seen or self.stop is not None)
            return True


async def run_selected(
    runner: Runner,
    plan: Any,
    stats: sweep.Stats,
    progress: Callable[[], Awaitable[None]],
    deadline: float,
    stop: str | None,
) -> None:
    search = getattr(plan, "search", None)
    if search is None:
        return  # Pre-v1 legacy callers have no selected interactive sources.
    sites = run_order([source for source in search.interactive_sites if source.enabled])
    slots = _Slots(stop)

    async def run_one(source: InteractiveSite, entry: dict[str, Any], ready: asyncio.Event) -> None:
        try:
            # Safe stable identity only. Instructions and search text do not become labels.
            stats.current_board = entry.get("source_id")
            await progress()
            commits = getattr(plan, "commits", None)
            if commits is not None:
                commits.admit(entry["source_id"])
            # No time cap (B20, owner 2026-11-04: "as long as it takes"): a website runs until
            # its agent ends or the run is cancelled; the sweep's own deadline bounds its API
            # sources.
            try:
                entry["error"] = await _one(
                    runner, plan, source, search, entry, math.inf, (slots, ready)
                )
            except (BrowserError, StoreUnavailableError):
                entry["error"] = "browser_unavailable"
            except Exception:
                entry["error"] = "agent_failed"
            if entry["error"] in STOPS_SWEEP:
                await slots.halt(entry["error"])
            if commits is not None and not await commits.settle(entry["source_id"]):
                # A commit outlived the wait: no later source runs, the row is left as it is.
                entry["error"] = entry["error"] or "store_failed"
                await slots.halt("store_failed")
            sweep.finish_source(entry)
            sweep.release_board(stats, entry.get("source_id"))
            await progress()
        finally:
            # After its commits settled: a website that waits for this slot starts after them.
            await slots.gave_back(ready)
            ready.set()

    running: list[asyncio.Task[None]] = []
    try:
        for source in sites:
            entry = sweep.bind_source(
                stats,
                "llm_browser",
                (source.url,),
                {
                    "source_id": sweep._source_id("llm_browser", source.url),
                    "mode": "llm_browser",
                    "kind": source.effective_platform,
                    "host": urlsplit(source.url).hostname,
                    "result_pages": 0,
                    "found": 0,
                    "postings": 0,
                    "error": None,
                },
            )
            if slots.stop:
                entry["error"] = "not_started"
                sweep.finish_source(entry)
                await progress()
                continue
            ready = asyncio.Event()
            running.append(asyncio.ensure_future(run_one(source, entry, ready)))
            await ready.wait()
        for task in running:
            await task
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)


async def _one(
    runner: Runner,
    plan: Any,
    source: InteractiveSite,
    search: SearchSection,
    entry: dict[str, Any],
    deadline: float,
    turn: tuple[_Slots, asyncio.Event] | None = None,
) -> str | None:
    from app.browser import platform_policy

    platform = source.effective_platform
    if platform == "custom":
        platform = platform_policy.platform_of(source.url)  # indeed.fr saved as custom
    # Tests only: a protected platform opens the fixture server, never the real host (B14).
    start = platform_policy.test_start(runner.base_url, platform, source.url)
    context = SourceContext(
        str(plan.user_id),
        str(plan.sweep_id) if getattr(plan, "sweep_id", None) else None,
        sweep._source_id("llm_browser", source.url),
        platform,
        start,
    )
    try:
        await _permit(runner.interactive_policy, context)
    except SourceError as exc:
        return exc.code
    try:
        planned = searches_for(search, start)
    except ValueError:
        return "unsupported_criteria"
    if runner.finder is None or not await runner.finder.available(str(plan.user_id)):
        return "no_credential"
    # B21: the agent's first message gets the summary of the person's profile.
    profile = await runner.finder.profile(str(plan.user_id))
    scope = dataclasses.replace(planned.scope, profile=profile)
    planned = dataclasses.replace(planned, scope=scope)
    site = _Site(
        planned,
        commits=getattr(plan, "commits", None),
        source_id=entry["source_id"],
        host=entry.get("host") or "",
        flow=getattr(plan, "flow", None),
    )
    try:
        code = await _website(runner, plan, context, planned.scope, site, deadline, turn)
    finally:
        site.write(entry)
    return code or ("finding_persistence_pending" if site.found else None)


async def _website(
    runner: Runner,
    plan: Any,
    context: SourceContext,
    scope: finder.InteractiveScope,
    site: _Site,
    deadline: float,
    turn: tuple[_Slots, asyncio.Event] | None = None,
) -> str | None:
    """The website's one agent run on one lease, until the agent ends or the run is cancelled
    (B20: no time cap). `turn`: the sweep's websites at once, and the event this website sets
    once it holds its lease (`_Slots`)."""
    from app.browser import platform_policy

    asked = runner.monotonic()
    if asked + PAGE_BUDGET_S >= deadline:
        return "time_budget"
    host = urlsplit(scope.start).hostname or ""
    logins = getattr(runner, "site_logins", None)
    if logins is not None and await logins.signed_in(str(plan.user_id), host):
        scope = dataclasses.replace(scope, signed_in=True)
    else:
        logins = None
    # The website's hosts: its platform's family (B19, finder.lease_hosts), else its own host.
    hosts = finder.lease_hosts(scope.start)
    slots, ready = turn if turn is not None else (_Slots(), asyncio.Event())
    got = await _take(runner, plan, context, hosts, slots)
    if isinstance(got, str):
        return got
    slots.took(ready)
    lease_id = got.lease.lease_id
    paced = platform_policy.platform_for(context)
    try:
        pages = Pages(
            GuardedTools(
                runner.tools_for(str(plan.user_id), lease_id, got.token),
                runner.interactive_policy,
                context,
                logins,
            ),
            deadline,
            sleep=runner.sleep,
            rng=runner.rng,
            monotonic=runner.monotonic,
            # One pace per platform, shared by all its addresses (B14), else per host.
            host=platform_policy.pace_key(paced, host, runner.base_url),
            pacer=runner.pacer,
            # No lease maximum beyond the run, no page pause (B20): the pace hold across people
            # stays, so two people's pages of one host never run at once.
            lease_until=math.inf,
            pause=platform_policy.pause_for(paced),
            idle_s=got.lease.idle_s,
        )
        return await _search(runner.finder, pages, scope, str(plan.user_id), site, runner.base_url)
    finally:
        await runner.leases.release(str(plan.user_id), lease_id, reason="sweep_done")


async def _take(
    runner: Runner, plan: Any, context: SourceContext, hosts: Any, slots: _Slots
) -> Any:
    """A sweep lease of the person's browser for the source of `context`, in its turn among the
    sweep's websites (`_Slots`): the lease store's answer holding the lease, or the code that
    ends the source (`browser_busy`, `not_started`)."""
    freeing = 0
    while True:
        seen = slots.ended
        got = await runner.leases.acquire(
            str(plan.user_id), "sweep", automation=True, hosts=hosts, source_id=context.source_id
        )
        if got.lease is not None and got.token is not None:
            return got
        held = None if got.lease is None else ""
        await runner._give_back(str(plan.user_id), held, context.source_id)
        if held is not None:
            return "browser_busy"
        if not await slots.wait_end(seen):
            if freeing >= FREEING_TRIES or await _held_elsewhere(runner, str(plan.user_id)):
                return "browser_busy"
            freeing += 1
            await runner.sleep(FREEING_POLL_S)
        if slots.stop:
            return "not_started"


async def _held_elsewhere(runner: Runner, user: str) -> bool:
    """Whether a lease that is none of this sweep's holds the browser, when no website of the
    sweep holds one: another person's, or one of the person's own (a fill, a sign in, Look),
    since the sweep's websites give theirs back before they end. False while nothing holds it:
    browserd still ends the last window (Chromium closing, the profile measured), past the
    api's own wait for that end (client.END_TIMEOUT_S), and refuses a start until then."""
    view = await runner.leases.current(user)
    return view.held_by_other or view.lease is not None or bool(view.leases)


# The app's own refusals of an agent's search (B19): browserd's, and the api's of an address
# that is no observed link. A model's slip (`model_value`) is its own, never one of them.
APP_REFUSALS = ("unsupported_interaction",)


def outcome(session: finder.FinderSession) -> str | None:
    """How a website the agent ran ends (2026-10-28, issue 716). It was searched when the agent
    reported a posting code verified, or said after a search the session saw that the website
    listed no matching job (`report_no_postings` no_match). Else the blocker it named (a cookie
    wall, a sign in wall, no search control, a page with no list), but when the app refused one
    of its actions (B19, 2026-10-29: "The app refused the search on this website."), the
    refusal wins, `search_refused`, over a security check that never cleared too (2026-10-30,
    `security_check`: the agent says it, so it never hides a refusal of the app); else
    `unsupported_interaction` when browserd or the website
    refused one of its actions, `search_refused` when the api refused an address; else
    `nothing_reported`. The web source whose plan WebSearch failing lifted before it was searched
    (`FinderSession.web_search_failed`) ends `web_search_failed` (round 1 of #814). Opening a
    search page by its address, pressing a link or a failed click that it recovered from are how
    a person searches too: no kind of action is required, and nothing read is no success.
    The web source whose agent ended without report_no_postings while its plan was left, its
    follow ups spent (`FinderSession.plan_unfinished`), ends `plan_unfinished` whatever it
    reported (jobs J27): its postings are kept, the source is partial, never "searched"."""
    if session.plan_unfinished():
        return "plan_unfinished"  # J27: never searched while the plan is left
    if session.found or session.verified_count:
        return None
    if session.web_search_failed():
        return "web_search_failed"  # J23: WebSearch failing lifted a plan not searched
    refused = any(session.refused[name] for name in APP_REFUSALS)
    if session.outcome:
        code = finder.NO_POSTINGS[session.outcome]
        if code is not None:
            return "search_refused" if refused else code
        if session.interactions:  # no_match after the agent acted on the website (B20)
            return None
        web = session.interactive is not None and session.interactive.web
        if web and session.web_searches:  # J21: no_match after searching the web
            return None
    if session.refused["unsupported_interaction"]:
        return "unsupported_interaction"
    return "search_refused" if refused else "nothing_reported"


async def _signed_out(
    pages: Pages, session: finder.FinderSession, base_url: str | None = None
) -> bool:
    """B17, kept by B20: a website read signed in whose search ended on a sign in wall nobody
    solved has lost the person's login. It is marked expired, so Account and Find jobs ask the
    person to sign in again, and True is returned. Nobody solved the wall when the last page the
    search read is a sign in page (platform_policy.wall: the agent asked and nobody was
    watching, or it ended there), or when the agent reported a sign in wall (`signin_wall`) and
    the person never had the browser through browser_ask_person. A person who signs in again
    when asked leaves the search on a page that is no sign in page: nothing is marked. The page
    read before the person had the browser counts no more (`ask_person` forgets it): only a page
    read after it does. A mark that cannot be written is logged and the website ends as it
    would."""
    from app.browser import platform_policy

    guarded = pages.tools
    if not isinstance(guarded, GuardedTools) or guarded.logins is None:
        return False
    url, text = guarded.page
    platform = platform_policy.platform_for(guarded.context)
    on_wall = bool(url) and platform_policy.wall(platform, url, text, base_url) in SIGNIN_WALLS
    if not on_wall and (session.outcome != "signin_wall" or guarded.helped):
        return False
    try:
        host = urlsplit(guarded.context.url).hostname or ""
        await guarded.logins.expire(guarded.context.user_id, host)
    except Exception as error:
        logger.warning(
            "the login of a website could not be marked expired: error=%s", type(error).__name__
        )
    return True


async def _search(
    agent: finder.FinderAgent,
    pages: Pages,
    scope: finder.InteractiveScope,
    user: str,
    site: _Site,
    base_url: str | None = None,
) -> str | None:
    """The complete run, the first navigation included, with no time cap (B20); a login the
    search found ended is marked (`_signed_out`)."""
    session = None
    cap = asyncio.timeout(None)  # B20: no time cap; the run's Cancel stops it
    try:
        async with cap:
            if scope.web:
                # J21: the web search opens no start page; its first tool is WebSearch.
                site.started = True
                session = finder.FinderSession(
                    pages,
                    scope.start,
                    "",
                    user_id=user,
                    interactive=scope,
                    on_verified=None if site.commits is None else site.verified,
                    flow=site.flow,
                )
            else:
                result: dict[str, Any] = {}

                async def start():
                    nonlocal result
                    result = await pages.tools.call(
                        "browser_navigate",
                        {
                            "url": scope.start,
                            "_discovery": "native_get",
                            "_discovery_scope": scope.tool_scope(),
                        },
                    )
                    return result

                try:
                    await pages._act(start)
                except SourceError as exc:
                    if exc.code == "page_failed" and result.get("error") == "unsupported_discovery":
                        return "unsupported_interaction"
                    return exc.code
                # The start page may land on another address (Welcome to the Jungle answers `/` on
                # `/en`, Indeed's on the person's country site): any public address since B20,
                # browserd's egress proxy holding it.
                landed = finder.canonical(pages.last_url)
                if not landed:
                    return "unsupported_interaction"
                site.started = True
                # #797: the start page (up to PAGE_MAX) is parsed off the event loop when large.
                parsed = await finder.parsed_off_loop(landed, pages.last_text)
                session = finder.FinderSession(
                    pages,
                    scope.start,
                    pages.last_text,
                    user_id=user,
                    interactive=scope,
                    on_verified=None if site.commits is None else site.verified,
                    landed="" if landed == scope.start else landed,
                    flow=site.flow,
                    parsed=parsed,
                )
            ran = await agent(session)
            site.input_tokens += ran.input_tokens
            site.output_tokens += ran.output_tokens
            site.tokens_seen = True
            signed_out = await _signed_out(pages, session, base_url)
            if session.stopped:
                return session.stopped
            if ran.stopped in ("no_credential", "cancelled"):
                # The person's Cancel of the website's run is never retried (sweep.NOT_RETRIED).
                return "agent_cancelled" if ran.stopped == "cancelled" else "no_credential"
            if ran.stopped:
                return "agent_failed"
            code = outcome(session)
            return SIGNIN_WALL if signed_out and code is not None else code
    except TimeoutError:
        return "time_budget" if cap.expired() else "agent_failed"
    except SourceError as exc:
        return exc.code
    finally:
        site.pages = pages.count
        if session:
            site.keep(session)


ASK_POLL_S = 2.0


async def await_person(
    tools: Any, left: int, monotonic: Callable[[], float], sleep: Callable[[float], Any]
) -> str:
    """After browserd's `waiting_person` answer to an ask (B20, and One click Apply's agent,
    apply A19): polls browserd until the lease is active again (`back`: the person gave the
    browser back, or the ask's deadline passed), another lease is current (`ended`), or `left`
    and a grace went by (`timeout`). The api's copy of the lease waits with browserd's
    (`Leases.wait_person`), so it never ends it meanwhile."""
    from app.browser.leases import MAX_GRACE_S

    end_at = monotonic() + left + MAX_GRACE_S
    synced = False
    while monotonic() < end_at:
        try:
            found = await tools.client.current()
        except BrowserError:
            found = None
        lease = held_in(found, tools.lease_id)
        if found is not None and lease is None:
            return "ended"
        if isinstance(lease, dict):
            try:
                if lease.get("state") == "waiting_person" and not synced:
                    host = str(lease.get("wait_host") or "")[:253]
                    await tools.leases.wait_person(
                        tools.user_id, tools.lease_id, "ask_person", host, left
                    )
                    synced = True
                elif lease.get("state") == "active":
                    if synced:
                        await tools.leases.resume(tools.user_id, tools.lease_id)
                    return "back"
            except StoreUnavailableError:
                pass  # Redis away: the api's copy is set on the next round
        await sleep(ASK_POLL_S)
    return "timeout"


async def ask_person(pages: Pages, reason: str, tool_scope: dict[str, Any]) -> str:
    """browser_ask_person (B20, browserd/tools/ask_person.py): `nobody_watching` at once, or the
    person is asked in the live view and this polls browserd until the lease is active again
    (the person gave the browser back, or the ask's deadline passed) or it ended. The api's copy
    of the lease waits with browserd's (`Leases.wait_person`), so it never ends it meanwhile.
    The answer is the sentence the agent reads."""
    guarded = pages.tools
    answer = await guarded.call(
        "browser_ask_person",
        {"reason": reason, "_discovery": "native_get", "_discovery_scope": tool_scope},
    )
    code = answer.get("error")
    if code == "nobody_watching":
        return "Nobody is watching the browser now: go on another way, or end the website."
    if code != "waiting_person":
        return "The person could not be asked here: go on another way."
    left = answer.get("left_s")
    left = left if isinstance(left, int) and not isinstance(left, bool) and left > 0 else 900
    outcome = await await_person(guarded.tools, left, pages.monotonic, pages.sleep)
    if outcome == "ended":
        raise SourceError("lease_ended")
    if outcome == "back":
        guarded.helped = True
        # The page read before the ask is no longer the browser's (`_signed_out`).
        guarded.page = ("", "")
        return "The person had the browser: read the page again (browser_snapshot)."
    return "The person did not answer: go on another way, or end the website."


async def run_web(
    runner: Runner,
    plan: Any,
    stats: sweep.Stats,
    progress: Callable[[], Awaitable[None]],
    deadline: float,
    stop: str | None,
) -> None:
    """ "Anywhere on the web" (jobs J21, owner 2026-11-18): the search of the whole web, after the
    websites, on leases of the person's browser held only around its browserd calls (web_fetch
    and the pages it opens, `WebLease`), with the same criteria, profile, tools and
    verification as a website's search and no start page."""
    search = getattr(plan, "search", None)
    if search is None or not sweep.has_web(search):
        return
    entry = sweep.bind_source(
        stats,
        "web",
        (sweep.WEB_KEY,),
        {**sweep.web_entry(), "result_pages": 0, "found": 0, "postings": 0, "error": None},
    )
    if stop:
        entry["error"] = "not_started"
        sweep.finish_source(entry)
        await progress()
        return
    stats.current_board = entry["source_id"]
    await progress()
    commits = getattr(plan, "commits", None)
    if commits is not None:
        commits.admit(entry["source_id"])
    try:
        entry["error"] = await _web(runner, plan, search, entry)
    except (BrowserError, StoreUnavailableError):
        entry["error"] = "browser_unavailable"
    except Exception as error:
        logger.exception(
            "web source failed",
            extra={
                "sweep_id": str(getattr(plan, "sweep_id", None)),
                "error_type": type(error).__name__,
            },
        )
        entry["error"] = "agent_failed"
    if commits is not None and not await commits.settle(entry["source_id"]):
        entry["error"] = entry["error"] or "store_failed"
    sweep.finish_source(entry)
    sweep.release_board(stats, entry.get("source_id"))
    await progress()


async def _web(
    runner: Runner, plan: Any, search: SearchSection, entry: dict[str, Any]
) -> str | None:
    """The web search's one agent run (`run_web`): every saved criterion and the profile, the
    scope's start WEB_START (never opened), its postings stored as a website's (`_Site`), so a
    posting another source stored is counted a duplicate there (same address, or the same
    company, title and place: sweep.commit_verified)."""
    user = str(plan.user_id)
    context = SourceContext(
        user,
        str(plan.sweep_id) if getattr(plan, "sweep_id", None) else None,
        entry["source_id"],
        "custom",
        finder.WEB_START,
    )
    try:
        planned = searches_for(search, finder.WEB_START)
    except ValueError:
        return "unsupported_criteria"
    if runner.finder is None or not await runner.finder.available(user):
        return "no_credential"
    profile = await runner.finder.profile(user)
    scope = dataclasses.replace(planned.scope, profile=profile, web=True)
    planned = dataclasses.replace(planned, scope=scope)
    site = _Site(
        planned,
        commits=getattr(plan, "commits", None),
        source_id=entry["source_id"],
        host="web",
        flow=getattr(plan, "flow", None),
    )
    from app.browser import platform_policy

    lease = WebLease(runner, plan, context)
    paced = platform_policy.platform_for(context)
    web_host = urlsplit(finder.WEB_START).hostname or ""
    pages = Pages(
        GuardedTools(lease, runner.interactive_policy, context),
        math.inf,
        sleep=runner.sleep,
        rng=runner.rng,
        monotonic=runner.monotonic,
        host=platform_policy.pace_key(paced, web_host, runner.base_url),
        pacer=runner.pacer,
        # No lease maximum, no idle bound: a lease is taken again when the agent next needs one.
        lease_until=math.inf,
        pause=platform_policy.pause_for(paced),
    )
    try:
        code = await _search(runner.finder, pages, scope, user, site, runner.base_url)
    finally:
        try:
            await lease.close()
        finally:
            site.write(entry)
    return code or ("finding_persistence_pending" if site.found else None)


# The calls a new lease of the web source may start with (browserd's FIRST_CALLS): they open
# a page by its address.
WEB_FIRST_CALLS = frozenset({"browser_navigate", "web_fetch"})


class WebLease:
    """The browser of "Anywhere on the web" (jobs J21, round 1 of #809): held only around its
    browserd calls. The first call that opens an address takes a sweep lease (`_take`); the
    agent going back to its web search (`searching`, from `FinderSession.searched_web`) gives
    it back, so browserd's idle timer never ends it while WebSearch runs, and the browser slot
    serves an apply meanwhile. A lease browserd ended anyway (idle while the model thought, a
    sign in that took the slot) is dropped: a call that opens an address takes a new one, any
    other answers `no_page`, so the agent opens its page again. The tools interface of
    browserd (`call`, `snapshot`, `wait_for`), under GuardedTools."""

    def __init__(self, runner: Runner, plan: Any, context: SourceContext) -> None:
        self.runner, self.plan, self.context = runner, plan, context
        self.hosts = finder.lease_hosts(finder.WEB_START)
        self.lease_id: str | None = None
        self.tools: Any = None
        self.taken = 0  # the leases taken in the run
        self.calls = 0  # browserd calls in flight
        self.lock = asyncio.Lock()
        self.giving: asyncio.Task[None] | None = None

    async def _held(self, opens: bool) -> Any:
        """The lease's tools, a lease taken when `opens`; None with no lease and no address."""
        async with self.lock:
            if self.tools is None:
                if not opens:
                    return None
                got = await _take(self.runner, self.plan, self.context, self.hosts, _Slots())
                if isinstance(got, str):
                    raise SourceError(got)
                self.lease_id = got.lease.lease_id
                self.tools = self.runner.tools_for(str(self.plan.user_id), self.lease_id, got.token)
                self.taken += 1
            self.calls += 1
            return self.tools

    async def _drop(self, tools: Any = None, idle: bool = False) -> None:
        """Gives the lease back (`tools`: only while it is still that one's; `idle`: only while
        no browserd call is in flight)."""
        async with self.lock:
            if self.tools is None or (tools is not None and tools is not self.tools):
                return
            if idle and self.calls:
                return
            lease_id, self.tools, self.lease_id = self.lease_id, None, None
            if lease_id is not None:
                await self.runner.leases.release(
                    str(self.plan.user_id), lease_id, reason="sweep_done"
                )

    async def _run(self, name: str, call: Callable[[Any], Awaitable[dict[str, Any]]]) -> Any:
        opens = name in WEB_FIRST_CALLS
        for _ in range(2 if opens else 1):
            tools = await self._held(opens)
            if tools is None:
                break
            try:
                return await call(tools)
            except BrowserError as exc:
                if exc.code != "ended":
                    raise
            finally:
                self.calls -= 1
            with contextlib.suppress(StoreUnavailableError):
                await self._drop(tools)
        return {"ok": False, "error": "no_page"}

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return await self._run(name, lambda tools: tools.call(name, args))

    async def snapshot(self) -> dict[str, Any]:
        return await self._run("browser_snapshot", lambda tools: tools.snapshot())

    async def wait_for(self, **args: Any) -> dict[str, Any]:
        return await self._run("browser_wait_for", lambda tools: tools.wait_for(**args))

    def searching(self) -> None:
        """The agent runs a WebSearch: the lease goes back once no browserd call is in flight."""
        if self.tools is None or self.calls or (self.giving and not self.giving.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.giving = loop.create_task(self._give())

    async def _give(self) -> None:
        try:
            await self._drop(idle=True)
        except Exception as error:
            # The lease stays the run's; `close` gives it back at the end.
            logger.warning("web source lease give back failed: %s", type(error).__name__)

    async def close(self) -> None:
        """The end of the run: the lease, if held, goes back."""
        if self.giving is not None:
            await self.giving
        await self._drop()
