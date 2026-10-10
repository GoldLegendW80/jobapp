"""Migration 0009_agent_threads against a real Postgres: columns (no sdk_session_id), enum,
indexes, constraints (title length, content an object, one thread per job and user), cascades
(user, job, thread deleted; run deleted sets run_id NULL), down to 0008 and up again,
`alembic check`, offline SQL."""

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import DataError, IntegrityError

from app.agents.models import MessageRole
from tests.agents.test_runs_migration import add_run, add_user, at_head
from tests.test_migrations import alembic_config

__all__ = ["at_head"]


def add_job(conn: sa.Connection, user: str, company: str = "Acme") -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title) "
                "VALUES (:u, gen_random_uuid()::text, :c, 'Engineer') RETURNING id"
            ),
            {"u": user, "c": company},
        ).scalar_one()
    )


def add_thread(conn: sa.Connection, user: str, job: str | None, title: str = "Chat") -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO agent_threads (user_id, job_id, title) VALUES (:u, :j, :t) "
                "RETURNING id"
            ),
            {"u": user, "j": job, "t": title},
        ).scalar_one()
    )


def add_message(
    conn: sa.Connection, thread: str, role: str = "user", content: str = '{"text": "hi"}', run=None
) -> int:
    return int(
        conn.execute(
            sa.text(
                "INSERT INTO agent_messages (thread_id, role, content, run_id) VALUES "
                "(:t, CAST(:r AS agent_message_role), CAST(:c AS jsonb), :run) RETURNING id"
            ),
            {"t": thread, "r": role, "c": content, "run": run},
        ).scalar_one()
    )


def count(conn: sa.Connection, table: str) -> int:
    return int(conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())


def test_schema(at_head: sa.Engine) -> None:
    inspector = sa.inspect(at_head)
    threads = {c["name"] for c in inspector.get_columns("agent_threads")}
    assert threads == {"id", "user_id", "job_id", "title", "created_at", "updated_at"}
    assert "sdk_session_id" not in threads
    messages = {c["name"]: c for c in inspector.get_columns("agent_messages")}
    assert set(messages) == {"id", "thread_id", "role", "content", "run_id", "at"}
    assert messages["run_id"]["nullable"] is True
    assert {i["name"] for i in inspector.get_indexes("agent_threads")} == {
        "ix_agent_threads_user_id_updated_at",
        "uq_agent_threads_user_id_job_id",
    }
    assert {i["name"] for i in inspector.get_indexes("agent_messages")} == {
        "ix_agent_messages_thread_id_id",
        "ix_agent_messages_run_id",
    }
    with at_head.connect() as conn:
        roles = conn.execute(
            sa.text("SELECT unnest(enum_range(NULL::agent_message_role))::text")
        ).scalars()
        assert list(roles) == [role.value for role in MessageRole]


@pytest.mark.parametrize(("title", "error"), [("", IntegrityError), ("t" * 201, IntegrityError)])
def test_thread_title_length(at_head: sa.Engine, title: str, error: type[Exception]) -> None:
    with at_head.begin() as conn:
        user = add_user(conn)
    with pytest.raises(error), at_head.begin() as conn:
        add_thread(conn, user, None, title)


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ({"content": "[]"}, IntegrityError),
        ({"content": '"text"'}, IntegrityError),
        ({"role": "system"}, DataError),
        ({"run": "00000000-0000-4000-8000-000000000999"}, IntegrityError),
    ],
)
def test_message_constraints(
    at_head: sa.Engine, values: dict[str, str], error: type[Exception]
) -> None:
    with at_head.begin() as conn:
        thread = add_thread(conn, add_user(conn), None)
    with pytest.raises(error), at_head.begin() as conn:
        add_message(conn, thread, **values)


def test_one_thread_per_job_and_user(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        alice, bob = add_user(conn, "a@example.org"), add_user(conn, "b@example.org")
        job = add_job(conn, alice)
        add_thread(conn, alice, job)
        # Threads without a job are not limited, and another user's thread is another row.
        add_thread(conn, alice, None)
        add_thread(conn, alice, None)
        add_thread(conn, bob, job)
    with pytest.raises(IntegrityError), at_head.begin() as conn:
        add_thread(conn, alice, job)


def test_cascades(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        user = add_user(conn)
        job = add_job(conn, user)
        thread = add_thread(conn, user, job)
        run = add_run(conn, user)
        add_message(conn, thread, run=run)
        add_message(conn, thread, role="assistant", run=run)
        # A deleted run leaves the messages, without their run.
        conn.execute(sa.text("DELETE FROM agent_runs WHERE id = :r"), {"r": run})
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM agent_messages WHERE run_id IS NULL")
            ).scalar_one()
            == 2
        )
        # A deleted thread takes its messages along.
        other = add_thread(conn, user, None)
        add_message(conn, other)
        conn.execute(sa.text("DELETE FROM agent_threads WHERE id = :t"), {"t": other})
        assert count(conn, "agent_messages") == 2
        # A deleted job takes its thread and messages along.
        conn.execute(sa.text("DELETE FROM jobs WHERE id = :j"), {"j": job})
        assert count(conn, "agent_threads") == 0
        assert count(conn, "agent_messages") == 0
        # A deleted user takes everything along.
        add_message(conn, add_thread(conn, user, None))
        conn.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": user})
        assert count(conn, "agent_threads") == 0
        assert count(conn, "agent_messages") == 0


def test_down_to_0008_and_up_again(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        add_message(conn, add_thread(conn, add_user(conn), None))
    command.downgrade(alembic_config(), "0008")
    inspector = sa.inspect(at_head)
    assert "agent_threads" not in inspector.get_table_names()
    assert "agent_messages" not in inspector.get_table_names()
    assert "proposals" in inspector.get_table_names()
    with at_head.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM pg_type WHERE typname = 'agent_message_role'")
            ).scalar_one()
            == 0
        )
    command.upgrade(alembic_config(), "head")
    command.check(alembic_config())


def test_offline_sql(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0008:0009", sql=True)
    out = capsys.readouterr().out
    assert "CREATE TYPE agent_message_role AS ENUM" in out
    assert "CREATE TABLE agent_threads" in out
    assert "CREATE TABLE agent_messages" in out
    assert "sdk_session_id" not in out
