"""The chain of an ingestion run (ingestion G10): start, advance, pause, resume, continue, cancel.

A run is a row of `ingestion_runs` and its ordered steps; each step that calls the model is an
ordinary agent run on the agents queue. `advance` is the one function that moves a run. It is
called by the routes (start, cancel, resume, continue), by the `on_final` hook of the agents
when a step's agent run has ended, and by a cron every minute for every run that is not final:
the cron is the only guarantee, the hook is a shortcut.

The states of a run, and what moves between them:

    running  --a provider or box failure, the queue down, the token ceiling-->  paused
    paused   --Resume (any reason but the ceiling), Continue (the ceiling)-->   running
    running  --no step left, or the 60 minutes passed-->                        done | failed
    paused   --24 hours without a resume-->                                     done | failed
    running, paused --Cancel, once the running step has ended-->                cancelled

`done` carries `partial = true` when something selected was not read; `failed` is a run in which
no step finished. A step is `planned`, then `running` (it has an agent run), then `done`,
`failed` or `cancelled`; `reused` from the start when an earlier run already read it. A step
goes back from `running` to `planned` when it is to run again: after a provider or box failure,
after a worker restart (once), when the ceiling interrupted it.

Rules that hold everywhere in this file:

* Every change of a run happens under its row lock (`FOR UPDATE`), in the plan's lock order: the
  run row (3), the step row (5), the user's advisory lock of `runs.create_run` (6), the
  `agent_runs` row (7). A document row is only read, never locked.
* The `agent_runs` row of a step is read without a lock and written only by the short update of
  a cancel request: a lock held on it would hold back that run's event writes.
* A step gets its final status only once its agent run has ended (the tools of G9 answer "not
  found" for a step that is no longer `running`). A retry is a new agent run.
* The enqueue comes after the commit. `advance` answers the agent run to enqueue; `drive` does
  it and, when Redis refuses, records the failure and pauses the run.
* `tokens_spent` is added once per agent run, when its end is recorded, so both attempts of a
  step a restart cut are counted. The ceiling is compared with it before every step that will
  call the model, and never for a run whose steps are all reused.
* Nothing of a document or of an item is logged, raised, queued or returned by a job: ids,
  statuses and counts only.
"""

import asyncio
import logging
import math
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import credentials, definitions, failures
from app.agents import runs as agent_runs
from app.agents.models import AgentEvent, AgentRun
from app.agents.models import RunStatus as AgentStatus
from app.config import Settings
from app.ingestion import agents, estimate, estimate_router, merge, plan, reuse
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import (
    IngestionItem,
    IngestionRun,
    IngestionRunStep,
    RunStatus,
    StepStatus,
    identity_hash,
    of_stored_text,
    stored_text_sha256,
    text_sha256,
)
from app.profile import service as profile_service

logger = logging.getLogger("app.ingestion.engine")

ACTIVE = (RunStatus.running, RunStatus.paused)
RUN_TIME = timedelta(minutes=plan.RUN_MINUTES)
PAUSE_MAX = timedelta(hours=24)
PURGE_FOR = timedelta(minutes=5)  # how long after its end a purged step's events are deleted again
ATTEMPTS = 2  # a step cut by a restart or failed by the box runs once more, then it has failed

TOKEN_CEILING = "token_ceiling"  # noqa: S105, a reason, not a secret
WAITING_SLOTS = "slots"
QUEUE_DOWN = "The agent queue is unavailable."
TRANSCRIBE_NAME = "document_transcribe"  # registered by G14
AGENT_OF = {plan.StepKind.extract: agents.EXTRACT_NAME, plan.StepKind.transcribe: TRANSCRIBE_NAME}
REAPED = frozenset({agent_runs.INTERRUPTED, agent_runs.STOPPED, agent_runs.LOST, agent_runs.STALE})
_TIME_LIMIT = failures.TIME_LIMIT.split("{", 1)[0]
# The key of `RunContext.services` (and so of `FinalRun.services`) under which the worker gives
# the hook what enqueues an agent run.
ENQUEUE_SERVICE = "enqueue_agent_run"
# What G6 measured: a run can pass its ceiling by the request in flight when the count crosses
# it, and by one more when the CLI had already sent the next one.
OVERSHOOT_REQUESTS = 2
# A step the ceiling cut starts again from its first request, so it is never started on a
# budget that already failed or on less: every later budget is at least this many times the
# largest one the ceiling cut it on (`cut_budget`, stored on the step), and never above its
# backstop. A step cut at its backstop is the step's own failure.
CUT_GROWTH = 2
# `advance` raising this many times in a row at the cron (about as many minutes) closes the run:
# a run that cannot move must not stay the user's one active run for ever.
ADVANCE_FAILURES = 5
# Two failures closer than this count once: ticks that waited behind a long job run back to
# back and must not close a run in one instant. The cron ticks every minute and a statement
# times out after 15 seconds, so two real ticks are never this close; the five failures that
# close a run therefore span at least four gaps, two minutes.
ADVANCE_FAILURE_GAP = timedelta(seconds=30)
STUCK = (
    "This run was closed because an error on our side kept it from going on. The documents it "
    "had finished reading are kept, and a new run does not read them again."
)

Enqueue = Callable[[uuid.UUID], Awaitable[Any]]
# The recorded streams of the fake backend (app/agents/fake_backend.py), found without importing it.
STREAMS = Path(definitions.__file__).parent / "fake_streams"


class ActiveRunError(Exception):
    """The user already has a run that is running or paused."""

    def __init__(self, run_id: uuid.UUID | None) -> None:
        super().__init__("active run")
        self.run_id = run_id


class TextChangedError(Exception):
    """A document's text changed between the quote and the creation of the steps."""


