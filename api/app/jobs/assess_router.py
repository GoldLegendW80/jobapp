"""Assess this posting (SPEC-jobs.md, "Assess this posting"; the agent and its checks: assess.py).

- POST /api/jobs/{id}/assess: 202 with the queued run of `jobs_assess` on the caller's posting,
  or with the run already queued or running on it (a second click starts nothing). 404 when
  the posting is not the caller's, 409 without a saved model credential, 429 when the caller
  already has the most runs at once, 503 when the server has no CREDENTIALS_KEY or the queue
  is down (the run is failed at once), in the words of POST /api/agents/runs.
- GET /api/jobs/{id}/assessment: the posting's last assessment (null before the first) and
  its latest run (null before the first click), so the page shows a run under way, one that
  failed and the assessment kept from before. It never calls a model.

Both need a session and act on the caller's own postings only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials, definitions, operator, runs
from app.agents.models import RunStatus
from app.agents.schemas import RunJobOut, RunOut, run_out
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import assess
from app.jobs import service as jobs_service
from app.jobs.models import Job

NOT_FOUND = "Job not found."


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(_no_store)])
CurrentUser = Annotated[User, Depends(deps.current_user)]


class AssessRunOut(BaseModel):
    id: uuid.UUID
    status: RunStatus
    error: str | None


class AssessmentOut(BaseModel):
    run_id: uuid.UUID | None
    created_at: datetime
    # What assess.result_of stored: requirements, more_requirements, culture, signals,
    # verdict, confidence, role_clarity.
    result: dict[str, Any]


class AssessmentView(BaseModel):
    run: AssessRunOut | None
    assessment: AssessmentOut | None


def _sessions(request: Request) -> sessionmaker[Session]:
    found: sessionmaker[Session] = request.app.state.sessionmaker
    return found


def _view(
    sessions: sessionmaker[Session], user_id: uuid.UUID, job_id: uuid.UUID
) -> AssessmentView | None:
    with sessions() as db:
        if jobs_service.get_job(db, user_id, job_id) is None:
            return None
        run = assess.latest_run(db, user_id, job_id)
        row = assess.stored(db, user_id, job_id)
        return AssessmentView(
            run=AssessRunOut(id=run.id, status=run.status, error=run.error) if run else None,
            assessment=AssessmentOut(
                run_id=row.run_id, created_at=row.created_at, result=row.result
            )
            if row
            else None,
        )


def _start(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    agent: definitions.AgentDefinition,
    job_id: uuid.UUID,
    now: datetime,
) -> tuple[RunOut, bool]:
    """The run that answers the click, committed, and whether it is new. The posting's row is
    held to the commit, so two clicks at once on one posting make one run."""
    with sessions() as db:
        job = db.execute(
            jobs_service.jobs_of(user_id).where(Job.id == job_id).with_for_update(key_share=True)
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
        label = RunJobOut(id=job.id, company=job.company, title=job.title)
        run = assess.latest_run(db, user_id, job_id)
        if run is not None and assess.is_active(run):
            return run_out(run, label), False
        if credentials.get(db, user_id) is None:
            raise operator.Refused(status.HTTP_409_CONFLICT, operator.NO_CREDENTIAL)
        try:
            row = runs.create_run(db, user_id, agent.name, job_id, {}, agent.model, now)
        except runs.ActiveRunsError:
            db.rollback()
            raise operator.Refused(
                status.HTTP_429_TOO_MANY_REQUESTS, operator.TOO_MANY, {"Retry-After": "30"}
            ) from None
        out = run_out(row, label)
        db.commit()
    return out, True


def _job_id(value: str) -> uuid.UUID:
    """A path id that is not a UUID names nothing: 404, as a missing posting (jobs router)."""
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None


@router.post(
    "/{job_id}/assess",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunOut,
    responses={
        401: {"model": ErrorOut},
        404: {"model": ErrorOut, "description": "No such posting (or not yours)"},
        409: {"model": ErrorOut, "description": "No model credential saved"},
        429: {"model": ErrorOut, "description": "Too many runs queued or running"},
        503: {"model": ErrorOut, "description": "Not configured, or the queue is down"},
    },
)
async def start(job_id: str, user: CurrentUser, request: Request) -> RunOut:
    posting = _job_id(job_id)
    agent = definitions.get(assess.NAME, request.app.state.settings.app_env)
    operator.require_configured(request.app.state.credentials_keyring)
    sessions = _sessions(request)
    run, created = await run_in_threadpool(
        _start, sessions, user.id, agent, posting, deps.clock(request)
    )
    if created:
        await operator.enqueue(
            request.app.state.queue, sessions, run.id, lambda: deps.clock(request)
        )
    return run


@router.get(
    "/{job_id}/assessment",
    response_model=AssessmentView,
    responses={
        401: {"model": ErrorOut},
        404: {"model": ErrorOut, "description": "No such posting (or not yours)"},
    },
)
async def read(job_id: str, user: CurrentUser, request: Request) -> AssessmentView:
    found = await run_in_threadpool(_view, _sessions(request), user.id, _job_id(job_id))
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return found
