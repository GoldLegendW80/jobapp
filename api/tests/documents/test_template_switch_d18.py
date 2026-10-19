"""D18: what a template switch changes, through the routes the picker and the gallery call
(SPEC-documents.md, "Base CVs", Inheritance, and "Templates", Switching). A base CV's new template
is taken by the CVs written from it afterwards, not by those already written; one posting's switch
leaves its base CV and its letter as they were; writing a CV again from another base CV takes that
base CV's template unless the person switched it by hand since the last write; a template no
longer served renders with `classic`, which its version records, is absent from the gallery,
which is what the picker's note reads, and is no switch by hand."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents.models import RunStatus
from app.documents import chat, generate, render_job, store, submit, templates
from app.documents import content as c
from app.documents.models import (
    BaseSource,
    Document,
    DocumentVersion,
    GenerationMode,
    GenerationTrigger,
    Kind,
)
from tests.documents.render_support import CV, RecordingQueue
from tests.documents.test_documents_api import _person, _posting, _render, _save
from tests.identity.api_helpers import Api
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-docs-d18-alex@example.org"
BLAKE = "e2e-docs-d18-blake@example.org"


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.state.queue = recording
    return recording


def _base_cv(factory: sessionmaker[Session], user: uuid.UUID, clock: Any) -> uuid.UUID:
    with factory() as db:
        base = store.create_base(db, user, "Main CV", BaseSource.built, at=clock(), limit=10)
        db.commit()
        return base.id


def _posting_document(
    factory: sessionmaker[Session], user: uuid.UUID, posting: uuid.UUID, kind: Kind, clock: Any
) -> uuid.UUID:
    with factory() as db:
        document = store.get_or_create(db, user, posting, kind, at=clock())
        db.commit()
        return document.id


def _write(
    factory: sessionmaker[Session], user: uuid.UUID, document_id: uuid.UUID, clock: Any
) -> str:
    """A writer's generation of the posting's CV, created as the tick or Write now does; the
    template the document has afterwards."""
    with factory() as db:
        document = store.get(db, user, document_id)
        assert document is not None
        mode, trigger = GenerationMode.posting, GenerationTrigger.click
        generate.create(db, document, mode, trigger, None, {}, 1000, at=clock())
        db.commit()
        return db.scalar(sa.select(Document.template_slug).where(Document.id == document_id))


def _written(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    document_id: uuid.UUID,
    clock: Any,
    base: uuid.UUID | None = None,
) -> None:
    """A writer's generation of the posting's CV from `base` (else its own), done as the writer's
    final hook leaves it: the CV's last write."""
    with factory() as db:
        document = store.get(db, user, document_id)
        assert document is not None
        mode, trigger = GenerationMode.posting, GenerationTrigger.click
        generation = generate.create(db, document, mode, trigger, base, {}, 1000, at=clock())
        generate.finish(db, generation, c.parse("cv", CV), [], at=clock())
        db.commit()


def _chatted(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    document_id: uuid.UUID,
    clock: Any,
    template: str | None,
) -> int:
    """A chat message whose run submitted new content and, when named, asked `template` through
    `set_template`, ended as the chat's final hook ends it; the version it made."""
    with factory() as db:
        document = store.lock_document(db, user, document_id)
        mode, trigger = GenerationMode.chat, GenerationTrigger.chat
        generation = store.create_generation(
            db, user, document_id, mode, trigger, at=clock(), estimate={}, token_ceiling=1000
        )
        edited = {**CV, "summary": f"Changed in a chat at revision {document.draft_revision}."}
        accepted = submit.Accepted(c.parse("cv", edited), ())
        asked = chat.Asked(template=template)
        chat.conclude(
            db, document, generation, RunStatus.done, None, accepted, False, asked, clock()
        )
        db.commit()
        assert generation.version_number is not None
        return generation.version_number


