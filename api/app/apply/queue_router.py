"""The Like and the review queue (apply A12; SPEC-apply, "API" and "Frontend", `/applications`).

- POST /api/postings/{posting_id}/like: `likes.on_like`, the Like of the jobs list until the swipe
  deck (#74) calls it. 201 with the application and the documents asked; 409 `{detail, code,
  application_id}` for a refusal (A2's codes and `same_role_live`), 409 `busy` past a lock
  timeout; 404 for a posting that is not the person's.
- GET /api/apply/queue: the person's applications the queue shows, those waiting for the person
  first (`ready`, `needs_person`, `refused_by_site`, `submit_unknown`), then those under way
  (`queued`, `filling`, `approved`, `held`, `submitting`), oldest first within each; Apply myself
  is never in it. Each card holds the company and the title, the newest "before" screenshot,
  the count of fields left (and of those required), the documents filled with their check
  (`documents_review`: Full auto's rule, a page overflow or a correction), and the reason a
  `ready` item came back (`error_code`, else the code of its last refused decision), with its
  sentence.
- GET /api/apply/queue/count: how many wait for the person, for the app shell.
- POST /api/apply/queue/{application_id}/reject `{skip_posting}`: Reject (A6's `gate.cancel`,
  `ready` or `needs_person` to `cancelled`, the posting kept); with `skip_posting` the posting
  also moves to `skipped` in the same transaction, from a status before `applied` only.

Approve and Edit are A6's routes (`/approval`, `/approve`, `/answers/{field}`): every decision of
the queue goes through the gate, its caps and its pace. The user always comes from the session;
another person's posting or application is a 404, as a missing one. `Cache-Control: no-store`.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.apply import gate, likes, service
from app.apply.models import (
    AnswerSource,
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    ApplicationScreenshot,
    Mode,
    ShotKind,
    State,
)
from app.apply.router import (
    BUSY,
    NO_APPLICATION,
    NO_STORE,
    NOT_FOUND,
    ApplicationOut,
    RefusalOut,
)
from app.db import get_session, read_snapshot
from app.documents import store as documents_store
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus

S = State
WAITING_ON_YOU = (S.ready, S.needs_person, S.refused_by_site, S.submit_unknown)
UNDER_WAY = (S.queued, S.filling, S.approved, S.held, S.submitting)
QUEUE_MAX = 200
# The statuses "and skip the posting" moves from: before the posting was applied to.
SKIP_FROM = frozenset(
    {JobStatus.found, JobStatus.shortlisted, JobStatus.cv_ready, JobStatus.staged}
)


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(tags=["apply"], dependencies=[Depends(_no_store)])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class LikedOut(BaseModel):
    application: ApplicationOut
    documents: list[str]


class ReasonOut(BaseModel):
    code: str
    sentence: str | None


class DocumentCheckOut(BaseModel):
    number: int | None
    fit: str | None
    corrections: int


class QueueItem(ApplicationOut):
    company: str
    title: str
    shot_id: uuid.UUID | None
    left: int
    required_left: int
    documents: dict[str, DocumentCheckOut]
    documents_review: bool
    reason: ReasonOut | None


class QueueOut(BaseModel):
    items: list[QueueItem]
    waiting_on_you: int


class CountOut(BaseModel):
    waiting_on_you: int


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skip_posting: bool = False


class RejectedOut(BaseModel):
    application: ApplicationOut
    posting_status: str


_404 = {404: {"model": ErrorOut, "description": "No such posting or application (or not yours)"}}
_409 = {409: {"model": RefusalOut, "description": "Refused, or busy"}}


def _uuid_or_404(value: str, detail: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from None


def _refused(db: Session, code: str, detail: str, application_id: uuid.UUID | None = None) -> Any:
    db.rollback()
    out = RefusalOut(detail=detail, code=code, application_id=application_id)
    return JSONResponse(status_code=409, content=out.model_dump(mode="json"), headers=NO_STORE)


@router.post(
    "/api/postings/{posting_id}/like",
    status_code=status.HTTP_201_CREATED,
    response_model=LikedOut,
    responses={**_404, **_409},
)
def like(posting_id: str, request: Request, user: CurrentUser, db: Db) -> LikedOut | JSONResponse:
    """The person's Like: the application itself, created at once, or why not."""
    try:
        liked = likes.on_like(
            db,
            user.id,
            _uuid_or_404(posting_id, NOT_FOUND),
            now=deps.clock(request),
            stub_base=request.app.state.settings.apply_stub_base_url,
        )
    except service.PostingNotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None
    except service.RefusedError as exc:
        refusal = exc.refusal
        return _refused(db, refusal.code, likes.sentence(refusal.code), refusal.application_id)
    except (jobs_service.JobBusyError, documents_store.DocumentBusyError):
        return _refused(db, "busy", BUSY)
    except DBAPIError as exc:
        if not jobs_service.is_lock_timeout(exc):
            raise
        return _refused(db, "busy", BUSY)
    return LikedOut(
        application=ApplicationOut.model_validate(liked.application),
        documents=[kind.value for kind in liked.documents],
    )


def _queued(user_id: uuid.UUID, wanted: tuple[State, ...]) -> sa.Select[Any]:
    return (
        sa.select(Application, Job.company, Job.title)
        .join(Job, sa.and_(Job.id == Application.posting_id, Job.user_id == Application.user_id))
        .where(
            Application.user_id == user_id,
            Application.mode != Mode.manual,
            Application.state.in_(wanted),
        )
    )


