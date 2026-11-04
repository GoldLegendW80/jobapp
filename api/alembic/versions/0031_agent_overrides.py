"""The Agents tab of Admin (agents A6): `agent_overrides`, the saved versions of what an admin
changed in an agent's definition; `agent_tools`, the app tools as the worker describes them; and
`agent_runs.agent_version`, the version a run used.

Revision ID: 0031
Revises: 0030

Expand only: two new tables, a nullable column and an index (an agent's last runs). The previous release never reads them.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_overrides",
        sa.Column("id", sa.BigInteger(), autoincrement=True),  # bigserial
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changes", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("saved_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_agent_overrides"),
        sa.ForeignKeyConstraint(
            ["saved_by"],
            ["users.id"],
            name="fk_agent_overrides_saved_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("agent", "version", name="uq_agent_overrides_agent_version"),
        sa.CheckConstraint(
            "char_length(agent) >= 1 AND char_length(agent) <= 64",
            name="ck_agent_overrides_agent_length",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_overrides_version_positive"),
        sa.CheckConstraint(
            "jsonb_typeof(changes) = 'object'", name="ck_agent_overrides_changes_is_object"
        ),
        sa.CheckConstraint(
            "note IS NULL OR char_length(note) <= 500", name="ck_agent_overrides_note_length"
        ),
    )
    op.create_table(
        "agent_tools",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("module", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", pg.JSONB(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name", name="pk_agent_tools"),
        sa.CheckConstraint(
            "jsonb_typeof(input_schema) = 'object'", name="ck_agent_tools_schema_is_object"
        ),
    )
    op.add_column("agent_runs", sa.Column("agent_version", sa.Integer(), nullable=True))
    op.create_index(
        "ix_agent_runs_agent_created_at", "agent_runs", ["agent", sa.text("created_at DESC")]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_agent_created_at", table_name="agent_runs")
    op.drop_column("agent_runs", "agent_version")
    op.drop_table("agent_tools")
    op.drop_table("agent_overrides")
