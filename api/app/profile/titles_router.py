"""/api/profile/title-suggestions: the job titles agent's last suggestions, "More ideas", a
message to the agent, "Not for me" (SPEC-profile, "Job title suggestions"; the rules are in
titles.py).

- GET answers what the pages show: the stored suggestions, checked again against the search as
  saved now, whether a run is on its way, why the last one gave nothing or that it added
  nothing (`note`), what is missing for a run (`needs`: a model key, or something to read: a
  CV, a profile or a message), the person's kept messages and the
  estimate for what a run reads now (tokens, and money on an API key only). It never calls a
  model.
- POST asks for a new run on a click, `{message?}` with what the person tells the agent (at
  most titles.MESSAGE_MAX characters, kept with the last ones once the run starts; 422 past
  it): 409 while one is on its way, without a model key, with nothing to read, or when the
  person's runs are busy; 429 past CLICKS_PER_MINUTE
  (the HTTP guard, in this process); 503 when the queue cannot take it (the run fails at once).
- POST /dismiss `{title}`: "Not for me", the idea leaves and never comes back; 422 for a title
  that is not plain words. DELETE /messages: the agent forgets what the person wrote. Both past
  EDITS_PER_MINUTE: 429. No model is called.

Each answers what GET answers, needs a session and acts on the caller's own row only.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
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
EDITS_PER_MINUTE = 30  # "Not for me" and clearing the messages: no model call
WINDOW_S = 60.0
MESSAGE_TOO_LONG = f"Write at most {titles.MESSAGE_MAX} characters, in one short message."
MESSAGE_EMPTY = "Write what you want to do first."
NOT_A_TITLE = "This is not a job title the suggestions hold."
REFUSED = {
    "running": (status.HTTP_409_CONFLICT, "Suggestions are already on their way."),
    "no_credential": (status.HTTP_409_CONFLICT, "Add a model key on the Account page first."),
    "nothing_to_read": (
        status.HTTP_409_CONFLICT,
        "Add your CV, fill your profile or tell the assistant what you want to do first.",
    ),
    "search_invalid": (
        status.HTTP_409_CONFLICT,
        "Your search settings no longer pass the checks. Open the Search tab and save them again.",
    ),
    "busy": (
        status.HTTP_409_CONFLICT,
        "Your assistant is busy with other tasks. Try again in a few minutes.",
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


class MessageOut(BaseModel):
    text: str
    at: str


class SuggestionsOut(BaseModel):
    status: str  # idle, running, failed
    needs: str | None  # model_key, input
    suggestions: list[SuggestionOut]
    refreshed_at: datetime | None
    error: str | None
    note: str | None  # the last ask ended well and added nothing
    messages: list[MessageOut]  # oldest first
    message_max: int
    estimate: EstimateOut | None
    cost_note: str | None


def _out(found: dict[str, Any]) -> SuggestionsOut:
    estimate = found["estimate"]
    if estimate is not None and estimate["cost"] is not None:
        cost = estimate["cost"]
        shown = {**cost, "low": str(cost["low"]), "high": str(cost["high"])}
        estimate = {**estimate, "cost": shown}
    return SuggestionsOut.model_validate({**found, "estimate": estimate})


class MoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Bounded twice over: the cleaned text is checked against titles.MESSAGE_MAX below.
    message: str | None = Field(default=None, max_length=4 * titles.MESSAGE_MAX)


class DismissIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=4 * titles.TITLE_MAX)


def _limiter(request: Request, name: str, per_minute: int) -> RateLimiter:
    """The clicks of each person in the last minute, in this process, made on first use."""
    found: RateLimiter | None = getattr(request.app.state, name, None)
    if found is None:
        found = RateLimiter(per_minute, WINDOW_S)
        setattr(request.app.state, name, found)
    return found


def _guard(request: Request, user: User, name: str, per_minute: int) -> None:
    wait = _limiter(request, name, per_minute).take(user.id)
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many clicks. {throttle.wait_phrase(wait)}",
            headers={"Retry-After": str(wait)},
        )


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
        422: {"model": ErrorOut, "description": "A message that is empty or too long"},
        429: {"model": ErrorOut, "description": "Too many clicks; see Retry-After"},
        503: {"model": ErrorOut, "description": "The queue cannot take the run"},
    },
)
def more_ideas(
    user: CurrentUser, request: Request, db: Db, body: MoreIn | None = None
) -> SuggestionsOut:
    _guard(request, user, "title_suggestion_clicks", CLICKS_PER_MINUTE)
    message = None
    if body is not None and body.message is not None:
        message = titles.clean_message(body.message)
        if message is None:
            empty = not body.message.strip()
            detail = MESSAGE_EMPTY if empty else MESSAGE_TOO_LONG
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)
    now = deps.clock(request)
    try:
        run_id = titles.start(db, user.id, now, auto=False, message=message)
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


_EDITS = {
    401: {"model": ErrorOut},
    429: {"model": ErrorOut, "description": "Too many clicks; see Retry-After"},
}


@router.post(
    "/dismiss",
    response_model=SuggestionsOut,
    responses={**_EDITS, 422: {"model": ErrorOut, "description": "Not a plain title"}},
)
def dismiss(body: DismissIn, user: CurrentUser, request: Request, db: Db) -> SuggestionsOut:
    _guard(request, user, "title_suggestion_edits", EDITS_PER_MINUTE)
    if not titles.dismiss(db, user.id, body.title):
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=NOT_A_TITLE)
    db.commit()
    found = titles.view(db, user.id, deps.clock(request))
    db.rollback()
    return _out(found)


@router.delete("/messages", response_model=SuggestionsOut, responses=_EDITS)
def clear_messages(user: CurrentUser, request: Request, db: Db) -> SuggestionsOut:
    _guard(request, user, "title_suggestion_edits", EDITS_PER_MINUTE)
    titles.clear_messages(db, user.id)
    db.commit()
    found = titles.view(db, user.id, deps.clock(request))
    db.rollback()
    return _out(found)
