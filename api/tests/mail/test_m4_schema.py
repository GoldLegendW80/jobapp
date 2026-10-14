"""Mail M4 below the routes: migration 0013 both ways, the two locks, the OAuth state and the
verifier, the list of routers and the registry, and the two fixes of issue #175 in the client."""

# ruff: noqa: F811, the fixtures of m4_support are imported by name and named again as arguments

from __future__ import annotations

import types
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from fastapi import APIRouter
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.jobs.service import is_lock_timeout
from app.mail import locks, oauth, registry, routers
from app.mail.errors import OAuthError
from app.mail.google import SCOPES, GoogleClient
from tests.ingestion.test_migration import add_user, at_base, at_head, columns  # noqa: F401
from tests.mail.conftest import Connected, run
from tests.mail.fake_gmail import FakeGoogle
from tests.mail.m4_support import MAIL_TABLES, engine, factory, migrated_url  # noqa: F401
from tests.test_migrations import HEAD, alembic_config, current_revision

ENUMS = {
    "mail_account_status": ["ok", "needs_reconnect", "error"],
    "mail_assist_batch_status": ["running", "paused", "done", "cancelled"],
    "mail_classification_source": ["rule", "model"],
    "mail_label": [
        "application_received",
        "rejection",
        "interview_invitation",
        "assessment",
        "offer",
        "info_request",
        "other",
    ],
    "mail_link_source": ["rule", "model", "manual"],
    "mail_message_direction": ["inbound", "outbound"],
}
ACCOUNT_COLUMNS = {
    "user_id": ("UUID", False),
    "email": ("TEXT", False),
    "google_sub": ("TEXT", False),
    "refresh_token_encrypted": ("BYTEA", False),
    "key_version": ("INTEGER", False),
    "scopes": ("ARRAY", False),
    "history_id": ("TEXT", True),
    "connected_at": ("TIMESTAMP", False),
    "initial_sync_done_at": ("TIMESTAMP", True),
    "last_sync_at": ("TIMESTAMP", True),
    "sync_cursor": ("JSONB", True),
    "sync_counts": ("JSONB", True),
    "backoff_until": ("TIMESTAMP", True),
    "status": ("ENUM", False),
    "error_code": ("TEXT", True),
    "disconnecting_at": ("TIMESTAMP", True),
    "assistant_consent_at": ("TIMESTAMP", True),
    "assistant_consent_version": ("INTEGER", True),
}


def mail_enums(found: sa.Engine) -> dict[str, list[str]]:
    with found.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = "
                "t.oid WHERE t.typname LIKE 'mail_%' ORDER BY t.typname, e.enumsortorder"
            )
        ).all()
    labels: dict[str, list[str]] = {}
    for name, label in rows:
        labels.setdefault(name, []).append(label)
    return labels


# --- migration 0013 --------------------------------------------------------------------------


def test_0013_up_and_down(at_head: sa.Engine) -> None:
    assert HEAD >= "0013"
    inspector = sa.inspect(at_head)
    assert set(inspector.get_table_names()) >= set(MAIL_TABLES)
    assert columns(at_head, "mail_accounts") == ACCOUNT_COLUMNS
    assert mail_enums(at_head) == ENUMS
    uniques = {u["name"] for u in inspector.get_unique_constraints("jobs")}
    assert "uq_jobs_id_user_id" in uniques
    cascades = {
        (table, tuple(fk["constrained_columns"])): (fk["referred_table"], fk["options"]["ondelete"])
        for table in MAIL_TABLES
        for fk in inspector.get_foreign_keys(table)
    }
    assert cascades == {
        ("mail_accounts", ("user_id",)): ("users", "CASCADE"),
        ("mail_threads", ("user_id",)): ("users", "CASCADE"),
        ("mail_threads", ("posting_id", "user_id")): ("jobs", "SET NULL (posting_id)"),
        ("mail_messages", ("thread_id", "user_id")): ("mail_threads", "CASCADE"),
        ("mail_classifications", ("message_id", "user_id")): ("mail_messages", "CASCADE"),
        ("mail_classifications", ("proposal_id",)): ("proposals", "SET NULL"),
        ("mail_confirmed_domains", ("user_id",)): ("users", "CASCADE"),
        ("mail_assist_batches", ("user_id",)): ("users", "CASCADE"),
    }
    # One open batch per user; a consent is a date and a version, or neither.
    with at_head.begin() as conn:
        user = add_user(conn, "e2e-mail-a@example.org")
        batch = sa.text(
            "INSERT INTO mail_assist_batches (user_id, status, estimate_low, estimate_high,"
            " threads_total) VALUES (:u, :s, 1, 2, 3)"
        )
        conn.execute(batch, {"u": user, "s": "done"})
        conn.execute(batch, {"u": user, "s": "paused"})
    for statement in (
        "INSERT INTO mail_assist_batches (user_id, estimate_low, estimate_high, threads_total)"
        " SELECT id, 1, 2, 3 FROM users",
        "INSERT INTO mail_accounts (user_id, email, google_sub, refresh_token_encrypted,"
        " key_version, connected_at, assistant_consent_at) SELECT id, 'e2e-x@example.org', 's',"
        " '\\x00', 1, now(), now() FROM users",
    ):
        with pytest.raises(IntegrityError), at_head.begin() as conn:
            conn.execute(sa.text(statement))

    command.downgrade(alembic_config(), "0012")
    url = at_head.url.render_as_string(hide_password=False)
    assert current_revision(url) == "0012"
    inspector = sa.inspect(at_head)
    assert not set(inspector.get_table_names()) & set(MAIL_TABLES)
    assert mail_enums(at_head) == {}
    assert "uq_jobs_id_user_id" not in {u["name"] for u in inspector.get_unique_constraints("jobs")}
    command.upgrade(alembic_config(), "head")
    assert current_revision(url) == HEAD


