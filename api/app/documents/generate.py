"""Generations of a posting's documents (task D7; SPEC-documents.md, Flow step 4, "Being careful,
and the automatic correction", "Limits of one run").

A generation is a row of `document_generations` and, once started, one agent run of the writer
of its document's kind (the CV writer, or the letter writer of task D8):

1. `create(db, document, mode, trigger, base_document_id, estimate, token_ceiling, ...)`: the row,
   `queued` (or `waiting` with its reason). `estimate` and `token_ceiling` are computed by the
   caller (D9's estimate.py, D10's tick); nothing here prices a run. The first generation of a
   posting's CV, and a generation from another base CV, sets the document's template and base to
   the base CV's; a letter's first generation takes the template of the posting's CV.
2. `start(db, generation, at=...)`: the writer run, `internal` and bound to the generation, with
   `token_budget` = the generation's ceiling (the runner stops it there and counts each API
   message once). The caller commits, then `enqueue_run`.
3. The run submits through `submit_cv` (submit.py: the schema, the dash fix, the fact check, the
   one correction pass answered to the writer, then the code removal). When it ends, the
   writer's final hook calls `on_final`, which takes what was accepted and calls
   `finish(db, generation, content, corrections)`: a version with its corrections; the draft
   replaced only when nobody wrote it since the generation was created (`draft_revision ==
   base_draft_revision`), else the result is kept as a version beside the person's draft; a
   render of a replaced draft is marked in the same transaction and queued after the commit
   (D6's `mark_queued` and `enqueue`, the two halves of `queue_render`; a lost enqueue is queued
   again by D6's cron). A run that ends any other way keeps nothing: `cancelled`, or `failed`
   with a code of `ERRORS`. Nothing refuses a document once its schema passed.
   The letter follows the CV (`start_letter`, D8): when a CV's `posting` generation of an
   `auto` or `click` trigger is done and the posting's letter has no version yet, the letter's
   generation is created with the CV's `estimate`, `token_ceiling` and base CV (the caller
   computed them for the pair), started, and queued by the same final hook. A chat, a
   shortening and a letter that already has a version start none: the person's letter is not
   rewritten behind their back. A letter is never shortened: the render fits its font.
4. An overflow: D6's render job calls `render_job.on_overflow(document_id, version_id)` after
   storing a PDF that overflows at the tightest step. This module sets it to `on_overflow`,
   which creates one `shorten` generation for a version a writer's `posting` generation made,
   and starts it: the writer is asked to bring the fields over the template's soft limits under
   them, through the same steps. A version made by a `shorten` generation, or by hand, never
   starts another: the pass runs once, and what still overflows is kept and shown.

Logs carry ids, modes, statuses, counts and codes, never a text.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import failures
from app.agents import runs as agent_runs
from app.agents.definitions import FinalRun
from app.agents.models import AgentRun, RunStatus
from app.config import Settings, get_settings
from app.documents import content as c
from app.documents import render_job, rules, store, submit, writer_cv, writer_letter
from app.documents.corrections import Correction
from app.documents.models import (
    Author,
    Document,
    DocumentGeneration,
    DocumentVersion,
    GenerationMode,
    GenerationOutcome,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
    Origin,
    Scope,
)
from app.profile.models import Profile

logger = logging.getLogger("app.documents.generate")

# What a failed generation records (`document_generations.error`).
ERRORS = ("invalid_output", "no_result", "token_limit", "binding_gone", "run_failed")
# A key of a click's CV generation estimate: the click asked for both kinds, so the letter
# follows the new CV even when it has a version (`start_letter`; D10's "Write now").
LETTER_FOLLOWS = "letter_follows"
# The service of a run's context that queues renders (tests); else a Queue on REDIS_URL.
QUEUE_SERVICE = "documents_queue"
# The recorded streams a writer replays on the fake backend when its caller names none.
DEFAULT_SCRIPTS = {
    (Kind.cv, GenerationMode.posting): "documents_cv_valid",
    (Kind.cv, GenerationMode.shorten): "documents_cv_shorten",
    (Kind.letter, GenerationMode.posting): "documents_letter_valid",
}
# The writer of each kind of posting document, and the modes it writes in.
WRITERS = {
    Kind.cv: (writer_cv.CV_WRITER, (GenerationMode.posting, GenerationMode.shorten)),
    Kind.letter: (writer_letter.LETTER_WRITER, (GenerationMode.posting,)),
}


@dataclass(frozen=True)
class Runtime:
    """What `on_overflow` works with: D6 calls it with ids alone."""

    sessions: sessionmaker[Session]
    clock: Callable[[], datetime]
    settings: Settings
    queue: Any  # render_job.Enqueuer: the agents and documents queues share one Redis


RUNTIME: Runtime | None = None


def runtime() -> Runtime:
    """The process's runtime, made once from the settings (tests set RUNTIME)."""
    global RUNTIME
    if RUNTIME is None:
        from app.db import build_engine, build_sessionmaker
        from app.identity.service import utcnow

        settings = get_settings()
        RUNTIME = Runtime(
            sessions=build_sessionmaker(build_engine(settings)),
            clock=utcnow,
            settings=settings,
            queue=queues.Queue(settings.redis_url.get_secret_value()),
        )
    return RUNTIME


