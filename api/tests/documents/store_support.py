"""What the tests of D5 (the tables and the store) share: a real Postgres at head, two users,
postings, uploads and job files, all invented. conftest.py (D2) is never edited: a test file
imports what it needs from here.

Every account is `e2e-*@example.org`. No test sleeps: a lock wait is bounded by
`store.LOCK_TIMEOUT_MS`, which `short_locks` sets to a few milliseconds, and a race is played
with two sessions in a fixed order (the first holds its lock, the second gives up, the first
commits, the second tries again).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.documents import store
from tests.identity import conftest as identity

# The identity fixtures (real Postgres at head; users truncated with CASCADE after each test).
migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory

AT = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
USER_A = "e2e-docs-a@example.org"
USER_B = "e2e-docs-b@example.org"
ENGLISH = "Lumen Example is hiring a platform engineer. You will own our deployment tooling."
FRENCH = "Brise Exemple recrute une ingénieure plateforme. Vous avez une expérience sur Kubernetes."
PDF = b"%PDF-1.7\n" + bytes(range(256)) + b"\n%%EOF\n"


@pytest.fixture
def db(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        yield session
        session.rollback()


@pytest.fixture
def short_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "LOCK_TIMEOUT_MS", 30)


def add_user(db: Session, email: str) -> uuid.UUID:
    return db.execute(
        sa.text(
            "INSERT INTO users (email, name, password_hash) VALUES (:e, 'U', 'h') RETURNING id"
        ),
        {"e": email},
    ).scalar_one()


def add_posting(db: Session, user: uuid.UUID, text: str = ENGLISH) -> uuid.UUID:
    return db.execute(
        sa.text(
            "INSERT INTO jobs (user_id, slug, company, title, posting_text) "
            "VALUES (:u, :s, 'Example Corp', 'Platform engineer', :t) RETURNING id"
        ),
        {"u": user, "s": f"example-{uuid.uuid4().hex[:8]}", "t": text},
    ).scalar_one()


def add_upload(db: Session, user: uuid.UUID) -> uuid.UUID:
    return db.execute(
        sa.text(
            "INSERT INTO ingestion_documents (user_id, name, mime, size, sha256, text_source, "
            "parser_version) VALUES (:u, 'cv.pdf', 'application/pdf', 10, :h, 'parsed', 1) "
            "RETURNING id"
        ),
        {"u": user, "h": uuid.uuid4().bytes * 2},
    ).scalar_one()


def add_job_file(db: Session, job: uuid.UUID, data: bytes = PDF) -> uuid.UUID:
    return db.execute(
        sa.text(
            "INSERT INTO job_files (job_id, name, mime, bytes, size, sha256, origin) "
            "VALUES (:j, 'cv.pdf', 'application/pdf', :b, :n, :h, 'legacy') RETURNING id"
        ),
        {"j": job, "b": data, "n": len(data), "h": hashlib.sha256(data).digest()},
    ).scalar_one()


def document_tables(db: Session) -> list[str]:
    """Every table of the module, read from the database itself."""
    names = sa.inspect(db.connection()).get_table_names()
    return sorted(name for name in names if name.startswith("document"))


def rows_of(db: Session, user: uuid.UUID) -> dict[str, int]:
    return {
        table: db.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE user_id = :u"),
            {"u": user},
        ).scalar_one()
        for table in document_tables(db)
    }
