"""/api/agents: the agents a user may start, and their runs (SPEC-agents.md, API).

- GET /api/agents: the agents available here (test agents only outside production), without the
  chat agents, which start from a thread only (app/agents/chat_router.py; 422 here), and without
  the internal agents, which only their own module starts (ingestion G6).
- POST /api/agents/runs {agent, job_id?, input}: 202 with the queued run. 422 when the agent is
  unknown here (an internal agent is, in the same words: this route never starts one), the
  input does not fit it (the message names the field) or the agent needs a
  job and none is named (triage); 404 when job_id is
  not one of the caller's jobs; 409 without a saved model credential; 429 when the caller already
  has runs.ACTIVE_PER_USER queued or running; 503 when the server has no CREDENTIALS_KEY or the
  queue (Redis) is down (the run is then failed at once, never left queued).
- GET /api/agents/runs?limit=&before=&job_id=&agent=: the caller's runs, newest first, each with
  its job's company and title (A3, for the job link).
- GET /api/agents/runs/{id}: one run with its events (at most EVENTS_MAX; the stream replays all).
- GET /api/agents/runs/{id}/stream: server-sent events (app/sse.py `replay`): one `step` event
  per stored event, `id` = its seq, data {seq, at, kind, data}, from after the Last-Event-ID
  header (EventSource sends it when it reconnects) or the `after` query parameter, then each new
  one as the worker writes it, and `end` once the run has ended and everything was sent. While
  an answer is being written, `partial` events without an id carry its text as it grows
  ({block, after, offset, text}, app/agents/live.py); the `text` step replaces it. Each
  stream closes after STREAM_MAX_S (the browser resumes it, through the session check again) and
  as soon as the client leaves; at most STREAMS_PER_USER open per user (429 beyond).
- POST /api/agents/runs/{id}/cancel: 202 with the run: cancelled at once when still queued, or
  `cancel_requested` while the worker stops it (within about a second); 409 when it has ended.

Another user's run answers 404 like one that does not exist, for reads, the stream and cancel
alike. The user always comes from the session (default deny, CSRF on every write). Nothing here
logs an input or an output.
"""

import json
import uuid
import weakref
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app import sse
from app.agents import credentials, definitions, live, runs
from app.agents.models import FINAL_STATUSES, AgentRun, RunStatus
from app.agents.schemas import (
    AgentList,
    AgentOut,
    EventOut,
    RunDetail,
    RunIn,
    RunJobOut,
    RunList,
    RunOut,
)
from app.db import read_snapshot
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import service as jobs_service
from app.jobs.models import Job

router = APIRouter(prefix="/api/agents", tags=["agents"])
CurrentUser = Annotated[User, Depends(deps.current_user)]

INPUT_MAX_BYTES = 16_384
LIST_DEFAULT = 30
LIST_MAX = 100
EVENTS_MAX = 2000
STREAM_PAGE = 200
# Open streams per user in this process (one api process on the Jetson); one more answers 429.
STREAMS_PER_USER = 8
# A stream closes after this long and the browser resumes it at once with Last-Event-ID: each
# resume goes through the session check again, so a sign out or a disabled account ends it soon.
STREAM_MAX_S = 5 * 60
TOO_MANY_STREAMS = f"At most {STREAMS_PER_USER} live run views at once. Close one and retry."


