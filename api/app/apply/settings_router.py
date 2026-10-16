"""The Applications settings of a person (A11; SPEC-apply, "API" and "Frontend", Settings).

- GET /api/apply/settings: the mode after a Like (Full auto is not offered until A13), the pause
  switch, the fit threshold "Apply automatically from" (`min_fit`, the person's or the default),
  the Advanced values (the safety ceiling, the hold), and, read only, the two other thresholds
  ("Show postings from", "Write documents from") from the person's search options.
  PUT changes `paused` and `min_fit` only; anything else is a 422.
- GET /api/apply/caps: the person's company caps with what each counts now, and `revision`, the
  hash of the list shown. PUT replaces the whole list if `revision` is still the stored one (else
  409 `stale`); 422 `no_company` or `same_company` for a list that cannot be saved.
- GET /api/apply/answer-bank: the person's saved answers. PUT `{question_key, answer}` changes one
  (`answer` null, empty or blank forgets it, as the bank's own rule does); 404 for a key the
  person has not saved.

Every route reads and writes the session's person only (CSRF on every write, by the middleware),
so another person's caps, settings and answers are never named here; every answer is
`Cache-Control: no-store`.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.apply import bank, caps
from app.apply.caps_models import COUNT_MAX, DAYS_MAX, LABEL_MAX
from app.apply.models import AnswerBankEntry
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.jobs import service as jobs_service

NO_STORE = {"Cache-Control": "no-store"}
SENTENCES = {
    "stale": "Your caps changed in another tab. The page now shows the current ones.",
    "no_company": "Name the company with letters or digits.",
    "same_company": "Two caps name the same company. Keep one of them.",
    "too_many": f"You can keep at most {caps.CAPS_MAX} caps.",
    "out_of_range": "A cap counts at least 1 application in at least 1 day.",
    "busy": "Your applications are busy with another change. Try again in a moment.",
}
NO_ANSWER = "No such saved answer."


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(tags=["apply"], dependencies=[Depends(_no_store)])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class SettingsOut(BaseModel):
    mode_after_like: str
    full_auto_available: bool
    paused: bool
    min_fit: int
    min_fit_chosen: bool
    safety_ceiling: int
    hold_s: int
    company_days: int
    show_postings_from: int | None
    write_documents_from: int | None


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode_after_like: Literal["review"] | None = None
    paused: Annotated[bool, Field(strict=True)] | None = None
    min_fit: Annotated[int, Field(strict=True, ge=0, le=100)] | None = None


class CapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(min_length=1, max_length=LABEL_MAX)]
    max_count: Annotated[int, Field(strict=True, ge=1, le=COUNT_MAX)]
    window_days: Annotated[int, Field(strict=True, ge=1, le=DAYS_MAX)]
    same_role_days: Annotated[int, Field(strict=True, ge=1, le=DAYS_MAX)] | None = None


class CapsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: Annotated[list[CapIn], Field(max_length=caps.CAPS_MAX)]
    revision: Annotated[str, Field(min_length=64, max_length=64)]


class CapOut(BaseModel):
    id: str
    label: str
    max_count: int
    window_days: int
    same_role_days: int | None
    used: int


class CapsOut(BaseModel):
    items: list[CapOut]
    revision: str


class BankIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_key: Annotated[str, Field(min_length=1, max_length=bank.KEY_MAX)]
    answer: Annotated[str, Field(max_length=bank.VALUE_MAX)] | None


class BankEntryOut(BaseModel):
    question_key: str
    question: str
    answer: Any
    used_count: int
    updated_at: datetime


class BankOut(BaseModel):
    items: list[BankEntryOut]


def _refusal(status_code: int, code: str) -> JSONResponse:
    content = {"detail": SENTENCES[code], "code": code}
    return JSONResponse(status_code=status_code, content=content, headers=NO_STORE)


def _settings_out(db: Session, user: User) -> SettingsOut:
    values = caps.settings_of(db, user.id)
    return SettingsOut(
        mode_after_like=values.mode_after_like.value,
        full_auto_available=False,
        paused=values.paused,
        min_fit=values.min_fit,
        min_fit_chosen=values.min_fit_chosen,
        safety_ceiling=values.safety_ceiling,
        hold_s=values.hold_s,
        company_days=values.company_days,
        show_postings_from=values.show_postings_from,
        write_documents_from=values.write_documents_from,
    )


def _caps_out(db: Session, user: User, now: datetime) -> CapsOut:
    found = caps.caps_of(db, user.id)
    counts = caps.used(db, user.id, found, now)
    items = [
        CapOut(
            id=str(cap.id),
            label=cap.label,
            max_count=cap.max_count,
            window_days=cap.window_days,
            same_role_days=cap.same_role_days,
            used=count,
        )
        for cap, count in zip(found, counts, strict=True)
    ]
    return CapsOut(items=items, revision=caps.revision(found))


def _bank_out(db: Session, user: User) -> BankOut:
    rows = db.execute(
        sa.select(AnswerBankEntry)
        .where(AnswerBankEntry.user_id == user.id)
        .order_by(AnswerBankEntry.question, AnswerBankEntry.question_key)
    ).scalars()
    return BankOut(items=[BankEntryOut.model_validate(r, from_attributes=True) for r in rows])


@router.get("/api/apply/settings", response_model=SettingsOut)
def read_settings(user: CurrentUser, db: Db) -> SettingsOut:
    """The caller's Applications settings, with the defaults where nothing was chosen."""
    return _settings_out(db, user)


