"""Tables of the identity module: users, sessions, invites, login_attempts.

Emails are `citext` so uniqueness and lookups ignore case in the database itself; the service
still stores them normalised (see `service.normalize_email`). Tokens are never stored: only
their SHA-256 (32 bytes, enforced by a check constraint). Every timestamp is timezone-aware.
Migration: alembic/versions/0002_identity.py, which must stay in step with this file
(`alembic check` in the tests compares them).
"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models_base import Base

TOKEN_HASH_BYTES = 32


class Role(enum.StrEnum):
    admin = "admin"
    member = "member"


class InviteKind(enum.StrEnum):
    invite = "invite"
    reset = "reset"


def _enum(cls: type[enum.Enum], name: str) -> sa.Enum:
    # Postgres enum named explicitly; the stored labels are the values ("admin"), not the names.
    return sa.Enum(cls, name=name, values_callable=lambda members: [m.value for m in members])


ROLE_ENUM = _enum(Role, "user_role")
INVITE_KIND_ENUM = _enum(InviteKind, "invite_kind")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        pg.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )


def _now_column() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(pg.CITEXT, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    role: Mapped[Role] = mapped_column(
        ROLE_ENUM, nullable=False, default=Role.member, server_default=Role.member.value
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    created_at: Mapped[datetime] = _now_column()
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Session(Base):
    """A signed-in browser. The cookie holds the token; this row holds only its hash."""

    __tablename__ = "sessions"
    __table_args__ = (
        sa.CheckConstraint(
            f"octet_length(token_hash) = {TOKEN_HASH_BYTES}", name="token_hash_length"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False, unique=True)
    created_at: Mapped[datetime] = _now_column()
    last_seen_at: Mapped[datetime] = _now_column()
    # Indexed so expired sessions can be purged without a full scan.
    expires_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, index=True
    )
    user_agent: Mapped[str | None] = mapped_column(sa.Text)
    ip: Mapped[str | None] = mapped_column(pg.INET)


class Invite(Base):
    """A one-time link: `invite` creates an account, `reset` sets a new password.

    `role` is the role the account gets when an `invite` is accepted: member for links made in
    the admin area, admin for the setup link of `python -m app.identity.cli create-admin`.
    At most one active (unused, unrevoked) link per email and kind: a partial unique index
    enforces what `service.create_invite` does by revoking the previous one.
    """

    __tablename__ = "invites"
    __table_args__ = (
        sa.CheckConstraint(
            f"octet_length(token_hash) = {TOKEN_HASH_BYTES}", name="token_hash_length"
        ),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        sa.Index(
            "ix_invites_active_email_kind",
            "email",
            "kind",
            unique=True,
            postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(pg.CITEXT, nullable=False)
    kind: Mapped[InviteKind] = mapped_column(INVITE_KIND_ENUM, nullable=False)
    role: Mapped[Role] = mapped_column(
        ROLE_ENUM, nullable=False, default=Role.member, server_default=Role.member.value
    )
    token_hash: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False, unique=True)
    # Null for links made from the command line. Kept (set null) if the creator is deleted.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = _now_column()
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class LoginAttempt(Base):
    """One sign-in attempt, for throttling per email and per IP over a sliding window."""

    __tablename__ = "login_attempts"
    __table_args__ = (
        sa.Index("ix_login_attempts_email_at", "email", "at"),
        sa.Index("ix_login_attempts_ip_at", "ip", "at"),
    )

    # bigserial
    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(pg.CITEXT, nullable=False)
    ip: Mapped[str | None] = mapped_column(pg.INET)
    at: Mapped[datetime] = _now_column()
    success: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
