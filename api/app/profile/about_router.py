"""/api/profile/about: the "About you" document, Done, Apply, Undo (SPEC-profile, "About you";
the rules are in about.py and about_text.py).

- GET: the document (made once from the profile the first time), the state of each part, the
  estimate of a Done on each (tokens, and money on an API key only), the last reading with what
  it would change, what the last Apply did, and what is missing (a model key). Never calls a
  model.
- PUT `{version, text}`: the text as typed (autosave); 409 with the current document on another
  version, 422 past the size or for a control character. TEXT_SAVES_PER_MINUTE.
- POST /done `{heading, version}`: read that part with the model (an empty part needs none);
  409 while one is read, without a model key, for a stale version, a heading that is not a part
  or is twice in the text; 422 for a part too long; 429 past `about.DONES_PER_DAY` or
  CLICKS_PER_MINUTE; 503 when the queue cannot take it (the run fails, the click is given back).
- POST /apply, POST /undo, POST /discard: write the last reading through the profile's saves,
  put back what the last Apply replaced, forget the last reading. 409 when the part or the
  profile changed since. POST /rewrite `{heading?, version}`: write a part again from the
  profile, or add the fact blocks no part holds.

Each answers what GET answers, needs a session and acts on the caller's own row only.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import queue as queues
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.profile import about, about_agent, about_text
from app.profile.titles_router import EstimateOut, _guard, _no_store

logger = logging.getLogger(__name__)

TEXT_SAVES_PER_MINUTE = 120  # an autosave a second while typing, two tabs
CLICKS_PER_MINUTE = 10
REFUSED = {
    "running": (status.HTTP_409_CONFLICT, "A part is already being read. Wait for it first."),
    "no_part": (status.HTTP_409_CONFLICT, "This part is no longer in your document."),
    "duplicate": (
        status.HTTP_409_CONFLICT,
        "Two parts have this heading. Rename one of them first.",
    ),
    "too_long": (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "This is too long to read at once. Split it into shorter parts.",
    ),
    "not_text": (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "The text holds a character that cannot be saved.",
    ),
    "no_credential": (status.HTTP_409_CONFLICT, "Add a model key on the Account page first."),
    "busy": (
        status.HTTP_409_CONFLICT,
        "Your assistant is busy with other tasks. Try again in a few minutes.",
    ),
    "asked_enough": (
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"You clicked Done {about.DONES_PER_DAY} times today. Try again tomorrow.",
    ),
    "nothing": (status.HTTP_409_CONFLICT, "There is nothing to apply or undo now."),
    "part_changed": (
        status.HTTP_409_CONFLICT,
        "You changed this part since Done. Click Done again to see what changes.",
    ),
    "profile_changed": (
        status.HTTP_409_CONFLICT,
        "Your profile changed since. Click Done again to see what changes.",
    ),
}
STALE = "This document changed in another tab. The latest version is shown."

router = APIRouter(prefix="/api/profile/about", tags=["profile"], dependencies=[Depends(_no_store)])
Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class PartOut(BaseModel):
    heading: str
    key: str
    kind: str
    state: str  # in_step, edited, elsewhere, new
    duplicate: bool
    too_long: bool
    estimate: EstimateOut | None


class ReadingOut(BaseModel):
    heading: str
    key: str
    status: str  # running, ready, failed
    error: str | None
    lines: list[str]
    refused: list[str]
    can_apply: bool
    stale: bool


class UndoOut(BaseModel):
    heading: str
    lines: list[str]


class AboutOut(BaseModel):
    text: str
    version: int
    updated_at: datetime
    parts: list[PartOut]
    reading: ReadingOut | None
    undo: UndoOut | None
    needs: str | None  # model_key
    advanced: int
    missing_blocks: list[str]
    dones_left: int
    part_max: int
    text_max: int


def _out(found: dict[str, Any]) -> AboutOut:
    for part in found["parts"]:
        cost = (part["estimate"] or {}).get("cost")
        if cost is not None:
            part["estimate"]["cost"] = {**cost, "low": str(cost["low"]), "high": str(cost["high"])}
    return AboutOut.model_validate(found)


class TextIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: int = Field(ge=0)
    # Bounded twice over: about.save_text checks the cleaned text against TEXT_MAX.
    text: str = Field(max_length=2 * about_text.TEXT_MAX)


class DoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: int = Field(ge=0)
    heading: str = Field(min_length=1, max_length=1_000)


class RewriteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: int = Field(ge=0)
    heading: str | None = Field(default=None, min_length=1, max_length=1_000)


def _view(db: Session, user: User, request: Request) -> AboutOut:
    found = about.view(db, user.id, deps.clock(request))
    db.commit()  # the document may have just been written from the profile
    return _out(found)


def _refused(db: Session, code: str) -> HTTPException:
    db.rollback()
    status_code, detail = REFUSED[code]
    return HTTPException(status_code=status_code, detail=detail)


def _stale(db: Session, user: User, request: Request) -> JSONResponse:
    db.rollback()
    current = _view(db, user, request)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": STALE, "current": current.model_dump(mode="json")},
    )


_ANSWERS = {401: {"model": ErrorOut}, 409: {"model": ErrorOut}, 429: {"model": ErrorOut}}


@router.get("", response_model=AboutOut, responses={401: {"model": ErrorOut}})
def read(user: CurrentUser, request: Request, db: Db) -> AboutOut:
    return _view(db, user, request)


@router.put("", response_model=AboutOut, responses={**_ANSWERS, 422: {"model": ErrorOut}})
def save(body: TextIn, user: CurrentUser, request: Request, db: Db) -> AboutOut | JSONResponse:
    _guard(request, user, "about_text_saves", TEXT_SAVES_PER_MINUTE)
    try:
        about.save_text(db, user.id, body.version, body.text, deps.clock(request))
    except about.StaleError:
        return _stale(db, user, request)
    except about.RefusedError as refused:
        raise _refused(db, refused.code) from None
    return _view(db, user, request)


@router.post(
    "/done",
    response_model=AboutOut,
    responses={**_ANSWERS, 422: {"model": ErrorOut}, 503: {"model": ErrorOut}},
)
def done(body: DoneIn, user: CurrentUser, request: Request, db: Db) -> AboutOut | JSONResponse:
    _guard(request, user, "about_clicks", CLICKS_PER_MINUTE)
    now = deps.clock(request)
    try:
        run_id = about.start(db, user.id, body.heading, body.version, now)
    except about.StaleError:
        return _stale(db, user, request)
    except about.RefusedError as refused:
        raise _refused(db, refused.code) from None
    db.commit()
    if run_id is not None and not _queued(request, run_id):
        about.not_queued(db, user.id, run_id, now)
        db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=about.ERRORS["queue_down"])
    return _view(db, user, request)


def _queued(request: Request, run_id: uuid.UUID) -> bool:
    queue = request.app.state.queue
    try:
        anyio.from_thread.run(
            lambda: queue.enqueue(
                about_agent.JOB, str(run_id), queue=queues.AGENTS, job_id=about_agent.job_id(run_id)
            )
        )
    except queues.QueueUnavailableError:
        logger.warning("profile.about.run_not_queued", extra={"run_id": str(run_id)})
        return False
    return True


def _click(step: str) -> Any:
    def route(user: CurrentUser, request: Request, db: Db) -> AboutOut:
        _guard(request, user, "about_clicks", CLICKS_PER_MINUTE)
        try:
            getattr(about, step)(db, user.id, deps.clock(request))
        except about.RefusedError as refused:
            raise _refused(db, refused.code) from None
        db.commit()
        return _view(db, user, request)

    route.__name__ = step
    return route


for _step in ("apply", "undo", "discard"):
    router.add_api_route(
        f"/{_step}", _click(_step), methods=["POST"], response_model=AboutOut, responses=_ANSWERS
    )


@router.post("/rewrite", response_model=AboutOut, responses=_ANSWERS)
def rewrite(
    body: RewriteIn, user: CurrentUser, request: Request, db: Db
) -> AboutOut | JSONResponse:
    _guard(request, user, "about_clicks", CLICKS_PER_MINUTE)
    try:
        about.rewrite(db, user.id, body.heading, body.version, deps.clock(request))
    except about.StaleError:
        return _stale(db, user, request)
    except about.RefusedError as refused:
        raise _refused(db, refused.code) from None
    return _view(db, user, request)
