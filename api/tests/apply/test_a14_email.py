"""A14 through the real mail gate and MCP transport, with invented postings and Gmail only."""

import asyncio
import base64
import dataclasses
import hashlib
import uuid
from datetime import timedelta
from email import message_from_bytes
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa

from app.agents.crypto import Keyring
from app.apply import email, full_auto, gate, service
from app.apply.models import Application, ApplicationAnswer, ApplicationEvent, Mode, State
from app.config import Settings
from app.documents import freeze as documents
from app.documents.models import DocumentVersion
from app.gmailmcp.server import Service, build_app
from app.jobs.models import Job
from app.mail import dispatch, reconciling
from app.mail import gate as mail_gate
from app.mail.draft_models import MailDraft, MailSend
from app.mail.google import SCOPE_READONLY, SCOPE_SEND, GoogleClient
from app.mail.mcp_client import GmailMcp
from app.mail.models import TOKEN_KIND, AccountStatus, MailAccount, MailThread
from tests.apply.conftest import AT, add_posting
from tests.apply.test_a13_full_auto import turn_on
from tests.mail import canary
from tests.mail.fake_gmail import FakeGoogle
from tests.mail.test_mcp_send import Keys

SECRET = "synthetic-mail-send-key-" * 3


@pytest.fixture(autouse=True)
def no_google():
    restore = canary.block_google()
    yield
    restore()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Send your CV to e2e-jobs@example.org", ["e2e-jobs@example.org"]),
        ("SEND YOUR CV TO e2e-jobs@example.org", ["e2e-jobs@example.org"]),
        ("Send your résumé to e2e-jobs@example.org.", ["e2e-jobs@example.org"]),
        ("Send your application to e2e-jobs@example.org", ["e2e-jobs@example.org"]),
        ("Envoyez votre CV à e2e-jobs@example.org", ["e2e-jobs@example.org"]),
        ("Envoyer votre candidature à e2e-jobs@example.org", ["e2e-jobs@example.org"]),
        ("Apply to e2e-jobs@example.org", ["e2e-jobs@example.org"]),
        ("Postulez à e2e-jobs@example.org", ["e2e-jobs@example.org"]),
        (
            "Send your CV to e2e-jobs@example.org or e2e-team@example.org",
            ["e2e-jobs@example.org", "e2e-team@example.org"],
        ),
        ("Send CV to e2e-jobs@example.org, e2e-jobs@example.org", ["e2e-jobs@example.org"]),
        ("Send CV to <e2e-jobs@example.org>", ["e2e-jobs@example.org"]),
        ("Send CV to e2e-jobs@EXAMPLE.ORG", ["e2e-jobs@example.org"]),
        ("Contact e2e-jobs@example.org", []),
        ("Questions: e2e-jobs@example.org", []),
        ("Email e2e-jobs@example.org", []),
        ("Send a question to e2e-jobs@example.org", []),
        ("Your CV will be reviewed by e2e-jobs@example.org", []),
        ("Send CV using the form\nContact e2e-jobs@example.org", []),
        ("", []),
        ("CV", []),
        ("Send your CV", []),
        ("Send CV to invalid@", []),
        ("Send CV to @example.org", []),
        ("Send CV to a..b@example.org", []),
        ("Send CV to e2e-jobs@localhost", []),
        ("Send CV to e2e-jobs@127.0.0.1", []),
        ("Send CV to e2e-jobs@bad_domain.org", []),
        ("Send CV to e2e-jobs@example..org", []),
        ("Contact support. Apply on the site. e2e-jobs@example.org", []),
        ("Send " + "x" * 150 + " CV to e2e-jobs@example.org", []),
        ("x" * 100_000 + "Send CV to e2e-jobs@example.org", []),
        ("Use the apply button, e2e-jobs@example.org", []),
        ("Reply to our newsletter at e2e-jobs@example.org", []),
        ("Support e2e-help@example.org. Send CV using the form.", []),
        (
            "Questions: e2e-help@example.org. Send CV to e2e-jobs@example.org",
            ["e2e-jobs@example.org"],
        ),
        (
            "Send CV to e2e-jobs@example.org. Questions: e2e-help@example.org",
            ["e2e-jobs@example.org"],
        ),
        ("Send CV using the form. Contact e2e-help@example.org", []),
        (
            "Send CV to e2e-jobs@example.org; support: e2e-help@example.org",
            ["e2e-jobs@example.org"],
        ),
        (
            "Send CV to e2e-jobs@example.org and e2e-team@example.org",
            ["e2e-jobs@example.org", "e2e-team@example.org"],
        ),
        (
            "Send CV to e2e-jobs@example.org / e2e-team@example.org",
            ["e2e-jobs@example.org", "e2e-team@example.org"],
        ),
    ],
)
def test_posting_recipient_instructions(text, expected):
    assert email.recipients(text) == expected


