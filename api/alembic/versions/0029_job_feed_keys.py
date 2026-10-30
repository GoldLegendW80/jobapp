"""The keys of the job feeds that need one (Adzuna), encrypted per person (SPEC-jobs, Job feeds).

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_feed_keys",
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("feed", sa.Text(), nullable=False),
        sa.Column("secret_encrypted", pg.BYTEA(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("last4", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("feed IN ('adzuna')", name="feed_known"),
        sa.CheckConstraint("octet_length(secret_encrypted) > 28", name="secret_encrypted_length"),
        sa.CheckConstraint("char_length(last4) = 4", name="last4_length"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "feed"),
    )


def downgrade() -> None:
    op.drop_table("job_feed_keys")