# --- locks -----------------------------------------------------------------------------------


def test_the_two_locks_are_per_user_bounded_and_released_at_the_commit(
    factory: sessionmaker[Session],
) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    assert (locks.sync_key(a), locks.send_key(a)) == (f"mail-sync:{a}", f"mail-send:{a}")
    with factory() as holder, factory() as other:
        locks.lock_sync(holder, a)
        # Another user, and the other lock of the same user, are free.
        locks.lock_sync(other, b)
        locks.lock_send(other, a)
        other.rollback()
        locks.bounded(other, 20)
        with pytest.raises(DBAPIError) as refused:
            other.execute(
                sa.select(
                    sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(locks.sync_key(a), 0))
                )
            )
        assert is_lock_timeout(refused.value)
        other.rollback()
        holder.commit()
        locks.lock_sync(other, a)  # released at the commit
        assert other.execute(sa.text("SHOW lock_timeout")).scalar_one() == "5s"
        other.commit()
        assert other.execute(sa.text("SHOW lock_timeout")).scalar_one() != "5s"


def test_the_order_is_sync_then_send(factory: sessionmaker[Session]) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    with factory() as db:
        locks.lock_send(db, a)
        with pytest.raises(locks.LockOrderError):
            locks.lock_sync(db, a)
        locks.lock_sync(db, b)  # another user's is no part of this order
        db.rollback()
        # A new transaction of the same session starts with nothing held.
        locks.lock_both(db, a)
        locks.lock_sync(db, a)
        held = db.execute(
            sa.text(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
            )
        ).scalar_one()
        assert held == 2
        db.commit()


# --- the state and the verifier --------------------------------------------------------------

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def test_a_state_opens_only_for_its_user_session_and_time() -> None:
    key, user, session = oauth.derive_key(b"s" * 40), uuid.uuid4(), uuid.uuid4()
    nonce = oauth.new_nonce()
    token = oauth.sign_state(key, user_id=user, session_id=session, nonce=nonce, now=NOW)
    how: dict[str, Any] = {"user_id": user, "session_id": session, "now": NOW}
    state = oauth.read_state(key, token, **how)
    assert (state.user_id, state.nonce) == (user, nonce)
    assert state.expires_at == NOW + timedelta(minutes=10)
    assert nonce not in repr(state)
    assert str(session) not in token
    assert session.hex not in token
    payload, signature = token.split(".")
    cases = {
        "": "malformed",
        "x" * 601: "malformed",
        payload: "malformed",
        f"{payload}.{signature}.x": "malformed",
        f"{payload}.!!": "signature",
        f"{payload}.{signature[:-2]}AA": "signature",
        f"{payload[:-2]}AA.{signature}": "signature",
    }
    for text, reason in cases.items():
        with pytest.raises(oauth.StateError) as refused:
            oauth.read_state(key, text, **how)
        assert refused.value.reason == reason
        assert str(refused.value) == reason  # nothing of the value
    for change, reason in (
        ({"now": NOW + timedelta(minutes=10)}, "expired"),
        ({"user_id": uuid.uuid4()}, "user"),
        ({"session_id": uuid.uuid4()}, "session"),
    ):
        with pytest.raises(oauth.StateError) as refused:
            oauth.read_state(key, token, **{**how, **change})
        assert refused.value.reason == reason
    # Signed with another key (another purpose of SECRET_KEY, or another server).
    with pytest.raises(oauth.StateError) as refused:
        oauth.read_state(oauth.derive_key(b"t" * 40), token, **how)
    assert refused.value.reason == "signature"
    assert oauth.derive_key(b"s" * 40) != b"s" * 40
    # RFC 7636, appendix B.
    assert (
        oauth.challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )
    assert 43 <= len(oauth.new_verifier()) <= 128


