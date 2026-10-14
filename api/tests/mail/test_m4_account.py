"""Mail M4 through the api, on the in process fake Google and a real Postgres: connect, the
consent, disconnect and the purge, with two users (`e2e-mail-a`, `e2e-mail-b`)."""

# ruff: noqa: F811, the fixtures of m4_support are imported by name and named again as arguments

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agents.crypto import DecryptError, Keyring
from app.agents.models import Proposal
from app.identity.models import User
from app.jobs.models import Job, JobEvent, JobEventKind
from app.mail import accounts, locks, purge
from app.mail.google import SCOPE_READONLY, SCOPE_SEND, SCOPES
from app.mail.models import (
    TOKEN_KIND,
    AccountStatus,
    ClassificationSource,
    Direction,
    Label,
    MailAccount,
    MailAssistBatch,
    MailClassification,
    MailConfirmedDomain,
    MailMessage,
    MailThread,
)
from tests.identity.api_helpers import Api
from tests.identity.api_helpers import Clock as AppClock
from tests.mail.fake_gmail import FakeGoogle
from tests.mail.m4_support import (  # noqa: F401, fixtures
    ALICE,
    BOB,
    FROZEN,
    MAIL_TABLES,
    READS,
    REDIRECT,
    Lines,
    MemoryVerifiers,
    api,
    app,
    app_clock,
    connect,
    count,
    decide,
    endpoints,
    engine,
    factory,
    keyring,
    lines,
    migrated_url,
    outcome,
    sleeps,
    start,
    user_a,
    user_b,
    verifiers,
)

Signed = tuple[uuid.UUID, str]
Factory = sessionmaker[Session]


def account_of(factory: Factory, user_id: uuid.UUID) -> MailAccount | None:
    with factory() as db:
        return db.scalars(
            sa.select(MailAccount)
            .where(MailAccount.user_id == user_id)
            .options(undefer(MailAccount.refresh_token_encrypted))
        ).one_or_none()


def read(api: Api, token: str) -> dict[str, Any]:
    answer = api.get("/api/mail/account", token=token)
    assert answer.status_code == 200, answer.text
    assert answer.headers["cache-control"] == "no-store"
    found: dict[str, Any] = answer.json()
    return found


# --- connect ---------------------------------------------------------------------------------


def test_start_asks_google_for_exactly_what_the_plan_lists(
    api: Api, user_a: Signed, verifiers: MemoryVerifiers
) -> None:
    address = urlsplit(start(api, user_a[1]))
    asked = {name: values for name, values in parse_qs(address.query).items()}
    assert address.path == "/o/oauth2/v2/auth"
    assert asked["access_type"] == ["offline"]
    assert asked["prompt"] == ["consent"]
    assert asked["response_type"] == ["code"]
    assert asked["code_challenge_method"] == ["S256"]
    assert sorted(asked["scope"][0].split()) == sorted(SCOPES)
    # The redirect address is the setting, whatever the request claims to be.
    assert asked["redirect_uri"] == [REDIRECT]
    forged = api.get("/api/mail/oauth/start", token=user_a[1], headers={"Host": "evil.example"})
    assert parse_qs(urlsplit(forged.headers["location"]).query)["redirect_uri"] == [REDIRECT]
    # The verifier waits under the nonce and is not in the address; its challenge is.
    (verifier,) = list(verifiers.held.values())[:1]
    assert verifier not in address.query
    assert len(asked["code_challenge"][0]) == 43


