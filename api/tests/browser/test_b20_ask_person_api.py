"""B20: the api's copy of a sweep lease that browserd put in `waiting_person` for
browser_ask_person: the reason `ask_person` and the tab's host reach GET /api/browser's lease
(`LeaseOut`), the wait suspends the idle timer, and resuming gives the lease back to the agent."""

import pytest

from app.browser.router import _lease_out
from tests.browser.b3_support import IDLE_S, World, flush, redis_url  # noqa: F401  (fixture)
from tests.browser.test_b3_leases import A, run

HOST = "jobs.example.org"


@pytest.fixture
def world(redis_url: str) -> World:  # noqa: F811
    flush(redis_url)
    return World(redis_url)


def test_the_ask_reaches_the_status_route_and_suspends_the_idle_timer(world: World) -> None:
    async def go() -> None:
        store = world.store
        held = await store.acquire(A, "sweep", True)
        assert held.lease is not None
        lease_id = held.lease.lease_id
        world.browserd.down = True  # the api's own timers apply
        assert await store.wait_person(A, lease_id, "ask_person", HOST, 900) is not None
        world.clock.advance(IDLE_S * 2)
        view = await store.current(A)
        assert view.lease is not None
        shown = _lease_out(view.lease, world.clock())
        assert (shown.state, shown.wait_reason, shown.wait_host) == (
            "waiting_person",
            "ask_person",
            HOST,
        )
        assert shown.wait_minutes_left is not None
        assert 0 < shown.wait_minutes_left <= 15
        await store.resume(A, lease_id)
        view = await store.current(A)
        assert view.lease is not None
        after = _lease_out(view.lease, world.clock())
        assert (after.state, after.wait_reason, after.wait_host) == ("active", None, None)

    run(world, go)
