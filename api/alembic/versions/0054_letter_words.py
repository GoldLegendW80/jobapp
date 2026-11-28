"""Documents: the length a person's letters aim at (documents D32, issue #862; SPEC-documents.md,
"Being careful", a letter's length). `document_settings.letter_words`, the words a letter's
paragraphs aim at together, 150 to 600. Expand only: one NOT NULL column with a server default of
350, the length the release before this one asks for, which neither reads nor writes it, and a
check of its bounds that the default meets. Letters already written are not touched.

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_settings",
        sa.Column("letter_words", sa.Integer(), nullable=False, server_default=sa.text("350")),
    )
    op.create_check_constraint(
        op.f("ck_document_settings_letter_words_bounds"),
        "document_settings",
        "letter_words BETWEEN 150 AND 600",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_document_settings_letter_words_bounds"), "document_settings", type_="check"
    )
    op.drop_column("document_settings", "letter_words")
