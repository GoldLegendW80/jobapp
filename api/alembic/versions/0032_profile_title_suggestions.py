"""The job titles agent's last suggestions per person (SPEC-profile, "Job title suggestions"):
the ideas kept, the titles the person dismissed and the person's last messages to the agent.
Expand only: a new table that nothing older reads. (The dismissed titles and the messages were
added to this revision before it was deployed anywhere.)

Revision ID: 0032
Revises: 0031
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_title_suggestions",
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("suggestions", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("basis", sa.Text(), nullable=True),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("asks_on", sa.Date(), nullable=True),
        sa.Column("asks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dismissed", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("messages", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.CheckConstraint("jsonb_typeof(suggestions) = 'array'", name="suggestions_is_array"),
        sa.CheckConstraint("asks >= 0", name="asks_not_negative"),
        sa.CheckConstraint("error IS NULL OR char_length(error) <= 40", name="error_length"),
        sa.CheckConstraint("basis IS NULL OR char_length(basis) = 64", name="basis_length"),
        sa.CheckConstraint("jsonb_typeof(dismissed) = 'array'", name="dismissed_is_array"),
        sa.CheckConstraint("jsonb_typeof(messages) = 'array'", name="messages_is_array"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("profile_title_suggestions")
