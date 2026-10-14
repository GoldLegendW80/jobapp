"""D6, first review: the reviewer's probe of a save while the person's render queue is full."""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.documents import render_job
from app.documents.models import Document
from tests.documents.render_support import CV, RecordingQueue
from tests.documents.test_documents_api import _document, _person, _posting, _save
from tests.identity.api_helpers import Api
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]


def test_probe_save_refused_by_queue_bound_loses_the_save(
    factory: sessionmaker[Session],
    api: Api,
    clock: Any,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.queue = RecordingQueue()
    monkeypatch.setattr(render_job, "QUEUED_PER_USER", 1)
    token, user = _person(factory, api, "e2e-rv274-probe@example.org", clock)
    first = _document(factory, user, _posting(factory, user), clock)
    second = _document(factory, user, _posting(factory, user), clock)
    assert api.post(f"/api/documents/{first}/render", token=token).status_code == 202
    saved = _save(api, token, second, CV, 1)
    with factory() as db:
        revision = db.execute(
            sa.select(Document.draft_revision).where(Document.id == second)
        ).scalar_one()
    assert saved.status_code == 200
    assert revision == 2
