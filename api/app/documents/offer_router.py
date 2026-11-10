"""The notes on an offer letter of one posting (SPEC-documents.md, "Reading an offer").
Included by `routers.py` before D6's `/api/documents/{document_id}`.

- `GET /api/documents/offer-notes/{posting_id}`: the notes as the posting page shows them, with
  their newest reading, what they compare the letter with and whether the letter was longer
  than what was read of it (`truncated`, `read_chars`); `document_id` null before the first
  reading. A chat change whose run ended without its hook is settled first, so `busy` does not
  stay true. 404 for a posting that is not the person's.
- `POST /api/documents/offer-notes/{posting_id} {ingestion_document_id, script?}`: read that
  upload of the person's, 202 with the notes. 404 for a posting or an upload that is not the
  person's; 422 for an upload that cannot be read (not read yet, failed, pages of pictures, not
  a PDF or a Word file, a secret warning not kept), with a sentence for each; 409 without a
  model credential (no consent is asked), while the notes are being written, or while their
  row is locked; 429 when the person's runs are all busy. `script` names the fake backend's
  stream (tests and journeys), ignored on the real model.

The chat of the notes is the documents chat (`chat_router`). Every answer is `Cache-Control:
private, no-store` (`routers.no_store`). Logs carry ids, never a text.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

import anyio.from_thread
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import queue as queues
from app.agents import runs as agent_runs
from app.db import get_session
from app.documents import generate, offer_notes, store, writer_base
from app.documents.models import GenerationStatus, Language
from app.identity import deps
from app.identity.models import User

logger = logging.getLogger("app.documents.offer_notes")

PRIVATE = {"Cache-Control": "private, no-store"}
POSTING_NOT_FOUND = "Posting not found."
UPLOAD_NOT_FOUND = "This file is not among your documents."
UNREADABLE = {
    "reading": "The letter is still being read. Try again in a moment.",
    "pictures": (
        "This letter is a scan: its pages are pictures. Upload a PDF with text, or a Word file."
    ),
    "not_a_cv": "Choose a PDF or a Word document (DOCX).",
    "secret": "This file may hold a password or a key. Keep it first, or delete it.",
}
UNREAD = "The letter could not be read. Upload it again, or another file."
REFUSED = {
    "credential": "Add a model credential on the Account page first, then read the letter again.",
}
RUNNING = "The notes are being written. Try again once they are done."
ALL_BUSY = "Your assistants are all busy. Try again in a moment."

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]
router = APIRouter(tags=["documents"])


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail, headers=PRIVATE)


class ReadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingestion_document_id: uuid.UUID
    script: Annotated[str, Field(max_length=19, pattern=r"^[a-z0-9_]{1,19}$")] | None = None


class ReadingOut(BaseModel):
    id: uuid.UUID
    status: GenerationStatus
    error: str | None


class LetterOut(BaseModel):
    id: uuid.UUID
    name: str


class OfferNotesOut(BaseModel):
    document_id: uuid.UUID | None
    language: Language | None
    notes: dict[str, Any] | None  # the draft: summary, clauses, differences, questions
    version: int | None  # the newest version's number, None before the first notes
    reading: ReadingOut | None  # the newest reading, whatever its status
    busy: bool  # a reading or a chat change is open
    letter: LetterOut | None  # the file read; None once deleted in ingestion
    pay: dict[str, Any]  # what the letter is compared with
    truncated: bool  # the letter is longer than what was read of it
    read_chars: int | None  # the characters read of it when truncated


def _out(found: offer_notes.View) -> OfferNotesOut:
    document, reading = found.document, found.reading
    return OfferNotesOut(
        document_id=document.id if document is not None else None,
        language=document.language if document is not None else None,
        notes=document.draft if document is not None else None,
        version=found.latest,
        reading=(
            ReadingOut(id=reading.id, status=reading.status, error=reading.error)
            if reading is not None
            else None
        ),
        busy=found.busy,
        letter=LetterOut(id=found.letter[0], name=found.letter[1]) if found.letter else None,
        pay=found.pay,
        truncated=found.read_chars is not None,
        read_chars=found.read_chars,
    )


def _view(db: Session, user: User, posting_id: uuid.UUID, request: Request) -> OfferNotesOut:
    found = offer_notes.view(db, user.id, posting_id, deps.clock(request))
    if found is None:
        raise _error(status.HTTP_404_NOT_FOUND, POSTING_NOT_FOUND)
    return _out(found)


@router.get("/api/documents/offer-notes/{posting_id}", response_model=OfferNotesOut)
def read_notes(posting_id: uuid.UUID, user: CurrentUser, request: Request, db: Db) -> OfferNotesOut:
    return _view(db, user, posting_id, request)


@router.post(
    "/api/documents/offer-notes/{posting_id}",
    response_model=OfferNotesOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"description": "Not the person's posting or upload"},
        409: {"description": "A credential is missing, or the notes are busy"},
        422: {"description": "The upload cannot be read"},
        429: {"description": "The person's runs are all busy"},
    },
)
def read_letter(
    posting_id: uuid.UUID, body: ReadIn, user: CurrentUser, request: Request, db: Db
) -> OfferNotesOut:
    try:
        started = offer_notes.read(
            db,
            user.id,
            posting_id,
            body.ingestion_document_id,
            at=deps.clock(request),
            script=body.script,
            settings=request.app.state.settings,
        )
        store.commit(db)
    except writer_base.UploadRefusedError as refused:
        db.rollback()
        if refused.code == "not_found":
            raise _error(status.HTTP_404_NOT_FOUND, UPLOAD_NOT_FOUND) from None
        if refused.code == "failed":
            detail = refused.reason or UNREAD
        else:
            detail = UNREADABLE.get(refused.code, UNREAD)
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, detail) from None
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, POSTING_NOT_FOUND) from None
    except offer_notes.OfferRefusedError as refused:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, REFUSED[refused.reason]) from None
    except store.GenerationOpenError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, RUNNING) from None
    except store.DocumentBusyError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, store.BUSY) from None
    except agent_runs.ActiveRunsError:
        db.rollback()
        raise _error(status.HTTP_429_TOO_MANY_REQUESTS, ALL_BUSY) from None
    try:
        anyio.from_thread.run(generate.enqueue_run, request.app.state.queue, started.run_id)
    except queues.QueueUnavailableError:
        # The run stays queued; the agents module's reaper fails it, and the page says so.
        logger.warning("documents.offer_notes.not_queued", extra={"run_id": str(started.run_id)})
    return _view(db, user, posting_id, request)
