"""/api/opportunities/onboarding: the first login wizard (opportunities O5), onboarding.py.

- GET /api/opportunities/onboarding: the caller's wizard state: `completed_at`, `dismissed_at`,
  `profile_empty`, `done` (the steps the profile already answers), `steps_left`, `open_wizard`
  (the app opens /welcome by itself) and `show_card` ("Finish setting up" on the home page).
- POST /api/opportunities/onboarding/complete: records `completed_at` and starts the first sweep.
  200 `{state, sweep: started|no_boards|search_invalid|queue_down, sweep_id}`; 409
  `{detail, code: "sweep_running", sweep_id, state}` when a sweep already runs: the wizard is
  finished all the same and that sweep is the first one.
- POST /api/opportunities/onboarding/dismiss: "Skip for now", records `dismissed_at`. 200 with the
  state.
- POST /api/opportunities/onboarding/steps `{step}`: a step passed (Next or "Skip this step"), kept
  in the profile (`passed` in the state), so the wizard resumes at the same step on any device.
  200 with the state; 422 for an unknown step. Each new step is one revision of the profile's
  `preferences`: the web reads the profile again before its next save.

Every route needs a session and acts on the caller's profile only; the step answers themselves are
saved through the profile's and apply's own routes. Never cached.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import sweep
from app.opportunities import onboarding
from app.profile.schemas import WizardStep

NO_STORE = {"Cache-Control": "no-store"}
RUNNING = "A sweep is already running: it is your first one."

router = APIRouter(prefix="/api/opportunities/onboarding", tags=["opportunities"])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]
_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


class StateOut(BaseModel):
    completed_at: str | None
    dismissed_at: str | None
    profile_empty: bool
    done: list[str]
    passed: list[str]
    steps_left: list[str]
    open_wizard: bool
    show_card: bool


class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: WizardStep


class CompleteOut(BaseModel):
    state: StateOut
    sweep: Literal["started", "no_boards", "search_invalid", "queue_down"]
    sweep_id: uuid.UUID | None


class RunningOut(BaseModel):
    detail: str
    code: Literal["sweep_running"]
    sweep_id: uuid.UUID
    state: StateOut


def _out(found: onboarding.State) -> StateOut:
    return StateOut(**found.__dict__)


@router.get("", response_model=StateOut, responses=_401, dependencies=[Depends(_no_store)])
def read_state(user: CurrentUser, request: Request, db: Db) -> StateOut:
    """The caller's wizard state."""
    found = onboarding.state(db, user, deps.clock(request))
    db.commit()
    return _out(found)


@router.post("/dismiss", response_model=StateOut, responses=_401, dependencies=[Depends(_no_store)])
def dismiss(user: CurrentUser, request: Request, db: Db) -> StateOut:
    """Skip for now: the wizard no longer opens by itself; the home page shows its card."""
    found = onboarding.dismiss(db, user, deps.clock(request))
    db.commit()
    return _out(found)


@router.post("/steps", response_model=StateOut, responses=_401, dependencies=[Depends(_no_store)])
def pass_step(body: StepIn, user: CurrentUser, request: Request, db: Db) -> StateOut:
    """A step passed: kept in the profile, so the wizard resumes there on any device."""
    found = onboarding.pass_step(db, user, body.step, deps.clock(request))
    db.commit()
    return _out(found)


def _finish(sessions: sessionmaker[Session], user: User, request: Request) -> StateOut:
    with sessions() as db:
        found = onboarding.finish(db, user, deps.clock(request))
        db.commit()
    return _out(found)


@router.post(
    "/complete",
    response_model=CompleteOut,
    responses={**_401, 409: {"model": RunningOut, "description": "A sweep already runs"}},
    dependencies=[Depends(_no_store)],
)
async def complete(user: CurrentUser, request: Request) -> Any:
    """Start: the wizard is finished, then the first sweep starts (in the worker)."""
    sessions: sessionmaker[Session] = request.app.state.sessionmaker
    finished = await run_in_threadpool(_finish, sessions, user, request)
    now = deps.clock(request)
    outcome, sweep_id = await run_in_threadpool(
        onboarding.start_first_sweep, sessions, user.id, now
    )
    if outcome == "running" and sweep_id is not None:
        body = RunningOut(detail=RUNNING, code="sweep_running", sweep_id=sweep_id, state=finished)
        return JSONResponse(status_code=409, content=body.model_dump(mode="json"), headers=NO_STORE)
    if outcome == "started" and sweep_id is not None:
        queue: queues.Queue = request.app.state.queue
        try:
            await queue.enqueue(
                "run_sweep",
                str(sweep_id),
                queue=queues.SWEEPS,
                job_id=queues.sweep_job_id(sweep_id),
            )
        except queues.QueueUnavailableError:
            await run_in_threadpool(sweep.fail_sweep, sessions, sweep_id, sweep.QUEUE_DOWN, now)
            return CompleteOut(state=finished, sweep="queue_down", sweep_id=None)
        return CompleteOut(state=finished, sweep="started", sweep_id=sweep_id)
    reason: Literal["no_boards", "search_invalid"] = (
        "search_invalid" if outcome == "search_invalid" else "no_boards"
    )
    return CompleteOut(state=finished, sweep=reason, sweep_id=None)
