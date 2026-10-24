"""Apply person waits keep browser-owned time outside the active lease budget."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd import app, handoff
from browserd.leases import LeaseError

from tests.browser.test_browserd_unit import Clock, manager, request, run


@pytest.mark.parametrize("kind", ["workday", "linkedin"])
@pytest.mark.parametrize("reason", ["captcha", "email_code"])
def test_later_wizard_captcha_keeps_prior_answers_and_ordered_steps(kind, reason) -> None:
    from app.apply.ats import generic
    from app.apply.ats.linkedin import LinkedIn
    from app.apply.ats.workday import Workday
    from tests.apply.a15_support import BASE, WizardForm, url
    from tests.apply.a16_support import URL, LinkedInForm
    from tests.apply.test_a15_wizards import context, rows

    async def go() -> None:
        form, adapter, address = (
            (WizardForm(), Workday(BASE), url())
            if kind == "workday"
            else (LinkedInForm(), LinkedIn(), URL)
        )
        click = form.click

        async def challenge(ref, element=""):
            result = await click(ref, element)
            if form.step == 1:
                form.extras = [
                    ("checkbox", "I'm not a robot")
                    if reason == "captcha"
                    else ("textbox", "Verification code")
                ]
            return result

        form.click = challenge
        await form.put_file("cv.pdf", b"%PDF-synthetic")
        assert await adapter.open(form, address) == ""
        first = await adapter.fill_form(form, context())
        assert first.needs_person == reason
        assert first.steps == ["Next"]
        original = [o.field.key for o in first.outcomes]
        form.extras, form.click = [], click
        final = await adapter.fill_form(form, context())
        assert not final.stopped
        assert final.steps == (["Next", "Next"] if kind == "workday" else ["Next", "Review"])
        assert [o.field.key for o in final.outcomes][: len(original)] == original
        approved = rows(final)
        replay = generic.configured(adapter, approved)
        assert len(generic.stages(replay, approved)) == 3
        assert not form.submit_clicks

    run(go())


def waiting_route(svc, lease_id, token, action, body=None):
    route = next(r for r in app.ROUTES if "apply-wait" in r.pattern.pattern)
    path = f"/leases/{lease_id}/apply-wait/{action}"
    req = app.HttpRequest("POST", path, {"x-lease-token": token}, json.dumps(body or {}).encode())
    return route.handler(svc, req, route.pattern.fullmatch(path))


@pytest.mark.parametrize("reason", ["captcha", "email_code", "forbidden"])
def test_private_wait_has_fixed_time_person_input_passive_reads_and_active_remainder(reason):
    clock = Clock()
    mgr, _ = manager(clock, idle_s=300, session_max_s=60)
    svc = app.Service(mgr, b"fake-control")

    async def go():
        lease, token = await mgr.acquire(request())
        clock.now += 240
        code, answer = await waiting_route(svc, lease.lease_id, token, "begin", {"reason": reason})
        assert code == 200
        assert answer["lease"]["wait_left_s"] == 1800
        assert lease.person_input
        assert lease.max_s == 900
        deadline, last_input = lease.wait_deadline, lease.last_input
        clock.now += 1500
        assert await mgr.tick() is None
        await waiting_route(svc, lease.lease_id, token, "begin", {"reason": reason})
        assert lease.wait_deadline == deadline
        assert (await waiting_route(svc, lease.lease_id, token, "read"))[1]["lease"][
            "wait_left_s"
        ] == 300

        async def read():
            return "safe snapshot"

        with pytest.raises(LeaseError, match="waiting_person"):
            await mgr.call(lease.lease_id, token, read)
        assert (
            await mgr.call(lease.lease_id, token, read, touch=False, passive_wait=True)
            == "safe snapshot"
        )
        assert lease.last_input == last_input
        # A stale sign-in handoff must not accept this wait's Done or Cancel.
        lease.scratch[handoff.KEY] = SimpleNamespace(done=True)
        for action in ("done", "cancel"):
            route = next(r for r in app.ROUTES if r.pattern.pattern.endswith(f"handoff/{action}"))
            with pytest.raises(LeaseError, match="not_waiting"):
                await route.handler(
                    svc, None, route.pattern.fullmatch(f"/leases/{lease.lease_id}/handoff/{action}")
                )
        await waiting_route(svc, lease.lease_id, token, "resume")
        assert lease.state == "active"
        assert not lease.person_input
        assert lease.active_s(clock()) == 240
        for _ in range(10):
            clock.now += 60
            lease.touch(clock())
            assert await mgr.tick() is None
        clock.now += 60
        assert await mgr.tick() == "lease_max"

    run(go())


@pytest.mark.parametrize("reason", ["captcha", "email_code", "forbidden"])
def test_api_mirror_and_redis_restart_keep_browser_deadline_and_active_budget(reason):
    from app.browser.leases import HOLDER, Leases

    clock = Clock()
    mgr, _ = manager(clock, idle_s=300, session_max_s=60)
    svc = app.Service(mgr, b"fake-control")

    class Redis:
        holder = None

        async def set(self, key, value, **kwargs):
            if key == HOLDER:
                self.holder = value
            return True

        async def get(self, key):
            return self.holder

        async def sismember(self, *args):
            return False

    class Client:
        async def current(self):
            return {"lease": mgr.current().public(clock())}

    store = Leases.__new__(Leases)
    store.client, store.session_max_s, store.idle_s = Client(), 60, 300
    store.max_s = {"apply_fill": 900}
    redis = Redis()

    async def go():
        lease, token = await mgr.acquire(request())
        clock.now += 240
        await waiting_route(svc, lease.lease_id, token, "begin", {"reason": reason})
        await store._sync(redis, clock())
        from app.browser.leases import Lease

        api_lease = Lease.loads(redis.holder)
        deadline = api_lease.wait_deadline
        assert deadline == lease.wait_deadline
        assert api_lease.active_s(clock()) == 240

        async def update(user, lease_id, change):
            change(api_lease, clock())
            return api_lease

        store._update = update
        clock.now += 1500
        assert api_lease.expired(clock()) is None
        await store.apply_wait("a", lease.lease_id, lease.public(clock()))
        assert api_lease.wait_deadline == deadline
        redis.holder = None
        await store._sync(redis, clock())
        restored = Lease.loads(redis.holder)
        assert restored.wait_deadline == deadline
        assert restored.active_s(clock()) == 240
        assert restored.wait_reason == reason
        # Browser-owned expiry remains authoritative after losing the mirror repeatedly.
        clock.now += 299
        redis.holder = None
        await store._sync(redis, clock())
        assert Lease.loads(redis.holder).wait_deadline == deadline
        await waiting_route(svc, lease.lease_id, token, "resume")
        await store.apply_wait("a", lease.lease_id, lease.public(clock()))
        assert api_lease.state == "active"
        assert api_lease.active_s(clock()) == 240
        assert api_lease.expired(clock() + 299) is None
        assert api_lease.expired(clock() + 300) == "idle"

    run(go())


@pytest.mark.parametrize(
    "body",
    [
        {"reason": "signin"},
        {"reason": "captcha", "deadline_s": 9999},
        {"reason": []},
        {},
        {"reason": "other"},
    ],
)
def test_wait_rejects_unowned_duration_or_reason(body):
    clock = Clock()
    mgr, _ = manager(clock)

    async def go():
        lease, token = await mgr.acquire(request())
        with pytest.raises(LeaseError, match="bad_args"):
            await waiting_route(
                app.Service(mgr, b"fake-control"), lease.lease_id, token, "begin", body
            )
        assert lease.state == "active"

    run(go())


def test_wait_checks_exact_token_purpose_and_timeout_without_extending_on_resume():
    clock = Clock()
    mgr, _ = manager(clock)
    svc = app.Service(mgr, b"fake-control")

    async def go():
        wrong, wrong_token = await mgr.acquire(request("full_auto"))
        with pytest.raises(LeaseError, match="wrong_purpose"):
            await waiting_route(svc, wrong.lease_id, wrong_token, "begin", {"reason": "captcha"})
        await mgr.release(wrong.lease_id, "test")
        lease, token = await mgr.acquire(request())
        with pytest.raises(LeaseError, match="bad_lease_token"):
            await waiting_route(svc, lease.lease_id, "wrong", "begin", {"reason": "captcha"})
        await waiting_route(svc, lease.lease_id, token, "begin", {"reason": "captcha"})
        deadline = lease.wait_deadline
        clock.now += 100
        await waiting_route(svc, lease.lease_id, token, "resume")
        clock.now += 100
        await waiting_route(svc, lease.lease_id, token, "begin", {"reason": "email_code"})
        assert lease.wait_deadline == deadline
        clock.now = deadline
        with pytest.raises(LeaseError, match="lease_ended"):
            await waiting_route(svc, lease.lease_id, token, "read")
        assert mgr.current() is None
        assert mgr.last_end["reason"] == "person_timeout"
        with pytest.raises(LeaseError, match="no_such_lease"):
            await waiting_route(svc, lease.lease_id, token, "resume")

    run(go())


def test_apply_wait_polling_cannot_extend_the_original_deadline() -> None:
    clock = Clock()
    mgr, _ = manager(clock, idle_s=300)

    async def go() -> None:
        lease, _ = await mgr.acquire(request())
        clock.now += 240
        lease.wait_person(clock(), "captcha", "", 1800)
        deadline = lease.wait_deadline
        clock.now += 1500
        lease.wait_person(clock(), "captcha", "", 1800)
        assert lease.wait_deadline == deadline
        assert lease.active_s(clock()) == 240
        assert await mgr.tick() is None
        clock.now += 300
        assert await mgr.tick() == "person_timeout"

    run(go())
