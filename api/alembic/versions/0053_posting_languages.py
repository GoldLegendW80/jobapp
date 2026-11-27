"""Jobs: the languages a posting asks for, as the posting reader read them (jobs J35, issue #858;
SPEC-jobs.md, "Posting reader" and "Kept with a badge"). `jobs.languages`: `{"written_in": the
language the ad is written in or null, "asked": [{name, level, required, alternatives, quote}]}`,
null on a posting the reader has not read since this revision. Expand only: one nullable column
the release before this one neither reads nor writes. No backfill: the reader's backlog reads the
open postings again, a bounded number a day (app/jobs/reader.py, `start_backlog`).

Revision ID: 0053
Revises: 0052
Create Date: 2026-11-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("languages", pg.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "languages")
