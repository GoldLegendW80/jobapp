"""Final mail pacing keeps a valid approval reusable without crossing the send boundary."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.apply import caps, email, full_auto, gate, service
from app.apply.models import (
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    Authorisation,
    Mode,
    State,
)
from app.documents.models import DocumentVersion
from app.jobs.models import Job
from app.mail import dispatch
from app.mail import gate as mail_gate
from app.mail.draft_models import MailDraft, MailSend
from tests.apply.conftest import AT, add_posting
from tests.apply.test_a13_full_auto import turn_on
from tests.apply.test_a14_email import approve, connect, no_google, provider
from tests.mail.fake_gmail import FakeGoogle

__all__ = ["no_google"]
ON = gate.Switches(enabled=True, paused=False)


def approved(db, person, mode, recipient):
    posting = db.get(Job, person.posting_id)
    posting.url = f"https://example.org/careers/{posting.id}"
    posting.posting_text = f"Send your CV to {recipient}"
    db.commit()
    application = service.start(db, person.user_id, posting.id, mode, False, now=AT).application
    email.prepare(db, application.id, AT)
    db.commit()
    if mode is Mode.review:
        return application, approve(db, application)
    answer = db.get(ApplicationAnswer, (application.id, "email_body"))
    answer.source, answer.value = "document", "Please find my application attached."
    db.commit()
    assert full_auto.decide(db, application.id, AT, ON) == "held"
    db.commit()
    auth = mail_gate.authorise_application(db, application, AT)
    db.commit()
    return application, auth


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize("same_person", [False, True])
@pytest.mark.parametrize(
    ("daily_max", "retry_after", "valid"),
    [(20, timedelta(minutes=3), True), (1, timedelta(days=1), True), (1, timedelta(days=7), False)],
)
def test_overlapping_pending_sends_reserve_at_the_click_and_retry_same_approval(
    db, factory, person_a, person_b, monkeypatch, mode, same_person, daily_max, retry_after, valid
):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake)
    if same_person:
        posting_id, _ = add_posting(db, person_a.user_id, "https://example.org/second")
        second_person = replace(person_a, posting_id=posting_id)
        db.get(Job, posting_id).company = "Example Factory"
        db.commit()
    else:
        second_person = person_b
        connect(db, second_person, fake)
    if mode is Mode.full_auto:
        turn_on(db, person_a)
        if not same_person:
            turn_on(db, second_person)
    first, first_auth = approved(db, person_a, mode, "e2e-first@example.org")
    second, second_auth = approved(db, second_person, mode, "e2e-second@example.org")
    first_id, second_id = first.id, second.id
    hold = full_auto.hold_until(db, second) if mode is Mode.full_auto else None
    authority = db.get(Authorisation, second_auth.apply_id)
    original_authority = (
        authority.id,
        authority.created_at,
        authority.expires_at,
        authority.used_at,
    )
    db.commit()
    current = [AT + timedelta(minutes=15)]
    settings, _, _ = provider(fake, ring)
    settings = settings.model_copy(update={"apply_submit_enabled": True})
    monkeypatch.setattr(caps, "pace_of", lambda host: caps.Pace(timedelta(minutes=3), daily_max))
    interval = timedelta(minutes=3) if daily_max > 1 else timedelta(days=1)
    minted, calls, click_locks = [], [], []
    original_mint, original_event = mail_gate.send_key.mint, gate._event

    def mint(*args):
        minted.append(args)
        return original_mint(*args)

    def event(session, application, kind, at, **data):
        if kind == "click":
            host = caps.host_of(application.ats, application.form_url)
            with factory() as other:
                free = other.scalar(
                    sa.select(
                        sa.func.pg_try_advisory_xact_lock(
                            sa.func.hashtextextended(f"apply_pace:{host}", 0)
                        )
                    )
                )
                click_locks.append(not free)
        return original_event(session, application, kind, at, **data)

    monkeypatch.setattr(mail_gate.send_key, "mint", mint)
    monkeypatch.setattr(gate, "_event", event)

    class Provider:
        async def call(self, account, tool, arguments):
            with factory() as other:
                assert (
                    other.scalar(
                        sa.select(sa.func.count())
                        .select_from(ApplicationEvent)
                        .where(ApplicationEvent.kind == "click")
                    )
                    == len(calls) + 1
                )
            calls.append(arguments)
            return {"result": "sent", "id": f"synthetic-message-{len(calls)}"}

        async def get_message(self, *args, **kwargs):
            return {}

    async def scenario():
        first_pending, second_pending, first_finished = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        async def first_switches():
            first_pending.set()
            await second_pending.wait()
            with factory() as other:
                assert (
                    other.scalar(
                        sa.select(sa.func.count())
                        .select_from(MailSend)
                        .where(MailSend.result == "pending")
                    )
                    == 2
                )
            return ON

        async def second_switches():
            await first_pending.wait()
            second_pending.set()
            await first_finished.wait()
            return ON

        async def send_first():
            try:
                return await mail_gate.send(
                    factory,
                    first_auth,
                    current[0],
                    settings=settings,
                    mcp=Provider(),
                    switches=ON,
                    switches_for=first_switches,
                    clock=lambda: current[0],
                )
            finally:
                first_finished.set()

        first_row, second_row = await asyncio.gather(
            send_first(),
            mail_gate.send(
                factory,
                second_auth,
                current[0],
                settings=settings,
                mcp=Provider(),
                switches=ON,
                switches_for=second_switches,
                clock=lambda: current[0],
            ),
        )
        assert first_row.result == "sent"
        assert (second_row.result, second_row.refusal_code) == ("failed", "paced")
        assert len(calls) == len(minted) == 1
        with factory() as other:
            application = other.get(Application, second_id)
            assert (application.mode, application.state, application.needs) == (
                mode,
                State.held if mode is Mode.full_auto else State.approved,
                "host_pace",
            )
            assert application.mail_send_id is None
            assert other.get(MailDraft, second_auth.draft_id).status == "draft"
            spent = other.get(Authorisation, second_auth.apply_id)
            assert (
                spent.id,
                spent.created_at,
                spent.expires_at,
                spent.used_at,
            ) == original_authority
            assert not full_auto.expired(application, spent, AT + timedelta(days=1, seconds=1))
            assert full_auto.expired(application, spent, AT + timedelta(days=7))
            assert full_auto.hold_until(other, application) == hold
            waiting = other.scalar(
                sa.select(ApplicationEvent).where(
                    ApplicationEvent.application_id == second_id, ApplicationEvent.kind == "waiting"
                )
            )
            assert waiting.data == {
                "needs": "host_pace",
                "until": (current[0] + interval).isoformat(),
            }
            assert caps.next_slot(other, application, current[0]) == current[0] + interval
            assert all(
                other.get(DocumentVersion, record["version_id"]).frozen_at is None
                for record in application.document_versions.values()
            )

        async def available(*args):
            return False

        monkeypatch.setattr(dispatch.admin, "paused", available)
        runtime = SimpleNamespace(
            state=SimpleNamespace(
                sessionmaker=factory,
                clock=lambda: current[0],
                settings=settings,
                mail_mcp=Provider(),
                queue=SimpleNamespace(pool=available),
            )
        )
        current[0] += retry_after
        retry = await dispatch.application(runtime, second_id)
        if not valid:
            assert retry.result in {"refused", "failed"}
            assert retry.refusal_code == "authorisation_expired"
            with factory() as other:
                assert other.get(Application, second_id).state is State.ready
            return
        assert retry.result == "sent"
        assert retry.id != second_row.id
        duplicate = await mail_gate.send(
            factory,
            second_auth,
            current[0],
            settings=settings,
            mcp=Provider(),
            switches=ON,
            clock=lambda: current[0],
        )
        assert duplicate.id == retry.id

    asyncio.run(scenario())
    assert len(calls) == len(minted) == (2 if valid else 1)
    assert click_locks == [True] * (2 if valid else 1)
    db.expire_all()
    assert [db.get(Application, wanted).state for wanted in (first_id, second_id)] == [
        State.submitted,
        State.submitted if valid else State.ready,
    ]
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(Authorisation)
            .where(Authorisation.application_id == second_id)
        )
        == 1
    )


@pytest.mark.parametrize("lock_kind", ["mail", "apply"])
def test_final_clock_is_read_after_lock_wait_before_expiry(
    db, factory, person_a, monkeypatch, lock_kind
):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake)
    application, auth = approved(db, person_a, Mode.review, "e2e-jobs@example.org")
    settings, _, _ = provider(fake, ring)
    current, armed, calls, minted = [AT], [False], [], []
    module = mail_gate.locks if lock_kind == "mail" else gate
    name = "lock_send" if lock_kind == "mail" else "lock"
    original = getattr(module, name)

    def locking(*args, **kwargs):
        result = original(*args, **kwargs)
        if armed[0]:
            current[0] = AT + timedelta(days=8)
        return result

    monkeypatch.setattr(module, name, locking)
    monkeypatch.setattr(mail_gate.send_key, "mint", lambda *args: minted.append(args))

    async def switches():
        armed[0] = True
        return ON

    class Provider:
        async def call(self, *args):
            calls.append(args)
            return {"result": "unknown"}

    result = asyncio.run(
        mail_gate.send(
            factory,
            auth,
            AT,
            settings=settings,
            mcp=Provider(),
            switches=ON,
            switches_for=switches,
            clock=lambda: current[0],
        )
    )
    assert (result.result, result.refusal_code) == ("failed", "authorisation_expired")
    assert calls == minted == []
    db.expire_all()
    assert db.get(Application, application.id).state is State.ready
