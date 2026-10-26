"""The API lease mirror must bank the actual browser's paused takeover clock."""

import asyncio
import uuid

import pytest

from app.browser import live_router
from app.browser.leases import MAX_GRACE_S
from tests.browser.b3_support import World, flush, redis_url

__all__ = ["redis_url"]


@pytest.mark.parametrize("give_back", ["owner", "expiry"])
def test_takeover_longer_than_active_limit_survives_in_both_lease_stores(redis_url, give_back):
    flush(redis_url)

    async def go():
        from browserd.live import live_of

        world = World(redis_url)
        try:
            owner, viewer = str(uuid.uuid4()), str(uuid.uuid4())
            got = await world.store.acquire(owner, "apply_fill", True)
            assert got.lease is not None
            lease_id = got.lease.lease_id
            world.clock.advance(600)
            await live_router._set_input(world.store, owner, viewer)
            waiting = (await world.store.current(owner)).lease
            assert waiting is not None
            assert waiting.wait_reason == "takeover"
            world.clock.advance(899)
            still = (await world.store.current(owner)).lease
            assert still is not None
            assert still.active_s(world.clock()) == 600
            live = live_of(world.browserd.service)
            # Another tab cannot give this viewer's input back or resume the mirror clock.
            await live_router._set_input(world.store, owner, None, str(uuid.uuid4()))
            assert live.holder(lease_id) == viewer
            if give_back == "owner":
                await live_router._set_input(world.store, owner, None, viewer)
            else:
                world.clock.advance(1)
                live.tick()
            after = (await world.store.current(owner)).lease
            assert after is not None
            assert after.state == "active"
            assert after.active_s(world.clock()) == 600
            assert world.browserd.manager.current() is not None
        finally:
            await world.close()

    asyncio.run(go())


def test_an_active_browser_cannot_avoid_the_api_hard_maximum(redis_url):
    flush(redis_url)

    async def go():
        world = World(redis_url)
        try:
            owner = str(uuid.uuid4())
            got = await world.store.acquire(owner, "apply_fill", True)
            assert got.lease is not None
            world.clock.advance(got.lease.max_s + MAX_GRACE_S)
            assert (await world.store.current(owner)).lease is None
        finally:
            await world.close()

    asyncio.run(go())


def test_browser_transport_and_submit_job_allow_the_specified_takeover_wait():
    import httpx
    from browserd.live import TAKEOVER_S

    from app import worker
    from app.browser import client, leases
    from tests.conftest import make_settings

    timeouts = []

    async def handle(request):
        timeouts.append((request.url.path, request.extensions["timeout"]))
        return httpx.Response(200, json={"ok": True, "lease": None, "last_end": None})

    async def go():
        transport = httpx.MockTransport(handle)
        browser = client.BrowserdClient("http://browser:9300", "e2e-only", transport=transport)
        await browser.tool("lease", "token", "browser_snapshot")
        await browser.gate_click("lease", "token", "e1", "e2e-capability")
        await browser.linkedin_open("lease", "token", "123")
        await browser.current()

    asyncio.run(go())
    for _path, timeout in timeouts[:3]:
        assert timeout["read"] >= TAKEOVER_S + client.TOOL_TIMEOUT_S
        assert timeout["connect"] == client.TIMEOUT_S
    assert timeouts[-1][1]["read"] == client.TIMEOUT_S
    settings = make_settings("postgresql+psycopg://unused/unused")
    queue = worker.apply_queue(settings)
    submit = next(f for f in queue.functions if f.name == "apply_submit")
    assert (
        submit.timeout_s
        >= leases.LEASE_MAX_S["apply_submit"] + TAKEOVER_S + worker.APPLY_JOB_MARGIN_S
    )
