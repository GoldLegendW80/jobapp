"""Sweeps: index on the slot of scheduled sweeps (jobs J5).

The worker checks, every 5 minutes and for every user with a schedule, whether a slot already has
its sweep (app/jobs/sweep.py slot_taken: user_id, trigger schedule, stats->>'schedule_slot').
This partial expression index answers that without reading the user's sweep rows.

Written by hand to match app/jobs/models.py (the tests run `alembic check` against it).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_sweeps_user_id_schedule_slot",
        "sweeps",
        ["user_id", sa.text("(stats ->> 'schedule_slot')")],
        postgresql_where=sa.text("trigger = 'schedule'"),
    )


def downgrade() -> None:
    op.drop_index("ix_sweeps_user_id_schedule_slot", table_name="sweeps")
