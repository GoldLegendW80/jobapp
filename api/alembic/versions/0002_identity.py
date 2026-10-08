"""Identity: users, sessions, invites, login_attempts; citext extension; role and kind enums.

Written by hand to match app/identity/models.py (the tests run `alembic check` against it).
Every statement is plain SQL or DDL so `alembic upgrade head --sql` works without a database.

citext: upgrade creates the extension only when it is missing, and then marks it with a
comment. Downgrade drops it only when it carries that mark (this migration created it) and
nothing else uses it any more: a citext that existed before, or that another table has since
started to use, is kept. Postgres refuses `DROP EXTENSION` (RESTRICT) while a column depends on
it, and the downgrade turns that refusal into a notice.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = pg.ENUM("admin", "member", name="user_role", create_type=False)
INVITE_KIND = pg.ENUM("invite", "reset", name="invite_kind", create_type=False)


def _tz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'citext') THEN
                CREATE EXTENSION citext;
                COMMENT ON EXTENSION citext IS 'created by jobapp migration 0002_identity';
            END IF;
        END
        $$
        """
    )
    op.execute("CREATE TYPE user_role AS ENUM ('admin', 'member')")
    op.execute("CREATE TYPE invite_kind AS ENUM ('invite', 'reset')")

    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", pg.CITEXT(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", ROLE, nullable=False, server_default="member"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", _tz(), nullable=False, server_default=_now()),
        sa.Column("updated_at", _tz(), nullable=False, server_default=_now()),
        sa.Column("last_login_at", _tz(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", pg.BYTEA(), nullable=False),
        sa.Column("created_at", _tz(), nullable=False, server_default=_now()),
        sa.Column("last_seen_at", _tz(), nullable=False, server_default=_now()),
        sa.Column("expires_at", _tz(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip", pg.INET(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="ck_sessions_token_hash_length"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "invites",
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", pg.CITEXT(), nullable=False),
        sa.Column("kind", INVITE_KIND, nullable=False),
        sa.Column("role", ROLE, nullable=False, server_default="member"),
        sa.Column("token_hash", pg.BYTEA(), nullable=False),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", _tz(), nullable=False, server_default=_now()),
        sa.Column("expires_at", _tz(), nullable=False),
        sa.Column("used_at", _tz(), nullable=True),
        sa.Column("revoked_at", _tz(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_invites"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_invites_created_by_users", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("token_hash", name="uq_invites_token_hash"),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="ck_invites_token_hash_length"),
        sa.CheckConstraint("expires_at > created_at", name="ck_invites_expires_after_creation"),
    )
    op.create_index("ix_invites_created_by", "invites", ["created_by"])
    op.create_index(
        "ix_invites_active_email_kind",
        "invites",
        ["email", "kind"],
        unique=True,
        postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
    )

    op.create_table(
        "login_attempts",
        sa.Column("id", sa.BigInteger(), autoincrement=True),  # bigserial
        sa.Column("email", pg.CITEXT(), nullable=False),
        sa.Column("ip", pg.INET(), nullable=True),
        sa.Column("at", _tz(), nullable=False, server_default=_now()),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_login_attempts"),
    )
    op.create_index("ix_login_attempts_email_at", "login_attempts", ["email", "at"])
    op.create_index("ix_login_attempts_ip_at", "login_attempts", ["ip", "at"])


def downgrade() -> None:
    # Dropping a table drops its indexes and constraints; children before parents.
    op.drop_table("login_attempts")
    op.drop_table("invites")
    op.drop_table("sessions")
    op.drop_table("users")
    op.execute("DROP TYPE invite_kind")
    op.execute("DROP TYPE user_role")
    op.execute(
        """
        DO $$
        DECLARE
            ext_oid oid := (SELECT oid FROM pg_extension WHERE extname = 'citext');
        BEGIN
            IF obj_description(ext_oid, 'pg_extension')
               = 'created by jobapp migration 0002_identity' THEN
                BEGIN
                    DROP EXTENSION citext;
                EXCEPTION WHEN dependent_objects_still_exist THEN
                    RAISE NOTICE 'citext kept: other objects still use it';
                END;
            END IF;
        END
        $$
        """
    )
