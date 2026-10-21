"""The chat of a document and the follow of a generation (task D17; SPEC-documents.md, "API").
Included by `routers.py` before D6's `/api/documents/{document_id}`.

- `GET /api/documents/{id}/messages?channel=content`: the conversation, oldest first.
- `POST /api/documents/{id}/messages {text, channel?, script?}`: 202 with the message and the
  generation it started (`chat.post`). 404 for another person's document; 422 for an empty
  message, one over DOCUMENTS_CHAT_MAX_CHARS, or the `design` channel (task D26); 409 while a
  generation of the document is open, while its row is locked, or without the consent or a
  model credential (nothing is stored then: the answer says what to settle); 429 when the
  person's runs are all busy. `script` names the fake backend's stream (tests and journeys),
  ignored on the real model.
- `GET /api/document-generations/{id}`: one generation of the person's, as the page polls it.
  A chat generation whose run ended without its final hook is settled first (`chat.recover`).
- `POST /api/document-generations/{id}/cancel`: stop a chat generation of the person's
  (`chat.cancel`). 202 with the generation when a run was stopped or asked to stop (a running
  one ends `cancelled` within a second or so, its answer saying nothing changed); 200 with it,
  unchanged, when it had already ended, so a second click is harmless. 404 for another
  person's generation or one that is not a chat's; 409 while the document's row is locked.

Every answer is `Cache-Control: private, no-store`. Logs carry ids and sizes, never a text.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

import anyio.from_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import queue as queues
from app.agents import runs as agent_runs
from app.db import get_session
from app.documents import chat, generate, store
from app.documents.models import (
    Channel,
    DocumentGeneration,
    DocumentMessage,
    GenerationOutcome,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Role,
)
from app.identity import deps
from app.identity.models import User

logger = logging.getLogger("app.documents.chat")

PRIVATE = {"Cache-Control": "private, no-store"}
NOT_FOUND = "Document not found."
GENERATION_NOT_FOUND = "Generation not found."
EMPTY_OR_LONG = "Write a message of 1 to {limit} characters."
RUNNING = "The assistant is still answering the last message of this document."
ALL_BUSY = "Your assistants are all busy. Try again in a moment."
REFUSED = {"consent": chat.NO_CONSENT, "credential": chat.NO_CREDENTIAL}


def _private(response: Response) -> None:
    response.headers.update(PRIVATE)


Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

router = APIRouter(tags=["documents"], dependencies=[Depends(_private)])


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail, headers=PRIVATE)


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The cap is the setting's, checked once control characters are dropped (`chat.clean`).
    text: Annotated[str, Field(max_length=20_000)]
    channel: Literal["content"] = "content"
    script: Annotated[str, Field(max_length=19, pattern=r"^[a-z0-9_]{1,19}$")] | None = None


class MessageOut(BaseModel):
    id: int
    channel: Channel
    role: Role
    text: str
    generation_id: uuid.UUID | None
    version_number: int | None
    at: datetime


class MessagesOut(BaseModel):
    messages: list[MessageOut]


class GenerationOut(BaseModel):
    id: uuid.UUID
    status: GenerationStatus
    wait_reason: GenerationWait | None
    error: str | None


class SentOut(BaseModel):
    message: MessageOut
    generation: GenerationOut


class GenerationDetailOut(GenerationOut):
    document_id: uuid.UUID
    mode: str
    trigger: GenerationTrigger
    outcome: GenerationOutcome | None
    version_number: int | None
    corrections: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _message(row: DocumentMessage) -> MessageOut:
    return MessageOut(
        id=row.id,
        channel=row.channel,
        role=row.role,
        text=row.text,
        generation_id=row.generation_id,
        version_number=row.version_number,
        at=row.at,
    )


def _generation(row: DocumentGeneration) -> GenerationOut:
    return GenerationOut(id=row.id, status=row.status, wait_reason=row.wait_reason, error=row.error)


def _detail(row: DocumentGeneration) -> GenerationDetailOut:
    return GenerationDetailOut(
        id=row.id,
        status=row.status,
        wait_reason=row.wait_reason,
        error=row.error,
        document_id=row.document_id,
        mode=row.mode.value,
        trigger=row.trigger,
        outcome=row.outcome,
        version_number=row.version_number,
        corrections=row.corrections,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _settle_stale(db: Session, user: User, document_id: uuid.UUID, at: datetime) -> None:
    """`chat.recover` under the document's lock, committed; a locked row is left for later."""
    try:
        document = store.lock_document(db, user.id, document_id)
        if chat.recover(db, document, at):
            store.commit(db)
        else:
            db.rollback()
    except (store.DocumentBusyError, store.NotFoundError):
        db.rollback()


