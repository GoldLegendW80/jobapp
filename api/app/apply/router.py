"""The applications of a person (A2).

- POST /api/postings/{posting_id}/applications `{mode: "manual" | "review", dry_run}` starts one:
  201 with the application and its warnings (`{code, sentence}`: Apply myself on a role the
  person's rules exclude), 409 `{detail, code, application_id}` for a refusal before anything is
  spent (app/apply/guardrails.py lists the codes), 404 for a posting that is not the person's.
  Full auto is never started here: its applications come from a Like while Full auto is on (A13),
  so a request cannot skip its switch and its authorisation. A rehearsal (`dry_run`) is Review
  first only.
- GET /api/applications (`posting_id`, `limit`) lists the person's applications, newest first.
- GET /api/applications/{id} is one, with its timeline (ids, codes and counts only).

The user always comes from the session (default deny, CSRF on every write); another person's
posting or application answers 404, as a missing one. Every answer is `Cache-Control: no-store`.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.orm import Session

from app.apply import guardrails, service
from app.apply.models import Application, ApplicationEvent, Mode
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut

NOT_FOUND = "No such posting."
NO_APPLICATION = "No such application."
NO_STORE = {"Cache-Control": "no-store"}


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(tags=["apply"], dependencies=[Depends(_no_store)])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["manual", "review"] = "review"
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


class ApplicationList(BaseModel):
    items: list[ApplicationOut]


_404 = {404: {"model": ErrorOut, "description": "No such posting or application (or not yours)"}}
_409 = {409: {"model": RefusalOut, "description": "Refused before anything is spent"}}


def _uuid_or_404(value: str, detail: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from None


def _out(application: Application) -> dict[str, Any]:
    return ApplicationOut.model_validate(application).model_dump()


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
            Mode(body.mode),
            body.dry_run,
            now=deps.clock(request),
            stub_base=request.app.state.settings.apply_stub_base_url,
        )
    except service.PostingNotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None
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
def read_application(application_id: str, user: CurrentUser, db: Db) -> ApplicationDetail:
    """One of the caller's applications with its timeline."""
    application = service.get_application(db, user.id, _uuid_or_404(application_id, NO_APPLICATION))
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION)
    timeline: list[ApplicationEvent] = service.events(db, user.id, application.id)
    return ApplicationDetail(
        **_out(application), events=[EventOut.model_validate(e) for e in timeline]
    )
