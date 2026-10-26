"""Round 2 of #687: one item never stops the dispatch tick, and a send in flight never freezes
the event loop for another path of the same person."""

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace

import sqlalchemy as sa

from app.agents.crypto import Keyring
from app.apply import gate
from app.apply.models import Application, ApplicationEvent, State
from app.jobs.models import Job
from app.mail import dispatch, reconciling
from app.mail import gate as mail_gate
from app.mail.draft_models import MailSend
from app.mail.google import SCOPE_READONLY, SCOPE_SEND
from app.mail.models import TOKEN_KIND, AccountStatus, MailAccount
from tests.apply.conftest import AT
from tests.apply.test_a14_email import SECRET, approve, no_google, prepare, provider
from tests.mail.fake_gmail import FakeGoogle

__all__ = ["no_google"]


def connect(db, person, fake, mailbox):
    ring = Keyring(b"x" * 32)
    sealed = ring.encrypt(fake.grant(mailbox), person.user_id, TOKEN_KIND)
    db.add(
        MailAccount(
            user_id=person.user_id,
            email=mailbox,
            google_sub=uuid.uuid4().hex,
            refresh_token_encrypted=sealed.ciphertext,
            key_version=sealed.key_version,
            scopes=[SCOPE_READONLY, SCOPE_SEND],
            connected_at=AT,
            status=AccountStatus.ok,
        )
    )
    db.commit()
    return ring


class Pool:
    async def get(self, key):
        return None


def test_a_refused_item_stops_visibly_and_the_rest_of_the_batch_runs(
    db, factory, person_a, person_b, monkeypatch
):
    fake = FakeGoogle()
    fake.seed("alice")
    fake.seed("bob")
    connect(db, person_a, fake, "e2e-alice@example.org")
    connect(db, person_b, fake, "e2e-bob@example.org")
    poisoned = prepare(db, person_a)
    approve(db, poisoned)
    healthy = prepare(db, person_b)
    approve(db, healthy)
    # After the Approve, the person edits the job's address: the approved recipient is gone.
    db.get(Job, person_a.posting_id).url = "mailto:e2e-other@example.org"
    db.get(Application, poisoned.id).updated_at = AT - timedelta(minutes=2)
    db.get(Application, healthy.id).updated_at = AT - timedelta(minutes=1)
    unknown = MailSend(
        id=uuid.uuid4(),
        user_id=person_b.user_id,
        authorisation_kind="user_click",
        authorised_at=AT,
        content_hash="synthetic",
        recipients_count=1,
        new_recipients=True,
        result="unknown",
        message_id_header="<e2e-unknown@example.org>",
        at=AT - timedelta(seconds=1),
        updated_at=AT - timedelta(seconds=1),
    )
    db.add(unknown)
    db.commit()
    sent, looked = [], []

    async def fake_send(sessions, auth, now, **rest):
        sent.append(auth.user_id)

    class Sent:
        async def list_messages(self, account, *, q, max_results):
            looked.append(q)
            return {"messages": []}

    monkeypatch.setattr(dispatch.gate, "send", fake_send)
    monkeypatch.setattr(dispatch, "client", lambda app: Sent())
    queue = SimpleNamespace(pool=lambda: asyncio.sleep(0, Pool()))
    app = SimpleNamespace(
        state=SimpleNamespace(
            sessionmaker=factory,
            clock=lambda: AT + timedelta(seconds=1),
            queue=queue,
            settings=SimpleNamespace(
                mail_send_key=SECRET, mail_sends_keep_days=365, apply_submit_enabled=True
            ),
        )
    )

    assert asyncio.run(dispatch.once(app)) == 2
    assert sent == [person_b.user_id]
    assert looked == ["in:sent rfc822msgid:<e2e-unknown@example.org>", "in:sent newer_than:2d"]
    db.expire_all()
    stopped = db.get(Application, poisoned.id)
    assert stopped.state is State.ready
    assert stopped.error_code == "email_recipient"
    assert gate.SENTENCES["email_recipient"] == "Choose an email recipient from the posting."
    assert db.scalar(
        sa.select(ApplicationEvent.id).where(
            ApplicationEvent.application_id == poisoned.id,
            ApplicationEvent.kind == "submit_stopped",
            ApplicationEvent.data["code"].astext == "email_recipient",
        )
    )
    # The next tick no longer picks the stopped item.
    sent.clear()
    db.get(Application, healthy.id).state = State.ready
    db.commit()
    assert asyncio.run(dispatch.once(app)) == 0
    assert sent == []


def test_the_loop_stays_responsive_while_a_send_holds_the_lock(db, factory, person_a):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake, "e2e-alice@example.org")
    application = prepare(db, person_a)
    auth = approve(db, application)
    settings, app, real = provider(fake, ring)
    entered, release = asyncio.Event(), asyncio.Event()

    class Slow:
        """The real provider, but its send waits until the test lets it go."""

        async def call(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return await real.call(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(real, name)

    def waiting_on_a_lock() -> bool:
        with factory() as other:
            return bool(
                other.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
                    )
                )
            )

    async def scenario():
        async with app.router.lifespan_context(app):
            send = asyncio.create_task(
                mail_gate.send(
                    factory,
                    auth,
                    AT,
                    settings=settings,
                    mcp=Slow(),
                    switches=gate.Switches(enabled=True, paused=False),
                )
            )
            await entered.wait()
            reconcile = asyncio.create_task(
                reconciling.reconcile(factory, person_a.user_id, real, AT)
            )
            seen = False
            # The reconcile waits for the send lock. If it waited on the event loop, nothing
            # here would run until its lock_timeout ends it with an error.
            for _ in range(5000):
                if await asyncio.to_thread(waiting_on_a_lock):
                    seen = True
                    break
            assert seen
            assert not reconcile.done()
            release.set()
            row = await send
            assert row.result == "sent", row.refusal_code
            assert await reconcile == 0

    asyncio.run(scenario())
    assert len(fake.sent) == 1
    db.expire_all()
    assert db.get(Application, application.id).state is State.submitted


def test_the_dispatch_loop_opens_nothing_before_its_first_interval(monkeypatch):
    """Starting the api starts the loop, and the loop's first tick waits one interval, so the
    app's startup opens no database connection (tests/test_app.py)."""
    ticks = []

    async def once(app):
        ticks.append(app)
        return 0

    monkeypatch.setattr(dispatch, "once", once)

    async def scenario():
        async with dispatch.lifespan(object()):
            for _ in range(20):
                await asyncio.sleep(0)

    asyncio.run(scenario())
    assert ticks == []
