"""Agent runs and their events in the database: the state machine both the api and the worker
go through (SPEC-agents.md, Data model).

    queued --claim--> running --finish--> done | failed | cancelled
    queued --cancel or queue down--> cancelled | failed

Every transition is one conditional UPDATE on the status it leaves, so two actors racing (the
user cancelling while the worker claims, a reaper and a finishing run) never both win: the loser
changes nothing and learns it from the returned row count. Reads for a user always filter on
that user (another user's run is "not found").

Events: `seq` counts from 1 per run. The api writes the first one (`status: queued`) with the
run, and the cancel of a queued run; from the claim on, only the run's own runner appends
(app/agents/runner.py), so there is one writer and seq never collides (the unique constraint on
(run_id, seq) would refuse it). The runner starts from `next_seq` after its claim.
"""

import enum
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agents.models import (
    FINAL_STATUSES,
    AgentEvent,
    AgentRun,
    CredentialKind,
    RunStatus,
)

# At most this many queued or running runs per user at once; a further start answers 429.
ACTIVE_PER_USER = 4
INTERRUPTED = "Interrupted: the worker restarted while this run was running."
STOPPED = "Interrupted: the worker stopped while this run was running."
LOST = "The run never started: its queue message was lost."
STALE = "The run stopped reporting and was marked failed."
QUEUE_DOWN = "The agent queue is unavailable. Try again in a minute."


class ActiveRunsError(Exception):
    """The user already has ACTIVE_PER_USER queued or running runs."""


def runs_of(user_id: uuid.UUID) -> sa.Select[tuple[AgentRun]]:
    return sa.select(AgentRun).where(AgentRun.user_id == user_id)


def get_run(db: Session, user_id: uuid.UUID, run_id: uuid.UUID) -> AgentRun | None:
    return db.execute(
        runs_of(user_id).where(AgentRun.id == run_id).execution_options(populate_existing=True)
    ).scalar_one_or_none()


def list_runs(
    db: Session,
    user_id: uuid.UUID,
    limit: int,
    before: datetime | None = None,
    *,
    job_id: uuid.UUID | None = None,
    agent: str | None = None,
) -> Sequence[AgentRun]:
    query = runs_of(user_id)
    if before is not None:
        query = query.where(AgentRun.created_at < before)
    if job_id is not None:
        query = query.where(AgentRun.job_id == job_id)
    if agent is not None:
        query = query.where(AgentRun.agent == agent)
    return db.scalars(
        query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(limit)
    ).all()


def count_active(db: Session, user_id: uuid.UUID) -> int:
    return (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(AgentRun)
            .where(
                AgentRun.user_id == user_id,
                AgentRun.status.in_((RunStatus.queued, RunStatus.running)),
            )
        )
        or 0
    )


def events_after(
    db: Session, run_id: uuid.UUID, after_seq: int = 0, limit: int = 500
) -> Sequence[AgentEvent]:
    return db.scalars(
        sa.select(AgentEvent)
        .where(AgentEvent.run_id == run_id, AgentEvent.seq > after_seq)
        .order_by(AgentEvent.seq)
        .limit(limit)
    ).all()


def next_seq(db: Session, run_id: uuid.UUID) -> int:
    last = db.scalar(sa.select(sa.func.max(AgentEvent.seq)).where(AgentEvent.run_id == run_id))
    return (last or 0) + 1


def append_event(
    db: Session, run_id: uuid.UUID, seq: int, kind: str, data: dict[str, Any], now: datetime
) -> None:
    db.add(AgentEvent(run_id=run_id, seq=seq, kind=kind, data=data, at=now))
    db.flush()


def create_run(
    db: Session,
    user_id: uuid.UUID,
    agent: str,
    job_id: uuid.UUID | None,
    data: dict[str, Any],
    model: str,
    now: datetime,
) -> AgentRun:
    """A queued run and its first event. Raises ActiveRunsError over ACTIVE_PER_USER. The caller
    commits, then enqueues."""
    # Serialises the count and the insert per user (two clicks at once cannot both pass).
    db.execute(sa.select(sa.func.pg_advisory_xact_lock(_lock_key(user_id))))
    if count_active(db, user_id) >= ACTIVE_PER_USER:
        raise ActiveRunsError
    run = AgentRun(
        user_id=user_id,
        agent=agent,
        job_id=job_id,
        status=RunStatus.queued,
        input=data,
        model=model,
        created_at=now,
    )
    db.add(run)
    db.flush()
    append_event(db, run.id, 1, "status", {"status": RunStatus.queued.value}, now)
    return run


def _lock_key(user_id: uuid.UUID) -> int:
    # A 63 bit key from the user id, in its own namespace (the high bits of the uuid).
    return (user_id.int >> 65) ^ 0x6A6F_6261_6770


class CancelOutcome(enum.StrEnum):
    cancelled = "cancelled"  # was queued: cancelled now, the worker will skip it
    requested = "requested"  # is running: the worker stops it within a second or so
    final = "final"  # already done, failed or cancelled: nothing to do
    missing = "missing"


