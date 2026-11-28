"""Migration 0055, the career and culture reading (jobs J38): up (a posting of the release before
has none: no backfill), a reading written, down (the column goes, the posting stays), up again,
and `alembic check`."""

import json

import sqlalchemy as sa
from alembic import command

from tests.jobs.test_migration import add_user, at_base, at_head, url
from tests.test_migrations import alembic_config, current_revision

__all__ = ["at_base", "at_head"]


def test_0055_up_down_up(at_head: sa.Engine) -> None:
    command.downgrade(alembic_config(), "0054")
    with at_head.begin() as conn:
        user = add_user(conn, "e2e-match-0055@example.org")
        before = conn.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title) VALUES (:u, "
                "'example-robotics-data-engineer', 'Example Robotics', 'Data engineer') "
                "RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
    command.upgrade(alembic_config(), "head")
    read = sa.text("SELECT match FROM jobs WHERE id = :j")
    with at_head.begin() as conn:
        assert conn.execute(read, {"j": before}).scalar_one() is None
        reading = {
            "career": 82,
            "culture": 64,
            "strengths": ["Moves you to a lead role"],
            "gaps": ["Pays under your salary range"],
            "rated_at": "2026-11-28T09:00:00+00:00",
            "text": "0" * 64,
        }
        conn.execute(
            sa.text("UPDATE jobs SET match = CAST(:m AS jsonb) WHERE id = :j"),
            {"m": json.dumps(reading), "j": before},
        )
        assert conn.execute(read, {"j": before}).scalar_one() == reading
    command.downgrade(alembic_config(), "0054")
    assert current_revision(url(at_head)) == "0054"
    assert "match" not in {c["name"] for c in sa.inspect(at_head).get_columns("jobs")}
    with at_head.begin() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one() == 1
    command.upgrade(alembic_config(), "head")
    assert "match" in {c["name"] for c in sa.inspect(at_head).get_columns("jobs")}
    command.check(alembic_config())
