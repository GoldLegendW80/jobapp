"""Agents A4: agent_threads, agent_messages and the enum agent_message_role.

A thread has no SDK session id (tasks/agents/todo.md, Chat continuity): each message starts a
fresh run whose prompt is rebuilt from agent_messages. agent_threads cascades from users and from
jobs (a job's conversation goes with the job); one thread per user and job (partial unique
index, job_id stays nullable for chats that are not about a job). agent_messages cascades from
its thread; run_id is set to NULL if the run is deleted.

Revises 0008 (A3, proposals, PR #23): A4 is stacked on A3.

Written by hand to match app/agents/models.py (the tests run `alembic check` against it).
Every statement is plain DDL so `alembic upgrade head --sql` works without a database.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("user", "assistant")


def _tz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    labels = ", ".join(f"'{role}'" for role in ROLES)
    op.execute(f"CREATE TYPE agent_message_role AS ENUM ({labels})")
    op.create_table(
        "agent_threads",
        sa.Column(
            "id", pg.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_at", _tz(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", _tz(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_threads"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_agent_threads_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_agent_threads_job_id_jobs", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "char_length(title) >= 1 AND char_length(title) <= 200",
            name="ck_agent_threads_title_length",
        ),
    )
    op.create_index(
        "ix_agent_threads_user_id_updated_at",
        "agent_threads",
        ["user_id", sa.text("updated_at DESC")],
    )
    op.create_index(
        "uq_agent_threads_user_id_job_id",
        "agent_threads",
        ["user_id", "job_id"],
        unique=True,
        postgresql_where=sa.text("job_id IS NOT NULL"),
    )

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True),  # bigserial
        sa.Column("thread_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            pg.ENUM(*ROLES, name="agent_message_role", create_type=False),
            nullable=False,
        ),
        sa.Column("content", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("at", _tz(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_messages"),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["agent_threads.id"],
            name="fk_agent_messages_thread_id_agent_threads",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_messages_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(content) = 'object'", name="ck_agent_messages_content_is_object"
        ),
    )
    op.create_index("ix_agent_messages_thread_id_id", "agent_messages", ["thread_id", "id"])
    op.create_index("ix_agent_messages_run_id", "agent_messages", ["run_id"])


def downgrade() -> None:
    op.drop_table("agent_messages")
    op.drop_table("agent_threads")
    op.execute("DROP TYPE agent_message_role")
