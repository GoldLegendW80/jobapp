"""Apply A6: the options of a field, on its answer row.

`application_answers.options` (jsonb, null when the form offers none): the labels of a select,
combobox or single choice, so the gate can check the person's choice against them and the approval
view can show them (app/apply/models.py).

Expand only: one nullable column. The release before this one never reads or writes it
(CONTRIBUTING.md, Migrations). The number is the expected one: the orchestrator renumbers it on
the head of `main` when it merges.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("application_answers", sa.Column("options", pg.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("application_answers", "options")
