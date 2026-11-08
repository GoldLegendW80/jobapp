"""/api/insights: your search in numbers (insights.py), and the close of the silent applications.

- GET /api/insights: the funnel, the interview rate by fit band, by applicant tracking system and
  by source, the days to a first answer, the reasons of the Passes and the silent applications,
  as of the clock's today, read in one snapshot. A rate or a median under its floor is null.
- POST /api/insights/silent/close `{ids}` (1 to 500): closes those still silent, on the person's
  click only; 200 `{closed, skipped, undo_token, undo_expires_at}`, the token undone by the
  jobs module's `POST /api/jobs/bulk/undo`. An id that is not the caller's is skipped.

Both need a session, act on the caller's rows only and are never cached.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_session, read_snapshot
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import undo as undo_tokens
from app.jobs.router import _undo_key as undo_key
from app.jobs.schemas import BULK_MAX
from app.opportunities import insights


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/insights", tags=["opportunities"])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]
_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class FunnelOut(_Out):
    found: int
    liked: int
    applied: int
    answered: int
    interview: int
    rejected: int
    passed: int
    waiting: int
    answer_rate: int | None
    interview_rate: int | None


class GroupOut(_Out):
    key: str
    applied: int
    answered: int
    interviews: int
    rate: int | None


class FirstAnswerOut(_Out):
    median_days: float | None
    measured: int
    undated: int
    waiting: int


class ReasonCountOut(_Out):
    reason: str
    count: int


class SilentOut(_Out):
    id: uuid.UUID
    company: str
    title: str
    applied_on: date | None
    last_signal: date
    days: int


class InsightsOut(_Out):
    silent_days: int
    funnel: FunnelOut
    by_fit: list[GroupOut]
    by_ats: list[GroupOut]
    by_source: list[GroupOut]
    first_answer: FirstAnswerOut
    pass_reasons: list[ReasonCountOut]
    passes_without_reason: int
    silent: list[SilentOut]
    silent_undated: int


class CloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=BULK_MAX)]


class CloseOut(BaseModel):
    closed: list[uuid.UUID]
    skipped: list[uuid.UUID]
    undo_token: str | None
    undo_expires_at: datetime | None


@router.get("", response_model=InsightsOut, responses={**_401}, dependencies=[Depends(_no_store)])
def read_insights(user: CurrentUser, request: Request, db: Db) -> Any:
    """The caller's numbers."""
    read_snapshot(db)
    return InsightsOut.model_validate(insights.insights(db, user.id, deps.clock(request).date()))


@router.post(
    "/silent/close",
    response_model=CloseOut,
    responses={**_401, 409: {"model": ErrorOut, "description": "A posting is being changed"}},
    dependencies=[Depends(_no_store)],
)
def close_silent(body: CloseIn, user: CurrentUser, request: Request, db: Db) -> Any:
    """Close the ticked silent applications that are still silent."""
    now = deps.clock(request)
    outcome = insights.close_silent(db, user.id, body.ids, now)
    token = (
        undo_tokens.sign(undo_key(request), user.id, outcome.undo, now) if outcome.undo else None
    )
    db.commit()
    return CloseOut(
        closed=outcome.closed,
        skipped=outcome.skipped,
        undo_token=token,
        undo_expires_at=undo_tokens.expires_at(now) if token else None,
    )