class ContinueChangedError(Exception):
    """The figure the click carried is not what one Continue adds now (`adds`): nothing is
    written, the page shows the new figure and asks again."""

    def __init__(self, adds: int) -> None:
        super().__init__()
        self.adds = adds


class WrongStateError(Exception):
    """Resume or Continue on a run that is not paused, or paused for the other reason."""


@dataclass(frozen=True)
class Moved:
    """What `advance` answers: the run's status now (None: no such run) and the agent run to
    put on the queue once the transaction has committed."""

    status: RunStatus | None
    enqueue: uuid.UUID | None = None


@dataclass(frozen=True)
class NextStep:
    """What `next_step` answers: the step that would run next and the smallest budget it may
    start on (`step` None and `needs` zero: nothing would run, the run would close). The rest
    is what `_next` writes before it starts that step, listed here and not done: the steps that
    cannot run (`failed`), the planned parts of a replaced text (`removed`) and the parts of
    the text stored now (`added`, rows no session holds yet). `step` can be one of `added`."""

    step: IngestionRunStep | None
    needs: int
    failed: tuple[IngestionRunStep, ...] = ()
    removed: tuple[IngestionRunStep, ...] = ()
    added: tuple[IngestionRunStep, ...] = ()


# --- start ------------------------------------------------------------------------------------


