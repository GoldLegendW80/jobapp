"""Selected native search sites. J11 supplies truthful incremental finding persistence.

Until then, verified detail findings are counted as partial, never sent to the legacy
company/date store. Protected platforms need the person's current permission, checked by the
runner's InteractivePolicy (app/browser/platform_policy.py, browser B14).

One search per combination (owner, 2026-10-26): a website runs one native search per pair of a
title term and a location term, in the order saved (titles first, then places), at most
SEARCHES_MAX a sweep. Each search is its own lease with its own frozen scope (browserd binds one
scope to a lease), so only that search's exact words and filters can be typed in it. The
searches of a website share its limits: the finder's PAGES_MAX pages, its TOKEN_BUDGET, the
platform's pace key and the platform policy's caps (LinkedIn's pages and its one sweep a day).
Their postings merge into the website's one result, counted once per canonical address. One
contract type is the website's own filter; several are checked on what was read (the engine's
`kind` of the title, location and address), as a native filter takes one value. Places are
typed as the person wrote them, never mapped to a website's own codes. Unwanted titles and
unwanted places reach the agent as plain context (`criteria`) and are dropped after reading.
"""

from __future__ import annotations

import asyncio
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
from app.profile import matching
from app.profile.schemas import InteractiveSite, SearchSection, TermList

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.browser.sources import Runner

CRITERIA = frozenset(
    {
        "title_include",
        "title_exclude",
        "location",
        "location_exclude",  # unwanted places, once the plain lists land (read defensively)
        "remote_stance",
        "contract_types",
        "seniority",
        "days",
        "compensation",
    }
)
SEARCHES_MAX = 6  # native searches per website per sweep (owner, 2026-10-26)
# A saved regular expression is read as a plain term only when it is one: words of letters or
# digits joined by single spaces, hyphens or apostrophes, with no character a pattern reads.
PLAIN = re.compile(r"[^\W_]+(?:[ '-][^\W_]+)*")


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

    def __init__(self, tools: Any, policy: InteractivePolicy | None, context: SourceContext):
        self.tools, self.policy, self.context = tools, policy, context
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
        return await self.tools.call(name, args)

    async def snapshot(self) -> dict[str, Any]:
        await self._before("browser_snapshot")
        shot = await self.tools.snapshot()
        if shot.get("ok"):
            url, text = shot.get("url"), shot.get("snapshot")
            if (
                not isinstance(url, str)
                or len(url) > 2000
                or not isinstance(text, str)
                or len(text) > finder.SNAPSHOT_MAX
                or finder.origin(finder.canonical(url)) != finder.origin(self.context.url)
            ):
                raise SourceError("page_failed")
            if self.policy:
                _refuse(await self.policy.observe(self.context, url, text))
        return shot

    async def wait_for(self, **args: Any) -> dict[str, Any]:
        await self._before("browser_wait_for")
        return await self.tools.wait_for(**args)


def _plain(terms: TermList) -> list[str]:
    """The saved terms as typed words; a regular expression only when it is a plain word."""
    if terms.advanced_regex and not all(PLAIN.fullmatch(term) for term in terms.terms):
        raise ValueError("unsupported_criteria")
    return list(terms.terms)


@dataclass(frozen=True)
class Searches:
    """The native searches of one website, frozen from the sweep's frozen criteria."""

    scopes: tuple[finder.InteractiveScope, ...]
    total: int  # combinations before the SEARCHES_MAX cap
    contracts: frozenset[str] = frozenset()  # checked after reading, empty when native
    unwanted_titles: re.Pattern[str] | None = None
    unwanted_places: re.Pattern[str] | None = None


def _unwanted(terms: Any) -> re.Pattern[str] | None:
    """A term list read defensively: a TermList, a plain list of words, or absent."""
    if isinstance(terms, list):
        return matching.compile_terms([t for t in terms if isinstance(t, str) and t], False)
    words = getattr(terms, "terms", None)
    if not isinstance(words, list):
        return None
    return matching.compile_terms(words, getattr(terms, "advanced_regex", False) is True)


def searches_for(search: SearchSection, start: str = "") -> Searches:
    """Use exact saved values, with no inferred website-specific filter encoding.

    Other preferences remain private context, not permission to vary the native request.
    """
    titles = _plain(search.title_include)
    places: list[str | None] = [*_plain(search.location)] or [None]
    if not titles:
        raise ValueError("unsupported_criteria")
    shared: list[tuple[str, str]] = []
    if search.remote_stance not in (None, "any"):
        shared.append(("remote", search.remote_stance))
    contracts = list(search.contract_types)
    if len(contracts) == 1:
        shared.append(("contract", contracts[0]))
    criteria = json.dumps(search.model_dump(mode="json", include=CRITERIA))
    pairs = [(title, place) for title in titles for place in places]
    return Searches(
        tuple(
            finder.InteractiveScope(
                start or search.interactive_sites[0].url,
                title,
                (*((("location", place),) if place else ()), *shared),
                criteria=criteria,
                instructions=search.interactive_instructions or "",
            )
            for title, place in pairs[:SEARCHES_MAX]
        ),
        len(pairs),
        frozenset(contracts) if len(contracts) > 1 else frozenset(),
        _unwanted(search.title_exclude),
        _unwanted(getattr(search, "location_exclude", None)),
    )


