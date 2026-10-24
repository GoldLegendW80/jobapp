"""Mail lifetime, the proven non-send boundary, and exact email editor contracts."""

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.apply import caps, full_auto, gate, states
from app.apply.models import (
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    Authorisation,
    Mode,
    State,
)
from app.mail import dispatch
from app.mail import gate as mail_gate
from app.mail.draft_models import MailDraft, MailSend
from tests.apply.conftest import AT
from tests.apply.test_a13_full_auto import api, stub, turn_on
from tests.apply.test_a14_email import approve, connect, no_google, prepare, provider
from tests.mail.fake_gmail import FakeGoogle

__all__ = ["api", "no_google", "stub"]


def audit(user_id, at, **extra):
    return MailSend(
        id=uuid.uuid4(), user_id=user_id, at=at, updated_at=at, content_hash="0" * 64, **extra
    )


def test_dispatch_expires_send_audit_without_connection_or_send_key(
    db, factory, person_a, person_b
):
    old = AT - timedelta(days=365, microseconds=1)
    boundary = AT - timedelta(days=365)
    rows = [
        audit(person_a.user_id, old, result="sent"),
        audit(person_b.user_id, old, result="unknown"),
        audit(person_a.user_id, boundary, result="sent"),
        audit(person_b.user_id, AT, result="refused"),
    ]
    db.add_all(rows)
    db.commit()
    ids = [row.id for row in rows]
    state = SimpleNamespace(
        sessionmaker=factory,
        clock=lambda: AT,
        settings=SimpleNamespace(mail_sends_keep_days=365, mail_send_key=None),
        mail_mcp=None,
        mail_google=None,
        credentials_keyring=None,
    )
    assert asyncio.run(dispatch.once(SimpleNamespace(state=state))) == 0
    db.expire_all()
    assert [db.get(MailSend, wanted) is not None for wanted in ids] == [False, False, True, True]
    assert asyncio.run(dispatch.once(SimpleNamespace(state=state))) == 0


def test_audit_retention_is_bounded_and_keeps_uncertain_application_closed(db, factory, person_a):
    from app.mail import retention

    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake)
    application = prepare(db, person_a)
    auth = approve(db, application)
    settings, _, _ = provider(fake, ring)
    with factory() as other:
        row, _, _, _ = mail_gate._decide(
            other, auth, AT, settings, gate.Switches(enabled=True, paused=False)
        )
        other.commit()
        wanted = row.id
    # A dead process before provider invocation still leaves the audit unresolved. Pruning
    # must settle that binding conservatively before deleting its sole send record.
    later = AT + timedelta(days=366)
    with factory() as other:
        assert retention.prune(other, later, keep_days=365, batch_size=1) == 1
        other.commit()
    db.expire_all()
    application = db.get(Application, application.id)
    assert db.get(MailSend, wanted) is None
    assert application.state is State.submit_unknown
    assert db.get(MailDraft, auth.draft_id).status == "send_unknown"
    assert application.mail_send_id == wanted
    assert (
        asyncio.run(
            dispatch.application(
                SimpleNamespace(state=SimpleNamespace(sessionmaker=factory, clock=lambda: later)),
                application.id,
            )
        )
        is None
    )
    assert db.get(Authorisation, auth.apply_id).used_at is not None
    db.add_all([audit(person_a.user_id, AT, result="refused") for _ in range(3)])
    db.commit()
    assert retention.prune(db, later, keep_days=365, batch_size=2) == 2
    db.commit()
    assert db.scalar(sa.select(sa.func.count()).select_from(MailSend)) == 1


