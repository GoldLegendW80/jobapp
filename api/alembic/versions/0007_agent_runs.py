"""Agents A2: agent_runs, agent_events and the enum agent_run_status.

agent_runs cascades from users (a deleted account leaves no run behind); its job_id is set to
NULL when the job is deleted, so the run log survives the job. agent_events cascades from
agent_runs, and (run_id, seq) is unique: seq is the SSE event id a reconnecting browser resumes
after.

Written by hand to match app/agents/models.py (the tests run `alembic check` against it).
Every statement is plain DDL so `alembic upgrade head --sql` works without a database.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("queued", "running", "done", "failed", "cancelled")


def _tz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    labels = ", ".join(f"'{status}'" for status in STATUSES)
    op.execute(f"CREATE TYPE agent_run_status AS ENUM ({labels})")
    op.create_table(
        "agent_runs",
        sa.Column(
            "id", pg.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            pg.ENUM(*STATUSES, name="agent_run_status", create_type=False),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("input", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output", pg.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("cancel_requested_at", _tz(), nullable=True),
        sa.Column("started_at", _tz(), nullable=True),
        sa.Column("finished_at", _tz(), nullable=True),
        sa.Column("created_at", _tz(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_agent_runs_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_agent_runs_job_id_jobs", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "char_length(agent) >= 1 AND char_length(agent) <= 64",
            name="ck_agent_runs_agent_length",
        ),
        sa.CheckConstraint("jsonb_typeof(input) = 'object'", name="ck_agent_runs_input_is_object"),
        sa.CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) "
            "AND (output_tokens IS NULL OR output_tokens >= 0) "
            "AND (cost_usd IS NULL OR cost_usd >= 0)",
            name="ck_agent_runs_usage_not_negative",
        ),
    )
    op.create_index(
        "ix_agent_runs_user_id_created_at",
        "agent_runs",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_agent_runs_status_active",
        "agent_runs",
        ["status"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index("ix_agent_runs_job_id", "agent_runs", ["job_id"])

    op.create_table(
        "agent_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True),  # bigserial
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("at", _tz(), nullable=False, server_default=sa.text("now()")),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("data", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_events"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_events_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("run_id", "seq", name="uq_agent_events_run_id_seq"),
        sa.CheckConstraint("seq >= 1", name="ck_agent_events_seq_positive"),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="ck_agent_events_data_is_object"),
    )


def downgrade() -> None:
    op.drop_table("agent_events")
    op.drop_table("agent_runs")
    op.execute("DROP TYPE agent_run_status")
