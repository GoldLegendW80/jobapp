"""Private Apply waits on real Redis and browserd's actual HTTP control routes."""

import pytest

from app.browser.client import BrowserError
from app.browser.tools import LeaseTools
from tests.browser.b3_support import World, redis_url
from tests.browser.test_b3_leases import A, B, run, world

__all__ = ["redis_url", "world"]


@pytest.mark.parametrize("reason", ["captcha", "email_code", "forbidden"])
def test_private_wait_recovers_after_redis_loss_without_refresh_and_resumes_remaining_time(
    world: World, reason: str
):
    async def go():
        store = world.make_store()
        store.session_max_s = 60
        world.store = store
        held = await store.acquire(A, "apply_fill", True)
        lease_id, token = held.lease.lease_id, held.token
        tools = LeaseTools(store, world.browserd.client(), A, lease_id, token)
        world.clock.advance(240)
        waiting = await tools._apply_wait("begin", reason)
        browser_lease = world.browserd.manager.current()
        deadline = browser_lease.wait_deadline
        assert waiting["wait_left_s"] == 1800
        assert (await store.current(A)).lease.wait_deadline == deadline
        other = LeaseTools(store, world.browserd.client(), B, lease_id, token)
        with pytest.raises(BrowserError, match="ended"):
            await other._apply_wait("resume")
        wrong = LeaseTools(store, world.browserd.client(), A, lease_id, "wrong")
        with pytest.raises(BrowserError, match="refused"):
            await wrong._apply_wait("resume")
        world.clock.advance(1500)
        await (await world.redis()).flushdb()
        assert (await tools._apply_wait("read"))["wait_left_s"] == 300
        restored = (await store.current(A)).lease
        assert restored.wait_deadline == deadline
        assert restored.active_s(world.clock()) == 240
        await tools._apply_wait("begin", reason)
        assert browser_lease.wait_deadline == deadline
        await tools._apply_wait("resume")
        restored = (await store.current(A)).lease
        assert restored.state == "active"
        assert restored.active_s(world.clock()) == 240
        assert restored.last_input == world.clock()
        for _ in range(10):
            world.clock.advance(60)
            browser_lease.touch(world.clock())
            assert await world.browserd.manager.tick() is None
        world.clock.advance(60)
        assert await world.browserd.manager.tick() == "lease_max"
        assert (await store.current(A)).lease is None

    run(world, go)
