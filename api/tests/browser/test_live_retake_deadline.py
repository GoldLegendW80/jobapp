"""A continuous Take over pause has one deadline, across viewers and both lease stores."""

import asyncio
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd.leases import LeaseError
from browserd.live import PERSON_USED, TAKEOVER_S, live_of

from app.browser import live_router
from app.browser.client import BrowserError
from app.browser.leases import MAX_GRACE_S
from tests.browser.b3_support import World, flush, redis_url
from tests.browser.test_b4_live_unit import World as NativeWorld

__all__ = ["redis_url"]


@dataclass(eq=False)
class PresentViewer:
    """An open viewer, without an x11vnc process or a network connection."""

    lease_id: str
    viewer: str

    def close(self):
        pass


@pytest.mark.parametrize("same_viewer", [True, False])
def test_retake_keeps_the_original_native_and_public_deadline(same_viewer):
    async def go():
        world = NativeWorld()
        lease_id = await world.lease()
        lease = world.manager.current()
        assert lease is not None
        first = str(uuid.uuid4())
        second = first if same_viewer else str(uuid.uuid4())
        world.live.viewers = {PresentViewer(lease_id, first), PresentViewer(lease_id, second)}
        world.live.take(lease_id, first)  # t=1000, deadline=1900
        generation = lease.scratch["input_epoch"]
        world.clock.now = 1800
        world.live.take(lease_id, second)
        assert world.live.holder(lease_id) == second
        assert lease.scratch["input_epoch"] == generation + 1
        assert world.live.input_until == lease.wait_deadline == 1900
        assert lease.public(world.clock())["wait_left_s"] == 100
        world.clock.now = 1899
        world.live.tick()
        assert world.live.holder(lease_id) == second
        world.clock.now = 1900
        world.live.tick()
        assert world.live.holder(lease_id) is None
        assert lease.public(world.clock())["state"] == "active"
        assert lease.wait_deadline is None
        assert await world.live.agent_turn(lease_id) == PERSON_USED
        world.clock.now = 1960
        assert lease.expired(world.clock()) is None
        assert lease.active_s(world.clock()) == 60

    asyncio.run(go())


@pytest.mark.parametrize("late_s", [0, 1, MAX_GRACE_S])
def test_retake_at_or_after_the_deadline_gives_back_without_renewing(late_s):
    async def go():
        world = NativeWorld()
        lease_id = await world.lease()
        lease = world.manager.current()
        assert lease is not None
        world.live.take(lease_id, str(uuid.uuid4()))
        deadline = lease.wait_deadline
        assert deadline is not None
        world.clock.now = deadline + late_s
        # No Live.tick ran between the deadline and this control request.
        with pytest.raises(LeaseError) as refused:
            world.live.take(lease_id, str(uuid.uuid4()))
        assert refused.value.status == 409
        assert world.live.holder(lease_id) is None
        assert world.live.input_until == deadline
        assert lease.state == "active"
        assert lease.wait_deadline is None
        assert world.live.given_back.is_set()
        assert await world.live.agent_turn(lease_id) == PERSON_USED

    asyncio.run(go())


@pytest.mark.parametrize("same_viewer", [True, False])
def test_retake_control_route_keeps_redis_and_native_clocks_in_step(redis_url, same_viewer):
    flush(redis_url)

    async def go():
        world = World(redis_url)
        try:
            world.clock.now = 400
            owner, foreign = str(uuid.uuid4()), str(uuid.uuid4())
            first = str(uuid.uuid4())
            second = first if same_viewer else str(uuid.uuid4())
            got = await world.store.acquire(owner, "apply_fill", True)
            assert got.lease is not None
            lease_id = got.lease.lease_id
            native = world.browserd.manager.current()
            assert native is not None
            live = live_of(world.browserd.service)
            live.viewers = {PresentViewer(lease_id, first), PresentViewer(lease_id, second)}
            world.clock.now = 1000
            await live_router._set_input(world.store, owner, first)
            world.clock.now = 1800
            await live_router._set_input(world.store, owner, second)
            mirrored = (await world.store.current(owner)).lease
            public = (await world.store.client.current())["lease"]
            assert mirrored is not None
            assert live.input_until == native.wait_deadline == mirrored.wait_deadline == 1900
            assert public["wait_left_s"] == mirrored.wait_deadline - world.clock() == 100
            assert mirrored.active_s(world.clock()) == native.active_s(world.clock()) == 600
            with pytest.raises(HTTPException) as refused:
                await live_router._set_input(world.store, foreign, str(uuid.uuid4()))
            assert refused.value.status_code == 403
            if not same_viewer:
                await live_router._set_input(world.store, owner, None, first)
            assert live.holder(lease_id) == second
            assert (await world.store.current(owner)).lease.wait_deadline == 1900
            world.clock.now = 1899
            live.tick()
            assert live.holder(lease_id) == second
            world.clock.now = 1900
            live.tick()
            after = (await world.store.current(owner)).lease
            assert after is not None
            assert after.state == native.state == "active"
            assert live.holder(lease_id) is None
            assert after.active_s(world.clock()) == native.active_s(world.clock()) == 600
            # This is the reviewer's exact t=1960 _settle path, now without person_timeout.
            world.clock.now = 1960
            after = (await world.store.current(owner)).lease
            assert after is not None
            assert after.active_s(world.clock()) == native.active_s(world.clock()) == 660
            # A later, separate pause gets its own allowance; Give back resumes both stores.
            await live_router._set_input(world.store, owner, first)
            assert live.input_until == native.wait_deadline == 1960 + TAKEOVER_S
            world.clock.advance(1)
            await live_router._set_input(world.store, owner, None, first)
            after = (await world.store.current(owner)).lease
            assert after is not None
            assert after.state == native.state == "active"
            assert after.active_s(world.clock()) == native.active_s(world.clock()) == 660
            world.clock.advance(240)
            assert native.expired(world.clock()) == "lease_max"
            await world.browserd.manager.tick()
            assert (await world.store.current(owner)).lease is None
            # Release during a fresh pause clears that lease's holder before the next lease.
            got = await world.store.acquire(owner, "apply_fill", True)
            assert got.lease is not None
            await live_router._set_input(world.store, owner, first)
            await world.store.release(owner, got.lease.lease_id)
            live.tick()
            assert live.holder(got.lease.lease_id) is None
            assert live.given_back.is_set()
        finally:
            await world.close()

    asyncio.run(go())


@pytest.mark.parametrize("late_s", [0, 1, MAX_GRACE_S])
def test_expired_retake_control_request_resumes_the_redis_mirror(redis_url, late_s):
    flush(redis_url)

    async def go():
        world = World(redis_url)
        try:
            owner, first, second = (str(uuid.uuid4()) for _ in range(3))
            got = await world.store.acquire(owner, "apply_fill", True)
            assert got.lease is not None
            lease_id = got.lease.lease_id
            live = live_of(world.browserd.service)
            live.viewers = {PresentViewer(lease_id, first), PresentViewer(lease_id, second)}
            await live_router._set_input(world.store, owner, first)
            world.clock.advance(TAKEOVER_S + late_s)
            # Exercise the actual native control route before its periodic tick runs.
            with pytest.raises(BrowserError) as refused:
                await world.store.client.live_input(lease_id, second)
            assert refused.value.code == "busy"
            assert live.holder(lease_id) is None
            current = (await world.store.current(owner)).lease
            assert current is not None
            assert current.state == "active"
            assert current.wait_deadline is None
            assert (await world.store.client.current())["lease"]["state"] == "active"
        finally:
            await world.close()

    asyncio.run(go())
