"""API failures preserve ownership, local answers and durable recovery state."""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from app import queue as queues
from app.apply import admin, gate, manual, run, submit
from app.apply.models import (
    AnswerBankEntry,
    AnswerSource,
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    Authorisation,
    Mode,
    State,
)
from app.documents.models import DocumentVersion
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus
from app.mail import dispatch
from app.mail.draft_models import MailDraft, MailSend
from tests.apply.a6_support import FakeQueue, FakeRedis
from tests.apply.conftest import AT
from tests.apply.test_a5_run import application, events, fill, services
from tests.apply.test_a6_gate import _password, api_app, ready, stub
from tests.apply.test_a9_manual import start_manual
from tests.apply.test_a14_email import connect, no_google, prepare
from tests.apply.test_a15_owned import start_wizard, unknown_form
from tests.identity.api_helpers import Api
from tests.mail.fake_gmail import FakeGoogle

__all__ = ["api_app", "no_google", "stub"]


@dataclass
class PanelRedis(FakeRedis):
    commands: dict[str, list[str]] = field(default_factory=dict)

    async def llen(self, key):
        if self.broken:
            raise ConnectionError("invented panel outage")
        return len(self.commands.get(key, []))

    async def rpush(self, key, command):
        self.commands.setdefault(key, []).append(command)

    async def expire(self, key, seconds):
        return True


@dataclass
class OfflineQueue(FakeQueue):
    async def pool(self):
        raise queues.QueueUnavailableError("invented queue outage")

    async def enqueue(self, *args, **kwargs):
        raise queues.QueueUnavailableError("invented queue outage")


@pytest.fixture
def panel(api_app, factory, person_a, person_b):
    _password(factory, person_a, person_b)
    with TestClient(api_app, follow_redirects=False) as client:
        api_app.state.queue = FakeQueue(redis=PanelRedis())
        api = Api(client)
        yield SimpleNamespace(
            api=api,
            app=api_app,
            owner=api.login_token(person_a.email),
            other=api.login_token(person_b.email),
        )


def durable(db, app_id):
    found = application(db, app_id)
    return (
        found.state,
        found.needs,
        found.error_code,
        found.submitted_at,
        [(e.kind, e.data) for e in events(db, app_id)],
    )


def assert_unsent(db, person):
    db.expire_all()
    assert db.get(Job, person.posting_id).status is not JobStatus.applied
    versions = db.scalars(
        sa.select(DocumentVersion).where(
            DocumentVersion.id.in_([person.cv_version_id, person.letter_version_id])
        )
    ).all()
    assert len(versions) == 2
    assert all(v.frozen_at is None for v in versions)


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "/manual", None),
        ("POST", "/manual/actions", {"action": "attach_cv"}),
        ("POST", "/manual/close", None),
        ("GET", "/approval", None),
        ("PUT", "/answers/identity:full_name", {"value": "Other Candidate"}),
    ],
)
def test_unknown_or_foreign_routes_reveal_no_state_and_change_nothing(
    db, person_a, panel, method, suffix, body
):
    app_id = start_manual(db, person_a)
    db.commit()
    before = durable(db, app_id)
    missing = panel.api.request(
        method, f"/api/applications/not-a-uuid{suffix}", token=panel.owner, json=body
    )
    foreign = panel.api.request(
        method, f"/api/applications/{app_id}{suffix}", token=panel.other, json=body
    )
    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == foreign.json()
    assert durable(db, app_id) == before
    assert panel.app.state.queue.enqueued == []
    assert panel.app.state.queue.redis.commands == {}
    assert_unsent(db, person_a)


@pytest.mark.parametrize("suffix", ["open", "actions", "close"])
def test_review_applications_cannot_accept_manual_commands(db, person_a, panel, suffix):
    app_id = start_manual(db, person_a)
    db.get(Application, app_id).mode = Mode.review
    db.commit()
    before = durable(db, app_id)
    response = panel.api.post(
        f"/api/applications/{app_id}/manual/{suffix}",
        token=panel.owner,
        json={"action": "attach_cv"} if suffix == "actions" else None,
    )
    assert (response.status_code, response.json()["code"]) == (409, "not_manual")
    assert response.headers["cache-control"] == "no-store"
    assert durable(db, app_id) == before
    assert panel.app.state.queue.enqueued == []
    assert panel.app.state.queue.redis.commands == {}


