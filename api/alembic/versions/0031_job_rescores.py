"""The rescore that follows a change of the skills section (product audit, chapter 2, owner
answer 4 of 2026-11-05): one row per person, the revision asked for and the one done.

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_rescores",
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("wanted_revision", sa.Integer(), nullable=False),
        sa.Column("wanted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("done_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("running_revision", sa.Integer(), nullable=True),
        sa.Column("running_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint(
            "(running_revision IS NULL) = (running_since IS NULL)", name="running_pair"
        ),
        sa.CheckConstraint("jsonb_typeof(outcome) = 'object'", name="outcome_is_object"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("job_rescores")
