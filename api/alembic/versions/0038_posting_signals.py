"""Before the click (owner, 2026-11-07, "build all" of the career-ops analysis; SPEC-jobs, "Before
the click"): what a posting's text says, read at store and at rescore by app/jobs/signals.py.

Three nullable columns on `jobs`: `signals` (JSONB, a list of {code, quote, value}), the
`text_fingerprint` (the text's 64-bit SimHash, a bigint) and `closes_on` (the closing date the
text states). Expand only: nothing older reads or writes them, and a row the release before this
one stores leaves them null until a rescore reads it.

Revision ID: 0038
Revises: 0042
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0038"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("signals", pg.JSONB(), nullable=True))
    op.add_column("jobs", sa.Column("text_fingerprint", sa.BigInteger(), nullable=True))
    op.add_column("jobs", sa.Column("closes_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "closes_on")
    op.drop_column("jobs", "text_fingerprint")
    op.drop_column("jobs", "signals")
