"""/api/jobs/searches: the caller's searches, the main one and the saved ones (jobs J32,
app/jobs/searches.py), and their runs.

- GET /api/jobs/searches: the main search first (`id` "main", the profile's search section), then
  the saved ones in the order they were made, each with its schedule, its next run and its run
  waiting in the line, and the running sweep's id.
- POST /api/jobs/searches: 201 with a new saved search. 422 for a name or criteria the checks
  refuse, a ticked website the person gave no permission for, or past 20; 409 for a name taken.
- GET, PUT, DELETE /api/jobs/searches/{id}: read; change with the revision read (409 with the
  stored revision when it moved); delete (204; its sweeps keep its name). The main search is
  read here and changed in Settings (PUT /api/profile/search): PUT and DELETE of `main` answer 422.
- POST /api/jobs/searches/{id}/run: 202 with the sweep started, or with the run kept in the line
  (`queued`, its place, 1 starts next) when a sweep runs. 422 without a website or for a search
  that no longer validates, 503 when the queue is down.
- DELETE /api/jobs/searches/{id}/request: take back the search's waiting run (204, 404 when none).
- POST /api/jobs/searches/run: Run all, each search in order (the main one first), one outcome
  each.

Another person's search answers 404, like one that does not exist. Mounted before the jobs
router, so /api/jobs/searches is never read as a job id.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import schedule, searches, service, sweep
from app.jobs.models import SavedSearch, SweepRequest
from app.jobs.schemas import (
    SavedSearchChangeIn,
    SavedSearchIn,
    SavedSearchList,
    SavedSearchOut,
    SearchConflictOut,
    SearchRequestOut,
    SearchRunAllOut,
    SearchRunOut,
    SearchScheduleOut,
)
from app.jobs.sweep_router import NO_STORE, _no_store, queue_of, sessions_of, sweep_out
from app.profile.models import Profile
from app.profile.schemas import SearchSection

NOT_FOUND = "Search not found."
NAME_TAKEN = "You already have a search with this name."
TOO_MANY = f"You can save at most {searches.SAVED_MAX} searches."
MAIN_IN_SETTINGS = "The main search is your profile's search: change it in Settings."
MOVED = "This search changed in another tab. Open it again."
NONE_WAITING = "No run of this search is waiting."

router = APIRouter(prefix="/api/jobs/searches", tags=["jobs"])
CurrentUser = Annotated[User, Depends(deps.current_user)]

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such search (or not yours)"}}
_422 = {422: {"model": ErrorOut, "description": "Refused by the checks"}}


def _search_id(value: str) -> uuid.UUID | None:
    """None for the main search; a saved search's id; 404 for anything else."""
    if value == searches.MAIN:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None


def _not_main(value: str) -> uuid.UUID:
    found = _search_id(value)
    if found is None:
        raise HTTPException(status_code=422, detail=MAIN_IN_SETTINGS)
    return found


# --- views --------------------------------------------------------------------------------------


def _request_out(
    line: Sequence[SweepRequest], saved_search_id: uuid.UUID | None
) -> SearchRequestOut | None:
    for place, item in enumerate(line, 1):
        if item.saved_search_id == saved_search_id:
            return SearchRequestOut(
                id=item.id,
                search_id=str(saved_search_id) if saved_search_id else searches.MAIN,
                place=place,
                trigger=item.trigger.value,
                requested_at=item.requested_at,
            )
    return None


def _schedule_out(plan: Any, search: SearchSection | None, now: datetime) -> SearchScheduleOut:
    active = plan.enabled and sweep.has_sources(search)
    return SearchScheduleOut(
        enabled=plan.enabled,
        days=list(plan.days),
        times=list(plan.times),
        timezone=plan.timezone,
        active=active,
        next_run=schedule.next_run(plan, now) if active else None,
    )


