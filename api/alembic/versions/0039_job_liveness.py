"""What the last check of a posting's own address found (owner, 2026-11-07, "build all" of the
career-ops analysis; SPEC-jobs, "Liveness"): `jobs.liveness`, {verdict, reason, sentence,
checked_at}, null until a check ran. Expand only: a nullable column that nothing older reads or
writes.

Revision ID: 0039
Revises: 0040
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0039"
down_revision = "0040"
branch_labels = None
depends_on = None

# The same text as app/jobs/models.py LIVENESS_CHECK, frozen here as this revision wrote it.
LIVENESS_CHECK = "liveness IS NULL OR jsonb_typeof(liveness) = 'object'"


def upgrade() -> None:
    op.add_column("jobs", sa.Column("liveness", pg.JSONB(), nullable=True))
    op.create_check_constraint(op.f("ck_jobs_liveness_is_object"), "jobs", LIVENESS_CHECK)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_jobs_liveness_is_object"), "jobs", type_="check")
    op.drop_column("jobs", "liveness")
