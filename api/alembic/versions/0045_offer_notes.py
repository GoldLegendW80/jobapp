"""Documents: the document kind `offer_notes`, the notes on an offer letter the person uploaded
(owner 2026-11-07, "build all" of the career-ops analysis; SPEC-documents.md, "Reading an
offer").

`document_kind` gains the value `offer_notes`. No table changes: the notes are a posting
document like a proposal (one per posting, by the partial unique index of 0015; no base CV of
their own), text only, and the letter they read is `source_document_id`.

Expand only: a new enum value, added with IF NOT EXISTS, so a second upgrade does nothing. The
release before this one writes no notes.

The downgrade deletes the notes (with their versions, generations and messages) and makes the
type again without the value, keeping every other value the type holds then: Postgres cannot
drop an enum value. The two checks that compare `kind` with a literal are dropped first and made
again on the new type.

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VALUE = "offer_notes"
# The checks of 0015 that read `kind`, by name, with their expression.
CHECKS = {
    "ck_documents_base_is_a_cv": "scope = 'posting' OR kind = 'cv'",
    "ck_documents_letter_has_no_base": (
        "scope = 'base' OR kind = 'cv' OR base_document_id IS NULL"
    ),
}


def upgrade() -> None:
    op.execute(f"ALTER TYPE document_kind ADD VALUE IF NOT EXISTS '{VALUE}'")


def downgrade() -> None:
    kept = [
        label
        for label in op.get_bind()
        .execute(
            sa.text(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'document_kind' ORDER BY e.enumsortorder"
            )
        )
        .scalars()
        if label != VALUE
    ]
    op.execute("DELETE FROM documents WHERE kind::text = 'offer_notes'")
    for name in CHECKS:
        op.execute(f"ALTER TABLE documents DROP CONSTRAINT {name}")
    op.execute("ALTER TYPE document_kind RENAME TO document_kind_0045")
    labels = ", ".join(f"'{label}'" for label in kept)
    op.execute(f"CREATE TYPE document_kind AS ENUM ({labels})")
    op.execute(
        "ALTER TABLE documents ALTER COLUMN kind TYPE document_kind USING kind::text::document_kind"
    )
    op.execute("DROP TYPE document_kind_0045")
    for name, expression in CHECKS.items():
        op.execute(f"ALTER TABLE documents ADD CONSTRAINT {name} CHECK ({expression})")
