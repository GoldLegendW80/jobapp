"""Approval validity is checked after the shared host lock actually stops a send."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.apply import caps, service
from app.apply.models import Application, ApplicationEvent, Authorisation, Mode, State
from app.documents.models import DocumentVersion
from app.mail import gate as mail_gate
from app.mail.draft_models import MailDraft
from tests.apply.conftest import AT
from tests.apply.test_a13_full_auto import turn_on
from tests.apply.test_a14_email import connect, no_google, provider
from tests.apply.test_a14_pacing import ON, approved
from tests.mail.fake_gmail import FakeGoogle

__all__ = ["no_google"]


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("pace_case", ["clear", "new_click", "elapsed"])
def test_host_lock_wait_rechecks_deadline_before_click(
    db, factory, person_a, person_b, monkeypatch, mode, offset, pace_case
):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake)
    if mode is Mode.full_auto:
        turn_on(db, person_a)
    application, auth = approved(db, person_a, mode, "e2e-jobs@example.org")
    application_id = application.id
    authority = db.get(Authorisation, auth.apply_id)
    deadline = authority.expires_at
    original_authority = (authority.created_at, authority.expires_at, authority.used_at)
    other = service.start(
        db, person_b.user_id, person_b.posting_id, Mode.review, False, now=AT
    ).application
    other.ats, other.form_url = application.ats, application.form_url
    other_id = other.id
    host = caps.host_of(application.ats, application.form_url)
    db.commit()
    current, armed, calls, minted, click_locks = (
        [deadline - timedelta(minutes=1)],
        [False],
        [],
        [],
        [],
    )
    locked, entering = threading.Event(), threading.Event()
    sender_pid = []
    settings, _, _ = provider(fake, ring)
    reserve, mint, event = caps.reserve, mail_gate.send_key.mint, mail_gate.apply_gate._event

    def final_reserve(session, item, now, **kwargs):
        if armed[0]:
            sender_pid.append(session.scalar(sa.select(sa.func.pg_backend_pid())))
            entering.set()
        return reserve(session, item, now, **kwargs)

    def mint_key(*args):
        minted.append(args[-1])
        return mint(*args)

    def write_event(session, item, kind, at, **data):
        if kind == "click":
            with factory() as observer:
                click_locks.append(
                    not observer.scalar(
                        sa.select(
                            sa.func.pg_try_advisory_xact_lock(
                                sa.func.hashtextextended(
                                    f"apply_pace:{caps.host_of(item.ats, item.form_url)}", 0
                                )
                            )
                        )
                    )
                )
        return event(session, item, kind, at, **data)

    monkeypatch.setattr(caps, "reserve", final_reserve)
    monkeypatch.setattr(mail_gate.send_key, "mint", mint_key)
    monkeypatch.setattr(mail_gate.apply_gate, "_event", write_event)

    def lock_owner():
        with factory() as owner:
            owner.execute(
                sa.select(
                    sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(f"apply_pace:{host}", 0))
                )
            )
            locked.set()
            assert entering.wait(timeout=10)
            # Query real Postgres until the sender is observed blocked on this owner.
            # The bounded query count protects a broken barrier without using test sleeps.
            for _ in range(1000):
                blockers = owner.scalar(sa.select(sa.func.pg_blocking_pids(sender_pid[0])))
                if blockers:
                    break
            else:
                pytest.fail("the final host reservation did not wait on the lock owner")
            current[0] = deadline + timedelta(seconds=offset)
            if pace_case != "clear":
                owner.add(
                    ApplicationEvent(
                        application_id=other_id,
                        user_id=person_b.user_id,
                        kind="click",
                        at=current[0]
                        - (caps.pace_of(host).interval if pace_case == "elapsed" else timedelta()),
                        data={},
                    )
                )
            owner.commit()

    class Provider:
        async def call(self, account, tool, arguments):
            calls.append(arguments)
            return {"result": "sent", "id": "synthetic-expiry-message"}

        async def get_message(self, *args, **kwargs):
            return {}

    with ThreadPoolExecutor(max_workers=1) as executor:
        owner_future = []

        async def switches():
            owner_future.append(executor.submit(lock_owner))
            assert locked.wait(timeout=10)
            armed[0] = True
            return ON

        result = asyncio.run(
            mail_gate.send(
                factory,
                auth,
                current[0],
                settings=settings,
                mcp=Provider(),
                switches=ON,
                switches_for=switches,
                clock=lambda: current[0],
            )
        )
        owner_future[0].result(timeout=10)

    expected = (
        "authorisation_expired" if offset >= 0 else "paced" if pace_case == "new_click" else None
    )
    assert (result.result, result.refusal_code) == ("failed" if expected else "sent", expected)
    assert len(calls) == len(minted) == (0 if expected else 1)
    assert click_locks == ([] if expected else [True])
    if not expected:
        assert minted == [current[0]]
    db.expire_all()
    application = db.get(Application, application_id)
    spent = db.get(Authorisation, auth.apply_id)
    assert (spent.created_at, spent.expires_at, spent.used_at) == original_authority
    if expected:
        assert application.mail_send_id is None
        assert db.get(MailDraft, auth.draft_id).status == "draft"
        assert application.state is (
            State.held
            if mode is Mode.full_auto and expected == "paced"
            else State.approved
            if expected == "paced"
            else State.ready
        )
        assert application.mode is (mode if expected == "paced" else Mode.review)
        assert all(
            db.get(DocumentVersion, record["version_id"]).frozen_at is None
            for record in application.document_versions.values()
        )
    else:
        assert application.state is State.submitted
