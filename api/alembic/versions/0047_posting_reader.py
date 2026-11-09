"""Jobs: what the posting reader did with each posting (jobs J19, issue #764; SPEC-jobs.md,
"Posting reader"). `jobs.read_at`, when the reader agent read the posting (null: never read), and
`jobs.read_attempts`, how many runs of the reader were given it, so the worker's backlog reads
every saved posting once and gives one up after its third attempt, and `jobs.read_run_id`, the
run holding the last attempt until its end is settled (an attempt counts only for a run that
failed for its own content). Expand only: nullable columns and a column with a server default,
which the release before this one neither reads nor writes. Existing rows start unread with no
attempt, so each is read once (owner, 2026-11-09).

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("read_attempts", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column("jobs", sa.Column("read_run_id", pg.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "read_run_id")
    op.drop_column("jobs", "read_attempts")
    op.drop_column("jobs", "read_at")