class _Person:
    """What every view of one person's searches reads once: the profile, the main search, the
    line."""

    def __init__(self, db: Session, user_id: uuid.UUID) -> None:
        profile = db.get(Profile, user_id)
        raw = (profile.sections or {}).get("search") if profile else None
        self.raw = raw if isinstance(raw, dict) else None
        self.revision = profile.revision if profile else 0
        self.main = sweep.user_search(db, user_id)
        self.timezone = sweep.schedule_of(self.main).timezone
        self.line = searches.waiting(db, user_id)

    def main_out(self, now: datetime) -> SavedSearchOut:
        return SavedSearchOut(
            id=searches.MAIN,
            name=searches.MAIN_NAME,
            main=True,
            revision=self.revision,
            criteria=searches.criteria_from(self.main),
            schedule=_schedule_out(sweep.schedule_of(self.main), self.main, now),
            request=_request_out(self.line, None),
        )

    def saved_out(self, row: SavedSearch, now: datetime) -> SavedSearchOut:
        try:
            merged = SearchSection.model_validate(searches.merged(self.raw, row.criteria))
        except ValidationError:
            merged = None
        return SavedSearchOut(
            id=str(row.id),
            name=row.name,
            main=False,
            revision=row.revision,
            criteria=dict(row.criteria),
            schedule=_schedule_out(searches.schedule_of(row).of(self.timezone), merged, now),
            request=_request_out(self.line, row.id),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def _one(
    sessions: sessionmaker[Session], user_id: uuid.UUID, search_id: uuid.UUID | None, now: datetime
) -> SavedSearchOut | None:
    with sessions() as db:
        person = _Person(db, user_id)
        if search_id is None:
            return person.main_out(now)
        row = searches.get(db, user_id, search_id)
        return None if row is None else person.saved_out(row, now)


def _found(view: SavedSearchOut | None) -> SavedSearchOut:
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return view


# --- read ---------------------------------------------------------------------------------------


def _list(sessions: sessionmaker[Session], user_id: uuid.UUID, now: datetime) -> SavedSearchList:
    with sessions() as db:
        person = _Person(db, user_id)
        running = service.running_sweep(db, user_id)
        return SavedSearchList(
            items=[
                person.main_out(now),
                *(person.saved_out(row, now) for row in searches.listed(db, user_id)),
            ],
            running_sweep_id=running.id if running else None,
        )


@router.get("", response_model=SavedSearchList, responses=_401, dependencies=[Depends(_no_store)])
async def list_searches(user: CurrentUser, request: Request) -> SavedSearchList:
    """The caller's searches, the main one first."""
    return await run_in_threadpool(_list, sessions_of(request), user.id, deps.clock(request))


@router.get(
    "/{search_id}",
    response_model=SavedSearchOut,
    responses={**_401, **_404},
    dependencies=[Depends(_no_store)],
)
async def read_search(search_id: str, user: CurrentUser, request: Request) -> SavedSearchOut:
    found = await run_in_threadpool(
        _one, sessions_of(request), user.id, _search_id(search_id), deps.clock(request)
    )
    return _found(found)


# --- write --------------------------------------------------------------------------------------


def _refuse_unpermitted(
    db: Session, user_id: uuid.UUID, body: SavedSearchIn, now: datetime
) -> None:
    missing = searches.unpermitted(db, user_id, body.criteria, now)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Give your permission to search {missing[0]} in Find jobs first.",
        )


def _create(
    sessions: sessionmaker[Session], user_id: uuid.UUID, body: SavedSearchIn, now: datetime
) -> SavedSearchOut:
    with sessions() as db:
        _refuse_unpermitted(db, user_id, body, now)
        try:
            row = searches.create(db, user_id, body.name, body.criteria, body.schedule)
        except searches.LimitError:
            raise HTTPException(status_code=422, detail=TOO_MANY) from None
        except searches.NameTakenError:
            raise HTTPException(status_code=409, detail=NAME_TAKEN) from None
        db.commit()
        return _Person(db, user_id).saved_out(row, now)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SavedSearchOut,
    responses={**_401, **_422, 409: {"model": ErrorOut, "description": "Name taken"}},
    dependencies=[Depends(_no_store)],
)
async def create_search(user: CurrentUser, request: Request, body: SavedSearchIn) -> SavedSearchOut:
    return await run_in_threadpool(
        _create, sessions_of(request), user.id, body, deps.clock(request)
    )


def _change(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    search_id: uuid.UUID,
    body: SavedSearchChangeIn,
    now: datetime,
) -> SavedSearchOut | JSONResponse:
    with sessions() as db:
        _refuse_unpermitted(db, user_id, body, now)
        try:
            row = searches.change(
                db, user_id, search_id, body.revision, body.name, body.criteria, body.schedule
            )
        except searches.RevisionError as exc:
            conflict = SearchConflictOut(detail=MOVED, revision=exc.revision)
            return JSONResponse(status_code=409, content=conflict.model_dump(), headers=NO_STORE)
        except searches.NameTakenError:
            raise HTTPException(status_code=409, detail=NAME_TAKEN) from None
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
        db.commit()
        return _Person(db, user_id).saved_out(row, now)


