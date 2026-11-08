"""Migration 0045, the document kind `offer_notes`: up (notes can be stored, one per posting),
down (the notes go with their versions, every other kind stays, the checks of 0015 still hold),
up again, and twice in a row."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command

from tests.documents.test_migration_0022 import _document, _labels, _version
from tests.ingestion.test_migration import add_user, at_base, at_head, url
from tests.test_migrations import alembic_config, current_revision

__all__ = ["at_base", "at_head"]


def test_0045_up_down_up(at_head: sa.Engine) -> None:
    assert "offer_notes" in _labels(at_head)
    before = _labels(at_head)
    with at_head.begin() as conn:
        user = add_user(conn, "e2e-docs-0045@example.org")
        posting = conn.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title) "
                "VALUES (:u, 'example-0045', 'Example Robotics', 'Engineer') RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
        cv = _document(conn, user, posting, "cv")
        notes = _document(conn, user, posting, "offer_notes")
        _version(conn, user, notes)
        with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
            _document(conn, user, posting, "offer_notes")  # one per posting
    # One step back, to 0045's parent: earlier revisions (0043's interview_prep) stay.
    command.downgrade(alembic_config(), "0044")
    assert current_revision(url(at_head)) == "0044"
    assert _labels(at_head) == [label for label in before if label != "offer_notes"]
    with at_head.begin() as conn:
        kinds = conn.execute(sa.text("SELECT kind::text FROM documents")).scalars().all()
        assert kinds == ["cv"]
        assert conn.execute(sa.text("SELECT count(*) FROM document_versions")).scalar_one() == 0
        with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
            conn.execute(  # the check of 0015 holds on the new type
                sa.text(
                    "UPDATE documents SET scope = 'base', posting_id = NULL, name = 'n', "
                    "base_source = 'built', kind = 'letter' WHERE id = :d"
                ),
                {"d": cv},
            )
    command.upgrade(alembic_config(), "head")
    command.upgrade(alembic_config(), "head")
    assert _labels(at_head) == before


def test_0045_offline_sql(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0037:0045", sql=True)
    assert (
        "ALTER TYPE document_kind ADD VALUE IF NOT EXISTS 'offer_notes'" in capsys.readouterr().out
    )
