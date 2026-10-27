"""The job websites a person signed in to, per browser profile (browser B17).

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_site_logins",
        sa.Column("profile_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("signed_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(host) BETWEEN 1 AND 253", name="host_length"),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["browser_profiles.profile_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("profile_id", "host"),
    )


def downgrade() -> None:
    op.drop_table("browser_site_logins")
