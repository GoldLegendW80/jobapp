"""/api/profile/title-suggestions: the job titles agent's last suggestions, and "More ideas"
(SPEC-profile, "Job title suggestions"; the rules are in titles.py).

- GET answers what the pages show: the stored suggestions, checked again against the search as
  saved now, whether a run is on its way or why the last one gave nothing, what is missing for
  a run (`needs`: a model key, or a CV or skills to read), the clicks left today and the
  estimate (tokens, and money on an API key only). It never calls a model.
- POST asks for a new run on a click: 409 while one is on its way, without a model key, with
  nothing to read, or when the person's runs are busy; 429 past `titles.ASKS_PER_DAY` clicks a
  day or past CLICKS_PER_MINUTE (the HTTP guard, in this process); 503 when the queue cannot
  take it (the run fails at once and the click is given back). It answers what GET answers.

Both need a session and act on the caller's own row only.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import queue as queues
from app.agents.ratelimit import RateLimiter
from app.db import get_session
from app.identity import deps, throttle
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.profile import agents, titles

logger = logging.getLogger(__name__)

CLICKS_PER_MINUTE = 5
WINDOW_S = 60.0
REFUSED = {
    "running": (status.HTTP_409_CONFLICT, "Suggestions are already on their way."),
    "no_credential": (status.HTTP_409_CONFLICT, "Add a model key on the Account page first."),
    "nothing_to_read": (
        status.HTTP_409_CONFLICT,
        "Add your CV or your skills first, so there is something to suggest from.",
    ),
    "search_invalid": (
        status.HTTP_409_CONFLICT,
        "Your search settings no longer pass the checks. Open the Search tab and save them again.",
    ),
    "busy": (
        status.HTTP_409_CONFLICT,
        "Your assistant is busy with other tasks. Try again in a few minutes.",
    ),
    "asked_enough": (
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"You asked for more ideas {titles.ASKS_PER_DAY} times today. Try again tomorrow.",
    ),
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/api/profile/title-suggestions",
    tags=["profile"],
    dependencies=[Depends(_no_store)],
)

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class TokensOut(BaseModel):
    low: int
    high: int


class CostOut(BaseModel):
    currency: str
    low: str
    high: str


class EstimateOut(BaseModel):
    tokens: TokensOut
    cost: CostOut | None


class SuggestionOut(BaseModel):
    title: str
    reason: str
    language: str


class SuggestionsOut(BaseModel):
    status: str  # idle, running, failed
    needs: str | None  # model_key, cv
    suggestions: list[SuggestionOut]
    refreshed_at: datetime | None
    error: str | None
    asks_left: int
    asks_per_day: int
    estimate: EstimateOut | None
    cost_note: str | None


def _out(found: dict[str, Any]) -> SuggestionsOut:
    estimate = found["estimate"]
    if estimate is not None and estimate["cost"] is not None:
        cost = estimate["cost"]
        shown = {**cost, "low": str(cost["low"]), "high": str(cost["high"])}
        estimate = {**estimate, "cost": shown}
    return SuggestionsOut.model_validate({**found, "estimate": estimate})


def _clicks(request: Request) -> RateLimiter:
    """The clicks of each person in the last minute, in this process, made on first use."""
    found: RateLimiter | None = getattr(request.app.state, "title_suggestion_clicks", None)
    if found is None:
        found = RateLimiter(CLICKS_PER_MINUTE, WINDOW_S)
        request.app.state.title_suggestion_clicks = found
    return found


@router.get("", response_model=SuggestionsOut, responses={401: {"model": ErrorOut}})
def read(user: CurrentUser, request: Request, db: Db) -> SuggestionsOut:
    found = titles.view(db, user.id, deps.clock(request))
    db.rollback()
    return _out(found)


@router.post(
    "",
    response_model=SuggestionsOut,
    responses={
        401: {"model": ErrorOut},
        409: {"model": ErrorOut, "description": "A run is on its way, or none can start"},
        429: {"model": ErrorOut, "description": "Too many clicks; see Retry-After"},
        503: {"model": ErrorOut, "description": "The queue cannot take the run"},
    },
)
def more_ideas(user: CurrentUser, request: Request, db: Db) -> SuggestionsOut:
    wait = _clicks(request).take(user.id)
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many clicks. {throttle.wait_phrase(wait)}",
            headers={"Retry-After": str(wait)},
        )
    now = deps.clock(request)
    try:
        run_id = titles.start(db, user.id, now, auto=False)
    except titles.RefusedError as refused:
        db.rollback()
        code, detail = REFUSED[refused.code]
        raise HTTPException(status_code=code, detail=detail) from None
    db.commit()
    if not _queued(request, run_id):
        titles.not_queued(db, user.id, run_id, now)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=titles.ERRORS["queue_down"]
        )
    found = titles.view(db, user.id, now)
    db.rollback()
    return _out(found)


def _queued(request: Request, run_id: uuid.UUID) -> bool:
    queue = request.app.state.queue
    try:
        anyio.from_thread.run(
            lambda: queue.enqueue(
                agents.JOB, str(run_id), queue=queues.AGENTS, job_id=agents.job_id(run_id)
            )
        )
    except queues.QueueUnavailableError:
        logger.warning("profile.titles.run_not_queued", extra={"run_id": str(run_id)})
        return False
    return True
