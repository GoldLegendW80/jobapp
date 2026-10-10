"""Proposals: changes an agent suggests, applied only when the user accepts them (SPEC-agents.md,
Runtime: "write tools only create proposals").

    pending --accept--> accepted         (the user; the change is applied through the jobs service)
    pending --reject--> rejected         (the user)
    pending --newer proposal--> superseded   (the system: same kind, same target)
    pending --accept, target gone--> invalid (the system)
    accepted --undo--> undone            (mail's undo, SPEC-mail; not used by A3)

Kinds of A3, both on target "job":
- `job_note` {text}: appended to the job's notes on a new line. The user may accept an edited
  text (`edited_value`: a non-empty string).
- `job_status` {status, from, reason}: the job moves to `status`. An edited value is another job
  status (any of the jobs enum: the user can set any status by hand anyway).

Applying goes through app/jobs/service.py (`set_notes`, `set_status`) on the locked job row, so a
job event is written (`via: "agent"` and the proposal's id), `applied_on` is filled when the
status becomes applied, and `updated_at` moves on. When the caller passes the job version it
showed the user (`expected_updated_at`) and the job has changed since, nothing is applied
(StaleJobError, the api's 409).

What the triage agent may propose for a status (TRIAGE_FROM, TRIAGE_TO, checked by its tool when
it proposes): it judges a posting, nothing more, so it moves a job that is not yet sent
(found, shortlisted, cv_ready, staged) to shortlisted (only from found: nothing moves backwards),
skipped or closed. It never proposes applied, interview or rejected (facts about the world it
cannot know), and never the status the job already has.

Every function takes the caller's user id and touches that user's rows only. The caller owns the
transaction.
"""

import enum
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agents.models import Proposal, ProposalDecider, ProposalStatus
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus
from app.jobs.schemas import TEXT_MAX as NOTES_MAX

TARGET_JOB = "job"
NOTE_MAX = 4000
REASON_MAX = 1000
VIA = "agent"

SUPERSEDED = "Replaced by a newer proposal."
JOB_GONE = "The job no longer exists."
UNKNOWN_KIND = "This kind of proposal cannot be applied."


class ProposalKind(enum.StrEnum):
    job_note = "job_note"
    job_status = "job_status"


TRIAGE_FROM = frozenset(
    {JobStatus.found, JobStatus.shortlisted, JobStatus.cv_ready, JobStatus.staged}
)
TRIAGE_TO = (JobStatus.shortlisted, JobStatus.skipped, JobStatus.closed)


def triage_transition_error(current: JobStatus, proposed: JobStatus) -> str | None:
    """Why the triage agent may not propose moving a job from `current` to `proposed`, or None."""
    allowed = ", ".join(status.value for status in TRIAGE_TO)
    if proposed not in TRIAGE_TO:
        return f"Status {proposed.value} cannot be proposed. Allowed: {allowed}."
    if current == proposed:
        return f"The job already has the status {current.value}."
    if current not in TRIAGE_FROM:
        return (
            f"The job is {current.value}: past triage, so no status change can be proposed. "
            "Propose a note instead."
        )
    if proposed is JobStatus.shortlisted and current is not JobStatus.found:
        return f"The job is {current.value}: shortlisted would move it backwards."
    return None


class InvalidValueError(ValueError):
    """An edited value (or a stored payload) does not fit the proposal's kind. The message says
    what is expected."""


class NotPendingError(Exception):
    def __init__(self, proposal: Proposal) -> None:
        super().__init__("not pending")
        self.proposal = proposal


class TargetGoneError(Exception):
    """The proposal's target no longer exists: the proposal was marked invalid (the caller
    commits that)."""

    def __init__(self, proposal: Proposal) -> None:
        super().__init__("target gone")
        self.proposal = proposal


def clean_note(value: Any) -> str:
    if not isinstance(value, str):
        raise InvalidValueError("a note is a text")
    text = value.replace("\r\n", "\n").strip()
    if not text:
        raise InvalidValueError("the note must not be empty")
    if len(text) > NOTE_MAX:
        raise InvalidValueError(f"the note is at most {NOTE_MAX} characters")
    return text


def clean_status(value: Any) -> JobStatus:
    try:
        return JobStatus(value)
    except ValueError:
        allowed = ", ".join(status.value for status in JobStatus)
        raise InvalidValueError(f"the status must be one of {allowed}") from None


