"""/api/agents/threads: chat about a job with the chat agent (agents A4, SPEC-agents.md, API).

- GET /api/agents/threads?job_id=&limit=: the caller's threads, most recently active first.
- POST /api/agents/threads {job_id}: the caller's thread on that job, created when there is none
  (201) or the existing one (200): one thread per job and user. 404 when the job is not one of
  the caller's.
- GET /api/agents/threads/{id}: the thread with its newest messages (oldest first), each with the
  state of its run, and the run still answering, if any.
- POST /api/agents/threads/{id}/messages {text}: store the message and queue the run that answers
  it (202 with both). 409 while the previous answer is queued or running (`active_run_id` names
  it) and 409 without a saved model credential; 429 over the user's active runs; 503 when the
  server has no CREDENTIALS_KEY or the queue is down (the run is then failed at once, the message
  stays, marked unanswered).
- DELETE /api/agents/threads/{id}: delete it and its messages (204); an answer still pending is
  cancelled. The runs stay in the run log.

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

from app import queue as queues
from app.agents import chat, credentials, runs
from app.agents.models import AgentMessage, AgentRun, AgentThread, MessageRole
from app.agents.runs_router import (
    NO_CREDENTIAL,
    NOT_CONFIGURED,
    TOO_MANY,
    _fail_queued,
    run_out,
    sessions_of,
)
from app.agents.schemas import (
    MessageIn,
    MessageOut,
    SentOut,
    ThreadBusyOut,
    ThreadDetail,
    ThreadIn,
    ThreadList,
    ThreadOut,
)
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import service as jobs_service

router = APIRouter(prefix="/api/agents/threads", tags=["agents"])
CurrentUser = Annotated[User, Depends(deps.current_user)]

LIST_DEFAULT = 30
LIST_MAX = 100
MESSAGES_MAX = 500
NO_STORE = {"Cache-Control": "no-store"}
NOT_FOUND = "Conversation not found."
JOB_NOT_FOUND = "Job not found."
BUSY = "The previous message is still being answered. Wait for the answer, then send again."
EMPTY = "text: write a message."

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such thread (or not yours)"}}


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


def _thread_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None


def thread_out(row: AgentThread) -> ThreadOut:
    return ThreadOut(
        id=row.id,
        job_id=row.job_id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def message_out(row: AgentMessage, run: AgentRun | None) -> MessageOut:
    return MessageOut(
        id=row.id,
        role=MessageRole(row.role).value,
        text=str((row.content or {}).get("text", "")),
        run_id=row.run_id,
        run_status=run.status if run is not None else None,
        error=run.error if run is not None and row.role is MessageRole.user else None,
        at=row.at,
    )


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


def _open(
    sessions: sessionmaker[Session], user_id: uuid.UUID, job_id: uuid.UUID, now: datetime
) -> tuple[ThreadOut, bool] | None:
    with sessions() as db:
        job = jobs_service.get_job(db, user_id, job_id)
        if job is None:
            return None
        thread, created = chat.thread_for_job(db, user_id, job, now)
        out = thread_out(thread)
        db.commit()
        return out, created


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
    found = await run_in_threadpool(
        _open, sessions_of(request), user.id, body.job_id, deps.clock(request)
    )
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=JOB_NOT_FOUND)
    out, created = found
    if not created:
        response.status_code = status.HTTP_200_OK
    return out


def _detail(
    sessions: sessionmaker[Session], user_id: uuid.UUID, thread_id: uuid.UUID
) -> ThreadDetail | None:
    with sessions() as db:
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


def _send(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    text: str,
    now: datetime,
) -> SentOut | JSONResponse:
    with sessions() as db:
        thread = chat.get_thread(db, user_id, thread_id, lock=True)
        if thread is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
        if credentials.get(db, user_id) is None:
            db.rollback()
            return _busy(None, NO_CREDENTIAL)
        try:
            message, run = chat.send(db, user_id, thread, text, now)
        except chat.ThreadBusyError as exc:
            db.rollback()
            return _busy(exc.run_id, BUSY)
        except runs.ActiveRunsError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=TOO_MANY,
                headers={"Retry-After": "30"},
            ) from None
        out = SentOut(message=message_out(message, run), run=run_out(run))
        db.commit()
    return out


@router.post(
    "/{thread_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SentOut,
    responses={
        **_401,
        **_404,
        409: {"model": ThreadBusyOut, "description": "Still answering, or no model credential"},
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
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail=EMPTY)
    if request.app.state.credentials_keyring is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED)
    sessions = sessions_of(request)
    sent = await run_in_threadpool(_send, sessions, user.id, parsed, text, deps.clock(request))
    if isinstance(sent, JSONResponse):
        return sent
    try:
        await request.app.state.queue.enqueue(
            "run_agent",
            str(sent.run.id),
            queue=queues.AGENTS,
            job_id=queues.agent_run_job_id(sent.run.id),
        )
    except queues.QueueUnavailableError:
        await run_in_threadpool(_fail_queued, sessions, sent.run.id, deps.clock(request))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=runs.QUEUE_DOWN
        ) from None
    return sent


# --- delete -------------------------------------------------------------------------------------


def _delete(
    sessions: sessionmaker[Session], user_id: uuid.UUID, thread_id: uuid.UUID, now: datetime
) -> bool:
    with sessions() as db:
        deleted = chat.delete_thread(db, user_id, thread_id, now)
        db.commit()
        return deleted


@router.delete(
    "/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={**_401, **_404},
)
async def delete_thread(thread_id: str, user: CurrentUser, request: Request) -> Response:
    deleted = await run_in_threadpool(
        _delete, sessions_of(request), user.id, _thread_id(thread_id), deps.clock(request)
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
