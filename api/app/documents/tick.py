"""What the documents tick still does once nothing is written without a click (task D29; it
replaced D10's automatic generation; SPEC-documents.md, "Writing on a click", Flow).

The owner, 2026-11-06: "I don't want to automaticaly generate the cv and cover letter anymore."
No CV, letter or base CV is written here on the tick's own initiative: no eligible postings, no
fit threshold, no daily safety ceiling, no backfill, no first base CV from the profile. What
stays is the work a click leaves for later.

`run(ctx)` is an arq cron job of the documents queue, every minute (app/worker.py). It holds ids
only and returns a count. `python -m app.documents.tick <email>` runs one pass for one account,
so a journey never waits a minute.

**One pass, per person** (`person`), in one transaction under the person's tick lock (a
transaction level advisory lock taken with `try`: a second worker, or a second pass, finds it
held and leaves the person to the next tick, so two passes never start the same thing twice):

0. The running generations whose run ended without its final hook (a worker killed under it, a
   run the reaper failed, one lost by a queue that was down, one cancelled while queued) and
   finished over `HOOK_GRACE_S` ago are settled as the hook would have (`recover`): the
   runner's contract is that whoever chains runs also looks on a timer, and the tick is it.
   A hiring panel whose job died is closed the same way (`panel.recover`), and the letter its
   CV waited for is started.
1. The base CV builds, uploads and design captures whose reason to wait is gone start
   (`writer_base.start_ready`). Every one of them was asked for by a click.
2. The person's clicks ("Write", an Apply, a Like) that waited for the consent, a credential, a
   base CV being built, or the person's limit of active runs start (`start_clicks`).
3. A generation of trigger `auto` that has not started (left by D10 at the deploy of D29) is
   cancelled, once, its document untouched: nothing creates one any more.

**Never two starts.** A click cancels a generation that has not started, under its document's
lock. So the pass writes nothing to a generation, and starts none, before it holds the
document's lock and has read the generation again (`_claim`): one the click cancelled meanwhile
is left alone. The posting's lock (shared) comes first, as for the click, the chain and a move to
`applied`. One person whose pass fails for any other reason is rolled back and logged; the pass
goes on with the next person.

The runs are queued on the agents queue after the commit. A queue that is down leaves the run
`queued`, as the routes do. Logs carry ids and counts, never a text.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import failures
from app.agents import runs as agent_runs
from app.agents.models import FINAL_STATUSES, AgentRun, RunStatus
from app.config import Settings, get_settings
from app.documents import generate, panel, render_job, store, writer_base
from app.documents.models import (
    Document,
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    Kind,
    Scope,
)
from app.jobs.models import JobStatus
from app.profile.models import Profile

logger = logging.getLogger("app.documents.tick")

LOCK_PREFIX = "documents.tick:"
NOT_STARTED = (GenerationStatus.waiting, GenerationStatus.queued)
# The modes `recover` settles: a posting's writes, its panel's rebuild, the base CV builds, an
# upload's conversion and capture.
RECOVERY_MODES = (
    GenerationMode.posting,
    GenerationMode.panel,
    GenerationMode.base_profile,
    GenerationMode.base_upload,
    GenerationMode.template_capture,
)
# A run that ended without its final hook is settled once it finished this long ago: over
# runner.FINAL_HOOK_TIMEOUT_S (30 s), so a hook that is merely late still settles its own result.
HOOK_GRACE_S = 120
# The code `generate.settle` gives a failed run, by the run's error; any other is `run_failed`.
RUN_ERRORS = {failures.TOKEN_LIMIT: "token_limit", failures.BINDING_GONE: "binding_gone"}


def try_lock(db: Session, user_id: uuid.UUID) -> bool:
    """The person's tick lock, held until the transaction ends; False when another holds it."""
    key = sa.func.hashtextextended(LOCK_PREFIX + str(user_id), 0)
    return bool(db.scalar(sa.select(sa.func.pg_try_advisory_xact_lock(key))))


@dataclass
class Outcome:
    runs: list[uuid.UUID] = field(default_factory=list)
    cancelled: int = 0
    recovered: int = 0
    # The CV runs whose panel was closed by `panel.recover`: their letter follows after the commit.
    letters_after: list[uuid.UUID] = field(default_factory=list)


def recover(db: Session, user_id: uuid.UUID, at: datetime) -> int:
    """Step 0 of the module docstring: each running generation whose run is final and finished
    over HOOK_GRACE_S ago gets the run's outcome, as `generate.settle` would have given it:
    `failed` with a code of RUN_ERRORS or `run_failed`, `cancelled` for a run cancelled while
    queued. Returns how many."""
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
            DocumentGeneration.mode.in_(RECOVERY_MODES),
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
            generation.error = RUN_ERRORS.get(run.error or "", "run_failed")
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


