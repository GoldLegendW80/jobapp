"""A run Anthropic refuses as busy waits and starts again, and the starts of one credential are
paced (agents A13, SPEC-agents.md "A13"). No SDK import: the api reads the waiting step too.

**Did nothing** (`untouched`), decided in code: the run read no token, made no tool call (let
through or refused) and wrote no text. Only such a run is started again: a refusal that comes
after a first tool call keeps today's failure, so an act on a page or a write is never made twice.

**The wait** (`plan`): Anthropic's own delay when its rate limit event names one (`resets_at`),
at most `agents_busy_retry_max_delay_s`, else the next of `agents_busy_retry_delays_s`; at most
`agents_busy_retry_tries` tries, the last one starting within `agents_busy_retry_window_s` of the
first refusal. Then the run fails as before, with `failures.BUSY`.

**The pace** (`start_at`, at the claim, under the person's run lock): a run started by the agents
queue waits its turn while `agents_starts_per_minute` runs of the same person started, or are
planned to start, in the minute before; turns are given in the order they are asked for. While
one of the person's runs waits on a refusal, a try again starts only when none of the person's
other runs is running (`COOL_STEP_S` between looks): one at a time after a refusal.

A waiting run is `queued` (cancelled at once, as any queued run) and its last event is a
`waiting` step: `{"reason": "busy" | "pace", "at": <ISO time>, "try": <next try>, "of": <tries>}`.
The credential is the person's own, so every count here is the person's, never the app's.
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
MINUTE = timedelta(seconds=60)
# How long a try again waits, after a refusal, for the person's other runs to end.
COOL_STEP_S = 15
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
    refused: Sequence[datetime], now: datetime, provider_s: float | None, limits: Bounds
) -> datetime | None:
    """When a run refused at `now` tries again, its earlier refusals being `refused` (oldest
    first); None when it fails now (its tries used up, or the try would start past the window)."""
    tries = len(refused) + 1  # the tries made, this one included
    if tries >= limits.tries:
        return None
    if provider_s is not None and provider_s > 0:
        wait = min(provider_s, float(limits.max_delay_s))
    else:
        wait = float(limits.delays_s[min(tries, len(limits.delays_s)) - 1])
    first = refused[0] if refused else now
    at = now + timedelta(seconds=wait)
    return at if at <= first + timedelta(seconds=limits.window_s) else None


def _last_steps(db: Session, user_id: uuid.UUID, but: uuid.UUID) -> list[tuple[uuid.UUID, Any]]:
    """The last step of each queued run of the person but `but`: (run id, the step's data) for
    those that wait."""
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
    rows = db.execute(
        sa.select(AgentEvent.run_id, AgentEvent.data)
        .join(last, sa.and_(last.c.run_id == AgentEvent.run_id, last.c.seq == AgentEvent.seq))
        .where(AgentEvent.kind == WAITING)
    ).all()
    return [(row.run_id, row.data) for row in rows]


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


def start_at(
    db: Session, run: AgentRun, now: datetime, limits: Bounds, paced: bool
) -> tuple[str, datetime] | None:
    """When the queued `run` may start: None now, else why it waits and until when. The caller
    holds the person's run lock (runs.lock_user)."""
    own = last_wait(db, run.id)
    others = _last_steps(db, run.user_id, run.id)
    # Refused runs only: a run once held behind one is not a refused one itself.
    refused = db.scalar(
        sa.select(sa.func.count())
        .select_from(AgentEvent)
        .join(AgentRun, AgentRun.id == AgentEvent.run_id)
        .where(
            AgentRun.user_id == run.user_id,
            AgentRun.status == RunStatus.queued,
            AgentEvent.kind == WAITING,
            AgentEvent.data["retry"].astext == "refused",
            sa.or_(AgentRun.id == run.id, sa.literal(paced)),
        )
    )
    cooling = bool(refused)
    if cooling:
        running = db.scalar(
            sa.select(sa.func.count())
            .select_from(AgentRun)
            .where(
                AgentRun.user_id == run.user_id,
                AgentRun.status == RunStatus.running,
                AgentRun.id != run.id,
            )
        )
        if running:
            return BUSY, now + timedelta(seconds=COOL_STEP_S)
    if not paced:
        return None
    started = db.scalars(
        sa.select(AgentRun.started_at).where(
            AgentRun.user_id == run.user_id,
            AgentRun.id != run.id,
            AgentRun.started_at > now - MINUTE,
            AgentRun.started_at <= now,
        )
    ).all()
    planned = [at for _, data in others if (at := _at(data)) is not None and at > now - MINUTE]
    turns = [_at(data) for _, data in others if data.get("reason") == PACE]
    # A run that already had a turn keeps its place; a new one queues behind every turn given.
    begin = now if own is not None else max([now, *(at for at in turns if at is not None)])
    starts = sorted([*started, *(at for at in planned if at <= begin)])
    if len([at for at in starts if at > begin - MINUTE]) < limits.per_minute:
        return None if begin == now else (PACE, begin)
    return PACE, max(begin, starts[-limits.per_minute] + MINUTE)


def wait_step(
    reason: str, at: datetime, retry: str, attempt: int, limits: Bounds
) -> dict[str, Any]:
    """The `waiting` step's data. `retry` is "refused" for the wait a refusal starts, "held" for
    a turn given later (the pace, or one at a time after a refusal)."""
    return {
        "reason": reason,
        "at": at.isoformat(),
        "retry": retry,
        "try": attempt,
        "of": limits.tries,
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


def overdue(db: Session, before: datetime) -> list[uuid.UUID]:
    """Queued runs whose last step is a wait that ended before `before`: whoever was to start
    them again is gone (a worker stopped between the wait and its job)."""
    last = (
        sa.select(AgentEvent.run_id, sa.func.max(AgentEvent.seq).label("seq"))
        .join(AgentRun, AgentRun.id == AgentEvent.run_id)
        .where(AgentRun.status == RunStatus.queued)
        .group_by(AgentEvent.run_id)
        .subquery()
    )
    rows = db.execute(
        sa.select(AgentEvent.run_id, AgentEvent.data)
        .join(last, sa.and_(last.c.run_id == AgentEvent.run_id, last.c.seq == AgentEvent.seq))
        .where(AgentEvent.kind == WAITING)
    ).all()
    return [row.run_id for row in rows if (at := _at(row.data)) is None or at < before]