@dataclass
class _Site:
    """What the searches of one website read together: their shared budgets and postings."""

    planned: Searches
    pages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0
    found: set[str] = field(default_factory=set)
    other_contract: set[str] = field(default_factory=set)
    unwanted: set[str] = field(default_factory=set)
    refused: Counter[str] = field(default_factory=Counter)
    tokens_seen: bool = False

    def keep(self, session: finder.FinderSession) -> None:
        for posting in session.found:
            url = finder.canonical(posting.url) or posting.url
            contract = kind(posting.title, posting.location, url).value
            titles, places = self.planned.unwanted_titles, self.planned.unwanted_places
            if (titles and titles.search(posting.title)) or (
                places and places.search(posting.location)
            ):
                self.unwanted.add(url)
            elif self.planned.contracts and contract not in self.planned.contracts:
                self.other_contract.add(url)
            else:
                self.found.add(url)
        self.refused.update(session.refused)

    def write(self, entry: dict[str, Any]) -> None:
        entry["result_pages"] = self.pages
        entry["searches"] = self.searches
        if self.tokens_seen:
            entry["tokens"] = {"input": self.input_tokens, "output": self.output_tokens}
        if self.searches:
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
    for source in search.interactive_sites:
        if not source.enabled:
            continue
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
            try:
                entry["error"] = await _one(runner, plan, source, search, entry, deadline)
            except (BrowserError, StoreUnavailableError):
                entry["error"] = "browser_unavailable"
            except Exception:
                entry["error"] = "agent_failed"
            if entry["error"] in STOPS_SWEEP:
                stop = entry["error"]
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
    entry.update(searches_planned=len(planned.scopes), searches_total=planned.total)
    site = _Site(planned)
    code: str | None = None
    try:
        for index, scope in enumerate(planned.scopes):
            if site.pages >= finder.PAGES_MAX:
                code = "page_budget"
            elif site.input_tokens >= finder.TOKEN_BUDGET:
                code = "token_budget"
            else:
                code = await _combination(runner, plan, context, scope, site, deadline, index)
            site.write(entry)
            if code is not None:
                break
    finally:
        site.write(entry)
    return code or ("finding_persistence_pending" if site.found else None)


async def _combination(
    runner: Runner,
    plan: Any,
    context: SourceContext,
    scope: finder.InteractiveScope,
    site: _Site,
    deadline: float,
    index: int,
) -> str | None:
    """One native search on its own lease, inside what the website's earlier searches left."""
    from app.browser import platform_policy

    if index:
        # The first search's permission was read just before; a later one reads it again.
        try:
            await _permit(runner.interactive_policy, context)
        except SourceError as exc:
            return exc.code
    asked = runner.monotonic()
    if asked + PAGE_BUDGET_S >= deadline:
        return "time_budget"
    host = urlsplit(scope.start).hostname or ""
    got = await runner.leases.acquire(str(plan.user_id), "sweep", automation=True, hosts=[host])
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
        pages.count = site.pages  # the website's searches share its page cap
        return await _search(runner.finder, pages, scope, str(plan.user_id), site)
    finally:
        await runner.leases.release(str(plan.user_id), lease_id, reason="sweep_done")


async def _search(
    agent: finder.FinderAgent,
    pages: Pages,
    scope: finder.InteractiveScope,
    user: str,
    site: _Site,
) -> str | None:
    """Bound the complete search, including the first navigation, by all outer deadlines."""
    remaining = min(
        finder.WALL_TIME_S,
        pages.deadline - pages.monotonic(),
        pages.lease_until - pages.monotonic(),
    )
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
            if finder.canonical(pages.last_url) != scope.start:
                return "unsupported_interaction"
            site.searches += 1
            session = finder.FinderSession(
                pages,
                scope.start,
                pages.last_text,
                user_id=user,
                token_budget=finder.TOKEN_BUDGET - site.input_tokens,
                interactive=scope,
            )
            ran = await agent(session)
            site.input_tokens += ran.input_tokens
            site.output_tokens += ran.output_tokens
            site.tokens_seen = True
            if session.stopped:
                return session.stopped
            if ran.stopped:
                return (
                    ran.stopped
                    if ran.stopped in {"no_credential", "token_budget", "turn_limit"}
                    else "agent_failed"
                )
            if session.refused or not all(
                session.interactions[name] for name in ("browser_type", "browser_click")
            ):
                return "unsupported_interaction"
            return None
    except TimeoutError:
        return "time_budget" if cap.expired() else "agent_failed"
    except SourceError as exc:
        return exc.code
    finally:
        site.pages = pages.count
        if session:
            site.keep(session)
