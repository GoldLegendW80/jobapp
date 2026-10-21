"""The rehearsal's answer (apply A13a; SPEC-apply, "Modes", Dry run).

- POST /api/applications/{application_id}/rehearsal `{answer: "yes" | "no"}`: the person's
  answer to "Does this look right?" on a Full auto rehearsal (`rehearsed`). Yes moves it to the
  review queue, `ready` for a one click Approve (`full_auto.confirm_rehearsal`); No keeps it
  `rehearsed` and Full auto in rehearsal. 409 `not_rehearsal` for anything else, 409
  `rehearsal_taken` when another live application took the posting or its form since, 409 `busy`
  past a lock timeout; 404 for an application that is not the person's.
- GET /api/apply/summary: Full auto's day so far (A13b), from midnight in the person's time zone
  (`caps.day_start`, as the safety ceiling counts): how many were submitted, how many are held
  now, and how many were sent to the review queue, by reason with its sentence. Counts and
  codes only.

The user always comes from the session (CSRF on the write, by the middleware);
`Cache-Control: no-store`.
"""

from datetime import datetime
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.apply import approval_router, caps, full_auto, gate
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.apply.router import NO_STORE, ApplicationOut
from app.db import get_session, read_snapshot
from app.identity import deps
from app.identity.models import User


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(tags=["apply"], dependencies=[Depends(_no_store)])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class RehearsalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: Literal["yes", "no"]


@router.post(
    "/api/applications/{application_id}/rehearsal",
    response_model=ApplicationOut,
    responses={404: {}, 409: {}},
)
def answer_rehearsal(
    application_id: str, body: RehearsalIn, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """Does this look right? Yes sends it to the review queue; No keeps Full auto rehearsing."""
    act = full_auto.confirm_rehearsal if body.answer == "yes" else full_auto.decline_rehearsal
    return approval_router._person(request, user, db, application_id, act)


class ReviewReasonOut(BaseModel):
    code: str
    sentence: str | None
    count: int


class SummaryOut(BaseModel):
    since: datetime
    submitted: int
    held: int
    sent_to_review: list[ReviewReasonOut]


def _count(db: Session, *where: sa.ColumnElement[bool]) -> int:
    return int(db.execute(sa.select(sa.func.count()).where(*where)).scalar_one())


@router.get("/api/apply/summary", response_model=SummaryOut)
def read_summary(request: Request, user: CurrentUser, db: Db) -> SummaryOut:
    """Full auto today: submitted, held, sent to review and why."""
    read_snapshot(db)
    since = caps.day_start(deps.clock(request), caps.settings_of(db, user.id).timezone)
    submitted = _count(
        db,
        Application.user_id == user.id,
        Application.mode == Mode.full_auto,
        Application.state == State.submitted,
        Application.submitted_at >= since,
    )
    held = _count(
        db,
        Application.user_id == user.id,
        Application.mode == Mode.full_auto,
        Application.state == State.held,
    )
    code = sa.func.coalesce(ApplicationEvent.data["code"].astext, "other")
    rows = db.execute(
        sa.select(code, sa.func.count())
        .where(
            ApplicationEvent.user_id == user.id,
            ApplicationEvent.kind == "sent_to_review",
            ApplicationEvent.at >= since,
        )
        .group_by(code)
        .order_by(sa.func.count().desc(), code)
    ).all()
    reasons = [
        ReviewReasonOut(code=name, sentence=gate.SENTENCES.get(name), count=int(n))
        for name, n in rows
    ]
    return SummaryOut(since=since, submitted=submitted, held=held, sent_to_review=reasons)
