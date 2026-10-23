"""The upload a base CV was converted from is lock 1b of the store's lock order (store.py module
docstring): every path takes it before lock 3, and the shared mode (`FOR KEY SHARE`) of a read
never waits for `delete_base` (`FOR NO KEY UPDATE`). Each test pauses one real path right after
its first locks, lets the other run until Postgres shows it waiting, then lets the first go on:
both finish, no deadlock and no busy answer. Real Postgres at head, no sleep, no clock."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.documents import chat, store, writer_base
from app.documents import upload as upload_module
from app.documents.models import Document
from app.ingestion import deletion
from app.ingestion.models import IngestionDocument
from tests.documents.store_support import AT, PDF, USER_A, engine, factory, migrated_url
from tests.documents.test_writer_base_upload import MORGAN, add_upload, convert, hooks
from tests.documents.two_sessions import in_thread, wait_until_blocked
from tests.documents.writer_support import Person, person

__all__ = ["engine", "factory", "hooks", "migrated_url"]


def kept(factory: sessionmaker[Session], upload_id: uuid.UUID) -> None:
    """The upload's file is kept for its base CVs (`keep_bytes`)."""
    with factory() as db:
        db.execute(
            sa.update(IngestionDocument)
            .where(IngestionDocument.id == upload_id)
            .values(raw=PDF, keep_bytes=True)
        )
        db.commit()


def raw_of(factory: sessionmaker[Session], upload_id: uuid.UUID) -> bytes | None:
    with factory() as db:
        found: bytes | None = db.execute(
            sa.select(IngestionDocument.raw).where(IngestionDocument.id == upload_id)
        ).scalar_one()
        return found


def source_of(factory: sessionmaker[Session], base_id: uuid.UUID) -> uuid.UUID | None:
    with factory() as db:
        document = db.get(Document, base_id)
        assert document is not None
        return document.source_document_id


def pause_after(
    monkeypatch: pytest.MonkeyPatch,
    engine: sa.Engine,
    module: ModuleType,
    name: str,
    other: Callable[[], None],
) -> tuple[dict[str, Any], list[threading.Thread]]:
    """`module.name` runs as before, then, the first time, starts `other` in a thread and
    returns once Postgres shows that thread waiting for a lock (or once it ended)."""
    original = getattr(module, name)
    outcome: dict[str, Any] = {}
    threads: list[threading.Thread] = []

    def paused(*args: Any, **kwargs: Any) -> Any:
        found = original(*args, **kwargs)
        if not threads:
            threads.append(in_thread(other, outcome))
            wait_until_blocked(engine, threads, 1)
        return found

    monkeypatch.setattr(module, name, paused)
    return outcome, threads


def converted_world(
    factory: sessionmaker[Session], migrated_url: str
) -> tuple[Person, uuid.UUID, uuid.UUID]:
    who = person(factory, migrated_url, USER_A)
    upload_id = add_upload(factory, who.user, MORGAN)
    base_id, _ = convert(who, upload_id, "documents_base_upload_valid")
    kept(factory, upload_id)
    return who, upload_id, base_id


def test_a_read_of_the_upload_takes_it_for_key_share(
    factory: sessionmaker[Session], migrated_url: str, engine: sa.Engine
) -> None:
    """`with_for_update(key_share=True)` alone is `FOR NO KEY UPDATE`, the mode of lock 1b."""
    who = person(factory, migrated_url, USER_A)
    upload_id = add_upload(factory, who.user, MORGAN)
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        seen.append(statement)

    sa.event.listen(engine, "before_cursor_execute", record)
    try:
        with factory() as db:
            writer_base.readable_upload(db, who.user, upload_id)
            store.lock_source(db, who.user, upload_id)
            db.rollback()
    finally:
        sa.event.remove(engine, "before_cursor_execute", record)
    locks = [s.split()[-3:] for s in seen if "FROM ingestion_documents" in s]
    assert locks == [["FOR", "KEY", "SHARE"], ["FOR", "KEY", "SHARE"]]


def test_a_conversion_and_the_delete_of_a_base_cv_from_the_same_file_both_finish(
    factory: sessionmaker[Session],
    migrated_url: str,
    engine: sa.Engine,
    hooks: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`build_from_upload` once read the upload (`FOR NO KEY UPDATE`) before the person's row,
    `delete_base` takes the person's row and then the upload: a circle Postgres broke."""
    who, upload_id, base_id = converted_world(factory, migrated_url)

    def remove_base() -> None:
        with factory() as db:
            store.delete_base(db, who.user, base_id, AT)
            store.commit(db)

    outcome, threads = pause_after(monkeypatch, engine, upload_module, "language_of", remove_base)
    with factory() as db:
        built = writer_base.build_from_upload(db, who.user, upload_id, at=AT)
        store.commit(db)
        made = built.document.id
    threads[0].join()
    assert "error" not in outcome, outcome.get("error")
    assert source_of(factory, made) == upload_id
    with factory() as db:
        assert db.get(Document, base_id) is None
    assert raw_of(factory, upload_id) == PDF  # the new base CV is converted from it


def test_a_chat_message_on_a_base_cv_and_its_delete_both_finish(
    factory: sessionmaker[Session],
    migrated_url: str,
    engine: sa.Engine,
    hooks: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`chat.post` holds the base CV (lock 3) and reads its upload; `delete_base` holds the
    upload (1b) and waits for the base CV."""
    who, upload_id, base_id = converted_world(factory, migrated_url)

    def remove_base() -> None:
        with factory() as db:
            store.delete_base(db, who.user, base_id, AT)
            store.commit(db)

    outcome, threads = pause_after(monkeypatch, engine, writer_base, "wait_reason", remove_base)
    with factory() as db:
        chat.post(db, who.user, base_id, "shorter summary", at=AT, settings=who.settings)
        store.commit(db)
    threads[0].join()
    assert "error" not in outcome, outcome.get("error")
    with factory() as db:
        assert db.get(Document, base_id) is None
    assert raw_of(factory, upload_id) is None  # its last base CV went


def test_a_chat_message_on_a_base_cv_and_the_delete_of_its_upload_both_finish(
    factory: sessionmaker[Session],
    migrated_url: str,
    engine: sa.Engine,
    hooks: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ingestion's delete holds the upload `FOR UPDATE` and then rewrites the base CV (its
    `SET NULL`); `chat.post` takes the upload before the base CV, so it waits on neither side
    of a circle."""
    who, upload_id, base_id = converted_world(factory, migrated_url)

    def remove_upload() -> None:
        with factory() as db:
            assert deletion.delete(db, who.user, upload_id, AT)
            db.commit()

    outcome, threads = pause_after(monkeypatch, engine, writer_base, "wait_reason", remove_upload)
    with factory() as db:
        chat.post(db, who.user, base_id, "shorter summary", at=AT, settings=who.settings)
        store.commit(db)
    threads[0].join()
    assert "error" not in outcome, outcome.get("error")
    assert source_of(factory, base_id) is None
    with factory() as db:
        assert db.get(IngestionDocument, upload_id) is None
