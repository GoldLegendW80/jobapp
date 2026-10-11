"""Migration 0011_ingestion_documents against a real Postgres: the three tables hold every
column of the plan's list, the size checks hold, everything cascades from the user, and the
revision goes up, down and up again."""

from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import DataError, IntegrityError

from app.config import get_settings
from tests.test_migrations import HEAD, alembic_config, current_revision

TABLES = {"ingestion_documents", "ingestion_images", "ingestion_consents"}
ENUMS = {
    "ingestion_text_source": ["parsed", "transcribed", "pasted"],
    "ingestion_text_status": ["pending", "awaiting_transcription", "done", "failed"],
    "ingestion_failed_kind": ["file", "server"],
}
# SPEC-ingestion.md, Documents: the DDL, column by column. (name, type, nullable)
DOCUMENT_COLUMNS = {
    "id": ("UUID", False),
    "user_id": ("UUID", False),
    "name": ("TEXT", False),
    "mime": ("TEXT", False),
    "size": ("INTEGER", False),
    "sha256": ("BYTEA", False),
    "bytes": ("BYTEA", True),
    "text": ("TEXT", True),
    "text_source": ("ENUM", False),
    "text_status": ("ENUM", False),
    "note": ("TEXT", True),
    "truncated": ("BOOLEAN", False),
    "pages": ("INTEGER", True),
    "pages_text": ("INTEGER", True),
    "pages_image": ("INTEGER", True),
    "transcribed_pages": ("ARRAY", True),
    "parser_version": ("SMALLINT", False),
    "failed_reason": ("TEXT", True),
    "failed_kind": ("ENUM", True),
    "attempts": ("SMALLINT", False),
    "secret_warning": ("BOOLEAN", False),
    "secret_confirmed_at": ("TIMESTAMP", True),
    "pending_since": ("TIMESTAMP", True),
    "created_at": ("TIMESTAMP", False),
}
IMAGE_COLUMNS = {
    "id": ("BIGINT", False),
    "document_id": ("UUID", False),
    "user_id": ("UUID", False),
    "position": ("INTEGER", False),
    "jpeg": ("BYTEA", False),
    "width": ("INTEGER", False),
    "height": ("INTEGER", False),
    "text": ("TEXT", True),
}
CONSENT_COLUMNS = {
    "user_id": ("UUID", False),
    "text_version": ("INTEGER", False),
    "accepted_at": ("TIMESTAMP", False),
}

DOCUMENT = (
    "INSERT INTO ingestion_documents (user_id, name, mime, size, sha256, text_source, "
    "parser_version) VALUES (:u, 'cv.pdf', 'application/pdf', 10, :h, 'parsed', 1) RETURNING id"
)
IMAGE = (
    "INSERT INTO ingestion_images (document_id, user_id, position, jpeg, width, height) "
    "VALUES (:d, :u, :p, '\\xffd8ff'::bytea, 800, 600)"
)


@pytest.fixture
def at_base(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.Engine]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.downgrade(alembic_config(), "base")
    engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    yield engine
    command.downgrade(alembic_config(), "base")
    engine.dispose()


@pytest.fixture
def at_head(at_base: sa.Engine) -> sa.Engine:
    command.upgrade(alembic_config(), "head")
    return at_base


