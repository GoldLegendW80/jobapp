"""Proposals: changes an agent suggests, applied only when the user accepts them (SPEC-agents.md,
Runtime: "write tools only create proposals").

    pending --accept--> accepted         (the user; the change is applied through the jobs service)
    pending --reject--> rejected         (the user)
    pending --newer proposal--> superseded   (the system: same kind, same target)
    pending --accept, target gone--> invalid (the system)
    pending --accept, job status moved since (unedited status proposal)--> invalid (the system)
    pending status proposal --job status changed by any other path--> superseded (the system,
                                 app/jobs/service.py set_status)
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

Kind of the ingestion module, on target "profile" (no target id, no run):
- `profile_item`: one fact for the profile. `create`, `accept` and `undo` hand such a proposal
  to app/ingestion/item_proposals.py, which says what its payload holds and what stale,
  conflict and invalid mean there. The dispatch reads `proposal.target` before the kind, so
  the job kinds above are untouched. `reject` and the list need no dispatch.

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
from app.profile.models import Profile

TARGET_JOB = "job"
TARGET_PROFILE = "profile"
KIND_PROFILE_ITEM = "profile_item"
NOTE_MAX = 4000
REASON_MAX = 1000
VIA = "agent"

SUPERSEDED = "Replaced by a newer proposal."
JOB_GONE = "The job no longer exists."
STATUS_CHANGED = "The job's status changed since this was proposed."
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


class StatusChangedError(TargetGoneError):
    """A status proposal made for another status than the job has now (it moved since): the
    proposal was marked invalid (the caller commits that)."""


class EditedValueError(InvalidValueError):
    """An edited value of a profile item that its section model refuses. `loc` is the path of
    the field inside the value (field names and positions, never the value)."""

    def __init__(self, message: str, loc: tuple[str | int, ...] = ()) -> None:
        super().__init__(message)
        self.loc = loc


class NotStoredError(Exception):
    """`create` did not store a profile item. `reason` is `equal_to_profile` (the profile already
    holds it) or `rejected_before` (the user rejected this value at this key)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ItemInvalidError(Exception):
    """A profile item whose stored value no longer fits its section model: the proposal was
    marked invalid, with the reason (the caller commits that)."""

    def __init__(self, proposal: Proposal) -> None:
        super().__init__("item invalid")
        self.proposal = proposal


class ProfileConflictError(Exception):
    """The profile section as it is now cannot take the item: nothing was applied and the
    proposal stays pending. `message` is the section model's sentence, never a value."""

    def __init__(self, proposal: Proposal, message: str) -> None:
        super().__init__("profile conflict")
        self.proposal = proposal
        self.message = message


class StaleItemError(Exception):
    """The profile changed at the item's key since it was proposed and the caller did not show
    the current revision: nothing was applied. `revision` is the profile's current revision."""

    def __init__(self, proposal: Proposal, revision: int) -> None:
        super().__init__("stale item")
        self.proposal = proposal
        self.revision = revision


