"""/api/postings: the swipe deck, the Pass and its undo (opportunities O1).

- GET /api/postings/deck `?cursor=&limit=&type[]=&remote=&source=`: one page of the deck
  (deck.py): at most 20 cards in order, the next cursor (null at the end), `left` (the cards from
  this page on), the mode a Like runs in (`review` or `full_auto`, apply's `mode_after_like`),
  the threshold in force (`min_fit`, for the empty deck's sentence) and `rules_unreadable`. A
  damaged cursor, an unknown type or remote value is a 422.
- POST /api/postings/{id}/pass: the Pass (passes.py). 200 `{id, status, changed}`, also for a
  posting already skipped (`changed` false, no second event); 409 `{detail, code,
  application_id}` with `in_progress`, `submitted` or `changed`.
- DELETE /api/postings/{id}/pass: its undo. 200 with the status put back; 409 `changed` when the
  posting moved since the Pass, or was never passed.

The Like is apply's `POST /api/postings/{id}/like` (A12a), never a route of this module. Every
route needs a session, acts on the caller's rows only (another person's posting answers the same
404 as a missing one) and is never cached.
"""

import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs.models import ContractType, Job, JobStatus, WorkMode
from app.opportunities import deck as decks
from app.opportunities import passes
from app.opportunities import pay as pays

NO_STORE = {"Cache-Control": "no-store"}
NOT_FOUND = "Posting not found."
_UUID = re.compile(r"^[0-9a-fA-F]{8}-?([0-9a-fA-F]{4}-?){3}[0-9a-fA-F]{12}$")


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(prefix="/api/postings", tags=["opportunities"])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class ReasonOut(BaseModel):
    matches: list[str]
    gap: str | None
    text: str | None


class CardOut(BaseModel):
    id: uuid.UUID
    title: str
    company: str
    location: str
    contract_type: ContractType
    source: str | None
    url: str | None
    fit: int | None
    found_on: str
    reason: ReasonOut
    # What the posting states (O3): "45,000 to 55,000 EUR a year", "remote"; None when nothing.
    pay: str | None = None
    work_mode: WorkMode | None = None


class DeckOut(BaseModel):
    cards: list[CardOut]
    next_cursor: str | None
    left: int
    mode: str
    min_fit: int
    rules_unreadable: bool


class PassOut(BaseModel):
    id: uuid.UUID
    status: JobStatus
    changed: bool


class RefusedOut(BaseModel):
    detail: str
    code: str
    application_id: uuid.UUID | None


_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such posting (or not yours)"}}
_409 = {409: {"model": RefusedOut, "description": "The posting moved, or an application holds it"}}
_422 = {422: {"model": ErrorOut, "description": "Invalid parameters"}}


def _invalid(field: str, message: str) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("query", field), "msg": message}]
    )


def _uuid_or_404(value: str) -> uuid.UUID:
    if not _UUID.match(value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return uuid.UUID(value)


def _card(card: decks.Card) -> CardOut:
    posting = card.posting
    return CardOut(
        id=posting.id,
        title=posting.title,
        company=posting.company,
        location=posting.location,
        contract_type=posting.contract_type,
        source=posting.source,
        url=posting.url,
        fit=posting.fit,
        found_on=posting.found_on.isoformat(),
        reason=ReasonOut(
            matches=list(card.reason.matches), gap=card.reason.gap, text=card.reason.text
        ),
        pay=_pay(posting),
        work_mode=posting.work_mode,
    )


def _pay(posting: Job) -> str | None:
    if posting.pay_min is None or posting.pay_currency is None or posting.pay_period is None:
        return None
    return pays.pay_text(
        posting.pay_min, posting.pay_max, posting.pay_currency, str(posting.pay_period)
    )


@router.get(
    "/deck",
    response_model=DeckOut,
    responses={**_401, **_422},
    dependencies=[Depends(_no_store)],
)
def get_deck(
    user: CurrentUser,
    db: Db,
    cursor: Annotated[str | None, Query(max_length=decks.CURSOR_MAX)] = None,
    limit: Annotated[int, Query(ge=1, le=decks.LIMIT_MAX)] = decks.LIMIT_MAX,
    types: Annotated[list[ContractType] | None, Query(alias="type[]")] = None,
    remote: decks.Remote | None = None,
    source: Annotated[str | None, Query(min_length=1, max_length=decks.SOURCE_MAX)] = None,
) -> Any:
    """The person's deck, one page of cards in order."""
    filters = decks.Filters(types=tuple(dict.fromkeys(types or ())), remote=remote, source=source)
    try:
        found = decks.deck(db, user.id, cursor=cursor, limit=limit, filters=filters)
    except decks.InvalidCursorError:
        raise _invalid("cursor", "Invalid cursor.") from None
    return DeckOut(
        cards=[_card(card) for card in found.cards],
        next_cursor=found.next_cursor,
        left=found.left,
        mode=found.mode,
        min_fit=found.min_fit,
        rules_unreadable=found.rules_unreadable,
    )


def _refused(exc: passes.PassRefusedError) -> JSONResponse:
    content = RefusedOut(detail=exc.detail, code=exc.code, application_id=exc.application_id)
    return JSONResponse(status_code=409, content=content.model_dump(mode="json"), headers=NO_STORE)


def _swipe(action: Any, posting_id: str, user: User, request: Request, db: Session) -> Any:
    target = _uuid_or_404(posting_id)
    try:
        outcome = action(db, user.id, target, deps.clock(request))
    except passes.PostingNotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None
    except passes.PassRefusedError as exc:
        db.rollback()
        return _refused(exc)
    db.commit()
    return PassOut(id=outcome.posting.id, status=outcome.posting.status, changed=outcome.changed)


@router.post(
    "/{posting_id}/pass",
    response_model=PassOut,
    responses={**_401, **_404, **_409},
    dependencies=[Depends(_no_store)],
)
def pass_posting(posting_id: str, user: CurrentUser, request: Request, db: Db) -> Any:
    """A Pass: the posting leaves the deck for good (status skipped, a swipe event)."""
    return _swipe(passes.pass_posting, posting_id, user, request, db)


@router.delete(
    "/{posting_id}/pass",
    response_model=PassOut,
    responses={**_401, **_404, **_409},
    dependencies=[Depends(_no_store)],
)
def undo_pass(posting_id: str, user: CurrentUser, request: Request, db: Db) -> Any:
    """The undo of a Pass, while the Pass is the posting's last status change."""
    return _swipe(passes.undo_pass, posting_id, user, request, db)