def active_run_id(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    return db.scalar(
        sa.select(IngestionRun.id).where(
            IngestionRun.user_id == user_id, IngestionRun.status.in_(ACTIVE)
        )
    )


def create(
    db: Session, user_id: uuid.UUID, quote: estimate_router.Quote, now: datetime
) -> IngestionRun:
    """The run of a quote and its steps, in the caller's transaction (the one that checked the
    consent and the echo). Raises `ActiveRunError` when the user has a run that is not final
    (from the partial unique index when two starts race) and `TextChangedError` when a text is
    no longer the one the quote cut. The caller commits, then calls `drive`."""
    found = active_run_id(db, user_id)
    if found is not None:
        raise ActiveRunError(found)
    doc = IngestionDocument
    stored = {
        row.id: row
        for row in db.execute(
            sa.select(
                doc.id,
                doc.sha256,
                stored_text_sha256().label("text_hash"),
                sa.func.coalesce(sa.func.char_length(doc.text), 0).label("characters"),
            ).where(doc.user_id == user_id, doc.id.in_(quote.plan.planned_documents))
        )
    }
    figures = quote.estimate
    run = IngestionRun(
        user_id=user_id,
        document_ids=list(quote.selection),
        base_revision=profile_service.get_profile(db, user_id, now).revision,
        status=RunStatus.running,
        deadline_at=now + RUN_TIME,
        estimate_low=figures.low,
        estimate_high=figures.high,
        token_ceiling=figures.ceiling,
        tokens_spent=0,
        ceiling_history=[],
        created_at=now,
    )
    try:
        with db.begin_nested():
            db.add(run)
            db.flush()
    except IntegrityError:
        raise ActiveRunError(active_run_id(db, user_id)) from None
    ends: dict[uuid.UUID, int] = {}
    for position, step in enumerate(quote.plan.steps):
        row = stored.get(step.document_id)
        if row is None or bytes(row.sha256).hex() != step.identity.sha256:
            raise TextChangedError
        if step.kind is plan.StepKind.extract and not step.predicted:
            ends[step.document_id] = max(ends.get(step.document_id, 0), step.end or 0)
            if row.text_hash is None:
                raise TextChangedError
        db.add(_row_of(run.id, position, step, row.sha256, row.text_hash))
    if any(stored[document_id].characters != end for document_id, end in ends.items()):
        raise TextChangedError  # not the text that was cut: its parts would not cover it
    db.flush()
    logger.info(
        "ingestion_run_created",
        extra={
            "user_id": str(user_id),
            "ingestion_run_id": str(run.id),
            "steps": figures.steps,
            "reused_steps": figures.reused,
            "tokens_ceiling": figures.ceiling,
        },
    )
    return run


def _row_of(
    run_id: uuid.UUID, position: int, step: plan.Step, sha256: bytes, text_hash: bytes | None
) -> IngestionRunStep:
    """The row of a planned step. An extraction step carries the hash of the text it was cut
    from; a part predicted before its text exists carries none and is planned again from the
    real text before it runs."""
    extract = step.kind is plan.StepKind.extract
    known = extract and not step.predicted
    return IngestionRunStep(
        ingestion_run_id=run_id,
        position=position,
        kind=step.kind,
        document_id=step.document_id,
        sha256=sha256,
        part=step.part if extract else None,
        range_start=step.start if known else None,
        range_end=step.end if known else None,
        image_positions=None if extract else list(step.images),
        identity_hash=identity_hash(step.identity),
        text_sha256=text_hash if known else None,
        status=StepStatus.reused if step.reused else StepStatus.planned,
    )


# --- advance ----------------------------------------------------------------------------------


def _lock(db: Session, run_id: uuid.UUID, user_id: uuid.UUID | None = None) -> IngestionRun | None:
    query = sa.select(IngestionRun).where(IngestionRun.id == run_id)
    if user_id is not None:
        query = query.where(IngestionRun.user_id == user_id)
    return db.execute(
        query.with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _steps(db: Session, run_id: uuid.UUID) -> list[IngestionRunStep]:
    db.flush()  # the sessions do not flush by themselves: a re-read must not undo a change
    return list(
        db.scalars(
            sa.select(IngestionRunStep)
            .where(IngestionRunStep.ingestion_run_id == run_id)
            .order_by(IngestionRunStep.position)
            .execution_options(populate_existing=True)
        )
    )


def _agent(db: Session, agent_run_id: uuid.UUID | None) -> sa.Row[Any] | None:
    """The agent run of a step, read without a lock (columns, not the entity)."""
    if agent_run_id is None:
        return None
    run = AgentRun
    return db.execute(
        sa.select(
            run.id,
            run.status,
            run.error,
            run.input_tokens,
            run.output_tokens,
            run.cancel_requested_at,
            run.finished_at,
        ).where(run.id == agent_run_id)
    ).first()


def _running(steps: Sequence[IngestionRunStep]) -> IngestionRunStep | None:
    return next((step for step in steps if step.status is StepStatus.running), None)


def advance(db: Session, run_id: uuid.UUID, now: datetime, settings: Settings) -> Moved:
    """Move the run as far as it can go without waiting, in the caller's transaction (its own:
    never the one that ends an agent run). The caller commits, then enqueues `Moved.enqueue`.
    Called twice, or from two connections at once, it has one effect: the second call waits for
    the row lock and finds the work done."""
    run = _lock(db, run_id)
    if run is None:
        return Moved(None)
    if run.status not in ACTIVE:
        _record_late(db, run)
        return Moved(run.status)
    if run.advance_failures:
        run.advance_failures = 0  # it moves again; committed only when this call succeeds
        run.advance_failed_at = None
    steps = _steps(db, run.id)
    current = _running(steps)
    if current is not None:
        agent = _agent(db, current.agent_run_id)
        if agent is not None and agent.status in (AgentStatus.queued, AgentStatus.running):
            stop = run.cancel_requested_at is not None or now >= run.deadline_at
            if not stop:
                # Still at work. A run that is still queued is offered to the queue again: the
                # job id is the run's, so a job that exists is not doubled.
                again = agent.id if agent.status is AgentStatus.queued else None
                return Moved(run.status, again)
            if agent.cancel_requested_at is not None:
                return Moved(run.status)
            outcome = agent_runs.request_cancel(db, run.user_id, agent.id, now)
            if outcome is not agent_runs.CancelOutcome.cancelled:
                return Moved(run.status)  # the runner stops it; the next call records its end
            agent = _agent(db, current.agent_run_id)
        _record(db, run, current, agent, now)
        steps = _steps(db, run.id)
    return _next(db, run, steps, now, settings)


def _record_late(db: Session, run: IngestionRun) -> bool:
    """A run closed with a step still at work (`advance_failed`) keeps that step `running`
    until its agent run has ended: its tokens are then added to the run, once, and the step is
    `cancelled`. Nothing else of a closed run changes. True when no step is left at work."""
    current = _running(_steps(db, run.id))
    if current is None:
        return True
    agent = _agent(db, current.agent_run_id)
    if agent is not None and agent.status in (AgentStatus.queued, AgentStatus.running):
        return False
    db.execute(
        sa.select(IngestionRunStep.id).where(IngestionRunStep.id == current.id).with_for_update()
    )
    if agent is not None:
        run.tokens_spent += int(agent.input_tokens or 0) + int(agent.output_tokens or 0)
    current.status = StepStatus.cancelled
    return True


def _record(
    db: Session,
    run: IngestionRun,
    step: IngestionRunStep,
    agent: sa.Row[Any] | None,
    now: datetime,
) -> None:
    """The end of a step's agent run, by "Failure rules". The step leaves `running` here, under
    its row lock, so the tokens of an agent run are added exactly once."""
    db.execute(
        sa.select(IngestionRunStep.id).where(IngestionRunStep.id == step.id).with_for_update()
    )
    status = agent.status if agent is not None else AgentStatus.failed
    error = agent.error if agent is not None else None
    spent = 0
    if agent is not None:
        spent = int(agent.input_tokens or 0) + int(agent.output_tokens or 0)
        run.tokens_spent += spent
        if step.purge_events:
            _delete_events(db, [agent.id])
    step.status = _after(run, step, status, error, spent, now)
    if step.status is StepStatus.failed and error == failures.BINDING_GONE:
        _plan_again(db, run, step.document_id)
    logger.info(
        "ingestion_step_ended",
        extra={
            "ingestion_run_id": str(run.id),
            "step_id": str(step.id),
            "agent_status": status.value,
            "step_status": step.status.value,
            "run_status": run.status.value,
            "tokens": spent,
        },
    )


def _after(
    run: IngestionRun,
    step: IngestionRunStep,
    status: AgentStatus,
    error: str | None,
    spent: int,
    now: datetime,
) -> StepStatus:
    """The status of a step whose agent run ended, pausing or cancelling the run where the
    rules say so."""
    if status is AgentStatus.done:
        return StepStatus.done
    if status is AgentStatus.cancelled:
        if step.purge_events:  # its document was deleted under it (G15): the chain goes on
            return StepStatus.failed
        if run.cancel_requested_at is None and now < run.deadline_at:
            # One step cancelled through the agents route is the run cancelled.
            run.cancel_requested_at = now
        return StepStatus.cancelled
    if error == failures.TOKEN_LIMIT and _on_the_remainder(step):
        # Cut by the ceiling: it keeps its items and runs again, above the largest budget that
        # was not enough, which is kept on the step whatever happens to its later attempts.
        step.cut_budget = max(step.cut_budget or 0, step.token_budget or 0)
        if not _past_deadline(run, now):  # a run out of time asks for no raise: `_next` closes it
            _pause(run, TOKEN_CEILING, now)
        return StepStatus.planned
    if failures.is_provider_failure(error) or (
        # The API never answered (a closed port): the CLI retried in silence up to the time
        # limit. Not the document's fault, and the next step would spend 15 minutes the same way.
        error is not None and error.startswith(_TIME_LIMIT) and spent == 0
    ):
        _pause(run, error or QUEUE_DOWN, now)
        return StepStatus.planned
    box = failures.is_box_failure(error)
    if box or error in REAPED:
        if step.attempt >= ATTEMPTS:
            return StepStatus.failed  # a second such failure is the step's own
        step.attempt = ATTEMPTS
        if box:
            _pause(run, error or failures.INTERNAL, now)
        return StepStatus.planned
    return StepStatus.failed  # the step's own content: the chain goes on


def _plan_step(step: IngestionRunStep) -> plan.Step:
    """The row as the estimate reads it."""
    extract = step.kind is plan.StepKind.extract
    positions = (int(step.part or 0),) if extract else tuple(step.image_positions or ())
    return plan.Step(
        kind=step.kind,
        document_id=step.document_id,
        identity=plan.Identity(
            bytes(step.sha256).hex(), step.kind.value, positions, plan.EXTRACTOR_VERSION, ""
        ),
        reused=False,
        part=int(step.part or 0),
        characters=max((step.range_end or 0) - (step.range_start or 0), 0),
        start=step.range_start,
        end=step.range_end,
        images=() if extract else positions,
    )


def _on_the_remainder(step: IngestionRunStep) -> bool:
    """Whether the budget that ended the step was what remained under the ceiling, and not the
    step's own backstop."""
    if step.token_budget is None:
        return False
    return step.token_budget < estimate.of_step(_plan_step(step)).backstop


def _floor(step: IngestionRunStep) -> int:
    """The smallest budget the step may start on, whatever ended its attempt before. Its low
    estimate when the ceiling never cut it. Once it did, `CUT_GROWTH` times the largest budget
    it was cut on (`cut_budget`, a stored fact that a restart of the worker or a provider
    failure does not erase), or its backstop when that is less. So the budgets the ceiling
    cuts a step on at least double each time and stop at the backstop, where a cut is the
    step's own failure and the chain goes on: a step is cut on its budget at most
    `1 + ceil(log2(backstop / first budget))` times, the cut at the backstop included, and
    those attempts spend under three backstops in all, whatever the number of Continues. An
    attempt that something else ended (one restart of the worker per step, a provider failure
    per Resume) adds what it spent, under one backstop each, and never lowers the floor."""
    one = estimate.of_step(_plan_step(step))
    if step.cut_budget is not None:
        return min(one.backstop, CUT_GROWTH * step.cut_budget)
    return math.ceil(one.low)


def continue_adds(
    db: Session, run: IngestionRun, steps: Sequence[IngestionRunStep], now: datetime
) -> int:
    """What one Continue adds to the ceiling now: one `H`, or what the next step needs to start
    on its floor when one `H` is not enough (a step the ceiling cut on more than half an `H`, or
    a part planned again from a longer text). The step is the one `next_step` names, the one
    `_next` starts after the click, so the figure shown, the figure added and the step started
    cannot disagree. Zero when nothing would run, a run out of time included: no raise is asked,
    and the run closes at its next `advance`. The page shows this figure before the click."""
    chosen = next_step(db, run, steps, now)
    if chosen.step is None:
        return 0
    return max(run.estimate_high, chosen.needs - (run.token_ceiling - run.tokens_spent), 1)


def _pause(run: IngestionRun, reason: str, now: datetime) -> None:
    """The run pauses now. `paused_at` is the start of this pause and of no other: the 24 hours
    and the move of the deadline are counted from it."""
    run.status = RunStatus.paused
    run.paused_at = now
    run.paused_reason = reason
    run.waiting = None


def _past_deadline(run: IngestionRun, now: datetime) -> bool:
    """Whether the run's time is over, as `_next` judges it: the one rule of the deadline. A
    pause stops the clock (`_restart` moves the deadline by the length of the pause), so a
    paused run is judged at the deadline it will have right after its Continue or Resume, and
    the time spent paused never counts: it is over only if it paused at or after its deadline.
    A run that paused before it can be continued however late the click comes."""
    deadline = run.deadline_at
    if run.status is RunStatus.paused:
        deadline += now - _paused_at(run)
    return now >= deadline


def _next(
    db: Session,
    run: IngestionRun,
    steps: list[IngestionRunStep],
    now: datetime,
    settings: Settings,
) -> Moved:
    """With no step running: cancel, wait, close, pause, or start the next step."""
    if run.cancel_requested_at is not None:
        _close(db, run, steps, RunStatus.cancelled, now)
        return Moved(run.status)
    if run.status is RunStatus.paused:
        if now - _paused_at(run) >= PAUSE_MAX:
            _merge(db, run, steps, now)
        elif run.paused_reason == TOKEN_CEILING:
            # A pause on the ceiling waits for a raise, and a raise pays for a step. When
            # nothing would run (the documents left to read were deleted during the pause)
            # there is nothing to pay for: the run closes here, on the ceiling it has.
            chosen = next_step(db, run, steps, now)
            if chosen.step is None:
                _settle(db, run, steps, chosen)
                _merge(db, run, steps, now)
        return Moved(run.status)
    if _past_deadline(run, now):
        _merge(db, run, steps, now)
        return Moved(run.status)
    chosen = next_step(db, run, steps, now)
    _settle(db, run, steps, chosen)
    step = chosen.step
    if step is None:
        _merge(db, run, _steps(db, run.id), now)
        return Moved(run.status)
    remaining = run.token_ceiling - run.tokens_spent
    budget = estimate.budget(_plan_step(step), remaining) if remaining > 0 else None
    if budget is None or budget < chosen.needs:
        # Under the step's low estimate, or not above a budget that already cut it: no agent
        # run, no token. Continue raises the ceiling by what this step needs (`continue_adds`,
        # which reads the same `next_step`).
        _pause(run, TOKEN_CEILING, now)
        return Moved(run.status)
    if agent_runs.count_active(db, run.user_id) >= agent_runs.ACTIVE_PER_USER:
        run.waiting = WAITING_SLOTS
        return Moved(run.status)
    data: dict[str, Any] = {
        "ingestion_run_id": str(run.id),
        "step_id": str(step.id),
        "token_budget": budget,
    }
    script = stream_name(settings, step)
    if script is not None:
        data["script"] = script
    agent = definitions.AGENTS[AGENT_OF[step.kind]]
    credential = credentials.get(db, run.user_id)
    model = plan.model_for(credential.kind if credential is not None else None)
    db.execute(
        sa.select(IngestionRunStep.id).where(IngestionRunStep.id == step.id).with_for_update()
    )
    try:
        created = agent_runs.create_run(
            db, run.user_id, agent.name, None, agent.validate_input(data), model, now
        )
    except agent_runs.ActiveRunsError:  # counted again under the user's advisory lock
        run.waiting = WAITING_SLOTS
        return Moved(run.status)
    step.status = StepStatus.running
    step.agent_run_id = created.id
    step.token_budget = budget
    step.attempt = max(step.attempt, 1)
    # A retry counts its refusals afresh (G9: the counters add up over the attempts).
    step.refused_count = 0
    step.two_digit_year_count = 0
    run.waiting = None
    return Moved(run.status, created.id)


def next_step(
    db: Session, run: IngestionRun, steps: Sequence[IngestionRunStep], now: datetime
) -> NextStep:
    """The step that would run next and the budget it needs: the one place that decides it.
    `continue_adds` (the figure the page shows and the click adds) and `_next` (the step that
    starts) both ask here, so they cannot name two steps.

    It is the first planned step that can run, once the steps before it that cannot are out of
    the way: a step whose agent no process knows, or whose document is gone or has no text,
    fails; a part whose text is not the one stored now is planned again from that text, its
    new parts placed after every other step, and a part of that document which already ran
    fails with the text it ran on.

    Read only: it reads the hash of the stored texts, and for a replaced text the text and the
    steps that finished in earlier runs. It changes no row and no object of the session: what
    it would write is listed in its answer, and `_settle` writes exactly that list, with no
    decision of its own. That is what makes the page's figure a prediction of `_next`.

    A run out of time (`_past_deadline`) has no next step: `_next` closes it, so no figure is
    shown and no raise is asked for a step that could not start."""
    if _past_deadline(run, now):
        return NextStep(None, 0)
    rows = list(steps)
    failed: list[IngestionRunStep] = []
    removed: list[IngestionRunStep] = []
    added: list[IngestionRunStep] = []
    out: set[int] = set()  # the rows of `failed`, by identity: a row of `added` has no id yet
    handled: set[uuid.UUID] = set()

    def answer(step: IngestionRunStep | None) -> NextStep:
        needs = _floor(step) if step is not None else 0
        return NextStep(step, needs, tuple(failed), tuple(removed), tuple(added))

    def planned(one: IngestionRunStep) -> bool:
        return one.status is StepStatus.planned and id(one) not in out

    while True:
        step = next((one for one in rows if planned(one)), None)
        if step is None:
            return answer(None)
        runnable = AGENT_OF[step.kind] in definitions.AGENTS
        if runnable and step.kind is not plan.StepKind.extract:
            return answer(step)
        now_stored = _stored_text_hash(db, run, step) if runnable else None
        planned_on = bytes(step.text_sha256) if step.text_sha256 is not None else None
        if now_stored is not None and now_stored == planned_on:
            return answer(step)
        if now_stored is None or step.document_id in handled:
            failed.append(step)  # no agent for it, or the document is gone or has no text
            out.add(id(step))
            continue
        handled.add(step.document_id)
        old = _never_ran([one for one in rows if planned(one)], step.document_id)
        gone = {id(one) for one in old}
        rows = [one for one in rows if id(one) not in gone]
        new = _planned_again(db, run, step.document_id, rows)
        removed += old
        added += new
        rows += new


def _never_ran(
    planned: Sequence[IngestionRunStep], document_id: uuid.UUID
) -> list[IngestionRunStep]:
    """Among planned steps, the parts of a document that no agent run ever read: the rows a new
    plan of its text replaces."""
    return [
        one
        for one in planned
        if one.document_id == document_id
        and one.kind is plan.StepKind.extract
        and one.agent_run_id is None
    ]


def _stored_text_hash(db: Session, run: IngestionRun, step: IngestionRunStep) -> bytes | None:
    """The hash of the text stored now for the step's document; None when the document is gone
    or has no text."""
    stored = db.execute(
        sa.select(stored_text_sha256()).where(
            IngestionDocument.id == step.document_id,
            IngestionDocument.user_id == run.user_id,
            IngestionDocument.sha256 == step.sha256,
        )
    ).first()
    return bytes(stored[0]) if stored is not None and stored[0] is not None else None


def _planned_again(
    db: Session, run: IngestionRun, document_id: uuid.UUID, kept: Sequence[IngestionRunStep]
) -> list[IngestionRunStep]:
    """The rows of a document's extraction planned from the text stored now (its transcription
    has finished, or its text was replaced), placed after `kept`, the steps the run keeps. Rows
    only: nothing is added to the session. The 60 step rule: when the new parts do not fit
    there are none, the document is left unread and the run ends partial."""
    document = db.execute(
        sa.select(IngestionDocument.sha256, IngestionDocument.text).where(
            IngestionDocument.id == document_id, IngestionDocument.user_id == run.user_id
        )
    ).first()
    if document is None or not document.text:
        return []
    text = document.text
    digest = text_sha256(text)
    credential = credentials.get(db, run.user_id)
    model = plan.model_for(credential.kind if credential is not None else None)
    described = plan.Document(
        document_id, bytes(document.sha256).hex(), len(text), (), plan.cut(text)
    )
    planned = plan.build(
        [described], model=model, finished=reuse.finished(db, run.user_id, [described], model)
    ).steps
    if len(kept) + len(planned) > plan.RUN_MAX_STEPS:
        return []
    position = max((one.position for one in kept), default=-1) + 1
    return [
        _row_of(run.id, position + offset, one, bytes(document.sha256), digest)
        for offset, one in enumerate(planned)
    ]


def _settle(
    db: Session, run: IngestionRun, steps: list[IngestionRunStep], chosen: NextStep
) -> None:
    """Write what `next_step` listed, and nothing else, under the run's row lock: the planned
    parts of a replaced text are removed (they never ran; a step that ran keeps its row), the
    parts of the text stored now are added, the steps that cannot run have failed. `steps` is
    read again when rows came or went."""
    _swap(db, chosen.removed, chosen.added)
    for one in chosen.failed:
        one.status = StepStatus.failed
    if chosen.removed or chosen.added:
        steps[:] = _steps(db, run.id)


def _swap(
    db: Session, removed: Sequence[IngestionRunStep], added: Sequence[IngestionRunStep]
) -> None:
    if removed:
        db.flush()
        for one in removed:
            db.delete(one)
        db.flush()  # before the new rows, which may take the positions of the removed ones
    db.add_all(added)


def _plan_again(db: Session, run: IngestionRun, document_id: uuid.UUID) -> None:
    """Plan a document's extraction from the text stored now, at once: the step that just ended
    found its text replaced. The same rows `next_step` would list for that document."""
    steps = _steps(db, run.id)
    old = _never_ran([one for one in steps if one.status is StepStatus.planned], document_id)
    gone = {id(one) for one in old}
    kept = [one for one in steps if id(one) not in gone]
    _swap(db, old, _planned_again(db, run, document_id, kept))
    db.flush()


def stream_name(settings: Settings, step: IngestionRunStep) -> str | None:
    """The recorded stream a step replays, only when the fake backend is on (never in
    production): `ingest_<kind>_<first 12 hex digits of the document's sha256>_p<part>` when
    that file exists, else `ingest_<kind>_default`, which reads and proposes nothing."""
    if not settings.agents_fake_backend or settings.app_env == "production":
        return None
    kind = step.kind.value
    part = int(step.part or 0) if step.kind is plan.StepKind.extract else 0
    name = f"ingest_{kind}_{bytes(step.sha256).hex()[:12]}_p{part}"
    if (STREAMS / f"{name}.json").is_file():
        return name
    return f"ingest_{kind}_default"


# --- the end of a run -------------------------------------------------------------------------


def _paused_at(run: IngestionRun) -> datetime:
    """When the current pause began: the fact `_pause` stored. A paused row without it was not
    written by this engine; its creation is the earliest it can have paused."""
    return run.paused_at or run.created_at


def _delete_events(db: Session, agent_run_ids: Sequence[uuid.UUID]) -> None:
    if agent_run_ids:
        db.execute(
            sa.delete(AgentEvent)
            .where(AgentEvent.run_id.in_(agent_run_ids))
            .execution_options(synchronize_session=False)
        )


def _merge(db: Session, run: IngestionRun, steps: list[IngestionRunStep], now: datetime) -> None:
    """Close the run with what finished. `done` when at least one step was read, with
    `partial` when something selected was not; `failed` when nothing was."""
    for step in steps:
        if step.status is StepStatus.planned:
            step.status = StepStatus.cancelled  # left unread
    read = {
        step.document_id for step in steps if step.status in (StepStatus.done, StepStatus.reused)
    }
    unread = [step for step in steps if step.status in (StepStatus.failed, StepStatus.cancelled)]
    partial = bool(unread) or any(one not in read for one in run.document_ids)
    _close(db, run, steps, RunStatus.done if read else RunStatus.failed, now)
    run.partial = partial
    if read:
        merge.run(db, run, now)


def _close(
    db: Session,
    run: IngestionRun,
    steps: list[IngestionRunStep],
    status: RunStatus,
    now: datetime,
) -> None:
    for step in steps:
        if step.status is StepStatus.planned:
            step.status = StepStatus.cancelled
    db.flush()
    # The items of a replaced text cite a text that is gone (G9, "A replaced text").
    db.execute(
        sa.delete(IngestionItem)
        .where(
            IngestionItem.step_id.in_(
                sa.select(IngestionRunStep.id).where(IngestionRunStep.ingestion_run_id == run.id)
            ),
            sa.not_(of_stored_text()),
        )
        .execution_options(synchronize_session=False)
    )
    _delete_events(
        db, [step.agent_run_id for step in steps if step.purge_events and step.agent_run_id]
    )
    run.status = status
    run.waiting = None
    run.paused_reason = None  # a closed run is not paused: the reason ends with the pause
    run.paused_at = None
    run.finished_at = now
    logger.info(
        "ingestion_run_closed",
        extra={
            "ingestion_run_id": str(run.id),
            "user_id": str(run.user_id),
            "status": status.value,
            "tokens_spent": run.tokens_spent,
        },
    )


# --- the user's actions -----------------------------------------------------------------------


def request_cancel(
    db: Session, user_id: uuid.UUID, run_id: uuid.UUID, now: datetime
) -> IngestionRun | None:
    """Under the run's row lock: the cancel is recorded and asked of the step's agent run. None
    for a run that is not this user's. The caller commits and calls `drive`, which closes the
    run when no step is running, and otherwise when the step has ended."""
    run = _lock(db, run_id, user_id)
    if run is None or run.status not in ACTIVE:
        return run
    if run.cancel_requested_at is None:
        run.cancel_requested_at = now
    current = _running(_steps(db, run.id))
    if current is not None and current.agent_run_id is not None:
        agent_runs.request_cancel(db, user_id, current.agent_run_id, now)
    return run


def _restart(run: IngestionRun, now: datetime) -> None:
    run.deadline_at = run.deadline_at + (now - _paused_at(run))  # a pause stops the clock
    run.paused_at = None
    run.status = RunStatus.running
    run.paused_reason = None
    run.waiting = None


def resume(
    db: Session, user_id: uuid.UUID, run_id: uuid.UUID, now: datetime
) -> IngestionRun | None:
    """A run paused for anything but its ceiling runs again, the failed step first. Raises
    `WrongStateError` otherwise. None for a run that is not this user's."""
    run = _lock(db, run_id, user_id)
    if run is None:
        return None
    if run.status is not RunStatus.paused or run.paused_reason == TOKEN_CEILING:
        raise WrongStateError
    _restart(run, now)
    return run


def continue_(
    db: Session, user_id: uuid.UUID, run_id: uuid.UUID, now: datetime, adds: int
) -> IngestionRun | None:
    """A run paused on its ceiling gets `adds` more, recorded in its history, and runs again.
    `adds` is the echo of what the page showed: it must equal `continue_adds` as computed here,
    under the run's row lock (one `H`, or what the step that would run needs to start on its
    floor). Any other figure, lower or higher, raises `ContinueChangedError` with the figure of
    now and writes nothing: a stale page never raises the ceiling by an amount its user did not
    see. When nothing would run (`continue_adds` is zero: the documents left were deleted, or
    the run is out of time) every click is such a stale one: it raises `ContinueChangedError(0)`,
    and the route closes the run. Raises
    `WrongStateError` for any other state. None for a run that is not this user's."""
    run = _lock(db, run_id, user_id)
    if run is None:
        return None
    if run.status is not RunStatus.paused or run.paused_reason != TOKEN_CEILING:
        raise WrongStateError
    current = continue_adds(db, run, _steps(db, run.id), now)
    if current == 0 or adds != current:  # a figure now zero: no click is answered as accepted
        raise ContinueChangedError(current)
    raised = run.token_ceiling + adds
    _restart(run, now)
    run.ceiling_history = [
        *run.ceiling_history,
        {
            "at": now.isoformat(),
            "spent": run.tokens_spent,
            "from": run.token_ceiling,
            "to": raised,
        },
    ]
    run.token_ceiling = raised
    return run


def queue_failed(db: Session, run_id: uuid.UUID, agent_run_id: uuid.UUID, now: datetime) -> None:
    """Redis refused the step's job: the agent run is failed as the agents route does, the step
    goes back to `planned` and the run pauses. The caller commits."""
    run = _lock(db, run_id)
    if run is None or run.status not in ACTIVE:
        return
    current = _running(_steps(db, run.id))
    if current is None or current.agent_run_id != agent_run_id:
        return
    if not agent_runs.fail_queued(db, agent_run_id, agent_runs.QUEUE_DOWN, now):
        return  # it was taken after all, or ended: `advance` reads what became of it
    current.status = StepStatus.planned
    _pause(run, QUEUE_DOWN, now)


# --- callers: the routes, the hook and the cron ------------------------------------------------


def advance_failed(db: Session, run_id: uuid.UUID, now: datetime) -> bool:
    """`advance` raised for this run at the cron. Counted in the run's row, in a transaction of
    its own (the caller's); at `ADVANCE_FAILURES` in a row the run is closed `failed` with
    `STUCK`, whatever it had read, without a merge (the merge may be what raises): the user can
    start a new run, which reuses the steps that ended `done`. True when it closed the run.
    The close uses as little as it can: no item is read, no event deleted."""
    run = _lock(db, run_id)
    if run is None or run.status not in ACTIVE:
        return False
    last = run.advance_failed_at
    if last is not None and now - last < ADVANCE_FAILURE_GAP:
        return False  # the same stall, seen again by a tick that had piled up behind it
    run.advance_failures += 1
    run.advance_failed_at = now
    if run.advance_failures < ADVANCE_FAILURES:
        return False
    current = _running(_steps(db, run.id))
    if current is not None and current.agent_run_id is not None:
        try:
            with db.begin_nested():  # an agent run still at work is asked to stop
                agent_runs.request_cancel(db, run.user_id, current.agent_run_id, now)
        except Exception as error:
            logger.warning(
                "ingestion_stuck_cancel_failed",
                extra={"ingestion_run_id": str(run.id), "error_type": type(error).__name__},
            )
    db.execute(
        sa.update(IngestionRunStep)
        .where(
            IngestionRunStep.ingestion_run_id == run.id,
            IngestionRunStep.status == StepStatus.planned,
        )
        .values(status=StepStatus.cancelled)
        .execution_options(synchronize_session=False)
    )
    # The step that was running: the tokens of its agent run are the user's money and are
    # recorded here when it has ended. One still at work stays `running` on the closed run
    # until it ends, and `advance` (the hook, or the cron through `_due`) records it then.
    try:
        with db.begin_nested():  # this exit is for the days when reads fail: it closes anyway
            _record_late(db, run)
    except Exception as error:
        logger.warning(
            "ingestion_stuck_record_failed",
            extra={"ingestion_run_id": str(run.id), "error_type": type(error).__name__},
        )
    run.status = RunStatus.failed
    run.partial = True
    run.waiting = None
    run.paused_reason = None
    run.paused_at = None
    run.closed_reason = STUCK
    run.finished_at = now
    logger.error(
        "ingestion_run_closed_stuck",
        extra={
            "ingestion_run_id": str(run.id),
            "user_id": str(run.user_id),
            "failures": run.advance_failures,
        },
    )
    return True


def _advance_failed(sessions: sessionmaker[Session], run_id: uuid.UUID, now: datetime) -> bool:
    with sessions() as db, db.begin():
        return advance_failed(db, run_id, now)


def _advance(
    sessions: sessionmaker[Session], run_id: uuid.UUID, now: datetime, settings: Settings
) -> Moved:
    with sessions() as db, db.begin():
        return advance(db, run_id, now, settings)


def _queue_failed(
    sessions: sessionmaker[Session], run_id: uuid.UUID, agent_run_id: uuid.UUID, now: datetime
) -> None:
    with sessions() as db, db.begin():
        queue_failed(db, run_id, agent_run_id, now)


async def drive(
    sessions: sessionmaker[Session],
    enqueue: Enqueue | None,
    run_id: uuid.UUID,
    clock: Callable[[], datetime],
    settings: Settings,
) -> RunStatus | None:
    """`advance` in a transaction of its own, in a thread, then the enqueue it asked for. Without
    an `enqueue` the agent run stays queued and the cron offers it to the queue."""
    moved = await asyncio.to_thread(_advance, sessions, run_id, clock(), settings)
    if moved.enqueue is None or enqueue is None:
        return moved.status
    try:
        await enqueue(moved.enqueue)
    except queues.QueueUnavailableError:
        logger.warning("ingestion_enqueue_failed", extra={"ingestion_run_id": str(run_id)})
        await asyncio.to_thread(_queue_failed, sessions, run_id, moved.enqueue, clock())
        return RunStatus.paused
    return moved.status


def queue_enqueue(queue: Any) -> Enqueue:
    """`Enqueue` over an `app.queue.Queue` (the api)."""

    async def enqueue(agent_run_id: uuid.UUID) -> Any:
        return await queue.enqueue(
            "run_agent",
            str(agent_run_id),
            queue=queues.AGENTS,
            job_id=queues.agent_run_job_id(agent_run_id),
        )

    return enqueue


def pool_enqueue(pool: Any) -> Enqueue:
    """`Enqueue` over the arq pool of a worker's job context."""

    async def enqueue(agent_run_id: uuid.UUID) -> Any:
        try:
            return await pool.enqueue_job(
                "run_agent",
                str(agent_run_id),
                _job_id=queues.agent_run_job_id(agent_run_id),
                _queue_name=queues.AGENTS,
            )
        except (TimeoutError, OSError, ConnectionError) as exc:
            raise queues.QueueUnavailableError(type(exc).__name__) from None
        except Exception as exc:
            if type(exc).__module__.startswith("redis"):
                raise queues.QueueUnavailableError(type(exc).__name__) from None
            raise

    return enqueue


def _run_of(sessions: sessionmaker[Session], agent_run_id: uuid.UUID) -> uuid.UUID | None:
    with sessions() as db:
        return db.scalar(
            sa.select(IngestionRunStep.ingestion_run_id).where(
                IngestionRunStep.agent_run_id == agent_run_id
            )
        )


async def on_final(final: definitions.FinalRun) -> None:
    """The `on_final` hook of both agents: the step's agent run has ended, move its chain. Short,
    with its database work in threads; when it is not called, the cron does the same within a
    minute."""
    run_id = await asyncio.to_thread(_run_of, final.sessions, final.run_id)
    if run_id is None:
        return
    await drive(
        final.sessions, final.services.get(ENQUEUE_SERVICE), run_id, final.clock, final.settings
    )


def _due(sessions: sessionmaker[Session]) -> list[uuid.UUID]:
    with sessions() as db:
        return list(
            db.scalars(
                sa.select(IngestionRun.id)
                .where(
                    sa.or_(
                        IngestionRun.status.in_(ACTIVE),
                        # closed by `advance_failed` with a step whose tokens are still due
                        sa.exists().where(
                            IngestionRunStep.ingestion_run_id == IngestionRun.id,
                            IngestionRunStep.status == StepStatus.running,
                        ),
                    )
                )
                .order_by(IngestionRun.created_at)
            )
        )


def purge(db: Session, now: datetime) -> int:
    """Delete, again, the events of the steps whose document was deleted under them, until five
    minutes after the end of their agent run (a write of the runner may land after the delete).
    The caller commits."""
    late = (
        sa.select(AgentRun.id)
        .join(IngestionRunStep, IngestionRunStep.agent_run_id == AgentRun.id)
        .where(
            IngestionRunStep.purge_events.is_(True),
            sa.or_(AgentRun.finished_at.is_(None), AgentRun.finished_at > now - PURGE_FOR),
        )
    )
    result = db.execute(
        sa.delete(AgentEvent)
        .where(AgentEvent.run_id.in_(late))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def _purge(sessions: sessionmaker[Session], now: datetime) -> int:
    with sessions() as db, db.begin():
        return purge(db, now)


def _log_failure(event: str, run_id: uuid.UUID, error: Exception) -> None:
    logger.error(event, extra={"ingestion_run_id": str(run_id), "error_type": type(error).__name__})


async def tick(
    sessions: sessionmaker[Session],
    enqueue: Enqueue | None,
    clock: Callable[[], datetime],
    settings: Settings,
) -> int:
    """The cron, every minute: every run that is not final is advanced, one after the other. A
    run that fails here does not stop the others; its failure is counted (`advance_failed`) and
    logged by its type only, since the message of an error may quote what it was handling."""
    moved = 0
    for run_id in await asyncio.to_thread(_due, sessions):
        try:
            await drive(sessions, enqueue, run_id, clock, settings)
            moved += 1
        except Exception as error:
            _log_failure("ingestion_advance_failed", run_id, error)
            try:
                await asyncio.to_thread(_advance_failed, sessions, run_id, clock())
            except Exception as again:
                _log_failure("ingestion_advance_count_failed", run_id, again)
    await asyncio.to_thread(_purge, sessions, clock())
    return moved


agents.register_final_hook(on_final)