def prepare(db, person, *, text="Send your CV to e2e-jobs@example.org", mode=Mode.review):
    posting = db.get(Job, person.posting_id)
    posting.url = "https://example.org/careers/role"
    posting.posting_text = text
    db.commit()
    application = service.start(db, person.user_id, posting.id, mode, False, now=AT).application
    assert application.ats == "email"
    email.prepare(db, application.id, AT)
    db.commit()
    return application


def connect(db, person, fake, *, send_scope=True):
    ring = Keyring(b"x" * 32)
    mailbox = "e2e-alice@example.org"
    token = fake.grant(mailbox)
    sealed = ring.encrypt(token, person.user_id, TOKEN_KIND)
    account = MailAccount(
        user_id=person.user_id,
        email=mailbox,
        google_sub=uuid.uuid4().hex,
        refresh_token_encrypted=sealed.ciphertext,
        key_version=sealed.key_version,
        scopes=[SCOPE_READONLY, *([SCOPE_SEND] if send_scope else [])],
        connected_at=AT,
        status=AccountStatus.ok,
    )
    db.add(account)
    db.commit()
    return ring


def approve(db, application):
    gate.set_answer(
        db,
        application.user_id,
        application.id,
        "email_body",
        "Please find my application attached.",
        AT,
    )
    auth = gate.authorise_approval(
        db, application.user_id, application.id, gate.current_hash(db, application), AT, 86400
    )
    decision = gate.decide(
        db, application.user_id, auth.id, AT, switches=gate.Switches(enabled=True, paused=False)
    )
    assert decision.passed, decision.code
    db.commit()
    mail_auth = mail_gate.authorise_application(db, application, AT)
    db.commit()
    return mail_auth


def provider(fake, ring, *, now=AT):
    settings = Settings(
        database_url="postgresql+psycopg://unused@invalid/unused",
        app_env="test",
        google_oauth_client_id=fake.client_id,
        google_oauth_client_secret=fake.client_secret,
        google_auth_url="http://fake-google.test/o/oauth2/v2/auth",
        google_token_base_url="http://fake-google.test",
        google_api_base_url="http://fake-google.test",
        gmail_mcp_url="http://gmailmcp.test/mcp",
        mail_send_key=SECRET,
    )
    app = build_app(Service(settings, Keys(), transport=fake.transport(), now=lambda: now))
    mcp = GmailMcp(
        settings,
        GoogleClient(settings, transport=fake.transport()),
        ring,
        transport=httpx.ASGITransport(app=app),
    )
    return settings, app, mcp


def test_approved_application_sends_exact_pdfs_once(db, factory, person_a):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake)
    application = prepare(db, person_a)
    auth = approve(db, application)
    settings, app, mcp = provider(fake, ring)
    expected = {
        kind: documents.pdf(db, person_a.user_id, uuid.UUID(record["version_id"]))
        for kind, record in application.document_versions.items()
    }
    db.commit()

    async def scenario():
        async with app.router.lifespan_context(app):
            row = await mail_gate.send(
                factory,
                auth,
                AT,
                settings=settings,
                mcp=mcp,
                switches=gate.Switches(enabled=True, paused=False),
            )
            assert row.result == "sent", row.refusal_code
            duplicate = await mail_gate.send(
                factory,
                auth,
                AT,
                settings=settings,
                mcp=mcp,
                switches=gate.Switches(enabled=True, paused=False),
            )
            assert duplicate.id == row.id

    asyncio.run(scenario())
    assert len(fake.sent) == 1
    raw = base64.urlsafe_b64decode(fake.sent[0]["raw"])
    message = message_from_bytes(raw)
    assert message["To"] == "e2e-jobs@example.org"
    assert {
        part.get_filename(): part.get_payload(decode=True)
        for part in message.walk()
        if part.get_content_type() == "application/pdf"
    } == {f"{kind}.pdf": data for kind, data in expected.items()}
    db.expire_all()
    assert db.get(Application, application.id).state is State.submitted
    assert db.get(Job, person_a.posting_id).status.value == "applied"


