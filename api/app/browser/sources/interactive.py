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
one lease and one finder run a sweep. Its frozen scope (`searches_for`) holds every saved title
and place, or the first ones that fit when the person saved more than it carries (100 of each,
32 KiB as sent: `_carried`), the rest counted in the summary as left out; the agent decides
which searches to run, in which order, and when the website is covered, and browserd lets it
type only one saved title in a search box or one saved place in the place box (or nothing, to
clear one), and set a native filter only to its saved value (browser/browserd/tools/
discovery.py). A value the model types that its box does not take, or a choice that is no saved
value, is its own slip: told to it before the browser and counted apart, it is no reason to say
the website unsupported (finder.FinderSession._slip). How the website ends is what the agent
read and said, never which tools it used (`outcome`, 2026-10-28): a click the page did not let
finish (a cookie banner over it) is told to it to recover from. Its budget a website is "no limit"
(owner): no cap on its turns, pages, tokens or verified postings. What bounds it is not its
budget: its share of the
sweep's time (the sweep's time left divided by the websites left, `run_selected`, so one website
never starves the next), which it is told; the lease's own maximum; pacing per platform and the
7 day pause (no daily limit and no page cap on any website, LinkedIn included, owner 2026-10-28).
Its postings merge into the website's one result, counted once per canonical address, in one
place only: an unwanted reading wins over another contract, which wins over a kept one
(`_Site.place`). One contract type is the website's own filter; several are checked on what
was read (the engine's `kind` of the title, location and address), as a native filter takes one
value. Places are typed as the person wrote them, never mapped to a website's own codes.
The other criteria (unwanted titles and places, seniority, industries, company types, pay, the
days) reach the agent as plain context (`criteria`), never typed; unwanted titles and unwanted
places are dropped after reading.
With the run's commits, the verified batches go through the website's `_Site.verified` on their
way to the store: a posting given before, an unwanted one and one of another contract are
counted there and never stored, so a website stores a posting once. An address stored stays
stored and counted once, whatever a later reading of it says. The summary names the searches
the agent ran, from its own tool calls (`FinderSession.searches`), and counts them.

A website the person signed in to (B17, app/browser/site_logins.py) is read signed in: the
scope says so and browserd runs it in the person's own persisted context, the lease being that
person's as every lease is. A sign in page met then ends the source `platform_signin` and marks
the login expired, so the card asks the person to sign in again.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from app.browser import finder
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.browser.sources import ERRORS, PAGE_BUDGET_S, STOPS_SWEEP, Pages, SourceError
from app.jobs import sweep
from app.jobs.importer import kind
from app.jobs.schemas import SEARCHES_LISTED
from app.profile import matching
from app.profile.plain_terms import typed_words
from app.profile.schemas import InteractiveSite, SearchSection, TermList

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.browser.sources import Runner

SIGNIN_CODE = "platform_signin"  # guard.SIGNIN, the sources' code of a sign in page
# All the person's criteria, the agent's context (jobs J18); only titles, places and the native
# filters' values can be typed or chosen.
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

    async def _before(self, name: str) -> None:
        await _permit(self.policy, self.context)
        if not self.reserved:
            if name != "browser_navigate":
                raise SourceError("permission_unavailable")
            if self.policy:
                _refuse(await self.policy.reserve(self.context))
            self.reserved = True

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        await self._before(name)
        result = await self.tools.call(name, args)
        if self.logins is not None and result.get("error") == "signin_required":
            await self._signed_out(SIGNIN_CODE, "", "")
        return result

    async def snapshot(self) -> dict[str, Any]:
        await self._before("browser_snapshot")
        shot = await self.tools.snapshot()
        if shot.get("ok"):
            url, text = shot.get("url"), shot.get("snapshot")
            # Every page is on the start's website: its origin, or its platform's family (B19:
            # `fr.indeed.com` for `www.indeed.com`, finder.on_family), as browserd holds it.
            if (
                not isinstance(url, str)
                or len(url) > 2000
                or not isinstance(text, str)
                or len(text) > finder.SNAPSHOT_MAX
                or not finder.on_family(url, self.context.url)
            ):
                raise SourceError("page_failed")
            code = await self.policy.observe(self.context, url, text) if self.policy else None
            if self.logins is not None:
                await self._signed_out(code, url, text)
            _refuse(code)
        return shot

    async def _signed_out(self, code: str | None, url: str, text: str) -> None:
        """B17: a sign in page on a website read signed in (any platform, custom ones too): the
        login is gone, so it is marked expired and the source ends `platform_signin`."""
        from app.browser import platform_policy

        if code is None and url:
            code = platform_policy.wall("", url, text)
        if code in {platform_policy.SIGNIN, "linkedin_signin"}:
            host = urlsplit(self.context.url).hostname or ""
            await self.logins.expire(self.context.user_id, host)
            raise SourceError(code)

    async def wait_for(self, **args: Any) -> dict[str, Any]:
        await self._before("browser_wait_for")
        return await self.tools.wait_for(**args)


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
    contracts: frozenset[str] = frozenset()  # checked after reading, empty when native
    unwanted_titles: re.Pattern[str] | None = None
    unwanted_places: re.Pattern[str] | None = None
    # Saved titles and places the scope could not carry (`_carried`), told in the summary.
    titles_left_out: int = 0
    places_left_out: int = 0


def _carried(
    titles: list[str], places: list[str], start: str, filters: list[tuple[str, str]]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The saved titles and places a website's frozen scope carries (jobs J18): the first ones
    that fit, in saved order, taken a title then a place in turn, so a long list of one leaves
    room for the other. A value fits when it is at most finder.SCOPE_VALUE_MAX characters, its
    list holds fewer than finder.SCOPE_VALUES_MAX, and the scope as sent stays within
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
    """Use the saved values (typed as saved, or with another case or accents, browserd's
    discovery.saved_as), with no inferred website-specific filter encoding: the saved
    titles and places the scope carries (`_carried`: all of them, or the first ones that fit,
    the rest counted as left out), the agent typing one at a time (ValueError when one cannot
    be typed or no title fits, `unsupported_criteria` before a lease).

    Other preferences remain private context, not permission to vary the native request.
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
        frozenset(contracts) if len(contracts) > 1 else frozenset(),
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
    other_contract: set[str] = field(default_factory=set)
    unwanted: set[str] = field(default_factory=set)
    refused: Counter[str] = field(default_factory=Counter)
    tokens_seen: bool = False
    # With the run's commits (sweep.Commits): the website's source, and the addresses given to
    # the store (once each; `found` then stays empty, the commits count the source's postings).
    commits: Any = None
    source_id: str = ""
    host: str = ""
    sent: set[str] = field(default_factory=set)

    def _sort(self, title: str, location: str, url: str) -> set[str]:
        """Where a posting read goes: unwanted, of another contract, or kept."""
        titles, places = self.planned.unwanted_titles, self.planned.unwanted_places
        if (titles and titles.search(title)) or (places and places.search(location)):
            return self.unwanted
        if (
            self.planned.contracts
            and kind(title, location, url).value not in self.planned.contracts
        ):
            return self.other_contract
        return self.found

    def place(self, url: str, into: set[str]) -> bool:
        """Put an address in one set only: unwanted wins over another contract, which wins over
        found (fail closed). False when a stronger reading already holds it."""
        order = (self.unwanted, self.other_contract, self.found)
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
        self.searches.update(session.searches)

    async def verified(self, batch: finder.VerifiedBatch) -> None:
        """A verified batch on its way to the store (module docstring)."""
        kept: list[finder.VerifiedFinding] = []
        for finding in batch.findings:
            url = finding.url  # canonical (finder.FinderSession._verified)
            if url in self.sent:
                continue  # stored already: counted once, by the commits
            place = self._sort(finding.title, finding.location, url)
            if place is self.found:
                if url not in self.unwanted and url not in self.other_contract:
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
        # The person's own saved values only (FinderSession.searches), as the summary names them.
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
        if self.planned.contracts:
            entry["other_contract"] = len(self.other_contract)
        if self.unwanted:
            entry["unwanted"] = len(self.unwanted)


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
    sites = [source for source in search.interactive_sites if source.enabled]
    for index, source in enumerate(sites):
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
        if stop:
            entry["error"] = "not_started"
        else:
            # Safe stable identity only. Instructions and search text do not become labels.
            stats.current_board = entry.get("source_id")
            await progress()
            commits = getattr(plan, "commits", None)
            if commits is not None:
                commits.admit(entry["source_id"])
            # The website's share of the sweep's time: what is left, divided by the websites left
            # (jobs J18), so one website never starves the next; one that ends early leaves its
            # rest to the next ones.
            now = runner.monotonic()
            share = now + max(0.0, deadline - now) / (len(sites) - index)
            try:
                entry["error"] = await _one(
                    runner, plan, source, search, entry, min(deadline, share)
                )
            except (BrowserError, StoreUnavailableError):
                entry["error"] = "browser_unavailable"
            except Exception:
                entry["error"] = "agent_failed"
            if entry["error"] in STOPS_SWEEP:
                stop = entry["error"]
            if commits is not None and not await commits.settle():
                # A commit outlived the wait: no later source runs, the row is left as it is.
                entry["error"] = entry["error"] or "store_failed"
                stop = stop or "store_failed"
        sweep.finish_source(entry)
        await progress()


async def _one(
    runner: Runner,
    plan: Any,
    source: InteractiveSite,
    search: SearchSection,
    entry: dict[str, Any],
    deadline: float,
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
    site = _Site(
        planned,
        commits=getattr(plan, "commits", None),
        source_id=entry["source_id"],
        host=entry.get("host") or "",
    )
    try:
        code = await _website(runner, plan, context, planned.scope, site, deadline)
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
) -> str | None:
    """The website's one agent run on one lease, until its share of the sweep's time ends."""
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
    got = await runner.leases.acquire(str(plan.user_id), "sweep", automation=True, hosts=hosts)
    if got.lease is None or got.token is None:
        await runner._give_back(str(plan.user_id), None if got.lease is None else "")
        return "browser_busy"
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
            lease_until=asked + min(got.lease.max_s, 900),
            pause=platform_policy.pause_for(paced),
        )
        return await _search(runner.finder, pages, scope, str(plan.user_id), site)
    finally:
        await runner.leases.release(str(plan.user_id), lease_id, reason="sweep_done")


# The app's own refusals of an agent's search (B19): browserd's, and the api's of an address
# that is no observed link. A model's slip (`model_value`) is its own, never one of them.
APP_REFUSALS = ("unsupported_interaction", "unobserved_address")


def outcome(session: finder.FinderSession) -> str | None:
    """How a website the agent ran ends (2026-10-28, issue 716). It was searched when the agent
    reported a posting code verified, or said after a search the session saw that the website
    listed no matching job (`report_no_postings` no_match). Else the blocker it named (a cookie
    wall, a sign in wall, no search control, a page with no list), but when the app refused one
    of its actions (B19, 2026-10-29: "The app refused the search on this website."), the
    refusal wins, `search_refused`; else `unsupported_interaction` when browserd or the website
    refused one of its actions, `search_refused` when the api refused an address; else
    `nothing_reported`. Opening a search page by its address, pressing a link or a failed click
    that it recovered from are how a person searches too: no kind of action is required, and
    nothing read is no success."""
    if session.found or session.verified_count:
        return None
    refused = any(session.refused[name] for name in APP_REFUSALS)
    if session.outcome:
        code = finder.NO_POSTINGS[session.outcome]
        if code is not None:
            return "search_refused" if refused else code
        if session.searches:
            return None
    if session.refused["unsupported_interaction"]:
        return "unsupported_interaction"
    return "search_refused" if refused else "nothing_reported"


async def _search(
    agent: finder.FinderAgent,
    pages: Pages,
    scope: finder.InteractiveScope,
    user: str,
    site: _Site,
) -> str | None:
    """Bound the complete run, including the first navigation, by the website's share of the
    sweep's time and the lease's (no cap of its own, jobs J18)."""
    remaining = min(pages.deadline - pages.monotonic(), pages.lease_until - pages.monotonic())
    session = None
    cap = asyncio.timeout(max(0.0, remaining))
    try:
        async with cap:
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
            # The start page may land on another address of its own website (Welcome to the
            # Jungle answers `/` on `/en`, Indeed's on the person's country site, B19's family);
            # browserd held that redirect to the frozen scope.
            landed = finder.canonical(pages.last_url)
            if not landed or not finder.on_family(landed, scope.start):
                return "unsupported_interaction"
            site.started = True
            session = finder.FinderSession(
                pages,
                scope.start,
                pages.last_text,
                user_id=user,
                interactive=scope,
                on_verified=None if site.commits is None else site.verified,
                landed="" if landed == scope.start else landed,
            )
            ran = await agent(session)
            site.input_tokens += ran.input_tokens
            site.output_tokens += ran.output_tokens
            site.tokens_seen = True
            if session.stopped:
                return session.stopped
            if ran.stopped:
                return "no_credential" if ran.stopped == "no_credential" else "agent_failed"
            return outcome(session)
    except TimeoutError:
        return "time_budget" if cap.expired() else "agent_failed"
    except SourceError as exc:
        return exc.code
    finally:
        site.pages = pages.count
        if session:
            site.keep(session)
