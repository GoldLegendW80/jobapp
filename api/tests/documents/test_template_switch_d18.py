"""D18: what a template switch changes, through the routes the picker and the gallery call
(SPEC-documents.md, "Base CVs", Inheritance, and "Templates", Switching). A base CV's new template
is taken by the CVs written from it afterwards, not by those already written; one posting's switch
leaves its base CV and its letter as they were; a template no longer served renders with
`classic` and is absent from the gallery, which is what the picker's note reads."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.documents import generate, render_job, store, templates
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
        assert version.template_version == classic.version
        assert version.pdf_sha256 is not None  # rendered, never refused
    # What the picker reads for its note: the document's slug, absent from the gallery.
    assert api.get(f"/api/documents/{cv}", token=alex).json()["template_slug"] == retired
    gallery = api.get("/api/document-templates", token=alex).json()["templates"]
    assert retired not in {one["slug"] for one in gallery}
    assert "classic" in {one["slug"] for one in gallery}
    # Choosing a served template again is an ordinary switch.
    assert _patch(api, alex, cv, "compact").json()["template_slug"] == "compact"
