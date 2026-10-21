"""Browser B10: the LinkedIn checkpoints met for each person, from which the 7 day pause is read.

`linkedin_checkpoints` (cascaded with the user): written only by
`app/browser/linkedin_guard.record_checkpoint` (the sweep, later A16 and A18a), never by a route,
so no request of the person lifts a pause (app/browser/models.py).

Expand only: one new table. The release before this one reads and writes none of it
(CONTRIBUTING.md, Migrations). The number is the expected one: the orchestrator renumbers it on
the head of `main` when it merges.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "linkedin_checkpoints",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "origin IN ('sweep', 'easy_apply', 'profile')",
            name=op.f("ck_linkedin_checkpoints_origin_known"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_linkedin_checkpoints_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_linkedin_checkpoints")),
    )
    op.create_index("ix_linkedin_checkpoints_user_id_at", "linkedin_checkpoints", ["user_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_linkedin_checkpoints_user_id_at", table_name="linkedin_checkpoints")
    op.drop_table("linkedin_checkpoints")
