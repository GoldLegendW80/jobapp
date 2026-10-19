"""The automatic generation of a found posting's CV and letter (task D10; SPEC-documents.md,
Flow step 3, "Automatic generation", Decisions 14, 15 and 17).

`run(ctx)` is an arq cron job of the documents queue, every minute (app/worker.py registers it
once this file exists). It holds ids only and returns a count. `python -m app.documents.tick
<email>` runs one pass for one account, so a journey never waits a minute.

**One pass, per person** (`person`), in one transaction under the person's tick lock (a
transaction level advisory lock taken with `try`: a second worker, or a second pass, finds it
held and leaves the person to the next tick, so two passes never start the same thing twice):

0. The running generations whose run ended without its final hook (a worker killed under it, a
   run the reaper failed, one lost by a queue that was down, one cancelled while queued) and
   finished over `HOOK_GRACE_S` ago are settled as the hook would have (`recover`): the
   runner's contract is that whoever chains runs also looks on a timer, and the tick is it.
1. A stored search section that no longer validates: nothing automatic for this person (fail
   closed), and the reason is logged as a code.
2. The base CV generations whose reason to wait is gone start (`writer_base.start_ready`), and
   the person's clicks ("Write now") that met their limit of active runs start before anything
   automatic.
3. `auto` off: every automatic generation that has not started is cancelled; nothing else.
4. No base CV and a profile with an employer: the first base CV is built from the profile
   (`writer_base.build_from_profile`, source `profile`, trigger `auto`), started when it may.
   No employer: nothing is created (the CV tab says what to fill), and steps 6 to 8 still run.
   With `auto` off, an automatic base CV build that waits does not start either.
5. The eligible postings (`eligibility.refusal`) are read in the order of `eligibility.order_key`.
   Each gets a CV generation, or, once its CV has a version, a letter generation, unless one is
   open, the person stopped one, or it failed already twice (`eligibility.next_try`, which also
   raises the ceiling after a stop at the ceiling). Each is created with the estimate of D9's
   `estimate.for_posting` and its ceiling, naming the default base CV; a letter gets what the
   pair's ceiling has left after its CV's run (`generate.letter_ceiling`), none when that run
   has no count (created `failed` at its limit; the one retry after a stop at the ceiling then
   applies). A posting whose CV
   moved it to `cv_ready` is still read for its letter (`eligibility.LETTER_STATUSES`): a letter
   the chain could not start (the person's runs all busy, the hook cut short) is work here.
6. Every automatic generation that has not started is cancelled when its posting is no longer
   eligible (it left `found` and `shortlisted`, or `cv_ready` for a letter, its fit fell under
   the threshold, it fell out of the window, a never apply rule now matches it).
7. The others get their state: `waiting` with `consent` or `credential` (nothing spends without
   both), `base_cv` while the default base CV is being built, `daily_ceiling` for the CVs past
   today's room (`daily_ceiling` minus the postings whose CV started today in the person's time
   zone), else `queued`. A letter never waits for the ceiling: its posting counted with its CV.
8. While fewer than `DOCUMENTS_AUTO_AT_ONCE` automatic generations of the person run, the first
   queued one starts: a letter whose CV is written first, then the CVs in posting order. A CV
   that starts records `ceiling_day`, the day it counts against.

**Never two starts.** A click ("Write now") cancels an automatic generation that has not
started, under its document's lock. So the pass writes nothing to a generation, and starts none,
before it holds the document's lock and has read the generation again (`_claim`): one the click
cancelled meanwhile is left alone. The posting's lock (shared) comes first, as for the click,
the chain and a move to `applied`, and its status is read under it: an automatic generation
whose posting moved past the statuses of its kind since step 5 read them is cancelled there,
never started (D14). One person whose pass fails for any other reason is rolled back and
logged; the pass goes on with the next person.

The runs are queued on the agents queue after the commit. A queue that is down leaves the run
`queued`, as the routes do. Logs carry ids and counts, never a text.

**Latency.** The tick runs on the documents queue, one job at a time, behind a render: a
render ends within render_job.JOB_TIMEOUT_S, so the tick waits at most that long behind one, and
its own work is a handful of queries per person (no model call, no render).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import failures
from app.agents import runs as agent_runs
from app.agents.models import FINAL_STATUSES, AgentRun, RunStatus
from app.config import Settings, get_settings
from app.documents import eligibility, estimate, generate, render_job, store, writer_base
from app.documents.models import (
    BaseSource,
    Document,
    DocumentGeneration,
    DocumentVersion,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
    Scope,
)
from app.jobs import sweep
from app.jobs.models import FileOrigin, Job, JobFile, JobStatus
from app.profile.models import Profile
from app.profile.schemas import DocumentsGeneration, SearchSection

logger = logging.getLogger("app.documents.tick")

LOCK_PREFIX = "documents.tick:"
NOT_STARTED = (GenerationStatus.waiting, GenerationStatus.queued)
# The modes the tick starts and counts. A run of theirs that ended without its final hook is
# settled once it finished this long ago: over runner.FINAL_HOOK_TIMEOUT_S (30 s), so a hook that
# is merely late still settles its own result.
COUNTED_MODES = (GenerationMode.posting, GenerationMode.base_profile)
HOOK_GRACE_S = 120


def setting_of(search: SearchSection | None) -> eligibility.Setting:
    block = search.documents if search is not None else DocumentsGeneration()
    return eligibility.Setting(block.auto, block.min_fit, block.daily_ceiling)


def try_lock(db: Session, user_id: uuid.UUID) -> bool:
    """The person's tick lock, held until the transaction ends; False when another holds it."""
    key = sa.func.hashtextextended(LOCK_PREFIX + str(user_id), 0)
    return bool(db.scalar(sa.select(sa.func.pg_try_advisory_xact_lock(key))))