@router.get("/api/documents/{document_id}/messages", response_model=MessagesOut)
def list_messages(
    document_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    db: Db,
    channel: Annotated[Channel, Query()] = Channel.content,
) -> MessagesOut:
    if store.get(db, user.id, document_id) is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    _settle_stale(db, user, document_id, deps.clock(request))
    rows = store.messages(db, user.id, document_id, channel)
    return MessagesOut(messages=[_message(row) for row in rows])


@router.post(
    "/api/documents/{document_id}/messages",
    response_model=SentOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"description": "Not the person's document"},
        409: {"description": "A generation runs, or the consent or a credential is missing"},
        422: {"description": "Empty or too long"},
        429: {"description": "The person's runs are all busy"},
    },
)
def send_message(
    document_id: uuid.UUID, body: MessageIn, user: CurrentUser, request: Request, db: Db
) -> SentOut:
    settings = request.app.state.settings
    if store.get(db, user.id, document_id) is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    try:
        posted = chat.post(
            db,
            user.id,
            document_id,
            body.text,
            at=deps.clock(request),
            script=body.script,
            settings=settings,
        )
        out = SentOut(message=_message(posted.message), generation=_generation(posted.generation))
        store.commit(db)
    except store.MessageInvalidError:
        db.rollback()
        limit = settings.documents_chat_max_chars
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, EMPTY_OR_LONG.format(limit=limit)
        ) from None
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    except chat.ChatRefusedError as refused:
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
        anyio.from_thread.run(generate.enqueue_run, request.app.state.queue, posted.run_id)
    except queues.QueueUnavailableError:
        # The run stays queued; the agents module's reaper fails it, and the chat says so.
        logger.warning("documents.chat.run_not_queued", extra={"run_id": str(posted.run_id)})
    return out


@router.get("/api/document-generations/{generation_id}", response_model=GenerationDetailOut)
def read_generation(
    generation_id: uuid.UUID, user: CurrentUser, request: Request, db: Db
) -> GenerationDetailOut:
    found = db.get(DocumentGeneration, generation_id)
    if found is None or found.user_id != user.id:
        raise _error(status.HTTP_404_NOT_FOUND, GENERATION_NOT_FOUND)
    if found.status is GenerationStatus.running:
        _settle_stale(db, user, found.document_id, deps.clock(request))
        found = db.get(DocumentGeneration, generation_id, populate_existing=True)
        if found is None:
            raise _error(status.HTTP_404_NOT_FOUND, GENERATION_NOT_FOUND)
    return _detail(found)


@router.post(
    "/api/document-generations/{generation_id}/cancel",
    response_model=GenerationDetailOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"description": "Already ended: nothing to stop"},
        404: {"description": "Not a chat generation of the person's"},
        409: {"description": "The document is being changed"},
    },
)
def cancel_generation(
    generation_id: uuid.UUID, user: CurrentUser, request: Request, response: Response, db: Db
) -> GenerationDetailOut:
    try:
        found, stopped = chat.cancel(db, user.id, generation_id, at=deps.clock(request))
        out = _detail(found)
        store.commit(db)
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, GENERATION_NOT_FOUND) from None
    except store.DocumentBusyError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, store.BUSY) from None
    if not stopped:
        response.status_code = status.HTTP_200_OK
    return out
