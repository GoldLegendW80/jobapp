"""The person's decisions on an application (apply A6; SPEC-apply, "API").

- GET /api/applications/{id}/approval: what the approval covers: the fields of the filled form
  (label, kind, value, source, required; a field left is `you_answer`), the documents filled
  (version, fit, pages, how many corrections), the screenshots, and `answers_hash`, the hex
  SHA-256 the approve call sends back.
- GET /api/applications/{id}/screenshots/{shot_id}: one JPEG, owner only.
- POST /api/applications/{id}/approve `{answers_hash}`: the person's approval, then the gate's
  decision (`gate.authorise_approval`, `gate.decide`), committed before the submit lease is
  queued. 200 with the application `approved`; 409 `{detail, code}` for a refusal (the gate's
  codes), and for a stale hash `{detail, code: "stale", answers_hash, answers}` with the current
  fields.
- POST /api/applications/{id}/reject, /cancel: `cancelled` (reject: the form as filled, before
  an approval; cancel: anything before the click).
- POST /api/applications/{id}/submitted: "It was submitted" on `submit_unknown`.
- POST /api/applications/{id}/retry: "Try again" after `submit_unknown` or `refused_by_site`:
  back to `ready`, where a new approval is needed.

The user always comes from the session (default deny, CSRF on every write); another person's
application answers 404, as a missing one. Every answer is `Cache-Control: no-store`. A lock
held past its timeout answers 409 `busy`.
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import queue as queues
from app.apply import admin, gate, service, shots
from app.apply.models import AnswerSource, Application
from app.apply.router import NO_APPLICATION, NO_STORE, ApplicationOut
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import service as jobs_service

logger = logging.getLogger(__name__)

SUBMIT_FUNCTION = "apply_submit"


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(tags=["apply"], dependencies=[Depends(_no_store)])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers_hash: str = Field(min_length=gate.HASH_HEX, max_length=gate.HASH_HEX)


class AnswerOut(BaseModel):
    field_key: str
    label: str
    kind: str
    value: Any
    source: str
    required: bool
    approved: bool
    you_answer: bool  # left by the app: the person answers it on the site, or leaves it


class ShotOut(BaseModel):
    id: uuid.UUID
    kind: str
    created_at: datetime


class ApprovalView(BaseModel):
    state: str
    dry_run: bool
    answers_hash: str
    answers: list[AnswerOut]
    documents: dict[str, dict[str, Any]]
    screenshots: list[ShotOut]


class GateRefusalOut(BaseModel):
    detail: str
    code: str
    state: str | None = None
    answers_hash: str | None = None
    answers: list[AnswerOut] | None = None


_404 = {404: {"model": ErrorOut, "description": "No such application (or not yours)"}}
_409 = {409: {"model": GateRefusalOut, "description": "Refused by the gate"}}


def _uuid_or_404(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION) from None


def _application(db: Session, user: User, application_id: str) -> Application:
    found = service.get_application(db, user.id, _uuid_or_404(application_id))
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION)
    return found


def _answers(db: Session, application: Application) -> list[AnswerOut]:
    return [
        AnswerOut(
            field_key=row.field_key,
            label=row.label,
            kind=row.kind,
            value=row.value,
            source=row.source.value,
            required=row.required,
            approved=row.approved,
            you_answer=row.source is AnswerSource.left,
        )
        for row in gate.rows_of(db, application)
    ]


def _documents(application: Application) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for kind, record in (application.document_versions or {}).items():
        if isinstance(record, dict):
            out[kind] = {
                "version_id": record.get("version_id"),
                "number": record.get("number"),
                "fit": record.get("fit"),
                "pages": record.get("pages"),
                "corrections": len(record.get("corrections") or []),
            }
    return out


def _refusal(code: str, **rest: Any) -> JSONResponse:
    out = GateRefusalOut(detail=gate.SENTENCES.get(code, code), code=code, **rest)
    return JSONResponse(status_code=409, content=out.model_dump(mode="json"), headers=NO_STORE)


async def _paused(request: Request) -> bool:
    try:
        pool = await request.app.state.queue.pool()
    except Exception:  # Redis down: fail closed
        return True
    return await admin.paused(pool)


def _enqueue_submit(request: Request, application_id: uuid.UUID) -> None:
    """After the commit. Redis down leaves the application `approved`: the tick asks again."""

    async def enqueue() -> bool:
        found: bool = await request.app.state.queue.enqueue(
            SUBMIT_FUNCTION,
            str(application_id),
            queue=queues.APPLY,
            job_id=queues.apply_submit_job_id(application_id),
        )
        return found

    try:
        anyio.from_thread.run(enqueue)
    except queues.QueueUnavailableError:
        logger.warning("apply.submit_enqueue_failed", extra={"application_id": str(application_id)})


@router.get(
    "/api/applications/{application_id}/approval", response_model=ApprovalView, responses=_404
)
def read_approval(application_id: str, user: CurrentUser, db: Db) -> ApprovalView:
    """What an approval of this application covers, and the hash to send back."""
    application = _application(db, user, application_id)
    return ApprovalView(
        state=application.state.value,
        dry_run=application.dry_run,
        answers_hash=gate.current_hash(db, application).hex(),
        answers=_answers(db, application),
        documents=_documents(application),
        screenshots=[
            ShotOut(id=s.id, kind=s.kind.value, created_at=s.created_at)
            for s in shots.of_application(db, user.id, application.id)
        ],
    )


@router.get(
    "/api/applications/{application_id}/screenshots/{shot_id}",
    response_class=Response,
    responses={**_404, 200: {"content": {"image/jpeg": {}}}},
)
def read_screenshot(application_id: str, shot_id: str, user: CurrentUser, db: Db) -> Response:
    """One screenshot of one of the caller's applications."""
    application = _application(db, user, application_id)
    owned = {s.id for s in shots.of_application(db, user.id, application.id)}
    wanted = _uuid_or_404(shot_id)
    data = shots.get(db, user.id, wanted) if wanted in owned else None
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION)
    return Response(content=data, media_type="image/jpeg", headers=NO_STORE)


