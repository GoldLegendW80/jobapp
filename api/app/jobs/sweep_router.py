"""/api/jobs/sweeps: start a sweep of the caller's boards, list the caller's sweeps, follow one.

- POST /api/jobs/sweeps: 202 with the new sweep (status running, queued for the worker). 409
  with the running sweep's id when the caller already has one (a stale one, with no heartbeat
  for sweep.STALE_AFTER, is failed first and does not block). 422 when the profile's search
  section has no enabled board. 503 when the queue (Redis) is down: the sweep is then failed at
  once, so it never blocks the next try.
- GET /api/jobs/sweeps?limit=N: the caller's newest sweeps (default 20, at most 100) and their
  automatic schedule (`search.sweep_schedule`, off by default) with its next run. The schedule
  itself is changed through PUT /api/profile/search.
- GET /api/jobs/sweeps/{id}: one sweep, its progress and the jobs it added (best fit first).
- GET /api/jobs/sweeps/{id}/events: the same as server-sent events (app/sse.py): a `progress`
  event each time it changes, then one `done` event with the final state and the jobs added,
  and the stream ends. Polling GET /api/jobs/sweeps/{id} gives the same information.

Another user's sweep answers 404, like one that does not exist. Mounted before the jobs router,
so /api/jobs/sweeps is never read as a job id.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app import sse
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import schedule, service, sweep
from app.jobs.models import Sweep, SweepStatus
from app.jobs.schemas import (
    BoardFailureOut,
    ScheduleOut,
    SourceOut,
    SweepConflictOut,
    SweepDetail,
    SweepJobOut,
    SweepList,
    SweepOut,
)

NOT_FOUND = "Sweep not found."
RUNNING = "A sweep is already running."
LIST_DEFAULT = 20
LIST_MAX = 100
NO_STORE = {"Cache-Control": "no-store"}

router = APIRouter(prefix="/api/jobs/sweeps", tags=["jobs"])
CurrentUser = Annotated[User, Depends(deps.current_user)]

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such sweep (or not yours)"}}


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


def sessions_of(request: Request) -> sessionmaker[Session]:
    found: sessionmaker[Session] = request.app.state.sessionmaker
    return found


def queue_of(request: Request) -> queues.Queue:
    found: queues.Queue = request.app.state.queue
    return found


def sweep_out(row: Sweep) -> SweepOut:
    stats = row.stats or {}
    failures = []
    for item in stats.get("boards_failed") or []:
        if isinstance(item, dict):
            failures.append(
                BoardFailureOut(
                    company=str(item.get("company", "")),
                    ats=str(item.get("ats", "")),
                    org=str(item.get("org", "")),
                    error=str(item.get("error", "")),
                    message=str(item.get("message", item.get("error", ""))),
                )
            )

    def number(name: str) -> int:
        value = stats.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def count(item: dict[str, Any], name: str) -> int:
        value = item.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    sources = [
        SourceOut(
            kind=str(item.get("kind", "")),
            query=str(item.get("query", "")),
            location=item["location"] if isinstance(item.get("location"), str) else None,
            result_pages=count(item, "result_pages"),
            found=count(item, "found"),
            new=count(item, "postings"),
            skipped_by_rule=count(item, "skipped_by_rule"),
            error=item["error"] if isinstance(item.get("error"), str) else None,
        )
        for item in stats.get("sources") or []
        if isinstance(item, dict)
    ]
    duration = stats.get("duration")
    current = stats.get("current_board")
    return SweepOut(
        id=row.id,
        trigger=row.trigger.value,
        status=row.status.value,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=row.error,
        boards_total=number("boards_total"),
        boards_done=number("boards_done"),
        boards_ok=number("boards_ok"),
        boards_failed=failures,
        current_board=current
        if isinstance(current, str) and row.status.value == "running"
        else None,
        seen=number("seen"),
        matched=number("matched"),
        new=number("new"),
        duplicates=number("duplicates"),
        skipped_by_rule=number("skipped_by_rule"),
        duration=float(duration) if isinstance(duration, int | float) else None,
        sources=sources,
    )


def sweep_detail(db: Session, user_id: uuid.UUID, row: Sweep) -> SweepDetail:
    jobs = sweep.new_jobs(db, user_id, row.id, sweep.NEW_JOBS_LISTED)
    return SweepDetail(
        **sweep_out(row).model_dump(),
        new_jobs=[
            SweepJobOut(
                id=job.id,
                company=job.company,
                title=job.title,
                location=job.location,
                fit=job.fit,
                status=job.status.value,
            )
            for job in jobs
        ],
    )


def _sweep_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None


# --- start --------------------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SweepOut,
    responses={
        **_401,
        409: {"model": SweepConflictOut, "description": "A sweep is already running"},
        422: {
            "model": ErrorOut,
            "description": "No enabled board, or a search section to save again",
        },
        503: {"model": ErrorOut, "description": "The queue is unavailable"},
    },
    dependencies=[Depends(_no_store)],
)
async def start_sweep(user: CurrentUser, request: Request) -> Any:
    """Sweep the caller's enabled boards now. The work happens in the worker: follow it with
    GET /api/jobs/sweeps/{id} or its /events stream."""
    begun = await sweep.begin(
        sessions_of(request), queue_of(request), user.id, deps.clock(request), sweep_out
    )
    if begun.outcome == "running" and begun.sweep_id is not None:
        body = SweepConflictOut(detail=RUNNING, sweep_id=begun.sweep_id).model_dump(mode="json")
        return JSONResponse(status_code=409, content=body, headers=NO_STORE)
    if begun.outcome == "queue_down":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=sweep.QUEUE_DOWN
        )
    if begun.outcome == "search_invalid":
        raise HTTPException(status_code=422, detail=sweep.SEARCH_INVALID)
    if begun.outcome == "no_boards" or begun.view is None:
        raise HTTPException(status_code=422, detail=sweep.NO_BOARDS)
    return begun.view


# --- read ---------------------------------------------------------------------------------------


def _list(
    sessions: sessionmaker[Session], user_id: uuid.UUID, limit: int, now: datetime
) -> SweepList:
    with sessions() as db:
        rows = sweep.sweeps_of(db, user_id, limit)
        search = sweep.user_search(db, user_id)
        plan = sweep.schedule_of(search)
        active = plan.enabled and bool(
            sweep.enabled_boards(search)
            or sweep.enabled_sources(search)
            or sweep.enabled_feeds(search)
        )
        return SweepList(
            items=[sweep_out(row) for row in rows],
            schedule=ScheduleOut(
                enabled=plan.enabled,
                days=list(plan.days),
                times=list(plan.times),
                timezone=plan.timezone,
                active=active,
                next_run=schedule.next_run(plan, now) if active else None,
            ),
        )


@router.get("", response_model=SweepList, responses=_401, dependencies=[Depends(_no_store)])
async def list_sweeps(
    user: CurrentUser,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=LIST_MAX)] = LIST_DEFAULT,
) -> SweepList:
    """The caller's newest sweeps first, and their automatic schedule with its next run."""
    return await run_in_threadpool(_list, sessions_of(request), user.id, limit, deps.clock(request))


