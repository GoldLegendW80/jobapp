"""Migration 0002_identity against a real Postgres: schema, round trip, citext ownership."""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command

from app.config import get_settings
from tests.test_migrations import alembic_config, current_revision

TABLES = {"users", "sessions", "invites", "login_attempts"}


@pytest.fixture
def at_base(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.Engine]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.downgrade(alembic_config(), "base")
    engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP EXTENSION IF EXISTS citext"))
    yield engine
    command.downgrade(alembic_config(), "base")
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS other_module"))
        conn.execute(sa.text("DROP EXTENSION IF EXISTS citext"))
    engine.dispose()


def citext_installed(engine: sa.Engine) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                sa.text("SELECT count(*) FROM pg_extension WHERE extname='citext'")
            ).scalar_one()
        )


def enum_labels(engine: sa.Engine, name: str) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                sa.text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = :n ORDER BY e.enumsortorder"
                ),
                {"n": name},
            ).scalars()
        )


def test_upgrade_creates_schema(at_base: sa.Engine) -> None:
    command.upgrade(alembic_config(), "head")
    inspector = sa.inspect(at_base)
    assert set(inspector.get_table_names()) >= TABLES
    assert citext_installed(at_base)
    assert enum_labels(at_base, "user_role") == ["admin", "member"]
    assert enum_labels(at_base, "invite_kind") == ["invite", "reset"]

    indexes = {table: {ix["name"]: ix for ix in inspector.get_indexes(table)} for table in TABLES}
    assert set(indexes["sessions"]) >= {"ix_sessions_user_id", "ix_sessions_expires_at"}
    assert set(indexes["login_attempts"]) == {
        "ix_login_attempts_email_at",
        "ix_login_attempts_ip_at",
    }
    assert indexes["login_attempts"]["ix_login_attempts_email_at"]["column_names"] == [
        "email",
        "at",
    ]
    active = indexes["invites"]["ix_invites_active_email_kind"]
    assert active["unique"]
    assert active["column_names"] == ["email", "kind"]
    where = active["dialect_options"]["postgresql_where"]
    assert "used_at IS NULL" in where
    assert "revoked_at IS NULL" in where

    uniques = {
        table: {u["name"] for u in inspector.get_unique_constraints(table)} for table in TABLES
    }
    assert uniques["users"] == {"uq_users_email"}
    assert uniques["sessions"] == {"uq_sessions_token_hash"}
    assert uniques["invites"] == {"uq_invites_token_hash"}

    fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("sessions")}
    assert fks["fk_sessions_user_id_users"]["options"]["ondelete"] == "CASCADE"
    fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("invites")}
    assert fks["fk_invites_created_by_users"]["options"]["ondelete"] == "SET NULL"

    columns = {c["name"]: c for c in inspector.get_columns("users")}
    assert type(columns["email"]["type"]).__name__ == "CITEXT"
    assert columns["created_at"]["type"].timezone is True
    for table in TABLES:
        for column in inspector.get_columns(table):
            if isinstance(column["type"], sa.DateTime):
                assert column["type"].timezone, (table, column["name"])
    attempts_id = {c["name"]: c for c in inspector.get_columns("login_attempts")}["id"]
    assert isinstance(attempts_id["type"], sa.BigInteger)
    assert "nextval" in attempts_id["default"]  # bigserial


def test_up_down_up_with_data(at_base: sa.Engine) -> None:
    command.upgrade(alembic_config(), "head")
    with at_base.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO users (email, name, password_hash) VALUES ('a@b.co', 'A', 'h')")
        )
    command.downgrade(alembic_config(), "0001")
    assert current_revision(str(at_base.url.render_as_string(hide_password=False))) == "0001"
    inspector = sa.inspect(at_base)
    assert not TABLES & set(inspector.get_table_names())
    assert enum_labels(at_base, "user_role") == []
    assert enum_labels(at_base, "invite_kind") == []
    assert not citext_installed(at_base)  # created by 0002, unused: dropped
    command.upgrade(alembic_config(), "head")
    assert set(sa.inspect(at_base).get_table_names()) >= TABLES
    command.check(alembic_config())


def test_downgrade_keeps_citext_that_existed_before(at_base: sa.Engine) -> None:
    with at_base.begin() as conn:
        conn.execute(sa.text("CREATE EXTENSION citext"))
    command.upgrade(alembic_config(), "head")
    command.downgrade(alembic_config(), "0001")
    assert citext_installed(at_base)


def test_downgrade_keeps_citext_still_used_elsewhere(at_base: sa.Engine) -> None:
    command.upgrade(alembic_config(), "head")
    with at_base.begin() as conn:
        conn.execute(sa.text("CREATE TABLE other_module (tag citext)"))
    command.downgrade(alembic_config(), "0001")
    assert citext_installed(at_base)
    assert "other_module" in sa.inspect(at_base).get_table_names()


def test_offline_sql_contains_identity_ddl(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0001:0002", sql=True)
    out = capsys.readouterr().out
    assert "CREATE EXTENSION citext" in out
    assert "CREATE TYPE user_role AS ENUM ('admin', 'member')" in out
    assert "CREATE UNIQUE INDEX ix_invites_active_email_kind" in out
    command.downgrade(alembic_config(), "0002:0001", sql=True)
    out = capsys.readouterr().out
    assert "DROP TABLE users" in out
    assert "DROP TYPE user_role" in out