def test_the_verifier_waits_in_redis_and_is_read_once(redis_url: str) -> None:
    from redis.asyncio import Redis

    async def scenario() -> None:
        redis = Redis.from_url(redis_url)

        async def pool() -> Redis:
            return redis

        store = oauth.RedisVerifiers(pool)
        nonce, verifier = oauth.new_nonce(), oauth.new_verifier()
        try:
            await store.put(nonce, verifier)
            assert 0 < await redis.ttl(oauth.VERIFIER_PREFIX + nonce) <= 600
            assert await store.take(nonce) == verifier
            assert await store.take(nonce) is None
            assert await store.take(oauth.new_nonce()) is None
        finally:
            await redis.aclose()

        async def down() -> Redis:
            raise OSError("no redis")

        with pytest.raises(oauth.StoreUnavailableError):
            await oauth.RedisVerifiers(down).put(nonce, verifier)
        with pytest.raises(oauth.StoreUnavailableError):
            await oauth.RedisVerifiers(down).take(nonce)

    run(scenario())


# --- the lists --------------------------------------------------------------------------------


def test_routers_and_registry_skip_what_is_not_there_and_fail_on_what_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    assert routers.ROUTER_MODULES[0] == "account_router"
    loaded = routers.load()
    assert [route.prefix for route in loaded][:1] == ["/api/mail"]
    assert routers.load(("no_such_router",)) == ()
    assert routers.private("/api/mail/account")
    assert routers.private("/api/mail")
    assert not routers.private("/api/mailbox")
    assert not routers.private("/api/jobs")

    good = types.ModuleType("app.mail.m4_probe_good")
    calls: list[str] = []
    good.register = lambda: calls.append("good")  # type: ignore[attr-defined]
    good.router = APIRouter(prefix="/probe")  # type: ignore[attr-defined]
    good.__spec__ = types.SimpleNamespace(name=good.__name__)  # type: ignore[assignment]
    broken = types.ModuleType("app.mail.m4_probe_broken")
    broken.__spec__ = types.SimpleNamespace(name=broken.__name__)  # type: ignore[assignment]
    monkeypatch.setitem(sys.modules, good.__name__, good)
    monkeypatch.setitem(sys.modules, broken.__name__, broken)
    monkeypatch.setattr(registry, "_loaded", set())
    assert registry.load(("m4_probe_good", "no_such_module")) == ("m4_probe_good",)
    assert registry.load(("m4_probe_good",)) == ("m4_probe_good",)
    assert calls == ["good"]  # once per process
    with pytest.raises(AttributeError):
        registry.load(("m4_probe_broken",))
    assert [route.prefix for route in routers.load(("m4_probe_good",))] == ["/probe"]
    with pytest.raises(AttributeError):
        routers.load(("m4_probe_broken",))
    assert registry.MODULES == (
        "linking",
        "status",
        "jobs_filter",
        "assist",
        "reply_agent",
        "checker_agent",
    )


# --- issue #175: the client ------------------------------------------------------------------


def test_revoke_takes_only_invalid_token_for_done(
    fake: FakeGoogle, google: GoogleClient, account_a: Connected
) -> None:
    token = account_a.grant.refresh_token
    fake.fail("revoke", "400")
    with pytest.raises(OAuthError) as refused:
        run(google.revoke(token))
    assert token not in repr(refused.value) + str(refused.value)
    assert token in fake.refresh_tokens  # the grant lives: it must not be recorded as revoked
    run(google.revoke(token))
    assert token not in fake.refresh_tokens
    run(google.revoke(token))  # Google no longer knows it: done


def test_consent_url_refuses_other_scopes_and_an_empty_state_or_challenge(
    google: GoogleClient,
) -> None:
    how = {"redirect_uri": "http://localhost/api/mail/oauth/callback"}
    assert "scope=" in google.consent_url(state="s", code_challenge="c", **how)
    for scopes in (["https://mail.google.com/"], [*SCOPES, "https://mail.google.com/"], []):
        with pytest.raises(ValueError, match="scope"):
            google.consent_url(state="s", code_challenge="c", scopes=scopes, **how)
    for state, challenge in (("", "c"), ("s", "")):
        with pytest.raises(ValueError, match="required"):
            google.consent_url(state=state, code_challenge=challenge, **how)