# --- what the pass reads ------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    id: uuid.UUID
    fit: int | None
    found_on: date
    status: JobStatus

    def key(self) -> tuple[int, int, str]:
        return eligibility.order_key(self.fit, self.found_on, str(self.id))


def eligible(
    db: Session,
    user_id: uuid.UUID,
    setting: eligibility.Setting,
    search: SearchSection | None,
    last_day: date,
    lookback_days: int,
    statuses: frozenset[JobStatus] = eligibility.OPEN_STATUSES,
) -> list[Candidate]:
    """The person's eligible postings, in order. The query narrows by status, fit and window;
    `eligibility.refusal` decides on every row it returns."""
    if not setting.auto:
        return []
    legacy = (
        sa.select(JobFile.id)
        .where(JobFile.job_id == Job.id, JobFile.origin == FileOrigin.legacy)
        .exists()
    )
    rows = db.execute(
        sa.select(
            Job.id, Job.status, Job.fit, Job.found_on, Job.company, Job.title, legacy.label("lg")
        ).where(
            Job.user_id == user_id,
            Job.status.in_(statuses),
            Job.fit.is_not(None),
            Job.fit >= setting.min_fit,
            Job.found_on >= eligibility.since(last_day, lookback_days),
        )
    ).all()
    rules = sweep.never_apply_rules(search) if search is not None else []
    found = [
        Candidate(row.id, row.fit, row.found_on, row.status)
        for row in rows
        if eligibility.refusal(
            eligibility.Posting(row.status, row.fit, row.found_on, row.company, row.title, row.lg),
            setting,
            rules,
            last_day,
            lookback_days,
            statuses,
        )
        is None
    ]
    return sorted(found, key=Candidate.key)


@dataclass
class Docs:
    """The posting documents of a person, by (posting, kind)."""

    ids: dict[tuple[uuid.UUID, Kind], uuid.UUID] = field(default_factory=dict)
    written: set[uuid.UUID] = field(default_factory=set)  # documents with a version
    open: set[uuid.UUID] = field(default_factory=set)  # documents with an open generation
    stopped: set[uuid.UUID] = field(default_factory=set)  # a writing the person stopped
    done: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)  # the last done generation
    failures: dict[uuid.UUID, list[eligibility.Failure]] = field(
        default_factory=lambda: defaultdict(list)
    )


