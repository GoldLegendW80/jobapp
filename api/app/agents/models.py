"""Tables of the agents module. A1: model_credentials, one row per user at most. A2: agent_runs
and agent_events.

The secret is stored only encrypted (`crypto.py`: AES-GCM, bound to the user id and the kind),
with the version of the key that wrote it and its last four characters for display. It is
deferred, so loading a row for GET never pulls the ciphertext along.
Migrations: alembic/versions/0006_model_credentials.py and 0007_agent_runs.py, which must stay
in step with this file (`alembic check` in the tests compares them).
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.agents.crypto import NONCE_BYTES, TAG_BYTES
from app.models_base import Base


class CredentialKind(enum.StrEnum):
    claude_subscription = "claude_subscription"
    anthropic_api_key = "anthropic_api_key"


CREDENTIAL_KIND_ENUM = sa.Enum(
    CredentialKind,
    name="model_credential_kind",
    values_callable=lambda members: [m.value for m in members],
)


class ModelCredential(Base):
    __tablename__ = "model_credentials"
    __table_args__ = (
        sa.CheckConstraint(
            f"octet_length(secret_encrypted) > {NONCE_BYTES + TAG_BYTES}",
            name="secret_encrypted_length",
        ),
        sa.CheckConstraint("char_length(last4) = 4", name="last4_length"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[CredentialKind] = mapped_column(CREDENTIAL_KIND_ENUM, nullable=False)
    secret_encrypted: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False, deferred=True)
    key_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    last4: Mapped[str] = mapped_column(sa.Text, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


# --- A2: agent runs and their events --------------------------------------------------------


class RunStatus(enum.StrEnum):
    """queued -> running -> done | failed | cancelled; queued -> cancelled | failed."""

    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


FINAL_STATUSES = frozenset({RunStatus.done, RunStatus.failed, RunStatus.cancelled})

RUN_STATUS_ENUM = sa.Enum(
    RunStatus,
    name="agent_run_status",
    values_callable=lambda members: [m.value for m in members],
)

AGENT_NAME_MAX = 64


class AgentRun(Base):
    """One run of one agent for one user (SPEC-agents.md, Data model). `cancel_requested_at` is
    set by POST /api/agents/runs/{id}/cancel on a running run; the worker sees it and stops the
    run. Tokens and cost come from the SDK's final ResultMessage."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        sa.CheckConstraint(
            f"char_length(agent) >= 1 AND char_length(agent) <= {AGENT_NAME_MAX}",
            name="agent_length",
        ),
        sa.CheckConstraint("jsonb_typeof(input) = 'object'", name="input_is_object"),
        sa.CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) "
            "AND (output_tokens IS NULL OR output_tokens >= 0) "
            "AND (cost_usd IS NULL OR cost_usd >= 0)",
            name="usage_not_negative",
        ),
        sa.Index("ix_agent_runs_user_id_created_at", "user_id", sa.text("created_at DESC")),
        sa.Index(
            "ix_agent_runs_status_active",
            "status",
            postgresql_where="status IN ('queued', 'running')",
        ),
        sa.Index("ix_agent_runs_job_id", "job_id"),
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
    agent: Mapped[str] = mapped_column(sa.Text, nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="SET NULL")
    )
    status: Mapped[RunStatus] = mapped_column(
        RUN_STATUS_ENUM, nullable=False, server_default=RunStatus.queued.value
    )
    input: Mapped[dict[str, Any]] = mapped_column(
        pg.JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    output: Mapped[dict[str, Any] | None] = mapped_column(pg.JSONB)
    error: Mapped[str | None] = mapped_column(sa.Text)
    model: Mapped[str | None] = mapped_column(sa.Text)
    input_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    output_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 6))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class AgentEvent(Base):
    """One step of a run, kept for the live view and its replay: `seq` counts from 1 within the
    run, without gaps, and is the SSE event id (Last-Event-ID resumes after it)."""

    __tablename__ = "agent_events"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "seq", name="uq_agent_events_run_id_seq"),
        sa.CheckConstraint("seq >= 1", name="seq_positive"),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="data_is_object"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(
        pg.JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
