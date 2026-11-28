"""A run Anthropic refuses as busy waits and starts again, and the starts of one credential on
the agents queue are paced (agents A13, SPEC-agents.md "A13"). No SDK import: the api reads the
waiting step too.

**Did nothing** (`untouched`), decided in code: the run read no token, made no tool call (let
through or refused) and wrote no text. Only such a run is started again: a refusal that comes
after a first tool call keeps today's failure, so an act on a page or a write is never made twice.

**The wait** (`plan`): Anthropic's own delay when its rate limit event names one (`resets_at`),
at most `agents_busy_retry_max_delay_s`, else the next of `agents_busy_retry_delays_s`; at most
`agents_busy_retry_tries` tries, the last one starting within `agents_busy_retry_window_s` of the
first refusal, and before the caller's own bound when it has one (the hiring panel's job). Then
the run fails as before, with `failures.BUSY`. A refused run waits its own time and holds no
other run: nothing a run waits for depends on another run ending.

**The pace** (`start_at`, at the claim, under the person's run lock): a run the agents queue
starts (`run_agent`: the writers, chat, ingestion, the posting reader) waits its turn while
`agents_starts_per_minute` of the person's starts on that queue happened, or are planned, in the
minute before; turns are given in the order they are asked for; 0 is no pace. Those starts and
waits carry `PACED`; the runs the hiring panel starts inside its job and the apply agent's
neither count nor wait.

A waiting run is `queued` (cancelled at once, as any queued run) and its last event is a
`waiting` step: `{"reason": "busy" | "pace", "at": <ISO time>, "retry": "refused" | "held",
"try": <next try>, "of": <tries>, "paced": <on the agents queue>}`. The credential is the
person's own, so every count here is the person's, never the app's.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agents.models import AgentEvent, AgentRun, RunStatus

WAITING = "waiting"
BUSY = "busy"
PACE = "pace"
PACED = "paced"  # the key that marks a start or a wait of the agents queue
MINUTE = timedelta(seconds=60)
# Kinds of step that show the run did something: a tool call (let through or refused), a text.
ACTED = ("tool_call", "tool_denied", "tool_result", "text")


@dataclass(frozen=True)
class Bounds:
    delays_s: tuple[int, ...] = (30, 120, 480)
    max_delay_s: int = 600
    tries: int = 4
    window_s: int = 1800
    per_minute: int = 6


def bounds(settings: Any) -> Bounds:
    return Bounds(
        delays_s=tuple(int(part) for part in str(settings.agents_busy_retry_delays_s).split(",")),
        max_delay_s=int(settings.agents_busy_retry_max_delay_s),
        tries=int(settings.agents_busy_retry_tries),
        window_s=int(settings.agents_busy_retry_window_s),
        per_minute=int(settings.agents_starts_per_minute),
    )


def untouched(
    tool_calls: int,
    denied: int,
    text_blocks: int,
    input_tokens: int | None,
    output_tokens: int | None,
) -> bool:
    """Whether a run did nothing: no tool call, no refused call, no text, no token read."""
    return not (tool_calls or denied or text_blocks or input_tokens or output_tokens)


def plan(
    refused: Sequence[datetime],
    now: datetime,
    provider_s: float | None,
    limits: Bounds,
    until: datetime | None = None,
) -> datetime | None:
    """When a run refused at `now` tries again, its earlier refusals being `refused` (oldest
    first); None when it fails now: its tries used up, or the try would start past the window or
    past `until` (the caller's own bound)."""
    tries = len(refused) + 1  # the tries made, this one included
    if tries >= limits.tries:
        return None
    if provider_s is not None and provider_s > 0:
        wait = min(provider_s, float(limits.max_delay_s))
    else:
        wait = float(limits.delays_s[min(tries, len(limits.delays_s)) - 1])
    first = refused[0] if refused else now
    at = now + timedelta(seconds=wait)
    latest = first + timedelta(seconds=limits.window_s)
    if until is not None:
        latest = min(latest, until)
    return at if at <= latest else None


def running(paced: bool) -> dict[str, Any]:
    """The `status` step of a claim; a start of the agents queue is marked, the pace counts it."""
    return {"status": "running", PACED: True} if paced else {"status": "running"}


def _paced_waits(db: Session, user_id: uuid.UUID, but: uuid.UUID) -> list[dict[str, Any]]:
    """The last step of each queued run of the person but `but`, for those that wait on the
    agents queue: the step's data."""
    last = (
        sa.select(AgentEvent.run_id, sa.func.max(AgentEvent.seq).label("seq"))
        .join(AgentRun, AgentRun.id == AgentEvent.run_id)
        .where(
            AgentRun.user_id == user_id,
            AgentRun.status == RunStatus.queued,
            AgentRun.id != but,
        )
        .group_by(AgentEvent.run_id)
        .subquery()
    )
    rows = db.scalars(
        sa.select(AgentEvent.data)
        .join(last, sa.and_(last.c.run_id == AgentEvent.run_id, last.c.seq == AgentEvent.seq))
        .where(AgentEvent.kind == WAITING)
    ).all()
    return [dict(data) for data in rows if data.get(PACED) is True]


def _paced_starts(db: Session, user_id: uuid.UUID, but: uuid.UUID, now: datetime) -> list[datetime]:
    """When the agents queue started the person's runs but `but` in the minute before `now`."""
    return list(
        db.scalars(
            sa.select(AgentEvent.at)
            .join(AgentRun, AgentRun.id == AgentEvent.run_id)
            .where(
                AgentRun.user_id == user_id,
                AgentRun.id != but,
                # A run's last start is its started_at: older runs started nothing in the minute.
                AgentRun.started_at > now - MINUTE,
                AgentEvent.kind == "status",
                AgentEvent.data[PACED].astext == "true",
                AgentEvent.at > now - MINUTE,
                AgentEvent.at <= now,
            )
        ).all()
    )


def last_wait(db: Session, run_id: uuid.UUID) -> tuple[int, dict[str, Any]] | None:
    """The run's last step when it is a `waiting` one: its seq and data."""
    row = db.execute(
        sa.select(AgentEvent.seq, AgentEvent.kind, AgentEvent.data)
        .where(AgentEvent.run_id == run_id)
        .order_by(AgentEvent.seq.desc())
        .limit(1)
    ).one_or_none()
    if row is None or row.kind != WAITING:
        return None
    return row.seq, dict(row.data)


def refusals(db: Session, run_id: uuid.UUID) -> list[datetime]:
    """When the run was refused as busy, oldest first (one `waiting` step of reason busy each)."""
    rows = db.execute(
        sa.select(AgentEvent.at, AgentEvent.data["retry"].astext.label("retry"))
        .where(
            AgentEvent.run_id == run_id,
            AgentEvent.kind == WAITING,
            AgentEvent.data["reason"].astext == BUSY,
        )
        .order_by(AgentEvent.seq)
    ).all()
    return [row.at for row in rows if row.retry == "refused"]


def _at(data: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(data.get("at")))
    except (AttributeError, TypeError, ValueError):
        return None


def start_at(db: Session, run: AgentRun, now: datetime, limits: Bounds) -> datetime | None:
    """When the queued `run` of the agents queue may start: None now, else its turn. Only the
    person's starts and turns on that queue count, never whether another run has ended. The
    caller holds the person's run lock (runs.lock_user)."""
    if limits.per_minute <= 0:
        return None
    own = last_wait(db, run.id)
    others = _paced_waits(db, run.user_id, run.id)
    started = _paced_starts(db, run.user_id, run.id, now)
    planned = [at for data in others if (at := _at(data)) is not None and at > now - MINUTE]
    turns = [at for data in others if data.get("reason") == PACE and (at := _at(data)) is not None]
    # A run that already had a turn keeps its place; a new one queues behind every turn given.
    begin = now if own is not None else max([now, *turns])
    starts = sorted([*started, *(at for at in planned if at <= begin)])
    if len([at for at in starts if at > begin - MINUTE]) < limits.per_minute:
        return None if begin == now else begin
    return max(begin, starts[-limits.per_minute] + MINUTE)


def wait_step(
    reason: str, at: datetime, retry: str, attempt: int, limits: Bounds, paced: bool
) -> dict[str, Any]:
    """The `waiting` step's data. `retry` is "refused" for the wait a refusal starts, "held" for
    a turn of the pace; `paced` for a run of the agents queue."""
    return {
        "reason": reason,
        "at": at.isoformat(),
        "retry": retry,
        "try": attempt,
        "of": limits.tries,
        PACED: paced,
    }


def refused_untouched(db: Session, run_id: uuid.UUID, busy_error: str) -> bool:
    """Whether a failed run was refused as busy (`busy_error`) before it did anything: no token
    recorded and no step of `ACTED`. What a caller that cannot let the run wait reads (apply)."""
    row = db.execute(
        sa.select(
            AgentRun.status, AgentRun.error, AgentRun.input_tokens, AgentRun.output_tokens
        ).where(AgentRun.id == run_id)
    ).one_or_none()
    if row is None or row.status is not RunStatus.failed or row.error != busy_error:
        return False
    if row.input_tokens or row.output_tokens:
        return False
    acted = db.scalar(
        sa.select(sa.func.count())
        .select_from(AgentEvent)
        .where(AgentEvent.run_id == run_id, AgentEvent.kind.in_(ACTED))
    )
    return not acted


def overdue(db: Session, before: datetime) -> list[tuple[uuid.UUID, int]]:
    """Queued runs whose last step is a wait that ended before `before`, with that step's seq:
    whoever was to start them again may be gone (the worker's reaper asks the queue)."""
    last = (
        sa.select(AgentEvent.run_id, sa.func.max(AgentEvent.seq).label("seq"))
        .join(AgentRun, AgentRun.id == AgentEvent.run_id)
        .where(AgentRun.status == RunStatus.queued)
        .group_by(AgentEvent.run_id)
        .subquery()
    )
    rows = db.execute(
        sa.select(AgentEvent.run_id, AgentEvent.seq, AgentEvent.data)
        .join(last, sa.and_(last.c.run_id == AgentEvent.run_id, last.c.seq == AgentEvent.seq))
        .where(AgentEvent.kind == WAITING)
    ).all()
    return [(row.run_id, row.seq) for row in rows if (at := _at(row.data)) is None or at < before]