@router.put("/api/apply/settings", response_model=SettingsOut)
def change_settings(body: SettingsIn, request: Request, user: CurrentUser, db: Db) -> SettingsOut:
    """The pause switch and the fit threshold; Full auto is not offered yet."""
    caps.save_settings(db, user.id, deps.clock(request), paused=body.paused, min_fit=body.min_fit)
    db.commit()
    return _settings_out(db, user)


@router.get("/api/apply/caps", response_model=CapsOut)
def read_caps(request: Request, user: CurrentUser, db: Db) -> CapsOut:
    """The caller's company caps and what each counts now."""
    return _caps_out(db, user, deps.clock(request))


@router.put("/api/apply/caps", response_model=CapsOut, responses={409: {}, 422: {}})
def replace_caps(
    body: CapsIn, request: Request, user: CurrentUser, db: Db
) -> CapsOut | JSONResponse:
    """The caller's whole list of caps, if it is still the one the page showed."""
    now = deps.clock(request)
    items = [caps.CapIn(i.label, i.max_count, i.window_days, i.same_role_days) for i in body.items]
    try:
        caps.replace_caps(db, user.id, items, body.revision, now)
    except jobs_service.JobBusyError:
        db.rollback()
        return _refusal(status.HTTP_409_CONFLICT, "busy")
    except caps.CapsError as exc:
        db.rollback()
        conflict = exc.code == "stale"
        return _refusal(409 if conflict else 422, exc.code)
    db.commit()
    return _caps_out(db, user, now)


@router.get("/api/apply/answer-bank", response_model=BankOut)
def read_bank(user: CurrentUser, db: Db) -> BankOut:
    """The caller's saved answers."""
    return _bank_out(db, user)


@router.put("/api/apply/answer-bank", response_model=BankOut, responses={404: {}})
def change_bank(body: BankIn, request: Request, user: CurrentUser, db: Db) -> BankOut:
    """One saved answer changed, or forgotten when `answer` is null or blank."""
    entry = db.execute(
        sa.select(AnswerBankEntry)
        .where(
            AnswerBankEntry.user_id == user.id, AnswerBankEntry.question_key == body.question_key
        )
        .with_for_update()
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_ANSWER)
    text = (body.answer or "").strip()
    if not text:
        db.delete(entry)
    else:
        entry.answer = text
        entry.updated_at = deps.clock(request)
    db.commit()
    return _bank_out(db, user)
