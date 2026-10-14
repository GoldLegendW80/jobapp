"""D5: migration 0015 up, down and up again, with its tables, enums, named indexes and the
trigger on `document_versions`; `alembic check` itself runs in tests/test_migrations.py."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command

from tests.ingestion.test_migration import at_base, at_head
from tests.test_migrations import HEAD, alembic_config, current_revision

__all__ = ["at_base", "at_head"]

TABLES = {
    "documents",
    "document_versions",
    "document_generations",
    "document_messages",
    "document_consents",
    "document_settings",
    "document_user_templates",
}
ENUMS = {
    "document_scope",
    "base_source",
    "document_kind",
    "document_language",
    "document_author",
    "render_state",
    "version_origin",
    "fit_result",
    "freeze_reason",
    "generation_mode",
    "generation_trigger",
    "generation_status",
    "generation_wait",
    "generation_outcome",
    "message_channel",
    "message_role",
}
NAMED = {
    "uq_documents_id_user_id",
    "uq_documents_user_id_default",
    "uq_documents_user_id_posting_id_kind",
    "uq_document_generations_document_id_open",
    "uq_document_versions_document_id",
    "uq_document_user_templates_user_id_slug",
    "fk_documents_posting_id_jobs",
    "fk_document_versions_document_id_documents",
    "fk_document_generations_document_id_documents",
    "fk_document_messages_document_id_documents",
    "fk_documents_base_document_id_documents",
    "fk_document_versions_generation_id_document_generations",
    "ck_document_versions_frozen_is_whole",
    "ck_documents_base_has_no_posting",
}


def _state(engine: sa.Engine) -> tuple[set[str], set[str], set[str], int]:
    with engine.connect() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        enums = set(
            conn.execute(sa.text("SELECT typname FROM pg_type WHERE typtype = 'e'")).scalars()
        )
        names = set(conn.execute(sa.text("SELECT conname FROM pg_constraint")).scalars())
        names |= set(conn.execute(sa.text("SELECT indexname FROM pg_indexes")).scalars())
        trigger = conn.execute(
            sa.text(
                "SELECT count(*) FROM pg_proc WHERE proname IN "
                "('document_versions_frozen', 'document_versions_legacy_file')"
            )
        ).scalar_one()
    return tables, enums, names, trigger


def test_0015_up_down_up(at_head: sa.Engine) -> None:
    assert HEAD >= "0015"
    url = at_head.url.render_as_string(hide_password=False)
    tables, enums, names, trigger = _state(at_head)
    assert tables >= TABLES
    assert enums >= ENUMS
    assert names >= NAMED
    assert trigger == 2  # the frozen and the legacy file triggers' functions
    command.downgrade(alembic_config(), "0013")
    assert current_revision(url) == "0013"
    tables, enums, names, trigger = _state(at_head)
    assert not TABLES & tables
    assert not ENUMS & enums
    assert not NAMED & names
    assert trigger == 0
    assert "uq_jobs_id_user_id" in names  # 0013's, kept
    command.upgrade(alembic_config(), "head")
    assert current_revision(url) == HEAD
    assert _state(at_head)[0] >= TABLES


def test_0015_offline_sql_creates_the_trigger(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0013:0015", sql=True)
    out = capsys.readouterr().out
    assert "CREATE FUNCTION document_versions_frozen()" in out
    assert "CREATE TRIGGER document_versions_frozen" in out
    assert (
        "UPDATE alembic_version SET version_num='0015' WHERE alembic_version.version_num = '0013'"
        in out
    )
