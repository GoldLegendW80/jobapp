"""Each account's browsers at once (owner, 2026-11-07: "As an admin, i want being able to set the
make browser parallel by person"; SPEC-identity, SPEC-browser "Two browsers at once"): how many
slots of the browser the person's work may use at once, set by an admin, NULL for the server's
BROWSER_SLOTS (every account, existing and new, until an admin changes it). Expand only: a
nullable column that nothing older reads or writes.

Revision ID: 0037
Revises: 0036
"""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

# The same text as app/identity/models.py BROWSERS_CHECK, frozen here as this revision wrote it.
BROWSERS_CHECK = "browsers_at_once IS NULL OR browsers_at_once BETWEEN 1 AND 8"


def upgrade() -> None:
    op.add_column("users", sa.Column("browsers_at_once", sa.SmallInteger(), nullable=True))
    op.create_check_constraint(op.f("ck_users_browsers_at_once_range"), "users", BROWSERS_CHECK)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_browsers_at_once_range"), "users", type_="check")
    op.drop_column("users", "browsers_at_once")
