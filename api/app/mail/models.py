"""Mail tables of the reading side (mail M4, migration 0013; tasks/mail/todo.md, "Data model").

- `mail_accounts`: one connected Gmail per user. The refresh token is stored sealed, the way
  `model_credentials.secret_encrypted` is (`crypto.Keyring`, associated data of the user id and
  `TOKEN_KIND`), and is loaded only when asked for (`undefer`). `google_sub` is unique: one
  Google account belongs to one app user. `disconnecting_at` is the flag Disconnect sets first
  ("Locks"). The consent of decision 13 is the pair `assistant_consent_at` and
  `assistant_consent_version`, both set or both empty.
- `mail_threads`, `mail_messages`, `mail_classifications`: what a sync stores (M7, M13). A
  message and a classification carry `(thread_id, user_id)` and `(message_id, user_id)` as
  composite keys, and a thread points at its posting through `(posting_id, user_id)`, so no row
  can point at another user's thread or posting, even by a bug. Deleting the posting empties
  `posting_id` only (`ON DELETE SET NULL (posting_id)`, Postgres 15 or later).
- `mail_confirmed_domains`: the sending domains a user confirmed for a company (M11b).
- `mail_assist_batches`: the batch after the first sync, one open per user (M17).

Every table goes with its user: `users` cascades to accounts, threads, confirmed domains and
batches, threads to messages, messages to classifications.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models_base import Base

# The `kind` of the associated data the refresh token is sealed with (crypto.associated_data).
TOKEN_KIND = "gmail_refresh_token"  # noqa: S105, a label, not a credential


class AccountStatus(enum.StrEnum):
    ok = "ok"
    needs_reconnect = "needs_reconnect"
    error = "error"


class LinkSource(enum.StrEnum):
    rule = "rule"
    model = "model"
    manual = "manual"


class Direction(enum.StrEnum):
    inbound = "inbound"
    outbound = "outbound"


class Label(enum.StrEnum):
    application_received = "application_received"
    rejection = "rejection"
    interview_invitation = "interview_invitation"
    assessment = "assessment"
    offer = "offer"
    info_request = "info_request"
    other = "other"


class ClassificationSource(enum.StrEnum):
    rule = "rule"
    model = "model"


class BatchStatus(enum.StrEnum):
    running = "running"
    paused = "paused"
    done = "done"
    cancelled = "cancelled"


def _enum(kind: type[enum.StrEnum], name: str) -> sa.Enum:
    return sa.Enum(kind, name=name, values_callable=lambda members: [m.value for m in members])


ACCOUNT_STATUS_ENUM = _enum(AccountStatus, "mail_account_status")
LINK_SOURCE_ENUM = _enum(LinkSource, "mail_link_source")
DIRECTION_ENUM = _enum(Direction, "mail_message_direction")
LABEL_ENUM = _enum(Label, "mail_label")
CLASSIFICATION_SOURCE_ENUM = _enum(ClassificationSource, "mail_classification_source")
BATCH_STATUS_ENUM = _enum(BatchStatus, "mail_assist_batch_status")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        pg.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )


def _user_fk(primary_key: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=primary_key,
        nullable=False,
    )


def _now() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _later() -> Mapped[datetime | None]:
    return mapped_column(sa.DateTime(timezone=True))


def _flag() -> Mapped[bool]:
    return mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))


def _text(default: str | None = "") -> Mapped[str]:
    return mapped_column(
        sa.Text, nullable=False, server_default=None if default is None else sa.text(f"'{default}'")
    )


def _texts() -> Mapped[list[str]]:
    return mapped_column(pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]"))


class MailAccount(Base):
    __tablename__ = "mail_accounts"
    __table_args__ = (
        sa.UniqueConstraint("google_sub", name="uq_mail_accounts_google_sub"),
        sa.CheckConstraint("email <> '' AND google_sub <> ''", name="identity_not_empty"),
        sa.CheckConstraint(
            "(assistant_consent_at IS NULL) = (assistant_consent_version IS NULL)",
            name="consent_is_whole",
        ),
    )

    user_id: Mapped[uuid.UUID] = _user_fk(primary_key=True)
    email: Mapped[str] = _text(None)
    google_sub: Mapped[str] = _text(None)
    # Sealed, never loaded unless asked for, never in a repr.
    refresh_token_encrypted: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False, deferred=True)
    key_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    scopes: Mapped[list[str]] = _texts()
    history_id: Mapped[str | None] = mapped_column(sa.Text)
    connected_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    initial_sync_done_at: Mapped[datetime | None] = _later()
    last_sync_at: Mapped[datetime | None] = _later()
    sync_cursor: Mapped[dict[str, Any] | None] = mapped_column(pg.JSONB)
    sync_counts: Mapped[dict[str, Any] | None] = mapped_column(pg.JSONB)
    backoff_until: Mapped[datetime | None] = _later()
    status: Mapped[AccountStatus] = mapped_column(
        ACCOUNT_STATUS_ENUM, nullable=False, server_default=AccountStatus.ok.value
    )
    # A code of mail.errors.STORED_CODES, never Google's message.
    error_code: Mapped[str | None] = mapped_column(sa.Text)
    disconnecting_at: Mapped[datetime | None] = _later()
    assistant_consent_at: Mapped[datetime | None] = _later()
    assistant_consent_version: Mapped[int | None] = mapped_column(sa.Integer)

    def __repr__(self) -> str:  # ids only: no address, no token
        return f"MailAccount(user_id={self.user_id})"


class MailThread(Base):
    __tablename__ = "mail_threads"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["posting_id", "user_id"],
            ["jobs.id", "jobs.user_id"],
            name="fk_mail_threads_posting_id_jobs",
            ondelete="SET NULL (posting_id)",
        ),
        sa.UniqueConstraint(
            "user_id", "gmail_thread_id", name="uq_mail_threads_user_id_gmail_thread_id"
        ),
        sa.UniqueConstraint("id", "user_id", name="uq_mail_threads_id_user_id"),
        sa.Index("ix_mail_threads_user_id_posting_id", "user_id", "posting_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    gmail_thread_id: Mapped[str] = _text(None)
    posting_id: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    link_source: Mapped[LinkSource | None] = mapped_column(LINK_SOURCE_ENUM)
    link_confidence: Mapped[float | None] = mapped_column(sa.REAL)
    # A manual link or unlink: no rule and no model changes it afterwards.
    link_locked: Mapped[bool] = _flag()
    subject: Mapped[str] = _text()
    participants: Mapped[list[str]] = _texts()
    last_message_at: Mapped[datetime | None] = _later()
    needs_reply: Mapped[bool] = _flag()
    # Tombstone: the text is gone and the thread is never synced back.
    deleted_at: Mapped[datetime | None] = _later()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class MailMessage(Base):
    __tablename__ = "mail_messages"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["mail_threads.id", "mail_threads.user_id"],
            name="fk_mail_messages_thread_id_mail_threads",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id", "gmail_message_id", name="uq_mail_messages_user_id_gmail_message_id"
        ),
        sa.UniqueConstraint("id", "user_id", name="uq_mail_messages_id_user_id"),
        sa.CheckConstraint("jsonb_typeof(attachments) = 'array'", name="attachments_is_array"),
        sa.Index("ix_mail_messages_thread_id_received_at", "thread_id", "received_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    gmail_message_id: Mapped[str] = _text(None)
    message_id_header: Mapped[str | None] = mapped_column(sa.Text)
    references_header: Mapped[str | None] = mapped_column(sa.Text)
    from_addr: Mapped[str] = _text()
    from_name: Mapped[str | None] = mapped_column(sa.Text)
    reply_to: Mapped[str | None] = mapped_column(sa.Text)
    to_addrs: Mapped[list[str]] = _texts()
    cc_addrs: Mapped[list[str]] = _texts()
    received_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    subject: Mapped[str] = _text()
    snippet: Mapped[str] = _text()
    body_text: Mapped[str] = _text()
    body_html_sanitized: Mapped[str | None] = mapped_column(sa.Text)
    truncated: Mapped[bool] = _flag()
    has_remote_images: Mapped[bool] = _flag()
    # A of the binding rule (M3, M5).
    trusted: Mapped[bool] = _flag()
    from_domain: Mapped[str | None] = mapped_column(sa.Text)
    suspicious: Mapped[bool] = _flag()
    direction: Mapped[Direction] = mapped_column(DIRECTION_ENUM, nullable=False)
    labels: Mapped[list[str]] = _texts()
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    # received_at <= connected_at, set by store.py only (M7).
    before_connection: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    created_at: Mapped[datetime] = _now()


class MailClassification(Base):
    __tablename__ = "mail_classifications"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["message_id", "user_id"],
            ["mail_messages.id", "mail_messages.user_id"],
            name="fk_mail_classifications_message_id_mail_messages",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    label: Mapped[Label] = mapped_column(LABEL_ENUM, nullable=False)
    source: Mapped[ClassificationSource] = mapped_column(CLASSIFICATION_SOURCE_ENUM, nullable=False)
    rule: Mapped[str | None] = mapped_column(sa.Text)
    confidence: Mapped[float] = mapped_column(sa.REAL, nullable=False)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("proposals.id", ondelete="SET NULL")
    )
    applied: Mapped[bool] = _flag()
    undone: Mapped[bool] = _flag()
    created_at: Mapped[datetime] = _now()


class MailConfirmedDomain(Base):
    """The basis `user_confirmed` of the binding rule: this domain writes for this company."""

    __tablename__ = "mail_confirmed_domains"
    __table_args__ = (
        sa.CheckConstraint("company_key <> '' AND domain <> ''", name="key_not_empty"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk(primary_key=True)
    company_key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    domain: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    confirmed_at: Mapped[datetime] = _now()


class MailAssistBatch(Base):
    __tablename__ = "mail_assist_batches"
    __table_args__ = (
        sa.CheckConstraint(
            "estimate_low >= 0 AND estimate_high >= estimate_low AND tokens_spent >= 0 "
            "AND threads_total >= 0 AND threads_done >= 0",
            name="counts_not_negative",
        ),
        # One open batch per user.
        sa.Index(
            "uq_mail_assist_batches_user_id_open",
            "user_id",
            unique=True,
            postgresql_where="status IN ('running', 'paused')",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    status: Mapped[BatchStatus] = mapped_column(
        BATCH_STATUS_ENUM, nullable=False, server_default=BatchStatus.running.value
    )
    estimate_low: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    estimate_high: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    tokens_spent: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    threads_total: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    threads_done: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