class StreamSlots:
    """The open streams per user, as weak references to their generators: a stream releases its
    slot when it ends (`release`), and also when its generator is dropped without ever having
    run (a client gone before the first byte), so a slot can never leak. Used from the event
    loop only (no lock needed)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.open: dict[uuid.UUID, weakref.WeakSet[object]] = {}

    def count(self, user_id: uuid.UUID) -> int:
        return len(self.open.get(user_id, ()))

    def take(self, user_id: uuid.UUID, stream: object) -> bool:
        held = self.open.setdefault(user_id, weakref.WeakSet())
        if len(held) >= self.limit:
            return False
        held.add(stream)
        return True

    def release(self, user_id: uuid.UUID, stream: object) -> None:
        held = self.open.get(user_id)
        if held is not None:
            held.discard(stream)
            if not held:
                self.open.pop(user_id, None)


def slots_of(request: Request) -> StreamSlots:
    slots: StreamSlots | None = getattr(request.app.state, "run_stream_slots", None)
    if slots is None:
        slots = StreamSlots(STREAMS_PER_USER)
        request.app.state.run_stream_slots = slots
    return slots


NO_STORE = {"Cache-Control": "no-store"}

NOT_FOUND = "Run not found."
JOB_NOT_FOUND = "Job not found."
UNKNOWN_AGENT = "agent: no such agent here."
TOO_BIG = f"input: at most {INPUT_MAX_BYTES} bytes of JSON."
NO_CREDENTIAL = "No model credential is saved. Add one on the Account page first."
NOT_CONFIGURED = "Agents cannot run on this server yet: its CREDENTIALS_KEY is not set."
TOO_MANY = f"You already have {runs.ACTIVE_PER_USER} runs queued or running. Wait for one to end."
ENDED = "This run has already ended."
NEEDS_JOB = "job_id: this agent works on a job; name one."
CHAT_AGENT = "agent: a chat agent answers in a thread; send a message to the thread instead."
# Recorded streams are for the fake backend: refused in production, never stored there.
NO_SCRIPT = "input.script: recorded streams exist only outside production."

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such run (or not yours)"}}


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


def sessions_of(request: Request) -> sessionmaker[Session]:
    found: sessionmaker[Session] = request.app.state.sessionmaker
    return found


def run_out(row: AgentRun, job: RunJobOut | None = None) -> RunOut:
    duration = None
    if row.started_at is not None and row.finished_at is not None:
        duration = round((row.finished_at - row.started_at).total_seconds(), 3)
    return RunOut(
        id=row.id,
        agent=row.agent,
        job_id=row.job_id,
        status=row.status,
        input=row.input or {},
        output=row.output,
        error=row.error,
        model=row.model,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cost_usd=float(row.cost_usd) if row.cost_usd is not None else None,
        credential_kind=row.credential_kind,
        cancel_requested=row.cancel_requested_at is not None,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_s=duration,
        job=job,
    )


def job_labels(
    db: Session, user_id: uuid.UUID, rows: Sequence[AgentRun]
) -> dict[uuid.UUID, RunJobOut]:
    """The company and title of the runs' jobs (the caller's jobs only), in one query."""
    ids = {row.job_id for row in rows if row.job_id is not None}
    if not ids:
        return {}
    found = db.execute(
        sa.select(Job.id, Job.company, Job.title).where(Job.user_id == user_id, Job.id.in_(ids))
    ).all()
    return {
        job_id: RunJobOut(id=job_id, company=company, title=title)
        for job_id, company, title in found
    }


def runs_out(db: Session, user_id: uuid.UUID, rows: Sequence[AgentRun]) -> list[RunOut]:
    labels = job_labels(db, user_id, rows)
    return [run_out(row, labels.get(row.job_id) if row.job_id else None) for row in rows]


def _run_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None


# --- agents -------------------------------------------------------------------------------------


@router.get("", response_model=AgentList, responses=_401)
def list_agents(user: CurrentUser, request: Request) -> AgentList:
    env = request.app.state.settings.app_env
    return AgentList(
        items=[
            AgentOut(
                name=agent.name,
                description=agent.description,
                model=agent.model,
                max_turns=agent.max_turns,
                wall_time_s=agent.wall_time_s,
                test_only=agent.test_only,
                needs_job=agent.needs_job,
            )
            # Not the chat agents, which start from their thread (app/agents/chat_router.py),
            # nor the internal ones, which their own module starts.
            for agent in definitions.listed(env)
        ]
    )


# --- start --------------------------------------------------------------------------------------


def _create(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    agent: definitions.AgentDefinition,
    job_id: uuid.UUID | None,
    data: dict[str, Any],
    now: datetime,
) -> RunOut:
    with sessions() as db:
        if credentials.get(db, user_id) is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NO_CREDENTIAL)
        job = jobs_service.get_job(db, user_id, job_id) if job_id is not None else None
        if job_id is not None and job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=JOB_NOT_FOUND)
        label = RunJobOut(id=job.id, company=job.company, title=job.title) if job else None
        try:
            row = runs.create_run(db, user_id, agent.name, job_id, data, agent.model, now)
        except runs.ActiveRunsError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=TOO_MANY,
                headers={"Retry-After": "30"},
            ) from None
        out = run_out(row, label)
        db.commit()
    return out


def _fail_queued(sessions: sessionmaker[Session], run_id: uuid.UUID, now: datetime) -> None:
    with sessions() as db:
        runs.fail_queued(db, run_id, runs.QUEUE_DOWN, now)
        db.commit()


@router.post(
    "/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunOut,
    responses={
        **_401,
        404: {"model": ErrorOut, "description": "No such job (or not yours)"},
        409: {"model": ErrorOut, "description": "No model credential saved"},
        422: {"model": ErrorOut, "description": "Unknown agent or invalid input"},
        429: {"model": ErrorOut, "description": "Too many runs queued or running"},
        503: {"model": ErrorOut, "description": "Not configured, or the queue is down"},
    },
    dependencies=[Depends(_no_store)],
)
async def start_run(body: RunIn, user: CurrentUser, request: Request) -> RunOut:
    """Queue a run of `agent` for the caller. Follow it with GET /api/agents/runs/{id}."""
    try:
        # An internal agent is unknown here: only its own module starts it (runs.create_run).
        agent = definitions.startable(body.agent, request.app.state.settings.app_env)
    except definitions.UnknownAgentError:
        raise HTTPException(status_code=422, detail=UNKNOWN_AGENT) from None
    if agent.chat:
        raise HTTPException(status_code=422, detail=CHAT_AGENT)
    if len(json.dumps(body.input, ensure_ascii=False).encode()) > INPUT_MAX_BYTES:
        raise HTTPException(status_code=422, detail=TOO_BIG)
    try:
        data = agent.validate_input(body.input)
    except definitions.InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if agent.needs_job and body.job_id is None:
        raise HTTPException(status_code=422, detail=NEEDS_JOB)
    if "script" in data and request.app.state.settings.app_env == "production":
        raise HTTPException(status_code=422, detail=NO_SCRIPT)
    if request.app.state.credentials_keyring is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED)
    sessions = sessions_of(request)
    started = await run_in_threadpool(
        _create, sessions, user.id, agent, body.job_id, data, deps.clock(request)
    )
    try:
        await request.app.state.queue.enqueue(
            "run_agent",
            str(started.id),
            queue=queues.AGENTS,
            job_id=queues.agent_run_job_id(started.id),
        )
    except queues.QueueUnavailableError:
        await run_in_threadpool(_fail_queued, sessions, started.id, deps.clock(request))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=runs.QUEUE_DOWN
        ) from None
    return started


# --- read ---------------------------------------------------------------------------------------


def _list(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    limit: int,
    before: datetime | None,
    job_id: uuid.UUID | None,
    agent: str | None,
) -> RunList:
    with sessions() as db:
        rows = runs.list_runs(db, user_id, limit, before, job_id=job_id, agent=agent)
        return RunList(items=runs_out(db, user_id, rows))


@router.get("/runs", response_model=RunList, responses=_401, dependencies=[Depends(_no_store)])
async def list_runs(
    user: CurrentUser,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=LIST_MAX)] = LIST_DEFAULT,
    before: datetime | None = None,
    job_id: uuid.UUID | None = None,
    agent: Annotated[str | None, Query(max_length=64)] = None,
) -> RunList:
    """The caller's runs, newest first; `before` (a created_at) pages back; `job_id` and
    `agent` keep the runs on that job, of that agent."""
    return await run_in_threadpool(
        _list, sessions_of(request), user.id, limit, before, job_id, agent
    )


def _detail(
    sessions: sessionmaker[Session], user_id: uuid.UUID, run_id: uuid.UUID
) -> RunDetail | None:
    with sessions() as db:
        # One snapshot for the run and its events: the final status is committed with the run's
        # last event, and two statements at READ COMMITTED could show one without the other.
        read_snapshot(db)
        row = runs.get_run(db, user_id, run_id)
        if row is None:
            return None
        events = runs.events_after(db, run_id, 0, EVENTS_MAX + 1)
        return RunDetail(
            **runs_out(db, user_id, [row])[0].model_dump(),
            events=[
                EventOut(seq=e.seq, at=e.at, kind=e.kind, data=e.data) for e in events[:EVENTS_MAX]
            ],
            events_truncated=len(events) > EVENTS_MAX,
        )


@router.get(
    "/runs/{run_id}",
    response_model=RunDetail,
    responses={**_401, **_404},
    dependencies=[Depends(_no_store)],
)
async def read_run(run_id: str, user: CurrentUser, request: Request) -> RunDetail:
    found = await run_in_threadpool(_detail, sessions_of(request), user.id, _run_id(run_id))
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return found


# --- stream -------------------------------------------------------------------------------------


def _page(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    after: int,
    feed: live.LiveFeed | None = None,
) -> sse.Page | None:
    """The run's events after `after`, and whether the run has ended. The status is read before
    the events: the final status is committed with the run's last event, so a run read as ended
    has all its events visible to the read that follows. `feed` (the connection's live text) is
    told whether the run is running (it reads Redis only then) and which steps are about to
    be sent."""
    with sessions() as db:
        run = runs.get_run(db, user_id, run_id)
        if run is None:
            return None
        ended = run.status in FINAL_STATUSES
        rows = runs.events_after(db, run_id, after, STREAM_PAGE)
        if feed is not None:
            feed.active(run.status is RunStatus.running)
            feed.stored([(row.seq, row.kind) for row in rows])
        events = [
            sse.Event(
                row.seq,
                "step",
                {"seq": row.seq, "at": row.at.isoformat(), "kind": row.kind, "data": row.data},
            )
            for row in rows
        ]
        return sse.Page(events, final=ended and len(rows) < STREAM_PAGE)


@router.get(
    "/runs/{run_id}/stream",
    responses={
        **_401,
        **_404,
        200: {"content": {"text/event-stream": {}}, "description": "The run's events"},
    },
    response_class=StreamingResponse,
)
async def stream_run(
    run_id: str,
    user: CurrentUser,
    request: Request,
    after: str | None = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Server-sent events of one run: its stored events, then the new ones, then `end`."""
    sessions, parsed = sessions_of(request), _run_id(run_id)

    def agent_of() -> str | None:
        with sessions() as db:
            found = runs.get_run(db, user.id, parsed)
            return found.agent if found is not None else None

    agent = await run_in_threadpool(agent_of)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    slots = slots_of(request)
    start = sse.last_event_id(last_event_id, after)
    settings = {"max_s": STREAM_MAX_S, **getattr(request.app.state, "sse_settings", {})}
    # The answer being written (issue #33): None when live text is turned off, for the server
    # (AGENTS_LIVE_TEXT) or for this run's agent (AgentDefinition.live_text): Redis is then
    # never read for this stream. The feed starts idle and reads only while the run is running
    # (`_page` tells it).
    store: live.LiveStore | None = getattr(request.app.state, "live_store", None)
    feed = (
        live.LiveFeed(store, parsed, running=False)
        if store is not None and definitions.live_text(agent)
        else None
    )
    if feed is not None:
        settings = {"transient_interval_s": live.POLL_S, **settings}

    async def events() -> AsyncIterator[bytes]:
        try:
            async for chunk in sse.replay(
                lambda seq: _page(sessions, user.id, parsed, seq, feed),
                after=start,
                disconnected=request.is_disconnected,
                transient=feed.poll if feed is not None else None,
                **settings,
            ):
                yield chunk
        finally:
            slots.release(user.id, stream)

    stream = events()
    if not slots.take(user.id, stream):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=TOO_MANY_STREAMS,
            headers={"Retry-After": "10"},
        )
    return sse.response(stream)


# --- cancel -------------------------------------------------------------------------------------


def _cancel(
    sessions: sessionmaker[Session], user_id: uuid.UUID, run_id: uuid.UUID, now: datetime
) -> tuple[runs.CancelOutcome, RunOut | None]:
    with sessions() as db:
        outcome = runs.request_cancel(db, user_id, run_id, now)
        db.commit()
        row = runs.get_run(db, user_id, run_id)
        return outcome, run_out(row) if row is not None else None


@router.post(
    "/runs/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunOut,
    responses={**_401, **_404, 409: {"model": ErrorOut, "description": "The run has ended"}},
    dependencies=[Depends(_no_store)],
)
async def cancel_run(run_id: str, user: CurrentUser, request: Request) -> RunOut:
    outcome, out = await run_in_threadpool(
        _cancel, sessions_of(request), user.id, _run_id(run_id), deps.clock(request)
    )
    if outcome is runs.CancelOutcome.missing or out is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    if outcome is runs.CancelOutcome.final:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ENDED)
    return out