def posting_docs(db: Session, user_id: uuid.UUID) -> Docs:
    docs = Docs()
    rows = db.execute(
        sa.select(
            Document.id,
            Document.posting_id,
            Document.kind,
            sa.select(DocumentVersion.id)
            .where(DocumentVersion.document_id == Document.id)
            .exists()
            .label("written"),
        ).where(Document.user_id == user_id, Document.scope == Scope.posting)
    ).all()
    for row in rows:
        if row.posting_id is None:
            continue
        docs.ids[(row.posting_id, row.kind)] = row.id
        if row.written:
            docs.written.add(row.id)
    generations = db.execute(
        sa.select(
            DocumentGeneration.id,
            DocumentGeneration.document_id,
            DocumentGeneration.status,
            DocumentGeneration.started_at,
            DocumentGeneration.finished_at,
            DocumentGeneration.error,
            DocumentGeneration.token_ceiling,
            DocumentGeneration.created_at,
        )
        .join(Document, Document.id == DocumentGeneration.document_id)
        .where(
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.mode == GenerationMode.posting,
            Document.scope == Scope.posting,
        )
        .order_by(DocumentGeneration.created_at, DocumentGeneration.id)
    ).all()
    for g in generations:
        if g.status in (*NOT_STARTED, GenerationStatus.running):
            docs.open.add(g.document_id)
        elif g.status is GenerationStatus.done:
            docs.written.add(g.document_id)  # a done generation always made a version
            docs.done[g.document_id] = g.id
        elif g.status is GenerationStatus.cancelled and g.started_at is not None:
            docs.stopped.add(g.document_id)
        elif g.status is GenerationStatus.failed:
            docs.failures[g.document_id].append(
                eligibility.Failure(g.error, g.finished_at or g.created_at, g.token_ceiling)
            )
    return docs


def started_today(db: Session, user_id: uuid.UUID, today: date) -> int:
    """The postings whose automatic CV started on `today` (each counts once)."""
    return int(
        db.scalar(
            sa.select(sa.func.count(sa.distinct(DocumentGeneration.document_id)))
            .join(Document, Document.id == DocumentGeneration.document_id)
            .where(
                DocumentGeneration.user_id == user_id,
                DocumentGeneration.ceiling_day == today,
                DocumentGeneration.trigger == GenerationTrigger.auto,
                DocumentGeneration.mode == GenerationMode.posting,
                Document.kind == Kind.cv,
            )
        )
        or 0
    )


# --- one pass for one person --------------------------------------------------------------------


@dataclass
class Outcome:
    runs: list[uuid.UUID] = field(default_factory=list)
    created: int = 0
    cancelled: int = 0
    recovered: int = 0


def recover(db: Session, user_id: uuid.UUID, at: datetime) -> int:
    """Step 0 of the module docstring: each running generation whose run is final and finished
    over HOOK_GRACE_S ago gets the run's outcome, as `generate.settle` would have given it:
    `failed` with `token_limit` or `run_failed` (the bounded retry then applies), `cancelled`
    for a run cancelled while queued (a stop, never retried). Returns how many."""
    ended = sa.func.coalesce(AgentRun.finished_at, AgentRun.created_at)
    rows = db.execute(
        sa.select(DocumentGeneration.id, DocumentGeneration.document_id)
        .join(
            AgentRun,
            sa.and_(
                AgentRun.id == DocumentGeneration.run_id,
                AgentRun.user_id == DocumentGeneration.user_id,
            ),
        )
        .where(
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.status == GenerationStatus.running,
            DocumentGeneration.mode.in_(COUNTED_MODES),
            AgentRun.status.in_(FINAL_STATUSES),
            ended < at - timedelta(seconds=HOOK_GRACE_S),
        )
        .order_by(DocumentGeneration.id)
    ).all()
    settled = 0
    for row in rows:
        _lock(db, user_id, row.document_id)  # held for the pass, so the posting comes first
        generation = db.execute(
            sa.select(DocumentGeneration)
            .where(DocumentGeneration.id == row.id)
            .execution_options(populate_existing=True)
        ).scalar_one()
        run = (
            db.get(AgentRun, generation.run_id, populate_existing=True)
            if generation.run_id is not None
            else None
        )
        if (
            generation.status is not GenerationStatus.running
            or run is None
            or run.status not in FINAL_STATUSES
        ):
            continue  # the hook settled it meanwhile
        if run.status is RunStatus.cancelled:
            generation.status = GenerationStatus.cancelled
        else:
            generation.status = GenerationStatus.failed
            generation.error = "token_limit" if run.error == failures.TOKEN_LIMIT else "run_failed"
        generation.finished_at = run.finished_at or run.created_at
        settled += 1
        logger.info(
            "documents.tick.recovered",
            extra={"generation_id": str(generation.id), "code": generation.error or "cancelled"},
        )
    return settled