def url(engine: sa.Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def add_user(conn: sa.Connection, email: str) -> str:
    return str(
        conn.execute(
            sa.text(
                "INSERT INTO users (email, name, password_hash) VALUES (:e, 'U', 'h') RETURNING id"
            ),
            {"e": email},
        ).scalar_one()
    )


def add_document(conn: sa.Connection, user: str, digest: bytes = b"a" * 32) -> str:
    return str(conn.execute(sa.text(DOCUMENT), {"u": user, "h": digest}).scalar_one())


def columns(engine: sa.Engine, table: str) -> dict[str, tuple[str, bool]]:
    return {
        column["name"]: (type(column["type"]).__name__, column["nullable"])
        for column in sa.inspect(engine).get_columns(table)
    }


def enum_labels(engine: sa.Engine) -> dict[str, list[str]]:
    with engine.connect() as conn:
        found = conn.execute(
            sa.text(
                "SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = "
                "t.oid WHERE t.typname LIKE 'ingestion_%' ORDER BY t.typname, e.enumsortorder"
            )
        ).all()
    labels: dict[str, list[str]] = {}
    for name, label in found:
        labels.setdefault(name, []).append(label)
    return labels


def test_upgrade_creates_the_three_tables_with_every_column_of_the_plan(
    at_head: sa.Engine,
) -> None:
    inspector = sa.inspect(at_head)
    assert set(inspector.get_table_names()) >= TABLES
    assert columns(at_head, "ingestion_documents") == DOCUMENT_COLUMNS
    assert columns(at_head, "ingestion_images") == IMAGE_COLUMNS
    assert columns(at_head, "ingestion_consents") == CONSENT_COLUMNS
    assert enum_labels(at_head) == ENUMS

    documents = {c["name"]: c for c in inspector.get_columns("ingestion_documents")}
    assert "gen_random_uuid()" in documents["id"]["default"]
    assert documents["text_source"]["type"].name == "ingestion_text_source"
    assert documents["text_status"]["type"].name == "ingestion_text_status"
    assert documents["failed_kind"]["type"].name == "ingestion_failed_kind"
    assert "pending" in documents["text_status"]["default"]
    assert documents["truncated"]["default"] == "false"
    assert documents["secret_warning"]["default"] == "false"
    assert documents["attempts"]["default"] == "'0'::smallint"
    assert documents["created_at"]["type"].timezone is True
    assert documents["secret_confirmed_at"]["type"].timezone is True
    assert "now()" in documents["created_at"]["default"]
    assert inspector.get_pk_constraint("ingestion_documents")["constrained_columns"] == ["id"]

    images = {c["name"]: c for c in inspector.get_columns("ingestion_images")}
    assert "nextval" in images["id"]["default"]  # bigserial
    consents = {c["name"]: c for c in inspector.get_columns("ingestion_consents")}
    assert consents["accepted_at"]["type"].timezone is True
    assert inspector.get_pk_constraint("ingestion_consents")["constrained_columns"] == ["user_id"]


def test_keys_cascades_and_indexes(at_head: sa.Engine) -> None:
    inspector = sa.inspect(at_head)

    def foreign(table: str) -> dict[tuple[str, ...], tuple[str, tuple[str, ...], str]]:
        return {
            tuple(fk["constrained_columns"]): (
                fk["referred_table"],
                tuple(fk["referred_columns"]),
                fk["options"]["ondelete"],
            )
            for fk in inspector.get_foreign_keys(table)
        }

    assert foreign("ingestion_documents") == {("user_id",): ("users", ("id",), "CASCADE")}
    # An image points at its document together with the document's user, in one key.
    assert foreign("ingestion_images") == {
        ("document_id", "user_id"): ("ingestion_documents", ("id", "user_id"), "CASCADE"),
        ("user_id",): ("users", ("id",), "CASCADE"),
    }
    assert foreign("ingestion_consents") == {("user_id",): ("users", ("id",), "CASCADE")}

    uniques = {
        unique["name"]: unique["column_names"]
        for unique in inspector.get_unique_constraints("ingestion_documents")
    }
    assert uniques == {
        "uq_ingestion_documents_user_id_sha256": ["user_id", "sha256"],
        "uq_ingestion_documents_id_user_id": ["id", "user_id"],
    }
    (unique,) = inspector.get_unique_constraints("ingestion_images")
    assert unique["name"] == "uq_ingestion_images_document_id_position"
    assert unique["column_names"] == ["document_id", "position"]
    indexes = {ix["name"]: ix["column_names"] for ix in inspector.get_indexes("ingestion_images")}
    assert indexes["ix_ingestion_images_user_id"] == ["user_id"]

    checks = {
        table: {check["name"] for check in inspector.get_check_constraints(table)}
        for table in TABLES
    }
    assert checks == {
        "ingestion_documents": {
            "ck_ingestion_documents_name_length",
            "ck_ingestion_documents_size_range",
            "ck_ingestion_documents_sha256_length",
            "ck_ingestion_documents_bytes_size",
            "ck_ingestion_documents_text_length",
            "ck_ingestion_documents_pages_not_negative",
            "ck_ingestion_documents_parser_version_positive",
            "ck_ingestion_documents_transcribed_pages_count",
            "ck_ingestion_documents_attempts_not_negative",
        },
        "ingestion_images": {
            "ck_ingestion_images_position_not_negative",
            "ck_ingestion_images_jpeg_size",
            "ck_ingestion_images_side_range",
            "ck_ingestion_images_text_length",
        },
        "ingestion_consents": {"ck_ingestion_consents_text_version_positive"},
    }


def test_a_new_document_starts_pending_with_the_defaults(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        document = add_document(conn, add_user(conn, "a@b.co"))
        stored = conn.execute(
            sa.text(
                "SELECT text_status::text, truncated, attempts, secret_warning, bytes, text, "
                "note, pages, pages_text, pages_image, failed_reason, failed_kind::text, "
                "secret_confirmed_at, created_at IS NOT NULL FROM ingestion_documents WHERE id = :d"
            ),
            {"d": document},
        ).one()
    assert tuple(stored) == (
        "pending", False, 0, False, None, None, None, None, None, None, None, None, None, True
    )  # fmt: skip


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ingestion_documents SET name = ''",
        "UPDATE ingestion_documents SET name = repeat('x', 201)",
        "UPDATE ingestion_documents SET name = NULL",
        "UPDATE ingestion_documents SET mime = NULL",
        "UPDATE ingestion_documents SET size = 0",
        "UPDATE ingestion_documents SET size = 20971521",
        "UPDATE ingestion_documents SET sha256 = '\\x00'::bytea",
        "UPDATE ingestion_documents SET sha256 = NULL",
        "UPDATE ingestion_documents SET bytes = decode(repeat('00', 20971521), 'hex')",
        "UPDATE ingestion_documents SET text = repeat('x', 500001)",
        "UPDATE ingestion_documents SET pages = -1",
        "UPDATE ingestion_documents SET pages_text = -1",
        "UPDATE ingestion_documents SET pages_image = -1",
        "UPDATE ingestion_documents SET parser_version = 0",
        "UPDATE ingestion_documents SET parser_version = NULL",
        "UPDATE ingestion_documents SET attempts = -1",
        "UPDATE ingestion_documents SET text_source = NULL",
        "UPDATE ingestion_documents SET text_status = NULL",
        "UPDATE ingestion_documents SET truncated = NULL",
        "UPDATE ingestion_documents SET user_id = gen_random_uuid()",
        # the same file twice for one user
        "INSERT INTO ingestion_documents (user_id, name, mime, size, sha256, text_source, "
        "parser_version) SELECT user_id, 'again', mime, size, sha256, 'parsed', 1 "
        "FROM ingestion_documents",
        "UPDATE ingestion_images SET position = -1",
        "UPDATE ingestion_images SET jpeg = ''::bytea",
        "UPDATE ingestion_images SET jpeg = decode(repeat('00', 300001), 'hex')",
        "UPDATE ingestion_images SET width = 0",
        "UPDATE ingestion_images SET height = 0",
        "UPDATE ingestion_images SET width = 2001",
        "UPDATE ingestion_images SET height = 2001",
        "UPDATE ingestion_images SET text = repeat('x', 20001)",
        "UPDATE ingestion_images SET document_id = gen_random_uuid()",
        "UPDATE ingestion_images SET user_id = gen_random_uuid()",
        # an image under another user than its document's, even one that exists
        "UPDATE ingestion_images SET user_id = (SELECT id FROM users WHERE email = 'c@d.co')",
        "INSERT INTO ingestion_images (document_id, user_id, position, jpeg, width, height) "
        "SELECT d.id, u.id, 7, '\\xffd8ff'::bytea, 10, 10 FROM ingestion_documents d, users u "
        "WHERE u.email = 'c@d.co'",
        "UPDATE ingestion_documents SET user_id = (SELECT id FROM users WHERE email = 'c@d.co')",
        "UPDATE ingestion_documents SET transcribed_pages = "
        "(SELECT array_agg(n) FROM generate_series(0, 200) AS n)",
        # one image per page
        "INSERT INTO ingestion_images (document_id, user_id, position, jpeg, width, height) "
        "SELECT document_id, user_id, position, jpeg, width, height FROM ingestion_images",
        "UPDATE ingestion_consents SET text_version = 0",
        "UPDATE ingestion_consents SET accepted_at = NULL",
        "INSERT INTO ingestion_consents (user_id, text_version) SELECT user_id, 2 "
        "FROM ingestion_consents",
        "INSERT INTO ingestion_consents (user_id, text_version) VALUES (gen_random_uuid(), 1)",
    ],
)
def test_the_constraints_hold(at_head: sa.Engine, statement: str) -> None:
    with at_head.begin() as conn:
        user = add_user(conn, "a@b.co")
        add_user(conn, "c@d.co")
        document = add_document(conn, user)
        conn.execute(sa.text(IMAGE), {"d": document, "u": user, "p": 0})
        conn.execute(
            sa.text("INSERT INTO ingestion_consents (user_id, text_version) VALUES (:u, 1)"),
            {"u": user},
        )
    with pytest.raises(IntegrityError), at_head.begin() as conn:
        conn.execute(sa.text(statement))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ingestion_documents SET text_status = 'parsing'",
        "UPDATE ingestion_documents SET text_source = 'ocr'",
        "UPDATE ingestion_documents SET failed_kind = 'user'",
    ],
)
def test_an_unknown_enum_label_is_refused(at_head: sa.Engine, statement: str) -> None:
    with at_head.begin() as conn:
        add_document(conn, add_user(conn, "a@b.co"))
    with pytest.raises(DataError), at_head.begin() as conn:
        conn.execute(sa.text(statement))


