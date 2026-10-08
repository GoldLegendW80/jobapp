"""Profile: profiles (one row per user, sections in JSONB) and profile_revisions (append-only).

Written by hand to match app/profile/models.py (the tests run `alembic check` against it).
Every statement is plain DDL so `alembic upgrade head --sql` works without a database.
Both tables cascade on user deletion: a deleted account leaves no profile behind.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("sections", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("photo", pg.BYTEA(), nullable=True),
        sa.Column("photo_mime", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", _tz(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("user_id", name="pk_profiles"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_profiles_user_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint("revision >= 0", name="ck_profiles_revision_not_negative"),
        sa.CheckConstraint(
            "jsonb_typeof(sections) = 'object'", name="ck_profiles_sections_is_object"
        ),
        sa.CheckConstraint(
            "(photo IS NULL) = (photo_mime IS NULL)", name="ck_profiles_photo_and_mime_together"
        ),
        sa.CheckConstraint(
            "photo_mime IS NULL OR photo_mime IN ('image/png', 'image/jpeg')",
            name="ck_profiles_photo_mime_known",
        ),
    )

    op.create_table(
        "profile_revisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True),  # bigserial
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("data", pg.JSONB(), nullable=False),
        sa.Column("created_at", _tz(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_profile_revisions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_profile_revisions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "revision", name="uq_profile_revisions_user_id_revision"),
        sa.CheckConstraint("revision > 0", name="ck_profile_revisions_revision_positive"),
    )
    op.create_index(
        "ix_profile_revisions_user_id_section_revision",
        "profile_revisions",
        ["user_id", "section", sa.text("revision DESC")],
    )


def downgrade() -> None:
    op.drop_table("profile_revisions")
    op.drop_table("profiles")