def test_unavailable_manual_enqueue_preserves_the_queued_application(db, person_a, panel):
    app_id = start_manual(db, person_a)
    db.commit()
    before = durable(db, app_id)
    panel.app.state.queue = OfflineQueue()
    response = panel.api.post(f"/api/applications/{app_id}/manual/open", token=panel.owner)
    assert (response.status_code, response.json()["code"]) == (409, "unavailable")
    assert durable(db, app_id) == before
    assert panel.app.state.queue.enqueued == []
    assert_unsent(db, person_a)


@pytest.mark.parametrize("outcome", ["accepted", "full", "outage"])
def test_manual_command_refusals_do_not_add_commands_or_record_a_submission(
    db, person_a, panel, outcome
):
    app_id = start_manual(db, person_a)
    assert manual.begin(db, app_id, AT)
    db.commit()
    before = durable(db, app_id)
    redis = panel.app.state.queue.redis
    key = f"{manual.KEY}{app_id}:commands"
    initial = ["attach_letter"] * (manual.COMMANDS_MAX if outcome == "full" else 0)
    redis.commands[key] = initial.copy()
    redis.broken = outcome == "outage"
    response = panel.api.post(
        f"/api/applications/{app_id}/manual/actions",
        token=panel.owner,
        json={"action": "attach_cv"},
    )
    if outcome == "accepted":
        assert response.status_code == 202
        assert response.json()["state"] == "filling"
        assert redis.commands[key] == ["attach_cv"]
    else:
        assert response.status_code == 409
        assert response.json()["code"] == ("too_many" if outcome == "full" else "unavailable")
        assert redis.commands[key] == initial
    assert response.headers["cache-control"] == "no-store"
    assert durable(db, app_id) == before
    assert panel.app.state.queue.enqueued == []
    assert_unsent(db, person_a)


def test_reopening_an_active_manual_session_does_not_restart_the_worker(db, person_a, panel):
    app_id = start_manual(db, person_a)
    assert manual.begin(db, app_id, AT)
    db.commit()
    response = panel.api.post(f"/api/applications/{app_id}/manual/open", token=panel.owner)
    assert response.status_code == 202
    assert response.json()["state"] == "filling"
    assert panel.app.state.queue.enqueued == []
    assert application(db, app_id).attempt == 1


def test_legacy_creation_without_warning_codes_has_a_safe_manual_status(db, person_a, panel):
    app_id = start_manual(db, person_a)
    created = db.scalar(
        sa.select(ApplicationEvent).where(
            ApplicationEvent.application_id == app_id, ApplicationEvent.kind == "created"
        )
    )
    created.data = {}
    db.commit()
    before = durable(db, app_id)
    response = panel.api.get(f"/api/applications/{app_id}/manual", token=panel.owner)
    assert response.status_code == 200
    assert response.json()["warnings"] == []
    assert response.json()["state"] == "queued"
    assert durable(db, app_id) == before
    assert panel.app.state.queue.enqueued == []


def test_manual_close_is_committed_and_cannot_later_be_recorded(db, person_a, panel):
    app_id = start_manual(db, person_a)
    db.commit()
    response = panel.api.post(f"/api/applications/{app_id}/manual/close", token=panel.owner)
    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.cancelled, "closed")
    refused = panel.api.post(
        f"/api/applications/{app_id}/manual/record", token=panel.owner, json={"seen": False}
    )
    assert (refused.status_code, refused.json()["code"]) == (409, "not_open")
    assert not any(e.kind == "recorded" for e in events(db, app_id))
    assert_unsent(db, person_a)


@pytest.mark.parametrize("suffix", ["record", "close"])
@pytest.mark.parametrize("failure", ["busy", "gone"])
def test_manual_lock_refusals_preserve_the_application(
    db, person_a, panel, monkeypatch, suffix, failure
):
    app_id = start_manual(db, person_a)
    db.commit()
    before = durable(db, app_id)

    def unavailable(*args, **kwargs):
        if failure == "gone":
            raise gate.NotFoundError()
        raise jobs_service.JobBusyError(jobs_service.BUSY)

    monkeypatch.setattr(gate, "lock", unavailable)
    response = panel.api.post(
        f"/api/applications/{app_id}/manual/{suffix}", token=panel.owner, json={}
    )
    assert response.status_code == (404 if failure == "gone" else 409)
    if failure == "busy":
        assert response.json()["code"] == "busy"
        assert response.headers["cache-control"] == "no-store"
    assert durable(db, app_id) == before
    assert_unsent(db, person_a)


