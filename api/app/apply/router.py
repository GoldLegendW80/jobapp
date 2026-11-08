"""The applications of a person (A2).

- POST /api/postings/{posting_id}/applications `{mode: "manual" | "review" | "apply", dry_run}`
  starts one (`apply` is the Apply button: `direct` when the person's "Let me review each filled
  form before it is sent" is unticked, the default, else `review`; issue #738; it asks the
  documents the posting lacks first, as a Like does, `likes.on_apply`):
  201 with the application and its warnings (`{code, sentence}`: Apply myself on a role the
  person's rules exclude), 409 `{detail, code, application_id}` for a refusal before anything is
  spent (app/apply/guardrails.py lists the codes; `posting_closed` with the page's own sentence
  when the posting's address, read first, says it is closed: jobs/liveness_check.py), 409 `busy`
  when a lock is held past its timeout, 404 for a posting that is not the person's.
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
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.apply import caps, full_auto, guardrails, likes, service
from app.apply.models import Application, ApplicationAnswer, ApplicationEvent, Mode, State
from app.db import get_session
from app.documents import store
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import liveness_check
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


class QuestionOut(BaseModel):
    """One Click Apply's open question (A19): a fixed sentence with the field's label and the
    page's host, never the agent's words; `kind` fact (a text answer) or action. `after_send`:
    asked after a control that may have sent the form (`submit_unknown`): the answer is the
    person's Try again, and the page says the application may have been sent already."""

    sentence: str
    label: str
    host: str
    kind: str
    after_send: bool = False


class QuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(default="", max_length=5000)
    remember: bool = False


class StartedOut(ApplicationOut):
    warnings: list[WarningOut]


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    at: datetime
    kind: str
    data: dict[str, Any]


class FieldOut(BaseModel):
    step: str
    label: str
    kind: str
    value: str
    chosen: str | None = None


class FilledOut(BaseModel):
    fields: list[FieldOut]
    submit: str
    fields_hash: str | None  # hex, sent back with Confirm and send


class ChosenOut(BaseModel):
    label: str
    value: str
    why: str


class ApplicationDetail(ApplicationOut):
    events: list[EventOut]
    question: QuestionOut | None = None
    # One click Apply's agent may have sent it (A19): Try again runs the agent once more.
    agent: bool = False
    hold_until: datetime | None
    now: datetime
    # A20: Review first's apply agent: what it filled (the list the person confirms) and the
    # answers it chose for the person; `review_agent` names such an application.
    review_agent: bool = False
    filled: FilledOut | None = None
    chosen: list[ChosenOut] = []
    summary: str | None = None  # the agent's own account, shown after a failed run


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
    closed = liveness_check.click_refusal(request, user.id, _uuid_or_404(posting_id, NOT_FOUND))
    if closed is not None:
        out = RefusalOut(detail=closed, code="posting_closed")
        return JSONResponse(status_code=409, content=out.model_dump(mode="json"), headers=NO_STORE)
    start = likes.on_apply if body.mode == "apply" else service.start
    try:
        started = start(
            db,
            user.id,
            _uuid_or_404(posting_id, NOT_FOUND),
            _mode_of(db, user, body),
            body.dry_run,
            now=deps.clock(request),
            stub_base=request.app.state.settings.apply_stub_base_url,
            agent_review=body.mode == "apply" and not body.dry_run,
        )
    except service.PostingNotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None
    except (jobs_service.JobBusyError, store.DocumentBusyError):
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
        question=_question(db, application),
        agent=_agent(db, application),
        hold_until=full_auto.hold_until(db, application) if held else None,
        now=deps.clock(request),
        **_filled(db, application, deps.clock(request)),
    )


def _filled(db: Session, application: Application, now: datetime) -> dict[str, Any]:
    """A20: the list of what the agent filled, and the answers it chose (from the run report).
    The list's hash, which a Confirm sends back, only while a held session of the current run
    waits for it (`confirm.live`)."""
    from app.apply import applier, confirm

    review = confirm.review_agent(db, application)
    out: dict[str, Any] = {"review_agent": review}
    listed = confirm.fields_of(db, application) if review else None
    if listed is not None:
        fields = [f for f in listed.get("fields", []) if isinstance(f, dict)]
        hashed = application.answers_hash.hex() if application.answers_hash else None
        out["filled"] = FilledOut(
            fields=[FieldOut.model_validate(f) for f in fields],
            submit=str(listed.get("submit") or ""),
            fields_hash=hashed if confirm.live(db, application, now) else None,
        )
    report = db.get(ApplicationAnswer, (application.id, applier.REPORT_KEY))
    if report is not None and report.user_id == application.user_id:
        rows = report.value.get("chosen") if isinstance(report.value, dict) else None
        said = report.value.get("summary") if isinstance(report.value, dict) else None
        out["summary"] = said if isinstance(said, str) and said else None
        if isinstance(rows, list):
            out["chosen"] = [ChosenOut.model_validate(r) for r in rows if isinstance(r, dict)]
    return out


def _question(db: Session, application: Application) -> QuestionOut | None:
    from app.apply import applier

    row = applier.open_question(db, application)
    if row is None:
        return None
    kind = "action" if row.kind == "question:action" else "fact"
    host = (row.options or [""])[0]
    return QuestionOut(
        sentence=applier.sentence(row),
        label=row.label,
        host=host,
        kind=kind,
        after_send=applier.after_send(application),
    )


def _agent(db: Session, application: Application) -> bool:
    from app.apply import applier

    return application.mode is Mode.direct and applier.ran_agent(db, application)


@router.post(
    "/api/applications/{application_id}/question",
    response_model=ApplicationOut,
    responses={**_404, 409: {"model": ErrorOut, "description": "No question waits"}},
)
def answer_question(
    application_id: str, body: QuestionIn, request: Request, user: CurrentUser, db: Db
) -> dict[str, Any]:
    """The person's answer to One click Apply's question (A19): the application goes on."""
    from app.apply import applier

    try:
        application = applier.answer_question(
            db,
            user.id,
            _uuid_or_404(application_id, NO_APPLICATION),
            body.answer,
            body.remember,
            deps.clock(request),
        )
    except applier.QuestionError as exc:
        db.rollback()
        detail = NO_APPLICATION if exc.status == 404 else exc.detail
        raise HTTPException(status_code=exc.status, detail=detail) from None
    db.commit()
    return _out(application)
