"""/api/agents: the agents a user may start, and their runs (SPEC-agents.md, API).

- GET /api/agents: the agents available here (test agents only outside production).
- POST /api/agents/runs {agent, job_id?, input}: 202 with the queued run. 422 when the agent is
  unknown here or the input does not fit it (the message names the field); 404 when job_id is
  not one of the caller's jobs; 409 without a saved model credential; 429 when the caller already
  has runs.ACTIVE_PER_USER queued or running; 503 when the server has no CREDENTIALS_KEY or the
  queue (Redis) is down (the run is then failed at once, never left queued).
- GET /api/agents/runs?limit=&before=: the caller's runs, newest first.
- GET /api/agents/runs/{id}: one run with its events (at most EVENTS_MAX; the stream replays all).
- POST /api/agents/runs/{id}/cancel: 202 with the run: cancelled at once when still queued, or
  `cancel_requested` while the worker stops it (within about a second); 409 when it has ended.

Another user's run answers 404 like one that does not exist, for reads and cancel alike. The
user always comes from the session (default deny, CSRF on every write). Nothing here logs an
input or an output.
"""

import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import credentials, definitions, runs
from app.agents.models import AgentRun
from app.agents.schemas import AgentList, AgentOut, EventOut, RunDetail, RunIn, RunList, RunOut
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import service as jobs_service

router = APIRouter(prefix="/api/agents", tags=["agents"])
CurrentUser = Annotated[User, Depends(deps.current_user)]

INPUT_MAX_BYTES = 16_384
LIST_DEFAULT = 30
LIST_MAX = 100
EVENTS_MAX = 2000
NO_STORE = {"Cache-Control": "no-store"}

NOT_FOUND = "Run not found."
JOB_NOT_FOUND = "Job not found."
UNKNOWN_AGENT = "agent: no such agent here."
TOO_BIG = f"input: at most {INPUT_MAX_BYTES} bytes of JSON."
NO_CREDENTIAL = "No model credential is saved. Add one on the Account page first."
NOT_CONFIGURED = "Agents cannot run on this server yet: its CREDENTIALS_KEY is not set."
TOO_MANY = f"You already have {runs.ACTIVE_PER_USER} runs queued or running. Wait for one to end."
ENDED = "This run has already ended."

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such run (or not yours)"}}


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


def sessions_of(request: Request) -> sessionmaker[Session]:
    found: sessionmaker[Session] = request.app.state.sessionmaker
    return found


def run_out(row: AgentRun) -> RunOut:
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
        cancel_requested=row.cancel_requested_at is not None,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_s=duration,
    )


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
            )
            for agent in definitions.available(env).values()
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
        if job_id is not None and jobs_service.get_job(db, user_id, job_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=JOB_NOT_FOUND)
        try:
            row = runs.create_run(db, user_id, agent.name, job_id, data, agent.model, now)
        except runs.ActiveRunsError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=TOO_MANY,
                headers={"Retry-After": "30"},
            ) from None
        out = run_out(row)
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
        agent = definitions.get(body.agent, request.app.state.settings.app_env)
    except definitions.UnknownAgentError:
        raise HTTPException(status_code=422, detail=UNKNOWN_AGENT) from None
    if len(json.dumps(body.input, ensure_ascii=False).encode()) > INPUT_MAX_BYTES:
        raise HTTPException(status_code=422, detail=TOO_BIG)
    try:
        data = agent.validate_input(body.input)
    except definitions.InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
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
    sessions: sessionmaker[Session], user_id: uuid.UUID, limit: int, before: datetime | None
) -> RunList:
    with sessions() as db:
        return RunList(items=[run_out(row) for row in runs.list_runs(db, user_id, limit, before)])


@router.get("/runs", response_model=RunList, responses=_401, dependencies=[Depends(_no_store)])
async def list_runs(
    user: CurrentUser,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=LIST_MAX)] = LIST_DEFAULT,
    before: datetime | None = None,
) -> RunList:
    """The caller's runs, newest first; `before` (a created_at) pages back."""
    return await run_in_threadpool(_list, sessions_of(request), user.id, limit, before)


def _detail(
    sessions: sessionmaker[Session], user_id: uuid.UUID, run_id: uuid.UUID
) -> RunDetail | None:
    with sessions() as db:
        row = runs.get_run(db, user_id, run_id)
        if row is None:
            return None
        events = runs.events_after(db, run_id, 0, EVENTS_MAX + 1)
        return RunDetail(
            **run_out(row).model_dump(),
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
