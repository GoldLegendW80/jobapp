"""B20, review round 2: while the person holds the input of a sweep lease (Take over, during a
browser_ask_person wait too), the request floor of its discovery does not apply, so the person
can sign in or solve a security check in the live view (spec item 3, "Who's there"); the moment
the input goes back to the agent (Give back, TAKEOVER_S, the holder's view gone past
HOLDER_GRACE_S, the lease's end), it applies again, to a request queued before that moment too.

The wiring is browserd's own: `routes._tab` gives the Tab `live.Live.person_holds`, and the
route (`Tab._line_route`) and the redirect listener (`Tab._hop`) read it for each request. Only
the Playwright boundary is fake (a route, a request), and the clock is the manager's."""

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd import live
from browserd.leases import Request
from browserd.tools import actions, routes

from tests.browser.test_b4_live_unit import SECRET, VIEWER, World

ATS = "https://www.linkedin.test"
# The person's own requests in the live view, each refused by the floor in the agent's turn.
PERSON = [
    pytest.param(f"{ATS}/checkpoint/lg/login-submit", "main", "fulfill", id="sign_in_submit"),
    pytest.param(f"{ATS}/checkpoint/challenge/verify", "main", "fulfill", id="check_verify"),
    pytest.param("https://secure.indeed.test/account/login", None, "abort", id="fetch_login"),
]


class Route:
    def __init__(self) -> None:
        self.done: list[str] = []

    async def fallback(self) -> None:
        self.done.append("fallback")

    async def abort(self, code: str = "") -> None:
        self.done.append("abort")

    async def fulfill(self, **_: object) -> None:
        self.done.append("fulfill")


def post(url: str, frame: str | None) -> Any:
    """A POST: `frame` "main" for a document of the tab, None for a fetch."""
    return SimpleNamespace(
        url=url,
        method="POST",
        resource_type="fetch" if frame is None else "document",
        is_navigation_request=lambda: frame is not None,
        frame=SimpleNamespace(parent_frame=None),
        redirected_from=None,
    )


def sweep() -> Request:
    return Request.parse(
        {"user_id": "e2e-b20", "profile_id": str(uuid.uuid4()), "uid": 20000,
         "purpose": "sweep", "automation": True}
    )  # fmt: skip


async def sweep_tab(w: World) -> tuple[str, actions.Tab]:
    """A sweep lease and its Tab as browserd's tool route makes it, its discovery begun."""
    made, _ = await w.manager.acquire(sweep())
    svc = SimpleNamespace(manager=w.manager, live=w.live, secret=SECRET)
    found = routes._tab(svc, made)
    found.state.discovery = True
    return made.lease_id, found


async def routed(found: actions.Tab, request: Any) -> list[str]:
    route = Route()
    await found._line_route(route, request)
    return route.done


def run(test) -> None:
    async def go() -> None:
        await test(World())

    asyncio.run(go())


@pytest.mark.parametrize(("url", "frame", "refused"), PERSON)
def test_the_person_holding_the_input_signs_in_and_the_floor_is_back_at_give_back(
    url: str, frame: str | None, refused: str
) -> None:
    async def go(w: World) -> None:
        lease_id, found = await sweep_tab(w)
        assert await routed(found, post(url, frame)) == [refused]  # the agent's turn
        found.state.blocked.clear()  # settle() reported it
        w.live.take(lease_id, VIEWER)
        assert w.live.person_holds(lease_id)
        assert await routed(found, post(url, frame)) == ["fallback"]
        assert found.state.blocked == []  # nothing stopped, nothing for settle() to report
        w.live.give_back(lease_id)
        assert not w.live.person_holds(lease_id)
        assert await routed(found, post(url, frame)) == [refused]

    run(go)


def test_the_floor_is_back_at_the_takeover_s_deadline_before_the_tick_runs() -> None:
    url, frame, refused = PERSON[0].values

    async def go(w: World) -> None:
        lease_id, found = await sweep_tab(w)
        w.live.take(lease_id, VIEWER)
        w.clock.now += live.TAKEOVER_S - 1
        assert await routed(found, post(url, frame)) == ["fallback"]
        w.clock.now += 1
        assert w.live.holder(lease_id) == VIEWER  # tick() has not given it back yet
        assert await routed(found, post(url, frame)) == [refused]
        w.live.tick()
        assert w.live.holder(lease_id) is None
        assert await routed(found, post(url, frame)) == [refused]

    run(go)