def test_connect_stores_the_account_and_reads_no_mail(
    api: Api,
    fake: FakeGoogle,
    factory: Factory,
    keyring: Keyring,
    user_a: Signed,
    user_b: Signed,
    lines: Lines,
) -> None:
    user_id, token = user_a
    assert read(api, token)["status"] == "not_connected"
    consent = start(api, token)
    back = decide(fake, consent, ALICE)
    answer = api.get(back, token=token)
    assert outcome(answer) == "connected"

    row = account_of(factory, user_id)
    assert row is not None
    assert (row.email, row.status, row.connected_at) == (ALICE, AccountStatus.ok, app_now(api))
    assert sorted(row.scopes) == sorted(SCOPES)
    assert row.history_id
    assert row.disconnecting_at is None
    assert row.assistant_consent_at is None
    assert row.assistant_consent_version is None
    # Consent first: connecting made no call that reads a mail.
    seen = endpoints(fake)
    assert not set(seen) & set(READS), seen
    assert seen.count("token") == 2
    assert "userinfo" in seen
    assert "getProfile" in seen

    # The token is sealed for this user alone, and appears nowhere it could be read.
    (refresh,) = fake.refresh_tokens
    opened = keyring.decrypt(row.refresh_token_encrypted, row.key_version, user_id, TOKEN_KIND)
    assert opened.plaintext == refresh
    with pytest.raises(DecryptError):
        keyring.decrypt(row.refresh_token_encrypted, row.key_version, user_b[0], TOKEN_KIND)
    assert refresh.encode() not in row.refresh_token_encrypted
    code = parse_qs(urlsplit(back).query)["code"][0]
    state = parse_qs(urlsplit(back).query)["state"][0]
    shown = read(api, token)
    told = f"{shown!r} {answer.headers!r} {answer.text} {row!r} {lines.text}"
    for planted in (refresh, code, state, fake.client_secret, *fake.access_tokens):
        assert planted not in told
    assert shown == {
        "available": True,
        "status": "ok",
        "email": ALICE,
        "can_read": True,
        "can_send": True,
        "connected_at": shown["connected_at"],
        "last_sync_at": None,
        "consent": {"granted": False, "at": None, "version": None, "current_version": 1},
    }
    # B sees nothing of A's connection.
    assert read(api, user_b[1])["status"] == "not_connected"


def app_now(api: Api) -> Any:
    return api.client.app.state.clock()  # type: ignore[attr-defined]


def test_deny_and_errors_of_google_end_on_a_fixed_word(
    api: Api, fake: FakeGoogle, factory: Factory, user_a: Signed
) -> None:
    user_id, token = user_a
    assert connect(api, fake, token, allow=False) == "denied"
    # Another error word, and values nobody asked for: `error`, and nothing echoed.
    back = decide(fake, start(api, token), allow=False).replace("access_denied", "server_error")
    answer = api.get(back + "&next=https://evil.example&gmail=connected", token=token)
    assert outcome(answer) == "error"
    assert "evil" not in answer.headers["location"]
    assert "server_error" not in answer.text
    # No code at all.
    state = parse_qs(urlsplit(start(api, token)).query)["state"][0]
    assert outcome(api.get(f"/api/mail/oauth/callback?state={state}", token=token)) == "error"
    assert outcome(api.get("/api/mail/oauth/callback", token=token)) == "error"
    assert account_of(factory, user_id) is None
    assert "token" not in endpoints(fake)


def test_fewer_scopes_than_asked(
    api: Api, fake: FakeGoogle, factory: Factory, user_a: Signed, user_b: Signed
) -> None:
    # Without gmail.readonly: refused, and the grant is revoked at Google.
    assert connect(api, fake, user_a[1], untick=(SCOPE_READONLY,)) == "scope"
    assert account_of(factory, user_a[0]) is None
    assert endpoints(fake)[-1] == "revoke"
    assert not fake.refresh_tokens
    # Without gmail.send: connected, and the answer says sending is off.
    assert connect(api, fake, user_b[1], BOB, untick=(SCOPE_SEND,)) == "connected"
    shown = read(api, user_b[1])
    assert (shown["can_read"], shown["can_send"]) == (True, False)


