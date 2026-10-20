"""Issue #485: migration 0022, the document kind `proposal`: up (a proposal can be stored), down
(the proposals go with their versions, the CVs and letters stay, the checks of 0015 still
hold), up again, and twice in a row."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command

from tests.ingestion.test_migration import add_user, at_base, at_head, url
from tests.test_migrations import alembic_config, current_revision

__all__ = ["at_base", "at_head"]


def _labels(engine: sa.Engine) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                sa.text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'document_kind' ORDER BY e.enumsortorder"
                )
            ).scalars()
        )


def _document(conn: sa.Connection, user: str, posting: str, kind: str) -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO documents (user_id, scope, posting_id, kind, language, "
                "template_slug, draft, draft_schema, draft_written_by, draft_updated_at) "
                "VALUES (:u, 'posting', :p, :k, 'en', 'classic', '{}'::jsonb, 1, 'writer', now()) "
                "RETURNING id"
            ),
            {"u": user, "p": posting, "k": kind},
        ).scalar_one()
    )


def _version(conn: sa.Connection, user: str, document: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO document_versions (document_id, user_id, number, content, "
            "content_schema, content_hash, language, template_slug, template_version, origin) "
            "VALUES (:d, :u, 1, '{}'::jsonb, 1, :h, 'en', 'classic', 1, 'generated')"
        ),
        {"d": document, "u": user, "h": b"\x00" * 32},
    )


def test_0022_up_down_up(at_head: sa.Engine) -> None:
    assert _labels(at_head) == ["cv", "letter", "proposal"]
    with at_head.begin() as conn:
        user = add_user(conn, "e2e-docs-0022@example.org")
        posting = conn.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title) "
                "VALUES (:u, 'example-0022', 'Example Robotics', 'Engineer') RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
        cv = _document(conn, user, posting, "cv")
        offer = _document(conn, user, posting, "proposal")
        _version(conn, user, offer)
        with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
            _document(conn, user, posting, "proposal")  # one per posting
        with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
            conn.execute(  # a proposal has no base CV of its own, as a letter
                sa.text("UPDATE documents SET base_document_id = :b WHERE id = :d"),
                {"b": cv, "d": offer},
            )
    command.downgrade(alembic_config(), "0019")
    assert current_revision(url(at_head)) == "0019"
    assert _labels(at_head) == ["cv", "letter"]
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
    assert _labels(at_head) == ["cv", "letter", "proposal"]


def test_0022_offline_sql(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0019:0022", sql=True)
    out = capsys.readouterr().out
    assert "ALTER TYPE document_kind ADD VALUE IF NOT EXISTS 'proposal'" in out
