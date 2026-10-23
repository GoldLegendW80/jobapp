"""Ingestion: the file of an upload sent to become a base CV is kept (issue #519).

`ingestion_documents.keep_bytes` (boolean, false by default): the upload came from the CV panel
(`POST /api/ingestion/documents?for=base_cv`) and is a PDF or a Word file, so its `bytes` stay
once its text is stored, until the document is deleted or no base CV is converted from it any
more (SPEC-ingestion.md, "Text extraction"; app/ingestion/models.py).

Expand only: one column with a server default, so the release before this one inserts rows
without it and keeps nulling the bytes as it always did (CONTRIBUTING.md, Migrations).

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_documents",
        sa.Column("keep_bytes", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("ingestion_documents", "keep_bytes")