def test_unknown_never_resends_and_reconciles(db, factory, person_a):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake)
    application = prepare(db, person_a)
    auth = approve(db, application)
    fake.switch("send_answer", "error")
    settings, app, mcp = provider(fake, ring)

    async def scenario():
        async with app.router.lifespan_context(app):
            row = await mail_gate.send(
                factory,
                auth,
                AT,
                settings=settings,
                mcp=mcp,
                switches=gate.Switches(enabled=True, paused=False),
            )
            assert row.result == "unknown"
            again = await mail_gate.send(
                factory,
                auth,
                AT + timedelta(minutes=20),
                settings=settings,
                mcp=mcp,
                switches=gate.Switches(enabled=True, paused=False),
            )
            assert again.id == row.id
            assert len(fake.sent) == 1
            assert await reconciling.reconcile(factory, person_a.user_id, mcp, AT) == 1

    asyncio.run(scenario())
    db.expire_all()
    assert db.get(Application, application.id).state is State.submitted


@pytest.mark.parametrize(
    ("send_scope", "code"), [(False, "no_send_scope"), (None, "not_connected")]
)
def test_connection_needed(db, person_a, send_scope, code):
    if send_scope is not None:
        fake = FakeGoogle()
        fake.seed("alice")
        connect(db, person_a, fake, send_scope=send_scope)
    application = prepare(db, person_a)
    assert application.state is State.needs_person
    assert application.needs == code


def test_two_addresses_need_person_choice(db, person_a):
    application = prepare(
        db, person_a, text="Send CV to e2e-jobs@example.org or e2e-team@example.org"
    )
    answer = db.get(ApplicationAnswer, (application.id, "email_to"))
    assert answer.value is None
    assert answer.options == ["e2e-jobs@example.org", "e2e-team@example.org"]
    with pytest.raises(gate.InvalidAnswerError):
        gate.set_answer(
            db, person_a.user_id, application.id, "email_to", "e2e-stranger@example.org", AT
        )


@pytest.mark.parametrize(
    ("recipient", "own"),
    [
        ("e2e-jobs@example.org", True),
        ("e2e-jobs@hr.example.org", True),
        ("e2e-jobs@evil.example.org.test", False),
        ("e2e-jobs@gmail.com", False),
        ("e2e-jobs@other.org", False),
    ],
)
def test_own_company_domain(recipient, own):
    assert email.own_company(Job(url="https://example.org/careers"), recipient) is own


@pytest.mark.parametrize(
    "case",
    [
        "user_click_application",
        "apply_reply",
        "other_owner",
        "pdf_changed",
        "body_changed",
        "paused_after_mime",
    ],
)
def test_send_authority_and_final_guards(db, factory, person_a, person_b, case):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake)
    application = prepare(db, person_a)
    auth = approve(db, application)
    settings, app, mcp = provider(fake, ring)
    expected = "not_authorised"
    if case == "user_click_application":
        auth = dataclasses.replace(auth, kind="user_click", apply_id=None)
    elif case == "apply_reply":
        thread = MailThread(
            id=uuid.uuid4(),
            user_id=person_a.user_id,
            gmail_thread_id="synthetic-thread",
            posting_id=person_a.posting_id,
        )
        db.add(thread)
        db.flush()
        draft = db.get(MailDraft, auth.draft_id)
        draft.kind, draft.application_id, draft.thread_id = "reply", None, thread.id
    elif case == "other_owner":
        auth = dataclasses.replace(auth, user_id=person_b.user_id)
    elif case == "pdf_changed":
        version = db.get(DocumentVersion, person_a.cv_version_id)
        version.pdf = b"%PDF-1.4 changed synthetic bytes"
        version.pdf_sha256 = hashlib.sha256(version.pdf).digest()
        expected = "stale"
    elif case == "body_changed":
        db.get(MailDraft, auth.draft_id).body = "A changed message."
        expected = "draft_changed"
    else:
        expected = "paused"
    db.commit()

    async def switches():
        return gate.Switches(enabled=True, paused=True)

    async def scenario():
        async with app.router.lifespan_context(app):
            if case == "other_owner":
                with pytest.raises(LookupError):
                    await mail_gate.send(
                        factory,
                        auth,
                        AT,
                        settings=settings,
                        mcp=mcp,
                        switches=gate.Switches(enabled=True, paused=False),
                    )
                return
            row = await mail_gate.send(
                factory,
                auth,
                AT,
                settings=settings,
                mcp=mcp,
                switches=gate.Switches(enabled=True, paused=False),
                switches_for=switches if case == "paused_after_mime" else None,
            )
            assert row.result == ("failed" if case == "paused_after_mime" else "refused")
            assert row.refusal_code == expected

    asyncio.run(scenario())
    assert fake.sent == []


