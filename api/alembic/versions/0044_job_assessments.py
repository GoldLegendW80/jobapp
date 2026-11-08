"""Assess this posting (owner, 2026-11-07, "build all" of the career-ops analysis; SPEC-jobs,
"Assess this posting"): the last assessment of each posting, written by the agent's submit tool
on the person's click. Expand only: a new table that nothing older reads or writes.

Revision ID: 0044
Revises: 0038
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0044"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_assessments",
        sa.Column("job_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", pg.JSONB(), nullable=False),
        sa.CheckConstraint("jsonb_typeof(result) = 'object'", name="result_is_object"),
        sa.ForeignKeyConstraint(
            ["job_id", "user_id"],
            ["jobs.id", "jobs.user_id"],
            name="fk_job_assessments_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("job_id"),
    )


def downgrade() -> None:
    op.drop_table("job_assessments")
