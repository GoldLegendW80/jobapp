"""The person's two confirmation routes settle one exact email attempt on real Postgres."""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
import sqlalchemy as sa

from app.apply import gate, service
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.documents.models import DocumentVersion
from app.jobs import service as jobs_service
from app.jobs.models import Job
from app.mail import gate as mail_gate
from app.mail import locks, reconciling
from app.mail.draft_models import MailDraft, MailSend
from tests.apply.conftest import AT
from tests.apply.test_a14_email import api, approve, connect, no_google, prepare, provider, stub
from tests.mail.fake_gmail import FakeGoogle

__all__ = ["api", "no_google", "stub"]


def unknown(db, factory, person):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person, fake)
    application = prepare(db, person)
    auth = approve(db, application)
    fake.switch("send_answer", "error")
    settings, app, mcp = provider(fake, ring)

    async def send():
        async with app.router.lifespan_context(app):
            return await mail_gate.send(
                factory,
                auth,
                AT,
                settings=settings,
                mcp=mcp,
                switches=gate.Switches(enabled=True, paused=False),
            )

    row = asyncio.run(send())
    assert row.result == "unknown"
    db.expire_all()
    assert db.get(Application, application.id).state is State.submit_unknown
    return application.id, row.id, auth.draft_id, fake, ring, settings


def confirm(api, token, application_id, send_id, entry):
    path, body = (
        (f"/api/applications/{application_id}/submitted", {})
        if entry == "apply"
        else (f"/api/mail/sends/{send_id}/resolve", {"action": "sent"})
    )
    return api.post(path, token=token, json=body)


def snapshot(db, application_id, send_id, draft_id):
    db.expire_all()
    application = db.get(Application, application_id)
    row, draft = db.get(MailSend, send_id), db.get(MailDraft, draft_id)
    versions = [
        db.get(DocumentVersion, uuid.UUID(record["version_id"]))
        for record in application.document_versions.values()
    ]
    return (
        application.state,
        application.submitted_at,
        row.result,
        row.resolved_by,
        row.gmail_message_id,
        row.gmail_thread_id,
        draft.status,
        draft.gmail_message_id,
        draft.sent_at,
        tuple((v.frozen_at, v.frozen_reason) for v in versions),
        db.scalar(
            sa.select(sa.func.count())
            .select_from(ApplicationEvent)
            .where(
                ApplicationEvent.application_id == application_id,
                ApplicationEvent.kind == "person_confirmed",
            )
        ),
    )


@pytest.mark.parametrize("entry", ["apply", "mail"])
def test_person_confirmation_settles_exact_attempt_and_replays(api, db, factory, person_a, entry):
    app_id, send_id, draft_id, fake, _, _ = unknown(db, factory, person_a)
    token = api.login_token(person_a.email)
    response = confirm(api, token, app_id, send_id, entry)
    assert response.status_code == 200, response.text
    first = snapshot(db, app_id, send_id, draft_id)
    assert first[0] is State.submitted
    assert first[2:4] == ("sent", "user")
    assert first[6] == "sent"
    assert all(at == AT for at, _ in first[9])
    assert first[10] == 1
    assert db.get(Job, person_a.posting_id).status.value == "applied"
    db.commit()
    for replay in (entry, "mail" if entry == "apply" else "apply"):
        response = confirm(api, token, app_id, send_id, replay)
        assert response.status_code == 200, response.text
        assert snapshot(db, app_id, send_id, draft_id) == first
        db.commit()
    assert len(fake.sent) == 1


@pytest.mark.parametrize("entry", ["apply", "mail"])
def test_provider_confirmation_survives_person_replay(api, db, factory, person_a, entry):
    app_id, send_id, draft_id, fake, ring, _ = unknown(db, factory, person_a)
    _, app, mcp = provider(fake, ring)

    async def reconcile():
        async with app.router.lifespan_context(app):
            return await reconciling.reconcile(factory, person_a.user_id, mcp, AT)

    assert asyncio.run(reconcile()) == 1
    first = snapshot(db, app_id, send_id, draft_id)
    assert first[2:4] == ("sent", "reconcile")
    assert first[4]
    assert first[5]
    db.commit()
    response = confirm(api, api.login_token(person_a.email), app_id, send_id, entry)
    assert response.status_code == 200, response.text
    assert snapshot(db, app_id, send_id, draft_id) == first
    assert len(fake.sent) == 1


def test_person_confirmation_skips_later_provider_reconciliation(api, db, factory, person_a):
    app_id, send_id, draft_id, fake, ring, _ = unknown(db, factory, person_a)
    _, app, mcp = provider(fake, ring)
    assert (
        confirm(api, api.login_token(person_a.email), app_id, send_id, "apply").status_code == 200
    )
    first = snapshot(db, app_id, send_id, draft_id)
    db.commit()

    async def reconcile():
        async with app.router.lifespan_context(app):
            return await reconciling.reconcile(factory, person_a.user_id, mcp, AT)

    assert asyncio.run(reconcile()) == 0
    assert snapshot(db, app_id, send_id, draft_id) == first
    assert len(fake.sent) == 1


