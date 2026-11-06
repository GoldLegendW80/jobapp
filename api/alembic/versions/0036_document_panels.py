"""The hiring panel's record (documents D29): one row per panel run over one version of a CV, a
letter or a base CV, and the mode and the version origin of its rebuild.

Revision ID: 0036
Revises: 0035

Expand only. A new table `document_panels` (owned by its document, removed with it), and one new
value in each of two enums: `generation_mode` gets `panel` (the rebuild's generation) and
`version_origin` gets `panel` (the rebuilt version). The previous release, running on the
migrated schema for the seconds of a deploy, never writes either value; it reads a generation or
a version of origin `panel` only once the new release wrote one, after the deploy.

The downgrade drops the table. Postgres cannot drop an enum value: the two values stay, unused
by the previous release, which never writes them.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE generation_mode ADD VALUE IF NOT EXISTS 'panel'")
    op.execute("ALTER TYPE version_origin ADD VALUE IF NOT EXISTS 'panel'")
    op.create_table(
        "document_panels",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "source_run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("reviewed_version", sa.Integer(), nullable=False),
        sa.Column("rebuilt_version", sa.Integer()),
        sa.Column("generation_id", pg.UUID(as_uuid=True)),
        sa.Column("reviews", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("mistakes", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("questions", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("closest", pg.JSONB()),
        sa.Column("screen_before", sa.Text()),
        sa.Column("screen_after", sa.Text()),
        sa.Column(
            "failed",
            pg.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "run_ids",
            pg.ARRAY(pg.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["documents.id", "documents.user_id"],
            name="fk_document_panels_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("reviewed_version >= 1", name="reviewed_version_positive"),
        sa.CheckConstraint("status IN ('running', 'done', 'failed')", name="status_known"),
    )
    op.create_index(
        "ix_document_panels_document_id", "document_panels", ["document_id", "created_at"]
    )
    op.create_index("ix_document_panels_user_id_status", "document_panels", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_document_panels_user_id_status", table_name="document_panels")
    op.drop_index("ix_document_panels_document_id", table_name="document_panels")
    op.drop_table("document_panels")