@router.post(
    "/api/applications/{application_id}/approve",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def approve(
    application_id: str, body: ApproveIn, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """Approve and submit: the form exactly as shown, then the gate's decision."""
    wanted = _uuid_or_404(application_id)
    sent = gate.parse_hash(body.answers_hash)
    if sent is None:
        return _refusal("stale")
    settings = request.app.state.settings
    paused = anyio.from_thread.run(_paused, request)
    now = deps.clock(request)
    try:
        authorisation = gate.authorise_approval(
            db, user.id, wanted, sent, now, settings.apply_approval_ttl_s
        )
        decision = gate.decide(
            db,
            user.id,
            authorisation.id,
            now,
            switches=gate.Switches(enabled=settings.apply_submit_enabled, paused=paused),
        )
        db.commit()
    except gate.NotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION) from None
    except gate.StaleError as exc:
        db.rollback()
        application = _application(db, user, application_id)
        return _refusal(
            "stale",
            state=application.state.value,
            answers_hash=exc.current.hex(),
            answers=_answers(db, application),
        )
    except gate.RefusedError as exc:
        db.rollback()
        return _refusal(exc.code)
    except jobs_service.JobBusyError:
        db.rollback()
        return _refusal("busy")
    if not decision.passed:
        return _refusal(decision.code or "refused", state=decision.state.value)
    _enqueue_submit(request, wanted)
    return ApplicationOut.model_validate(_application(db, user, application_id))


def _person(
    request: Request, user: User, db: Session, application_id: str, act: Any
) -> ApplicationOut | JSONResponse:
    wanted = _uuid_or_404(application_id)
    try:
        application = act(db, user.id, wanted, deps.clock(request))
        db.commit()
    except gate.NotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION) from None
    except gate.RefusedError as exc:
        db.rollback()
        return _refusal(exc.code)
    except jobs_service.JobBusyError:
        db.rollback()
        return _refusal("busy")
    return ApplicationOut.model_validate(application)


@router.post(
    "/api/applications/{application_id}/reject",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def reject(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """Reject the form as filled: nothing is sent."""
    return _person(
        request,
        user,
        db,
        application_id,
        lambda db, uid, aid, now: gate.cancel(db, uid, aid, now, reject=True),
    )


@router.post(
    "/api/applications/{application_id}/cancel",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def cancel(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """Stop an application before the click."""
    return _person(
        request,
        user,
        db,
        application_id,
        lambda db, uid, aid, now: gate.cancel(db, uid, aid, now, reject=False),
    )


@router.post(
    "/api/applications/{application_id}/submitted",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def submitted(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """It was submitted: the person confirms an application the app could not confirm."""
    return _person(request, user, db, application_id, gate.confirm_submitted)


@router.post(
    "/api/applications/{application_id}/retry",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def retry(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """Try again: back to the approval; nothing is sent without a new one."""
    return _person(request, user, db, application_id, gate.retry)