def approved_email(db, person, fake, mode):
    ring = connect(db, person, fake)
    if mode is Mode.full_auto:
        turn_on(db, person)
    application = prepare(db, person, mode=mode)
    if mode is Mode.review:
        auth, when = approve(db, application), AT
    else:
        answer = db.get(ApplicationAnswer, (application.id, "email_body"))
        answer.source, answer.value = "document", "Please find my application attached."
        db.commit()
        assert (
            full_auto.decide(db, application.id, AT, gate.Switches(enabled=True, paused=False))
            == "held"
        )
        db.commit()
        auth = mail_gate.authorise_application(db, application, AT)
        db.commit()
        when = AT + timedelta(minutes=15)
    return application, auth, when, provider(fake, ring, now=when)


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("pause", "paused"),
        ("disabled", "submit_disabled"),
        ("cancel", "not_open"),
        ("stale", "stale"),
        ("person_pause", "paused_by_you"),
        ("company_cap", "company_cap"),
        ("connection", "needs_reconnect"),
        ("guard", "never_apply_role"),
        ("expiry", "authorisation_expired"),
    ],
)
def test_local_stop_after_mime_never_becomes_a_provider_refusal(
    db, factory, person_a, monkeypatch, mode, case, code
):
    fake = FakeGoogle()
    fake.seed("alice")
    application, auth, when, (settings, app, mcp) = approved_email(db, person_a, fake, mode)
    minted = []
    switch_errors = []
    switch_reads = []
    original_call = mail_gate._call_send

    async def traced_call(*args, **kwargs):
        try:
            answer = await original_call(*args, **kwargs)
            if answer.get("result") == "not_sent":
                with factory() as other:
                    persisted = other.get(MailSend, args[3].id)
                    assert persisted.result == "failed"
                    assert persisted.refusal_code == code
            return answer
        except Exception as exc:
            switch_errors.append(repr(exc))
            raise

    monkeypatch.setattr(mail_gate, "_call_send", traced_call)
    monkeypatch.setattr(mail_gate.send_key, "mint", lambda *args: minted.append(args))

    async def switches():
        switch_reads.append(None)
        # The pause reader is called again under the final locks. The injected event
        # happens once; subsequent reads observe it without mutating it a second time.
        if len(switch_reads) > 1:
            return gate.Switches(enabled=case != "disabled", paused=case == "pause")
        with factory() as other:
            current = other.get(Application, application.id)
            assert current.state in {State.approved, State.held}
            assert other.get(MailSend, current.mail_send_id).result == "pending"
            if case == "cancel":
                states.move(current, State.cancelled, when)
            elif case == "stale":
                other.get(
                    ApplicationAnswer, (current.id, "email_subject")
                ).value = "Changed since approval"
            elif case == "person_pause":
                from app.apply.caps_models import ApplySettings

                values = other.get(ApplySettings, person_a.user_id)
                if values is None:
                    other.add(ApplySettings(user_id=person_a.user_id, paused=True))
                else:
                    values.paused = True
            elif case == "connection":
                from app.mail.models import AccountStatus, MailAccount

                other.get(MailAccount, person_a.user_id).status = AccountStatus.needs_reconnect
            elif case == "company_cap":
                from app.apply.caps_models import CompanyCap
                from app.jobs.models import Job
                from tests.apply.conftest import add_posting

                other.add(
                    CompanyCap(
                        user_id=person_a.user_id,
                        company_key=caps.company_key("Example Robotics"),
                        label="Example Robotics",
                        max_count=1,
                        window_days=30,
                    )
                )
                posting_id, _ = add_posting(
                    other, person_a.user_id, "https://example.org/another-role"
                )
                posting = other.get(Job, posting_id)
                posting.title, posting.status, posting.applied_on = (
                    "Different role",
                    "applied",
                    when.date(),
                )
                other.add(
                    Application(
                        id=uuid.uuid4(),
                        user_id=person_a.user_id,
                        posting_id=posting_id,
                        mode=Mode.review,
                        state=State.submitted,
                        dry_run=False,
                        ats="email",
                        form_url=posting.url,
                        canonical_key=f"email:{posting_id}",
                        submitted_at=when,
                        created_at=when,
                        updated_at=when,
                    )
                )
            elif case == "guard":
                from tests.apply.test_start import rules

                try:
                    rules(other, person_a.user_id, "Excluded company: Example Robotics")
                except Exception as exc:
                    switch_errors.append(repr(exc))
                    raise
            other.commit()
        return gate.Switches(enabled=case != "disabled", paused=case == "pause")

    async def scenario():
        async with app.router.lifespan_context(app):
            return await mail_gate.send(
                factory,
                auth,
                when,
                settings=settings,
                mcp=mcp,
                switches=gate.Switches(enabled=True, paused=False),
                switches_for=switches,
                clock=lambda: when + timedelta(days=8) if case == "expiry" else when,
            )

    row = asyncio.run(scenario())
    assert not switch_errors
    assert len(switch_reads) >= 2
    assert row.result == "failed"
    assert row.refusal_code == code
    assert minted == []
    assert fake.sent == []
    db.expire_all()
    application = db.get(Application, application.id)
    expected = (
        State.needs_person
        if case == "connection"
        else State.cancelled
        if case == "cancel" or (mode is Mode.review and case in {"pause", "disabled"})
        else State.blocked
        if case == "guard" and mode is Mode.review
        else State.ready
    )
    assert application.state is expected
    assert application.mode is Mode.review
    assert application.mail_send_id is None
    assert db.get(MailDraft, auth.draft_id).status == "draft"
    assert db.get(Authorisation, auth.apply_id).used_at is not None
    events = list(
        db.scalars(
            sa.select(ApplicationEvent).where(ApplicationEvent.application_id == application.id)
        )
    )
    assert not any(event.kind == "click" for event in events)
    if mode is Mode.full_auto:
        assert any(event.kind == "sent_to_review" for event in events)
    assert (
        asyncio.run(
            dispatch.application(
                SimpleNamespace(state=SimpleNamespace(sessionmaker=factory, clock=lambda: when)),
                application.id,
            )
        )
        is None
    )


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ({"result": "gmail_4xx", "status": 400}, State.refused_by_site),
        ({"result": "unknown"}, State.submit_unknown),
    ],
)
def test_actual_provider_outcomes_keep_the_irreversible_boundary(
    db, factory, person_a, outcome, expected
):
    fake = FakeGoogle()
    fake.seed("alice")
    application, auth, when, (settings, _, _) = approved_email(db, person_a, fake, Mode.full_auto)
    calls = []

    class Provider:
        async def call(self, *args):
            with factory() as other:
                assert other.get(Application, application.id).state is State.submitting
                assert (
                    other.get(MailSend, other.get(Application, application.id).mail_send_id).result
                    == "pending"
                )
            calls.append(args)
            return outcome

    row = asyncio.run(
        mail_gate.send(
            factory,
            auth,
            when,
            settings=settings,
            mcp=Provider(),
            switches=gate.Switches(enabled=True, paused=False),
        )
    )
    assert len(calls) == 1
    db.expire_all()
    assert db.get(Application, application.id).state is expected
    assert row.result == ("failed" if expected is State.refused_by_site else "unknown")


