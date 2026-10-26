"""Apply myself's panel (apply A9, app/apply/manual.py):

- POST /api/applications/{id}/manual/open: asks for the session (the job `apply_manual`, one per
  application); the panel asks again while it waits in the browser's queue, to keep its place.
  202 with the status. 409 `not_manual` outside Apply myself, `not_open` once it ended.
- GET /api/applications/{id}/manual: the application's state and the session's status: its
  phase (`waiting` with the place, `open`, `ended`), the documents placed, whether the page shows
  a confirmation, whether a file chooser waits for a document, the seconds before the idle
  timeout and `idle_warning` a minute before it, the command under way and the last outcome; and
  the warnings of the start (`{code, sentence}`: "Your rules exclude this role.").
- POST /api/applications/{id}/manual/actions `{action}`: "Attach my CV" (`attach_cv`), "Attach my
  letter" (`attach_letter`), "Fill my details" (`fill_details`). 202; 409 `no_session` while the
  session is not open, `too_many` past manual.COMMANDS_MAX waiting.
- POST /api/applications/{id}/manual/record `{seen}`: "Record as applied" (`seen` true, offered
  once the page showed a confirmation) and "I submitted it": `submitted`, the posting `applied`,
  the placed versions frozen. 409 `not_manual`, `already_recorded`, `not_open`.
- POST /api/applications/{id}/manual/close: `cancelled` before it is recorded (recorded instead
  once the page showed a confirmation); an open session sees it and ends its lease within a
  second.

The user always comes from the session (default deny, CSRF on every write); another person's
application answers 404, as a missing one. Every answer is `Cache-Control: no-store`; no answer
holds a value of the form.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

import anyio
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy.orm import Session

from app import queue as queues
from app.apply import gate, guardrails, manual, service
from app.apply.models import ApplicationEvent, Mode, State
from app.apply.router import NO_APPLICATION, NO_STORE, ApplicationOut, WarningOut
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import service as jobs_service

logger = logging.getLogger(__name__)

SENTENCES = {
    "not_manual": "This application is not one you send yourself.",
    "not_open": "This application has ended. Start a new one to apply again.",
    "already_recorded": "This application is already recorded as sent.",
    "no_session": "Your browser session for this application is not open.",
    "too_many": "Wait for the app to finish what you asked first.",
    "unavailable": "The browser is not available right now.",
    "busy": "Someone else is changing this posting. Try again in a moment.",
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["apply"], dependencies=[Depends(_no_store)])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["attach_cv", "attach_letter", "fill_details"]


class RecordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seen: StrictBool = False


class LastOut(BaseModel):
    action: str
    outcome: str


class ManualOut(BaseModel):
    state: str
    phase: Literal["waiting", "open", "ended"] | None
    position: int | None = None
    documents: list[str] = []
    confirmation_seen: bool = False
    chooser: bool = False
    idle_left_s: int | None = None
    idle_warning: bool = False
    busy: str | None = None
    last: LastOut | None = None
    warnings: list[WarningOut] = []


_404 = {404: {"model": ErrorOut, "description": "No such application (or not yours)"}}
_409 = {409: {"model": ErrorOut, "description": "Refused, with its code"}}


def _refusal(code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": SENTENCES.get(code, code), "code": code},
        headers=NO_STORE,
    )


def _owned(db: Session, user: User, application_id: str) -> Any:
    try:
        wanted = uuid.UUID(application_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_APPLICATION) from None
    found = service.get_application(db, user.id, wanted)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_APPLICATION)
    return found


def _redis(request: Request, work: Callable[[manual.Channel], Awaitable[Any]]) -> Any:
    """One call on the channel, from this sync route; None when Redis cannot be reached."""

    async def go() -> Any:
        pool = await request.app.state.queue.pool()
        return await work(manual.RedisChannel(pool))

    try:
        return anyio.from_thread.run(go)
    except Exception as exc:
        logger.warning("apply.manual_redis_failed", extra={"error": type(exc).__name__})
        return None


def _warnings(db: Session, application: Any) -> list[WarningOut]:
    """The warnings of the start, as the `created` event keeps their codes."""
    data = db.execute(
        sa.select(ApplicationEvent.data)
        .where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.user_id == application.user_id,
            ApplicationEvent.kind == "created",
        )
        .limit(1)
    ).scalar_one_or_none()
    codes = (data or {}).get("warnings")
    if not isinstance(codes, list):
        return []
    return [
        WarningOut(code=c, sentence=guardrails.WARNINGS[c])
        for c in codes
        if c in guardrails.WARNINGS
    ]


def _status(request: Request, db: Session, application: Any) -> ManualOut:
    found = _redis(request, lambda channel: channel.status(application.id)) or {}
    phase = found.get("phase") if found.get("phase") in ("waiting", "open", "ended") else None
    last = found.get("last") if isinstance(found.get("last"), dict) else None
    return ManualOut(
        state=application.state.value,
        phase=phase,
        position=found.get("position") if isinstance(found.get("position"), int) else None,
        documents=[d for d in found.get("documents") or [] if d in ("cv", "letter")],
        confirmation_seen=found.get("confirmation_seen") is True,
        chooser=found.get("chooser") is True,
        idle_left_s=found.get("idle_left_s") if isinstance(found.get("idle_left_s"), int) else None,
        idle_warning=found.get("idle_warning") is True,
        busy=found.get("busy") if isinstance(found.get("busy"), str) else None,
        last=LastOut(**last) if last and set(last) == {"action", "outcome"} else None,
        warnings=_warnings(db, application),
    )


@router.get("/api/applications/{application_id}/manual", response_model=ManualOut, responses=_404)
def read_manual(application_id: str, request: Request, user: CurrentUser, db: Db) -> ManualOut:
    return _status(request, db, _owned(db, user, application_id))


@router.post(
    "/api/applications/{application_id}/manual/open",
    response_model=ManualOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**_404, **_409},
)
def open_session(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> ManualOut | JSONResponse:
    application = _owned(db, user, application_id)
    if application.mode is not Mode.manual:
        return _refusal("not_manual")
    if application.state not in manual.OPEN:
        return _refusal("not_open")
    if application.state is State.queued:

        async def enqueue() -> bool:
            found: bool = await request.app.state.queue.enqueue(
                "apply_manual",
                str(application.id),
                queue=queues.APPLY,
                job_id=manual.job_id(application.id),
            )
            return found

        try:
            anyio.from_thread.run(enqueue)
        except Exception as exc:
            logger.warning("apply.manual_enqueue_failed", extra={"error": type(exc).__name__})
            return _refusal("unavailable")
    return _status(request, db, application)


@router.post(
    "/api/applications/{application_id}/manual/actions",
    response_model=ManualOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**_404, **_409},
)
def act(
    application_id: str, body: ActionIn, request: Request, user: CurrentUser, db: Db
) -> ManualOut | JSONResponse:
    application = _owned(db, user, application_id)
    if application.mode is not Mode.manual:
        return _refusal("not_manual")
    if application.state is not State.filling:
        return _refusal("no_session")
    pushed = _redis(request, lambda channel: channel.push(application.id, body.action))
    if pushed is None:
        return _refusal("unavailable")
    if not pushed:
        return _refusal("too_many")
    return _status(request, db, application)


@router.post(
    "/api/applications/{application_id}/manual/record",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def record(
    application_id: str, body: RecordIn, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    wanted = _owned(db, user, application_id).id
    try:
        application = manual.record(db, user.id, wanted, deps.clock(request), seen=body.seen)
        db.commit()
    except gate.NotFoundError:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_APPLICATION) from None
    except gate.RefusedError as exc:
        db.rollback()
        return _refusal(exc.code)
    except jobs_service.JobBusyError:
        db.rollback()
        return _refusal("busy")
    # The session sees the state changed and ends its lease within a second.
    return ApplicationOut.model_validate(application)


@router.post(
    "/api/applications/{application_id}/manual/close",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def close(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    wanted = _owned(db, user, application_id).id
    try:
        application = manual.cancel(db, user.id, wanted, deps.clock(request), final_watch=True)
        db.commit()
    except gate.NotFoundError:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_APPLICATION) from None
    except gate.RefusedError as exc:
        db.rollback()
        return _refusal(exc.code)
    except jobs_service.JobBusyError:
        db.rollback()
        return _refusal("busy")
    return ApplicationOut.model_validate(application)
