"""The interview preparation api (interview_prep.py; SPEC-documents.md, "Interview preparation").

- `GET /api/jobs/{job_id}/interview-prep`: the posting's preparation (null before the first
  Prepare), its writing, the Practice chat and the pending proposal of the kept stories when it
  holds stories of this posting.
- `POST /api/jobs/{job_id}/interview-prep`: Prepare, on the person's click (202). 409 without
  the consent or a credential, or while a writing or a practice turn runs; 429 when the
  person's runs are all busy. Nothing is stored then.
- `POST /api/documents/{id}/practice {text}`: a practice message and its answer's run (202).
  422 for an empty or long message; 409 and 429 as Prepare.
- `POST /api/documents/{id}/stories {indexes}`: the chosen stories as one pending proposal of
  the profile's facts (`proposal_id` null when the profile holds them already); the person
  accepts or rejects it through `/api/proposals/{id}/accept` and `/reject`.

The draft, its hand edits (the Debrief included), its versions and restores are the documents
routes of router.py. Every route is the signed in person's: another person's posting or
document answers 404. Answers are `Cache-Control: private, no-store`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

import anyio.from_thread
import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app import queue as queues
from app.agents import proposals
from app.documents import generate, interview_prep, store
from app.documents.models import DocumentGeneration, DocumentMessage, GenerationMode
from app.documents.router import NOT_FOUND, POSTING_NOT_FOUND, CurrentUser, Db, _error, _private
from app.identity import deps
from app.jobs.models import Job

logger = logging.getLogger("app.documents.interview_prep")

router = APIRouter(tags=["documents"], dependencies=[Depends(_private)])

REFUSED = {
    "consent": "Confirm what is sent to the model first, then click again.",
    "credential": "Add a model credential on the Account page first, then click again.",
    "busy": "The preparation is being written or a practice answer is coming. Wait for it.",
}
ALL_BUSY = "Your assistants are all busy. Try again in a moment."
EMPTY_OR_LONG = "Write a message of 1 to {limit} characters."
NO_STORY = "Choose at least one story of this preparation."
NO_ROOM = "Your profile's facts cannot take these stories. Make room there first."


class GenerationOut(BaseModel):
    status: str
    error: str | None
    created_at: datetime


class MessageOut(BaseModel):
    id: int
    role: str
    text: str
    at: datetime


class PrepDocumentOut(BaseModel):
    id: uuid.UUID
    draft: dict[str, Any]
    draft_revision: int
    notes: list[dict[str, Any]]
    language: str
    versions: int


class PrepOut(BaseModel):
    document: PrepDocumentOut | None
    writing: GenerationOut | None
    practice: list[MessageOut]
    practice_open: bool
    stories_proposal: uuid.UUID | None


class StartedOut(BaseModel):
    document_id: uuid.UUID
    writing: GenerationOut


class PracticeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=20_000)
    script: str | None = Field(default=None, max_length=41, pattern=r"^[a-z][a-z0-9_]{0,40}$")


class PracticeOut(BaseModel):
    message: MessageOut


class StoriesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indexes: list[Annotated[int, Field(ge=0, lt=50)]] = Field(min_length=1, max_length=8)


class StoriesOut(BaseModel):
    proposal_id: uuid.UUID | None


def _generation(found: DocumentGeneration | None) -> GenerationOut | None:
    if found is None:
        return None
    return GenerationOut(status=found.status.value, error=found.error, created_at=found.created_at)


def _message(found: DocumentMessage) -> MessageOut:
    return MessageOut(id=found.id, role=found.role.value, text=found.text, at=found.at)


def _enqueue(request: Request, run_id: uuid.UUID) -> None:
    try:
        anyio.from_thread.run(generate.enqueue_run, request.app.state.queue, run_id)
    except queues.QueueUnavailableError:
        # The run stays queued; the agents module's reaper fails it, and the tick settles it.
        logger.warning("documents.interview_prep.run_not_queued", extra={"run_id": str(run_id)})


def _refused(db: Db, refused: interview_prep.PrepRefusedError) -> Exception:
    db.rollback()
    if refused.code == "runs_busy":
        return _error(status.HTTP_429_TOO_MANY_REQUESTS, ALL_BUSY)
    return _error(status.HTTP_409_CONFLICT, REFUSED[refused.code])


@router.get("/api/jobs/{job_id}/interview-prep", response_model=PrepOut)
def read_prep(job_id: uuid.UUID, user: CurrentUser, request: Request, db: Db) -> PrepOut:
    owned = db.scalar(sa.select(Job.id).where(Job.user_id == user.id, Job.id == job_id))
    if owned is None:
        raise _error(status.HTTP_404_NOT_FOUND, POSTING_NOT_FOUND)
    document = interview_prep.of_posting(db, user.id, job_id)
    proposal = interview_prep.pending_stories(db, user.id, job_id)
    if document is None:
        db.rollback()
        return PrepOut(
            document=None,
            writing=None,
            practice=[],
            practice_open=False,
            stories_proposal=proposal.id if proposal else None,
        )
    if interview_prep.recover_practice(db, user.id, document.id, deps.clock(request)):
        store.commit(db)
    turn = interview_prep.latest_generation(db, user.id, document.id, GenerationMode.chat)
    out = PrepOut(
        document=PrepDocumentOut(
            id=document.id,
            draft=document.draft,
            draft_revision=document.draft_revision,
            notes=(document.checks or [])
            if document.checks_revision == document.draft_revision
            else [],
            language=document.language.value,
            versions=len(store.versions(db, user.id, document.id)),
        ),
        writing=_generation(
            interview_prep.latest_generation(db, user.id, document.id, GenerationMode.posting)
        ),
        practice=[_message(m) for m in interview_prep.practice_messages(db, user.id, document.id)],
        practice_open=turn is not None and turn.status.value == "running",
        stories_proposal=proposal.id if proposal else None,
    )
    db.rollback()
    return out


@router.post(
    "/api/jobs/{job_id}/interview-prep",
    response_model=StartedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def prepare(job_id: uuid.UUID, user: CurrentUser, request: Request, db: Db) -> StartedOut:
    try:
        started = interview_prep.start(
            db, user.id, job_id, at=deps.clock(request), settings=request.app.state.settings
        )
        out = StartedOut(
            document_id=started.document.id,
            writing=_generation(started.generation),  # type: ignore[arg-type]
        )
        store.commit(db)
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, POSTING_NOT_FOUND) from None
    except interview_prep.PrepRefusedError as refused:
        raise _refused(db, refused) from None
    except store.DocumentBusyError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, store.BUSY) from None
    _enqueue(request, started.run_id)
    return out


@router.post(
    "/api/documents/{document_id}/practice",
    response_model=PracticeOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def practice(
    document_id: uuid.UUID, body: PracticeIn, user: CurrentUser, request: Request, db: Db
) -> PracticeOut:
    settings = request.app.state.settings
    try:
        posted = interview_prep.post_practice(
            db,
            user.id,
            document_id,
            body.text,
            at=deps.clock(request),
            settings=settings,
            script=body.script,
        )
        out = PracticeOut(message=_message(posted.message))
        store.commit(db)
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    except store.MessageInvalidError:
        db.rollback()
        limit = settings.documents_chat_max_chars
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, EMPTY_OR_LONG.format(limit=limit)
        ) from None
    except interview_prep.PrepRefusedError as refused:
        raise _refused(db, refused) from None
    except store.DocumentBusyError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, store.BUSY) from None
    _enqueue(request, posted.run_id)
    return out


@router.post("/api/documents/{document_id}/stories", response_model=StoriesOut)
def keep_stories(
    document_id: uuid.UUID, body: StoriesIn, user: CurrentUser, request: Request, db: Db
) -> StoriesOut:
    try:
        made = interview_prep.keep_stories(
            db, user.id, document_id, body.indexes, deps.clock(request)
        )
        out = StoriesOut(proposal_id=made.id if made is not None else None)
        store.commit(db)
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    except proposals.InvalidValueError:
        db.rollback()
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, NO_ROOM) from None
    except ValueError:
        db.rollback()
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, NO_STORY) from None
    return out
