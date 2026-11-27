"""Jobs: a posting seen only as a card of a list, its own page not read (jobs J31, issue #830;
SPEC-jobs.md, "Seen only in a list"). `jobs.list_url`, the address of the list it was kept from;
null once its page was read or the person set its text, and for every other posting. Expand only:
one nullable column the release before this one neither reads nor writes.

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("list_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "list_url")
