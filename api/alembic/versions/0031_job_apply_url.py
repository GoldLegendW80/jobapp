"""The employer's application address of a posting, when its source gives one apart from the
posting's own page (a job board's listing): `jobs.apply_url`, read by the apply module as the
form address. The posting link the person sees stays `jobs.url`.

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("apply_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "apply_url")
