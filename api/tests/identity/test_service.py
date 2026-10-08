"""app.identity.service against a real Postgres."""

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.identity import security, service
from app.identity.models import Invite, InviteKind, LoginAttempt, Role, User
from app.identity.models import Session as UserSession
from app.identity.service import InvalidEmailError, create_invite, lookup_invite, normalize_email

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def make_user(db: Session, email: str = "admin@example.org", role: Role = Role.admin) -> User:
    user = User(
        email=email, name="Admin", password_hash=security.hash_password("x" * 12), role=role
    )
    db.add(user)
    db.flush()
    return user


# --- email normalisation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("harald@example.org", "harald@example.org"),
        ("  Harald@Example.ORG \n", "harald@example.org"),
        ("FOO.Bar+tag@Sub.Example.com", "foo.bar+tag@sub.example.com"),
        ("josé@exämple.org", "josé@exämple.org"),
        ("ÉLODIE@example.fr", "élodie@example.fr"),
    ],
)
def test_normalize_email(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "plainaddress",
        "@example.org",
        "harald@",
        "harald@@example.org",
        "harald@example",
        "harald example@example.org",
        "harald@exa mple.org",
        "harald@example..org",
        "<script>@example.org",
        "harald@example.org\x00",
        "'; DROP TABLE users; --@example.org",
        "a" * 250 + "@example.org",
        "a@" + "b" * 250 + ".org",
        "x" * 10_000,
        None,
        42,
    ],
)
def test_invalid_emails_are_rejected(raw: object) -> None:
    with pytest.raises(InvalidEmailError, match="valid email"):
        normalize_email(raw)  # type: ignore[arg-type]


def test_lower_not_casefold() -> None:
    # casefold would turn ß into ss and merge two different mailboxes.
    assert normalize_email("STRAßE@example.de") == "straße@example.de"


# --- citext ----------------------------------------------------------------------------------


def test_citext_email_uniqueness(db: Session) -> None:
    make_user(db, "Foo@X.com")
    db.commit()
    db.add(User(email="foo@x.com", name="Other", password_hash="h"))
    with pytest.raises(IntegrityError, match="uq_users_email"):
        db.flush()
    db.rollback()


def test_citext_lookup_ignores_case(db: Session) -> None:
    make_user(db, "Foo@X.com")
    db.commit()
    for probe in ("foo@x.com", "FOO@X.COM", "Foo@X.com"):
        found = service.get_user_by_email(db, probe)
        assert found is not None
        assert found.email == "Foo@X.com"  # stored as given
    assert service.get_user_by_email(db, "foo@y.com") is None


def test_user_defaults_and_timestamps(db: Session) -> None:
    user = User(email="m@example.org", name="Ünïcødé 名前 🦄", password_hash="h")
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.role is Role.member
    assert user.is_active is True
    assert user.created_at.tzinfo is not None
    assert user.updated_at.tzinfo is not None
    assert user.last_login_at is None
    assert isinstance(user.id, uuid.UUID)
    assert user.name == "Ünïcødé 名前 🦄"


