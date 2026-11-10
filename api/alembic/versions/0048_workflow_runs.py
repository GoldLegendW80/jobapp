"""Activity as workflow runs (agents A10): each agent run records its workflow, the workflow run
it belongs to and how that run was triggered, set by its starter. Expand only: three nullable
columns and an index; older runs keep nulls and are grouped from what their input holds.

Revision ID: 0048
Revises: 0047
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("workflow", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("workflow_run_id", pg.UUID(as_uuid=True), nullable=True))
    op.add_column("agent_runs", sa.Column("workflow_trigger", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_agent_runs_workflow_length",
        "agent_runs",
        "(workflow IS NULL OR char_length(workflow) BETWEEN 1 AND 64) "
        "AND (workflow_trigger IS NULL OR char_length(workflow_trigger) BETWEEN 1 AND 64)",
    )
    op.create_index(
        "ix_agent_runs_user_id_workflow_run_id",
        "agent_runs",
        ["user_id", "workflow_run_id"],
        postgresql_where=sa.text("workflow_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_user_id_workflow_run_id", table_name="agent_runs")
    op.drop_constraint("ck_agent_runs_workflow_length", "agent_runs", type_="check")
    op.drop_column("agent_runs", "workflow_trigger")
    op.drop_column("agent_runs", "workflow_run_id")
    op.drop_column("agent_runs", "workflow")