# --- create and start -------------------------------------------------------------------------


def _has_version(db: Session, document: Document) -> bool:
    found = db.execute(
        sa.select(DocumentVersion.id).where(DocumentVersion.document_id == document.id).limit(1)
    ).scalar_one_or_none()
    return found is not None


def create(
    db: Session,
    document: Document,
    mode: GenerationMode,
    trigger: GenerationTrigger,
    base_document_id: uuid.UUID | None,
    estimate: Mapping[str, Any],
    token_ceiling: int,
    *,
    at: datetime,
    status: GenerationStatus = GenerationStatus.queued,
    wait_reason: GenerationWait | None = None,
    ceiling_day: date | None = None,
) -> DocumentGeneration:
    """A generation of `document` (module docstring, 1). Raises store.GenerationOpenError when
    one is open, store.NotFoundError for a base CV that is not the person's."""
    user_id = document.user_id
    document = store.lock_document(db, user_id, document.id)
    if mode is GenerationMode.posting and document.scope == Scope.posting:
        first = not _has_version(db, document)
        if document.kind == Kind.cv:
            if base_document_id is None:
                base_document_id = document.base_document_id
            if base_document_id is None:
                default = store.default_base(db, user_id)
                base_document_id = default.id if default is not None else None
            if base_document_id is not None:
                base = store.get(db, user_id, base_document_id)
                if base is None or base.scope != Scope.base:
                    raise store.NotFoundError(base_document_id)
                if first or base.id != document.base_document_id:
                    document.template_slug = base.template_slug
                    document.base_document_id = base.id
        elif first and document.posting_id is not None:
            cv = next(
                (
                    found
                    for found in store.posting_documents(db, user_id, document.posting_id)
                    if found.kind == Kind.cv
                ),
                None,
            )
            if cv is not None:
                document.template_slug = cv.template_slug
        document.updated_at = at
        db.flush()
    generation = store.create_generation(
        db,
        user_id,
        document.id,
        mode,
        trigger,
        at=at,
        estimate=estimate,
        token_ceiling=token_ceiling,
        status=status,
        wait_reason=wait_reason,
        ceiling_day=ceiling_day,
        base_document_id=base_document_id,
    )
    logger.info(
        "documents.generation.created",
        extra={"generation_id": str(generation.id), "mode": mode.value, "status": status.value},
    )
    return generation


def _script(settings: Settings, kind: Kind, mode: GenerationMode, script: str | None) -> str | None:
    if not settings.agents_fake_backend or settings.app_env == "production":
        return None
    return script or DEFAULT_SCRIPTS.get((kind, mode))


