"""The starter list of banned phrases (owner, 2026-11-07, "build all" of the career-ops analysis;
SPEC-documents.md, "Being careful", Decision 9): a switch of the person's writing rules, off for
every account, existing and new. Expand only: a column with a default that nothing older reads.

Revision ID: 0042
Revises: 0043
"""

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_settings",
        sa.Column("starter_phrases", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("document_settings", "starter_phrases")