def _lock(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> JobStatus | None:
    """A posting document's posting (shared), then the document: the order of store.py, the one
    of a move to `applied` (the freeze locks the documents under the posting) and of a run's row,
    which references the posting. Returns the posting's status, read under its lock."""
    posting_id = db.scalar(
        sa.select(Document.posting_id).where(
            Document.user_id == user_id, Document.id == document_id
        )
    )
    status = store.lock_posting(db, user_id, posting_id) if posting_id is not None else None
    store.lock_document(db, user_id, document_id)
    return status


def _claim(db: Session, generation: DocumentGeneration, *, at: datetime | None = None) -> bool:
    """Lock the generation's posting and document (`_lock`; the document is the lock a click
    takes), then read the generation again: True while it has still not started. A click may
    have cancelled it, and started its own, since the pass read it: the pass then writes nothing
    to it. Given `at`, an automatic one whose posting left the statuses of its kind since the
    pass read them (moved to `applied` meanwhile) is cancelled at `at`, and False (D14)."""
    db.flush()  # what the pass already wrote to claimed rows, before the re-read replaces it
    status = _lock(db, generation.user_id, generation.document_id)
    fresh = db.execute(
        sa.select(DocumentGeneration)
        .where(DocumentGeneration.id == generation.id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    if fresh.status not in NOT_STARTED:
        return False
    if at is None or fresh.trigger is not GenerationTrigger.auto:
        return True
    kind = db.scalar(sa.select(Document.kind).where(Document.id == fresh.document_id))
    allowed = eligibility.LETTER_STATUSES if kind == Kind.letter else eligibility.OPEN_STATUSES
    if status in allowed:
        return True
    _cancel(fresh, at)
    return False


def _create(
    db: Session,
    user_id: uuid.UUID,
    posting_id: uuid.UUID,
    kind: Kind,
    docs: Docs,
    at: datetime,
    settings: Settings,
) -> bool:
    """One automatic generation of a posting's document, `queued` for now (step 7 sets its
    state). False when the document gets none now."""
    document_id = docs.ids.get((posting_id, kind))
    if document_id is not None and (
        document_id in docs.written or document_id in docs.open or document_id in docs.stopped
    ):
        return False
    failures = docs.failures.get(document_id, []) if document_id is not None else []
    if len(failures) > eligibility.RETRIES:
        return False
    # A letter is written from the base CV of the posting's CV (generate.start_letter does
    # the same); a CV from its document's base, else the default (generate.create).
    base_id = None
    cv_done: DocumentGeneration | None = None
    if kind == Kind.letter:
        cv_id = docs.ids[(posting_id, Kind.cv)]
        cv = store.get(db, user_id, cv_id)
        base_id = cv.base_document_id if cv is not None else None
        cv_done = db.get(DocumentGeneration, docs.done[cv_id]) if cv_id in docs.done else None
    figures = estimate.for_posting(db, user_id, posting_id, base_id)
    # None: the CV's run has no count, so the letter gets no budget (generate.no_budget).
    left = figures.ceiling if cv_done is None else generate.letter_ceiling(db, cv_done)
    chosen = eligibility.next_try(
        failures,
        stopped=False,
        now=at,
        retry_after_s=settings.documents_auto_retry_after_s,
        ceiling=left or 0,
        high=figures.high,
    )
    if chosen is None:
        return False
    store.lock_posting(db, user_id, posting_id)  # before the document's row and lock (store.py)
    document = store.get_or_create(db, user_id, posting_id, kind, at=at)
    try:
        made = generate.create(
            db,
            document,
            GenerationMode.posting,
            GenerationTrigger.auto,
            base_id,
            estimate.as_json(figures),
            chosen.token_ceiling,
            at=at,
        )
    except (store.GenerationOpenError, store.NotFoundError):
        return False  # opened by a click since the read, or its base CV deleted meanwhile
    if left is None and chosen.token_ceiling == 0:
        generate.no_budget(db, made, at)
    docs.ids[(posting_id, kind)] = document.id
    docs.open.add(document.id)
    return True


@dataclass(frozen=True)
class Waiting:
    """An automatic posting generation that has not started, with what orders it."""

    generation: DocumentGeneration
    kind: Kind
    posting_id: uuid.UUID
    fit: int | None
    found_on: date

    def key(self) -> tuple[int, tuple[int, int, str]]:
        # A letter first (its CV is written: finish the pair), then the postings' order.
        first = 0 if self.kind == Kind.letter else 1
        return (first, eligibility.order_key(self.fit, self.found_on, str(self.posting_id)))


def not_started(db: Session, user_id: uuid.UUID) -> list[Waiting]:
    rows = db.execute(
        sa.select(DocumentGeneration, Document.kind, Job.id, Job.fit, Job.found_on)
        .join(Document, Document.id == DocumentGeneration.document_id)
        .join(Job, Job.id == Document.posting_id)
        .where(
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.trigger == GenerationTrigger.auto,
            DocumentGeneration.mode == GenerationMode.posting,
            DocumentGeneration.status.in_(NOT_STARTED),
            Document.scope == Scope.posting,
        )
        .execution_options(populate_existing=True)
    ).all()
    found = [Waiting(row[0], row[1], row[2], row[3], row[4]) for row in rows]
    return sorted(found, key=Waiting.key)


def _cancel(generation: DocumentGeneration, at: datetime) -> None:
    generation.status = GenerationStatus.cancelled
    generation.wait_reason = None
    generation.finished_at = at


def _name_base(db: Session, item: Waiting, default: Document) -> None:
    """A CV that waited for the first base CV is written from the default one, and takes its
    template while it has no version."""
    document = store.lock_document(db, item.generation.user_id, item.generation.document_id)
    if document.base_document_id is None:
        document.base_document_id = default.id
        document.template_slug = default.template_slug
    item.generation.base_document_id = document.base_document_id


def start_clicks(
    db: Session, user_id: uuid.UUID, at: datetime, settings: Settings
) -> list[uuid.UUID]:
    """The person's clicks ("Write now") that have not started, oldest first: started when the
    consent and a credential are there (a click is never held by the threshold or the ceiling),
    else kept waiting with the reason. One that meets the limit of active runs stays `queued`
    for the next tick. Returns the runs to queue after the commit."""
    found = db.scalars(
        sa.select(DocumentGeneration)
        .join(Document, Document.id == DocumentGeneration.document_id)
        .where(
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.trigger == GenerationTrigger.click,
            DocumentGeneration.mode == GenerationMode.posting,
            DocumentGeneration.status.in_(NOT_STARTED),
            Document.scope == Scope.posting,
        )
        .order_by(DocumentGeneration.created_at, DocumentGeneration.id)
        .execution_options(populate_existing=True)
    ).all()
    reason = writer_base.wait_reason(db, user_id) if found else None
    runs: list[uuid.UUID] = []
    for generation in found:
        if reason is not None:
            unchanged = generation.status is GenerationStatus.waiting
            if (unchanged and generation.wait_reason is reason) or not _claim(db, generation):
                continue
            generation.status = GenerationStatus.waiting
            generation.wait_reason = reason
            continue
        if not _claim(db, generation):
            continue
        try:
            runs.append(generate.start(db, generation, at=at, settings=settings).id)
        except agent_runs.ActiveRunsError:
            generation.status = GenerationStatus.queued
            generation.wait_reason = None
            break
        except ValueError:
            continue
    return runs


def _running_auto(db: Session, user_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            sa.select(sa.func.count())
            .select_from(DocumentGeneration)
            .where(
                DocumentGeneration.user_id == user_id,
                DocumentGeneration.trigger == GenerationTrigger.auto,
                DocumentGeneration.status == GenerationStatus.running,
            )
        )
        or 0
    )


def person(db: Session, user_id: uuid.UUID, *, at: datetime, settings: Settings) -> Outcome | None:
    """One pass for one person (module docstring). The caller commits, then queues the runs.
    None when another pass holds the person."""
    if not try_lock(db, user_id):
        return None
    out = Outcome()
    out.recovered = recover(db, user_id, at)
    try:
        search = sweep.load_search(db, user_id)
    except sweep.InvalidSearchError:
        logger.warning("documents.tick.search_invalid", extra={"user_id": str(user_id)})
        return out
    setting = setting_of(search)
    timezone = search.sweep_schedule.timezone if search is not None else None
    today = eligibility.ceiling_day(at, timezone)
    auto = setting.auto
    out.runs += writer_base.start_ready(db, user_id, at=at, settings=settings, auto=auto)
    out.runs += start_clicks(db, user_id, at, settings)
    create = auto
    if auto and not store.base_cvs(db, user_id):
        if writer_base.view_of(db, user_id, at).employers:
            writer_base.build_from_profile(
                db,
                user_id,
                at=at,
                source=BaseSource.profile,
                trigger=GenerationTrigger.auto,
            )
            out.created += 1
            out.runs += writer_base.start_ready(db, user_id, at=at, settings=settings)
        else:
            create = False  # nothing to write from: nothing created, the rest still settled
    postings = eligible(
        db,
        user_id,
        setting,
        search,
        eligibility.found_day(at),
        settings.documents_auto_lookback_days,
        eligibility.LETTER_STATUSES,
    )
    if create:
        docs = posting_docs(db, user_id)
        for posting in postings:
            cv = docs.ids.get((posting.id, Kind.cv))
            if cv is not None and cv in docs.written:
                kind = Kind.letter
            elif posting.status in eligibility.OPEN_STATUSES:
                kind = Kind.cv
            else:
                continue  # `cv_ready` without a written CV: nothing of the pair is owed
            if _create(db, user_id, posting.id, kind, docs, at, settings):
                out.created += 1
    keep = {
        Kind.letter: {posting.id for posting in postings},
        Kind.cv: {p.id for p in postings if p.status in eligibility.OPEN_STATUSES},
    }
    waiting = []
    for item in not_started(db, user_id):
        if item.posting_id in keep[item.kind]:
            waiting.append(item)
        elif _claim(db, item.generation):
            _cancel(item.generation, at)
            out.cancelled += 1
    if waiting:
        out.runs += _settle_and_start(db, user_id, waiting, setting, today, at, settings)
    db.flush()
    return out


def _settle_and_start(
    db: Session,
    user_id: uuid.UUID,
    waiting: Sequence[Waiting],
    setting: eligibility.Setting,
    today: date,
    at: datetime,
    settings: Settings,
) -> list[uuid.UUID]:
    """Steps 7 and 8 of the module docstring."""
    reason = writer_base.wait_reason(db, user_id)
    default = store.default_base(db, user_id)
    if reason is None and (
        default is None or store.open_generation(db, user_id, default.id) is not None
    ):
        reason = GenerationWait.base_cv
    room = eligibility.room(setting.daily_ceiling, started_today(db, user_id, today))
    gone: set[uuid.UUID] = set()  # cancelled by a click since the pass read them
    for item in waiting:
        state = reason
        took_room = False
        if state is None and item.kind == Kind.cv:
            if room > 0:
                room -= 1
                took_room = True
            else:
                state = GenerationWait.daily_ceiling
        generation = item.generation
        status = GenerationStatus.waiting if state else GenerationStatus.queued
        to_name = state is None and item.kind == Kind.cv and default is not None
        if (
            generation.status is status
            and generation.wait_reason is state
            and not (to_name and generation.base_document_id is None)
        ):
            continue  # nothing to write
        if not _claim(db, generation, at=at):
            gone.add(generation.id)
            room += 1 if took_room else 0
            continue
        generation.status = status
        generation.wait_reason = state
        if to_name and default is not None and generation.base_document_id is None:
            _name_base(db, item, default)
    slots = settings.documents_auto_at_once - _running_auto(db, user_id)
    runs: list[uuid.UUID] = []
    for item in waiting:
        if slots <= 0:
            break
        generation = item.generation
        if generation.id in gone or generation.status is not GenerationStatus.queued:
            continue
        if not _claim(db, generation, at=at) or generation.status is not GenerationStatus.queued:
            continue
        try:
            run = generate.start(db, generation, at=at, settings=settings)
        except agent_runs.ActiveRunsError:
            break
        except ValueError:
            continue
        if item.kind == Kind.cv:
            generation.ceiling_day = today  # counted once it started, never before
        runs.append(run.id)
        slots -= 1
    return runs


# --- the cron and the command ---------------------------------------------------------------------


def people(db: Session, only: Sequence[uuid.UUID] | None = None) -> list[uuid.UUID]:
    """Every person with a profile, in a fixed order (or the ones named)."""
    query = sa.select(Profile.user_id).order_by(Profile.user_id)
    if only is not None:
        query = query.where(Profile.user_id.in_(list(only)))
    return list(db.scalars(query))


def tick(
    sessions: sessionmaker[Session],
    at: datetime,
    settings: Settings,
    only: Sequence[uuid.UUID] | None = None,
) -> list[uuid.UUID]:
    """One pass over every person, one transaction each. Returns the runs to queue."""
    with sessions() as db:
        users = people(db, only)
    runs: list[uuid.UUID] = []
    for user_id in users:
        with sessions() as db:
            try:
                out = person(db, user_id, at=at, settings=settings)
                store.commit(db)
            except store.DocumentBusyError:
                db.rollback()  # a click or a writer holds a row: the next tick tries again
                logger.info("documents.tick.busy", extra={"user_id": str(user_id)})
                continue
            except Exception as exc:
                # One person's fault never ends the pass for the people after them. The class
                # only: a database error's message can carry values.
                db.rollback()
                logger.error(
                    "documents.tick.person_failed",
                    extra={"user_id": str(user_id), "error": type(exc).__name__},
                )
                continue
        if out is None:
            continue
        runs += out.runs
        if out.created or out.cancelled or out.runs or out.recovered:
            logger.info(
                "documents.tick.person",
                extra={
                    "user_id": str(user_id),
                    "generations_created": out.created,
                    "generations_cancelled": out.cancelled,
                    "runs_started": len(out.runs),
                    "generations_recovered": out.recovered,
                },
            )
    return runs


async def send(queue: Any, runs: Sequence[uuid.UUID]) -> int:
    """Queue the runs on the agents queue; a queue that is down leaves them `queued`."""
    sent = 0
    for run_id in runs:
        try:
            if await generate.enqueue_run(queue, run_id):
                sent += 1
        except queues.QueueUnavailableError:
            logger.warning("documents.tick.run_not_queued", extra={"run_id": str(run_id)})
    return sent


async def run(ctx: dict[str, Any]) -> int:
    """The cron job: one pass, then the runs queued. Returns how many runs started."""
    clock: Callable[[], datetime] = ctx["clock"]
    runs = await asyncio.to_thread(tick, ctx["sessions"], clock(), ctx["settings"])
    pool = ctx.get("redis")
    if pool is not None and runs:
        await send(render_job.PoolQueue(pool), runs)
    return len(runs)


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m app.documents.tick <email>`: one pass for one account (journeys, support)."""
    parser = argparse.ArgumentParser(prog="python -m app.documents.tick")
    parser.add_argument("email")
    args = parser.parse_args(argv)
    from app.db import build_engine, build_sessionmaker
    from app.identity.models import User
    from app.identity.service import utcnow

    settings = get_settings()
    sessions = build_sessionmaker(build_engine(settings))
    with sessions() as db:
        user_id = db.scalar(sa.select(User.id).where(User.email == args.email.strip().lower()))
    if user_id is None:
        print("no such account", file=sys.stderr)
        return 1
    runs = tick(sessions, utcnow(), settings, only=[user_id])

    async def _queue() -> int:
        queue = queues.Queue(settings.redis_url.get_secret_value())
        try:
            return await send(queue, runs)
        finally:
            await queue.close()

    sent = asyncio.run(_queue()) if runs else 0
    print(f"started {len(runs)}, queued {sent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