def test_server_side_uuid_default(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        new_id = conn.execute(
            sa.text(
                "INSERT INTO users (email, name, password_hash) VALUES ('s@x.org', 'S', 'h') "
                "RETURNING id"
            )
        ).scalar_one()
    assert isinstance(new_id, uuid.UUID)


def test_role_enum_rejects_unknown_value(engine: sa.Engine) -> None:
    with engine.begin() as conn, pytest.raises(sa.exc.DataError, match="user_role"):
        conn.execute(
            sa.text(
                "INSERT INTO users (email, name, password_hash, role) "
                "VALUES ('r@x.org', 'R', 'h', 'owner')"
            )
        )


def test_sessions_cascade_and_token_hash_checks(db: Session) -> None:
    user = make_user(db)
    token_hash = security.hash_token(security.new_token())
    db.add(UserSession(user_id=user.id, token_hash=token_hash, expires_at=T0, ip="192.0.2.7"))
    db.commit()
    stored = db.scalars(sa.select(UserSession)).one()
    assert str(stored.ip) == "192.0.2.7"
    db.add(UserSession(user_id=user.id, token_hash=token_hash, expires_at=T0))
    with pytest.raises(IntegrityError, match="uq_sessions_token_hash"):
        db.flush()
    db.rollback()
    db.add(UserSession(user_id=user.id, token_hash=b"short", expires_at=T0))
    with pytest.raises(IntegrityError, match="ck_sessions_token_hash_length"):
        db.flush()
    db.rollback()
    db.delete(db.get(User, user.id))
    db.commit()
    assert db.scalar(sa.select(sa.func.count()).select_from(UserSession)) == 0


def test_login_attempt_row(db: Session) -> None:
    db.add(LoginAttempt(email="Someone@Example.org", ip="2001:db8::1", success=False))
    db.add(LoginAttempt(email="not an email at all", ip=None, success=False))
    db.commit()
    rows = db.scalars(sa.select(LoginAttempt).order_by(LoginAttempt.id)).all()
    assert [r.id for r in rows] == [1, 2]
    assert rows[0].at.tzinfo is not None
    count = db.scalar(
        sa.select(sa.func.count())
        .select_from(LoginAttempt)
        .where(LoginAttempt.email == "someone@example.org")
    )
    assert count == 1


# --- invites ---------------------------------------------------------------------------------


def test_create_invite_returns_token_once_and_stores_only_its_hash(db: Session) -> None:
    issued = create_invite(db, "Friend@Example.org", InviteKind.invite, now=T0)
    db.commit()
    invite = issued.invite
    assert invite.email == "friend@example.org"
    assert invite.kind is InviteKind.invite
    assert invite.role is Role.member
    assert invite.created_by is None
    assert invite.created_at == T0
    assert invite.expires_at == T0 + timedelta(days=7)
    assert invite.token_hash == security.hash_token(issued.token)
    assert issued.path == "/invite/" + issued.token
    raw = db.execute(sa.text("SELECT * FROM invites")).mappings().one()
    assert issued.token.encode() not in b"".join(
        v if isinstance(v, bytes) else str(v).encode() for v in raw.values()
    )


def test_reset_link_defaults_to_one_hour_and_reset_path(db: Session) -> None:
    issued = create_invite(db, "a@example.org", InviteKind.reset, now=T0)
    assert issued.invite.expires_at == T0 + timedelta(hours=1)
    assert issued.path.startswith("/reset/")


def test_created_by_and_role(db: Session) -> None:
    admin = make_user(db)
    issued = create_invite(db, "b@example.org", InviteKind.invite, admin.id, role=Role.admin)
    db.commit()
    assert issued.invite.created_by == admin.id
    assert issued.invite.role is Role.admin
    db.delete(admin)
    db.commit()
    db.refresh(issued.invite)
    assert issued.invite.created_by is None  # ON DELETE SET NULL keeps the link history


@pytest.mark.parametrize("ttl", [timedelta(0), timedelta(seconds=-1)])
def test_non_positive_ttl_is_refused(db: Session, ttl: timedelta) -> None:
    with pytest.raises(ValueError, match="ttl"):
        create_invite(db, "c@example.org", InviteKind.invite, ttl=ttl)


def test_invalid_email_creates_nothing(db: Session) -> None:
    with pytest.raises(InvalidEmailError):
        create_invite(db, "nope", InviteKind.invite)
    assert db.scalar(sa.select(sa.func.count()).select_from(Invite)) == 0


def test_new_invite_revokes_previous_unused_same_kind(db: Session) -> None:
    first = create_invite(db, "d@example.org", InviteKind.invite, now=T0)
    reset = create_invite(db, "d@example.org", InviteKind.reset, now=T0)
    second = create_invite(db, "D@EXAMPLE.ORG", InviteKind.invite, now=T0 + timedelta(minutes=1))
    db.commit()
    later = T0 + timedelta(minutes=2)
    assert lookup_invite(db, first.token, now=later) is None
    assert lookup_invite(db, second.token, now=later) is not None
    assert lookup_invite(db, reset.token, now=later) is not None  # other kind untouched
    db.refresh(first.invite)
    assert first.invite.revoked_at == T0 + timedelta(minutes=1)


def test_new_invite_does_not_touch_used_or_other_emails(db: Session) -> None:
    used = create_invite(db, "e@example.org", InviteKind.invite, now=T0)
    used.invite.used_at = T0 + timedelta(minutes=1)
    other = create_invite(db, "f@example.org", InviteKind.invite, now=T0)
    create_invite(db, "e@example.org", InviteKind.invite, now=T0 + timedelta(minutes=2))
    db.commit()
    db.refresh(used.invite)
    db.refresh(other.invite)
    assert used.invite.revoked_at is None
    assert other.invite.revoked_at is None


def test_expired_unused_invite_is_revoked_too(db: Session) -> None:
    old = create_invite(db, "g@example.org", InviteKind.invite, now=T0)
    create_invite(db, "g@example.org", InviteKind.invite, now=T0 + timedelta(days=30))
    db.commit()
    db.refresh(old.invite)
    assert old.invite.revoked_at == T0 + timedelta(days=30)


def test_active_link_index_forbids_two_active_links(db: Session) -> None:
    create_invite(db, "h@example.org", InviteKind.invite, now=T0)
    db.commit()
    db.add(
        Invite(
            email="H@example.org",
            kind=InviteKind.invite,
            token_hash=security.hash_token(security.new_token()),
            created_at=T0,
            expires_at=T0 + timedelta(days=1),
        )
    )
    with pytest.raises(IntegrityError, match="ix_invites_active_email_kind"):
        db.flush()
    db.rollback()


def test_expiry_check_constraint(db: Session) -> None:
    db.add(
        Invite(
            email="i@example.org",
            kind=InviteKind.invite,
            token_hash=security.hash_token(security.new_token()),
            created_at=T0,
            expires_at=T0,
        )
    )
    with pytest.raises(IntegrityError, match="ck_invites_expires_after_creation"):
        db.flush()
    db.rollback()


def test_concurrent_creates_leave_exactly_one_active_link(
    factory: sessionmaker[Session],
) -> None:
    barrier = threading.Barrier(8)
    tokens: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with factory() as session, session.begin():
                barrier.wait()
                tokens.append(create_invite(session, "j@example.org", InviteKind.invite).token)
        except BaseException as exc:  # collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    with factory() as session:
        active = [t for t in tokens if lookup_invite(session, t) is not None]
        total = session.scalar(sa.select(sa.func.count()).select_from(Invite))
    assert len(active) == 1
    assert total == 8


# --- lookup ----------------------------------------------------------------------------------


def test_lookup_valid(db: Session) -> None:
    issued = create_invite(db, "k@example.org", InviteKind.invite, now=T0)
    db.commit()
    found = lookup_invite(db, issued.token, now=T0 + timedelta(days=1))
    assert found is not None
    assert found.id == issued.invite.id


def test_expiry_boundary(db: Session) -> None:
    issued = create_invite(db, "l@example.org", InviteKind.invite, now=T0)
    db.commit()
    expires = issued.invite.expires_at
    assert lookup_invite(db, issued.token, now=expires - timedelta(microseconds=1)) is not None
    assert lookup_invite(db, issued.token, now=expires) is None  # exactly at expires_at: expired
    assert lookup_invite(db, issued.token, now=expires + timedelta(seconds=1)) is None


def test_lookup_default_now_uses_the_clock(db: Session) -> None:
    fresh = create_invite(db, "m@example.org", InviteKind.invite)
    stale = create_invite(db, "n@example.org", InviteKind.invite, now=T0 - timedelta(days=365))
    db.commit()
    assert lookup_invite(db, fresh.token) is not None
    assert lookup_invite(db, stale.token) is None


def test_lookup_used_and_revoked(db: Session) -> None:
    used = create_invite(db, "o@example.org", InviteKind.invite, now=T0)
    revoked = create_invite(db, "p@example.org", InviteKind.invite, now=T0)
    used.invite.used_at = T0
    revoked.invite.revoked_at = T0
    db.commit()
    assert lookup_invite(db, used.token, now=T0) is None
    assert lookup_invite(db, revoked.token, now=T0) is None


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not base64 at all!!",
        "%%%%",
        "a" * 43,  # well formed but unknown
        "x" * 10_000,
        "' OR '1'='1",
        "\x00" * 43,
        "é" * 43,
    ],
)
def test_lookup_malformed_or_unknown(db: Session, token: str) -> None:
    create_invite(db, "q@example.org", InviteKind.invite, now=T0)
    db.commit()
    assert lookup_invite(db, token, now=T0) is None


def test_lookup_token_with_extra_or_missing_char(db: Session) -> None:
    issued = create_invite(db, "r@example.org", InviteKind.invite, now=T0)
    db.commit()
    assert lookup_invite(db, issued.token + "A", now=T0) is None
    assert lookup_invite(db, issued.token[:-1], now=T0) is None
    assert lookup_invite(db, issued.token.swapcase(), now=T0) is None
