"""Selected native search sites. J11 supplies truthful incremental finding persistence.

Until then, verified detail findings are counted as partial, never sent to the legacy
company/date store. Protected platforms require the separate current-permission foundation.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from app.browser import finder
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.browser.sources import ERRORS, PAGE_BUDGET_S, STOPS_SWEEP, Pages, SourceError
from app.jobs import sweep
from app.profile.schemas import InteractiveSite, SearchSection

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.browser.sources import Runner

CRITERIA = frozenset(
    {
        "title_include",
        "title_exclude",
        "location",
        "remote_stance",
        "contract_types",
        "seniority",
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


def scope_for(search: SearchSection, start: str = "") -> finder.InteractiveScope:
    """Use exact saved values, with no inferred website-specific filter encoding.

    Multi-value and regular-expression native controls are not yet supported. Other
    preferences remain private context, not permission to vary the native request.
    """
    if search.title_include.advanced_regex or len(search.title_include.terms) != 1:
        raise ValueError("unsupported_criteria")
    if (
        search.location.advanced_regex
        or len(search.location.terms) > 1
        or len(search.contract_types) > 1
    ):
        raise ValueError("unsupported_criteria")
    filters: list[tuple[str, str]] = []
    if search.location.terms:
        filters.append(("location", search.location.terms[0]))
    if search.remote_stance not in (None, "any"):
        filters.append(("remote", search.remote_stance))
    if search.contract_types:
        filters.append(("contract", search.contract_types[0]))
    return finder.InteractiveScope(
        start or search.interactive_sites[0].url,
        search.title_include.terms[0],
        tuple(filters),
        criteria=json.dumps(search.model_dump(mode="json", include=CRITERIA)),
        instructions=search.interactive_instructions or "",
    )


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
    context = SourceContext(
        str(plan.user_id),
        str(plan.sweep_id) if getattr(plan, "sweep_id", None) else None,
        sweep._source_id("llm_browser", source.url),
        source.effective_platform,
        source.url,
    )
    try:
        await _permit(runner.interactive_policy, context)
    except SourceError as exc:
        return exc.code
    try:
        scope = scope_for(search, source.url)
    except ValueError:
        return "unsupported_criteria"
    if runner.finder is None or not await runner.finder.available(str(plan.user_id)):
        return "no_credential"
    asked = runner.monotonic()
    if asked + PAGE_BUDGET_S >= deadline:
        return "time_budget"
    host = urlsplit(scope.start).hostname or ""
    got = await runner.leases.acquire(str(plan.user_id), "sweep", automation=True, hosts=[host])
    if got.lease is None or got.token is None:
        await runner._give_back(str(plan.user_id), None if got.lease is None else "")
        return "browser_busy"
    lease_id = got.lease.lease_id
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
            host=host,
            pacer=runner.pacer,
            lease_until=asked + min(got.lease.max_s, 900),
        )
        return await _search(runner.finder, pages, scope, str(plan.user_id), entry)
    finally:
        await runner.leases.release(str(plan.user_id), lease_id, reason="sweep_done")


async def _search(
    agent: finder.FinderAgent,
    pages: Pages,
    scope: finder.InteractiveScope,
    user: str,
    entry: dict[str, Any],
) -> str | None:
    """Bound the complete source, including the first navigation, by all outer deadlines."""
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
            session = finder.FinderSession(
                pages,
                scope.start,
                pages.last_text,
                user_id=user,
                interactive=scope,
            )
            ran = await agent(session)
            entry["tokens"] = {"input": ran.input_tokens, "output": ran.output_tokens}
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
            return "finding_persistence_pending" if session.found else None
    except TimeoutError:
        return "time_budget" if cap.expired() else "agent_failed"
    except SourceError as exc:
        return exc.code
    finally:
        entry["result_pages"] = pages.count
        if session:
            entry["found"] = len(session.found)
            entry["refused"] = dict(session.refused)
