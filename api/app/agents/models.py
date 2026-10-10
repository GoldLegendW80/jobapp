"""Tables of the agents module. A1: model_credentials, one row per user at most. A2: agent_runs
and agent_events. A3: proposals. A4: agent_threads and agent_messages.

The secret is stored only encrypted (`crypto.py`: AES-GCM, bound to the user id and the kind),
with the version of the key that wrote it and its last four characters for display. It is
deferred, so loading a row for GET never pulls the ciphertext along.
Migrations: alembic/versions/0006_model_credentials.py, 0007_agent_runs.py,
0008_proposals.py and 0009_agent_threads.py, which must stay in step with this file
(`alembic check` in the tests compares them).
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


# --- A3: proposals ------------------------------------------------------------------------------


class ProposalStatus(enum.StrEnum):
    """pending -> accepted | rejected (by the user), superseded (a newer proposal of the same
    kind for the same target replaced it), invalid (its target was gone when the user accepted
    it); accepted -> undone (mail's undo, SPEC-mail). Mail's automatic changes are created
    accepted by the system."""

    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    superseded = "superseded"
    invalid = "invalid"
    undone = "undone"


class ProposalDecider(enum.StrEnum):
    user = "user"
    system = "system"


PROPOSAL_STATUS_ENUM = sa.Enum(
    ProposalStatus,
    name="proposal_status",
    values_callable=lambda members: [m.value for m in members],
)
PROPOSAL_DECIDER_ENUM = sa.Enum(
    ProposalDecider,
    name="proposal_decider",
    values_callable=lambda members: [m.value for m in members],
)

PROPOSAL_TARGET_MAX = 32
PROPOSAL_KIND_MAX = 64


class Proposal(Base):
    """A change an agent (or a rule) suggests to the user's data, applied only when accepted
    (SPEC-agents.md, Runtime and Data model). `target` names what it changes ("job"),
    `target_id` which row (null when the change creates one), `kind` the change (`job_note`,
    `job_status`), `payload` its content. `run_id` is the run that proposed it: null for a
    rule-based system proposal (SPEC-mail) and once the run is gone. `decided_by` and
    `decided_at` are null exactly while it is pending; `edited_value` is what the user applied
    instead of the payload's value; `reason` says why the system set it superseded or invalid."""

    __tablename__ = "proposals"
    __table_args__ = (
        sa.CheckConstraint(
            f"char_length(target) >= 1 AND char_length(target) <= {PROPOSAL_TARGET_MAX}",
            name="target_length",
        ),
        sa.CheckConstraint(
            f"char_length(kind) >= 1 AND char_length(kind) <= {PROPOSAL_KIND_MAX}",
            name="kind_length",
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_is_object"),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL) "
            "AND (status = 'pending') = (decided_by IS NULL)",
            name="decided_unless_pending",
        ),
        sa.Index("ix_proposals_user_id_created_at", "user_id", sa.text("created_at DESC")),
        sa.Index("ix_proposals_user_id_target_target_id", "user_id", "target", "target_id"),
        sa.Index("ix_proposals_run_id", "run_id"),
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
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    target: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        pg.JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    status: Mapped[ProposalStatus] = mapped_column(
        PROPOSAL_STATUS_ENUM, nullable=False, server_default=ProposalStatus.pending.value
    )
    decided_by: Mapped[ProposalDecider | None] = mapped_column(PROPOSAL_DECIDER_ENUM)
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    edited_value: Mapped[Any | None] = mapped_column(pg.JSONB)
    reason: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


# --- A4: chat threads and their messages ----------------------------------------------------------


class MessageRole(enum.StrEnum):
    user = "user"
    assistant = "assistant"


MESSAGE_ROLE_ENUM = sa.Enum(
    MessageRole,
    name="agent_message_role",
    values_callable=lambda members: [m.value for m in members],
)

THREAD_TITLE_MAX = 200


class AgentThread(Base):
    """One conversation of a user with an agent, about one job (SPEC-agents.md, Data model). There
    is no SDK session id: every message starts a fresh run whose prompt is rebuilt from
    agent_messages (tasks/agents/todo.md, Chat continuity). One thread per job and user (partial
    unique index); job_id stays nullable for later chats that are not about a job. Deleting the
    job or the user deletes its threads."""

    __tablename__ = "agent_threads"
    __table_args__ = (
        sa.CheckConstraint(
            f"char_length(title) >= 1 AND char_length(title) <= {THREAD_TITLE_MAX}",
            name="title_length",
        ),
        sa.Index("ix_agent_threads_user_id_updated_at", "user_id", sa.text("updated_at DESC")),
        sa.Index(
            "uq_agent_threads_user_id_job_id",
            "user_id",
            "job_id",
            unique=True,
            postgresql_where=sa.text("job_id IS NOT NULL"),
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
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class AgentMessage(Base):
    """One message of a thread: the user's text, or the agent's final answer. `content` is
    {"text": ...}. `run_id` is the run the user message started, or the run that wrote the answer;
    set to NULL if that run is ever deleted. Ordered by id (bigserial)."""

    __tablename__ = "agent_messages"
    __table_args__ = (
        sa.CheckConstraint("jsonb_typeof(content) = 'object'", name="content_is_object"),
        sa.Index("ix_agent_messages_thread_id_id", "thread_id", "id"),
        sa.Index("ix_agent_messages_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True),
        sa.ForeignKey("agent_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[MessageRole] = mapped_column(MESSAGE_ROLE_ENUM, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(
        pg.JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
