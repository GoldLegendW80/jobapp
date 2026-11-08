"""Documents: the document kind `interview_prep` and the chat channel `practice`.

An interview preparation is a posting document like a proposal (one per posting, by the partial
unique index of 0015; no base CV of its own), text only; its Practice chat is a channel of
`document_messages`. No table changes.

Expand only: two new enum values, added with IF NOT EXISTS, so a second upgrade does nothing.
The number is this branch's; the merge chains it on the head of `main`.

The downgrade deletes the preparations (with their versions, generations and messages) and makes
both types again without their value: Postgres cannot drop an enum value. The checks of 0015 that
compare `kind` with a literal, and the channel's default, are dropped first and made again.

Revision ID: 0043
Revises: 0037
Create Date: 2026-11-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0043"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKS = {
    "ck_documents_base_is_a_cv": "scope = 'posting' OR kind = 'cv'",
    "ck_documents_letter_has_no_base": (
        "scope = 'base' OR kind = 'cv' OR base_document_id IS NULL"
    ),
}


def upgrade() -> None:
    op.execute("ALTER TYPE document_kind ADD VALUE IF NOT EXISTS 'interview_prep'")
    op.execute("ALTER TYPE message_channel ADD VALUE IF NOT EXISTS 'practice'")


def downgrade() -> None:
    op.execute("DELETE FROM documents WHERE kind::text = 'interview_prep'")
    op.execute("DELETE FROM document_messages WHERE channel::text = 'practice'")
    for name in CHECKS:
        op.execute(f"ALTER TABLE documents DROP CONSTRAINT {name}")
    op.execute("ALTER TYPE document_kind RENAME TO document_kind_0043")
    op.execute("CREATE TYPE document_kind AS ENUM ('cv', 'letter', 'proposal')")
    op.execute(
        "ALTER TABLE documents ALTER COLUMN kind TYPE document_kind USING kind::text::document_kind"
    )
    op.execute("DROP TYPE document_kind_0043")
    for name, expression in CHECKS.items():
        op.execute(f"ALTER TABLE documents ADD CONSTRAINT {name} CHECK ({expression})")
    op.execute("ALTER TABLE document_messages ALTER COLUMN channel DROP DEFAULT")
    op.execute("ALTER TYPE message_channel RENAME TO message_channel_0043")
    op.execute("CREATE TYPE message_channel AS ENUM ('content', 'design')")
    op.execute(
        "ALTER TABLE document_messages ALTER COLUMN channel TYPE message_channel "
        "USING channel::text::message_channel"
    )
    op.execute("DROP TYPE message_channel_0043")
    op.execute("ALTER TABLE document_messages ALTER COLUMN channel SET DEFAULT 'content'")
