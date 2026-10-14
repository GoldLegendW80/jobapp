"""The browser's turns (browser B3, app/browser/leases.py) on a real Redis and browserd's own lease
logic (b3_support.FakeBrowserd), with a clock the test moves.

The rule under test, as a property: one turn at a time; the turn goes to the head of the first
non empty tier; time spent `waiting_person` counts against neither the purpose's maximum nor the
idle timer; nobody learns another person's lease; the lease token lives in the caller's memory
only.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import httpx
import pytest

from app.browser.client import BrowserdClient, BrowserError
from app.browser.leases import (
    HOLDER,
    LEASE_MAX_S,
    QUEUES,
    START_GRACE_S,
    SYNCED,
    TICKET,
    TICKET_TTL_S,
    Lease,
    StoreUnavailableError,
    lease_max_table,
)
from tests.browser.b3_support import IDLE_S, SESSION_MAX_S, World, flush, redis_url

__all__ = ["redis_url"]

A, B, C, D, E = (f"0000000{n}-0000-4000-8000-000000000000" for n in range(1, 6))


@pytest.fixture
def world(redis_url: str) -> Iterator[World]:
    flush(redis_url)
    made = World(redis_url)
    return made


def run(world: World, scenario: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        try:
            return await scenario()
        finally:
            await world.close()

    return asyncio.run(go())


# --- the clocks, pure ---------------------------------------------------------------------------


def apply_lease(now: float = 0.0) -> Lease:
    return Lease(
        lease_id="l",
        user_id=A,
        purpose="apply_fill",
        automation=True,
        profile_id="p",
        max_s=900,
        idle_s=IDLE_S,
        started_at=now,
        state="active",
        active_since=now,
        last_input=now,
    )


def work(lease: Lease, now: float, seconds: int) -> float:
    """`seconds` of work with a tool call every minute; the new time."""
    for _ in range(seconds // 60):
        now += 60
        assert lease.expired(now) is None, now
        lease.touch(now)
    return now


def test_wait_counts_against_neither_the_maximum_nor_the_idle_timer() -> None:
    # The acceptance line: 10 minutes of work, 25 of wait, 4 more of work on a 15 minute lease.
    lease = apply_lease()
    now = work(lease, 0.0, 600)
    lease.wait_person(now, "signin", "Example Portal", SESSION_MAX_S)
    now += 25 * 60
    assert lease.expired(now) is None
    assert lease.active_s(now) == 600
    lease.resume(now)
    now = work(lease, now, 240)
    assert lease.expired(now) is None
    assert lease.active_s(now) == 840
    assert lease.state == "active"


@pytest.mark.parametrize(
    ("after", "expected"),
    [(299, None), (300, "idle"), (301, "idle")],
)
def test_idle_boundary(after: int, expected: str | None) -> None:
    lease = apply_lease()
    assert lease.expired(after) == expected


def test_maximum_boundary_counts_active_time_only() -> None:
    lease = apply_lease()
    now = work(lease, 0.0, 840)
    lease.wait_person(now, "signin", "x", SESSION_MAX_S)
    lease.resume(now + 100)
    assert lease.expired(now + 100 + 59) is None
    assert lease.expired(now + 100 + 60) == "lease_max"


@pytest.mark.parametrize(("after", "expected"), [(1799, None), (1800, "person_timeout")])
def test_wait_deadline_boundary(after: int, expected: str | None) -> None:
    lease = apply_lease()
    lease.wait_person(10.0, "signin", "x", SESSION_MAX_S)
    assert lease.expired(10.0 + after) == expected


def test_resume_while_active_changes_nothing() -> None:
    lease = apply_lease()
    lease.resume(50.0)
    assert (lease.state, lease.active_since, lease.last_input) == ("active", 0.0, 0.0)


def test_a_starting_turn_is_dropped_after_the_grace() -> None:
    lease = apply_lease()
    lease.state = "starting"
    assert lease.expired(START_GRACE_S - 1) is None
    assert lease.expired(START_GRACE_S) == "start_failed"


def test_the_maximum_table_is_browserds() -> None:
    from browserd.config import LEASE_MAX_S as BROWSERD_TABLE
    from browserd.config import lease_max_table as browserd_table

    assert LEASE_MAX_S == BROWSERD_TABLE
    for raw in ("", "apply_fill=1200", "sweep=60, takeover=3600"):
        assert lease_max_table(raw) == browserd_table(raw)
    for bad in ("nope=10", "apply_fill=1", "apply_fill=abc", "apply_fill"):
        with pytest.raises(ValueError, match="is not purpose=seconds"):
            lease_max_table(bad)


# --- the store ------------------------------------------------------------------------------------


def test_two_people_at_once_one_holds_the_other_is_position_1(world: World) -> None:
    async def go() -> None:
        first, second = await asyncio.gather(
            world.store.acquire(A, "takeover", False), world.store.acquire(B, "takeover", False)
        )
        holders = [got for got in (first, second) if got.lease is not None]
        waiters = [got for got in (first, second) if got.waiting is not None]
        assert len(holders) == 1
        assert len(waiters) == 1
        assert waiters[0].waiting is not None
        assert waiters[0].waiting.position == 1
        assert len(world.browserd.starts) == 1
        assert holders[0].token == world.browserd.tokens[0]

    run(world, go)


def test_asking_again_is_idempotent(world: World) -> None:
    async def go() -> None:
        held = await world.store.acquire(A, "takeover", False)
        again = await world.store.acquire(A, "takeover", False)
        assert again.lease is not None
        assert held.lease is not None
        assert again.lease.lease_id == held.lease.lease_id
        assert again.token is None  # the token goes to the call that started the lease only
        await world.store.acquire(B, "sweep", True)
        place = await world.store.acquire(B, "sweep", True)
        assert place.waiting is not None
        assert place.waiting.position == 1
        assert len(world.browserd.starts) == 1

    run(world, go)


def test_priorities_takeover_before_full_auto_before_sweep(world: World) -> None:
    async def go() -> None:
        store = world.store
        await store.acquire(A, "apply_fill", True)
        # Arrival order: sweep, full_auto, apply, takeover.
        await store.acquire(B, "sweep", True)
        await store.acquire(C, "full_auto", True)
        await store.acquire(D, "apply_submit", True)
        await store.acquire(E, "takeover", False)
        positions = {who: (await store.current(who)).waiting[0].position for who in (B, C, D, E)}
        assert positions == {E: 1, D: 2, C: 3, B: 4}
        assert await store.release(A)
        # The sweep asks first, but the person's session is the head.
        assert (await store.acquire(B, "sweep", True)).lease is None
        assert (await store.acquire(E, "takeover", False)).lease is not None
        await store.release(E)
        assert (await store.acquire(C, "full_auto", True)).lease is None
        assert (await store.acquire(D, "apply_submit", True)).lease is not None

    run(world, go)


def test_a_waiting_person_cancels(world: World) -> None:
    async def go() -> None:
        store = world.store
        await store.acquire(A, "takeover", False)
        await store.acquire(B, "takeover", False)
        await store.acquire(C, "takeover", False)
        assert (await store.current(C)).waiting[0].position == 2
        assert await store.cancel(B) == 1
        assert (await store.current(B)).waiting == ()
        assert (await store.current(C)).waiting[0].position == 1
        assert await store.cancel(B) == 0

    run(world, go)


def test_a_waiter_that_stops_asking_leaves_the_queue(world: World) -> None:
    async def go() -> None:
        store, r = world.store, await world.redis()
        await store.acquire(A, "takeover", False)
        await store.acquire(B, "takeover", False)
        await store.acquire(C, "takeover", False)
        member = f"{B}|takeover"
        assert 0 < await r.ttl(TICKET + member) <= TICKET_TTL_S
        await r.delete(TICKET + member)  # what Redis does TICKET_TTL_S after B's last call
        assert (await store.current(C)).waiting[0].position == 1
        assert await r.zrank(QUEUES[0], member) is None

    run(world, go)


def test_the_holders_process_dies_and_the_lease_expires_after_the_idle_time(
    world: World,
) -> None:
    async def go() -> None:
        store = world.store
        await store.acquire(A, "sweep", True)
        world.browserd.down = True  # nobody can tell the api that the lease ended
        world.clock.advance(IDLE_S - 1)
        assert (await store.current(B)).held_by_other
        world.clock.advance(1)
        view = await store.current(B)
        assert not view.held_by_other
        assert view.lease is None
        world.browserd.down = False
        assert (await store.acquire(B, "takeover", False)).lease is None  # browserd still holds A
        assert world.browserd.manager.current() is not None

    run(world, go)


def test_an_idle_lease_is_ended_in_browserd_too(world: World) -> None:
    async def go() -> None:
        await world.store.acquire(A, "sweep", True)
        world.clock.advance(IDLE_S)
        assert (await world.store.current(A)).lease is None
        assert world.browserd.manager.current() is None
        assert world.browserd.chromiums[0].stopped
        assert (await world.store.acquire(B, "takeover", False)).lease is not None

    run(world, go)


def test_touch_keeps_a_lease_alive(world: World) -> None:
    async def go() -> None:
        held = await world.store.acquire(A, "apply_fill", True)
        assert held.lease is not None
        for _ in range(2):  # 598 s: past the idle time twice, under the 900 s maximum
            world.clock.advance(IDLE_S - 1)
            assert await world.store.touch(A, held.lease.lease_id) is not None
        assert (await world.store.current(A)).lease is not None
        assert await world.store.touch(B, held.lease.lease_id) is None  # not B's

    run(world, go)


def test_the_store_honours_the_wait_with_its_clock(world: World) -> None:
    async def go() -> None:
        store = world.store
        held = await store.acquire(A, "apply_fill", True)
        assert held.lease is not None
        lease_id = held.lease.lease_id
        for _ in range(10):
            world.clock.advance(60)
            await store.touch(A, lease_id)
        assert await store.wait_person(B, lease_id, "signin", "Example Portal") is None
        assert await store.wait_person(A, lease_id, "signin", "Example Portal") is not None
        world.clock.advance(25 * 60)
        view = await store.current(A)
        assert view.lease is not None
        assert view.lease.state == "waiting_person"
        await store.resume(A, lease_id)
        for _ in range(4):
            world.clock.advance(60)
            await store.touch(A, lease_id)
        view = await store.current(A)
        assert view.lease is not None
        assert view.lease.state == "active"
        assert view.lease.active_s(world.clock()) == 840

    run(world, go)


def test_browserd_ended_the_lease_itself(world: World) -> None:
    async def go() -> None:
        held = await world.store.acquire(A, "sweep", True)
        assert held.lease is not None
        await world.browserd.manager.release(held.lease.lease_id, "browser_crashed")
        assert (await world.store.current(A)).lease is None
        assert int((await (await world.redis()).lrange("jobapp:browser:durations", 0, -1))[0]) == 0

    run(world, go)


def test_redis_restarted_holders_come_back_from_browserd(world: World) -> None:
    async def go() -> None:
        held = await world.store.acquire(A, "apply_fill", True)
        assert held.lease is not None
        world.clock.advance(120)
        await world.flush()  # what a restart of Redis without persistence leaves
        view = await world.store.current(A)
        assert view.lease is not None
        assert view.lease.lease_id == held.lease.lease_id
        assert view.lease.purpose == "apply_fill"
        assert view.lease.active_s(world.clock()) == pytest.approx(120, abs=0.2)
        other = await world.store.current(B)
        assert other.held_by_other
        assert other.lease is None
        assert (await world.store.acquire(B, "takeover", False)).waiting is not None
        assert len(world.browserd.starts) == 1

    run(world, go)


def test_browserd_holds_a_lease_redis_does_not_know(world: World) -> None:
    async def go() -> None:
        held = await world.store.acquire(A, "sweep", True)
        assert held.lease is not None
        r = await world.redis()
        await r.delete(HOLDER)  # lost, while the marker says Redis already read browserd
        assert await r.exists(SYNCED)
        got = await world.store.acquire(B, "takeover", False)
        assert got.lease is None
        assert got.waiting is not None
        assert got.waiting.position == 1
        assert not await r.exists(SYNCED)
        view = await world.store.current(B)
        assert view.held_by_other  # browserd's lease is A's again
        assert (await world.store.current(A)).lease is not None

    run(world, go)


def test_b_never_sees_nor_ends_as_lease(world: World) -> None:
    async def go() -> None:
        held = await world.store.acquire(A, "apply_fill", True)
        assert held.lease is not None
        view = await world.store.current(B)
        assert view.lease is None
        assert view.held_by_other
        assert held.lease.lease_id not in repr(view)
        assert not await world.store.release(B, held.lease.lease_id)
        assert not await world.store.release(B)
        assert await world.store.wait_person(B, held.lease.lease_id, "signin", "x") is None
        assert (await world.store.current(A)).lease is not None
        assert not await world.store.release(A, "another-id")
        assert not await world.store.release(A, purposes=("takeover",))
        assert await world.store.release(A, held.lease.lease_id)

    run(world, go)


def test_two_leases_of_one_person_use_the_same_profile(world: World) -> None:
    async def go() -> None:
        first = await world.store.acquire(A, "takeover", False)
        assert first.lease is not None
        await world.store.release(A)
        second = await world.store.acquire(A, "apply_fill", True)
        assert second.lease is not None
        assert first.lease.profile_id == second.lease.profile_id
        one, two = world.browserd.starts
        assert (one.profile_id, one.uid) == (two.profile_id, two.uid)
        assert (one.purpose, two.purpose) == ("takeover", "apply_fill")
        assert (one.automation, two.automation) == (False, True)

    run(world, go)


def test_browserd_down_leaves_no_turn_behind(world: World) -> None:
    async def go() -> None:
        world.browserd.down = True
        with pytest.raises(BrowserError) as failed:
            await world.store.acquire(A, "takeover", False)
        assert failed.value.code == "unavailable"
        r = await world.redis()
        assert await r.get(HOLDER) is None
        world.browserd.down = False
        assert (await world.store.acquire(A, "takeover", False)).lease is not None

    run(world, go)


@pytest.mark.parametrize("secret", ["", "wrong-secret"])
def test_no_secret_or_a_wrong_one_starts_nothing(world: World, secret: str) -> None:
    async def go() -> None:
        store = world.make_store(client=world.browserd.client(secret))
        with pytest.raises(BrowserError) as failed:
            await store.acquire(A, "takeover", False)
        assert failed.value.code == "unavailable"
        assert world.browserd.starts == []
        assert await (await world.redis()).get(HOLDER) is None

    run(world, go)


def test_a_start_slower_than_the_grace_is_ended(world: World) -> None:
    async def go() -> None:
        slow_world = world
        original = slow_world.browserd.manager._start

        async def slow(request: Any, lease_id: str) -> Any:
            slow_world.clock.advance(START_GRACE_S)
            # Meanwhile another caller settles the holder and drops the stale turn.
            await slow_world.make_store().current(B)
            return await original(request, lease_id)

        slow_world.browserd.manager._start = slow
        with pytest.raises(BrowserError):
            await slow_world.store.acquire(A, "takeover", False)
        assert slow_world.browserd.manager.current() is None
        assert slow_world.browserd.chromiums[0].stopped

    run(world, go)


def test_redis_down_is_store_unavailable(world: World) -> None:
    async def broken() -> Any:
        raise ConnectionError("redis is down")

    async def go() -> None:
        store = world.make_store()
        store._pool = broken
        for call in (
            store.acquire(A, "takeover", False),
            store.current(A),
            store.release(A),
            store.cancel(A),
            store.touch(A, "x"),
        ):
            with pytest.raises(StoreUnavailableError):
                await call

    run(world, go)


def test_unknown_purpose_is_refused(world: World) -> None:
    async def go() -> None:
        with pytest.raises(ValueError, match="look"):
            await world.store.acquire(A, "look", False)

    run(world, go)


def test_estimated_wait_from_the_last_ten_leases(world: World) -> None:
    async def go() -> None:
        store = world.store
        for n in range(12):  # twelve leases of 100 then 200 seconds: the last ten count
            await store.acquire(A, "takeover", False)
            world.clock.advance(100 if n < 2 else 200)
            await store.release(A)
        await store.acquire(A, "takeover", False)
        world.clock.advance(50)
        await store.acquire(B, "takeover", False)
        await store.acquire(C, "takeover", False)
        b, c = (await store.current(B)).waiting[0], (await store.current(C)).waiting[0]
        assert (b.position, b.estimated_wait_s) == (1, 150)
        assert (c.position, c.estimated_wait_s) == (2, 350)

    run(world, go)


def test_the_token_lives_in_the_callers_memory_only(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    async def go() -> None:
        held = await world.store.acquire(A, "apply_fill", True)
        assert held.token is not None
        assert held.lease is not None
        await world.store.touch(A, held.lease.lease_id)
        await world.store.acquire(B, "takeover", False)
        dump = await world.dump()
        assert held.token not in dump
        assert held.token not in repr(held)
        assert held.token not in caplog.text
        await world.store.release(A)
        assert held.token not in await world.dump()
        assert held.token not in caplog.text

    run(world, go)


# --- the client against a hostile browserd --------------------------------------------------------


def answering(status: int, body: bytes = b"", seen: list[httpx.Request] | None = None) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, content=body)

    return BrowserdClient("http://browser:9300", "s3cret", transport=httpx.MockTransport(handle))


START = {"user_id": A, "profile_id": A, "uid": 20000, "purpose": "sweep", "automation": True}


@pytest.mark.parametrize(
    ("status", "body", "code", "holder"),
    [
        (409, b'{"error": "busy", "holder_purpose": "sweep"}', "busy", "sweep"),
        (409, b'{"error": "busy", "holder_purpose": 7}', "busy", None),
        (409, b"not json", "busy", None),
        (400, b'{"error": "bad_uid"}', "refused", None),
        (413, b"", "refused", None),
        (401, b'{"error": "unauthorized"}', "unavailable", None),
        (503, b'{"error": "browser_failed"}', "unavailable", None),
        (201, b'{"lease_id": "x"}', "unavailable", None),  # no token: not a lease
        (201, b'{"lease_id": "x", "lease_token": ""}', "unavailable", None),
        (200, b"[]", "unavailable", None),
    ],
)
def test_start_failures_are_typed(status: int, body: bytes, code: str, holder: str | None) -> None:
    with pytest.raises(BrowserError) as failed:
        asyncio.run(answering(status, body).start(**START))
    assert (failed.value.code, failed.value.holder_purpose) == (code, holder)
    assert str(failed.value) == code


def test_the_client_sends_the_secret_and_reads_end_and_current() -> None:
    seen: list[httpx.Request] = []
    assert asyncio.run(answering(204, seen=seen).end(A)) is True
    assert asyncio.run(answering(404, b'{"error": "no_such_lease"}').end(A)) is False
    assert seen[0].headers["authorization"] == "Bearer s3cret"
    assert seen[0].url.path == f"/leases/{A}"
    for status, body in ((500, b""), (200, b"{}"), (200, b"no")):
        with pytest.raises(BrowserError):
            asyncio.run(answering(status, body).current())
    with pytest.raises(BrowserError):
        asyncio.run(answering(500).end(A))
    odd = asyncio.run(answering(200, b'{"lease": 3, "last_end": "x"}').current())
    assert odd == {"lease": None, "last_end": None}
    with pytest.raises(ValueError, match="nope"):
        BrowserError("nope")
    started = asyncio.run(answering(201, b'{"lease_id": "l", "lease_token": "t0k"}').start(**START))
    assert "t0k" not in repr(started)
    assert started.lease == {"lease_id": "l"}


def test_a_lease_browserd_answers_badly_is_not_taken_back(world: World) -> None:
    async def go() -> None:
        bad = {"lease": {"lease_id": "x", "purpose": "nope", "user_id": B}, "last_end": None}

        def handle(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=bad)

        client = BrowserdClient("http://browser:9300", "s", transport=httpx.MockTransport(handle))
        store = world.make_store(client=client)
        assert (await store.current(A)).held_by_other is False
        bad["lease"] = {"lease_id": "x"}  # no purpose at all
        await (await world.redis()).delete(SYNCED)
        assert (await store.current(A)).held_by_other is False

    run(world, go)
