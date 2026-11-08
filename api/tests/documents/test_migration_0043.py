"""Migration 0043, the document kind `interview_prep` and the chat channel `practice`: up (a
preparation and its practice message can be stored), down (they go, the CVs and their messages
stay, the checks of 0015 and the channel's default still hold), up again, and twice in a row."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command

from tests.documents.test_migration_0022 import _document
from tests.ingestion.test_migration import add_user, at_base, at_head, url
from tests.test_migrations import alembic_config, current_revision

__all__ = ["at_base", "at_head"]


def _labels(engine: sa.Engine, name: str) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                sa.text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = :n ORDER BY e.enumsortorder"
                ),
                {"n": name},
            ).scalars()
        )


def _message(conn: sa.Connection, user: str, document: str, channel: str | None) -> None:
    columns, values = ("channel, ", ":c, ") if channel else ("", "")
    conn.execute(
        sa.text(
            f"INSERT INTO document_messages (document_id, user_id, {columns}role, text, at) "
            f"VALUES (:d, :u, {values}'user', 'Hello', now())"
        ),
        {"d": document, "u": user, "c": channel},
    )


def test_0043_up_down_up(at_head: sa.Engine) -> None:
    assert "interview_prep" in _labels(at_head, "document_kind")
    assert "practice" in _labels(at_head, "message_channel")
    with at_head.begin() as conn:
        user = add_user(conn, "e2e-docs-0043@example.org")
        posting = conn.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title) "
                "VALUES (:u, 'example-0043', 'Example Robotics', 'Engineer') RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
        cv = _document(conn, user, posting, "cv")
        _message(conn, user, cv, None)
        prep = _document(conn, user, posting, "interview_prep")
        _message(conn, user, prep, "practice")
        with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
            _document(conn, user, posting, "interview_prep")  # one per posting
    command.downgrade(alembic_config(), "0037")
    assert current_revision(url(at_head)) == "0037"
    assert _labels(at_head, "document_kind") == ["cv", "letter", "proposal"]
    assert _labels(at_head, "message_channel") == ["content", "design"]
    with at_head.begin() as conn:
        assert conn.execute(sa.text("SELECT kind::text FROM documents")).scalars().all() == ["cv"]
        channels = conn.execute(sa.text("SELECT channel::text FROM document_messages")).scalars()
        assert channels.all() == ["content"]
        _message(conn, user, cv, None)  # the default still holds
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
    assert "interview_prep" in _labels(at_head, "document_kind")
    assert "practice" in _labels(at_head, "message_channel")
