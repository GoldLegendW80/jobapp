"""Migration 0007_agent_runs against a real Postgres: tables, enum, indexes, constraints,
cascades, down to 0006 and up again, `alembic check`, offline SQL."""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import DataError, IntegrityError

from app.agents.models import RunStatus
from app.config import get_settings
from tests.test_migrations import alembic_config


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


def add_user(conn: sa.Connection, email: str = "a@example.org") -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO users (email, name, password_hash) VALUES (:e, 'U', 'h') RETURNING id"
            ),
            {"e": email},
        ).scalar_one()
    )


def add_run(conn: sa.Connection, user: str, **values: object) -> str:
    row = {"u": user, "a": "echo", "s": "queued", "i": "{}", **values}
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO agent_runs (user_id, agent, status, input) "
                "VALUES (:u, :a, CAST(:s AS agent_run_status), CAST(:i AS jsonb)) RETURNING id"
            ),
            row,
        ).scalar_one()
    )


def add_event(conn: sa.Connection, run: str, seq: int, data: str = "{}") -> None:
    conn.execute(
        sa.text(
            "INSERT INTO agent_events (run_id, seq, kind, data) "
            "VALUES (:r, :s, 'text', CAST(:d AS jsonb))"
        ),
        {"r": run, "s": seq, "d": data},
    )


def test_schema(at_head: sa.Engine) -> None:
    inspector = sa.inspect(at_head)
    runs = {c["name"]: c for c in inspector.get_columns("agent_runs")}
    assert set(runs) == {
        "id",
        "user_id",
        "agent",
        "job_id",
        "status",
        "input",
        "output",
        "error",
        "model",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "cancel_requested_at",
        "started_at",
        "finished_at",
        "created_at",
    }
    events = {c["name"] for c in inspector.get_columns("agent_events")}
    assert events == {"id", "run_id", "seq", "at", "kind", "data"}
    indexes = {i["name"]: i for i in inspector.get_indexes("agent_runs")}
    assert indexes["ix_agent_runs_user_id_created_at"]["column_names"][0] == "user_id"
    assert "ix_agent_runs_status_active" in indexes
    assert "ix_agent_runs_job_id" in indexes
    uniques = {
        u["name"]: u["column_names"] for u in inspector.get_unique_constraints("agent_events")
    }
    assert uniques == {"uq_agent_events_run_id_seq": ["run_id", "seq"]}
    with at_head.connect() as conn:
        labels = conn.execute(
            sa.text("SELECT unnest(enum_range(NULL::agent_run_status))::text")
        ).scalars()
        assert list(labels) == [status.value for status in RunStatus]


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ({"a": ""}, IntegrityError),
        ({"a": "x" * 65}, IntegrityError),
        ({"i": "[]"}, IntegrityError),
        ({"s": "paused"}, DataError),
        ({"u": "00000000-0000-4000-8000-000000000999"}, IntegrityError),
    ],
)
def test_run_constraints(
    at_head: sa.Engine, values: dict[str, object], error: type[Exception]
) -> None:
    with at_head.begin() as conn:
        user = add_user(conn)
    with pytest.raises(error), at_head.begin() as conn:
        add_run(conn, user, **values)


def test_usage_and_event_constraints(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        run = add_run(conn, add_user(conn))
    for statement in (
        "UPDATE agent_runs SET input_tokens = -1",
        "UPDATE agent_runs SET cost_usd = -0.01",
    ):
        with pytest.raises(IntegrityError), at_head.begin() as conn:
            conn.execute(sa.text(statement))
    with at_head.begin() as conn:
        add_event(conn, run, 1)
    for seq, data in ((1, "{}"), (0, "{}"), (2, "[1]")):
        with pytest.raises(IntegrityError), at_head.begin() as conn:
            add_event(conn, run, seq, data)


def test_cascades(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        user = add_user(conn)
        run = add_run(conn, user)
        add_event(conn, run, 1)
        conn.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": user})
        assert conn.execute(sa.text("SELECT count(*) FROM agent_runs")).scalar_one() == 0
        assert conn.execute(sa.text("SELECT count(*) FROM agent_events")).scalar_one() == 0


def test_down_to_0006_and_up_again(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        add_event(conn, add_run(conn, add_user(conn)), 1)
    command.downgrade(alembic_config(), "0006")
    inspector = sa.inspect(at_head)
    assert "agent_runs" not in inspector.get_table_names()
    assert "agent_events" not in inspector.get_table_names()
    assert "model_credentials" in inspector.get_table_names()
    with at_head.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM pg_type WHERE typname = 'agent_run_status'")
            ).scalar_one()
            == 0
        )
    command.upgrade(alembic_config(), "head")
    command.check(alembic_config())


def test_offline_sql(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0006:0007", sql=True)
    out = capsys.readouterr().out
    assert "CREATE TYPE agent_run_status AS ENUM" in out
    assert "CREATE TABLE agent_runs" in out
    assert "CREATE TABLE agent_events" in out
