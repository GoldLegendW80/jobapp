"""Apply, issue #362: the partial index through which the pace per ATS host reads its clicks.

`caps.next_slot` reads every person's `click` events of the last 24 hours on each tick of every
approved application; `ix_application_events_click_at` holds those events only, by time.

Expand only: one new index, which the release before this one does not need (CONTRIBUTING.md,
Migrations). It is built in the migration's transaction: writes to `application_events` wait
until it is built. The number is the expected one: the orchestrator renumbers it on the head of
`main` when it merges.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_application_events_click_at",
        "application_events",
        ["at"],
        postgresql_where=sa.text("kind = 'click'"),
    )


def downgrade() -> None:
    op.drop_index("ix_application_events_click_at", table_name="application_events")