def test_what_later_tasks_store_fits(at_head: sa.Engine) -> None:
    """The values G2b, G7, G8, G14 and G16 write, at their limits: none needs another column
    or a wider check."""
    values: dict[str, Any] = {
        "name": "x" * 200,
        "text": "\U0001f600" * 500_000,
        "note": "Pages 31 to 44 were not read (time limit).",
        "reason": "This PDF is protected. Remove the protection and export it again.",
        "jpeg": b"\xff\xd8\xff" + b"\x00" * (300_000 - 3),
        "transcription": "y" * 20_000,
    }
    with at_head.begin() as conn:
        user = add_user(conn, "a@b.co")
        other = add_user(conn, "c@d.co")
        document = add_document(conn, user)
        add_document(conn, other)  # the same file for another user
        conn.execute(
            sa.text(
                "UPDATE ingestion_documents SET name = :name, text = :text, note = :note, "
                "truncated = true, pages = 200, pages_text = 170, pages_image = 30, "
                "transcribed_pages = (SELECT array_agg(n) FROM generate_series(0, 199) AS n), "
                "parser_version = 32767, attempts = 2, text_source = 'transcribed', "
                "text_status = 'awaiting_transcription', bytes = NULL, "
                "pending_since = now() - interval '3 hours', "
                "secret_warning = true, secret_confirmed_at = now() WHERE id = :d"
            ),
            {**{k: values[k] for k in ("name", "text", "note")}, "d": document},
        )
        conn.execute(
            sa.text(
                "UPDATE ingestion_documents SET text_status = 'failed', failed_kind = 'server', "
                "failed_reason = :reason WHERE id = :d"
            ),
            {"reason": values["reason"], "d": document},
        )
        for position in (0, 29, 199):
            conn.execute(sa.text(IMAGE), {"d": document, "u": user, "p": position})
        conn.execute(
            sa.text(
                "UPDATE ingestion_images SET jpeg = :jpeg, width = 1568, height = 1109, "
                "text = :transcription WHERE position = 29"
            ),
            {"jpeg": values["jpeg"], "transcription": values["transcription"]},
        )
        conn.execute(
            sa.text("INSERT INTO ingestion_consents (user_id, text_version) VALUES (:u, 3)"),
            {"u": user},
        )
        conn.execute(
            sa.text(
                "UPDATE ingestion_documents SET bytes = decode(repeat('00', 20971520), 'hex'), "
                "size = 20971520 WHERE id = :d"
            ),
            {"d": document},
        )
        assert conn.execute(sa.text("SELECT count(*) FROM ingestion_documents")).scalar_one() == 2


