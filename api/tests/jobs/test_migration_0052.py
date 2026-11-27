"""Migration 0052, a posting seen only in a list (jobs J31): up (a posting of the release before
has no list address: no backfill), a list address written, down (the column goes, the posting
stays), up again, and `alembic check`."""

import sqlalchemy as sa
from alembic import command

from tests.jobs.test_migration import add_user, at_base, at_head, url
from tests.test_migrations import alembic_config, current_revision

__all__ = ["at_base", "at_head"]


def test_0052_up_down_up(at_head: sa.Engine) -> None:
    command.downgrade(alembic_config(), "0051")
    with at_head.begin() as conn:
        user = add_user(conn, "e2e-listed-0052@example.org")
        before = conn.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title) VALUES (:u, "
                "'example-robotics-data-analyst', 'Example Robotics', 'Data analyst') RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
    command.upgrade(alembic_config(), "head")
    read = sa.text("SELECT list_url FROM jobs WHERE id = :j")
    with at_head.begin() as conn:
        assert conn.execute(read, {"j": before}).scalar_one() is None
        listed = "https://aggregator.example.test/emplois?q=data"
        conn.execute(
            sa.text("UPDATE jobs SET list_url = :l WHERE id = :j"), {"l": listed, "j": before}
        )
        assert conn.execute(read, {"j": before}).scalar_one() == listed
    command.downgrade(alembic_config(), "0051")
    assert current_revision(url(at_head)) == "0051"
    assert "list_url" not in {c["name"] for c in sa.inspect(at_head).get_columns("jobs")}
    with at_head.begin() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one() == 1
    command.upgrade(alembic_config(), "head")
    assert "list_url" in {c["name"] for c in sa.inspect(at_head).get_columns("jobs")}
    command.check(alembic_config())
