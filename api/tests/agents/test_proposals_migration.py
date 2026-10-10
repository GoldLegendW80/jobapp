"""Migration 0008_proposals against a real Postgres: columns, enums, indexes, constraints (decided
exactly unless pending, payload an object, lengths), cascades (user deleted, run deleted), down
to 0007 and up again, `alembic check`, offline SQL."""

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import DataError, IntegrityError

from app.agents.models import ProposalDecider, ProposalStatus
from tests.agents.test_runs_migration import add_run, add_user, at_head
from tests.test_migrations import alembic_config

__all__ = ["at_head"]


def add_proposal(conn: sa.Connection, user: str, **values: object) -> str:
    row = {
        "u": user,
        "r": None,
        "t": "job",
        "k": "job_note",
        "p": '{"text": "hi"}',
        "s": "pending",
        "d": None,
        "at": None,
        **values,
    }
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO proposals (user_id, run_id, target, kind, payload, status, "
                "decided_by, decided_at) VALUES (:u, :r, :t, :k, CAST(:p AS jsonb), "
                "CAST(:s AS proposal_status), CAST(:d AS proposal_decider), :at) RETURNING id"
            ),
            row,
        ).scalar_one()
    )


def test_schema(at_head: sa.Engine) -> None:
    inspector = sa.inspect(at_head)
    columns = {c["name"]: c for c in inspector.get_columns("proposals")}
    assert set(columns) == {
        "id",
        "user_id",
        "run_id",
        "target",
        "target_id",
        "kind",
        "payload",
        "status",
        "decided_by",
        "decided_at",
        "edited_value",
        "reason",
        "created_at",
    }
    assert columns["run_id"]["nullable"] is True
    assert columns["target_id"]["nullable"] is True
    indexes = {i["name"] for i in inspector.get_indexes("proposals")}
    assert indexes == {
        "ix_proposals_user_id_created_at",
        "ix_proposals_user_id_target_target_id",
        "ix_proposals_run_id",
    }
    with at_head.connect() as conn:
        statuses = conn.execute(
            sa.text("SELECT unnest(enum_range(NULL::proposal_status))::text")
        ).scalars()
        assert list(statuses) == [status.value for status in ProposalStatus]
        deciders = conn.execute(
            sa.text("SELECT unnest(enum_range(NULL::proposal_decider))::text")
        ).scalars()
        assert list(deciders) == [decider.value for decider in ProposalDecider]


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ({"t": ""}, IntegrityError),
        ({"t": "x" * 33}, IntegrityError),
        ({"k": ""}, IntegrityError),
        ({"k": "k" * 65}, IntegrityError),
        ({"p": "[]"}, IntegrityError),
        ({"s": "maybe"}, DataError),
        ({"d": "robot"}, DataError),
        # decided_by and decided_at are set exactly when the proposal is not pending
        ({"s": "accepted"}, IntegrityError),
        ({"s": "accepted", "d": "user"}, IntegrityError),
        ({"s": "pending", "d": "user", "at": "2026-10-10T10:00:00Z"}, IntegrityError),
        ({"u": "00000000-0000-4000-8000-000000000999"}, IntegrityError),
        ({"r": "00000000-0000-4000-8000-000000000999"}, IntegrityError),
    ],
)
def test_constraints(at_head: sa.Engine, values: dict[str, object], error: type[Exception]) -> None:
    with at_head.begin() as conn:
        user = add_user(conn)
    with pytest.raises(error), at_head.begin() as conn:
        add_proposal(conn, user, **values)


def test_system_proposal_without_run_and_cascades(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        user = add_user(conn)
        run = add_run(conn, user)
        # SPEC-mail: a rule-based change, applied at once, has no run.
        add_proposal(
            conn, user, s="accepted", d="system", at="2026-10-10T10:00:00Z", k="mail_status"
        )
        from_run = add_proposal(conn, user, r=run)
        conn.execute(sa.text("DELETE FROM agent_runs WHERE id = :r"), {"r": run})
        assert (
            conn.execute(
                sa.text("SELECT run_id FROM proposals WHERE id = :p"), {"p": from_run}
            ).scalar_one()
            is None
        )
        conn.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": user})
        assert conn.execute(sa.text("SELECT count(*) FROM proposals")).scalar_one() == 0


def test_down_to_0007_and_up_again(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        add_proposal(conn, add_user(conn))
    command.downgrade(alembic_config(), "0007")
    inspector = sa.inspect(at_head)
    assert "proposals" not in inspector.get_table_names()
    assert "agent_runs" in inspector.get_table_names()
    with at_head.connect() as conn:
        count = conn.execute(
            sa.text(
                "SELECT count(*) FROM pg_type "
                "WHERE typname IN ('proposal_status', 'proposal_decider')"
            )
        ).scalar_one()
        assert count == 0
    command.upgrade(alembic_config(), "head")
    command.check(alembic_config())


def test_offline_sql(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0007:0008", sql=True)
    out = capsys.readouterr().out
    assert "CREATE TYPE proposal_status AS ENUM" in out
    assert "CREATE TYPE proposal_decider AS ENUM" in out
    assert "CREATE TABLE proposals" in out