def _detail(
    sessions: sessionmaker[Session], user_id: uuid.UUID, sweep_id: uuid.UUID
) -> SweepDetail | None:
    with sessions() as db:
        row = service.get_sweep(db, user_id, sweep_id)
        return None if row is None else sweep_detail(db, user_id, row)


@router.get(
    "/{sweep_id}",
    response_model=SweepDetail,
    responses={**_401, **_404},
    dependencies=[Depends(_no_store)],
)
async def read_sweep(sweep_id: str, user: CurrentUser, request: Request) -> SweepDetail:
    found = await run_in_threadpool(_detail, sessions_of(request), user.id, _sweep_id(sweep_id))
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return found


def _snapshot(
    sessions: sessionmaker[Session], user_id: uuid.UUID, sweep_id: uuid.UUID
) -> sse.Snapshot | None:
    with sessions() as db:
        row = service.get_sweep(db, user_id, sweep_id)
        if row is None:
            return None
        if row.status is SweepStatus.running:
            return sse.Snapshot("progress", sweep_out(row).model_dump(mode="json"))
        return sse.Snapshot("done", sweep_detail(db, user_id, row).model_dump(mode="json"), True)


@router.get(
    "/{sweep_id}/events",
    responses={
        **_401,
        **_404,
        200: {"content": {"text/event-stream": {}}, "description": "Progress events"},
    },
    response_class=StreamingResponse,
)
async def sweep_events(sweep_id: str, user: CurrentUser, request: Request) -> StreamingResponse:
    """Server-sent events of one sweep: `progress` while it runs, then `done`, then the end."""
    sessions, parsed = sessions_of(request), _sweep_id(sweep_id)
    if await run_in_threadpool(_snapshot, sessions, user.id, parsed) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    settings = getattr(request.app.state, "sse_settings", {})
    return sse.response(sse.watch(lambda: _snapshot(sessions, user.id, parsed), **settings))
