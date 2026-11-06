"""The "About you" document of each person (SPEC-profile, "About you"; owner, 2026-11-05: the
profile other than identity is a Markdown document that a model interprets): the text, the
state of each part and the last reading by the model. Expand only: a new table that nothing
older reads.

Revision ID: 0034
Revises: 0033
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_about",
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("parts", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("interpretation", pg.JSONB(), nullable=True),
        sa.Column("undo", pg.JSONB(), nullable=True),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("dones_on", sa.Date(), nullable=True),
        sa.Column("dones", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("char_length(text) <= 300000", name="text_length"),
        sa.CheckConstraint("version >= 0", name="version_not_negative"),
        sa.CheckConstraint("jsonb_typeof(parts) = 'object'", name="parts_is_object"),
        sa.CheckConstraint("dones >= 0", name="dones_not_negative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("profile_about")