def clean_reason(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidValueError("the reason must not be empty")
    reason = " ".join(value.split())
    if len(reason) > REASON_MAX:
        raise InvalidValueError(f"the reason is at most {REASON_MAX} characters")
    return reason


# --- reading ------------------------------------------------------------------------------------


def proposals_of(user_id: uuid.UUID) -> sa.Select[tuple[Proposal]]:
    return sa.select(Proposal).where(Proposal.user_id == user_id)


def get(
    db: Session, user_id: uuid.UUID, proposal_id: uuid.UUID, *, lock: bool = False
) -> Proposal | None:
    query = proposals_of(user_id).where(Proposal.id == proposal_id)
    if lock:
        query = query.with_for_update()
    return db.execute(query.execution_options(populate_existing=True)).scalar_one_or_none()


@dataclass(frozen=True)
class Filters:
    statuses: Sequence[ProposalStatus] = ()
    kinds: Sequence[str] = ()
    target: str | None = None
    target_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


def list_proposals(
    db: Session, user_id: uuid.UUID, filters: Filters, limit: int
) -> Sequence[Proposal]:
    """The user's proposals matching every filter given, newest first."""
    query = proposals_of(user_id)
    if filters.statuses:
        query = query.where(Proposal.status.in_(list(filters.statuses)))
    if filters.kinds:
        query = query.where(Proposal.kind.in_(list(filters.kinds)))
    if filters.target is not None:
        query = query.where(Proposal.target == filters.target)
    if filters.target_id is not None:
        query = query.where(Proposal.target_id == filters.target_id)
    if filters.run_id is not None:
        query = query.where(Proposal.run_id == filters.run_id)
    return db.scalars(
        query.order_by(Proposal.created_at.desc(), Proposal.id.desc()).limit(limit)
    ).all()


# --- writing ------------------------------------------------------------------------------------


def _decide(
    proposal: Proposal,
    status: ProposalStatus,
    by: ProposalDecider,
    now: datetime,
    reason: str | None = None,
) -> None:
    proposal.status = status
    proposal.decided_by = by
    proposal.decided_at = now
    if reason is not None:
        proposal.reason = reason


def create(
    db: Session,
    user_id: uuid.UUID,
    *,
    target: str,
    target_id: uuid.UUID | None,
    kind: str,
    payload: dict[str, Any],
    run_id: uuid.UUID | None,
    now: datetime,
    status: ProposalStatus = ProposalStatus.pending,
) -> Proposal:
    """A new proposal. Pending ones of the same kind for the same target are superseded by it
    (one suggestion of each kind stands at a time). `status=accepted` records a change the system
    already applied (SPEC-mail's exception), decided by the system. The caller commits."""
    if status not in (ProposalStatus.pending, ProposalStatus.accepted):
        raise ValueError(f"a proposal is created pending or accepted, not {status}")
    if target_id is not None:
        older = db.scalars(
            proposals_of(user_id)
            .where(
                Proposal.target == target,
                Proposal.target_id == target_id,
                Proposal.kind == kind,
                Proposal.status == ProposalStatus.pending,
            )
            .with_for_update()
        ).all()
        for proposal in older:
            _decide(proposal, ProposalStatus.superseded, ProposalDecider.system, now, SUPERSEDED)
    proposal = Proposal(
        user_id=user_id,
        run_id=run_id,
        target=target,
        target_id=target_id,
        kind=kind,
        payload=payload,
        status=status,
        created_at=now,
    )
    if status is ProposalStatus.accepted:
        _decide(proposal, status, ProposalDecider.system, now)
    db.add(proposal)
    db.flush()
    return proposal


def _apply_to_job(db: Session, proposal: Proposal, job: Job, value: Any, now: datetime) -> None:
    extra = {"proposal_id": str(proposal.id)}
    if proposal.kind == ProposalKind.job_note:
        notes = jobs_service.append_note(job.notes, clean_note(value))
        if len(notes) > NOTES_MAX:
            raise InvalidValueError(f"the job's notes would exceed {NOTES_MAX} characters")
        jobs_service.set_notes(db, job, notes, now, via=VIA, extra=extra)
    else:
        jobs_service.set_status(db, job, clean_status(value), now, via=VIA, extra=extra)


_VALUE_KEY = {ProposalKind.job_note: "text", ProposalKind.job_status: "status"}
_CLEAN = {ProposalKind.job_note: clean_note, ProposalKind.job_status: clean_status}

_MISSING: Any = object()


def accept(
    db: Session,
    user_id: uuid.UUID,
    proposal_id: uuid.UUID,
    now: datetime,
    *,
    edited_value: Any = _MISSING,
    expected_updated_at: datetime | None = None,
) -> tuple[Proposal, Job] | None:
    """Apply a pending proposal and mark it accepted by the user. None when it is not one of the
    user's proposals.

    Raises (nothing applied, in this order): NotPendingError; InvalidValueError when the edited
    value does not fit the kind (checked before anything is read or locked) or the kind is
    unknown; TargetGoneError when the job is gone, after marking the proposal invalid (the
    caller commits that); jobs_service.StaleJobError when `expected_updated_at` is not the job's
    version (the caller rolls back)."""
    proposal = get(db, user_id, proposal_id, lock=True)
    if proposal is None:
        return None
    if proposal.status is not ProposalStatus.pending:
        raise NotPendingError(proposal)
    try:
        kind = ProposalKind(proposal.kind)
    except ValueError:
        raise InvalidValueError(UNKNOWN_KIND) from None
    edited = edited_value is not _MISSING
    if edited:
        cleaned = _CLEAN[kind](edited_value)
        value: Any = cleaned.value if isinstance(cleaned, JobStatus) else cleaned
    else:
        value = proposal.payload.get(_VALUE_KEY[kind])
    job = (
        jobs_service.lock_job(db, user_id, proposal.target_id)
        if proposal.target == TARGET_JOB and proposal.target_id is not None
        else None
    )
    if job is None:
        _decide(proposal, ProposalStatus.invalid, ProposalDecider.system, now, JOB_GONE)
        db.flush()
        raise TargetGoneError(proposal)
    if expected_updated_at is not None and job.updated_at != expected_updated_at:
        raise jobs_service.StaleJobError(job)
    _apply_to_job(db, proposal, job, value, now)
    _decide(proposal, ProposalStatus.accepted, ProposalDecider.user, now)
    if edited:
        proposal.edited_value = value
    db.flush()
    return proposal, job


def reject(
    db: Session, user_id: uuid.UUID, proposal_id: uuid.UUID, now: datetime
) -> Proposal | None:
    """Mark a pending proposal rejected by the user; nothing else changes. None when it is not
    one of the user's proposals; NotPendingError when it was already decided."""
    proposal = get(db, user_id, proposal_id, lock=True)
    if proposal is None:
        return None
    if proposal.status is not ProposalStatus.pending:
        raise NotPendingError(proposal)
    _decide(proposal, ProposalStatus.rejected, ProposalDecider.user, now)
    db.flush()
    return proposal
