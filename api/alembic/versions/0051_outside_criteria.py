"""Jobs: what of a posting is outside the criteria of the search that found it (jobs J30, issue
#829; SPEC-jobs.md, "Kept with a badge"). `jobs.outside_criteria`, the badge's codes (`contract`,
`work_mode`, `older`; empty within, null never computed), `jobs.contract_stated`, whether a word,
a source, the posting reader or the person stated the contract (`importer.kind` answers CDI when
nothing does), and `jobs.criteria_search_id`, the saved search that found it last (null: the main
search). Expand only: three nullable columns the release before this one neither reads nor
writes. No backfill: a row stored before gets its badge at the next save of its search's criteria
or the next reading of the posting reader.

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("outside_criteria", pg.ARRAY(sa.Text()), nullable=True))
    op.add_column("jobs", sa.Column("contract_stated", sa.Boolean(), nullable=True))
    op.add_column("jobs", sa.Column("criteria_search_id", pg.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "criteria_search_id")
    op.drop_column("jobs", "contract_stated")
    op.drop_column("jobs", "outside_criteria")