@pytest.mark.parametrize(
    "subject", ["x" * 999, "x" * 1000, "hello\rworld", "hello\nworld", "hello\r\nworld"]
)
def test_email_subject_edit_rejects_before_mutation(api, db, person_a, subject):
    application = prepare(db, person_a)
    token = api.login_token(person_a.email)
    original = db.get(ApplicationAnswer, (application.id, "email_subject")).value
    before = gate.current_hash(db, application)
    response = api.request(
        "PUT",
        f"/api/applications/{application.id}/answers/email_subject",
        token=token,
        json={"value": subject},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "email_subject"
    db.expire_all()
    assert db.get(ApplicationAnswer, (application.id, "email_subject")).value == original
    assert gate.current_hash(db, db.get(Application, application.id)) == before


def test_exact_subject_limit_and_legacy_correction(api, db, person_a):
    fake = FakeGoogle()
    fake.seed("alice")
    connect(db, person_a, fake)
    application = prepare(db, person_a)
    token = api.login_token(person_a.email)
    path = f"/api/applications/{application.id}"
    db.get(ApplicationAnswer, (application.id, "email_subject")).value = "x" * 1000
    db.commit()
    view = api.get(path + "/approval", token=token)
    assert view.status_code == 200
    subject = next(row for row in view.json()["answers"] if row["field_key"] == "email_subject")
    assert subject["max_chars"] == 998
    invalid = api.post(
        path + "/approve",
        token=token,
        json={
            "answers_hash": view.json()["answers_hash"],
            "confirmed_email_recipient": "e2e-jobs@example.org",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "email_subject"
    db.expire_all()
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(Authorisation)
            .where(Authorisation.application_id == application.id)
        )
        == 0
    )
    corrected = api.request(
        "PUT", path + "/answers/email_subject", token=token, json={"value": "x" * 998}
    )
    assert corrected.status_code == 200
    assert (
        next(row for row in corrected.json()["answers"] if row["field_key"] == "email_subject")[
            "value"
        ]
        == "x" * 998
    )


@pytest.mark.parametrize("after_boundary", [False, True])
def test_expired_pending_attempt_is_coherent_and_never_resends(
    db, factory, person_a, after_boundary
):
    fake = FakeGoogle()
    fake.seed("alice")
    application, auth, when, (settings, _, mcp) = approved_email(db, person_a, fake, Mode.review)
    with factory() as other:
        row, _, _, _ = mail_gate._decide(
            other, auth, when, settings, gate.Switches(enabled=True, paused=False)
        )
        if after_boundary:
            states.move(other.get(Application, application.id), State.submitting, when)
        other.commit()
        send_id = row.id
    later = when + timedelta(seconds=120)
    with factory() as other:
        mail_gate.expire_pending(other, person_a.user_id, later)
        other.commit()
    db.expire_all()
    assert db.get(Application, application.id).state is State.submit_unknown
    assert db.get(Application, application.id).mail_send_id == send_id
    assert db.get(MailDraft, auth.draft_id).status == "send_unknown"
    assert db.get(MailSend, send_id).result == "unknown"
    again = asyncio.run(
        mail_gate.send(
            factory,
            auth,
            later,
            settings=settings,
            mcp=mcp,
            switches=gate.Switches(enabled=True, paused=False),
        )
    )
    assert again.id == send_id
    assert again.result == "unknown"
    assert fake.sent == []
    assert db.get(Authorisation, auth.apply_id).used_at is not None


def test_expired_audit_survives_disconnect_and_posting_deletion_until_cutoff(db, factory, person_a):
    from app.jobs.models import Job
    from app.mail import accounts, retention
    from app.mail.models import MailAccount

    fake = FakeGoogle()
    fake.seed("alice")
    connect(db, person_a, fake)
    old = AT - timedelta(days=365, microseconds=1)
    row = audit(person_a.user_id, old, result="sent", posting_id=person_a.posting_id)
    db.add(row)
    db.commit()
    wanted = row.id
    asyncio.run(
        accounts.disconnect(
            sessions=factory, google=None, keyring=None, user_id=person_a.user_id, now=AT
        )
    )
    db.expire_all()
    assert db.get(MailAccount, person_a.user_id) is None
    assert db.get(MailSend, wanted) is not None
    db.delete(db.get(Job, person_a.posting_id))
    db.commit()
    assert db.get(MailSend, wanted) is not None
    assert retention.prune(db, AT - timedelta(microseconds=1), keep_days=365) == 0
    db.commit()
    assert retention.prune(db, AT, keep_days=365) == 1
    db.commit()
    assert db.get(MailSend, wanted) is None


def test_new_approval_during_mime_supersedes_the_old_send_authority(db, factory, person_a):
    fake = FakeGoogle()
    fake.seed("alice")
    application, auth, when, (settings, app, mcp) = approved_email(db, person_a, fake, Mode.review)
    newer = []
    switch_reads = []

    async def reapprove():
        switch_reads.append(None)
        if newer:
            return gate.Switches(enabled=True, paused=False)
        with factory() as other:
            current = other.get(Application, application.id)
            gate.set_answer(
                other, person_a.user_id, current.id, "email_subject", "New exact subject", when
            )
            other.commit()
            fresh = gate.authorise_approval(
                other,
                person_a.user_id,
                current.id,
                gate.current_hash(other, current),
                when + timedelta(seconds=1),
                86400,
            )
            assert gate.decide(
                other,
                person_a.user_id,
                fresh.id,
                when + timedelta(seconds=1),
                switches=gate.Switches(enabled=True, paused=False),
            ).passed
            newer.append(fresh.id)
            other.commit()
        return gate.Switches(enabled=True, paused=False)

    async def scenario():
        async with app.router.lifespan_context(app):
            return await mail_gate.send(
                factory,
                auth,
                when,
                settings=settings,
                mcp=mcp,
                switches=gate.Switches(enabled=True, paused=False),
                switches_for=reapprove,
            )

    result = asyncio.run(scenario())
    assert result.result == "failed"
    assert result.refusal_code == "not_authorised"
    assert len(switch_reads) >= 2
    assert fake.sent == []
    db.expire_all()
    assert db.get(Application, application.id).state is State.ready
    assert db.get(ApplicationAnswer, (application.id, "email_subject")).value == "New exact subject"
    assert db.get(Authorisation, auth.apply_id).used_at is not None
    assert db.get(Authorisation, newer[0]).used_at is not None


@pytest.mark.parametrize("disconnect", [False, True])
@pytest.mark.parametrize("binding", ["exact", "wrong_owner", "wrong_posting", "wrong_send"])
def test_pending_application_recovers_after_its_private_content_is_gone(
    db, factory, person_a, person_b, disconnect, binding
):
    from app.mail import accounts

    fake = FakeGoogle()
    fake.seed("alice")
    application, auth, when, (settings, _, _) = approved_email(db, person_a, fake, Mode.review)
    with factory() as other:
        row, _, _, _ = mail_gate._decide(
            other, auth, when, settings, gate.Switches(enabled=True, paused=False)
        )
        other.commit()
        send_id = row.id
    if disconnect:
        asyncio.run(
            accounts.disconnect(
                sessions=factory, google=None, keyring=None, user_id=person_a.user_id, now=when
            )
        )
    else:
        db.delete(db.get(MailDraft, auth.draft_id))
        db.commit()
    db.expire_all()
    if binding == "wrong_owner":
        db.get(MailSend, send_id).user_id = person_b.user_id
    elif binding == "wrong_posting":
        db.get(MailSend, send_id).posting_id = person_b.posting_id
    elif binding == "wrong_send":
        db.get(Application, application.id).mail_send_id = uuid.uuid4()
    db.commit()
    with factory() as other:
        mail_gate.expire_pending(
            other,
            person_b.user_id if binding == "wrong_owner" else person_a.user_id,
            when + timedelta(seconds=120),
        )
        other.commit()
    db.expire_all()
    assert db.get(MailDraft, auth.draft_id) is None
    assert db.get(MailSend, send_id).result == "unknown"
    assert db.get(Application, application.id).state is (
        State.submit_unknown if binding == "exact" else State.approved
    )
    if binding != "wrong_send":
        assert db.get(Application, application.id).mail_send_id == send_id
    assert db.get(Authorisation, auth.apply_id).used_at is not None
    assert fake.sent == []