def _waiting_count(db: Session, user_id: uuid.UUID) -> int:
    return int(
        db.execute(
            sa.select(sa.func.count()).where(
                Application.user_id == user_id,
                Application.mode != Mode.manual,
                Application.state.in_(WAITING_ON_YOU),
            )
        ).scalar_one()
    )


def _shots(db: Session, user_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    """The newest "before" screenshot of each application."""
    rows = db.execute(
        sa.select(ApplicationScreenshot.application_id, ApplicationScreenshot.id)
        .where(
            ApplicationScreenshot.user_id == user_id,
            ApplicationScreenshot.application_id.in_(ids),
            ApplicationScreenshot.kind == ShotKind.before,
        )
        .order_by(ApplicationScreenshot.created_at, ApplicationScreenshot.id)
    ).all()
    return {application_id: shot_id for application_id, shot_id in rows}


def _left(
    db: Session, user_id: uuid.UUID, ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int]]:
    rows = db.execute(
        sa.select(
            ApplicationAnswer.application_id,
            sa.func.count(),
            sa.func.count().filter(ApplicationAnswer.required),
        )
        .where(
            ApplicationAnswer.user_id == user_id,
            ApplicationAnswer.application_id.in_(ids),
            ApplicationAnswer.source == AnswerSource.left,
        )
        .group_by(ApplicationAnswer.application_id)
    ).all()
    return {application_id: (int(left), int(required)) for application_id, left, required in rows}


def _refused_decisions(
    db: Session, user_id: uuid.UUID, ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """The code of the last decision of each application, when it was a refusal."""
    rows = db.execute(
        sa.select(ApplicationEvent.application_id, ApplicationEvent.data)
        .where(
            ApplicationEvent.user_id == user_id,
            ApplicationEvent.application_id.in_(ids),
            ApplicationEvent.kind == "decision",
        )
        .order_by(ApplicationEvent.at, ApplicationEvent.id)
    ).all()
    last: dict[uuid.UUID, dict[str, Any]] = {aid: data for aid, data in rows}
    return {
        aid: str(data["code"])
        for aid, data in last.items()
        if data.get("outcome") == "refused" and data.get("code")
    }


def _documents(versions: dict[str, Any] | None) -> dict[str, DocumentCheckOut]:
    out: dict[str, DocumentCheckOut] = {}
    for kind, record in (versions or {}).items():
        if isinstance(record, dict):
            number = record.get("number")
            fit = record.get("fit")
            out[kind] = DocumentCheckOut(
                number=number if isinstance(number, int) else None,
                fit=fit if isinstance(fit, str) else None,
                corrections=len(record.get("corrections") or []),
            )
    return out


def _reason(application: Application, refused: dict[uuid.UUID, str]) -> ReasonOut | None:
    if application.state is not S.ready:
        return None
    code = application.error_code or refused.get(application.id)
    return ReasonOut(code=code, sentence=gate.SENTENCES.get(code)) if code else None


@router.get("/api/apply/queue", response_model=QueueOut)
def read_queue(user: CurrentUser, db: Db) -> QueueOut:
    """The review queue: what waits for the person, then what is under way."""
    read_snapshot(db)  # the items, their details and the count are one moment of the tables
    group = sa.case((Application.state.in_(WAITING_ON_YOU), 0), else_=1)
    rows = db.execute(
        _queued(user.id, WAITING_ON_YOU + UNDER_WAY)
        .order_by(group, Application.created_at, Application.id)
        .limit(QUEUE_MAX)
    ).all()
    ids = [application.id for application, _, _ in rows]
    shots = _shots(db, user.id, ids)
    left = _left(db, user.id, ids)
    refused = _refused_decisions(db, user.id, ids)
    items = []
    for application, company, title in rows:
        versions = application.document_versions or {}
        open_left, required_left = left.get(application.id, (0, 0))
        items.append(
            QueueItem(
                **ApplicationOut.model_validate(application).model_dump(),
                company=company,
                title=title,
                shot_id=shots.get(application.id),
                left=open_left,
                required_left=required_left,
                documents=_documents(versions),
                documents_review=bool(versions) and gate.documents_need_review(versions),
                reason=_reason(application, refused),
            )
        )
    return QueueOut(items=items, waiting_on_you=_waiting_count(db, user.id))


@router.get("/api/apply/queue/count", response_model=CountOut)
def read_count(user: CurrentUser, db: Db) -> CountOut:
    """How many applications wait for the person."""
    return CountOut(waiting_on_you=_waiting_count(db, user.id))


@router.post(
    "/api/apply/queue/{application_id}/reject",
    response_model=RejectedOut,
    responses={**_404, **_409},
)
def reject(
    application_id: str, body: RejectIn, request: Request, user: CurrentUser, db: Db
) -> RejectedOut | JSONResponse:
    """Reject the form as filled, and with `skip_posting` set the posting aside."""
    wanted = _uuid_or_404(application_id, NO_APPLICATION)
    now: datetime = deps.clock(request)
    try:
        _, posting = gate.lock(db, user.id, wanted)
        application = gate.cancel(db, user.id, wanted, now, reject=True)
        if body.skip_posting and JobStatus(posting.status) in SKIP_FROM:
            jobs_service.set_status(
                db,
                posting,
                JobStatus.skipped,
                now,
                via="apply",
                extra={"application_id": str(application.id)},
            )
        db.commit()
    except gate.NotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION) from None
    except gate.RefusedError as exc:
        return _refused(db, exc.code, gate.SENTENCES.get(exc.code, exc.code))
    except jobs_service.JobBusyError:
        return _refused(db, "busy", BUSY)
    return RejectedOut(
        application=ApplicationOut.model_validate(application),
        posting_status=JobStatus(posting.status).value,
    )
