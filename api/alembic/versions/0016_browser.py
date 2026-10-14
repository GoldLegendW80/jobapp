"""Browser B3: the browser profile of each person, and the queue of directories to remove.

`browser_profiles` (one row per user, cascaded with the user), the sequence that numbers the
profiles' Linux uids from 20000 and never reuses one, `browser_profile_removals`, and the trigger
that queues a directory's removal whenever a profile row is deleted (app/browser/models.py says
why each exists).

Expand only: a new sequence, two new tables, one function and its trigger. The release before
this one reads and writes none of it (CONTRIBUTING.md, Migrations).

Written by hand to match app/browser/models.py (the tests run `alembic check` against it). Every
statement is plain DDL so `alembic upgrade head --sql` works without a database.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UID_MIN, UID_MAX = 20000, 59999

REMOVAL_FUNCTION = """
CREATE FUNCTION browser_profiles_queue_removal() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO browser_profile_removals (profile_id, uid)
    VALUES (OLD.profile_id, OLD.uid)
    ON CONFLICT (profile_id) DO NOTHING;
    RETURN OLD;
END;
$$
"""

REMOVAL_TRIGGER = (
    "CREATE TRIGGER browser_profiles_queue_removal AFTER DELETE ON browser_profiles "
    "FOR EACH ROW EXECUTE FUNCTION browser_profiles_queue_removal()"
)


def upgrade() -> None:
    op.execute(
        f"CREATE SEQUENCE browser_profile_uid_seq START WITH {UID_MIN} "
        f"MINVALUE {UID_MIN} MAXVALUE {UID_MAX} NO CYCLE"
    )
    op.create_table(
        "browser_profiles",
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"uid BETWEEN {UID_MIN} AND {UID_MAX}", name=op.f("ck_browser_profiles_uid_in_range")
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_browser_profiles_size_not_negative")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_browser_profiles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_browser_profiles")),
        sa.UniqueConstraint("profile_id", name=op.f("uq_browser_profiles_profile_id")),
        sa.UniqueConstraint("uid", name=op.f("uq_browser_profiles_uid")),
    )
    op.create_table(
        "browser_profile_removals",
        sa.Column("profile_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("profile_id", name=op.f("pk_browser_profile_removals")),
    )
    op.execute(REMOVAL_FUNCTION)
    op.execute(REMOVAL_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER browser_profiles_queue_removal ON browser_profiles")
    op.execute("DROP FUNCTION browser_profiles_queue_removal()")
    op.drop_table("browser_profile_removals")
    op.drop_table("browser_profiles")
    op.execute("DROP SEQUENCE browser_profile_uid_seq")
