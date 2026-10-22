"""The start of an agent run, one code path for the routes and the operator (SPEC-agents.md, API).

POST /api/agents/runs, POST /api/agents/threads and POST /api/agents/threads/{id}/messages call
these functions, and so does `python -m app.agents.cli start` (tests/agents_real_run_jetson.sh),
which has no web session. A refusal raises `Refused`, an HTTPException with the status, detail
and headers the endpoint answers; `Busy` is the 409 of a thread that cannot take a message.

`only` restricts the account a start may write for (the operator's: an admin with a credential of
the subscription kind). It is checked in the transaction that writes, after the credential read,
on the user and credential rows read again under a share lock held to the commit: a role or
credential changed before the check is seen, and one changed after it waits for the commit. The
worker reads the credential again when the run starts. The routes pass none: the session is the
user.
"""

import contextlib
import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import chat, credentials, definitions, runs
from app.agents.crypto import Keyring
from app.agents.models import CredentialKind, ModelCredential
from app.agents.schemas import (
    RunJobOut,
    RunOut,
    SentOut,
    ThreadOut,
    message_out,
    run_out,
    thread_out,
)
from app.identity.models import Role, User
from app.jobs import service as jobs_service

INPUT_MAX_BYTES = 16_384

JOB_NOT_FOUND = "Job not found."
THREAD_NOT_FOUND = "Conversation not found."
UNKNOWN_AGENT = "agent: no such agent here."
TOO_BIG = f"input: at most {INPUT_MAX_BYTES} bytes of JSON."
NO_CREDENTIAL = "No model credential is saved. Add one on the Account page first."
NOT_CONFIGURED = "Agents cannot run on this server yet: its CREDENTIALS_KEY is not set."
TOO_MANY = f"You already have {runs.ACTIVE_PER_USER} runs queued or running. Wait for one to end."
NEEDS_JOB = "job_id: this agent works on a job; name one."
CHAT_AGENT = "agent: a chat agent answers in a thread; send a message to the thread instead."
# Recorded streams are for the fake backend: refused in production, never stored there.
NO_SCRIPT = "input.script: recorded streams exist only outside production."
BUSY = "The previous message is still being answered. Wait for the answer, then send again."
# A send or a delete that could not have the thread within chat.LOCK_WAIT_S (409, nothing written).
THREAD_LOCKED = "This conversation is being updated. Try again in a moment."
EMPTY = "text: write a message."
NOT_ALLOWED = "This account may not start a run here: wrong role or credential kind."


class Refused(HTTPException):
    """A start refused: the status, detail and headers its endpoint answers."""

    def __init__(self, code: int, detail: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code=code, detail=detail, headers=headers)


class Busy(Refused):
    """409 on a send: the thread's previous answer is pending (`run_id`), no credential, or the
    thread held by another transaction past chat.LOCK_WAIT_S."""

    def __init__(self, run_id: uuid.UUID | None, detail: str) -> None:
        super().__init__(status.HTTP_409_CONFLICT, detail)
        self.run_id = run_id


@dataclass(frozen=True)
class Only:
    """The one kind of account a start may write for."""

    role: Role
    credential_kind: CredentialKind


def _too_many() -> Refused:
    return Refused(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY, {"Retry-After": "30"})


def _check(db: Session, user_id: uuid.UUID, only: Only | None) -> None:
    if only is None:
        return
    shared = {"read": True}  # FOR SHARE: a demotion or a credential swap waits for the commit
    user = db.get(User, user_id, populate_existing=True, with_for_update=shared)
    credential = db.get(ModelCredential, user_id, populate_existing=True, with_for_update=shared)
    if (
        user is None
        or user.role is not only.role
        or credential is None
        or credential.kind is not only.credential_kind
    ):
        db.rollback()
        raise Refused(status.HTTP_403_FORBIDDEN, NOT_ALLOWED)


def require_configured(keyring: Keyring | None) -> None:
    if keyring is None:
        raise Refused(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED)


def prepare(
    name: str, job_id: uuid.UUID | None, data: dict[str, Any], app_env: str
) -> tuple[definitions.AgentDefinition, dict[str, Any]]:
    """The agent `name` and its checked input, or the endpoint's 422."""
    try:
        # An internal agent is unknown here: only its own module starts it (runs.create_run).
        agent = definitions.startable(name, app_env)
    except definitions.UnknownAgentError:
        raise Refused(422, UNKNOWN_AGENT) from None
    if agent.chat:
        raise Refused(422, CHAT_AGENT)
    if len(json.dumps(data, ensure_ascii=False).encode()) > INPUT_MAX_BYTES:
        raise Refused(422, TOO_BIG)
    try:
        checked = agent.validate_input(data)
    except definitions.InvalidInputError as exc:
        raise Refused(422, str(exc)) from None
    if agent.needs_job and job_id is None:
        raise Refused(422, NEEDS_JOB)
    if "script" in checked and app_env == "production":
        raise Refused(422, NO_SCRIPT)
    return agent, checked


