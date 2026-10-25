"""Synthetic scheduling fixtures; no claim of actual original retention."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents.definitions import FinalRun
from app.agents.models import RunStatus
from app.documents import consent, generate, store, submit, writer_base
from app.documents import content as content_types
from app.documents.models import (
    BaseSource,
    Document,
    DocumentGeneration,
    GenerationMode,
    GenerationTrigger,
)
from tests.documents.conftest import load
from tests.documents.test_base_api import _credential
from tests.documents.test_writer_base_upload import MORGAN, add_upload
from tests.identity.api_helpers import Api


class CaptureWorld(SimpleNamespace):
    def __repr__(self) -> str:
        return f"CaptureWorld(user={self.user}, base={self.base}, upload={self.upload})"


@pytest.fixture
def world(alex: Any, factory: sessionmaker[Session], api: Api, app: FastAPI, clock: Any) -> Any:
    token, user = alex
    upload = add_upload(factory, user, MORGAN)
    _credential(factory, user, clock)
    with factory() as db:
        db.execute(
            sa.text("UPDATE ingestion_documents SET bytes=:b WHERE id=:i"),
            {"b": b"%PDF-1.7 invented scheduler fixture", "i": upload},
        )
        consent.record(db, user, clock())
        base = store.create_base(
            db,
            user,
            "Uploaded",
            BaseSource.uploaded,
            at=clock(),
            content=load("base_cv_alex.json"),
            source_document_id=upload,
        )
        db.commit()
    return CaptureWorld(
        token=token,
        user=user,
        upload=upload,
        base=base.id,
        factory=factory,
        api=api,
        app=app,
        clock=clock,
        url=f"/api/documents/{base.id}/design/capture",
    )


def captures(world: Any) -> list[DocumentGeneration]:
    with world.factory() as db:
        return list(
            db.scalars(
                sa.select(DocumentGeneration)
                .where(
                    DocumentGeneration.document_id == world.base,
                    DocumentGeneration.mode == GenerationMode.template_capture,
                )
                .order_by(DocumentGeneration.created_at, DocumentGeneration.id)
            )
        )


def conversion(world: Any, monkeypatch: pytest.MonkeyPatch) -> FinalRun:
    with world.factory() as db:
        document = db.get(Document, world.base)
        generation = generate.create(
            db,
            document,
            GenerationMode.base_upload,
            GenerationTrigger.click,
            None,
            {"low": 1, "high": 2},
            10,
            at=world.clock(),
        )
        run = writer_base.start(db, generation, at=world.clock(), settings=world.app.state.settings)
        run.status, run.finished_at = RunStatus.done, world.clock()
        db.commit()
    taken = submit.Taken(
        submit.Accepted(content_types.parse("base", load("base_cv_alex.json")), ()), 1, False
    )
    monkeypatch.setattr(submit, "take", lambda _: taken)
    return FinalRun(
        run.id,
        world.user,
        writer_base.NAME,
        RunStatus.done,
        world.factory,
        world.clock,
        world.app.state.settings,
        {generate.QUEUE_SERVICE: world.app.state.queue},
    )
