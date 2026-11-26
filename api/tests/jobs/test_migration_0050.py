"""Migration 0050, saved searches (jobs J32): up (a sweep of the release before reads as the main
search, null), a saved search, a request and a sweep of it written, down (the tables and the
column go, the sweep stays), up again, and `alembic check`."""

import sqlalchemy as sa
from alembic import command

from tests.jobs.test_migration import add_user, at_base, at_head, url
from tests.test_migrations import alembic_config, current_revision

__all__ = ["at_base", "at_head"]


def _add_sweep(conn: sa.Connection, user: str) -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO sweeps (user_id, trigger, status, finished_at) "
                "VALUES (:u, 'manual', 'done', now()) RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
    )


def test_0050_up_down_up(at_head: sa.Engine) -> None:
    command.downgrade(alembic_config(), "0049")
    with at_head.begin() as conn:
        user = add_user(conn, "e2e-searches-0050@example.org")
        before = _add_sweep(conn, user)
    command.upgrade(alembic_config(), "head")
    with at_head.begin() as conn:
        assert (
            conn.execute(
                sa.text("SELECT saved_search_id FROM sweeps WHERE id = :s"), {"s": before}
            ).scalar_one()
            is None
        )
        saved = conn.execute(
            sa.text(
                "INSERT INTO saved_searches (user_id, name, criteria, schedule) "
                "VALUES (:u, 'Data in Lyon', '{}', '{}') RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO sweep_requests (user_id, saved_search_id, trigger) "
                "VALUES (:u, :s, 'manual')"
            ),
            {"u": user, "s": saved},
        )
        conn.execute(
            sa.text("UPDATE sweeps SET saved_search_id = :s WHERE id = :w"),
            {"s": saved, "w": before},
        )
    command.downgrade(alembic_config(), "0049")
    assert current_revision(url(at_head)) == "0049"
    tables = set(sa.inspect(at_head).get_table_names())
    assert not {"saved_searches", "sweep_requests"} & tables
    columns = {c["name"] for c in sa.inspect(at_head).get_columns("sweeps")}
    assert "saved_search_id" not in columns
    with at_head.begin() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM sweeps")).scalar_one() == 1
    command.upgrade(alembic_config(), "head")
    command.check(alembic_config())