def test_audit_is_committed_before_provider_call(db, factory, person_a):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake)
    application = prepare(db, person_a)
    auth = approve(db, application)
    settings, app, mcp = provider(fake, ring)
    inner = fake.transport()

    class Checked(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if request.url.path.endswith("/messages/send"):
                with factory() as other:
                    assert (
                        other.scalar(
                            sa.select(MailSend.result).where(MailSend.draft_id == auth.draft_id)
                        )
                        == "pending"
                    )
                    assert other.get(Application, application.id).state is State.submitting
            return await inner.handle_async_request(request)

    # The actual Gmail request is inspected behind the MCP server, through a second DB connection.
    app = build_app(Service(settings, Keys(), transport=Checked(), now=lambda: AT))
    mcp.transport = httpx.ASGITransport(app=app)

    async def scenario():
        async with app.router.lifespan_context(app):
            row = await mail_gate.send(
                factory,
                auth,
                AT,
                settings=settings,
                mcp=mcp,
                switches=gate.Switches(enabled=True, paused=False),
            )
            assert row.result == "sent"

    asyncio.run(scenario())


def test_worker_refuses_signing_key():
    from app import worker

    with pytest.raises(RuntimeError, match="MAIL_SEND_KEY"):
        worker.checked_queues(
            Settings(
                database_url="postgresql+psycopg://unused@invalid/unused",
                app_env="test",
                mail_send_key=SECRET,
            )
        )


@pytest.mark.parametrize("missing_scope", [False, True])
def test_connecting_or_granting_scope_recovers_same_application(db, person_a, missing_scope):
    fake = FakeGoogle()
    fake.seed("alice")
    if missing_scope:
        connect(db, person_a, fake, send_scope=False)
    application = prepare(db, person_a)
    assert application.state is State.needs_person
    if missing_scope:
        db.get(MailAccount, person_a.user_id).scopes = [SCOPE_READONLY, SCOPE_SEND]
    else:
        connect(db, person_a, fake)
    db.commit()
    assert email.recover(db, application, AT, configured=True)
    db.commit()
    assert application.state is State.ready
    assert approve(db, application).kind == "person_approval"


def test_manual_email_has_explicit_early_refusal_without_browser_or_send(db, person_a):
    posting = db.get(Job, person_a.posting_id)
    posting.url = "mailto:e2e-jobs@example.org"
    db.commit()
    with pytest.raises(service.RefusedError) as refused:
        service.start(db, person_a.user_id, posting.id, Mode.manual, False, now=AT)
    assert refused.value.refusal.code == "email_manual"
    assert db.scalar(sa.select(sa.func.count()).select_from(Application)) == 0


def test_dispatch_excludes_future_holds_and_rotates_blocked_due_items(
    db, factory, person_a, monkeypatch
):
    approved = set()
    for index in range(12):
        posting_id, _ = add_posting(db, person_a.user_id, f"https://example.org/role/{index}")
        row = Application(
            id=uuid.uuid4(),
            user_id=person_a.user_id,
            posting_id=posting_id,
            mode=Mode.review,
            dry_run=False,
            state=State.held if index < 6 else State.approved,
            ats="email",
            form_url="mailto:e2e-jobs@example.org",
            canonical_key=f"email:{posting_id}",
            created_at=AT,
            updated_at=AT,
        )
        db.add(row)
        db.flush()
        if index < 6:
            db.add(
                ApplicationEvent(
                    application_id=row.id,
                    user_id=person_a.user_id,
                    at=AT,
                    kind="decision",
                    data={"outcome": "held", "until": (AT + timedelta(hours=1)).isoformat()},
                )
            )
        else:
            approved.add(row.id)
    db.commit()
    seen = set()

    async def blocked(app, wanted):
        seen.add(wanted)

    monkeypatch.setattr(dispatch, "application", blocked)
    monkeypatch.setattr(dispatch, "client", lambda app: None)
    current = [AT + timedelta(seconds=1)]
    app = SimpleNamespace(
        state=SimpleNamespace(
            sessionmaker=factory,
            clock=lambda: current[0],
            settings=SimpleNamespace(mail_send_key=SECRET),
        )
    )

    async def scenario():
        assert await dispatch.once(app) == 5
        current[0] += timedelta(seconds=15)
        assert await dispatch.once(app) == 5

    asyncio.run(scenario())
    assert seen == approved


def test_unknown_reconciliation_rotates_past_unresolved_first_batch(db, factory, person_a):
    fake = FakeGoogle()
    fake.seed("alice")
    connect(db, person_a, fake)
    headers = {f"<e2e-{i}@example.org>" for i in range(6)}
    for header in headers:
        db.add(
            MailSend(
                id=uuid.uuid4(),
                user_id=person_a.user_id,
                authorisation_kind="user_click",
                authorised_at=AT,
                content_hash="synthetic",
                recipients_count=1,
                new_recipients=True,
                result="unknown",
                message_id_header=header,
                at=AT - timedelta(seconds=1),
                updated_at=AT - timedelta(seconds=1),
            )
        )
    db.commit()
    queries = set()

    class Missing:
        async def list_messages(self, account, *, q, max_results):
            queries.add(q.removeprefix("in:sent rfc822msgid:"))
            return {"messages": []}

    async def scenario():
        await reconciling.reconcile(factory, person_a.user_id, Missing(), AT)
        await reconciling.reconcile(
            factory, person_a.user_id, Missing(), AT + timedelta(seconds=15)
        )

    asyncio.run(scenario())
    assert headers <= queries


@pytest.mark.parametrize("rehearsal", [False, True])
def test_full_auto_holds_then_sends_or_rehearses(db, factory, person_a, rehearsal):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake)
    turn_on(db, person_a, done=0 if rehearsal else 3)
    application = prepare(db, person_a, mode=Mode.full_auto)
    application.dry_run = rehearsal
    # A document-sourced message has no model-written answer to validate.
    answer = db.get(ApplicationAnswer, (application.id, "email_body"))
    answer.source = "document"
    answer.value = "Please find my application attached."
    db.commit()
    assert (
        full_auto.decide(db, application.id, AT, gate.Switches(enabled=True, paused=False))
        == "held"
    )
    db.commit()
    auth = mail_gate.authorise_application(db, application, AT)
    db.commit()
    assert auth.kind == "full_auto_like"
    later = AT + timedelta(minutes=15)
    settings, app, mcp = provider(fake, ring, now=later)

    async def scenario():
        async with app.router.lifespan_context(app):
            if not rehearsal:
                held = await mail_gate.send(
                    factory,
                    auth,
                    AT,
                    settings=settings,
                    mcp=mcp,
                    switches=gate.Switches(enabled=True, paused=False),
                )
                assert held.refusal_code == "holding"
                assert not fake.sent
                db.expire_all()
                assert db.get(Application, application.id).state is State.held
            row = await mail_gate.send(
                factory,
                auth,
                later,
                settings=settings,
                mcp=mcp,
                switches=gate.Switches(enabled=True, paused=False),
            )
            assert row.refusal_code == "dry_run" if rehearsal else row.result == "sent", (
                row.refusal_code
            )

    asyncio.run(scenario())
    db.expire_all()
    assert db.get(Application, application.id).state is (
        State.rehearsed if rehearsal else State.submitted
    )
    assert len(fake.sent) == (0 if rehearsal else 1)
