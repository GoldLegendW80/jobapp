"""Jobs: how a posting matches the person's career and culture, as a model read it (jobs J38, issue
#876; SPEC-jobs.md, "Career and culture"). `jobs.match`: the reading (`career`, `culture`,
`strengths`, `gaps`, `rated_at` and `text`, the digest of the text read) and the rating's own
bookkeeping (`attempts` on the text `tried`, the `run` holding the last one), null on a posting
never given to a rating. Expand only: one nullable column the release before this one neither
reads nor writes. No backfill: the reader's backlog rates the postings, a bounded number a day
(app/jobs/match.py).

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("match", pg.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "match")