def request_cancel(
    db: Session, user_id: uuid.UUID, run_id: uuid.UUID, now: datetime
) -> CancelOutcome:
    """Cancel the caller's run. The caller commits."""
    queued = db.execute(
        sa.update(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.user_id == user_id,
            AgentRun.status == RunStatus.queued,
        )
        .values(status=RunStatus.cancelled, cancel_requested_at=now, finished_at=now)
        .execution_options(synchronize_session=False)
    )
    if queued.rowcount:
        append_event(
            db, run_id, next_seq(db, run_id), "status", {"status": RunStatus.cancelled.value}, now
        )
        return CancelOutcome.cancelled
    running = db.execute(
        sa.update(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.user_id == user_id,
            AgentRun.status == RunStatus.running,
        )
        .values(cancel_requested_at=sa.func.coalesce(AgentRun.cancel_requested_at, now))
        .execution_options(synchronize_session=False)
    )
    if running.rowcount:
        return CancelOutcome.requested
    return CancelOutcome.missing if get_run(db, user_id, run_id) is None else CancelOutcome.final


def cancel_requested(db: Session, run_id: uuid.UUID) -> bool:
    return (
        db.scalar(sa.select(AgentRun.cancel_requested_at).where(AgentRun.id == run_id)) is not None
    )


# --- worker side ------------------------------------------------------------------------------


def claim(db: Session, run_id: uuid.UUID, now: datetime) -> AgentRun | None:
    """queued -> running, or None when the run is not queued (cancelled meanwhile, unknown, or
    already claimed). The caller commits."""
    claimed = db.execute(
        sa.update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status == RunStatus.queued)
        .values(status=RunStatus.running, started_at=now)
        .returning(AgentRun.id)
    ).scalar_one_or_none()
    if claimed is None:
        return None
    return db.get(AgentRun, run_id, populate_existing=True)


def record_credential_kind(db: Session, run_id: uuid.UUID, kind: CredentialKind) -> None:
    """The kind of credential the run is about to use (never the secret): what says later
    whether its cost was billed (an API key) or is an estimate (a subscription). The caller
    commits."""
    db.execute(
        sa.update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(credential_kind=kind)
        .execution_options(synchronize_session=False)
    )


@dataclass(frozen=True)
class Outcome:
    status: RunStatus
    error: str | None = None
    output: dict[str, Any] | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None


def finish(db: Session, run_id: uuid.UUID, outcome: Outcome, now: datetime) -> bool:
    """running -> a final status with its results; False when the run was not running (a
    reaper got there first). The caller commits."""
    if outcome.status not in FINAL_STATUSES:
        raise ValueError(f"not a final status: {outcome.status}")
    values: dict[str, Any] = {
        "status": outcome.status,
        "error": outcome.error,
        "output": outcome.output,
        "input_tokens": outcome.input_tokens,
        "output_tokens": outcome.output_tokens,
        "cost_usd": outcome.cost_usd,
        "finished_at": now,
    }
    if outcome.model is not None:
        values["model"] = outcome.model
    result = db.execute(
        sa.update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status == RunStatus.running)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)


def fail_queued(db: Session, run_id: uuid.UUID, error: str, now: datetime) -> bool:
    """queued -> failed (the queue refused the job). The caller commits."""
    result = db.execute(
        sa.update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status == RunStatus.queued)
        .values(status=RunStatus.failed, error=error, finished_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        append_event(
            db,
            run_id,
            next_seq(db, run_id),
            "status",
            {"status": RunStatus.failed.value, "error": error},
            now,
        )
    return bool(result.rowcount)


def _fail_where(db: Session, condition: sa.ColumnElement[bool], error: str, now: datetime) -> int:
    # The reapers break the one-writer rule on purpose: they write a run's last event with
    # next_seq. reap_orphans runs before any runner of this process; reap_stale only touches runs
    # past every limit, whose runner, if one still lived, would collide on (run_id, seq). The
    # unique constraint then fails that write instead of mixing up the log, and finish() leaves a
    # run that is no longer running alone.
    ids = db.scalars(
        sa.update(AgentRun)
        .where(condition)
        .values(status=RunStatus.failed, error=error, finished_at=now)
        .returning(AgentRun.id)
        .execution_options(synchronize_session=False)
    ).all()
    for run_id in ids:
        append_event(
            db,
            run_id,
            next_seq(db, run_id),
            "status",
            {"status": RunStatus.failed.value, "error": error},
            now,
        )
    return len(ids)


def reap_orphans(db: Session, now: datetime) -> int:
    """At worker start: every run still `running` was running in the process that died (one
    worker process runs agents). The caller commits."""
    return _fail_where(db, AgentRun.status == RunStatus.running, INTERRUPTED, now)


def reap_stale(
    db: Session, now: datetime, running_after: timedelta, queued_after: timedelta
) -> int:
    """Periodically: runs running longer than any limit allows (the runner could not write its
    end), and runs queued so long that their queue message must be lost. The caller commits."""
    stale_running = sa.and_(
        AgentRun.status == RunStatus.running, AgentRun.started_at < now - running_after
    )
    count = _fail_where(db, stale_running, STALE, now)
    stale_queued = sa.and_(
        AgentRun.status == RunStatus.queued, AgentRun.created_at < now - queued_after
    )
    return count + _fail_where(db, stale_queued, LOST, now)
