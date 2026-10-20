"""Opportunities O3: the pay and the work mode of a posting.

Five nullable columns on `jobs`: `pay_min`, `pay_max` (whole numbers), `pay_currency` (an ISO 4217
code), `pay_period` (the new enum `pay_period`: year, month, day, hour) and `work_mode` (the new
enum `work_mode`: remote, hybrid, office), with the check `ck_jobs_pay_complete`: all four pay
columns or none, 1 <= pay_min <= pay_max <= 10,000,000, the currency three capital letters
(app/jobs/models.py, PAY_CHECK).

Expand only: two new types, nullable columns, and a check every existing row passes (the new
columns are null on all of them). The release before this one never reads or writes them, and
its inserts leave them null (CONTRIBUTING.md, Migrations). The number is the expected one: the
orchestrator renumbers it on the head of `main` when it merges.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAY_PERIOD = pg.ENUM("year", "month", "day", "hour", name="pay_period", create_type=False)
WORK_MODE = pg.ENUM("remote", "hybrid", "office", name="work_mode", create_type=False)
# The same text as app/jobs/models.py PAY_CHECK, frozen here as this revision wrote it.
PAY_CHECK = (
    "(pay_min IS NULL AND pay_max IS NULL AND pay_currency IS NULL AND pay_period IS NULL) OR "
    "(pay_min IS NOT NULL AND pay_max IS NOT NULL AND pay_currency IS NOT NULL AND "
    "pay_period IS NOT NULL AND pay_min >= 1 AND pay_min <= pay_max AND pay_max <= 10000000 "
    "AND pay_currency ~ '^[A-Z]{3}$')"
)


def upgrade() -> None:
    PAY_PERIOD.create(op.get_bind(), checkfirst=True)
    WORK_MODE.create(op.get_bind(), checkfirst=True)
    op.add_column("jobs", sa.Column("pay_min", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("pay_max", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("pay_currency", sa.CHAR(3), nullable=True))
    op.add_column("jobs", sa.Column("pay_period", PAY_PERIOD, nullable=True))
    op.add_column("jobs", sa.Column("work_mode", WORK_MODE, nullable=True))
    op.create_check_constraint(op.f("ck_jobs_pay_complete"), "jobs", PAY_CHECK)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_jobs_pay_complete"), "jobs", type_="check")
    op.drop_column("jobs", "work_mode")
    op.drop_column("jobs", "pay_period")
    op.drop_column("jobs", "pay_currency")
    op.drop_column("jobs", "pay_max")
    op.drop_column("jobs", "pay_min")
    WORK_MODE.drop(op.get_bind(), checkfirst=True)
    PAY_PERIOD.drop(op.get_bind(), checkfirst=True)