def _template(factory: sessionmaker[Session], document_id: uuid.UUID) -> str:
    with factory() as db:
        return db.scalar(sa.select(Document.template_slug).where(Document.id == document_id))


def _patch(api: Api, token: str, document: uuid.UUID, slug: str) -> Any:
    return api.request(
        "PATCH", f"/api/documents/{document}", token=token, json={"template_slug": slug}
    )


def test_a_base_cvs_new_template_is_taken_by_the_cvs_written_afterwards_only(
    factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue
) -> None:
    alex, alex_id = _person(factory, api, ALEX, clock)
    base = _base_cv(factory, alex_id, clock)
    written = _posting_document(factory, alex_id, _posting(factory, alex_id), Kind.cv, clock)
    assert _save(api, alex, written, CV, 1).status_code == 200
    _render(factory, queue, clock)
    assert _template(factory, written) == "classic"

    changed = _patch(api, alex, base, "sidebar")
    assert changed.status_code == 200
    assert changed.json()["template_slug"] == "sidebar"
    listed = api.get("/api/documents/base-cvs", token=alex).json()["base_cvs"]
    assert [(one["id"], one["template_slug"]) for one in listed] == [(str(base), "sidebar")]

    # The CV already written keeps its template, even when it is written again from that base.
    assert _write(factory, alex_id, written, clock) == "classic"
    # A posting found afterwards: its CV and its letter take the base CV's new template.
    later = _posting(factory, alex_id, "Example Robotics")
    cv = _posting_document(factory, alex_id, later, Kind.cv, clock)
    assert _template(factory, cv) == "sidebar"
    assert _write(factory, alex_id, cv, clock) == "sidebar"
    letter = _posting_document(factory, alex_id, later, Kind.letter, clock)
    assert _template(factory, letter) == "sidebar"


def test_one_postings_switch_leaves_its_base_cv_and_its_letter_unchanged(
    factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue
) -> None:
    alex, alex_id = _person(factory, api, ALEX, clock)
    blake, _ = _person(factory, api, BLAKE, clock)
    base = _base_cv(factory, alex_id, clock)
    posting = _posting(factory, alex_id)
    cv = _posting_document(factory, alex_id, posting, Kind.cv, clock)
    letter = _posting_document(factory, alex_id, posting, Kind.letter, clock)

    switched = _patch(api, alex, cv, "compact")
    assert switched.status_code == 200
    assert switched.json()["template_slug"] == "compact"
    assert (_template(factory, cv), _template(factory, base), _template(factory, letter)) == (
        "compact",
        "classic",
        "classic",
    )
    assert api.get(f"/api/documents/{base}", token=alex).json()["template_slug"] == "classic"
    # Another person cannot switch it, nor learn that it exists.
    assert _patch(api, blake, cv, "banner").status_code == 404
    assert _template(factory, cv) == "compact"


