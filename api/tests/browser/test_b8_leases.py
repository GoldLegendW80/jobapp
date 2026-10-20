"""Browser B8: the one preemption of SPEC-browser "The service". A sweep lease ends at once,
through browserd's DELETE /leases/{id}, when a person asks for their own browser; every other
lease still ends first. Real Redis, browserd's own lease logic behind a transport (b3_support)."""

import pytest

from app.browser.client import BrowserError
from app.browser.tools import LeaseTools
from tests.browser.b3_support import World, flush, redis_url  # noqa: F401  (fixture)
from tests.browser.test_b3_leases import A, B, run


@pytest.fixture
def world(redis_url: str) -> World:  # noqa: F811
    flush(redis_url)
    return World(redis_url)


@pytest.mark.parametrize("purpose", ["takeover", "signin"])
@pytest.mark.parametrize("who", [A, B])
def test_a_persons_own_browser_ends_a_sweep_at_once(world: World, purpose: str, who: str) -> None:
    async def go() -> None:
        swept = await world.store.acquire(A, "sweep", True)
        assert swept.lease is not None
        assert swept.token is not None
        tools = LeaseTools(
            world.store, world.browserd.client(), A, swept.lease.lease_id, swept.token
        )
        got = await world.store.acquire(who, purpose, False)
        assert got.lease is not None  # no wait
        assert got.lease.purpose == purpose
        assert world.browserd.manager.current() is not None
        # The sweep's next tool call is refused: its source reports lease_ended.
        with pytest.raises(BrowserError) as caught:
            await tools.snapshot()
        assert caught.value.code == "ended"
        assert not await world.store.holds(A, swept.lease.lease_id)

    run(world, go)


@pytest.mark.parametrize("held", ["apply_fill", "apply_submit", "full_auto", "takeover"])
def test_no_other_lease_is_preempted(world: World, held: str) -> None:
    async def go() -> None:
        first = await world.store.acquire(A, held, held != "takeover")
        assert first.lease is not None
        got = await world.store.acquire(B, "takeover" if held != "takeover" else "signin", False)
        assert got.lease is None
        assert got.waiting is not None
        assert (await world.store.current(A)).lease is not None

    run(world, go)


@pytest.mark.parametrize("purpose", ["apply_fill", "full_auto", "sweep", "profile_edit"])
def test_only_a_persons_own_session_preempts_a_sweep(world: World, purpose: str) -> None:
    async def go() -> None:
        swept = await world.store.acquire(A, "sweep", True)
        assert swept.lease is not None
        got = await world.store.acquire(B, purpose, True)
        assert got.lease is None
        assert (await world.store.current(A)).lease is not None

    run(world, go)
