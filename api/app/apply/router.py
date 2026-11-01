"""The applications of a person (A2).

- POST /api/postings/{posting_id}/applications `{mode: "manual" | "review" | "apply", dry_run}`
  starts one (`apply` is the Apply button: `direct` when the person's "Let me review each filled
  form before it is sent" is unticked, the default, else `review`; issue #738):
  201 with the application and its warnings (`{code, sentence}`: Apply myself on a role the
  person's rules exclude), 409 `{detail, code, application_id}` for a refusal before anything is
  spent (app/apply/guardrails.py lists the codes), 409 `busy` when a lock is held past its
  timeout, 404 for a posting that is not the person's.
  Full auto is never started here: its applications come from a Like while Full auto is on (A13),
  so a request cannot skip its switch and its authorisation. A rehearsal (`dry_run`) is Review
  first only.
- GET /api/applications (`posting_id`, `limit`) lists the person's applications, newest first.
- GET /api/applications/{id} is one, with its timeline (ids, codes and counts only), the end of
  the hold of a `held` Full auto item (`hold_until`) and the server's time (`now`), so the
  page's countdown never reads the page's clock.

The user always comes from the session (default deny, CSRF on every write); another person's
posting or application answers 404, as a missing one. Every answer is `Cache-Control: no-store`.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.apply import caps, full_auto, guardrails, service
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import service as jobs_service

NOT_FOUND = "No such posting."
NO_APPLICATION = "No such application."
NO_STORE = {"Cache-Control": "no-store"}
BUSY = "This posting is busy with another change. Try again in a moment."


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(tags=["apply"], dependencies=[Depends(_no_store)])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # "apply": the Apply button. The person's setting decides (owner, 2026-11-01): one click
    # Apply (`direct`) when "Let me review each filled form" is unticked, else Review first.
    mode: Literal["manual", "review", "apply"] = "review"
    dry_run: bool = False

    @model_validator(mode="after")
    def _manual_is_never_dry(self) -> "StartIn":
        if self.dry_run and self.mode == "manual":
            raise ValueError("Apply myself cannot be a rehearsal.")
        return self


class WarningOut(BaseModel):
    code: str
    sentence: str


class RefusalOut(BaseModel):
    detail: str
    code: str
    application_id: uuid.UUID | None = None


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    posting_id: uuid.UUID
    mode: str
    dry_run: bool
    state: str
    ats: str
    form_url: str
    attempt: int
    needs: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None


class StartedOut(ApplicationOut):
    warnings: list[WarningOut]


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    at: datetime
    kind: str
    data: dict[str, Any]


class ApplicationDetail(ApplicationOut):
    events: list[EventOut]
    hold_until: datetime | None
    now: datetime


class ApplicationList(BaseModel):
    items: list[ApplicationOut]


_404 = {404: {"model": ErrorOut, "description": "No such posting or application (or not yours)"}}
_409 = {409: {"model": RefusalOut, "description": "Refused before anything is spent"}}


def _uuid_or_404(value: str, detail: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from None


def _busy(db: Session) -> JSONResponse:
    """A lock held past its timeout: a refusal with its code, like the others."""
    db.rollback()
    out = RefusalOut(detail=BUSY, code="busy")
    return JSONResponse(status_code=409, content=out.model_dump(mode="json"), headers=NO_STORE)


def _out(application: Application) -> dict[str, Any]:
    return ApplicationOut.model_validate(application).model_dump()


def _mode_of(db: Session, user: User, body: StartIn) -> Mode:
    if body.mode != "apply":
        return Mode(body.mode)
    if body.dry_run or caps.settings_of(db, user.id).review_before_send:
        return Mode.review
    return Mode.direct


@router.post(
    "/api/postings/{posting_id}/applications",
    status_code=status.HTTP_201_CREATED,
    response_model=StartedOut,
    responses={**_404, **_409},
)
def start_application(
    posting_id: str, body: StartIn, request: Request, user: CurrentUser, db: Db
) -> StartedOut | JSONResponse:
    """Start an application on one of the caller's postings, or say why not."""
    try:
        started = service.start(
            db,
            user.id,
            _uuid_or_404(posting_id, NOT_FOUND),
            _mode_of(db, user, body),
            body.dry_run,
            now=deps.clock(request),
            stub_base=request.app.state.settings.apply_stub_base_url,
        )
    except service.PostingNotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None
    except jobs_service.JobBusyError:
        return _busy(db)
    except DBAPIError as exc:
        # A later statement that waited past the lock timeout (the insert behind another writer's).
        if not jobs_service.is_lock_timeout(exc):
            raise
        return _busy(db)
    except service.RefusedError as exc:
        db.rollback()
        refusal = exc.refusal
        out = RefusalOut(
            detail=refusal.sentence, code=refusal.code, application_id=refusal.application_id
        )
        return JSONResponse(status_code=409, content=out.model_dump(mode="json"), headers=NO_STORE)
    warnings = [
        WarningOut(code=code, sentence=guardrails.WARNINGS[code]) for code in started.warnings
    ]
    return StartedOut(**_out(started.application), warnings=warnings)


@router.get("/api/applications", response_model=ApplicationList)
def list_applications(
    user: CurrentUser,
    db: Db,
    posting_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=service.LIST_LIMIT_MAX)] = 50,
) -> ApplicationList:
    """The caller's applications, newest first, optionally of one posting."""
    found = service.list_applications(db, user.id, posting_id, limit)
    return ApplicationList(items=[ApplicationOut.model_validate(a) for a in found])


@router.get("/api/applications/{application_id}", response_model=ApplicationDetail, responses=_404)
def read_application(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> ApplicationDetail:
    """One of the caller's applications with its timeline."""
    application = service.get_application(db, user.id, _uuid_or_404(application_id, NO_APPLICATION))
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION)
    timeline: list[ApplicationEvent] = service.events(db, user.id, application.id)
    held = application.state is State.held
    return ApplicationDetail(
        **_out(application),
        events=[EventOut.model_validate(e) for e in timeline],
        hold_until=full_auto.hold_until(db, application) if held else None,
        now=deps.clock(request),
    )
