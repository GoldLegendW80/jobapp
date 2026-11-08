"""The table of follow ups (migration 0040; SPEC-apply.md, "Follow ups").

`follow_ups`: one row per follow up of a posting. A row without `sent_on` is the posting's open
draft (at most one: `uq_follow_ups_open_posting`), written by the follow up agent on the person's
click or edited by the person; "I sent it" gives it `sent_on` and its `channel`. A row may hold
no text at all: a follow up the person wrote and sent by themselves, recorded with "I sent it".
The text is the person's, never logged; `issues` are the fact check's findings on it (mail M9).
"""

import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models_base import Base

CHANNELS = ("own_mail", "gmail")
WRITERS = ("agent", "person")


class FollowUp(Base):
    __tablename__ = "follow_ups"
    __table_args__ = (
        sa.CheckConstraint(
            "channel IS NULL OR channel IN ('own_mail', 'gmail')", name="channel_allowed"
        ),
        sa.CheckConstraint("written_by IN ('agent', 'person')", name="writer_allowed"),
        sa.CheckConstraint("(sent_on IS NULL) = (channel IS NULL)", name="sent_has_channel"),
        sa.CheckConstraint("jsonb_typeof(issues) = 'array'", name="issues_is_array"),
        sa.ForeignKeyConstraint(
            ["posting_id", "user_id"],
            ["jobs.id", "jobs.user_id"],
            name="fk_follow_ups_posting_id_jobs",
            ondelete="CASCADE",
        ),
        sa.Index("ix_follow_ups_user_id_posting_id", "user_id", "posting_id"),
        sa.Index(
            "uq_follow_ups_open_posting",
            "posting_id",
            unique=True,
            postgresql_where=sa.text("sent_on IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    posting_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    subject: Mapped[str] = mapped_column(sa.Text, nullable=False, default="", server_default="")
    body: Mapped[str] = mapped_column(sa.Text, nullable=False, default="", server_default="")
    issues: Mapped[list[dict[str, Any]]] = mapped_column(
        pg.JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    written_by: Mapped[str] = mapped_column(
        sa.Text, nullable=False, default="agent", server_default="agent"
    )
    # The agent run writing the draft now; None once it ended.
    run_id: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    # Why the last run gave no text (a code of followup_agent.ERRORS).
    error: Mapped[str | None] = mapped_column(sa.Text)
    # The Gmail draft of this text (mail_drafts.id), a plain id as mail_sends keeps its draft.
    mail_draft_id: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    sent_on: Mapped[date | None] = mapped_column(sa.Date)
    channel: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
