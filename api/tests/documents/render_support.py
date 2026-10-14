"""What the tests of D6 (the render queue and the documents api) share: a fake renderer that
makes real PDFs of the page count asked, a recording queue, and a person with an invented
profile ("Alex Example", e2e-*@example.org) and a posting with a hand written CV draft.

The fake renderer stands for render_client.render (the job's `documents_render` in its context);
the Docker marked test_render_docker.py runs the same job on the real renderer.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pypdfium2 as pdfium
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.documents import store
from app.documents.models import Author, Kind
from app.documents.render_client import Rendered, RenderError
from app.profile import service as profile_service
from tests.documents.conftest import load, profile


def pdf_of(pages: int) -> bytes:
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(595, 842)
    out = io.BytesIO()
    document.save(out)
    document.close()
    return out.getvalue()


@dataclass
class FakeRenderer:
    """`pages` per call; `fail` raises that code; `during` runs inside the first call."""

    pages: int = 1
    fail: str | None = None
    calls: list[str] = field(default_factory=list)
    during: Callable[[], None] | None = None

    async def __call__(self, html: str) -> Rendered:
        self.calls.append(html)
        if self.during is not None:
            during, self.during = self.during, None
            during()
        if self.fail is not None:
            raise RenderError(self.fail)
        return Rendered(pdf=pdf_of(self.pages), pages=self.pages)


@dataclass
class RecordingQueue:
    """Stands for app.queue.Queue: refuses an id it holds, as Redis does."""

    jobs: list[dict[str, Any]] = field(default_factory=list)
    down: bool = False

    async def enqueue(self, function: str, *args: Any, queue: str, job_id: str) -> bool:
        if self.down:
            raise queues.QueueUnavailableError("down")
        if any(job["job_id"] == job_id for job in self.jobs):
            return False
        self.jobs.append({"function": function, "args": args, "queue": queue, "job_id": job_id})
        return True

    async def close(self) -> None:
        return None


CV = load("written/cv_typical.json")


def give_profile(factory: sessionmaker[Session], user: uuid.UUID, at: datetime) -> None:
    sections, revision = profile("alex")
    with factory() as db:
        found = profile_service.get_profile(db, user, at)
        found.sections = sections
        found.revision = revision
        db.commit()


def cv_draft(
    db: Session,
    user: uuid.UUID,
    posting: uuid.UUID,
    at: datetime,
    content: dict[str, Any] | None = None,
) -> uuid.UUID:
    """The posting's CV with a hand written draft, committed."""
    document = store.get_or_create(db, user, posting, Kind.cv, at=at)
    store.save_draft(
        db, user, document.id, content or CV, document.draft_revision, Author.user, at=at
    )
    db.commit()
    return document.id


def context(factory: sessionmaker[Session], render: FakeRenderer, at: datetime) -> dict[str, Any]:
    return {
        "sessions": factory,
        "clock": lambda: at,
        "documents_render": render,
        "documents_retry_after_s": 0,
    }