def test_a_template_no_longer_served_renders_with_classic_and_leaves_the_gallery(
    factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue
) -> None:
    alex, alex_id = _person(factory, api, ALEX, clock)
    _base_cv(factory, alex_id, clock)
    cv = _posting_document(factory, alex_id, _posting(factory, alex_id), Kind.cv, clock)
    retired = f"retired-{uuid.uuid4().hex[:6]}"
    assert retired not in templates.registry()
    with factory() as db:
        db.execute(sa.update(Document).where(Document.id == cv).values(template_slug=retired))
        db.commit()

    assert _save(api, alex, cv, CV, 1).status_code == 200
    _render(factory, queue, clock)
    classic = templates.registry()[templates.DEFAULT]
    with factory() as db:
        assert render_job.template_of(db, alex_id, retired).slug == "classic"
        (version,) = db.execute(
            sa.select(DocumentVersion).where(DocumentVersion.document_id == cv)
        ).scalars()
        # What printed it: `classic`, with its version, never the slug that is gone.
        assert (version.template_slug, version.template_version) == ("classic", classic.version)
        assert version.pdf_sha256 is not None  # rendered, never refused
    # What the picker reads for its note: the document's slug, absent from the gallery.
    assert api.get(f"/api/documents/{cv}", token=alex).json()["template_slug"] == retired
    gallery = api.get("/api/document-templates", token=alex).json()["templates"]
    assert retired not in {one["slug"] for one in gallery}
    assert "classic" in {one["slug"] for one in gallery}
    # Choosing a served template again is an ordinary switch.
    assert _patch(api, alex, cv, "compact").json()["template_slug"] == "compact"


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        (["write A", "write B"], "sidebar"),
        (["write A", "switch compact", "write B"], "compact"),
        (["write A", "switch compact", "write A"], "compact"),
        # Switched before the last write: the next write from another base takes its template.
        (["write A", "switch compact", "write B", "write C"], "banner"),
        (["write A", "switch compact", "switch classic", "write B"], "classic"),
        (["write A", "switch compact", "save", "prune", "write B"], "compact"),
        (["write A", "save", "write B"], "sidebar"),
        (["save", "switch compact", "write B"], "compact"),
        (["save", "write B"], "sidebar"),
        # A chat that switches the template with its new content is the person's switch too.
        (["write A", "chat compact", "write B"], "compact"),
        (["write A", "chat compact", "chat classic", "write B"], "classic"),
        (["write A", "chat compact", "chat classic", "prune", "write B"], "classic"),
        (["write A", "chat compact", "write B", "write C"], "banner"),
        (["write A", "chat", "write B"], "sidebar"),
        (["chat compact", "write B"], "compact"),
    ],
)
def test_a_rewrite_from_another_base_cv_keeps_a_template_switched_by_hand_since_the_last_write(
    factory: sessionmaker[Session],
    api: Api,
    clock: Any,
    queue: RecordingQueue,
    steps: list[str],
    expected: str,
) -> None:
    alex, alex_id = _person(factory, api, ALEX, clock)
    bases = {"A": _base_cv(factory, alex_id, clock)}
    for name, slug in (("B", "sidebar"), ("C", "banner")):
        bases[name] = _base_cv(factory, alex_id, clock)
        assert _patch(api, alex, bases[name], slug).status_code == 200
    cv = _posting_document(factory, alex_id, _posting(factory, alex_id), Kind.cv, clock)
    last_base = bases["A"]
    for step in steps:
        verb, _, arg = step.partition(" ")
        if verb == "write":
            _written(factory, alex_id, cv, clock, bases[arg])
            last_base = bases[arg]
        elif verb == "switch":
            assert _patch(api, alex, cv, arg).status_code == 200
        elif verb == "chat":
            _chatted(factory, alex_id, cv, clock, arg or None)
        elif verb == "save":
            with factory() as db:
                revision = db.scalar(sa.select(Document.draft_revision).where(Document.id == cv))
            edited = {**CV, "summary": f"Edited by hand at revision {revision}."}
            assert _save(api, alex, cv, edited, revision).status_code == 200
            _render(factory, queue, clock)
        else:
            with factory() as db:
                assert store.prune(db, alex_id, cv, keep=1) > 0
                db.commit()
    assert _template(factory, cv) == expected
    with factory() as db:
        assert db.scalar(sa.select(Document.base_document_id).where(Document.id == cv)) == (
            last_base
        )
    # The base CVs keep their own templates whatever the posting's CV did.
    assert [_template(factory, bases[name]) for name in "ABC"] == ["classic", "sidebar", "banner"]


