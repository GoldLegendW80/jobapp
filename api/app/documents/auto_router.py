""" "Write now" and the spend of the automatic generation (task D10; SPEC-documents.md, "API",
"Automatic generation", "The spend"). Included by `routers.py` before D6's
`/api/documents/{document_id}`.

- `POST /api/jobs/{id}/documents/write {kinds, base_document_id?}`: a click. It writes the
  posting's CV, its letter, or both, whatever the fit, the threshold or today's safety ceiling.
  An automatic generation of the same document that has not started gives way to it (cancelled,
  never counted). Started at once when the consent and a credential are there, else `waiting`
  with `consent` or `credential` (nothing spends without both; the tick starts it once they
  exist). With both kinds, the CV is written now and the letter follows it once the new CV is
  final (D8's chain, `generate.start_letter`), even when the letter has a version: it is never
  written from the old CV. An automatic letter that has not started gives way the same.
  `base_document_id` names another of the person's base CVs to write the CV from. 202 with the
  generations; 404 for another person's posting or base CV; 409 while one is running.
- `GET /api/documents/spend?min_fit=`: postings written today against the safety ceiling (in the
  time zone of `sweep_schedule`), postings waiting, the postings of the last
  `DOCUMENTS_AUTO_LOOKBACK_DAYS` (30) days at or
  above the threshold (the one asked, else the person's) and how many of them have no CV yet
  with the estimate of writing them, and the tokens and cost of the module's runs today and
  over the last 30 days, from `agent_runs` joined through `document_generations.run_id`.
Every route is the signed in person's. Every answer is `Cache-Control: private, no-store`.
Logs carry ids and codes, never a text.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

import anyio.from_thread
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import queue as queues
from app.agents import runs as agent_runs
from app.agents.models import AgentRun
from app.db import get_session
from app.documents import consent, eligibility, estimate, generate, store, tick, writer_base
from app.documents.base_router import EstimateOut, _estimate_out
from app.documents.models import (
    Document,
    DocumentGeneration,
    DocumentVersion,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
)
from app.identity import deps
from app.identity.models import User
from app.ingestion import pricing
from app.jobs import sweep

logger = logging.getLogger("app.documents")

PRIVATE = {"Cache-Control": "private, no-store"}
POSTING_NOT_FOUND = "Posting not found."
BASE_NOT_FOUND = "CV not found."
RUNNING = "This document is being written. Wait for it to finish."
BUSY = "This posting is busy. Try again in a moment."


def _private(response: Response) -> None:
    response.headers.update(PRIVATE)


Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]
router = APIRouter(tags=["documents"], dependencies=[Depends(_private)])


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail, headers=PRIVATE)


# --- Write now ----------------------------------------------------------------------------------


class WriteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: list[Literal["cv", "letter"]] = Field(min_length=1, max_length=2)
    base_document_id: uuid.UUID | None = None
    # The fake backend's recorded streams (tests and journeys), named by what follows
    # `documents_<kind>_`; ignored on the real model.
    script: Annotated[str, Field(max_length=30, pattern=r"^[a-z0-9_]{1,30}$")] | None = None


class WrittenOut(BaseModel):
    id: uuid.UUID
    kind: Kind
    status: GenerationStatus
    wait_reason: GenerationWait | None


class WriteOut(BaseModel):
    generations: list[WrittenOut]


def _has_version(db: Session, document_id: uuid.UUID) -> bool:
    found = db.execute(
        sa.select(DocumentVersion.id).where(DocumentVersion.document_id == document_id).limit(1)
    ).scalar_one_or_none()
    return found is not None


def _give_way(db: Session, document: Document, at: datetime, *, follows: bool = False) -> None:
    """An open generation of the document: an automatic one not started gives way to the click
    (cancelled, never started, so never counted); a running one, or another click, is a 409,
    unless the document `follows` the CV (a letter of both kinds: then it is left as it is).
    The caller holds the posting (`store.lock_posting`); the document is locked before the read,
    the lock the tick takes before it starts a generation, so the read below is never older than a
    start the tick committed."""
    store.lock_document(db, document.user_id, document.id)
    current = store.open_generation(db, document.user_id, document.id)
    if current is None:
        return
    if current.status is GenerationStatus.running or current.trigger is not GenerationTrigger.auto:
        if follows:
            return
        raise _error(status.HTTP_409_CONFLICT, RUNNING)
    current.status = GenerationStatus.cancelled
    current.wait_reason = None
    current.finished_at = at
    db.flush()


@router.post(
    "/api/jobs/{job_id}/documents/write",
    response_model=WriteOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"description": "Not the person's posting or base CV"},
        409: {"description": "A generation of this document is running"},
    },
)
def write_now(
    job_id: uuid.UUID, body: WriteIn, user: CurrentUser, request: Request, db: Db
) -> WriteOut:
    at = deps.clock(request)
    kinds = [Kind(kind) for kind in dict.fromkeys(body.kinds)]
    made: list[tuple[Kind, DocumentGeneration]] = []
    reason = writer_base.wait_reason(db, user.id)
    runs: list[uuid.UUID] = []
    both = Kind.cv in kinds and Kind.letter in kinds
    try:
        # Lock 2 before lock 3 (store.py), as a render storing this CV and a move to applied.
        if store.lock_posting(db, user.id, job_id) is None:
            raise _error(status.HTTP_404_NOT_FOUND, POSTING_NOT_FOUND)
        cv_base = body.base_document_id
        for kind in kinds:
            document = store.get_or_create(
                db,
                user.id,
                job_id,
                kind,
                at=at,
                base_document_id=cv_base if kind == Kind.cv else None,
            )
            if kind == Kind.letter and both:
                _give_way(db, document, at, follows=True)
                continue  # the letter follows the new CV (generate.start_letter)
            _give_way(db, document, at)
            base_id = cv_base if kind == Kind.cv else None
            if kind == Kind.letter:
                cv = next(
                    (
                        found
                        for found in store.posting_documents(db, user.id, job_id)
                        if found.kind == Kind.cv
                    ),
                    None,
                )
                base_id = cv.base_document_id if cv is not None else None
            figures = estimate.for_posting(db, user.id, job_id, base_id)
            stored: dict[str, Any] = dict(estimate.as_json(figures))
            if kind == Kind.cv and both:
                stored[generate.LETTER_FOLLOWS] = True
            if kind == Kind.cv and body.script:
                stored[generate.LETTER_SCRIPT] = f"documents_letter_{body.script}"
            generation = generate.create(
                db,
                document,
                GenerationMode.posting,
                GenerationTrigger.click,
                base_id,
                stored,
                figures.ceiling,
                at=at,
                status=GenerationStatus.waiting if reason else GenerationStatus.queued,
                wait_reason=reason,
            )
            made.append((kind, generation))
        if reason is None:
            for kind, generation in made:
                script = f"documents_{kind.value}_{body.script}" if body.script else None
                try:
                    runs.append(generate.start(db, generation, at=at, script=script).id)
                except agent_runs.ActiveRunsError:
                    break  # stays queued: the tick starts it before anything automatic
        out = WriteOut(
            generations=[
                WrittenOut(id=g.id, kind=k, status=g.status, wait_reason=g.wait_reason)
                for k, g in made
            ]
        )
        store.commit(db)
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, BASE_NOT_FOUND) from None
    except store.GenerationOpenError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, RUNNING) from None
    except store.DocumentBusyError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, BUSY) from None
    except HTTPException:
        db.rollback()
        raise
    for run_id in runs:
        try:
            anyio.from_thread.run(generate.enqueue_run, request.app.state.queue, run_id)
        except queues.QueueUnavailableError:
            logger.warning("documents.write.run_not_queued", extra={"run_id": str(run_id)})
    logger.info(
        "documents.write",
        extra={"job_id": str(job_id), "generations": len(made), "started": len(runs)},
    )
    return out


# --- the spend ----------------------------------------------------------------------------------


class TokensSpent(BaseModel):
    input: int
    output: int
    cost_usd: Decimal  # a cost on an API key, an estimate at API prices on a subscription


class SpendOut(BaseModel):
    day: str  # today, in the time zone of the sweep schedule (ISO date)
    auto: bool
    min_fit: int
    daily_ceiling: int
    written_today: int  # postings whose automatic CV started today
    waiting: int  # postings with an automatic generation waiting
    postings_30_days: int  # found in the last 30 days at or above `min_fit`
    to_write: int  # of those, the eligible ones whose CV has no version yet
    to_write_estimate: EstimateOut
    priced: bool  # an API key: the costs are costs, else estimates at API prices
    cost_note: str
    today: TokensSpent
    last_30_days: TokensSpent


def _spent(db: Session, user_id: uuid.UUID, since: datetime) -> TokensSpent:
    row = db.execute(
        sa.select(
            sa.func.coalesce(sa.func.sum(AgentRun.input_tokens), 0),
            sa.func.coalesce(sa.func.sum(AgentRun.output_tokens), 0),
            sa.func.coalesce(sa.func.sum(AgentRun.cost_usd), 0),
        )
        .select_from(DocumentGeneration)
        .join(
            AgentRun,
            sa.and_(
                AgentRun.id == DocumentGeneration.run_id,
                AgentRun.user_id == DocumentGeneration.user_id,
            ),
        )
        .where(DocumentGeneration.user_id == user_id, AgentRun.created_at >= since)
    ).one()
    return TokensSpent(input=int(row[0]), output=int(row[1]), cost_usd=Decimal(row[2]))


def _waiting(db: Session, user_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            sa.select(sa.func.count(sa.distinct(Document.posting_id)))
            .select_from(DocumentGeneration)
            .join(Document, Document.id == DocumentGeneration.document_id)
            .where(
                DocumentGeneration.user_id == user_id,
                DocumentGeneration.trigger == GenerationTrigger.auto,
                DocumentGeneration.status == GenerationStatus.waiting,
            )
        )
        or 0
    )


@router.get("/api/documents/spend", response_model=SpendOut)
def spend(
    user: CurrentUser,
    request: Request,
    db: Db,
    min_fit: Annotated[int | None, Query(ge=0, le=100)] = None,
) -> SpendOut:
    at = deps.clock(request)
    try:
        search = sweep.load_search(db, user.id)
    except sweep.InvalidSearchError:
        search = None
    setting = tick.setting_of(search)
    timezone = search.sweep_schedule.timezone if search is not None else None
    today = eligibility.ceiling_day(at, timezone)
    threshold = setting.min_fit if min_fit is None else min_fit
    asked = eligibility.Setting(True, threshold, setting.daily_ceiling)
    lookback = request.app.state.settings.documents_auto_lookback_days
    found_day = eligibility.found_day(at)
    candidates = tick.eligible(db, user.id, asked, search, found_day, lookback)
    docs = tick.posting_docs(db, user.id)
    to_write = sum(
        1 for found in candidates if docs.ids.get((found.id, Kind.cv)) not in docs.written
    )
    figures = consent.figures(db, user.id, found_day)
    each = figures.posting
    zone_start = datetime.combine(today, time(), tzinfo=eligibility.zone(timezone))
    return SpendOut(
        day=today.isoformat(),
        auto=setting.auto,
        min_fit=threshold,
        daily_ceiling=setting.daily_ceiling,
        written_today=tick.started_today(db, user.id, today),
        waiting=_waiting(db, user.id),
        postings_30_days=consent.postings_at_or_above(db, user.id, threshold, found_day, lookback),
        to_write=to_write,
        to_write_estimate=_estimate_out(
            estimate.times(each, to_write), figures.model, figures.priced
        ),
        priced=figures.priced,
        cost_note=pricing.ESTIMATE_NOTE if figures.priced else pricing.QUOTA_NOTE,
        today=_spent(db, user.id, zone_start.astimezone(UTC)),
        last_30_days=_spent(
            db, user.id, (zone_start - timedelta(days=lookback - 1)).astimezone(UTC)
        ),
    )
