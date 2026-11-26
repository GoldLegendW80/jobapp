"""browserd's side of the apply agent's wait for the person (apply A25): `POST /leases/{id}/ask/
(begin|resume)`, code owned, for the apply agent's leases only; the lease waits `ask_person` with
both timers suspended; a Take over turns it into a takeover and only Give back (or the api's
resume) makes it active again; Live.tick never resumes it by itself, and the lease ends
ASK_GRACE_S past its deadline (a worker that died). A website search's ask is unchanged."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd import app, live
from browserd.leases import ASK_GRACE_S, LeaseError

from tests.browser.test_browserd_unit import Clock, manager, request, run

SECRET = b"fake-control"


def ask_route(svc, lease_id, token, action, body):
    route = next(r for r in app.ROUTES if "/ask/" in r.pattern.pattern)
    path = f"/leases/{lease_id}/ask/{action}"
    req = app.HttpRequest("POST", path, {"x-lease-token": token}, json.dumps(body).encode())
    return route.handler(svc, req, route.pattern.fullmatch(path))


def world(purpose: str = "apply_agent"):
    clock = Clock()
    mgr, _ = manager(clock, idle_s=300)
    svc = app.Service(mgr, SECRET)
    return clock, mgr, svc, purpose


@pytest.mark.parametrize("purpose", ["apply_agent", "apply_review"])
def test_the_ask_waits_without_the_idle_timer_and_resumes_on_the_api_s_word(purpose) -> None:
    clock, mgr, svc, _ = world()

    async def go():
        lease, token = await mgr.acquire(request(purpose))
        code, answer = await ask_route(svc, lease.lease_id, token, "begin", {"left_s": 900})
        assert code == 200
        said = answer["lease"]
        assert (said["state"], said["wait_reason"], said["wait_left_s"]) == (
            "waiting_person",
            "ask_person",
            900,
        )
        clock.now += 899  # far past the idle timer (300 s): the wait holds it
        assert lease.expired(clock.now) is None
        assert mgr.get(lease.lease_id) is lease
        code, answer = await ask_route(svc, lease.lease_id, token, "resume", {})
        assert (code, answer["lease"]["state"]) == (200, "active")
        assert lease.expired(clock.now + 299) is None  # its idle timer starts again now

    run(go())


@pytest.mark.parametrize(
    "body",
    [{}, {"left_s": 0}, {"left_s": 1801}, {"left_s": True}, {"left_s": "60"},
     {"left_s": 60, "reason": "x"}],
)  # fmt: skip
def test_a_begin_takes_its_seconds_only(body) -> None:
    _, mgr, svc, _ = world()

    async def go():
        lease, token = await mgr.acquire(request("apply_agent"))
        with pytest.raises(LeaseError) as refused:
            await ask_route(svc, lease.lease_id, token, "begin", body)
        assert (refused.value.status, refused.value.code) == (400, "bad_args")
        assert lease.state == "active"

    run(go())


@pytest.mark.parametrize("purpose", ["apply_fill", "sweep", "apply_submit"])
def test_only_the_apply_agent_s_leases_wait_so(purpose) -> None:
    _, mgr, svc, _ = world()

    async def go():
        lease, token = await mgr.acquire(request(purpose))
        with pytest.raises(LeaseError) as refused:
            await ask_route(svc, lease.lease_id, token, "begin", {"left_s": 60})
        assert (refused.value.status, refused.value.code) == (403, "wrong_purpose")
        with pytest.raises(LeaseError) as wrong:
            await ask_route(svc, lease.lease_id, "not-the-token", "begin", {"left_s": 60})
        assert wrong.value.status in (403, 404)

    run(go())


def test_a_take_over_during_the_ask_is_a_takeover_and_only_give_back_resumes_it() -> None:
    clock, mgr, svc, _ = world()
    view = live.Live(mgr, SECRET)

    async def go():
        lease, token = await mgr.acquire(request("apply_agent"))
        await ask_route(svc, lease.lease_id, token, "begin", {"left_s": 60})
        view.take(lease.lease_id, "viewer-1")
        assert (lease.state, lease.wait_reason) == ("waiting_person", "takeover")
        # The api's resume after an answer typed in the app leaves the person's take over.
        await ask_route(svc, lease.lease_id, token, "resume", {})
        assert lease.wait_reason == "takeover"
        clock.now += 61 + ASK_GRACE_S  # past the ask's deadline and its grace: still theirs
        assert lease.expired(clock.now) is None
        view.give_back(lease.lease_id, "viewer-1")
        assert lease.state == "active"

    run(go())


def test_nobody_taking_over_never_resumes_it_and_it_ends_past_its_grace() -> None:
    clock, mgr, svc, _ = world()
    view = live.Live(mgr, SECRET)

    async def go():
        lease, token = await mgr.acquire(request("apply_agent"))
        await ask_route(svc, lease.lease_id, token, "begin", {"left_s": 60})
        clock.now += 61
        view.tick()
        assert (lease.state, lease.wait_reason) == ("waiting_person", "ask_person")
        assert lease.expired(clock.now) is None
        clock.now += ASK_GRACE_S
        assert lease.expired(clock.now) == "person_timeout"

    run(go())


def test_a_website_search_s_ask_is_unchanged() -> None:
    clock, mgr, _, _ = world()
    view = live.Live(mgr, SECRET)

    async def go():
        lease, _ = await mgr.acquire(request("sweep"))
        lease.wait_person(clock.now, "ask_person", "", 60)
        clock.now += 61 + ASK_GRACE_S
        assert lease.expired(clock.now) is None
        view.tick()
        assert lease.state == "active"  # B20: an ask nobody took over resumes at its deadline

    run(go())
