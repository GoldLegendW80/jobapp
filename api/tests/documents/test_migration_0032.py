"""Documents D27: migration 0032, the documents of the twenty removed templates move to the
nearest of the three; a person's own template and the three stay; versions are not touched; the
downgrade moves nothing back; the offline SQL names every removed slug."""

from __future__ import annotations

import importlib.util

import pytest
import sqlalchemy as sa
from alembic import command

from app.documents import templates
from tests.ingestion.test_migration import add_user, at_base, at_head, url
from tests.test_migrations import alembic_config, current_revision

__all__ = ["at_base", "at_head"]

REMOVED = {
    "sidebar": "photo-columns",
    "split-right": "photo-columns",
    "portrait": "photo-columns",
    "banner": "photo-columns",
    "columns-header": "photo-columns",
    "cards": "photo-columns",
    "grid": "photo-columns",
    "serif": "ledger",
    "elegant": "ledger",
    "executive": "ledger",
    "nordic": "ledger",
    "academic": "band",
    "accent": "band",
    "bold": "band",
    "classic": "band",
    "compact": "band",
    "minimal": "band",
    "mono": "band",
    "plain": "band",
    "timeline": "band",
}
STAYS = ["band", "photo-columns", "ledger", "upload-abcd1234", "upload-zz99yy88"]


def _moves() -> dict[str, str]:
    path = next(templates.ROOT.parents[2].glob("alembic/versions/0032_*.py"))
    spec = importlib.util.spec_from_file_location("revision_0032", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.MOVES)


def test_the_table_is_the_spec_and_names_every_removed_slug_once() -> None:
    assert _moves() == REMOVED
    assert len(REMOVED) == 20
    assert set(REMOVED.values()) == set(templates.registry())
    assert not set(REMOVED) & set(templates.registry())


def _base_cv(conn: sa.Connection, user: str, slug: str) -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO documents (user_id, scope, kind, name, base_source, language, "
                "template_slug, draft, draft_schema, draft_written_by, draft_updated_at) "
                "VALUES (:u, 'base', 'cv', :n, 'built', 'en', :s, '{}'::jsonb, 1, 'writer', "
                "now()) RETURNING id"
            ),
            {"u": user, "n": f"CV {slug}", "s": slug},
        ).scalar_one()
    )


def _version(conn: sa.Connection, user: str, document: str, slug: str, frozen: bool) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO document_versions (document_id, user_id, number, content, "
            "content_schema, content_hash, language, template_slug, template_version, origin, "
            "frozen_at, frozen_reason) VALUES (:d, :u, 1, '{}'::jsonb, 1, :h, 'en', :s, 3, "
            "'generated', CASE WHEN :f THEN now() END, "
            "CASE WHEN :f THEN 'marked_sent'::freeze_reason END)"
        ),
        {"d": document, "u": user, "h": b"\x00" * 32, "s": slug, "f": frozen},
    )


def _slugs(engine: sa.Engine, table: str) -> dict[str, str]:
    column = "document_id" if table == "document_versions" else "id"
    with engine.connect() as conn:
        rows = conn.execute(sa.text(f"SELECT {column}, template_slug FROM {table}"))
        return {str(key): slug for key, slug in rows}


def test_0032_moves_each_removed_slug_and_nothing_else(at_head: sa.Engine) -> None:
    command.downgrade(alembic_config(), "0031")
    with at_head.begin() as conn:
        user = add_user(conn, "e2e-docs-0032@example.org")
        made = {}
        for index, slug in enumerate([*REMOVED, *STAYS]):
            made[slug] = _base_cv(conn, user, slug)
            _version(conn, user, made[slug], slug, frozen=index % 2 == 0)
    versions = _slugs(at_head, "document_versions")

    command.upgrade(alembic_config(), "0032")
    assert current_revision(url(at_head)) == "0032"
    after = _slugs(at_head, "documents")
    for slug, document in made.items():
        assert after[document] == REMOVED.get(slug, slug), slug
    assert _slugs(at_head, "document_versions") == versions  # every version as it was

    command.downgrade(alembic_config(), "0031")  # moves nothing back
    assert _slugs(at_head, "documents") == after
    command.upgrade(alembic_config(), "head")
    command.upgrade(alembic_config(), "head")
    assert _slugs(at_head, "documents") == after


def test_0032_offline_sql(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0031:0032", sql=True)
    out = capsys.readouterr().out
    assert out.count("UPDATE documents SET template_slug=") == 3
    for slug in REMOVED:
        assert f"'{slug}'" in out, slug