def test_deleting_a_document_or_a_user_cascades(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        user = add_user(conn, "a@b.co")
        kept = add_document(conn, user, b"a" * 32)
        gone = add_document(conn, user, b"b" * 32)
        for document in (kept, gone):
            conn.execute(sa.text(IMAGE), {"d": document, "u": user, "p": 0})
        conn.execute(
            sa.text("INSERT INTO ingestion_consents (user_id, text_version) VALUES (:u, 1)"),
            {"u": user},
        )
        conn.execute(sa.text("DELETE FROM ingestion_documents WHERE id = :d"), {"d": gone})
        left = conn.execute(sa.text("SELECT document_id::text FROM ingestion_images")).all()
        assert [row[0] for row in left] == [kept]
        conn.execute(sa.text("DELETE FROM users"))
        for table in TABLES:
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_up_down_up_with_data(at_head: sa.Engine) -> None:
    with at_head.begin() as conn:
        user = add_user(conn, "a@b.co")
        conn.execute(sa.text(IMAGE), {"d": add_document(conn, user), "u": user, "p": 0})
    command.downgrade(alembic_config(), "0010")
    assert current_revision(url(at_head)) == "0010"
    assert not TABLES & set(sa.inspect(at_head).get_table_names())
    assert enum_labels(at_head) == {}
    with at_head.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM users")).scalar_one() == 1
    command.upgrade(alembic_config(), "head")
    assert current_revision(url(at_head)) == HEAD
    assert set(sa.inspect(at_head).get_table_names()) >= TABLES
    command.check(alembic_config())


def test_offline_sql_has_the_ingestion_tables(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "0010:0011", sql=True)
    out = capsys.readouterr().out
    for name in ENUMS:
        assert f"CREATE TYPE {name} AS ENUM" in out
    for table in TABLES:
        assert f"CREATE TABLE {table}" in out
    assert "CREATE INDEX ix_ingestion_images_user_id" in out
    command.downgrade(alembic_config(), "0011:0010", sql=True)
    out = capsys.readouterr().out
    for table in TABLES:
        assert f"DROP TABLE {table}" in out
    for name in ENUMS:
        assert f"DROP TYPE {name}" in out
