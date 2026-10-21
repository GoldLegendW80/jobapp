"""/api/agents/threads: chat about a job with the chat agent (agents A4, SPEC-agents.md, API).

- GET /api/agents/threads?job_id=&limit=: the caller's threads, most recently active first.
- POST /api/agents/threads {job_id}: the caller's thread on that job, created when there is none
  (201) or the existing one (200): one thread per job and user. 404 when the job is not one of
  the caller's.
- GET /api/agents/threads/{id}: the thread with its newest messages (oldest first), each with the
  state of its run, and the run still answering, if any. One snapshot of the database: a send
  committing meanwhile is in it whole (the message and its run) or not at all.
- POST /api/agents/threads/{id}/messages {text}: store the message and queue the run that answers
  it (202 with both). 409 while the previous answer is queued or running (`active_run_id` names
  it) and 409 without a saved model credential; 429 over the user's active runs; 503 when the
  server has no CREDENTIALS_KEY or the queue is down (the run is then failed at once, the message
  stays, marked unanswered).
- DELETE /api/agents/threads/{id}: delete it and its messages (204); an answer still pending is
  cancelled. The runs stay in the run log.

A send or a delete waits chat.LOCK_WAIT_S at most for each lock it takes, the thread's row first,
and answers 409 THREAD_LOCKED when one is not granted in time: nothing is written.

Another user's thread answers 404 like one that does not exist; the user always comes from the
session (default deny, CSRF on every write). Nothing here logs a message.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from app.agents import chat, operator
from app.agents.operator import THREAD_NOT_FOUND as NOT_FOUND
from app.agents.runs_router import sessions_of
from app.agents.schemas import (
    MessageIn,
    SentOut,
    ThreadBusyOut,
    ThreadDetail,
    ThreadIn,
    ThreadList,
    ThreadOut,
    message_out,
    thread_out,
)
from app.db import read_snapshot
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut

router = APIRouter(prefix="/api/agents/threads", tags=["agents"])
CurrentUser = Annotated[User, Depends(deps.current_user)]

LIST_DEFAULT = 30
LIST_MAX = 100
MESSAGES_MAX = 500
NO_STORE = {"Cache-Control": "no-store"}

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such thread (or not yours)"}}


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


def _thread_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None


# --- list and open ------------------------------------------------------------------------------


def _list(
    sessions: sessionmaker[Session], user_id: uuid.UUID, job_id: uuid.UUID | None, limit: int
) -> ThreadList:
    with sessions() as db:
        return ThreadList(
            items=[thread_out(row) for row in chat.list_threads(db, user_id, job_id, limit)]
        )


@router.get("", response_model=ThreadList, responses=_401, dependencies=[Depends(_no_store)])
async def list_threads(
    user: CurrentUser,
    request: Request,
    job_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=LIST_MAX)] = LIST_DEFAULT,
) -> ThreadList:
    """The caller's threads, most recently active first; `job_id` keeps the one on that job."""
    return await run_in_threadpool(_list, sessions_of(request), user.id, job_id, limit)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ThreadOut,
    responses={
        **_401,
        200: {"model": ThreadOut, "description": "The existing thread on this job"},
        404: {"model": ErrorOut, "description": "No such job (or not yours)"},
    },
    dependencies=[Depends(_no_store)],
)
async def open_thread(
    body: ThreadIn, user: CurrentUser, request: Request, response: Response
) -> ThreadOut:
    """The caller's thread on this job: created (201) or found (200)."""
    out, created = await run_in_threadpool(
        operator.open_thread, sessions_of(request), user.id, body.job_id, deps.clock(request)
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return out


def _detail(
    sessions: sessionmaker[Session], user_id: uuid.UUID, thread_id: uuid.UUID
) -> ThreadDetail | None:
    with sessions() as db:
        # One snapshot for the three reads below. At READ COMMITTED each statement sees what is
        # committed when it starts, so a send committing in between gave a thread whose answer
        # was pending (and whose count said one message) while its message list was still empty.
        read_snapshot(db)
        thread = chat.get_thread(db, user_id, thread_id)
        if thread is None:
            return None
        rows, total = chat.messages_of(db, thread.id, MESSAGES_MAX)
        busy = chat.active_run(db, thread.id)
        return ThreadDetail(
            **thread_out(thread).model_dump(),
            messages=[message_out(message, run) for message, run in rows],
            messages_truncated=total > len(rows),
            active_run_id=busy.id if busy is not None else None,
        )


@router.get(
    "/{thread_id}",
    response_model=ThreadDetail,
    responses={**_401, **_404},
    dependencies=[Depends(_no_store)],
)
async def read_thread(thread_id: str, user: CurrentUser, request: Request) -> ThreadDetail:
    found = await run_in_threadpool(_detail, sessions_of(request), user.id, _thread_id(thread_id))
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return found


# --- send ---------------------------------------------------------------------------------------


def _busy(run_id: uuid.UUID | None, detail: str) -> JSONResponse:
    body = ThreadBusyOut(detail=detail, active_run_id=run_id)
    return JSONResponse(status_code=409, content=body.model_dump(mode="json"), headers=NO_STORE)


@router.post(
    "/{thread_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SentOut,
    responses={
        **_401,
        **_404,
        409: {
            "model": ThreadBusyOut,
            "description": "Still answering, no model credential, or the thread is being updated",
        },
        422: {"model": ErrorOut, "description": "Empty or too long message"},
        429: {"model": ErrorOut, "description": "Too many runs queued or running"},
        503: {"model": ErrorOut, "description": "Not configured, or the queue is down"},
    },
    dependencies=[Depends(_no_store)],
)
async def send_message(
    thread_id: str, body: MessageIn, user: CurrentUser, request: Request
) -> SentOut | JSONResponse:
    """Store the message and queue its answer; follow it with GET /api/agents/runs/{id}/stream."""
    parsed = _thread_id(thread_id)
    text = operator.clean_text(body.text)
    operator.require_configured(request.app.state.credentials_keyring)
    sessions = sessions_of(request)
    try:
        sent = await run_in_threadpool(
            operator.send_message, sessions, user.id, parsed, text, deps.clock(request)
        )
    except operator.Busy as busy:
        return _busy(busy.run_id, busy.detail)
    await operator.enqueue(
        request.app.state.queue, sessions, sent.run.id, lambda: deps.clock(request)
    )
    return sent


# --- delete -------------------------------------------------------------------------------------


def _delete(
    sessions: sessionmaker[Session], user_id: uuid.UUID, thread_id: uuid.UUID, now: datetime
) -> bool:
    try:
        with sessions() as db, chat.bounded_lock_waits(db, chat.LOCK_WAIT_S):
            deleted = chat.delete_thread(db, user_id, thread_id, now)
            db.commit()
            return deleted
    except chat.ThreadLockedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=operator.THREAD_LOCKED, headers=NO_STORE
        ) from None


@router.delete(
    "/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        **_401,
        **_404,
        409: {"model": ErrorOut, "description": "The thread is being updated: try again"},
    },
)
async def delete_thread(thread_id: str, user: CurrentUser, request: Request) -> Response:
    deleted = await run_in_threadpool(
        _delete, sessions_of(request), user.id, _thread_id(thread_id), deps.clock(request)
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
