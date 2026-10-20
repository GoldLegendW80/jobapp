"""The Pass and its undo (opportunities O1; SPEC-opportunities, "A Pass" and "Undo").

A Pass moves a posting of the deck to `skipped` through the jobs module's `set_status`, under the
posting's row lock (`lock_job`), with a `status` event whose data is `{from, to: "skipped", via:
"swipe"}`. That event is the stored signal; nothing reads it yet (owner decision 10: learning
from Passes is a later task). A skipped posting stays out of the deck for good, and the sweep's
duplicate rule (same address, or same company, title and location) finds its row and adds none.

Under the lock, so a Pass and a Like (`apply.on_like`, which takes the same lock first) on one
posting run one after the other:
- a posting already `skipped` answers as passed, with no second event (a Pass is idempotent);
- a posting the person moved out of the deck meanwhile (another status) is refused `changed`;
- a posting with an application of the person in any state but `cancelled` is refused:
  `in_progress` while it runs, `submitted` once clicked (or unknown), `changed` once ended.

The undo puts back the event's `from` only when the posting is still skipped by that Pass: its
last status event is the Pass. A later status change (by the person, in bulk, by mail, by an
agent) or an undo already done answers `changed` and nothing moves; a posting never passed too.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import guardrails
from app.apply.models import ENDED, Application, State
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from app.opportunities.deck import DECK_STATUSES

VIA = "swipe"
UNDO_VIA = "swipe_undo"
CHANGED = "This posting changed since you saw it. Reload the deck."
SENTENCES = {
    "in_progress": guardrails.SENTENCES["in_progress"],
    "submitted": guardrails.SENTENCES["submitted"],
    "changed": CHANGED,
}
_CLICKED = (State.submitted, State.submit_unknown)


class PostingNotFoundError(LookupError):
    """Not a posting of the person (or no posting at all): a 404 that says nothing more."""


class PassRefusedError(Exception):
    def __init__(self, code: str, application_id: uuid.UUID | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.application_id = application_id

    @property
    def detail(self) -> str:
        return SENTENCES[self.code]


@dataclass(frozen=True)
class Outcome:
    posting: Job
    changed: bool


def _locked(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> Job:
    posting = jobs_service.lock_job(db, user_id, posting_id)
    if posting is None:
        raise PostingNotFoundError(str(posting_id))
    return posting


def _holding_application(
    db: Session, user_id: uuid.UUID, posting_id: uuid.UUID
) -> Application | None:
    return db.execute(
        sa.select(Application)
        .where(
            Application.user_id == user_id,
            Application.posting_id == posting_id,
            Application.state != State.cancelled,
        )
        .order_by(Application.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def pass_posting(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, now: datetime) -> Outcome:
    """The Pass, in the caller's transaction (the caller commits). Raises PostingNotFoundError
    or PassRefusedError."""
    posting = _locked(db, user_id, posting_id)
    if posting.status is JobStatus.skipped:
        return Outcome(posting, changed=False)
    if posting.status not in DECK_STATUSES:
        raise PassRefusedError("changed")
    application = _holding_application(db, user_id, posting.id)
    if application is not None:
        if application.state in _CLICKED:
            code = "submitted"
        elif application.state in ENDED:
            code = "changed"
        else:
            code = "in_progress"
        raise PassRefusedError(code, application.id)
    jobs_service.set_status(db, posting, JobStatus.skipped, now, via=VIA)
    return Outcome(posting, changed=True)


def _last_status_event(db: Session, posting: Job) -> JobEvent | None:
    return db.execute(
        sa.select(JobEvent)
        .where(
            JobEvent.user_id == posting.user_id,
            JobEvent.job_id == posting.id,
            JobEvent.kind == JobEventKind.status,
        )
        .order_by(JobEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def undo_pass(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, now: datetime) -> Outcome:
    """The undo of the posting's Pass, in the caller's transaction. Raises PostingNotFoundError
    or PassRefusedError("changed")."""
    posting = _locked(db, user_id, posting_id)
    event = _last_status_event(db, posting) if posting.status is JobStatus.skipped else None
    data = event.data if event is not None else {}
    previous = data.get("from")
    if (
        data.get("via") != VIA
        or data.get("to") != JobStatus.skipped.value
        or previous not in {s.value for s in DECK_STATUSES}
    ):
        raise PassRefusedError("changed")
    jobs_service.set_status(db, posting, JobStatus(previous), now, via=UNDO_VIA)
    return Outcome(posting, changed=True)
