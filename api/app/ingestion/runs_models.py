"""Tables of the ingestion module, second half (task G9): ingestion_runs, ingestion_run_steps and
ingestion_items, as the DDL of SPEC-ingestion.md (Documents) writes them.

They hold every column a later task stores into, so no later task adds a migration: G9 itself
writes `ingestion_items` and the three counters of a step; G10 writes the run and its steps (the
chain: status, `waiting`, `paused_reason`, `deadline_at`, `cancel_requested_at`, the tokens and
`ceiling_history`, `agent_run_id`, `attempt`, `token_budget`); G15 sets `purge_events`.

What the rows are:
- An ingestion run is a chain of steps for one user. `document_ids` are the documents the user
  selected, `base_revision` the revision of the profile the run compares against. A run that is
  `running` or `paused` is active, and a user has at most one: the partial unique index
  `uq_ingestion_runs_user_id_active` is that rule.
- A step is one agent run over one document part (`kind = extract`: `part`, and
  `range_start`/`range_end`, the character offsets of the part in the document's text, both null
  when the document is read whole) or over a group of images (`kind = transcribe`:
  `image_positions`). `document_id` is no foreign key and `sha256` is copied: the step stays, with
  its status and tokens, when its document is deleted. `identity_hash` is `identity_hash()` below.
- An item is one checked candidate for the profile, stored by the tool of the step that read it:
  the cleaned value of the section model, its hash, and the citations the checker gave
  (`[{start, end, excerpt}]`, offsets in the document's stored text). It is the only table of this
  half that holds words of a document, and it goes with its document: `(document_id, user_id)`
  references the document together with its user and cascades, so an item filed under another
  user than its document's cannot exist, whatever code writes it.

Everything cascades from `users`. A step's `agent_run_id` is set to null if that agent run is
ever deleted: the step row outlives it.

The CHECK constraints are backstops (counts not negative, a range in order, the shapes of the two
kinds of step, hash lengths). The rules themselves live in `tools.py` (G9) and in the engine (G10).
Migration: alembic/versions/0012_ingestion_runs.py, which must stay in step with this file
(`alembic check` in the tests compares them).
"""

import enum
import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.ingestion import plan
from app.models_base import Base

SHA256_BYTES = 32
HASH_HEX_CHARS = 64  # a SHA-256 in hexadecimal: `identity_hash` and `value_hash`
RUN_DOCUMENTS_MAX = 30  # documents of one run (SPEC-ingestion.md, Limits)
SECTION_MAX_CHARS = 64
KEY_MAX_CHARS = 400  # items.KEY_MAX
ACTIONS = ("add", "replace", "remove")  # items.Action


class RunStatus(enum.StrEnum):
    """running and paused are active (one per user); done, failed and cancelled are final."""

    running = "running"
    paused = "paused"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class StepStatus(enum.StrEnum):
    """planned: no agent run yet. running: its agent run is queued or running. reused: an earlier
    run finished the same work (same `identity_hash`), so no model is called."""

    planned = "planned"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"
    reused = "reused"


def _enum(kind: type[enum.StrEnum], name: str) -> sa.Enum:
    return sa.Enum(kind, name=name, values_callable=lambda members: [m.value for m in members])


RUN_STATUS_ENUM = _enum(RunStatus, "ingestion_run_status")
STEP_KIND_ENUM = _enum(plan.StepKind, "ingestion_step_kind")
STEP_STATUS_ENUM = _enum(StepStatus, "ingestion_step_status")


