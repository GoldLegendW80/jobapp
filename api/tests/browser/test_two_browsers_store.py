"""Two browsers at once (owner, 2026-11-06): the api's lease store (app/browser/leases.py) on a
real Redis and browserd's own lease logic (b3_support.FakeBrowserd) with BROWSER_SLOTS 2. One
person's agent leases take a place each, up to browserd's slots, each its own lease of browserd;
nobody else and no person's own session joins; each place settles, ends and comes back after a
Redis restart on its own; the queue's order holds for a free slot too.
"""

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest

from app.browser.client import BrowserError
from app.browser.leases import HOLDERS, SLOT_PURPOSES, Lease
from tests.browser.b3_support import World, flush, redis_url

__all__ = ["redis_url"]

A, B = (f"0000000{n}-0000-4000-8000-0000000000b0" for n in (1, 2))
S1, S2, S3 = ("3f1c0a2b9e8d7c6b5a4f3e2d", "9a8b7c6d5e4f3a2b1c0d9e8f", "1b2c3d4e5f6a7b8c9d0e1f2a")
X = "00000000-0000-4000-9000-0000000000c1"
PROFILE = "6f1c2a52-6c55-4a0a-9a3f-0d6c1f6d2b11"


@pytest.fixture
def world(redis_url: str) -> Iterator[World]:
    flush(redis_url)
    made = World(redis_url)
    manager = made.browserd.manager
    manager.config = dataclasses.replace(manager.config, slots=2)
    return made


