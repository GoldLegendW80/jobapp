"""Agents A3: proposals and the enums proposal_status and proposal_decider.

proposals cascades from users; run_id is nullable (a rule-based system proposal of SPEC-mail has
no run, issue #17 item 1) and set to NULL when the run is deleted; target_id is nullable (a
proposal that creates something has no row yet) and has no foreign key, since `target` names the
table. decided_by and decided_at are null exactly while the proposal is pending.

Written by hand to match app/agents/models.py (the tests run `alembic check` against it).
Every statement is plain DDL so `alembic upgrade head --sql` works without a database.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("pending", "accepted", "rejected", "superseded", "invalid", "undone")
DECIDERS = ("user", "system")


def _tz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _labels(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(f"CREATE TYPE proposal_status AS ENUM ({_labels(STATUSES)})")
    op.execute(f"CREATE TYPE proposal_decider AS ENUM ({_labels(DECIDERS)})")
    op.create_table(
        "proposals",
        sa.Column(
            "id", pg.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("target_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "status",
            pg.ENUM(*STATUSES, name="proposal_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "decided_by",
            pg.ENUM(*DECIDERS, name="proposal_decider", create_type=False),
            nullable=True,
        ),
        sa.Column("decided_at", _tz(), nullable=True),
        sa.Column("edited_value", pg.JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", _tz(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_proposals"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_proposals_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_proposals_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "char_length(target) >= 1 AND char_length(target) <= 32",
            name="ck_proposals_target_length",
        ),
        sa.CheckConstraint(
            "char_length(kind) >= 1 AND char_length(kind) <= 64",
            name="ck_proposals_kind_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="ck_proposals_payload_is_object"
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL) "
            "AND (status = 'pending') = (decided_by IS NULL)",
            name="ck_proposals_decided_unless_pending",
        ),
    )
    op.create_index(
        "ix_proposals_user_id_created_at", "proposals", ["user_id", sa.text("created_at DESC")]
    )
    op.create_index(
        "ix_proposals_user_id_target_target_id", "proposals", ["user_id", "target", "target_id"]
    )
    op.create_index("ix_proposals_run_id", "proposals", ["run_id"])


def downgrade() -> None:
    op.drop_table("proposals")
    op.execute("DROP TYPE proposal_decider")
    op.execute("DROP TYPE proposal_status")
