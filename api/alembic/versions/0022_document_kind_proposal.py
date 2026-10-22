"""Documents, issue #485: the document kind `proposal`, an outreach proposal (opportunities O9a).

`document_kind` gains the value `proposal`. No table changes: a proposal is a posting document
like a letter (one per posting, by the partial unique index of 0015; no base CV of its own, by
`ck_documents_letter_has_no_base`), and nothing renders it.

Expand only: a new enum value. The release before this one writes no proposal and none exists
until opportunities O9a creates them; the value is added with IF NOT EXISTS, so a second upgrade
does nothing. The number is the expected one (B10 takes 0020, O3 0021): the orchestrator chains
it on the head of `main` when it merges.

The downgrade deletes the proposals (with their versions, generations and messages) and makes
the type again without the value: Postgres cannot drop an enum value. The two checks that
compare `kind` with a literal are dropped first and made again on the new type.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The checks of 0015 that read `kind`, by name, with their expression.
CHECKS = {
    "ck_documents_base_is_a_cv": "scope = 'posting' OR kind = 'cv'",
    "ck_documents_letter_has_no_base": (
        "scope = 'base' OR kind = 'cv' OR base_document_id IS NULL"
    ),
}


def upgrade() -> None:
    op.execute("ALTER TYPE document_kind ADD VALUE IF NOT EXISTS 'proposal'")


def downgrade() -> None:
    op.execute("DELETE FROM documents WHERE kind::text = 'proposal'")
    for name in CHECKS:
        op.execute(f"ALTER TABLE documents DROP CONSTRAINT {name}")
    op.execute("ALTER TYPE document_kind RENAME TO document_kind_0022")
    op.execute("CREATE TYPE document_kind AS ENUM ('cv', 'letter')")
    op.execute(
        "ALTER TABLE documents ALTER COLUMN kind TYPE document_kind USING kind::text::document_kind"
    )
    op.execute("DROP TYPE document_kind_0022")
    for name, expression in CHECKS.items():
        op.execute(f"ALTER TABLE documents ADD CONSTRAINT {name} CHECK ({expression})")