def clean_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise Refused(422, EMPTY)
    return cleaned


def create_run(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    agent: definitions.AgentDefinition,
    job_id: uuid.UUID | None,
    data: dict[str, Any],
    now: datetime,
    only: Only | None = None,
) -> RunOut:
    """The queued run, committed. The caller enqueues it (`enqueue`)."""
    with sessions() as db:
        credential = credentials.get(db, user_id)
        if credential is None:
            raise Refused(status.HTTP_409_CONFLICT, NO_CREDENTIAL)
        _check(db, user_id, only)
        job = jobs_service.get_job(db, user_id, job_id) if job_id is not None else None
        if job_id is not None and job is None:
            raise Refused(status.HTTP_404_NOT_FOUND, JOB_NOT_FOUND)
        label = RunJobOut(id=job.id, company=job.company, title=job.title) if job else None
        try:
            row = runs.create_run(db, user_id, agent.name, job_id, data, agent.model, now)
        except runs.ActiveRunsError:
            db.rollback()
            raise _too_many() from None
        out = run_out(row, label)
        db.commit()
    return out


def open_thread(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    now: datetime,
    only: Only | None = None,
) -> tuple[ThreadOut, bool]:
    """The user's thread on the job, and whether it was created now."""
    with sessions() as db:
        if only is not None:
            credential = credentials.get(db, user_id)
            if credential is None:
                raise Refused(status.HTTP_409_CONFLICT, NO_CREDENTIAL)
            _check(db, user_id, only)
        job = jobs_service.get_job(db, user_id, job_id)
        if job is None:
            raise Refused(status.HTTP_404_NOT_FOUND, JOB_NOT_FOUND)
        thread, created = chat.thread_for_job(db, user_id, job, now)
        out = thread_out(thread)
        db.commit()
        return out, created


def send_message(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    text: str,
    now: datetime,
    only: Only | None = None,
) -> SentOut:
    """The stored message and the queued run that answers it, committed. The caller enqueues.
    Each lock wait of the transaction, the thread's row first, ends after chat.LOCK_WAIT_S:
    `Busy` THREAD_LOCKED then, with nothing written."""
    with sessions() as db, _thread_bounded(db):
        thread = chat.get_thread(db, user_id, thread_id, lock=True)
        if thread is None:
            raise Refused(status.HTTP_404_NOT_FOUND, THREAD_NOT_FOUND)
        credential = credentials.get(db, user_id)
        if credential is None:
            db.rollback()
            raise Busy(None, NO_CREDENTIAL)
        _check(db, user_id, only)
        try:
            message, run = chat.send(db, user_id, thread, text, now)
        except chat.ThreadBusyError as exc:
            db.rollback()
            raise Busy(exc.run_id, BUSY) from None
        except runs.ActiveRunsError:
            db.rollback()
            raise _too_many() from None
        out = SentOut(message=message_out(message, run), run=run_out(run))
        db.commit()
    return out


@contextlib.contextmanager
def _thread_bounded(db: Session) -> Iterator[None]:
    try:
        with chat.bounded_lock_waits(db, chat.LOCK_WAIT_S):
            yield
    except chat.ThreadLockedError:
        raise Busy(None, THREAD_LOCKED) from None


def fail_queued(sessions: sessionmaker[Session], run_id: uuid.UUID, now: datetime) -> None:
    with sessions() as db:
        runs.fail_queued(db, run_id, runs.QUEUE_DOWN, now)
        db.commit()


async def enqueue(
    queue: queues.Queue,
    sessions: sessionmaker[Session],
    run_id: uuid.UUID,
    clock: Callable[[], datetime],
) -> bool:
    """Queue the run on the agents queue; False when its job already existed. When the queue is
    down the run is failed at once, never left queued, and 503 is raised."""
    try:
        return await queue.enqueue(
            "run_agent", str(run_id), queue=queues.AGENTS, job_id=queues.agent_run_job_id(run_id)
        )
    except queues.QueueUnavailableError:
        await run_in_threadpool(fail_queued, sessions, run_id, clock())
        raise Refused(status.HTTP_503_SERVICE_UNAVAILABLE, runs.QUEUE_DOWN) from None