@router.put(
    "/{search_id}",
    response_model=SavedSearchOut,
    responses={
        **_401,
        **_404,
        **_422,
        409: {"model": SearchConflictOut | ErrorOut, "description": "Moved, or name taken"},
    },
    dependencies=[Depends(_no_store)],
)
async def change_search(
    search_id: str, user: CurrentUser, request: Request, body: SavedSearchChangeIn
) -> Any:
    return await run_in_threadpool(
        _change, sessions_of(request), user.id, _not_main(search_id), body, deps.clock(request)
    )


def _delete(sessions: sessionmaker[Session], user_id: uuid.UUID, search_id: uuid.UUID) -> bool:
    with sessions() as db:
        gone = searches.delete(db, user_id, search_id)
        db.commit()
        return gone


@router.delete(
    "/{search_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_401, **_404, **_422},
)
async def delete_search(search_id: str, user: CurrentUser, request: Request) -> Response:
    if not await run_in_threadpool(_delete, sessions_of(request), user.id, _not_main(search_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)


# --- runs ---------------------------------------------------------------------------------------


def _waiting(
    sessions: sessionmaker[Session], user_id: uuid.UUID, saved_search_id: uuid.UUID | None
) -> SearchRequestOut | None:
    with sessions() as db:
        return _request_out(searches.waiting(db, user_id), saved_search_id)


async def _run(
    request: Request, user_id: uuid.UUID, saved_search_id: uuid.UUID | None
) -> SearchRunOut:
    sessions = sessions_of(request)
    begun = await sweep.begin(
        sessions,
        queue_of(request),
        user_id,
        deps.clock(request),
        sweep_out,
        saved_search_id=saved_search_id,
        line=True,
    )
    shown = str(saved_search_id) if saved_search_id else searches.MAIN
    waiting = None
    if begun.outcome == "queued":
        waiting = await run_in_threadpool(_waiting, sessions, user_id, saved_search_id)
    return SearchRunOut(
        search_id=shown,
        outcome=begun.outcome,
        sweep=begun.view,
        request=waiting,
        running_sweep_id=begun.sweep_id if begun.outcome == "queued" else None,
    )


@router.post(
    "/run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SearchRunAllOut,
    responses=_401,
    dependencies=[Depends(_no_store)],
)
async def run_all(user: CurrentUser, request: Request) -> SearchRunAllOut:
    """Run every search in order, the main one first: the first that can starts, the others
    wait in the line. A search without a website says `no_websites` and is left out."""
    saved = await run_in_threadpool(_saved_ids, sessions_of(request), user.id)
    items = []
    for saved_search_id in (None, *saved):
        ran = await _run(request, user.id, saved_search_id)
        if ran.outcome != "not_found":
            items.append(ran)
    return SearchRunAllOut(items=items)


def _saved_ids(sessions: sessionmaker[Session], user_id: uuid.UUID) -> list[uuid.UUID]:
    with sessions() as db:
        return [row.id for row in searches.listed(db, user_id)]


@router.post(
    "/{search_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SearchRunOut,
    responses={
        **_401,
        **_404,
        **_422,
        503: {"model": ErrorOut, "description": "The queue is unavailable"},
    },
    dependencies=[Depends(_no_store)],
)
async def run_search(search_id: str, user: CurrentUser, request: Request) -> SearchRunOut:
    """Run the search now, or keep the run in the line behind the running sweep."""
    ran = await _run(request, user.id, _search_id(search_id))
    if ran.outcome == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    if ran.outcome == "queue_down":
        raise HTTPException(status_code=503, detail=sweep.QUEUE_DOWN)
    if ran.outcome == "search_invalid":
        raise HTTPException(status_code=422, detail=sweep.SEARCH_INVALID)
    if ran.outcome == "no_websites":
        raise HTTPException(status_code=422, detail=sweep.NO_WEBSITES)
    return ran


def _take_back(
    sessions: sessionmaker[Session], user_id: uuid.UUID, saved_search_id: uuid.UUID | None
) -> bool:
    with sessions() as db:
        gone = searches.take_back(db, user_id, saved_search_id)
        db.commit()
        return gone


@router.delete(
    "/{search_id}/request",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_401, **_404},
)
async def take_back(search_id: str, user: CurrentUser, request: Request) -> Response:
    """Take back the search's run waiting in the line."""
    if not await run_in_threadpool(
        _take_back, sessions_of(request), user.id, _search_id(search_id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NONE_WAITING)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