def test_a_state_works_once_for_its_user_in_its_session_for_ten_minutes(
    api: Api,
    fake: FakeGoogle,
    factory: Factory,
    app_clock: AppClock,
    user_a: Signed,
    user_b: Signed,
) -> None:
    user_id, token = user_a
    # Signed for another user.
    assert outcome(api.get(decide(fake, start(api, token)), token=user_b[1])) == "error"
    # Used in another session of the same user.
    other_session = api.login_token("e2e-mail-a@example.org")
    assert outcome(api.get(decide(fake, start(api, token)), token=other_session)) == "error"
    # Expired.
    late = decide(fake, start(api, token))
    app_clock.advance(minutes=10, seconds=1)
    assert outcome(api.get(late, token=token)) == "error"
    # Forged: one character of the signature changed.
    good = decide(fake, start(api, token))
    state = parse_qs(urlsplit(good).query)["state"][0]
    forged = good.replace(state, state[:-1] + ("A" if state[-1] != "A" else "B"))
    assert outcome(api.get(forged, token=token)) == "error"
    # No session at all.
    assert api.get(good).status_code == 401
    assert account_of(factory, user_id) is None
    assert "token" not in endpoints(fake)
    # Replayed: the first use connects, the second finds no verifier and calls nothing.
    assert outcome(api.get(good, token=token)) == "connected"
    calls = len(fake.calls)
    assert outcome(api.get(good, token=token)) == "error"
    assert len(fake.calls) == calls


