"""Migration 0047, what the posting reader did with each posting (jobs J19): up (a row of the
release before it starts unread with no attempt and no run), down (the three columns go, the row
stays), up again, and the offline SQL."""

import pytest
import sqlalchemy as sa
from alembic import command

from tests.jobs.test_migration import add_user, at_base, at_head, url
from tests.test_migrations import alembic_config, current_revision

__all__ = ["at_base", "at_head"]


def _add_job(conn: sa.Connection, user: str) -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title, posting_text) "
                "VALUES (:u, 'example-labs-analyst', 'Example Labs', 'Analyst', 'A posting.') "
                "RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
    )


def _read(conn: sa.Connection, job: str) -> tuple[object, object, object]:
    row = conn.execute(
        sa.text("SELECT read_at, read_attempts, read_run_id FROM jobs WHERE id = :j"), {"j": job}
    ).one()
    return row.read_at, row.read_attempts, row.read_run_id


def test_0047_up_down_up(at_head: sa.Engine) -> None:
    command.downgrade(alembic_config(), "0046")
    with at_head.begin() as conn:
        job = _add_job(conn, add_user(conn, "e2e-reader-0047@example.org"))
    command.upgrade(alembic_config(), "head")
    with at_head.begin() as conn:
        assert _read(conn, job) == (None, 0, None)
        conn.execute(
            sa.text(
                "UPDATE jobs SET read_at = now(), read_attempts = 3, "
                "read_run_id = gen_random_uuid() WHERE id = :j"
            ),
            {"j": job},
        )
    command.downgrade(alembic_config(), "0046")
    assert current_revision(url(at_head)) == "0046"
    columns = {c["name"] for c in sa.inspect(at_head).get_columns("jobs")}
    assert not {"read_at", "read_attempts", "read_run_id"} & columns
    with at_head.begin() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one() == 1
    command.upgrade(alembic_config(), "head")
    with at_head.begin() as conn:
        assert _read(conn, job) == (None, 0, None)
    command.check(alembic_config())


def test_0047_offline_sql(at_base: sa.Engine, capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(alembic_config(), "0046:0047", sql=True)
    up = capsys.readouterr().out
    assert "ALTER TABLE jobs ADD COLUMN read_at TIMESTAMP WITH TIME ZONE" in up
    assert "ALTER TABLE jobs ADD COLUMN read_attempts SMALLINT DEFAULT '0' NOT NULL" in up
    assert "ALTER TABLE jobs ADD COLUMN read_run_id UUID" in up
    command.downgrade(alembic_config(), "0047:0046", sql=True)
    down = capsys.readouterr().out
    assert "ALTER TABLE jobs DROP COLUMN read_run_id" in down
    assert "ALTER TABLE jobs DROP COLUMN read_attempts" in down
    assert "ALTER TABLE jobs DROP COLUMN read_at" in down