def _profile_items() -> Any:
    """app.ingestion.item_proposals, imported when first needed (that module imports this one)."""
    from app.ingestion import item_proposals

    return item_proposals


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
    already applied (SPEC-mail's exception), decided by the system. The caller commits.

    For the target "profile" the payload is a profile item `{section, key, action, value,
    citations, ...}` (item_proposals.propose_payload): an older pending item at the same key with
    another value is superseded, the same value already pending gets the citations joined and is
    the proposal returned, and NotStoredError says the item was dropped because the profile holds
    it or the user rejected it before. InvalidValueError when the item is refused."""
    if status not in (ProposalStatus.pending, ProposalStatus.accepted):
        raise ValueError(f"a proposal is created pending or accepted, not {status}")
    if target == TARGET_PROFILE:
        if (
            kind != KIND_PROFILE_ITEM
            or target_id is not None
            or run_id is not None
            or status is not ProposalStatus.pending
        ):
            raise ValueError("a proposal for the profile is a pending profile_item, without ids")
        outcome = _profile_items().propose_payload(db, user_id, payload, now)
        if outcome.proposal is None:
            raise NotStoredError(outcome.status)
        stored: Proposal = outcome.proposal
        return stored
    if target_id is not None:
        # One writer at a time per (user, target, kind): two runs proposing at once on the same
        # job cannot both find no pending row and both stay pending. Released at commit.
        key = f"proposals:{user_id}:{target}:{target_id}:{kind}"
        db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(key, 0))))
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
    profile_revision: int | None = None,
) -> tuple[Proposal, Job | Profile] | None:
    """Apply a pending proposal and mark it accepted by the user. None when it is not one of the
    user's proposals.

    Raises (nothing applied, in this order): NotPendingError; InvalidValueError when the edited
    value does not fit the kind (checked before anything is read or locked) or the kind is
    unknown; TargetGoneError when the job is gone, after marking the proposal invalid (the
    caller commits that); jobs_service.StaleJobError when `expected_updated_at` is not the job's
    version (the caller rolls back); StatusChangedError (a TargetGoneError) when an unedited
    status proposal was made from another status than the job has now, after marking it
    invalid (the caller commits that). An edited status is the user's own choice: applied.

    Lock order (issue #25): the job first, then the proposal, as PATCH, bulk and undo do (they
    lock the job, then supersede its pending status proposals). The proposal is read without a
    lock to find its job, then read again under lock once the job is held: whatever decided it
    in between (a status change superseding it, a reject) wins, and nothing is applied.

    A proposal whose target is the profile is handed to item_proposals.accept, which returns the
    proposal with the profile and raises NotPendingError, InvalidValueError (EditedValueError),
    ItemInvalidError, ProfileConflictError and StaleItemError; `profile_revision` is the profile
    revision the user was looking at, and is read for such a proposal only."""
    proposal = get(db, user_id, proposal_id)
    if proposal is None:
        return None
    if proposal.status is not ProposalStatus.pending:
        raise NotPendingError(proposal)
    if proposal.target == TARGET_PROFILE:  # the dispatch on the target, before the kind is read
        applied: tuple[Proposal, Profile] | None = _profile_items().accept(
            db,
            user_id,
            proposal_id,
            now,
            edited_value=edited_value,
            profile_revision=profile_revision,
        )
        return applied
    try:
        kind = ProposalKind(proposal.kind)
    except ValueError:
        raise InvalidValueError(UNKNOWN_KIND) from None
    edited = edited_value is not _MISSING
    if edited:
        cleaned = _CLEAN[kind](edited_value)
        value: Any = cleaned.value if isinstance(cleaned, JobStatus) else cleaned
    job = (
        jobs_service.lock_job(db, user_id, proposal.target_id)
        if proposal.target == TARGET_JOB and proposal.target_id is not None
        else None
    )
    proposal = get(db, user_id, proposal_id, lock=True)  # still the caller's, filtered by user
    if proposal is None:
        return None
    if proposal.status is not ProposalStatus.pending:
        raise NotPendingError(proposal)
    if not edited:
        value = proposal.payload.get(_VALUE_KEY[kind])
    if job is None:
        _decide(proposal, ProposalStatus.invalid, ProposalDecider.system, now, JOB_GONE)
        db.flush()
        raise TargetGoneError(proposal)
    if expected_updated_at is not None and job.updated_at != expected_updated_at:
        raise jobs_service.StaleJobError(job)
    proposed_from = proposal.payload.get("from")
    if (
        kind is ProposalKind.job_status
        and not edited
        and proposed_from is not None
        and proposed_from != job.status.value
    ):
        _decide(proposal, ProposalStatus.invalid, ProposalDecider.system, now, STATUS_CHANGED)
        db.flush()
        raise StatusChangedError(proposal)
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


def undo(
    db: Session, user_id: uuid.UUID, proposal_id: uuid.UUID, now: datetime
) -> tuple[Proposal, Profile] | None:
    """Take back an accepted profile item: the profile returns to what it held at the item's key
    before the accept, as one revision, and the proposal becomes `undone`. None when it is not
    one of the user's proposals. Raises InvalidValueError for another target (the job kinds have
    no undo here: a status change is undone through the jobs service) and
    item_proposals.UndoError when the item is not accepted, cannot be restored exactly, or the
    profile changed at that key since (nothing is changed then)."""
    proposal = get(db, user_id, proposal_id)
    if proposal is None:
        return None
    if proposal.target != TARGET_PROFILE:
        raise InvalidValueError(UNKNOWN_KIND)
    undone: tuple[Proposal, Profile] | None = _profile_items().undo(db, user_id, proposal_id, now)
    return undone
