"""Migration 0046, the refusals of an extraction step: up (an object, `{}` by default, a row of the
release before it is written without it), down (the column goes, the step stays), up again, and
the offline SQL."""

import pytest
import sqlalchemy as sa
from alembic import command

from tests.ingestion.test_migration import at_base, at_head, url
from tests.ingestion.test_runs_migration import seeded
from tests.test_migrations import alembic_config, current_revision

__all__ = ["at_base", "at_head"]

COUNTS = '{"skills": {"unknown_field": 14}}'


def _refusals(conn: sa.Connection, step: str) -> object:
    return conn.execute(
        sa.text("SELECT refusals FROM ingestion_run_steps WHERE id = :s"), {"s": step}
    ).scalar_one()


def test_0046_up_down_up(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        _, _, _, step = seeded(conn, "e2e-ingest-0046@example.org")
        assert _refusals(conn, step) == {}
        conn.execute(
            sa.text("UPDATE ingestion_run_steps SET refusals = CAST(:r AS jsonb) WHERE id = :s"),
            {"r": COUNTS, "s": step},
        )
        assert _refusals(conn, step) == {"skills": {"unknown_field": 14}}
        with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
            conn.execute(
                sa.text("UPDATE ingestion_run_steps SET refusals = '[]'::jsonb WHERE id = :s"),
                {"s": step},
            )
    command.downgrade(alembic_config(), "0039")
    assert current_revision(url(at_head)) == "0039"
    columns = {c["name"] for c in sa.inspect(at_head).get_columns("ingestion_run_steps")}
    assert "refusals" not in columns
    assert {"refused_count", "two_digit_year_count", "items_count"} <= columns
    with at_head.begin() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM ingestion_run_steps")).scalar_one() == 1
    command.upgrade(alembic_config(), "head")
    command.upgrade(alembic_config(), "head")
    with at_head.begin() as conn:
        assert _refusals(conn, step) == {}
    command.check(alembic_config())


def test_0046_offline_sql(
    at_base: sa.Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    command.upgrade(alembic_config(), "0039:0046", sql=True)
    up = capsys.readouterr().out
    assert "ALTER TABLE ingestion_run_steps ADD COLUMN refusals JSONB" in up
    assert "DEFAULT '{}'::jsonb NOT NULL" in up
    assert "ck_ingestion_run_steps_refusals_is_object" in up
    command.downgrade(alembic_config(), "0046:0039", sql=True)
    down = capsys.readouterr().out
    assert "DROP CONSTRAINT ck_ingestion_run_steps_refusals_is_object" in down
    assert "ALTER TABLE ingestion_run_steps DROP COLUMN refusals" in down