@pytest.mark.parametrize("entry", ["apply", "mail"])
def test_existing_application_confirmation_repairs_only_mail_records(
    api, db, factory, person_a, entry
):
    app_id, send_id, draft_id, fake, _, _ = unknown(db, factory, person_a)
    application = db.get(Application, app_id)
    application.state, application.submitted_at = State.submitted, AT
    db.add(
        ApplicationEvent(
            application_id=app_id,
            user_id=person_a.user_id,
            at=AT,
            kind="person_confirmed",
            data={},
        )
    )
    db.commit()
    response = confirm(api, api.login_token(person_a.email), app_id, send_id, entry)
    assert response.status_code == 200, response.text
    result = snapshot(db, app_id, send_id, draft_id)
    assert result[0:4] == (State.submitted, AT, "sent", "user")
    assert result[6] == "sent"
    assert result[10] == 1
    assert len(fake.sent) == 1


@pytest.mark.parametrize("entry", ["apply", "mail"])
def test_confirmation_rolls_all_records_back_on_transition_failure(
    api, db, factory, person_a, entry, monkeypatch
):
    app_id, send_id, draft_id, fake, _, _ = unknown(db, factory, person_a)
    first = snapshot(db, app_id, send_id, draft_id)
    db.commit()

    raised = []

    def fail(*args, **kwargs):
        raised.append(True)
        raise RuntimeError("synthetic status failure")

    monkeypatch.setattr(jobs_service, "set_status", fail)
    # The app's one answer to an error no route answered: a fixed 500 without the trace.
    answer = confirm(api, api.login_token(person_a.email), app_id, send_id, entry)
    assert answer.status_code == 500, answer.text
    assert answer.json() == {"detail": "internal server error"}
    assert raised == [True]
    assert snapshot(db, app_id, send_id, draft_id) == first
    assert len(fake.sent) == 1


@pytest.mark.parametrize("entry", ["apply", "mail"])
@pytest.mark.parametrize(
    "binding", ["missing_send", "stale_send", "foreign_send", "missing_draft", "foreign_draft"]
)
def test_confirmation_refuses_invalid_binding(api, db, factory, person_a, person_b, entry, binding):
    app_id, send_id, draft_id, fake, _, _ = unknown(db, factory, person_a)
    application, row = db.get(Application, app_id), db.get(MailSend, send_id)
    if binding == "missing_send":
        application.mail_send_id = None
    elif binding == "stale_send":
        application.mail_send_id = uuid.uuid4()
    elif binding == "foreign_send":
        row.user_id = person_b.user_id
    elif binding == "missing_draft":
        row.draft_id = uuid.uuid4()
    else:
        foreign = prepare(db, person_b)
        row.draft_id = db.scalar(
            sa.select(MailDraft.id).where(MailDraft.application_id == foreign.id)
        )
    db.commit()
    response = confirm(api, api.login_token(person_a.email), app_id, send_id, entry)
    assert response.status_code == (404 if binding == "foreign_send" and entry == "mail" else 409)
    db.expire_all()
    assert db.get(Application, app_id).state is State.submit_unknown
    assert db.get(MailSend, send_id).result == "unknown"
    assert db.get(MailDraft, draft_id).status == "send_unknown"
    assert len(fake.sent) == 1


@pytest.mark.parametrize("entry", ["apply", "mail"])
def test_confirmation_is_owner_scoped(api, db, factory, person_a, person_b, entry):
    app_id, send_id, draft_id, fake, _, _ = unknown(db, factory, person_a)
    response = confirm(api, api.login_token(person_b.email), app_id, send_id, entry)
    assert response.status_code == 404
    assert snapshot(db, app_id, send_id, draft_id)[0] is State.submit_unknown
    assert len(fake.sent) == 1


@pytest.mark.parametrize("mode", [Mode.review, Mode.manual])
def test_form_confirmation_preserves_replay_refusal(api, db, person_a, mode):
    application = service.start(
        db, person_a.user_id, person_a.posting_id, mode, False, now=AT
    ).application
    application.state, application.submitted_at = State.submit_unknown, AT
    db.commit()
    token = api.login_token(person_a.email)
    response = confirm(api, token, application.id, uuid.uuid4(), "apply")
    assert response.status_code == 200, response.text
    response = confirm(api, token, application.id, uuid.uuid4(), "apply")
    assert response.status_code == 409
    assert response.json()["code"] == "not_unknown"


@pytest.mark.parametrize("provider_first", [False, True])
def test_person_and_provider_confirmation_serialize(db, factory, person_a, provider_first):
    app_id, send_id, draft_id, fake, _, _ = unknown(db, factory, person_a)
    start = Barrier(2)

    def provider():
        with factory() as current:
            if provider_first:
                locks.lock_send(current, person_a.user_id)
            start.wait()
            locks.lock_send(current, person_a.user_id)
            row = current.get(MailSend, send_id, populate_existing=True)
            if row.result == "unknown":
                mail_gate._finish(
                    current,
                    row,
                    "sent",
                    AT + timedelta(seconds=1),
                    message_id="synthetic-confirmed",
                    thread_id="synthetic-thread",
                    resolved_by="reconcile",
                )
            current.commit()

    def person():
        with factory() as current:
            if not provider_first:
                locks.lock_send(current, person_a.user_id)
            start.wait()
            gate.confirm_submitted(current, person_a.user_id, app_id, AT + timedelta(seconds=2))
            current.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(provider), pool.submit(person)]
        for future in futures:
            future.result()
    result = snapshot(db, app_id, send_id, draft_id)
    assert result[0] is State.submitted
    assert result[2:4] == ("sent", "reconcile" if provider_first else "user")
    assert result[4] == ("synthetic-confirmed" if provider_first else None)
    assert result[6] == "sent"
    assert result[10] == 1
    assert len(fake.sent) == 1
