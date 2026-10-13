"""Ingestion G9: ingestion_runs, ingestion_run_steps, ingestion_items and their three enums.

Amended in place by G10 (review of PR #148, 2026-10-13), before this migration was deployed
anywhere (production stops at 0011): `ingestion_runs.paused_at`, the start of the current pause.

The second half of the DDL of SPEC-ingestion.md (Documents); the first half (documents, images,
consents) is migration 0011, task G1. These three tables hold every column a later task stores
into, so none of G10, G12, G14 and G15 adds a migration: the chain's columns on runs and steps
(`waiting`, `paused_reason`, `paused_at`, `deadline_at`, `cancel_requested_at`, the estimates,
the ceiling and
its history, `agent_run_id`, `attempt`, `token_budget`, `purge_events`) are written by G10 and
G15, the three counters of a step and `ingestion_items` by G9.

Runs cascade from users, steps from their run, items from their step, their user and their
document: the item's foreign key on `(document_id, user_id)` references the document together
with its user, so an item can only be filed under the user of its document, and a deleted
document takes its items with it. A step keeps the id and the hash of its document without a
foreign key (the step row stays when the document goes), and its `agent_run_id` is set to null
should that agent run ever be deleted. `text_sha256` is the hash of the stored text the step was
planned on: a document keeps its id and the hash of its file when its text is replaced, so this
column is what tells that the text under a step is no longer the one it was cut from.
`uq_ingestion_runs_user_id_active` is the rule "at most one active run per user". The CHECK
constraints are backstops (app/ingestion/runs_models.py says what each one is).

Expand only: new types and new tables, which the release before this one never reads or writes
(CONTRIBUTING.md, Migrations). Deleting a user, a document or an agent run in that release
reaches these tables through the foreign keys, with no code involved.

The check constraints are named with `op.f()`: the name written here is the final one, the one
the convention of app/models_base.py gives the model's constraint.

Written by hand to match app/ingestion/runs_models.py (the tests run `alembic check` against
it). Every statement is plain DDL so `alembic upgrade head --sql` works without a database.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATUSES = ("running", "paused", "done", "failed", "cancelled")
STEP_KINDS = ("transcribe", "extract")
STEP_STATUSES = ("planned", "running", "done", "failed", "cancelled", "reused")

SHA256_BYTES = 32
HASH_HEX_CHARS = 64
RUN_DOCUMENTS_MAX = 30
SECTION_MAX_CHARS = 64
KEY_MAX_CHARS = 400


def _tz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _labels(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _uuid() -> pg.UUID:
    return pg.UUID(as_uuid=True)


def _false() -> sa.TextClause:
    return sa.text("false")


def upgrade() -> None:
    op.execute(f"CREATE TYPE ingestion_run_status AS ENUM ({_labels(RUN_STATUSES)})")
    op.execute(f"CREATE TYPE ingestion_step_kind AS ENUM ({_labels(STEP_KINDS)})")
    op.execute(f"CREATE TYPE ingestion_step_status AS ENUM ({_labels(STEP_STATUSES)})")

    op.create_table(
        "ingestion_runs",
        sa.Column("id", _uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("document_ids", pg.ARRAY(_uuid()), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            pg.ENUM(*RUN_STATUSES, name="ingestion_run_status", create_type=False),
            nullable=False,
            server_default="running",
        ),
        sa.Column("partial", sa.Boolean(), nullable=False, server_default=_false()),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.Column("paused_at", _tz(), nullable=True),
        sa.Column("waiting", sa.Text(), nullable=True),
        sa.Column("deadline_at", _tz(), nullable=False),
        sa.Column("cancel_requested_at", _tz(), nullable=True),
        sa.Column("estimate_low", sa.BigInteger(), nullable=False),
        sa.Column("estimate_high", sa.BigInteger(), nullable=False),
        sa.Column("token_ceiling", sa.BigInteger(), nullable=False),
        sa.Column("tokens_spent", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "ceiling_history", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("created_at", _tz(), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", _tz(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_runs"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_ingestion_runs_user_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            f"cardinality(document_ids) >= 1 AND cardinality(document_ids) <= {RUN_DOCUMENTS_MAX}",
            name=op.f("ck_ingestion_runs_document_ids_count"),
        ),
        sa.CheckConstraint(
            "base_revision >= 0", name=op.f("ck_ingestion_runs_base_revision_not_negative")
        ),
        sa.CheckConstraint(
            "estimate_low >= 0 AND estimate_high >= estimate_low "
            "AND token_ceiling >= 0 AND tokens_spent >= 0",
            name=op.f("ck_ingestion_runs_tokens_not_negative"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(ceiling_history) = 'array'",
            name=op.f("ck_ingestion_runs_ceiling_history_is_array"),
        ),
    )
    # At most one active run per user.
    op.create_index(
        "uq_ingestion_runs_user_id_active",
        "ingestion_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('running', 'paused')"),
    )
    op.create_index(
        "ix_ingestion_runs_user_id_created_at",
        "ingestion_runs",
        ["user_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "ingestion_run_steps",
        sa.Column("id", _uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ingestion_run_id", _uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            pg.ENUM(*STEP_KINDS, name="ingestion_step_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("document_id", _uuid(), nullable=False),
        sa.Column("sha256", pg.BYTEA(), nullable=False),
        sa.Column("part", sa.Integer(), nullable=True),
        sa.Column("range_start", sa.Integer(), nullable=True),
        sa.Column("range_end", sa.Integer(), nullable=True),
        sa.Column("image_positions", pg.ARRAY(sa.Integer()), nullable=True),
        sa.Column("identity_hash", sa.Text(), nullable=False),
        sa.Column("text_sha256", pg.BYTEA(), nullable=True),
        sa.Column(
            "status",
            pg.ENUM(*STEP_STATUSES, name="ingestion_step_status", create_type=False),
            nullable=False,
            server_default="planned",
        ),
        sa.Column("agent_run_id", _uuid(), nullable=True),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("token_budget", sa.BigInteger(), nullable=True),
        sa.Column("refused_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("two_digit_year_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("purge_events", sa.Boolean(), nullable=False, server_default=_false()),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_run_steps"),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.id"],
            name="fk_ingestion_run_steps_ingestion_run_id_ingestion_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_ingestion_run_steps_agent_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "ingestion_run_id", "position", name="uq_ingestion_run_steps_ingestion_run_id_position"
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_ingestion_run_steps_position_not_negative")
        ),
        sa.CheckConstraint(
            f"octet_length(sha256) = {SHA256_BYTES}",
            name=op.f("ck_ingestion_run_steps_sha256_length"),
        ),
        sa.CheckConstraint(
            f"char_length(identity_hash) = {HASH_HEX_CHARS}",
            name=op.f("ck_ingestion_run_steps_identity_hash_length"),
        ),
        sa.CheckConstraint(
            f"text_sha256 IS NULL OR octet_length(text_sha256) = {SHA256_BYTES}",
            name=op.f("ck_ingestion_run_steps_text_sha256_length"),
        ),
        sa.CheckConstraint(
            "(kind = 'extract' AND part IS NOT NULL AND part >= 0 "
            "AND (range_start IS NULL) = (range_end IS NULL) "
            "AND (range_start IS NULL OR (range_start >= 0 AND range_end >= range_start))) "
            "OR (kind = 'transcribe' AND image_positions IS NOT NULL "
            "AND cardinality(image_positions) >= 1)",
            name=op.f("ck_ingestion_run_steps_kind_shape"),
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND (token_budget IS NULL OR token_budget >= 0) "
            "AND refused_count >= 0 AND two_digit_year_count >= 0 AND items_count >= 0",
            name=op.f("ck_ingestion_run_steps_counts_not_negative"),
        ),
    )
    op.create_index("ix_ingestion_run_steps_agent_run_id", "ingestion_run_steps", ["agent_run_id"])
    op.create_index("ix_ingestion_run_steps_document_id", "ingestion_run_steps", ["document_id"])

    op.create_table(
        "ingestion_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True),  # bigserial
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("document_id", _uuid(), nullable=False),
        sa.Column("step_id", _uuid(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("value", pg.JSONB(), nullable=True),
        sa.Column("value_hash", sa.Text(), nullable=False),
        sa.Column("citations", pg.JSONB(), nullable=False),
        sa.Column(
            "flags", pg.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_items"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_ingestion_items_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["ingestion_documents.id", "ingestion_documents.user_id"],
            name="fk_ingestion_items_document_id_ingestion_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["ingestion_run_steps.id"],
            name="fk_ingestion_items_step_id_ingestion_run_steps",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "step_id",
            "section",
            "key",
            "value_hash",
            name="uq_ingestion_items_step_id_section_key_value_hash",
        ),
        sa.CheckConstraint(
            f"char_length(section) >= 1 AND char_length(section) <= {SECTION_MAX_CHARS}",
            name=op.f("ck_ingestion_items_section_length"),
        ),
        sa.CheckConstraint(
            f"char_length(key) >= 1 AND char_length(key) <= {KEY_MAX_CHARS}",
            name=op.f("ck_ingestion_items_key_length"),
        ),
        sa.CheckConstraint(
            "action IN ('add', 'replace', 'remove')", name=op.f("ck_ingestion_items_action_known")
        ),
        sa.CheckConstraint(
            "(action = 'remove') = (value IS NULL)", name=op.f("ck_ingestion_items_value_presence")
        ),
        sa.CheckConstraint(
            f"char_length(value_hash) = {HASH_HEX_CHARS}",
            name=op.f("ck_ingestion_items_value_hash_length"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(citations) = 'array'", name=op.f("ck_ingestion_items_citations_is_array")
        ),
    )
    op.create_index("ix_ingestion_items_document_id", "ingestion_items", ["document_id"])
    op.create_index("ix_ingestion_items_user_id", "ingestion_items", ["user_id"])


def downgrade() -> None:
    op.drop_table("ingestion_items")
    op.drop_table("ingestion_run_steps")
    op.drop_table("ingestion_runs")
    op.execute("DROP TYPE ingestion_step_status")
    op.execute("DROP TYPE ingestion_step_kind")
    op.execute("DROP TYPE ingestion_run_status")
