"""Migration 0051, the badge of what is outside the criteria (jobs J30): up (a posting of the
release before has no badge, no stated contract and the main search: no backfill), a badge
written, down (the three columns go, the posting stays), up again, and `alembic check`."""

import sqlalchemy as sa
from alembic import command

from tests.jobs.test_migration import add_user, at_base, at_head, url
from tests.test_migrations import alembic_config, current_revision

__all__ = ["at_base", "at_head"]

COLUMNS = {"outside_criteria", "contract_stated", "criteria_search_id"}


def _add_job(conn: sa.Connection, user: str) -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title, contract_type) "
                "VALUES (:u, 'example-robotics-data-analyst', 'Example Robotics', "
                "'Data analyst', 'Freelance') RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
    )


def test_0051_up_down_up(at_head: sa.Engine) -> None:
    command.downgrade(alembic_config(), "0050")
    with at_head.begin() as conn:
        user = add_user(conn, "e2e-badge-0051@example.org")
        before = _add_job(conn, user)
    command.upgrade(alembic_config(), "head")
    with at_head.begin() as conn:
        read = sa.text(
            "SELECT outside_criteria, contract_stated, criteria_search_id FROM jobs WHERE id = :j"
        )
        assert tuple(conn.execute(read, {"j": before}).one()) == (None, None, None)
        conn.execute(
            sa.text(
                "UPDATE jobs SET outside_criteria = ARRAY['contract', 'older'], "
                "contract_stated = true WHERE id = :j"
            ),
            {"j": before},
        )
        assert conn.execute(read, {"j": before}).one()[:2] == (["contract", "older"], True)
    command.downgrade(alembic_config(), "0050")
    assert current_revision(url(at_head)) == "0050"
    columns = {c["name"] for c in sa.inspect(at_head).get_columns("jobs")}
    assert not COLUMNS & columns
    with at_head.begin() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one() == 1
    command.upgrade(alembic_config(), "head")
    assert {c["name"] for c in sa.inspect(at_head).get_columns("jobs")} >= COLUMNS
    command.check(alembic_config())
