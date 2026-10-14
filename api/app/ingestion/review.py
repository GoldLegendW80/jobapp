"""The bulk decisions of the review (G13): several items of one section of one run, at once.

`accept_section` is "Accept all in this section" and `reject_section` "Reject the rest of this
section". Both are `item_proposals.accept_many` and `reject_many` (G5) narrowed to one run and
one section, so nothing is applied here that a single accept would not apply, and the profile is
written only through `profile.service.save_section`.

What the narrowing gives:
- **One save, one revision.** One section per call, and `accept_many` saves a section once: the
  call makes one profile revision, or none when the profile already held every item.
- **All or nothing.** One item that no longer fits its section model, or that the section cannot
  take (a list at its cap, a name another entry holds), stops the call: nothing is applied,
  `SectionRefusedError` names the item, and the others stay pending. An item that no longer fits is
  marked `invalid` on the way, as a single accept marks it: the caller commits that and nothing
  else.
- **Left out, never refused.** A locked fact, a flagged item (instruction, derived, transcribed,
  conflict), a removal and a stale item are accepted one by one, so the call leaves them pending
  and says so with the reason. An id that is not one of the user's pending items of this run
  (another user's, another run's, unknown) is left out as `not_found`, with nothing that tells
  them apart; one decided meanwhile is `not_pending`.

Locks, in the plan's order: `accept_section` takes the `profiles` row (2) then the proposals
rows in id order (8); `reject_section` takes the proposals rows only. The run row is read and
never locked, so neither waits for a run in flight. Two tabs, or a decision and the merge of a
newer run that supersedes the same items, are settled by those row locks: the one that comes
second reads each row after the first has committed and finds it decided. A wait longer than
`LOCK_WAIT_MS` raises `ReviewBusyError` and applies nothing (the lock timeout of the jobs
module, set for the transaction only).

The caller owns the transaction and passes its own user id: another user's run is not found.
Nothing here logs.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.ingestion import item_proposals
from app.ingestion.runs_models import IngestionRun
from app.jobs import service as jobs_service

LOCK_WAIT_MS = jobs_service.LOCK_TIMEOUT_MS
IDS_MAX = 2_500  # ids taken in one call: every item a run may show


class ReviewBusyError(Exception):
    """Another transaction holds the profile or one of the proposals for longer than the lock
    timeout. Nothing was applied: the caller rolls back and answers 409."""


class SectionRefusedError(Exception):
    """A bulk accept that applied nothing, because of one item. `failure.reason` is `invalid`
    (the item is now marked so: the caller commits) or `profile_conflict` (the caller rolls
    back)."""

    def __init__(self, failure: item_proposals.SectionFailure) -> None:
        super().__init__(failure.reason)
        self.failure = failure


@dataclass
class Decided:
    """`done`: the proposals accepted (or rejected), in the order they were applied. `skipped`:
    the ids left out, each with its reason. `revision`: the one profile revision an accept made,
    None when it changed nothing (and always None for a reject)."""

    done: list[uuid.UUID] = field(default_factory=list)
    skipped: dict[uuid.UUID, str] = field(default_factory=dict)
    revision: int | None = None

    @property
    def left_out(self) -> dict[str, int]:
        """How many were left out, by reason."""
        return dict(Counter(self.skipped.values()))


def run_of(db: Session, user_id: uuid.UUID, run_id: uuid.UUID) -> IngestionRun | None:
    """The user's run, or None: another user's run is an unknown one."""
    return db.scalar(
        sa.select(IngestionRun).where(IngestionRun.id == run_id, IngestionRun.user_id == user_id)
    )


def _wait_at_most(db: Session, wait_ms: int) -> None:
    db.execute(sa.text("select set_config('lock_timeout', :ms, true)"), {"ms": f"{wait_ms}ms"})


def _decided(result: item_proposals.BulkResult, section: str) -> Decided:
    # Another run's item is the user's own, but this run's page never showed it: it answers as
    # an id that does not exist.
    skipped = {
        one: item_proposals.NOT_FOUND if reason == item_proposals.OTHER_RUN else reason
        for one, reason in result.skipped.items()
    }
    return Decided(list(result.done), skipped, result.revisions.get(section))


def accept_section(
    db: Session,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    section: str,
    proposal_ids: Sequence[uuid.UUID],
    now: datetime,
    *,
    wait_ms: int = LOCK_WAIT_MS,
) -> Decided:
    """Accept the ids given that are pending items of this run and section and that a bulk
    accept may take, in one profile save. Raises SectionRefusedError when one of them stops the
    section (nothing applied) and ReviewBusyError when a lock is not free in time."""
    _wait_at_most(db, wait_ms)
    try:
        result = item_proposals.accept_many(
            db, user_id, now, proposal_ids=proposal_ids, ingestion_run_id=run_id, section=section
        )
    except DBAPIError as exc:
        if jobs_service.is_lock_timeout(exc):
            raise ReviewBusyError from exc
        raise
    failure = result.failed.get(section)
    if failure is not None:
        raise SectionRefusedError(failure)
    return _decided(result, section)


def reject_section(
    db: Session,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    section: str,
    proposal_ids: Sequence[uuid.UUID],
    now: datetime,
    *,
    wait_ms: int = LOCK_WAIT_MS,
) -> Decided:
    """Reject the ids given that are pending items of this run and section, locked and flagged
    ones too. The profile is not touched. Raises ReviewBusyError when a lock is not free in
    time."""
    _wait_at_most(db, wait_ms)
    try:
        result = item_proposals.reject_many(
            db, user_id, now, proposal_ids=proposal_ids, ingestion_run_id=run_id, section=section
        )
    except DBAPIError as exc:
        if jobs_service.is_lock_timeout(exc):
            raise ReviewBusyError from exc
        raise
    return _decided(result, section)
