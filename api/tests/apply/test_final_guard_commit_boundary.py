"""A consumed approval survives pacing, and its final check follows the last commit."""

import asyncio
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import submit
from app.apply.models import Application, ApplicationEvent, Authorisation, Mode, State
from app.mail import gate as mail_gate
from tests.apply.a6_support import submit_ashby
from tests.apply.conftest import AT
from tests.apply.test_a6_gate import approve, frozen, ready, run_submit, submit_services
from tests.apply.test_a13_full_auto import held, turn_on
from tests.apply.test_a14_email import connect, no_google, provider
from tests.apply.test_a14_pacing import ON, approved
from tests.mail.fake_gmail import FakeGoogle

__all__ = ["no_google"]

AGES = [
    timedelta(hours=1, seconds=-1),
    timedelta(hours=1),
    timedelta(days=1),
    timedelta(days=7, seconds=-1),
    timedelta(days=7),
    timedelta(days=7, seconds=1),
]


def set_deadline(db, app, paced):
    authority = submit._spent(db, app)
    authority.created_at = AT
    authority.expires_at = (
        AT + timedelta(days=7) if app.mode is Mode.full_auto else AT + timedelta(hours=1)
    )
    app.needs = "host_pace" if paced else None
    original = authority.created_at, authority.expires_at, authority.used_at
    db.commit()
    return authority.id, original


def has_time(mode, age, paced):
    return age < (timedelta(days=7) if mode is Mode.full_auto or paced else timedelta(hours=1))


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize("paced", [False, True])
@pytest.mark.parametrize("age", AGES)
def test_browser_consumed_authority_pacing_survives_arm_and_final_commit(
    db, factory, person_a, forms_base_url, mode, paced, age
):
    if mode is Mode.full_auto:
        turn_on(db, person_a)
        app_id = held(db, factory, person_a, forms_base_url)
    else:
        app_id = ready(db, factory, person_a, forms_base_url)
        assert approve(factory, person_a, app_id).passed
    db.expire_all()
    app = db.get(Application, app_id)
    auth_id, original = set_deadline(db, app, paced)
    form = submit_ashby()
    env = submit_services(factory, form, forms_base_url)
    env.clock = lambda: AT + age
    result = run_submit(env, app_id)
    allowed = has_time(mode, age, paced)
    assert result == ("submitted" if allowed else "authorisation_expired")
    assert len(form.gate_clicks) == int(allowed)
    db.expire_all()
    authority = db.get(Authorisation, auth_id)
    assert (authority.created_at, authority.expires_at, authority.used_at) == original
    app = db.get(Application, app_id)
    assert app.needs is None
    assert app.state is (State.submitted if allowed else State.ready)
    if not allowed:
        assert frozen(db, person_a.cv_version_id) == [None]


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize("paced", [False, True])
@pytest.mark.parametrize("age", AGES)
def test_mail_consumed_authority_has_the_same_pacing_boundary(
    db, factory, person_a, mode, paced, age
):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake)
    if mode is Mode.full_auto:
        turn_on(db, person_a)
    app, auth = approved(db, person_a, mode, "e2e-jobs@example.org")
    app_id = app.id
    auth_id, original = set_deadline(db, app, paced)
    settings, _, _ = provider(fake, ring)
    calls = []

    class Provider:
        async def call(self, *args):
            calls.append(args)
            return {"result": "sent", "id": "synthetic-paced-send"}

        async def get_message(self, *args, **kwargs):
            return {}

    result = asyncio.run(
        mail_gate.send(
            factory,
            auth,
            AT + age,
            settings=settings,
            mcp=Provider(),
            switches=ON,
            clock=lambda: AT + age,
        )
    )
    allowed = has_time(mode, age, paced)
    assert (result.result, result.refusal_code) == (
        ("sent", None) if allowed else ("refused", "authorisation_expired")
    )
    assert len(calls) == int(allowed)
    db.expire_all()
    authority = db.get(Authorisation, auth_id)
    assert (authority.created_at, authority.expires_at, authority.used_at) == original
    assert db.get(Application, app_id).state is (State.submitted if allowed else State.ready)


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize("paced", [False, True])
@pytest.mark.parametrize(
    "fault", ["valid", "expiry", "paused", "pause_error", "pause_delay", "disabled"]
)
def test_browser_pause_and_deadline_are_fresh_after_the_last_guard_commit(
    db, factory, person_a, forms_base_url, monkeypatch, mode, fault, paced
):
    if mode is Mode.full_auto:
        turn_on(db, person_a)
        app_id = held(db, factory, person_a, forms_base_url)
    else:
        app_id = ready(db, factory, person_a, forms_base_url)
        assert approve(factory, person_a, app_id).passed
    db.expire_all()
    app = db.get(Application, app_id)
    auth = submit._spent(db, app)
    auth_id, used = auth.id, auth.used_at
    app.needs = "host_pace" if paced else None
    deadline = (
        auth.created_at + timedelta(days=7) if paced and mode is Mode.review else auth.expires_at
    )
    db.commit()
    current, final_committed, pause, commits, final_pause_commits = (
        [deadline - timedelta(seconds=1)],
        [False],
        [False],
        [0],
        [None],
    )
    form = submit_ashby()
    env = submit_services(factory, form, forms_base_url)
    env.clock = lambda: current[0]
    in_db, click, commit = submit._in_db, env.click, Session.commit
    guarding = [False]

    def final_guard(sessions, step):
        guarding[0] = step.__name__ == "before_click"
        try:
            return in_db(sessions, step)
        finally:
            guarding[0] = False

    def after_commit(session):
        answer = commit(session)
        commits[0] += 1
        if guarding[0]:
            final_committed[0] = True
            if fault == "expiry":
                current[0] = deadline
            elif fault in {"paused", "pause_error"}:
                pause[0] = fault
            elif fault == "disabled":
                env.enabled = False
        return answer

    async def paused():
        if final_committed[0]:
            final_pause_commits[0] = commits[0]
            if fault == "pause_delay":
                current[0] = deadline
        if pause[0] == "pause_error":
            raise ConnectionError("synthetic unavailable pause store")
        return bool(pause[0])

    async def external_click(*args):
        assert final_pause_commits[0] == commits[0]
        return await click(*args)

    monkeypatch.setattr(submit, "_in_db", final_guard)
    monkeypatch.setattr(Session, "commit", after_commit)
    env.paused, env.click = paused, external_click
    result = run_submit(env, app_id)
    allowed = fault == "valid"
    assert len(form.gate_clicks) == int(allowed)
    assert result == ("submitted" if allowed else "ready")
    db.expire_all()
    app = db.get(Application, app_id)
    assert db.get(Authorisation, auth_id).used_at == used
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(ApplicationEvent)
            .where(ApplicationEvent.application_id == app_id, ApplicationEvent.kind == "click")
        )
        == 1
    )
    if not allowed:
        expected = (
            "paused"
            if fault in {"paused", "pause_error"}
            else "submit_disabled"
            if fault == "disabled"
            else "authorisation_expired"
        )
        assert (app.state, app.error_code, app.mode) == (State.ready, expected, Mode.review)
        assert frozen(db, person_a.cv_version_id) == [None]
