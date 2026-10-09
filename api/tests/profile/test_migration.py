"""Migration 0003_profile against a real Postgres: schema, constraints, up/down/up with data,
and `alembic check` against the models."""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from tests.test_migrations import HEAD, alembic_config, current_revision

TABLES = {"profiles", "profile_revisions"}


@pytest.fixture
def at_base(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.Engine]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.downgrade(alembic_config(), "base")
    engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    yield engine
    command.downgrade(alembic_config(), "base")
    engine.dispose()


def url(engine: sa.Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def add_user(conn: sa.Connection, email: str) -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO users (email, name, password_hash) VALUES (:e, 'U', 'h') RETURNING id"
            ),
            {"e": email},
        ).scalar_one()
    )


def test_upgrade_creates_the_profile_schema(at_base: sa.Engine) -> None:
    command.upgrade(alembic_config(), "head")
    inspector = sa.inspect(at_base)
    assert set(inspector.get_table_names()) >= TABLES

    columns = {c["name"]: c for c in inspector.get_columns("profiles")}
    assert set(columns) == {"user_id", "sections", "photo", "photo_mime", "revision", "updated_at"}
    assert type(columns["sections"]["type"]).__name__ == "JSONB"
    assert not columns["sections"]["nullable"]
    assert "'{}'::jsonb" in columns["sections"]["default"]
    assert type(columns["photo"]["type"]).__name__ == "BYTEA"
    assert columns["photo"]["nullable"]
    assert columns["photo_mime"]["nullable"]
    assert not columns["revision"]["nullable"]
    assert columns["revision"]["default"] == "0"
    assert columns["updated_at"]["type"].timezone is True
    assert inspector.get_pk_constraint("profiles")["constrained_columns"] == ["user_id"]

    columns = {c["name"]: c for c in inspector.get_columns("profile_revisions")}
    assert set(columns) == {"id", "user_id", "revision", "section", "data", "created_at"}
    assert isinstance(columns["id"]["type"], sa.BigInteger)
    assert "nextval" in columns["id"]["default"]  # bigserial
    assert type(columns["data"]["type"]).__name__ == "JSONB"
    assert columns["created_at"]["type"].timezone is True

    for table in TABLES:
        (fk,) = inspector.get_foreign_keys(table)
        assert fk["referred_table"] == "users"
        assert fk["options"]["ondelete"] == "CASCADE"

    uniques = {u["name"]: u for u in inspector.get_unique_constraints("profile_revisions")}
    assert uniques["uq_profile_revisions_user_id_revision"]["column_names"] == [
        "user_id",
        "revision",
    ]
    indexes = {ix["name"]: ix for ix in inspector.get_indexes("profile_revisions")}
    index = indexes["ix_profile_revisions_user_id_section_revision"]
    assert not index["unique"]
    assert index["column_names"] == ["user_id", "section", "revision"]
    assert index["column_sorting"] == {"revision": ("desc",)}


def test_constraints_hold(at_base: sa.Engine) -> None:
    command.upgrade(alembic_config(), "head")
    with at_base.begin() as conn:
        user = add_user(conn, "a@b.co")
        conn.execute(sa.text("INSERT INTO profiles (user_id) VALUES (:u)"), {"u": user})
        row = conn.execute(sa.text("SELECT sections, revision FROM profiles")).one()
        assert row == ({}, 0)
    bad_statements = [
        "UPDATE profiles SET revision = -1",
        "UPDATE profiles SET sections = '[]'::jsonb",
        "UPDATE profiles SET sections = NULL",
        "UPDATE profiles SET photo = '\\x00'::bytea",  # photo without its type
        "UPDATE profiles SET photo = '\\x00'::bytea, photo_mime = 'image/svg+xml'",
        "INSERT INTO profiles (user_id) SELECT id FROM users",  # one profile per user
        "INSERT INTO profiles (user_id) VALUES (gen_random_uuid())",  # no such user
        "INSERT INTO profile_revisions (user_id, revision, section, data) "
        "SELECT id, 0, 'facts', '{}' FROM users",
    ]
    for statement in bad_statements:
        with pytest.raises(IntegrityError), at_base.begin() as conn:
            conn.execute(sa.text(statement))
    with at_base.begin() as conn:
        insert = (
            "INSERT INTO profile_revisions (user_id, revision, section, data) "
            "SELECT id, 1, 'facts', '{}' FROM users"
        )
        conn.execute(sa.text(insert))
    with pytest.raises(IntegrityError), at_base.begin() as conn:
        conn.execute(sa.text(insert))  # unique (user_id, revision)
    with at_base.begin() as conn:
        conn.execute(
            sa.text("UPDATE profiles SET photo = '\\x00'::bytea, photo_mime = 'image/png'")
        )
        conn.execute(sa.text("DELETE FROM users"))
        assert conn.execute(sa.text("SELECT count(*) FROM profiles")).scalar_one() == 0
        assert conn.execute(sa.text("SELECT count(*) FROM profile_revisions")).scalar_one() == 0


def test_up_down_up_with_data(at_base: sa.Engine) -> None:
    command.upgrade(alembic_config(), "head")
    with at_base.begin() as conn:
        user = add_user(conn, "a@b.co")
        conn.execute(
            sa.text("INSERT INTO profiles (user_id, sections, revision) VALUES (:u, :s, 1)"),
            {"u": user, "s": '{"facts": {"blocks": []}}'},
        )
        conn.execute(
            sa.text(
                "INSERT INTO profile_revisions (user_id, revision, section, data) "
                "VALUES (:u, 1, 'facts', '{\"blocks\": []}')"
            ),
            {"u": user},
        )
    command.downgrade(alembic_config(), "0002")
    assert current_revision(url(at_base)) == "0002"
    names = set(sa.inspect(at_base).get_table_names())
    assert not TABLES & names
    assert "users" in names  # identity untouched
    with at_base.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM users")).scalar_one() == 1
    command.upgrade(alembic_config(), "head")
    assert current_revision(url(at_base)) == HEAD
    assert set(sa.inspect(at_base).get_table_names()) >= TABLES
    command.check(alembic_config())


def test_offline_sql_has_the_profile_tables(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0002:0003", sql=True)
    out = capsys.readouterr().out
    assert "CREATE TABLE profiles" in out
    assert "CREATE TABLE profile_revisions" in out
    assert "revision DESC" in out
    command.downgrade(alembic_config(), "0003:0002", sql=True)
    out = capsys.readouterr().out
    assert "DROP TABLE profile_revisions" in out
    assert "DROP TABLE profiles" in out