def test_approval_survives_queue_failure_for_the_tick_to_retry(db, factory, person_a, stub, panel):
    app_id = ready(db, factory, person_a, stub)
    view = panel.api.get(f"/api/applications/{app_id}/approval", token=panel.owner).json()
    panel.app.state.queue = queue = FakeQueue()

    async def offline(*args, **kwargs):
        raise queues.QueueUnavailableError("invented enqueue outage")

    queue.enqueue = offline
    response = panel.api.post(
        f"/api/applications/{app_id}/approve",
        token=panel.owner,
        json={"answers_hash": view["answers_hash"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "approved"
    assert application(db, app_id).state is State.approved
    assert app_id in submit.due(db, AT)
    assert queue.enqueued == []
    assert len(db.scalars(sa.select(Authorisation)).all()) == 1
    assert_unsent(db, person_a)


def test_a_non_hex_approval_hash_is_stale_without_creating_an_authorisation(
    db, factory, person_a, stub, panel
):
    app_id = ready(db, factory, person_a, stub)
    before = durable(db, app_id)
    response = panel.api.post(
        f"/api/applications/{app_id}/approve", token=panel.owner, json={"answers_hash": "z" * 64}
    )
    assert (response.status_code, response.json()["code"]) == (409, "stale")
    assert durable(db, app_id) == before
    assert db.scalars(sa.select(Authorisation)).all() == []
    assert panel.app.state.queue.enqueued == []
    assert_unsent(db, person_a)


@pytest.mark.parametrize("confirmed", [False, True])
def test_email_recipient_confirmation_controls_dispatch_after_the_approval_commits(
    db, factory, person_a, panel, monkeypatch, no_google, confirmed
):
    fake = FakeGoogle()
    fake.seed("alice")
    connect(db, person_a, fake)
    app = prepare(db, person_a)
    app_id = app.id
    draft = db.scalar(sa.select(MailDraft).where(MailDraft.application_id == app_id))
    assert draft is not None
    before_draft = (draft.to_addrs, draft.subject, draft.body, draft.fact_check)
    view = panel.api.get(f"/api/applications/{app_id}/approval", token=panel.owner).json()
    dispatched = []

    async def hold_dispatch(app, wanted):
        with factory() as persisted:
            approved = persisted.get(Application, wanted)
            assert approved.state is State.approved
            assert approved.answers_hash.hex() == view["answers_hash"]
            authorisation = persisted.scalar(
                sa.select(Authorisation).where(Authorisation.application_id == wanted)
            )
            assert authorisation.used_at == AT
            dispatched.append(wanted)

    monkeypatch.setattr(dispatch, "application", hold_dispatch)
    response = panel.api.post(
        f"/api/applications/{app_id}/approve",
        token=panel.owner,
        json={
            "answers_hash": view["answers_hash"],
            "confirmed_email_recipient": "e2e-jobs@example.org"
            if confirmed
            else "e2e-other@example.org",
        },
    )
    db.expire_all()
    if confirmed:
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "approved"
        assert dispatched == [app_id]
        assert db.get(MailDraft, draft.id).fact_check["acknowledged"] == []
    else:
        assert (response.status_code, response.json()["code"]) == (409, "unconfirmed_recipient")
        assert application(db, app_id).state is State.ready
        assert db.scalars(sa.select(Authorisation)).all() == []
        assert dispatched == []
        saved = db.get(MailDraft, draft.id)
        assert (saved.to_addrs, saved.subject, saved.body, saved.fact_check) == before_draft
    assert fake.sent == []
    assert db.scalars(sa.select(MailSend)).all() == []
    assert panel.app.state.queue.enqueued == []
    assert_unsent(db, person_a)


def test_partial_wizard_answer_commits_locally_when_the_continuation_queue_is_down(
    db, factory, person_a, person_b, stub, panel
):
    app_id = start_wizard(db, person_a, stub)
    form = unknown_form()
    assert fill(services(factory, form, stub_base=stub), app_id) == "needs_person"
    row = db.scalar(
        sa.select(ApplicationAnswer).where(
            ApplicationAnswer.application_id == app_id,
            ApplicationAnswer.label == "Your project codename",
        )
    )
    assert row is not None
    key = row.field_key
    for person, answer in (
        (person_a, "Owner reusable answer"),
        (person_b, "Other reusable answer"),
    ):
        db.add(
            AnswerBankEntry(
                user_id=person.user_id,
                question_key="your project codename",
                question="Your project codename",
                answer=answer,
            )
        )
    db.commit()
    panel.app.state.queue = OfflineQueue()
    response = panel.api.request(
        "PUT",
        f"/api/applications/{app_id}/answers/{quote(key, safe='')}",
        token=panel.owner,
        json={"value": "Application Cedar"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "queued"
    db.expire_all()
    saved = db.get(ApplicationAnswer, (app_id, key))
    assert (saved.value, saved.source, saved.approved) == (
        "Application Cedar",
        AnswerSource.person,
        False,
    )
    assert application(db, app_id).needs is None
    assert app_id in run.due(db, AT)
    assert {
        r.user_id: (r.answer, r.used_count) for r in db.scalars(sa.select(AnswerBankEntry))
    } == {
        person_a.user_id: ("Owner reusable answer", 0),
        person_b.user_id: ("Other reusable answer", 0),
    }
    assert panel.app.state.queue.enqueued == []
    answer_events = [e.data for e in events(db, app_id) if e.kind == "answer_set"]
    assert answer_events == [{"field_key": key}]
    assert_unsent(db, person_a)


@pytest.mark.parametrize("failure", ["busy", "ended"])
def test_answer_edit_refusals_preserve_the_original_answer(
    db, factory, person_a, stub, panel, monkeypatch, failure
):
    app_id = ready(db, factory, person_a, stub)
    row = next(r for r in gate.rows_of(db, application(db, app_id)) if r.kind == "text")
    key, original = row.field_key, (row.value, row.source)
    if failure == "ended":
        gate.cancel(db, person_a.user_id, app_id, AT, reject=False)
        db.commit()
    else:

        def busy(*args, **kwargs):
            raise jobs_service.JobBusyError(jobs_service.BUSY)

        monkeypatch.setattr(gate, "lock", busy)
    before = durable(db, app_id)
    response = panel.api.request(
        "PUT",
        f"/api/applications/{app_id}/answers/{quote(key, safe='')}",
        token=panel.owner,
        json={"value": "Refused Candidate"},
    )
    assert (response.status_code, response.json()["code"]) == (
        409,
        "busy" if failure == "busy" else "not_editable",
    )
    assert durable(db, app_id) == before
    db.expire_all()
    saved = db.get(ApplicationAnswer, (app_id, key))
    assert (saved.value, saved.source) == original
    assert panel.app.state.queue.enqueued == []


@dataclass
class AdminRedis(FakeRedis):
    closed: bool = False
    fail_write: bool = False

    async def set(self, name, value):
        if self.fail_write:
            raise ConnectionError("invented write outage")
        await super().set(name, value)

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize(
    ("action", "initial", "broken", "expected"),
    [
        ("pause", {}, False, "paused"),
        ("resume", {admin.PAUSE_KEY: "1"}, False, "running"),
        ("status", {admin.PAUSE_KEY: "1"}, False, "paused"),
        ("status", {}, True, admin.UNREADABLE),
    ],
)
def test_admin_command_reports_the_effective_flag_and_closes_its_connection(
    monkeypatch, capsys, action, initial, broken, expected
):
    from app import config

    redis = AdminRedis(flags=initial.copy(), broken=broken)
    monkeypatch.setattr(Redis, "from_url", lambda url: redis)
    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: SimpleNamespace(redis_url=SimpleNamespace(get_secret_value=lambda: "redis://fake")),
    )
    assert admin.main([action]) == 0
    assert capsys.readouterr().out == f"apply submissions: {expected}\n"
    assert redis.closed
    assert asyncio.run(admin.paused(redis)) is (expected != "running")


def test_failed_admin_write_is_not_reported_as_success_and_closes_its_connection(monkeypatch):
    redis = AdminRedis(fail_write=True)
    monkeypatch.setattr(Redis, "from_url", lambda url: redis)
    with pytest.raises(ConnectionError, match="invented write outage"):
        asyncio.run(admin._command("pause", "redis://fake"))
    assert redis.closed
    assert redis.flags == {}
