"""Follow ups of a sent application (owner, 2026-11-07, "build all" of the career-ops analysis;
SPEC-apply.md, "Follow ups"): one row per follow up of a posting, the open draft (no `sent_on`)
or one the person sent; and the person's switch, on by default. Expand only: a new table and a
column with a default that nothing older reads or writes.

Revision ID: 0040
Revises: 0045
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0040"
down_revision = "0045"
branch_labels = None
depends_on = None

# The same texts as app/apply/followup_models.py, frozen here as this revision wrote them.
CHANNEL_CHECK = "channel IS NULL OR channel IN ('own_mail', 'gmail')"
WRITER_CHECK = "written_by IN ('agent', 'person')"
SENT_CHECK = "(sent_on IS NULL) = (channel IS NULL)"


def upgrade() -> None:
    op.create_table(
        "follow_ups",
        sa.Column(
            "id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("posting_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.Text(), server_default="", nullable=False),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column("issues", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("written_by", sa.Text(), server_default="agent", nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("mail_draft_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("sent_on", sa.Date(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(CHANNEL_CHECK, name=op.f("ck_follow_ups_channel_allowed")),
        sa.CheckConstraint(WRITER_CHECK, name=op.f("ck_follow_ups_writer_allowed")),
        sa.CheckConstraint(SENT_CHECK, name=op.f("ck_follow_ups_sent_has_channel")),
        sa.CheckConstraint(
            "jsonb_typeof(issues) = 'array'", name=op.f("ck_follow_ups_issues_is_array")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_follow_ups_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["posting_id", "user_id"],
            ["jobs.id", "jobs.user_id"],
            name="fk_follow_ups_posting_id_jobs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_follow_ups")),
    )
    op.create_index(
        "ix_follow_ups_user_id_posting_id", "follow_ups", ["user_id", "posting_id"], unique=False
    )
    op.create_index(
        "uq_follow_ups_open_posting",
        "follow_ups",
        ["posting_id"],
        unique=True,
        postgresql_where=sa.text("sent_on IS NULL"),
    )
    op.add_column(
        "apply_settings",
        sa.Column("follow_ups", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("apply_settings", "follow_ups")
    op.drop_index("uq_follow_ups_open_posting", table_name="follow_ups")
    op.drop_index("ix_follow_ups_user_id_posting_id", table_name="follow_ups")
    op.drop_table("follow_ups")
