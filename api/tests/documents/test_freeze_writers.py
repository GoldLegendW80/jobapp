"""D14 through the writers: a frozen latest version under the CV writer (D7), the letter writer
(D8) and the CV's shortening pass. Each writes a new version and leaves the frozen one as it
was, every column of it, its PDF and its content: the writers write only through `store.snapshot`,
which never reuses a frozen latest version, and the trigger of 0015 holds below it. The second
run replays the same recorded stream, so the new version has the frozen one's content: the case
where a reuse would have touched it. Fake backend, no model, no key, invented people only."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.documents import freeze, generate, render_job, store
from app.documents.models import Author, DocumentVersion, FreezeReason, GenerationStatus, Kind
from tests.documents.render_support import FakeRenderer, context
from tests.documents.store_support import AT, USER_A, db, engine, factory, migrated_url
from tests.documents.writer_support import (
    Person,
    document_row,
    execute,
    generation_row,
    person,
    run_row,
    write,
    write_letter,
)

__all__ = ["db", "engine", "factory", "migrated_url"]

# Every column of a version that a frozen one keeps, its identity and its freeze included.
KEPT = (
    "id",
    "number",
    "origin",
    "generation_id",
    "content",
    "content_schema",
    "content_hash",
    "language",
    "template_slug",
    "template_version",
    "corrections",
    "fit",
    "fit_step",
    "pages",
    "pdf",
    "pdf_sha256",
    "legacy_file_id",
    "frozen_at",
    "frozen_reason",
    "created_at",
)


@pytest.fixture
def alex(factory: sessionmaker[Session], migrated_url: str) -> Person:
    who = person(factory, migrated_url, USER_A)
    with factory() as db:  # the posting of the recorded letter streams
        db.execute(
            sa.text("UPDATE jobs SET company = :c, title = :t WHERE id = :j"),
            {"c": "Lumen Example", "t": "Senior Platform Engineer", "j": who.posting},
        )
        db.commit()
    return who


def _row(who: Person, document: uuid.UUID, number: int) -> dict[str, Any]:
    with who.factory() as db:
        found = db.execute(
            sa.select(*(getattr(DocumentVersion, column) for column in KEPT)).where(
                DocumentVersion.document_id == document, DocumentVersion.number == number
            )
        ).one()
    return dict(found._mapping)


def _numbers(who: Person, document: uuid.UUID) -> list[int]:
    with who.factory() as db:
        return [v.number for v in store.versions(db, who.user, document)]


def _render(who: Person, document: uuid.UUID, renderer: FakeRenderer | None = None) -> None:
    """The draft rendered, as the person's "Render" asks for it."""
    with who.factory() as db:
        render_job.mark_queued(db, who.user, document, bounded=False)
        db.commit()
    revision = document_row(who.factory, document).draft_revision
    ctx = context(who.factory, renderer or FakeRenderer(), AT)
    asyncio.run(render_job.render_document(ctx, str(document), revision))


def _freeze_latest(who: Person, document: uuid.UUID, reason: FreezeReason) -> dict[str, Any]:
    """The latest version, rendered, frozen as sent: every column of it as it is then."""
    with who.factory() as db:
        latest = store.versions(db, who.user, document)[0]
        assert latest.pdf_sha256 is not None
        freeze.freeze(db, who.user, latest.id, reason, AT)
        db.commit()
        number = latest.number
    return _row(who, document, number)


def _a_new_version_and_the_frozen_one_untouched(
    who: Person, document: uuid.UUID, generation: uuid.UUID, frozen: dict[str, Any]
) -> dict[str, Any]:
    row = generation_row(who.factory, generation)
    assert row.status is GenerationStatus.done, (row.status, row.error)
    assert row.version_number == frozen["number"] + 1
    assert _row(who, document, frozen["number"]) == frozen
    made = _row(who, document, frozen["number"] + 1)
    assert (made["frozen_at"], made["pdf"], made["generation_id"]) == (None, None, generation)
    # The render of the draft stores a PDF on a later version; the frozen one keeps its own.
    _render(who, document)
    assert _row(who, document, frozen["number"]) == frozen
    with who.factory() as db:
        latest = store.versions(db, who.user, document)[0]
        assert (latest.number > frozen["number"], latest.frozen_at) == (True, None)
        assert latest.pdf_sha256 is not None
    return made


@pytest.mark.parametrize("edited", [False, True], ids=["draft_replaced", "kept_as_version"])
def test_the_cv_writer_writes_a_new_version_over_a_frozen_latest_one(
    alex: Person, edited: bool
) -> None:
    document, _, _ = write(alex, "documents_cv_valid")
    _render(alex, document)
    frozen = _freeze_latest(alex, document, FreezeReason.marked_sent)

    def edit() -> None:
        with alex.factory() as db:
            row = store.get_or_create(db, alex.user, alex.posting, Kind.cv, at=AT)
            draft = {**row.draft, "headline": "Written by hand during the run"}
            store.save_draft(db, alex.user, row.id, draft, row.draft_revision, Author.user, at=AT)
            db.commit()

    _, generation, _ = write(alex, "documents_cv_valid", before=edit if edited else None)
    made = _a_new_version_and_the_frozen_one_untouched(alex, document, generation, frozen)
    assert made["content_hash"] == frozen["content_hash"]  # the same text, a version of its own
    # Kept beside the person's draft, whose render is a version of its own after it.
    after = [frozen["number"] + 2] if edited else []
    assert _numbers(alex, document) == [*after, frozen["number"] + 1, frozen["number"]]


def test_the_letter_writer_writes_a_new_version_over_a_frozen_latest_one(alex: Person) -> None:
    document, _, _ = write_letter(alex, "documents_letter_valid")
    _render(alex, document)
    frozen = _freeze_latest(alex, document, FreezeReason.applied)
    _, generation, _ = write_letter(alex, "documents_letter_valid")
    made = _a_new_version_and_the_frozen_one_untouched(alex, document, generation, frozen)
    assert made["content_hash"] == frozen["content_hash"]
    assert _numbers(alex, document) == [frozen["number"] + 1, frozen["number"]]


def test_the_shortening_pass_writes_a_new_version_over_a_frozen_overflow(
    alex: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate, "RUNTIME", alex.runtime())
    document, _, _ = write(alex, "documents_cv_overflow")
    _render(alex, document, FakeRenderer(pages=2))  # overflows: the pass is queued
    [shorten_run] = [
        uuid.UUID(job["args"][0])
        for job in alex.queue.jobs
        if job["function"] == "run_agent"
        and run_row(alex.factory, uuid.UUID(job["args"][0])).input.get("mode") == "shorten"
    ]
    # The overflowing version is sent before the pass runs (the apply module's freeze).
    frozen = _freeze_latest(alex, document, FreezeReason.apply)
    assert frozen["fit"].value == "overflow"
    execute(alex, shorten_run)
    generation = run_row(alex.factory, shorten_run).input["generation_id"]
    made = _a_new_version_and_the_frozen_one_untouched(
        alex, document, uuid.UUID(generation), frozen
    )
    assert made["content_hash"] != frozen["content_hash"]  # shortened
    assert _numbers(alex, document) == [frozen["number"] + 1, frozen["number"]]