def _claim(db: Session, generation: DocumentGeneration) -> bool:
    """Lock the generation's posting and document (`_lock`; the document is the lock a click
    takes), then read the generation again: True while it has still not started. A click may
    have cancelled it, and started its own, since the pass read it: the pass then writes nothing
    to it."""
    db.flush()  # what the pass already wrote to claimed rows, before the re-read replaces it
    _lock(db, generation.user_id, generation.document_id)
    fresh = db.execute(
        sa.select(DocumentGeneration)
        .where(DocumentGeneration.id == generation.id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    return fresh.status in NOT_STARTED


def _name_default(db: Session, generation: DocumentGeneration, default: Document) -> None:
    """A CV that waited for its base CV (none when it was clicked) is written from the default
    one, which gives its template as `generate.create` does; the write records the template
    given as its own, so no later write reads it as a switch by hand."""
    document = store.lock_document(db, generation.user_id, generation.document_id)
    if document.kind != Kind.cv or document.base_document_id is not None:
        return
    if generate.name_base(db, document, default):
        # A new dict: an assignment is what marks the JSONB column changed.
        generation.estimate = {
            **generation.estimate,
            generate.WRITE_TEMPLATE: document.template_slug,
        }
    generation.base_document_id = document.base_document_id


def _cancel(generation: DocumentGeneration, at: datetime) -> None:
    generation.status = GenerationStatus.cancelled
    generation.wait_reason = None
    generation.finished_at = at


def start_clicks(
    db: Session, user_id: uuid.UUID, at: datetime, settings: Settings
) -> list[uuid.UUID]:
    """The person's clicks ("Write", an Apply, a Like) that have not started, oldest first:
    started when the consent and a credential are there and no base CV is being built for them
    (`writer_base.click_wait`), else kept waiting with the reason. One that meets the limit of
    active runs stays `queued` for the next tick. Returns the runs to queue after the commit."""
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
    reason = writer_base.click_wait(db, user_id, at) if found else None
    default = store.default_base(db, user_id) if found else None
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
        if default is not None:
            _name_default(db, generation, default)
        try:
            runs.append(generate.start(db, generation, at=at, settings=settings).id)
        except agent_runs.ActiveRunsError:
            generation.status = GenerationStatus.queued
            generation.wait_reason = None
            break
        except ValueError:
            continue
    return runs


def cancel_auto(db: Session, user_id: uuid.UUID, at: datetime) -> int:
    """Step 3 of the module docstring: every generation of trigger `auto` that has not started
    is cancelled, its document and its versions untouched. Returns how many."""
    found = db.scalars(
        sa.select(DocumentGeneration)
        .where(
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.trigger == GenerationTrigger.auto,
            DocumentGeneration.status.in_(NOT_STARTED),
        )
        .order_by(DocumentGeneration.id)
        .execution_options(populate_existing=True)
    ).all()
    cancelled = 0
    for generation in found:
        if _claim(db, generation):
            _cancel(generation, at)
            cancelled += 1
    return cancelled


def lock_postings(db: Session, user_id: uuid.UUID) -> None:
    """Lock 2 of every posting the rest of the pass may touch, shared and in id order, before
    the first of them is claimed: the postings of the generations not started. Any other
    transaction that holds several job rows (an import, a bulk) takes them in id order too
    (jobs.service lock order), so none waits for a row the pass holds while holding one the pass
    asks for next."""
    ids = {
        posting_id
        for posting_id in db.scalars(
            sa.select(Document.posting_id)
            .join(DocumentGeneration, DocumentGeneration.document_id == Document.id)
            .where(
                DocumentGeneration.user_id == user_id,
                DocumentGeneration.mode == GenerationMode.posting,
                DocumentGeneration.status.in_(NOT_STARTED),
                Document.scope == Scope.posting,
            )
        )
        if posting_id is not None
    }
    store.lock_postings(db, user_id, ids)


def person(db: Session, user_id: uuid.UUID, *, at: datetime, settings: Settings) -> Outcome | None:
    """One pass for one person (module docstring). The caller commits, then queues the runs and
    starts the letters of `letters_after`. None when another pass holds the person."""
    if not try_lock(db, user_id):
        return None
    out = Outcome()
    out.recovered = recover(db, user_id, at)
    out.letters_after = panel.recover(db, user_id, at)
    out.runs += writer_base.start_ready(db, user_id, at=at, settings=settings)
    lock_postings(db, user_id)
    out.cancelled = cancel_auto(db, user_id, at)
    out.runs += start_clicks(db, user_id, at, settings)
    db.flush()
    return out


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
        for run_id in out.letters_after:
            # The CV whose panel was closed: its letter follows, as the panel job's end does.
            letter = generate.start_letter(sessions, run_id, at, settings)
            if letter is not None:
                runs.append(letter)
        if out.cancelled or out.runs or out.recovered or out.letters_after:
            logger.info(
                "documents.tick.person",
                extra={
                    "user_id": str(user_id),
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
