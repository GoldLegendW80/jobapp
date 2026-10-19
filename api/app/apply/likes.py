"""The Like (apply A12; SPEC-apply, "The flow of one application", 1).

`on_like(db, user_id, posting_id, *, now, stub_base=None)` is the one entry point of a Like: the
contract with the swipe deck (product #74), whose signature and answers change only with both
plans. A Like is the application itself:

1. Under the posting's row lock, then the person's apply lock (the order of `service.start` and
   of the gate): never apply (`guardrails.never_apply`, A2's fold) refuses at once; then the one
   refusal of a Like's own, `same_role_live`: another posting of the person at the same company
   with the same title (A2's `company_key` and `title_key`) has an application under way, not
   clicked yet (a clicked one is A2's `same_role`, over 180 days).
2. The documents the posting lacks are asked of the documents module, as its "Write now" click
   asks them (trigger `click`: never held by the fit threshold or the daily ceiling, held by the
   consent and a credential; the documents tick starts it): the CV and its letter, or the letter
   alone once a CV is there. An automatic write the daily ceiling holds back, or one not started,
   gives way, as it does to the click. A document already there, or on its way for another
   reason, is not asked again.
3. `service.start` with the mode in force (`apply_settings.mode_after_like`; Full auto makes a
   Review first item until A13a writes its checks): its refusals (no address, duplicates) roll
   the ask back with them; else the application is created `queued` and committed with the ask
   in one transaction, so the fill (A5's tick) never reads the application before its documents
   were asked. The fill waits for them with their reason (`needs` = `documents:<reason>`) and
   ends `blocked` with `no_documents` when none come.
4. A `liked` event (the mode in force, the kinds asked), in a second transaction: the timeline's
   only, so a failure there leaves the Like as it is.

Raises `service.PostingNotFoundError` (404), `service.RefusedError` (409, the code and
`sentence(code)`), `jobs_service.JobBusyError` and `store.DocumentBusyError` (409 busy). The
caller rolls back on an error. Logs carry ids and codes only.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.apply import caps, guardrails, service, states
from app.apply.caps_models import LikeMode
from app.apply.models import ENDED, Application, Mode
from app.documents import estimate, generate, store, writer_base
from app.documents import freeze as documents
from app.documents.models import (
    Document,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
)
from app.jobs import service as jobs_service
from app.jobs.models import Job

logger = logging.getLogger(__name__)

SENTENCES = {
    "same_role_live": "An application to the same role at this company is already under way.",
}
# The mode of the application a Like creates. A11 refuses to turn Full auto on until A13a; a
# stored `full_auto` makes a Review first item until then, never an item no approval can pass.
LIKE_MODES = {LikeMode.review: Mode.review, LikeMode.full_auto: Mode.review}
# An automatic write that has not started gives way to a Like, as to a click.
NOT_STARTED = (GenerationStatus.waiting, GenerationStatus.queued)


def sentence(code: str) -> str:
    return SENTENCES.get(code) or guardrails.SENTENCES[code]


@dataclass(frozen=True)
class Liked:
    application: Application
    documents: tuple[Kind, ...]  # what the documents module was asked to write, in order


def _same_role_live(db: Session, user_id: uuid.UUID, posting: Job) -> uuid.UUID | None:
    role = (guardrails.company_key(posting.company), guardrails.title_key(posting.title))
    rows = db.execute(
        sa.select(Application.id, Job.company, Job.title)
        .join(Job, sa.and_(Job.id == Application.posting_id, Job.user_id == Application.user_id))
        .where(
            Application.user_id == user_id,
            Application.posting_id != posting.id,
            Application.state.not_in((*ENDED, *states.CLICKED)),
        )
        .order_by(Application.created_at, Application.id)
    ).all()
    for application_id, company, title in rows:
        if (guardrails.company_key(company), guardrails.title_key(title)) == role:
            return application_id
    return None


def _missing(slot: documents.Slot) -> bool:
    return slot.version is None and slot.coming in (None, GenerationWait.daily_ceiling.value)


def _give_way(db: Session, document: Document, at: datetime) -> bool:
    """Whether the document is free for the Like's write: nothing open, or an automatic write
    not started, cancelled here (never started, so never counted)."""
    store.lock_document(db, document.user_id, document.id)
    open_one = store.open_generation(db, document.user_id, document.id)
    if open_one is None:
        return True
    if open_one.trigger is not GenerationTrigger.auto or open_one.status not in NOT_STARTED:
        return False
    open_one.status = GenerationStatus.cancelled
    open_one.wait_reason = None
    open_one.finished_at = at
    db.flush()
    return True


def _cv_base(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> uuid.UUID | None:
    for found in store.posting_documents(db, user_id, posting_id):
        if found.kind == Kind.cv:
            return found.base_document_id
    return None


def ask_documents(
    db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, at: datetime
) -> tuple[Kind, ...]:
    """Step 2 of the module docstring, in the caller's transaction, under the posting's lock."""
    current = documents.current(db, user_id, posting_id)
    if _missing(current.cv):
        kinds = (Kind.cv, Kind.letter) if _missing(current.letter) else (Kind.cv,)
    elif current.cv.version is not None and _missing(current.letter):
        kinds = (Kind.letter,)
    else:
        return ()
    store.lock_posting(db, user_id, posting_id)
    first = store.get_or_create(db, user_id, posting_id, kinds[0], at=at)
    if not _give_way(db, first, at):
        return ()
    if kinds == (Kind.cv, Kind.letter):
        # The letter follows the new CV (generate.start_letter); an automatic one gives way.
        _give_way(db, store.get_or_create(db, user_id, posting_id, Kind.letter, at=at), at)
    base_id = _cv_base(db, user_id, posting_id) if kinds[0] is Kind.letter else None
    figures = estimate.for_posting(db, user_id, posting_id, base_id)
    stored: dict[str, object] = dict(estimate.as_json(figures))
    if len(kinds) == 2:
        stored[generate.LETTER_FOLLOWS] = True
    reason = writer_base.wait_reason(db, user_id)
    generate.create(
        db,
        first,
        GenerationMode.posting,
        GenerationTrigger.click,
        base_id,
        stored,
        figures.ceiling,
        at=at,
        status=GenerationStatus.waiting if reason else GenerationStatus.queued,
        wait_reason=reason,
    )
    return kinds


def on_like(
    db: Session,
    user_id: uuid.UUID,
    posting_id: uuid.UUID,
    *,
    now: datetime,
    stub_base: str | None = None,
) -> Liked:
    """The person's Like on one of their postings (module docstring)."""
    posting = jobs_service.lock_job(db, user_id, posting_id)
    if posting is None:
        raise service.PostingNotFoundError(str(posting_id))
    service._lock_person(db, user_id)
    in_force = caps.settings_of(db, user_id).mode_after_like
    mode = LIKE_MODES[in_force]
    code = guardrails.never_apply(
        posting,
        service._rules(db, user_id),
        swept=guardrails.swept_never_apply(db, user_id, posting.id),
        mode=mode,
    )
    if code is not None:
        raise service.RefusedError(guardrails.Refusal(code))
    other = _same_role_live(db, user_id, posting)
    if other is not None:
        raise service.RefusedError(guardrails.Refusal("same_role_live", other))
    asked = ask_documents(db, user_id, posting.id, now)
    started = service.start(db, user_id, posting.id, mode, False, now=now, stub_base=stub_base)
    application = started.application
    try:
        service._event(
            db,
            application,
            "liked",
            now,
            mode_after_like=in_force.value,
            documents=[kind.value for kind in asked],
        )
        db.commit()
    except DBAPIError:
        db.rollback()
        logger.warning("apply.liked_event_failed", extra={"application_id": str(application.id)})
    logger.info(
        "apply.liked",
        extra={"application_id": str(application.id), "documents": [k.value for k in asked]},
    )
    return Liked(application, asked)
