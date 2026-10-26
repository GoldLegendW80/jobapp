"""Bind the document-letter editor allowance to the adapter's persisted field."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Old rows retain only the broad source `document`, also used by CV text and file fields.
    # They cannot be backfilled safely; a fresh adapter fill establishes the classification.
    op.add_column(
        "application_answers",
        sa.Column("document_letter", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("application_answers", sa.Column("left_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("application_answers", "left_reason")
    op.drop_column("application_answers", "document_letter")
