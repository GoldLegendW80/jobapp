"""Draft content is private. The separate send audit contains ids and decisions only."""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.mail.models import _now, _text, _texts, _user_fk, _uuid_pk
from app.models_base import Base


class MailDraft(Base):
    __tablename__ = "mail_drafts"
    __table_args__ = (
        sa.UniqueConstraint("id", "user_id", name="uq_mail_drafts_id_user_id"),
        sa.UniqueConstraint("application_id", name="uq_mail_drafts_application_id"),
        sa.ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["mail_threads.id", "mail_threads.user_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "user_id"],
            ["applications.id", "applications.user_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("kind IN ('reply', 'application')", name="kind_allowed"),
        sa.CheckConstraint(
            "(kind = 'application') = (application_id IS NOT NULL)", name="kind_subject"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'sending', 'send_unknown', 'sent')", name="status_allowed"
        ),
        sa.CheckConstraint("written_by IN ('user', 'agent')", name="writer_allowed"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.Index(
            "uq_mail_drafts_open_thread",
            "thread_id",
            unique=True,
            postgresql_where=sa.text("status <> 'sent'"),
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    thread_id: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    application_id: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    kind: Mapped[str] = _text("reply")
    to_addrs: Mapped[list[str]] = _texts()
    cc_addrs: Mapped[list[str]] = _texts()
    subject: Mapped[str] = _text()
    body: Mapped[str] = _text()
    written_by: Mapped[str] = _text("user")
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="1")
    status: Mapped[str] = _text("draft")
    document_versions: Mapped[dict[str, Any]] = mapped_column(
        pg.JSONB, nullable=False, server_default="{}"
    )
    fact_check: Mapped[dict[str, Any] | None] = mapped_column(pg.JSONB)
    gmail_message_id: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class MailSend(Base):
    __tablename__ = "mail_sends"
    __table_args__ = (
        sa.CheckConstraint(
            "result IN ('refused', 'pending', 'sent', 'failed', 'unknown')", name="result_allowed"
        ),
        sa.CheckConstraint(
            "authorisation_kind IN ('user_click', 'person_approval', 'full_auto_like')",
            name="authorisation_allowed",
        ),
        sa.Index("ix_mail_sends_user_id_at", "user_id", "at"),
        sa.Index(
            "uq_mail_sends_pending_draft",
            "draft_id",
            unique=True,
            postgresql_where=sa.text("result = 'pending'"),
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    # Plain ids: the content can be purged while the audit survives.
    draft_id: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    posting_id: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    company_key: Mapped[str | None] = mapped_column(sa.Text)
    authorisation_kind: Mapped[str] = _text("user_click")
    authorised_at: Mapped[datetime] = _now()
    content_hash: Mapped[str] = _text()
    recipients_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    new_recipients: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default="false")
    result: Mapped[str] = _text("refused")
    refusal_code: Mapped[str | None] = mapped_column(sa.Text)
    message_id_header: Mapped[str | None] = mapped_column(sa.Text)
    gmail_message_id: Mapped[str | None] = mapped_column(sa.Text)
    gmail_thread_id: Mapped[str | None] = mapped_column(sa.Text)
    resolved_by: Mapped[str | None] = mapped_column(sa.Text)
    at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