def test_a_code_used_twice_and_a_missing_refresh_token(
    api: Api,
    fake: FakeGoogle,
    factory: Factory,
    app: FastAPI,
    user_a: Signed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, token = user_a
    # The code was already spent at Google (another client used it): the exchange is refused.
    back = decide(fake, start(api, token))
    fake.codes.clear()
    assert outcome(api.get(back, token=token)) == "error"
    assert account_of(factory, user_id) is None

    # Google answers without a refresh token: nothing is stored, the access is revoked.
    google = app.state.mail_google
    exchange = google.exchange_code

    async def without_refresh(**how: Any) -> Any:
        tokens = await exchange(**how)
        return type(tokens)(tokens.access_token, None, tokens.expires_in, tokens.scopes)

    monkeypatch.setattr(google, "exchange_code", without_refresh)
    assert connect(api, fake, token) == "error"
    assert account_of(factory, user_id) is None
    assert endpoints(fake)[-1] == "revoke"


def test_one_google_account_belongs_to_one_user(
    api: Api, fake: FakeGoogle, factory: Factory, user_a: Signed, user_b: Signed
) -> None:
    assert connect(api, fake, user_a[1], ALICE) == "connected"
    assert connect(api, fake, user_b[1], ALICE) == "taken"
    assert account_of(factory, user_b[0]) is None
    # Note N11: B's fresh tokens are dropped without a revocation, which would end A's grant.
    assert "revoke" not in endpoints(fake)
    assert read(api, user_a[1])["status"] == "ok"
    with pytest.raises(IntegrityError), factory() as db, db.begin():
        db.execute(
            sa.text(
                "INSERT INTO mail_accounts (user_id, email, google_sub, refresh_token_encrypted,"
                " key_version, connected_at) SELECT :b, email, google_sub, '\\x00', 1, now()"
                " FROM mail_accounts"
            ),
            {"b": user_b[0]},
        )


def test_two_tabs_and_another_address(
    api: Api, fake: FakeGoogle, factory: Factory, user_a: Signed
) -> None:
    user_id, token = user_a
    first, second = decide(fake, start(api, token)), decide(fake, start(api, token))
    assert outcome(api.get(first, token=token)) == "connected"
    before = account_of(factory, user_id)
    assert outcome(api.get(second, token=token)) == "connected"
    after = account_of(factory, user_id)
    assert before is not None
    assert after is not None
    assert after.refresh_token_encrypted == before.refresh_token_encrypted
    assert count(factory, "mail_accounts") == 1
    assert "revoke" not in endpoints(fake)
    # Connected: the start does not go to Google again, the user disconnects first.
    again = api.get("/api/mail/oauth/start", token=token)
    assert outcome(again) == "error"


def test_reconnect_is_for_the_same_google_account_only(
    api: Api, fake: FakeGoogle, factory: Factory, user_a: Signed
) -> None:
    user_id, token = user_a
    assert connect(api, fake, token, ALICE) == "connected"
    with factory() as db, db.begin():
        db.execute(sa.update(MailAccount).values(status="needs_reconnect", error_code="revoked"))
    old = account_of(factory, user_id)
    assert old is not None
    hinted = start(api, token)
    assert parse_qs(urlsplit(hinted).query)["login_hint"] == [ALICE]
    # Another Google account: refused, its fresh grant revoked, the row untouched.
    assert outcome(api.get(decide(fake, start(api, token), BOB), token=token)) == "error"
    kept = account_of(factory, user_id)
    assert kept is not None
    assert (kept.email, kept.status) == (ALICE, AccountStatus.needs_reconnect)
    assert endpoints(fake)[-1] == "revoke"
    # The same one: a new token, the same connection.
    assert outcome(api.get(decide(fake, hinted, ALICE), token=token)) == "connected"
    new = account_of(factory, user_id)
    assert new is not None
    assert (new.status, new.error_code, new.connected_at) == (
        AccountStatus.ok,
        None,
        old.connected_at,
    )
    assert new.refresh_token_encrypted != old.refresh_token_encrypted


def test_not_set_up_and_redis_down(
    migrated_url: str,
    api: Api,
    app: FastAPI,
    fake: FakeGoogle,
    verifiers: MemoryVerifiers,
    user_a: Signed,
) -> None:
    token = user_a[1]
    verifiers.down = True
    assert outcome(api.get("/api/mail/oauth/start", token=token)) == "error"
    verifiers.down = False
    back = decide(fake, start(api, token))
    verifiers.down = True
    assert outcome(api.get(back, token=token)) == "error"
    app.state.mail_google = None
    assert read(api, token)["available"] is False
    assert outcome(api.get("/api/mail/oauth/start", token=token)) == "error"
    assert outcome(api.get(back, token=token)) == "error"


# --- consent ---------------------------------------------------------------------------------


def test_consent_is_recorded_with_its_version_and_withdrawn(
    api: Api,
    fake: FakeGoogle,
    factory: Factory,
    app_clock: AppClock,
    user_a: Signed,
    user_b: Signed,
) -> None:
    user_id, token = user_a
    body = {"granted": True, "version": accounts.CONSENT_VERSION}
    missing = api.request("PUT", "/api/mail/account/consent", token=token, json=body)
    assert (missing.status_code, missing.json()) == (404, {"detail": "Gmail is not connected."})
    assert connect(api, fake, token) == "connected"
    with factory() as db:
        assert accounts.assistant_allowed(db, user_id) is False
    assert (
        api.request(
            "PUT", "/api/mail/account/consent", token=token, json=body, csrf=False
        ).status_code
        == 403
    )
    stale = api.request(
        "PUT", "/api/mail/account/consent", token=token, json={"granted": True, "version": 0}
    )
    assert stale.status_code == 409
    given = api.request("PUT", "/api/mail/account/consent", token=token, json=body)
    assert given.status_code == 200
    assert given.json()["consent"]["granted"] is True
    assert given.json()["consent"]["version"] == accounts.CONSENT_VERSION
    with factory() as db:
        assert accounts.assistant_allowed(db, user_id) is True
        assert accounts.assistant_allowed(db, db.get(User, user_id)) is True  # type: ignore[arg-type]
        assert accounts.assistant_allowed(db, user_b[0]) is False
    # B's write reaches B's own (missing) account, never A's.
    assert (
        api.request("PUT", "/api/mail/account/consent", token=user_b[1], json=body).status_code
        == 404
    )
    # A disconnect that has begun ends the permission at once.
    with factory() as db, db.begin():
        db.execute(sa.update(MailAccount).values(disconnecting_at=app_clock()))
    with factory() as db:
        assert accounts.assistant_allowed(db, user_id) is False
    with factory() as db, db.begin():
        db.execute(sa.update(MailAccount).values(disconnecting_at=None))
    off = api.request(
        "PUT", "/api/mail/account/consent", token=token, json={"granted": False, "version": 1}
    )
    assert off.json()["consent"] == {
        "granted": False,
        "at": None,
        "version": None,
        "current_version": 1,
    }
    with factory() as db:
        assert accounts.assistant_allowed(db, user_id) is False


# --- disconnect ------------------------------------------------------------------------------


def fill(factory: Factory, user_id: uuid.UUID, marker: str) -> uuid.UUID:
    """One row in every mail table of the user, a linked posting, and the proposals and the
    job event a mail left behind. `marker` is planted in the text columns."""

    with factory() as db, db.begin():
        job = Job(
            user_id=user_id, slug=f"acme-{marker[:8].lower()}", company="Acme", title="Engineer"
        )
        db.add(job)
        db.flush()
        thread = MailThread(
            user_id=user_id, gmail_thread_id=f"t-{marker}", posting_id=job.id, subject=marker
        )
        db.add(thread)
        db.flush()
        message = MailMessage(
            user_id=user_id,
            thread_id=thread.id,
            gmail_message_id=f"m-{marker}",
            received_at=FROZEN,
            body_text=marker,
            direction=Direction.inbound,
            before_connection=False,
        )
        db.add(message)
        db.flush()
        link = Proposal(
            user_id=user_id,
            target="job",
            target_id=job.id,
            kind=purge.KIND_LINK,
            payload={"thread_id": str(thread.id), "subject": marker},
        )
        status = Proposal(
            user_id=user_id,
            target="job",
            target_id=job.id,
            kind=purge.KIND_STATUS,
            payload={
                "from": "applied",
                "to": "interview",
                "message_id": str(message.id),
                "quote": marker,
            },
        )
        note = Proposal(
            user_id=user_id,
            target="job",
            target_id=job.id,
            kind="job_note",
            payload={"note": "kept"},
        )
        db.add_all([link, status, note])
        db.flush()
        db.add_all(
            [
                MailClassification(
                    message_id=message.id,
                    user_id=user_id,
                    label=Label.interview_invitation,
                    source=ClassificationSource.rule,
                    confidence=0.9,
                    proposal_id=status.id,
                ),
                MailConfirmedDomain(user_id=user_id, company_key="acme", domain="acme.example"),
                MailAssistBatch(user_id=user_id, estimate_low=1, estimate_high=2, threads_total=3),
                JobEvent(
                    job_id=job.id,
                    user_id=user_id,
                    kind=JobEventKind.status,
                    data={
                        "from": "applied",
                        "to": "interview",
                        "via": "mail",
                        "message_id": str(message.id),
                    },
                ),
                JobEvent(
                    job_id=job.id, user_id=user_id, kind=JobEventKind.note, data={"note": "by hand"}
                ),
            ]
        )
        return job.id


def left(factory: Factory, user_id: uuid.UUID) -> dict[str, int]:
    return {table: count(factory, table, user_id) for table in MAIL_TABLES}


def test_disconnect_revokes_purges_and_keeps_what_the_plan_keeps(
    api: Api, fake: FakeGoogle, factory: Factory, user_a: Signed, user_b: Signed, lines: Lines
) -> None:
    marker_a, marker_b = uuid.uuid4().hex, uuid.uuid4().hex
    assert connect(api, fake, user_a[1], ALICE) == "connected"
    assert connect(api, fake, user_b[1], BOB) == "connected"
    job_a = fill(factory, user_a[0], marker_a)
    fill(factory, user_b[0], marker_b)
    before_b = left(factory, user_b[0])
    assert all(found == 1 for found in before_b.values())

    answer = api.request("DELETE", "/api/mail/account", token=user_a[1], csrf=False)
    assert answer.status_code == 403  # a write: the CSRF header is required
    answer = api.request("DELETE", "/api/mail/account", token=user_a[1])
    assert (answer.status_code, answer.json()) == (200, {"disconnected": True, "revoked": True})
    assert answer.headers["cache-control"] == "no-store"

    # Nothing of the listed data is left for A; B's is untouched.
    assert left(factory, user_a[0]) == dict.fromkeys(MAIL_TABLES, 0)
    assert left(factory, user_b[0]) == before_b
    assert [call["email"] for call in fake.calls_of("revoke")] == [None]
    assert all(held["email"] != ALICE for held in fake.refresh_tokens.values())
    assert read(api, user_a[1])["status"] == "not_connected"
    assert read(api, user_b[1])["status"] == "ok"
    with factory() as db:
        kinds = {
            row.kind: row.payload
            for row in db.scalars(sa.select(Proposal).where(Proposal.user_id == user_a[0]))
        }
        assert kinds == {
            "mail_status": {"from": "applied", "to": "interview", "mail_deleted": True},
            "job_note": {"note": "kept"},
        }
        events = sorted(
            (row.data for row in db.scalars(sa.select(JobEvent).where(JobEvent.job_id == job_a))),
            key=lambda data: "note" in data,
        )
        assert events == [
            {"from": "applied", "to": "interview", "via": "mail", "mail_deleted": True},
            {"note": "by hand"},
        ]
        assert db.get(Job, job_a) is not None
        everything = repr(
            db.execute(
                sa.text("SELECT payload::text FROM proposals WHERE user_id = :u"), {"u": user_a[0]}
            ).all()
        )
        assert marker_a not in everything
        assert count(factory, "proposals", user_b[0]) == 3
    assert marker_a not in lines.text
    assert marker_b not in lines.text

    # Disconnect twice: nothing to do, the same calm answer, no call to Google.
    calls = len(fake.calls)
    again = api.request("DELETE", "/api/mail/account", token=user_a[1])
    assert (again.status_code, again.json()) == (200, {"disconnected": True, "revoked": True})
    assert len(fake.calls) == calls
    # And the purge itself runs twice without a complaint.
    with factory() as db, db.begin():
        locks.lock_both(db, user_a[0])
        assert purge.purge_user(db, user_a[0]) == purge.Purged(rows=0, proposals=0, events=0)
    # The address is free again: B could now connect it, and A can connect again.
    assert connect(api, fake, user_a[1], ALICE) == "connected"


@pytest.mark.parametrize("kind", ["500", "timeout", "400"])
def test_disconnect_when_google_does_not_confirm(
    api: Api, fake: FakeGoogle, factory: Factory, user_a: Signed, sleeps: list[float], kind: str
) -> None:
    user_id, token = user_a
    assert connect(api, fake, token) == "connected"
    fill(factory, user_id, uuid.uuid4().hex)
    fake.fail("revoke", kind, times=4)
    answer = api.request("DELETE", "/api/mail/account", token=token)
    # The local data is gone all the same, and the page is told to say so.
    assert (answer.status_code, answer.json()) == (200, {"disconnected": True, "revoked": False})
    assert left(factory, user_id) == dict.fromkeys(MAIL_TABLES, 0)
    assert fake.refresh_tokens  # Google still holds the grant: the user removes it there


def test_a_disconnect_interrupted_after_the_flag_is_finished_by_the_tick(
    api: Api,
    app: FastAPI,
    fake: FakeGoogle,
    factory: Factory,
    keyring: Keyring,
    app_clock: AppClock,
    user_a: Signed,
    user_b: Signed,
) -> None:
    user_id, token = user_a
    assert connect(api, fake, token, ALICE) == "connected"
    assert connect(api, fake, user_b[1], BOB) == "connected"
    fill(factory, user_id, uuid.uuid4().hex)
    row = account_of(factory, user_id)
    assert row is not None
    # Step 1 alone, as if the process died before the revocation.
    assert accounts._flag(factory, keyring, user_id, app_clock())[0] is True
    assert read(api, token)["status"] == "disconnecting"
    with factory() as db:
        # What a sync and the send gate check under their lock: stop.
        assert accounts.still_connected(db, user_id, row.connected_at) is False
        assert accounts.still_connected(db, user_b[0], row.connected_at) is True
        assert accounts.stale_disconnects(db, app_clock.advance(seconds=59)) == []
    # A reconnect while the flag is set is refused, at the start and at the callback.
    assert outcome(api.get("/api/mail/oauth/start", token=token)) == "error"
    put = api.request(
        "PUT", "/api/mail/account/consent", token=token, json={"granted": True, "version": 1}
    )
    assert put.status_code == 404
    with factory() as db:
        assert accounts.stale_disconnects(db, app_clock.advance(seconds=2)) == [user_id]

    def finish(user: uuid.UUID) -> accounts.Disconnected:
        return asyncio.run(
            accounts.finish_disconnect(
                sessions=factory,
                google=app.state.mail_google,
                keyring=keyring,
                user_id=user,
                now=app_clock(),
            )
        )

    # An account that is not flagged is never touched by the tick.
    assert finish(user_b[0]) == accounts.Disconnected(found=False, revoked=True)
    assert left(factory, user_b[0])["mail_accounts"] == 1
    assert finish(user_id) == accounts.Disconnected(found=True, revoked=True)
    assert left(factory, user_id) == dict.fromkeys(MAIL_TABLES, 0)
    assert all(held["email"] != ALICE for held in fake.refresh_tokens.values())


def test_a_disconnect_that_begins_during_a_callback_wins(
    api: Api,
    app: FastAPI,
    fake: FakeGoogle,
    factory: Factory,
    keyring: Keyring,
    app_clock: AppClock,
    user_a: Signed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The callback of a reconnect is held at its last call to Google (no transaction is open
    there); a Disconnect sets its flag meanwhile. Back under `lock_sync`, the callback reads
    the row again, stores nothing and ends."""
    user_id, token = user_a
    assert connect(api, fake, token) == "connected"
    with factory() as db, db.begin():
        db.execute(sa.update(MailAccount).values(status="needs_reconnect"))
    before = account_of(factory, user_id)
    assert before is not None
    google = app.state.mail_google
    profile = google.get_profile

    async def held(grant: Any) -> Any:
        found = await profile(grant)
        assert accounts._flag(factory, keyring, user_id, app_clock())[0] is True
        return found

    monkeypatch.setattr(google, "get_profile", held)
    assert connect(api, fake, token) == "error"
    after = account_of(factory, user_id)
    assert after is not None
    assert after.disconnecting_at == app_clock()
    assert after.refresh_token_encrypted == before.refresh_token_encrypted
    assert after.status is AccountStatus.needs_reconnect
    monkeypatch.setattr(google, "get_profile", profile)
    assert api.request("DELETE", "/api/mail/account", token=token).status_code == 200
    assert left(factory, user_id) == dict.fromkeys(MAIL_TABLES, 0)


def test_a_lock_wait_that_runs_out_is_409_and_the_next_try_works(
    api: Api,
    fake: FakeGoogle,
    factory: Factory,
    user_a: Signed,
    user_b: Signed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sync holds `lock_sync` for the batch it stores; the gate holds `lock_send` for its
    decision. Disconnect and the consent wait for them, 5 s at most (here 30 ms), then answer
    409 and never a 500. Another user's locks hold nobody up."""
    monkeypatch.setattr(locks, "LOCK_TIMEOUT_MS", 30)
    user_id, token = user_a
    assert connect(api, fake, token) == "connected"
    body = {"granted": True, "version": 1}
    for take in (locks.lock_sync, locks.lock_send):
        with factory() as holder, holder.begin():
            take(holder, user_id)
            take(holder, user_b[0])
            if take is locks.lock_sync:
                busy = api.request("PUT", "/api/mail/account/consent", token=token, json=body)
                assert (busy.status_code, busy.json()) == (409, {"detail": "Busy, try again."})
            busy = api.request("DELETE", "/api/mail/account", token=token)
            assert (busy.status_code, busy.json()) == (409, {"detail": "Busy, try again."})
            assert busy.headers["cache-control"] == "no-store"
            # The batch that holds the lock still sees its connection: nothing was flagged.
            row = holder.get(MailAccount, user_id)
            assert row is not None
            assert row.disconnecting_at is None
    assert (
        api.request("PUT", "/api/mail/account/consent", token=token, json=body).status_code == 200
    )
    assert api.request("DELETE", "/api/mail/account", token=token).json()["revoked"] is True
    assert count(factory, "mail_accounts", user_id) == 0


def test_deleting_the_user_row_takes_every_mail_table_and_a_disabled_account_keeps_its_mail(
    api: Api,
    fake: FakeGoogle,
    factory: Factory,
    app_clock: AppClock,
    user_a: Signed,
    user_b: Signed,
) -> None:
    assert connect(api, fake, user_a[1], ALICE) == "connected"
    assert connect(api, fake, user_b[1], BOB) == "connected"
    job_a = fill(factory, user_a[0], uuid.uuid4().hex)
    job_b = fill(factory, user_b[0], uuid.uuid4().hex)
    with factory() as db, db.begin():
        db.execute(sa.update(User).where(User.id == user_b[0]).values(is_active=False))
    with factory() as db:
        assert accounts.stale_disconnects(db, app_clock.advance(days=30)) == []
    assert all(found == 1 for found in left(factory, user_b[0]).values())

    # A thread cannot be given B's posting, even by a direct write: the composite key refuses.
    with pytest.raises(IntegrityError), factory() as db, db.begin():
        db.execute(
            sa.update(MailThread).where(MailThread.user_id == user_a[0]).values(posting_id=job_b)
        )
    # Deleting a posting empties the link and keeps the thread with its user.
    with factory() as db, db.begin():
        db.execute(sa.delete(Job).where(Job.id == job_a))
    with factory() as db:
        thread = db.scalars(sa.select(MailThread).where(MailThread.user_id == user_a[0])).one()
        assert (thread.posting_id, thread.user_id) == (None, user_a[0])
    with factory() as db, db.begin():
        db.execute(sa.delete(User).where(User.id == user_a[0]))
    assert left(factory, user_a[0]) == dict.fromkeys(MAIL_TABLES, 0)
    assert all(found == 1 for found in left(factory, user_b[0]).values())


def test_key_rotation_covers_mail_accounts(
    api: Api, app: FastAPI, fake: FakeGoogle, factory: Factory, keyring: Keyring, user_a: Signed
) -> None:
    user_id, token = user_a
    assert connect(api, fake, token) == "connected"
    (refresh,) = fake.refresh_tokens
    new = Keyring(b"n" * 32, previous=keyring.current)
    with factory() as db, db.begin():
        assert accounts.rotate_all(db, new) == 1
        assert accounts.rotate_all(db, new) == 0
    row = account_of(factory, user_id)
    assert row is not None
    assert row.key_version == new.version != keyring.version
    with factory() as db:
        grant = accounts.grant_of(db, Keyring(b"n" * 32), user_id)
        assert grant is not None
        assert grant.refresh_token == refresh
        assert refresh not in repr(grant)
        with pytest.raises(DecryptError):
            accounts.grant_of(db, keyring, user_id)
    # A row no key of this server opens: nothing changes.
    with pytest.raises(DecryptError), factory() as db, db.begin():
        accounts.rotate_all(db, Keyring(b"z" * 32))
    after = account_of(factory, user_id)
    assert after is not None
    assert after.refresh_token_encrypted == row.refresh_token_encrypted
