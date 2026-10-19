"""Apply A11: a person's company caps and Applications settings.

`apply_company_caps` (the person's own limits per company) and `apply_settings` (the mode after a
Like, the pause switch, the Full auto values; null reads the configured default), with the enum
`apply_like_mode` (app/apply/caps_models.py).

Expand only: two new tables and one new type. The release before this one never reads or writes
them (CONTRIBUTING.md, Migrations). The number is the expected one: the orchestrator renumbers it
on the head of `main` when it merges.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIKE_MODE = pg.ENUM("review", "full_auto", name="apply_like_mode", create_type=False)


def _user_fk(primary_key: bool = False) -> sa.Column:
    return sa.Column(
        "user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=primary_key,
        nullable=False,
    )


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    LIKE_MODE.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "apply_company_caps",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        _user_fk(),
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("max_count", sa.Integer(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("same_role_days", sa.Integer(), nullable=True),
        _now("created_at"),
        sa.UniqueConstraint("user_id", "company_key", name="uq_apply_company_caps_user_company"),
        sa.CheckConstraint("char_length(company_key) BETWEEN 1 AND 200", name="company_key_length"),
        sa.CheckConstraint("char_length(label) BETWEEN 1 AND 200", name="label_length"),
        sa.CheckConstraint("max_count BETWEEN 1 AND 100", name="max_count_range"),
        sa.CheckConstraint("window_days BETWEEN 1 AND 3650", name="window_days_range"),
        sa.CheckConstraint(
            "same_role_days IS NULL OR same_role_days BETWEEN 1 AND 3650",
            name="same_role_days_range",
        ),
    )
    op.create_table(
        "apply_settings",
        _user_fk(primary_key=True),
        sa.Column("mode_after_like", LIKE_MODE, server_default="review", nullable=False),
        sa.Column("paused", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("full_auto_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rehearsals_done", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("min_fit", sa.SmallInteger(), nullable=True),
        sa.Column("company_days", sa.Integer(), nullable=True),
        sa.Column("safety_ceiling", sa.Integer(), nullable=True),
        sa.Column("hold_s", sa.Integer(), nullable=True),
        _now("updated_at"),
        sa.CheckConstraint("min_fit IS NULL OR min_fit BETWEEN 0 AND 100", name="min_fit_range"),
        sa.CheckConstraint("rehearsals_done >= 0", name="rehearsals_done_not_negative"),
        sa.CheckConstraint(
            "company_days IS NULL OR company_days BETWEEN 1 AND 365", name="company_days_range"
        ),
        sa.CheckConstraint(
            "safety_ceiling IS NULL OR safety_ceiling BETWEEN 1 AND 100",
            name="safety_ceiling_range",
        ),
        sa.CheckConstraint("hold_s IS NULL OR hold_s BETWEEN 60 AND 86400", name="hold_s_range"),
    )


def downgrade() -> None:
    op.drop_table("apply_settings")
    op.drop_table("apply_company_caps")
    LIKE_MODE.drop(op.get_bind(), checkfirst=True)
