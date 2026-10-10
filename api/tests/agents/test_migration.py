"""Migration 0006_model_credentials against a real Postgres: schema, enum, constraints, cascade,
down to 0005 and up again, and `alembic check` against the models."""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import DataError, IntegrityError

from app.agents.models import CredentialKind
from app.config import get_settings
from tests.test_migrations import HEAD, alembic_config, current_revision


@pytest.fixture
def at_head(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.Engine]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.downgrade(alembic_config(), "base")
    command.upgrade(alembic_config(), "head")
    engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    yield engine
    command.downgrade(alembic_config(), "base")
    engine.dispose()


def add_user(conn: sa.Connection, email: str) -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO users (email, name, password_hash) VALUES (:e, 'U', 'h') RETURNING id"
            ),
            {"e": email},
        ).scalar_one()
    )


def add_credential(conn: sa.Connection, user: str, **values: object) -> None:
    row = {
        "u": user,
        "k": "anthropic_api_key",
        "s": b"\x00" * 40,
        "v": 7,
        "l": "abcd",
        **values,
    }
    conn.execute(
        sa.text(
            "INSERT INTO model_credentials (user_id, kind, secret_encrypted, key_version, last4) "
            "VALUES (:u, CAST(:k AS model_credential_kind), :s, :v, :l)"
        ),
        row,
    )


def test_schema(at_head: sa.Engine) -> None:
    assert current_revision(at_head.url.render_as_string(hide_password=False)) == HEAD
    inspector = sa.inspect(at_head)
    columns = {c["name"]: c for c in inspector.get_columns("model_credentials")}
    assert set(columns) == {
        "user_id",
        "kind",
        "secret_encrypted",
        "key_version",
        "last4",
        "verified_at",
        "created_at",
        "updated_at",
    }
    assert columns["verified_at"]["nullable"] is True
    assert all(c["nullable"] is False for n, c in columns.items() if n != "verified_at")
    assert inspector.get_pk_constraint("model_credentials")["constrained_columns"] == ["user_id"]
    with at_head.connect() as conn:
        labels = conn.execute(
            sa.text("SELECT unnest(enum_range(NULL::model_credential_kind))::text")
        ).scalars()
        assert list(labels) == [kind.value for kind in CredentialKind]


def test_one_row_per_user_and_cascade(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        user = add_user(conn, "a@example.org")
        add_credential(conn, user)
    with pytest.raises(IntegrityError), at_head.begin() as conn:
        add_credential(conn, user)
    with at_head.begin() as conn:
        conn.execute(sa.text("DELETE FROM users"))
        assert conn.execute(sa.text("SELECT count(*) FROM model_credentials")).scalar_one() == 0


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ({"s": b"\x00" * 28}, IntegrityError),
        ({"l": "abc"}, IntegrityError),
        ({"l": "abcde"}, IntegrityError),
        ({"k": "openai"}, DataError),
        ({"u": "00000000-0000-4000-8000-000000000999"}, IntegrityError),
    ],
)
def test_constraints(at_head: sa.Engine, values: dict[str, object], error: type[Exception]) -> None:
    with at_head.begin() as conn:
        user = add_user(conn, "a@example.org")
    with pytest.raises(error), at_head.begin() as conn:
        add_credential(conn, user, **values)


def test_down_to_0005_and_up_again(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        add_credential(conn, add_user(conn, "a@example.org"))
    command.downgrade(alembic_config(), "0005")
    inspector = sa.inspect(at_head)
    assert "model_credentials" not in inspector.get_table_names()
    assert "users" in inspector.get_table_names()
    with at_head.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM pg_type WHERE typname = 'model_credential_kind'")
            ).scalar_one()
            == 0
        )
    command.upgrade(alembic_config(), "head")
    command.check(alembic_config())


def test_offline_sql(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0005:0006", sql=True)
    out = capsys.readouterr().out
    assert "CREATE TYPE model_credential_kind AS ENUM" in out
    assert "CREATE TABLE model_credentials" in out