def start(
    db: Session,
    generation: DocumentGeneration,
    *,
    at: datetime,
    script: str | None = None,
    long_fields: Iterable[str] = (),
    settings: Settings | None = None,
) -> AgentRun:
    """The writer run of a generation, `queued`, and the generation `running`. The caller
    commits, then `enqueue_run`. Raises agent_runs.ActiveRunsError when the person has their
    most runs active (the generation stays as it was)."""
    settings = settings or get_settings()
    if generation.status not in (GenerationStatus.queued, GenerationStatus.waiting):
        raise ValueError(f"the generation is {generation.status.value}: it has its run")
    document = store.lock_document(db, generation.user_id, generation.document_id)
    if document.scope != Scope.posting or document.kind not in WRITERS:
        raise ValueError("the writers write a posting's CV or letter only")
    definition, modes = WRITERS[document.kind]
    if generation.mode not in modes:
        raise ValueError(f"not a mode of the {document.kind.value} writer: {generation.mode.value}")
    template = render_job.template_of(db, generation.user_id, document.template_slug)
    data: dict[str, Any] = {
        "generation_id": str(generation.id),
        "mode": generation.mode.value,
        "language": document.language.value,
        "limits": asdict(template.kinds[document.kind.value].soft_limits),
        "token_budget": generation.token_ceiling,
    }
    if document.kind == Kind.cv:
        data["long_fields"] = list(long_fields)
    chosen = _script(settings, document.kind, generation.mode, script)
    if chosen is not None:
        data["script"] = chosen
    run = agent_runs.create_run(
        db,
        generation.user_id,
        definition.name,
        document.posting_id,
        definition.validate_input(data),
        definition.model,
        at,
    )
    generation.run_id = run.id
    generation.status = GenerationStatus.running
    generation.wait_reason = None
    generation.started_at = at
    db.flush()
    logger.info(
        "documents.generation.started",
        extra={"generation_id": str(generation.id), "run_id": str(run.id)},
    )
    return run


async def enqueue_run(queue: Any, run_id: uuid.UUID) -> bool:
    """Queue the writer run on the agents queue, after the commit of `start`."""
    return bool(
        await queue.enqueue(
            "run_agent",
            str(run_id),
            queue=queues.AGENTS,
            job_id=queues.agent_run_job_id(run_id),
        )
    )


# --- the end of a run -------------------------------------------------------------------------


def _profile_revision(db: Session, user_id: uuid.UUID) -> int | None:
    return db.scalar(sa.select(Profile.revision).where(Profile.user_id == user_id))


def finish(
    db: Session,
    generation: DocumentGeneration,
    content: c.CvContent | c.LetterContent,
    corrections: Iterable[Correction],
    *,
    at: datetime,
) -> int | None:
    """Store a generation's result (module docstring, 3). Returns the draft revision whose render
    was marked, to `render_job.enqueue` after the commit, or None when the draft was kept."""
    user_id = generation.user_id
    document = store.lock_document(db, user_id, generation.document_id)
    records = list(corrections)
    revision: int | None = None
    if document.draft_revision == generation.base_draft_revision:
        store.save_draft(
            db, user_id, document.id, c.dump(content), document.draft_revision, Author.writer, at=at
        )
        version, _ = store.snapshot(
            db,
            user_id,
            document.id,
            Origin.generated,
            at=at,
            generation_id=generation.id,
            corrections=records,
            profile_revision=_profile_revision(db, user_id),
        )
        generation.outcome = GenerationOutcome.draft_replaced
        document, fresh = render_job.mark_queued(
            db, user_id, document.id, revision=document.draft_revision, bounded=False
        )
        revision = document.draft_revision if fresh else None
    else:
        version, _ = store.snapshot(
            db,
            user_id,
            document.id,
            Origin.generated,
            at=at,
            generation_id=generation.id,
            corrections=records,
            profile_revision=_profile_revision(db, user_id),
            content=c.dump(content),
        )
        generation.outcome = GenerationOutcome.kept_as_version
    generation.status = GenerationStatus.done
    generation.version_number = version.number
    generation.corrections = len(records)
    generation.error = None
    generation.finished_at = at
    db.flush()
    logger.info(
        "documents.generation.done",
        extra={
            "generation_id": str(generation.id),
            "outcome": generation.outcome.value,
            "version": version.number,
            "corrections": len(records),
        },
    )
    return revision


def _error(run: AgentRun | None, status: RunStatus, taken: submit.Taken) -> str:
    if taken.exhausted:
        return "invalid_output"
    if status is RunStatus.done:
        return "no_result"
    error = run.error if run is not None else None
    if error == failures.TOKEN_LIMIT:
        return "token_limit"
    if error == failures.BINDING_GONE:
        return "binding_gone"
    return "run_failed"