def run(world: World, scenario: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        try:
            return await scenario()
        finally:
            await world.close()

    return asyncio.run(go())


async def places(world: World) -> list[Lease | None]:
    r = await world.redis()
    raws = [await r.get(key) for key in HOLDERS]
    return [Lease.loads(raw) if raw is not None else None for raw in raws]


def test_the_slot_purposes_and_the_most_slots_are_browserd_s() -> None:
    from browserd.config import SLOTS_MAX as MOST
    from browserd.leases import SLOT_PURPOSES as BROWSERD

    from app.config import Settings

    assert SLOT_PURPOSES == BROWSERD
    assert len(HOLDERS) == MOST == Settings.model_fields["browser_slots"].metadata[1].le


def test_a_second_agent_lease_of_the_person_takes_the_second_slot(world: World) -> None:
    async def go() -> None:
        sweep = await world.store.acquire(A, "sweep", True, source_id=S1)
        fill = await world.store.acquire(A, "apply_fill", True, application_id=X)
        assert sweep.lease is not None
        assert fill.lease is not None
        assert (sweep.token is not None, fill.token is not None) == (True, True)
        assert (sweep.lease.slot, fill.lease.slot) == (0, 1)
        held = world.browserd.manager.held()
        assert [lease.lease_id for lease in held] == [sweep.lease.lease_id, fill.lease.lease_id]
        assert len(world.browserd.starts) == 1  # one Chromium, the second joined it
        view = await world.store.current(A)
        assert [lease.lease_id for lease in view.leases] == [
            sweep.lease.lease_id,
            fill.lease.lease_id,
        ]
        assert view.lease is not None
        assert view.lease.lease_id == sweep.lease.lease_id
        assert view.find(fill.lease.lease_id) == fill.lease
        assert [p.lease_id if p else None for p in await places(world)] == [
            sweep.lease.lease_id,
            fill.lease.lease_id,
        ]
        # Each call finds its own lease again, never the other's (no token: not the starter).
        again = await world.store.acquire(A, "apply_fill", True, application_id=X)
        assert again.lease is not None
        assert (again.lease.lease_id, again.token) == (fill.lease.lease_id, None)
        assert await world.store.holds(A, fill.lease.lease_id)

    run(world, go)


def test_two_websites_take_two_slots_and_a_third_waits_for_a_free_one(world: World) -> None:
    async def go() -> None:
        first = await world.store.acquire(A, "sweep", True, source_id=S1)
        second = await world.store.acquire(A, "sweep", True, source_id=S2)
        third = await world.store.acquire(A, "sweep", True, source_id=S3)
        assert first.lease is not None
        assert second.lease is not None
        assert third.waiting is not None
        assert third.waiting.position == 1
        assert await world.store.release(A, first.lease.lease_id, reason="sweep_done")
        # browserd closed the first window only: the second goes on.
        assert [lease.lease_id for lease in world.browserd.manager.held()] == [
            second.lease.lease_id
        ]
        now = await world.store.acquire(A, "sweep", True, source_id=S3)
        assert now.lease is not None
        assert now.token is not None
        assert now.lease.slot == 0  # browserd's free slot, whichever place the store used
        assert len(world.browserd.starts) == 1

    run(world, go)


def test_nobody_else_and_no_session_of_the_person_joins(world: World) -> None:
    async def go() -> None:
        sweep = await world.store.acquire(A, "sweep", True, source_id=S1)
        assert sweep.lease is not None
        assert (await world.store.acquire(B, "apply_fill", True, application_id=X)).waiting
        assert (await world.store.acquire(A, "apply_manual", True)).waiting
        assert len(world.browserd.manager.held()) == 1
        view = await world.store.current(B)
        assert (view.lease, view.leases, view.held_by_other) == (None, (), True)

    run(world, go)


def test_a_free_slot_goes_to_the_head_of_the_queue_only(world: World) -> None:
    async def go() -> None:
        assert (await world.store.acquire(A, "sweep", True, source_id=S1)).lease is not None
        assert (await world.store.acquire(B, "apply_fill", True, application_id=X)).waiting
        # B waits ahead of A's second website: the free slot is not A's to take.
        assert (await world.store.acquire(A, "sweep", True, source_id=S2)).waiting
        assert len(world.browserd.manager.held()) == 1

    run(world, go)


def test_a_person_session_ends_every_sweep_and_waits_for_the_other_slot(world: World) -> None:
    async def go() -> None:
        sweep = await world.store.acquire(A, "sweep", True, source_id=S1)
        other = await world.store.acquire(A, "sweep", True, source_id=S2)
        assert sweep.lease is not None
        assert other.lease is not None
        own = await world.store.acquire(A, "takeover", False)
        assert own.lease is not None
        assert own.lease.purpose == "takeover"
        assert world.browserd.manager.held()[0].request.purpose == "takeover"
        assert len(world.browserd.manager.held()) == 1
        assert not await world.store.holds(A, sweep.lease.lease_id)
        assert not await world.store.holds(A, other.lease.lease_id)
        # Nothing joins a person's own session.
        assert (await world.store.acquire(A, "sweep", True, source_id=S3)).waiting

    run(world, go)


def test_with_one_slot_in_browserd_nobody_joins(world: World) -> None:
    manager = world.browserd.manager
    manager.config = dataclasses.replace(manager.config, slots=1)

    async def go() -> None:
        assert (await world.store.acquire(A, "sweep", True, source_id=S1)).lease is not None
        assert (await world.store.acquire(A, "apply_fill", True, application_id=X)).waiting
        assert len(manager.held()) == 1
        assert (await places(world))[1] is None

    run(world, go)


def test_a_slot_browserd_ended_leaves_the_other_place_alone(world: World) -> None:
    async def go() -> None:
        sweep = await world.store.acquire(A, "sweep", True, source_id=S1)
        fill = await world.store.acquire(A, "apply_fill", True, application_id=X)
        assert sweep.lease is not None
        assert fill.lease is not None
        assert await world.browserd.manager.release(fill.lease.lease_id, "idle")
        view = await world.store.current(A)
        assert [lease.lease_id for lease in view.leases] == [sweep.lease.lease_id]
        assert (await places(world))[1] is None
        assert await world.store.holds(A, sweep.lease.lease_id)

    run(world, go)


def test_after_redis_lost_its_keys_every_slot_comes_back(world: World) -> None:
    async def go() -> None:
        sweep = await world.store.acquire(A, "sweep", True, source_id=S1)
        fill = await world.store.acquire(A, "apply_fill", True, application_id=X)
        assert sweep.lease is not None
        assert fill.lease is not None
        await world.flush()
        view = await world.store.current(A)
        assert [(lease.lease_id, lease.slot) for lease in view.leases] == [
            (sweep.lease.lease_id, 0),
            (fill.lease.lease_id, 1),
        ]
        assert all(place is not None for place in await places(world))
        # A wait of the person in one slot is mirrored on that slot's lease only.
        changed = await world.store.wait_person(A, fill.lease.lease_id, "ask_person", "", 60)
        assert changed is not None
        states = [lease.state for lease in (await world.store.current(A)).leases]
        assert states == ["active", "waiting_person"]

    run(world, go)


def test_two_calls_at_once_for_one_website_answer_one_lease(world: World) -> None:
    async def go() -> None:
        got = await asyncio.gather(
            world.store.acquire(A, "sweep", True, source_id=S1),
            world.store.acquire(A, "sweep", True, source_id=S1),
        )
        winner = next(g for g in got if g.token is not None)
        loser = next(g for g in got if g is not winner)
        assert winner.lease is not None
        assert loser.lease is not None
        assert loser.token is None
        # The same turn: browserd's id, or the turn still `starting` (as test_b3_leases has it).
        assert loser.lease.lease_id == winner.lease.lease_id or loser.lease.state == "starting"
        assert loser.lease.key == S1
        assert len(world.browserd.manager.held()) == 1

    run(world, go)


def test_cancel_with_a_key_leaves_the_person_s_other_places(world: World) -> None:
    async def go() -> None:
        assert (await world.store.acquire(B, "takeover", False)).lease is not None
        assert (await world.store.acquire(A, "sweep", True, source_id=S1)).waiting
        assert (await world.store.acquire(A, "sweep", True, source_id=S2)).waiting
        assert await world.store.cancel(A, ["sweep"], key=S1) == 1
        assert [w.position for w in (await world.store.current(A)).waiting] == [1]
        assert await world.store.cancel(A, ["sweep"]) == 1
        assert (await world.store.current(A)).waiting == ()

    run(world, go)


def test_a_person_set_to_one_browser_at_once_never_gets_a_second_slot(world: World) -> None:
    """The admin's browsers at once (owner, 2026-11-07): the store never claims a second place
    for the person, and tells browserd, which never grants it either."""

    async def one(_: str) -> int:
        return 1

    world.profiles.browsers = one  # type: ignore[attr-defined]

    async def go() -> None:
        sweep = await world.store.acquire(A, "sweep", True, source_id=S1)
        assert sweep.lease is not None
        assert (await world.store.acquire(A, "apply_fill", True, application_id=X)).waiting
        assert world.browserd.starts[0].browsers == 1
        profile = await world.profiles.ensure(A)
        with pytest.raises(BrowserError) as refused:  # browserd's own rule, asked directly
            await world.browserd.client().start(
                user_id=A, profile_id=profile.profile_id, uid=profile.uid,
                purpose="apply_fill", automation=True, browsers=2,
            )  # fmt: skip
        assert refused.value.code == "busy"
        assert len(world.browserd.manager.held()) == 1

    run(world, go)


def test_a_setting_that_cannot_be_read_fails_closed_to_one(world: World) -> None:
    async def broken(_: str) -> int:
        raise RuntimeError("the database is away")

    world.profiles.browsers = broken  # type: ignore[attr-defined]

    async def go() -> None:
        assert (await world.store.acquire(A, "sweep", True, source_id=S1)).lease is not None
        assert (await world.store.acquire(A, "sweep", True, source_id=S2)).waiting

    run(world, go)


def test_browserd_refuses_a_browsers_at_once_out_of_its_bounds() -> None:
    from browserd.leases import LeaseError, Request

    body = {"user_id": "u1", "profile_id": PROFILE, "uid": 20001, "purpose": "sweep"}
    assert Request.parse(body).browsers == 2  # unset: the server's ceiling
    assert Request.parse({**body, "browsers": 1}).browsers == 1
    for bad in (0, 3, "1", True, 1.0):
        with pytest.raises(LeaseError) as refused:
            Request.parse({**body, "browsers": bad})
        assert refused.value.code == "bad_browsers"
