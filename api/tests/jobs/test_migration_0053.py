"""Migration 0053, the languages a posting asks for (jobs J35): up (a posting of the release before
has none: no backfill, the reader's backlog reads it again), languages written, down (the column
goes, the posting stays), up again, and `alembic check`."""

import json

import sqlalchemy as sa
from alembic import command

from tests.jobs.test_migration import add_user, at_base, at_head, url
from tests.test_migrations import alembic_config, current_revision

__all__ = ["at_base", "at_head"]


def test_0053_up_down_up(at_head: sa.Engine) -> None:
    command.downgrade(alembic_config(), "0052")
    with at_head.begin() as conn:
        user = add_user(conn, "e2e-languages-0053@example.org")
        before = conn.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title) VALUES (:u, "
                "'example-robotics-data-analyst', 'Example Robotics', 'Data analyst') RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
    command.upgrade(alembic_config(), "head")
    read = sa.text("SELECT languages FROM jobs WHERE id = :j")
    with at_head.begin() as conn:
        assert conn.execute(read, {"j": before}).scalar_one() is None
        asked = {
            "written_in": "German",
            "asked": [
                {
                    "name": "German",
                    "level": "verhandlungssicher",
                    "required": True,
                    "alternatives": [],
                    "quote": "Verhandlungssichere Deutschkenntnisse",
                }
            ],
        }
        conn.execute(
            sa.text("UPDATE jobs SET languages = CAST(:l AS jsonb) WHERE id = :j"),
            {"l": json.dumps(asked), "j": before},
        )
        assert conn.execute(read, {"j": before}).scalar_one() == asked
    command.downgrade(alembic_config(), "0052")
    assert current_revision(url(at_head)) == "0052"
    assert "languages" not in {c["name"] for c in sa.inspect(at_head).get_columns("jobs")}
    with at_head.begin() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one() == 1
    command.upgrade(alembic_config(), "head")
    assert "languages" in {c["name"] for c in sa.inspect(at_head).get_columns("jobs")}
    command.check(alembic_config())
