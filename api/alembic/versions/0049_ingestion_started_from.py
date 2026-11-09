"""Activity (agents A10): an ingestion run records where it was started, so a run onboarding's
Build my profile started is told apart from Fill my profile's Start reading. Expand only: one
nullable column; older runs and the previous release's starts keep null.

Revision ID: 0049
Revises: 0048
"""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_runs", sa.Column("started_from", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_ingestion_runs_started_from_known",
        "ingestion_runs",
        "started_from IS NULL OR started_from = 'onboarding'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ingestion_runs_started_from_known", "ingestion_runs", type_="check")
    op.drop_column("ingestion_runs", "started_from")