def identity_hash(identity: plan.Identity) -> str:
    """What `ingestion_run_steps.identity_hash` holds: the SHA-256, in hexadecimal, of the
    step's identity (document hash, kind, part or image positions, extractor version, model)."""
    parts = [
        identity.sha256,
        str(identity.kind),
        list(identity.positions),
        identity.extractor_version,
        identity.model,
    ]
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        sa.CheckConstraint(
            f"cardinality(document_ids) >= 1 AND cardinality(document_ids) <= {RUN_DOCUMENTS_MAX}",
            name="document_ids_count",
        ),
        sa.CheckConstraint("base_revision >= 0", name="base_revision_not_negative"),
        sa.CheckConstraint(
            "estimate_low >= 0 AND estimate_high >= estimate_low "
            "AND token_ceiling >= 0 AND tokens_spent >= 0",
            name="tokens_not_negative",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(ceiling_history) = 'array'", name="ceiling_history_is_array"
        ),
        # At most one active run per user: POST /api/ingestion/runs answers 409 on its violation.
        sa.Index(
            "uq_ingestion_runs_user_id_active",
            "user_id",
            unique=True,
            postgresql_where="status IN ('running', 'paused')",
        ),
        sa.Index("ix_ingestion_runs_user_id_created_at", "user_id", sa.text("created_at DESC")),
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
    document_ids: Mapped[list[uuid.UUID]] = mapped_column(
        pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False
    )
    base_revision: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        RUN_STATUS_ENUM, nullable=False, server_default=RunStatus.running.value
    )
    # Done, but not everything was read (the deadline passed, a document did not fit).
    partial: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    paused_reason: Mapped[str | None] = mapped_column(sa.Text)
    # Why a running chain is not moving ("slots"), null when it is.
    waiting: Mapped[str | None] = mapped_column(sa.Text)
    deadline_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    estimate_low: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    estimate_high: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    token_ceiling: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    tokens_spent: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, server_default="0")
    # One `{at, spent, from, to}` per Continue.
    ceiling_history: Mapped[list[dict[str, Any]]] = mapped_column(
        pg.JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class IngestionRunStep(Base):
    __tablename__ = "ingestion_run_steps"
    __table_args__ = (
        sa.UniqueConstraint(
            "ingestion_run_id", "position", name="uq_ingestion_run_steps_ingestion_run_id_position"
        ),
        sa.CheckConstraint("position >= 0", name="position_not_negative"),
        sa.CheckConstraint(f"octet_length(sha256) = {SHA256_BYTES}", name="sha256_length"),
        sa.CheckConstraint(
            f"char_length(identity_hash) = {HASH_HEX_CHARS}", name="identity_hash_length"
        ),
        # An extraction step reads a part, whole (no range) or between two offsets in order; a
        # transcription step reads images.
        sa.CheckConstraint(
            "(kind = 'extract' AND part IS NOT NULL AND part >= 0 "
            "AND (range_start IS NULL) = (range_end IS NULL) "
            "AND (range_start IS NULL OR (range_start >= 0 AND range_end >= range_start))) "
            "OR (kind = 'transcribe' AND image_positions IS NOT NULL "
            "AND cardinality(image_positions) >= 1)",
            name="kind_shape",
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND (token_budget IS NULL OR token_budget >= 0) "
            "AND refused_count >= 0 AND two_digit_year_count >= 0 AND items_count >= 0",
            name="counts_not_negative",
        ),
        sa.Index("ix_ingestion_run_steps_agent_run_id", "agent_run_id"),
        sa.Index("ix_ingestion_run_steps_document_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True),
        sa.ForeignKey("ingestion_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    kind: Mapped[plan.StepKind] = mapped_column(STEP_KIND_ENUM, nullable=False)
    # No foreign key: the step outlives its document. `sha256` is the document's, copied.
    document_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    sha256: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False)
    part: Mapped[int | None] = mapped_column(sa.Integer)
    range_start: Mapped[int | None] = mapped_column(sa.Integer)
    range_end: Mapped[int | None] = mapped_column(sa.Integer)
    image_positions: Mapped[list[int] | None] = mapped_column(pg.ARRAY(sa.Integer))
    identity_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[StepStatus] = mapped_column(
        STEP_STATUS_ENUM, nullable=False, server_default=StepStatus.planned.value
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    attempt: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False, server_default="0")
    # What the step may spend, written when its agent run is created (estimate.budget).
    token_budget: Mapped[int | None] = mapped_column(sa.BigInteger)
    # Refused items that count toward the step's budget of 20 (a `setting` refusal does not).
    refused_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    # Dates refused because the document writes their year in two digits, counted apart.
    two_digit_year_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default="0"
    )
    # Rows of ingestion_items this step stored.
    items_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    # Set when the step's document is deleted while it runs: its events are deleted again once
    # its agent run has ended (G15).
    purge_events: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )


class IngestionItem(Base):
    __tablename__ = "ingestion_items"
    __table_args__ = (
        # The same item proposed twice in a step is stored once.
        sa.UniqueConstraint(
            "step_id",
            "section",
            "key",
            "value_hash",
            name="uq_ingestion_items_step_id_section_key_value_hash",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["ingestion_documents.id", "ingestion_documents.user_id"],
            name="fk_ingestion_items_document_id_ingestion_documents",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"char_length(section) >= 1 AND char_length(section) <= {SECTION_MAX_CHARS}",
            name="section_length",
        ),
        sa.CheckConstraint(
            f"char_length(key) >= 1 AND char_length(key) <= {KEY_MAX_CHARS}", name="key_length"
        ),
        sa.CheckConstraint("action IN ('add', 'replace', 'remove')", name="action_known"),
        # A removal carries no value, and nothing else goes without one.
        sa.CheckConstraint("(action = 'remove') = (value IS NULL)", name="value_presence"),
        sa.CheckConstraint(f"char_length(value_hash) = {HASH_HEX_CHARS}", name="value_hash_length"),
        sa.CheckConstraint("jsonb_typeof(citations) = 'array'", name="citations_is_array"),
        sa.Index("ix_ingestion_items_document_id", "document_id"),
        sa.Index("ix_ingestion_items_user_id", "user_id"),
    )

    # bigserial
    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    step_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True),
        sa.ForeignKey("ingestion_run_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    section: Mapped[str] = mapped_column(sa.Text, nullable=False)
    key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    action: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # The section model's cleaned value; SQL NULL for a removal.
    value: Mapped[Any | None] = mapped_column(pg.JSONB(none_as_null=True))
    value_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # `[{start, end, excerpt}]`: offsets in the document's stored text, and that slice.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(pg.JSONB, nullable=False)
    flags: Mapped[list[str]] = mapped_column(
        pg.ARRAY(sa.Text), nullable=False, default=list, server_default=sa.text("'{}'::text[]")
    )