def test_the_floor_is_back_when_the_holder_s_view_is_gone_past_the_grace() -> None:
    url, frame, refused = PERSON[0].values

    async def go(w: World) -> None:
        lease_id, found = await sweep_tab(w)
        w.live.take(lease_id, VIEWER)
        w.live.tick()  # no view of the holder is open: its grace starts
        w.clock.now += live.HOLDER_GRACE_S - 1
        assert await routed(found, post(url, frame)) == ["fallback"]
        w.clock.now += 1
        assert await routed(found, post(url, frame)) == [refused]

    run(go)


def test_the_floor_is_back_at_the_lease_s_end_and_a_discovery_that_ended_stays_refused() -> None:
    url, frame, refused = PERSON[0].values

    async def go(w: World) -> None:
        lease_id, found = await sweep_tab(w)
        w.live.take(lease_id, VIEWER)
        found.state.native_failed = True  # the discovery ended: its route refuses everything
        assert await routed(found, post(url, frame)) == [refused]
        found.state.native_failed = False
        assert await routed(found, post(url, frame)) == ["fallback"]
        await w.manager.release(lease_id, "released")
        assert await routed(found, post(url, frame)) == [refused]

    run(go)


def test_a_request_queued_while_held_is_judged_when_it_runs_after_give_back() -> None:
    url, frame, refused = PERSON[0].values

    async def go(w: World) -> None:
        lease_id, found = await sweep_tab(w)
        w.live.take(lease_id, VIEWER)
        route = Route()
        queued = found._line_route(route, post(url, frame))  # not run yet
        w.live.give_back(lease_id)
        await queued
        assert route.done == [refused]

    run(go)


def test_the_person_holds_during_an_ask_taken_over_and_not_before() -> None:
    url, frame, refused = PERSON[1].values

    async def go(w: World) -> None:
        lease_id, found = await sweep_tab(w)
        lease = w.manager.current()
        lease.wait_person(w.clock(), "ask_person", "www.linkedin.test", live.TAKEOVER_S)
        # Asked, nobody took over yet: the view is view only, the floor applies.
        assert await routed(found, post(url, frame)) == [refused]
        w.live.take(lease_id, VIEWER)
        assert await routed(found, post(url, frame)) == ["fallback"]
        w.live.give_back(lease_id)
        assert lease.state == "active"
        assert await routed(found, post(url, frame)) == [refused]

    run(go)


def gone() -> bool:
    raise RuntimeError("target closed")


@pytest.mark.parametrize(
    "holds", [None, lambda: 1, lambda: "yes", gone], ids=["none", "one", "text", "raises"]
)
def test_a_tab_that_cannot_tell_the_person_holds_keeps_the_floor(holds) -> None:
    url, frame, refused = PERSON[0].values
    found = actions.Tab(SimpleNamespace(hosts=()), actions.State(), None, None, lease_tag="e2e")
    found.person_holds = holds
    route = Route()
    asyncio.run(found._line_route(route, post(url, frame)))
    assert route.done == [refused]


def test_a_sign_out_redirect_hop_is_stopped_but_while_the_person_holds_the_input(
    monkeypatch,
) -> None:
    """Tab._hop, the redirect listener: a hop to a sign out address stops the load in the
    agent's turn, and goes on while the person holds the input (their own way out)."""
    hop = SimpleNamespace(
        url=f"{ATS}/m/logout/",
        redirected_from=object(),
        is_navigation_request=lambda: True,
        frame=SimpleNamespace(parent_frame=None),
    )

    async def go(w: World) -> None:
        lease_id, found = await sweep_tab(w)
        stopped: list[Any] = []

        async def stop(frame: Any) -> None:
            stopped.append(frame)

        monkeypatch.setattr(actions, "stop_loading", stop)
        w.live.take(lease_id, VIEWER)
        found._hop(hop)
        assert found.state.blocked == []
        w.live.give_back(lease_id)
        found._hop(hop)
        await asyncio.sleep(0)  # the stop is fire and forget: its task runs once
        assert found.state.blocked == [hop.url]
        assert stopped == [hop.frame]

    run(go)