def test_a_writers_version_on_a_removed_template_records_classic_and_its_render_keeps_it(
    factory: sessionmaker[Session], api: Api, clock: Any
) -> None:
    alex, alex_id = _person(factory, api, ALEX, clock)
    _base_cv(factory, alex_id, clock)
    cv = _posting_document(factory, alex_id, _posting(factory, alex_id), Kind.cv, clock)
    _written(factory, alex_id, cv, clock)
    retired = f"retired-{uuid.uuid4().hex[:6]}"
    with factory() as db:
        db.execute(sa.update(Document).where(Document.id == cv).values(template_slug=retired))
        db.commit()
    _written(factory, alex_id, cv, clock)
    with factory() as db:
        revision = db.scalar(sa.select(Document.draft_revision).where(Document.id == cv))
    _render(factory, RecordingQueue(jobs=[{"args": (str(cv), revision)}]), clock)
    with factory() as db:
        first, second = db.execute(
            sa.select(DocumentVersion)
            .where(DocumentVersion.document_id == cv)
            .order_by(DocumentVersion.number)
        ).scalars()
        # No third version: the render reused the writer's, so the overflow hook can follow it.
        assert first.generation_id != second.generation_id
        assert second.generation_id is not None
        assert second.template_slug == "classic"
        assert second.pdf_sha256 is not None
    assert api.get(f"/api/documents/{cv}", token=alex).json()["template_slug"] == retired


def test_a_template_no_longer_served_is_never_read_as_a_switch_by_hand(
    factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue
) -> None:
    alex, alex_id = _person(factory, api, ALEX, clock)
    first = _base_cv(factory, alex_id, clock)
    other = _base_cv(factory, alex_id, clock)
    assert _patch(api, alex, other, "banner").status_code == 200
    # The first base CV's template was served when it got it, and is gone since.
    retired = f"retired-{uuid.uuid4().hex[:6]}"
    with factory() as db:
        db.execute(sa.update(Document).where(Document.id == first).values(template_slug=retired))
        db.commit()
    cv = _posting_document(factory, alex_id, _posting(factory, alex_id), Kind.cv, clock)
    _written(factory, alex_id, cv, clock, first)
    with factory() as db:
        revision = db.scalar(sa.select(Document.draft_revision).where(Document.id == cv))
    edited = {**CV, "summary": "Edited by hand after the write."}
    assert _save(api, alex, cv, edited, revision).status_code == 200
    _render(factory, queue, clock)
    with factory() as db:
        printed = db.scalars(
            sa.select(DocumentVersion.template_slug).where(DocumentVersion.document_id == cv)
        ).all()
        assert printed == ["classic", "classic"]
        document = store.get(db, alex_id, cv)
        assert document is not None
        assert document.template_slug == retired
        assert not generate.switched_by_hand(db, document)
    _written(factory, alex_id, cv, clock, other)
    assert _template(factory, cv) == "banner"


def test_a_chats_version_on_a_template_no_longer_served_records_classic_and_its_render_keeps_it(
    factory: sessionmaker[Session], api: Api, clock: Any
) -> None:
    _, alex_id = _person(factory, api, ALEX, clock)
    _base_cv(factory, alex_id, clock)
    cv = _posting_document(factory, alex_id, _posting(factory, alex_id), Kind.cv, clock)
    retired = f"retired-{uuid.uuid4().hex[:6]}"
    with factory() as db:
        db.execute(sa.update(Document).where(Document.id == cv).values(template_slug=retired))
        db.commit()
    number = _chatted(factory, alex_id, cv, clock, None)
    with factory() as db:
        revision = db.scalar(sa.select(Document.draft_revision).where(Document.id == cv))
    _render(factory, RecordingQueue(jobs=[{"args": (str(cv), revision)}]), clock)
    with factory() as db:
        (version,) = db.execute(
            sa.select(DocumentVersion).where(DocumentVersion.document_id == cv)
        ).scalars()
        # The render reused the chat's version: printed with `classic`, which it records.
        assert (version.number, version.template_slug) == (number, "classic")
        assert version.pdf_sha256 is not None
        document = store.get(db, alex_id, cv)
        assert document is not None
        assert document.template_slug == retired
        assert not generate.switched_by_hand(db, document)
