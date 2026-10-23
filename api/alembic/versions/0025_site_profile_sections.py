"""Persist the candidate profile address, section approvals and field provenance.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("url",):
        op.add_column(
            "site_profile_runs", sa.Column(name, sa.Text(), nullable=False, server_default="")
        )
    for name in ("needs", "error_code"):
        op.add_column("site_profile_runs", sa.Column(name, sa.Text()))
    op.add_column(
        "site_profile_runs",
        sa.Column("autosave_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "site_profile_fields",
        sa.Column("source_path", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "site_profile_fields",
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("site_profile_fields", sa.Column("refusal", sa.Text()))
    op.add_column(
        "site_profile_fields", sa.Column("options", pg.JSONB(), nullable=False, server_default="[]")
    )
    op.create_table(
        "site_profile_sections",
        sa.Column("run_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section", sa.Text(), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("save_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.Text(), nullable=False, server_default="ready"),
        sa.Column("error_code", sa.Text()),
        sa.Column("events", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "user_id"],
            ["site_profile_runs.id", "site_profile_runs.user_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "state IN ('ready', 'approved', 'saving', 'saved', 'skipped', 'left')",
            name="ck_site_profile_sections_valid_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("site_profile_sections")
    for name in ("source_path", "required", "refusal", "options"):
        op.drop_column("site_profile_fields", name)
    for name in ("url", "needs", "error_code", "autosave_confirmed"):
        op.drop_column("site_profile_runs", name)
