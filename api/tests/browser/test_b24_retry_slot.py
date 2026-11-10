"""Browser B24 (issue 785): the retry of a website that failed waits for the person's browser as
any website does. The sweep of B23 (issue 780), one browser at once, on a real Redis and
browserd's own lease logic (b3_support): the VIE website's agent fails, the two other websites
run, then the VIE website is tried once more on its own lease, never two leases at once.
"""

import asyncio
import time
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import httpx

from app.browser import sources
from app.browser.sources import interactive, linkedin_guest
from app.jobs import sweep
from tests.browser.b3_support import World, redis_url
from tests.browser.fakes import Known
from tests.browser.test_b23_slot_wait import (
    FIXTURES,
    PERSON,
    VIE,
    WEBSITES,
    Sweep,
    _nothing,
    _yes,
    search_of,
    setup,
    source_id,
    start_of,
    world,
)
from tests.browser.test_linkedin_guest import Allow, LinkedInFake

__all__ = ["redis_url", "world"]


async def run_with_retry(state: Sweep, searched: Any) -> dict[str, dict[str, Any]]:
    """The websites' first attempts, then their one retry, as sweep.run_sweep runs them."""
    search = search_of()
    scope = sweep.freeze_scope(
        SimpleNamespace(sections={"search": search.model_dump(mode="json")}, revision=3),
        sweep.SweepTrigger.manual,
        datetime.fromisoformat("2026-11-10T09:00:00+00:00"),
    )
    stats = sweep.Stats(sources=sweep.initial_sources(scope))
    now = [0.0]
    linkedin = LinkedInFake(now)

    async def slept(seconds: float) -> None:
        now[0] += seconds
        manager = state.world.browserd.manager
        # browserd ends the last lease while the next website asks again: it ends now.
        if seconds == interactive.FREEING_POLL_S and manager.ending is not None:
            state.freed.set()
            await state.slow.ended()

    async def progress() -> None:
        pass

    runner = sources.Runner(
        state.world.store,
        lambda *_: object(),
        base_url=FIXTURES,
        sleep=slept,
        monotonic=lambda: now[0],
        pacer=sources.MemoryPacer(lambda: now[0]),
        finder=SimpleNamespace(available=_yes, profile=_nothing),
        interactive_policy=Allow(),
        guest_http=lambda: httpx.MockTransport(linkedin.handler),
        guest_cooldown=linkedin_guest.MemoryCooldown(lambda: now[0]),
    )
    plan = SimpleNamespace(
        search=search,
        user_id=PERSON,
        search_scope=scope,
        sweep_id=uuid.uuid4(),
        known=Known(),
        rules=None,
    )

    async def search_stand_in(agent: Any, pages: Any, scope: Any, *_: Any) -> str | None:
        state.most = max(state.most, len(state.world.browserd.manager.held()))
        state.searched.append(scope.start)
        return await searched(scope.start, state.searched.count(scope.start))

    interactive_search = interactive._search
    interactive._search = search_stand_in
    deadline = time.monotonic() + sweep.BROWSER_SEARCH_MAX_S
    try:
        ran = await sweep._bound_by(runner.websites, plan, stats, progress, deadline)
        await sweep._retry([ran], plan, stats, progress, deadline)
    finally:
        interactive._search = interactive_search
        state.freed.set()
        await state.slow.ended()
        await state.world.close()
    return {entry["source_id"]: entry for entry in stats.sources}


def test_one_browser_the_failed_websites_retry_waits_for_the_browser_after_the_others(
    world: World,
) -> None:
    state = setup(world)

    async def searched(start: str, attempt: int) -> str | None:
        return "agent_failed" if start == start_of(WEBSITES[0]) and attempt == 1 else None

    entries = asyncio.run(run_with_retry(state, searched))
    starts = [start_of(site) for site in WEBSITES]
    assert state.searched == [*starts, starts[0]]
    # Four leases of the one browser, one at a time: the retry took its turn as any website.
    assert [r.purpose for r in state.world.browserd.starts] == ["sweep"] * 4
    assert state.most == 1
    vie = entries[source_id(WEBSITES[0])]
    assert (vie["attempt"], vie["outcome"], vie["first_attempt"]["reason"]) == (
        2,
        "no_matches",
        "agent_failed",
    )
    assert VIE in [site["url"] for site in WEBSITES]
