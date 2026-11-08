"""Ingestion: why an extraction step refused items (owner, 2026-11-08; SPEC-ingestion.md,
"Extraction agent"). `ingestion_run_steps.refusals`, `{section: {rule: count}}`: the proposals the
step refused and did not store afterwards, by section of the profile and by the rule they broke,
so the run's page and its review can say why an item was not kept once the worker's log is gone.
Constants only (`app/ingestion/refusals.py`). Expand only: a column with a server default, which
the release before this one neither reads nor writes, and a check every row meets.

Revision ID: 0046
Revises: 0039
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0046"
down_revision = "0039"
branch_labels = None
depends_on = None

# The text of app/ingestion/runs_models.py REFUSALS_CHECK, frozen as this revision wrote it.
REFUSALS_CHECK = "jsonb_typeof(refusals) = 'object'"


def upgrade() -> None:
    op.add_column(
        "ingestion_run_steps",
        sa.Column("refusals", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_check_constraint(
        op.f("ck_ingestion_run_steps_refusals_is_object"), "ingestion_run_steps", REFUSALS_CHECK
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_ingestion_run_steps_refusals_is_object"), "ingestion_run_steps", type_="check"
    )
    op.drop_column("ingestion_run_steps", "refusals")