def settle(
    sessions: sessionmaker[Session],
    run_id: uuid.UUID,
    status: RunStatus,
    taken: submit.Taken,
    at: datetime,
) -> tuple[uuid.UUID, int] | None:
    """The generation of a finished run, finished, cancelled or failed, in one transaction.
    Returns `(document_id, revision)` of a render to queue after the commit."""
    with sessions() as db:
        found = db.execute(
            sa.select(DocumentGeneration.user_id, DocumentGeneration.document_id).where(
                DocumentGeneration.run_id == run_id
            )
        ).one_or_none()
        if found is None:
            return None
        store.lock_document(db, found.user_id, found.document_id)  # the lock order of store.py
        generation = db.execute(
            sa.select(DocumentGeneration)
            .where(DocumentGeneration.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        if generation.status is not GenerationStatus.running:
            return None
        render: tuple[uuid.UUID, int] | None = None
        if status is RunStatus.done and taken.accepted is not None:
            accepted = taken.accepted
            revision = finish(db, generation, accepted.content, accepted.corrections, at=at)
            render = (generation.document_id, revision) if revision is not None else None
        elif status is RunStatus.cancelled:
            generation.status = GenerationStatus.cancelled
            generation.finished_at = at
        else:
            generation.status = GenerationStatus.failed
            generation.error = _error(db.get(AgentRun, run_id), status, taken)
            generation.finished_at = at
            logger.info(
                "documents.generation.failed",
                extra={"generation_id": str(generation.id), "code": generation.error},
            )
        store.commit(db)
        return render


def start_letter(
    sessions: sessionmaker[Session],
    run_id: uuid.UUID,
    at: datetime,
    settings: Settings,
) -> uuid.UUID | None:
    """Create and start the letter that follows a CV (module docstring, 3): the CV's `posting`
    generation of run `run_id` is done, of an `auto` or `click` trigger, and the posting's letter
    has no version yet, or the click asked for both kinds (its estimate carries LETTER_FOLLOWS:
    the letter is written again, from the new CV). The letter's generation takes the CV's
    estimate, ceiling and base CV, and its document the CV's template. Returns the run to
    enqueue, else None (nothing to chain, a generation already open for the letter, or the
    person's runs all busy: the letter then stays unwritten and "Write now" writes it)."""
    with sessions() as db:
        found = db.execute(
            sa.select(DocumentGeneration.user_id, DocumentGeneration.document_id).where(
                DocumentGeneration.run_id == run_id
            )
        ).one_or_none()
        if found is None:
            return None
        cv = store.lock_document(db, found.user_id, found.document_id)
        parent = db.execute(
            sa.select(DocumentGeneration).where(DocumentGeneration.run_id == run_id)
        ).scalar_one()
        if (
            cv.scope != Scope.posting
            or cv.kind != Kind.cv
            or cv.posting_id is None
            or parent.mode is not GenerationMode.posting
            or parent.status is not GenerationStatus.done
            or parent.trigger not in (GenerationTrigger.auto, GenerationTrigger.click)
        ):
            return None
        letter = store.get_or_create(db, cv.user_id, cv.posting_id, Kind.letter, at=at)
        asked = parent.trigger is GenerationTrigger.click and bool(
            parent.estimate.get(LETTER_FOLLOWS)
        )
        if _has_version(db, letter) and not asked:
            return None  # the person's letter is not written again behind their back
        figures = {k: v for k, v in parent.estimate.items() if k != LETTER_FOLLOWS}
        try:
            generation = create(
                db,
                letter,
                GenerationMode.posting,
                parent.trigger,
                parent.base_document_id or cv.base_document_id,
                figures,
                parent.token_ceiling,
                at=at,
            )
            run = start(db, generation, at=at, settings=settings)
        except (store.GenerationOpenError, agent_runs.ActiveRunsError):
            db.rollback()
            return None
        store.commit(db)
        return run.id


async def _send(
    final: FinalRun, send: Callable[[Any], Awaitable[Any]], lost: str, **fields: str
) -> None:
    """`send(queue)` on the run's queue service, else on a Queue of the settings; a queue that
    is down is logged as `lost`, never raised (the row stays queued for D6's cron or the
    caller's next pass)."""
    queue = final.services.get(QUEUE_SERVICE)
    own = queue is None
    if own:
        queue = queues.Queue(final.settings.redis_url.get_secret_value())
    try:
        await send(queue)
    except queues.QueueUnavailableError:
        logger.warning(lost, extra=fields)
    finally:
        if own:
            await queue.close()


async def on_final(final: FinalRun) -> None:
    """The writers' final hook: settle the generation, queue its render, and after a CV start
    the posting's letter."""
    if final.agent not in (writer_cv.NAME, writer_letter.NAME):
        return
    taken = submit.take(final.run_id)
    render = await asyncio.to_thread(
        settle, final.sessions, final.run_id, final.status, taken, final.clock()
    )
    if render is not None:
        await _send(
            final,
            lambda queue: render_job.enqueue(queue, render[0], render[1]),
            # The row stays `queued`: D6's cron queues it again within a minute.
            "documents.generation.render_not_queued",
            run_id=str(final.run_id),
        )
    if final.agent != writer_cv.NAME or final.status is not RunStatus.done:
        return
    letter = await asyncio.to_thread(
        start_letter, final.sessions, final.run_id, final.clock(), final.settings
    )
    if letter is not None:
        logger.info("documents.generation.letter", extra={"run_id": str(letter)})
        await _send(
            final,
            lambda queue: enqueue_run(queue, letter),
            "documents.generation.run_not_queued",
            run_id=str(letter),
        )


# --- the shortening pass ----------------------------------------------------------------------


def long_fields(content: c.CvContent | c.LetterContent, limits: rules.SoftLimits) -> list[str]:
    """The fields over their soft limit, the most over first."""
    over = rules.soft_lengths(content, limits)
    over.sort(key=lambda issue: (-(issue.end - issue.start), issue.field))
    return [issue.field for issue in over]


def shorten(
    sessions: sessionmaker[Session],
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    at: datetime,
    settings: Settings,
) -> uuid.UUID | None:
    """Create and start the one `shorten` generation of an overflowing version that a writer's
    `posting` generation made. Returns the run to enqueue, else None."""
    with sessions() as db:
        owner = db.scalar(sa.select(Document.user_id).where(Document.id == document_id))
        if owner is None:
            return None
        document = store.lock_document(db, owner, document_id)
        version = db.get(DocumentVersion, version_id)
        if version is None or version.document_id != document.id or version.generation_id is None:
            return None
        parent = db.get(DocumentGeneration, version.generation_id)
        if parent is None or parent.mode is not GenerationMode.posting:
            return None  # a hand edit, or already a shortening: the pass runs once
        later = db.execute(
            sa.select(DocumentGeneration.id).where(
                DocumentGeneration.document_id == document.id,
                DocumentGeneration.mode == GenerationMode.shorten,
                DocumentGeneration.created_at >= parent.created_at,
            )
        ).first()
        if later is not None or document.scope != Scope.posting or document.kind != Kind.cv:
            return None
        if document.draft_written_by is not Author.writer or (
            c.content_hash(document.kind, c.parse(document.kind, document.draft))
            != version.content_hash
        ):
            return (
                None  # saved by hand during the render: the person's draft is not ours to shorten
            )
        template = render_job.template_of(db, owner, document.template_slug)
        fields = long_fields(c.parse("cv", version.content), template.kinds["cv"].soft_limits)
        try:
            generation = create(
                db,
                document,
                GenerationMode.shorten,
                parent.trigger,
                parent.base_document_id,
                parent.estimate,
                parent.token_ceiling,
                at=at,
            )
            run = start(db, generation, at=at, long_fields=fields, settings=settings)
        except (store.GenerationOpenError, agent_runs.ActiveRunsError):
            db.rollback()
            return None
        store.commit(db)
        return run.id


async def on_overflow(document_id: uuid.UUID, version_id: uuid.UUID) -> None:
    """D6's hook (`render_job.on_overflow`), set when this module is imported."""
    rt = runtime()
    run_id = await asyncio.to_thread(
        shorten, rt.sessions, document_id, version_id, rt.clock(), rt.settings
    )
    if run_id is None:
        return
    logger.info("documents.generation.shorten", extra={"run_id": str(run_id)})
    try:
        await enqueue_run(rt.queue, run_id)
    except queues.QueueUnavailableError:
        logger.warning("documents.generation.run_not_queued", extra={"run_id": str(run_id)})


writer_cv.register_final_hook(on_final)
render_job.on_overflow = on_overflow
