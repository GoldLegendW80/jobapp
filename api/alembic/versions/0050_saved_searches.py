"""Saved searches (jobs J32): the person's other searches beside the main one, the runs that wait
for the running sweep (a schedule slot's run taken back is kept, `taken_back_at`, until the slot
leaves its window), and the search a sweep ran. Expand only: two new tables, one nullable column
and an index; the previous release ignores them, and its sweeps keep a null search (the main
one).

Revision ID: 0050
Revises: 0049
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

TRIGGER = pg.ENUM("manual", "schedule", name="sweep_trigger", create_type=False)


def upgrade() -> None:
    op.create_table(
        "saved_searches",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("criteria", pg.JSONB(), nullable=False),
        sa.Column("schedule", pg.JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 60", name="name_length"),
        sa.CheckConstraint("jsonb_typeof(criteria) = 'object'", name="criteria_is_object"),
        sa.CheckConstraint("jsonb_typeof(schedule) = 'object'", name="schedule_is_object"),
        sa.CheckConstraint("revision >= 1", name="revision_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_saved_searches_id_user_id"),
    )
    op.create_index(
        "ix_saved_searches_user_id_name",
        "saved_searches",
        ["user_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "sweep_requests",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("position", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("saved_search_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger", TRIGGER, nullable=False),
        sa.Column("schedule_slot", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("taken_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["saved_search_id", "user_id"],
            ["saved_searches.id", "saved_searches.user_id"],
            name="fk_sweep_requests_saved_search_id_saved_searches",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sweep_requests_one_main",
        "sweep_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("saved_search_id IS NULL AND taken_back_at IS NULL"),
    )
    op.create_index(
        "ix_sweep_requests_one_per_search",
        "sweep_requests",
        ["user_id", "saved_search_id"],
        unique=True,
        postgresql_where=sa.text("saved_search_id IS NOT NULL AND taken_back_at IS NULL"),
    )
    op.create_index("ix_sweep_requests_user_id_position", "sweep_requests", ["user_id", "position"])
    op.add_column("sweeps", sa.Column("saved_search_id", pg.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_sweeps_saved_search_id_saved_searches",
        "sweeps",
        "saved_searches",
        ["saved_search_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_sweeps_saved_search_id",
        "sweeps",
        ["saved_search_id"],
        postgresql_where=sa.text("saved_search_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_sweeps_saved_search_id", table_name="sweeps")
    op.drop_constraint("fk_sweeps_saved_search_id_saved_searches", "sweeps", type_="foreignkey")
    op.drop_column("sweeps", "saved_search_id")
    op.drop_table("sweep_requests")
    op.drop_table("saved_searches")
