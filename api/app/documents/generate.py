"""Generations of a posting's documents (task D7; SPEC-documents.md, Flow step 4, "Being careful,
and the automatic correction", "Limits of one run").

A generation is a row of `document_generations` and, once started, one agent run of the writer
of its document's kind (the CV writer, or the letter writer of task D8):

1. `create(db, document, mode, trigger, base_document_id, estimate, token_ceiling, ...)`: the row,
   `queued` (or `waiting` with its reason). `estimate` and `token_ceiling` are computed by the
   caller (D9's estimate.py, D10's tick); nothing here prices a run. The first generation of a
   posting's CV sets the document's template and base to the base CV's. A generation from
   another base CV sets the base, and the template too unless the person switched it by hand
   since the CV's last write (`switched_by_hand`): then hers stays (`name_base`, which D10's
   tick also calls when it names the default for a CV that waited without a base CV). A posting
   CV's generation records the template it gave in its estimate (`WRITE_TEMPLATE`), and so does
   the tick when it names one. A letter's first generation takes the template of the posting's
   CV.
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
   generation is created with the CV's `estimate` and base CV, started, and queued by the same
   final hook. Its ceiling is what the pair has left (`letter_ceiling`): the CV generation's
   `token_ceiling`, computed by the caller for the pair, minus the tokens the CV's run spent,
   so a posting's CV and letter share that one ceiling. When that spend is unknown (no run, or
   a count the runner did not write), the letter gets no budget: it is created `failed` with
   `token_limit` and starts no run (`no_budget`). The consent and the credential are read
   again first: without either the letter waits with that reason; with the person's runs all
   busy it stays `queued`; the tick starts it in both cases. A chat, a
   shortening and a letter that already has a version start none: the person's letter is not
   rewritten behind their back. Nor does a posting past `cv_ready` (staged, applied, later or
   skipped): a letter written after the application is not what was sent (D14). The click
   that asked for both kinds is the exception to both. A letter is never shortened: the render
   fits its font.
4. An overflow: D6's render job calls `render_job.on_overflow(document_id, version_id)` after
   storing a PDF that overflows at the tightest step. This module sets it to `on_overflow`,
   which creates one `shorten` generation for a version a writer's `posting` generation made,
   and starts it: the writer is asked to bring the fields over the template's soft limits under
   them, through the same steps. A version made by a `shorten` generation, or by hand, never
   starts another: the pass runs once, and what still overflows is kept and shown. The pass is
   part of the posting's spend: it starts only with the person's consent and credential, and its
   ceiling is the letter's, what the pair has left after the CV's run (`shortening_ceiling`);
   with nothing left, or a spend unknown, there is no pass.
5. "Make it fit" (`make_fit`, the route `POST /api/documents/{id}/fit`): the person asks for a
   `shorten` generation of a posting CV whose latest version overflows, whoever made that
   version (a writer, a template switch, a hand edit, a shortening). It passes the checks of
   the pass above and refuses with a reason of `FIT_REFUSALS` instead of skipping: the consent,
   a credential, a write to draw on and a known ceiling left. That ceiling is what the CV's last
   write has left after its run and after the shortening passes made since
   (`shortening_ceiling`): passes clicked one after another share the one budget the automatic
   pass had. A person whose runs are all busy is refused too: nothing starts a queued pass
   later.
6. An outreach proposal (issue #485, `proposal.create`): a `posting` generation of a posting's
   proposal, written by `writer_proposal` through the same steps, never followed by anything
   and never rendered: `finish` stores its version and its draft and marks no render.

Logs carry ids, modes, statuses, counts and codes, never a text.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from app.documents import (
    eligibility,
    render_job,
    rules,
    store,
    submit,
    writer_cv,
    writer_letter,
    writer_proposal,
)
from app.documents.corrections import Correction
from app.documents.models import (
    RENDERED_KINDS,
    Author,
    Document,
    DocumentGeneration,
    DocumentVersion,
    FitResult,
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
# A key of a click's CV generation estimate: the recorded stream the following letter replays on
# the fake backend (a journey names the streams of both kinds in one click).
LETTER_SCRIPT = "letter_script"
# A key of a generation's estimate: the template it gave the document (a posting CV's write, or a
# chat's `set_template`), so a later write from another base CV can tell a hand switch since.
WRITE_TEMPLATE = "write_template"
# Why "Make it fit" starts no shortening pass (`make_fit`).
FIT_REFUSALS = (
    "not_shortened",
    "not_drawn",
    "fits",
    "consent",
    "credential",
    "not_written",
    "spend_unknown",
    "token_ceiling",
    "runs_busy",
)
# The service of a run's context that queues renders (tests); else a Queue on REDIS_URL.
QUEUE_SERVICE = "documents_queue"
# The recorded streams a writer replays on the fake backend when its caller names none.
DEFAULT_SCRIPTS = {
    (Kind.cv, GenerationMode.posting): "documents_cv_valid",
    (Kind.cv, GenerationMode.shorten): "documents_cv_shorten",
    (Kind.letter, GenerationMode.posting): "documents_letter_valid",
    (Kind.proposal, GenerationMode.posting): "documents_proposal_valid",
}
# The writer of each kind of posting document, and the modes it writes in.
WRITERS = {
    Kind.cv: (writer_cv.CV_WRITER, (GenerationMode.posting, GenerationMode.shorten)),
    Kind.letter: (writer_letter.LETTER_WRITER, (GenerationMode.posting,)),
    Kind.proposal: (writer_proposal.PROPOSAL_WRITER, (GenerationMode.posting,)),
}


@dataclass(frozen=True)
class _TextKind:
    soft_limits: rules.SoftLimits


@dataclass(frozen=True)
class _TextOnly:
    """What `start` reads of a template for a kind no template prints (an outreach proposal,
    issue #485): no soft limits, the hard limits of its content are its limits."""

    kinds: Mapping[str, _TextKind]


TEXT_ONLY = _TextOnly({Kind.proposal.value: _TextKind(submit.NO_SOFT_LIMITS)})


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


def give_way(db: Session, document: Document, at: datetime) -> bool:
    """Whether the document is free for a click (the "Write now" route, a Like): nothing open, or
    an automatic generation not started, whatever it waits for, cancelled here (never started, so
    never counted). False for a running generation, or a click or chat one: left as it is.
    The caller holds the posting (`store.lock_posting`); the document is locked before the read,
    the lock the tick takes before it starts a generation, so the read below is never older than a
    start the tick committed."""
    store.lock_document(db, document.user_id, document.id)
    current = store.open_generation(db, document.user_id, document.id)
    if current is None:
        return True
    if current.status is GenerationStatus.running or current.trigger is not GenerationTrigger.auto:
        return False
    current.status = GenerationStatus.cancelled
    current.wait_reason = None
    current.finished_at = at
    db.flush()
    return True


def _has_version(db: Session, document: Document) -> bool:
    found = db.execute(
        sa.select(DocumentVersion.id).where(DocumentVersion.document_id == document.id).limit(1)
    ).scalar_one_or_none()
    return found is not None


def last_write(db: Session, document: Document) -> DocumentGeneration | None:
    """The document's last write: of its `posting` generations that are done, the one whose
    version is the newest (version numbers only grow, where two clocks can tie)."""
    found: DocumentGeneration | None = db.execute(
        sa.select(DocumentGeneration)
        .where(
            DocumentGeneration.user_id == document.user_id,
            DocumentGeneration.document_id == document.id,
            DocumentGeneration.mode == GenerationMode.posting,
            DocumentGeneration.status == GenerationStatus.done,
        )
        .order_by(
            DocumentGeneration.version_number.desc().nulls_last(),
            DocumentGeneration.created_at.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()
    return found


def switched_by_hand(db: Session, document: Document) -> bool:
    """Did the person switch the CV's template since its last write (spec, "Base CVs",
    Inheritance)? Read so that neither a pruned version nor a switch back hides it: the template
    the last write gave is not the document's, or a `template` version, or a chat that switched
    it with its new content, came after it (any, for a CV the writer never wrote)."""
    last = last_write(db, document)
    gave = last.estimate.get(WRITE_TEMPLATE) if last is not None else None
    if gave is not None and gave != document.template_slug:
        return True
    since = last.version_number if last is not None and last.version_number is not None else 0
    found = db.execute(
        sa.select(DocumentVersion.id)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.origin == Origin.template,
            DocumentVersion.number > since,
        )
        .limit(1)
    ).first()
    if found is not None:
        return True
    by_chat = db.execute(
        sa.select(DocumentGeneration.id)
        .where(
            DocumentGeneration.user_id == document.user_id,
            DocumentGeneration.document_id == document.id,
            DocumentGeneration.mode == GenerationMode.chat,
            DocumentGeneration.status == GenerationStatus.done,
            DocumentGeneration.version_number > since,
            DocumentGeneration.estimate.has_key(WRITE_TEMPLATE),
        )
        .limit(1)
    ).first()
    return by_chat is not None


def name_base(db: Session, document: Document, base: Document) -> bool:
    """`base` becomes the posting CV's base CV. It gives its template at the CV's first write, or
    as another base CV unless the person switched the template by hand since the last write
    (spec, "Base CVs", Inheritance): True when it gave it. The caller records what it gave as
    the write's template (`WRITE_TEMPLATE`), so no later write reads it as a switch by hand."""
    first = not _has_version(db, document)
    another = base.id != document.base_document_id
    document.base_document_id = base.id
    if first or (another and not switched_by_hand(db, document)):
        document.template_slug = base.template_slug
        return True
    return False


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
                name_base(db, document, base)
            estimate = {**estimate, WRITE_TEMPLATE: document.template_slug}
        elif (
            document.kind == Kind.letter
            and document.posting_id is not None
            and not _has_version(db, document)
        ):
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
        raise ValueError("the writers write a posting's CV, letter or proposal only")
    definition, modes = WRITERS[document.kind]
    if generation.mode not in modes:
        raise ValueError(f"not a mode of the {document.kind.value} writer: {generation.mode.value}")
    template = (
        render_job.template_of(db, generation.user_id, document.template_slug)
        if document.kind in RENDERED_KINDS
        else TEXT_ONLY
    )
    data: dict[str, Any] = {
        "generation_id": str(generation.id),
        "mode": generation.mode.value,
        "language": document.language.value,
        "tone": store.preferences(db, generation.user_id).documents_tone,
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
    # What the render will print it with, so its snapshot reuses this version and its link. A
    # proposal is text only: its versions keep the document's slug, at version 1, as a hand
    # save's snapshot does (router.py).
    if document.kind in RENDERED_KINDS:
        template = render_job.template_of(db, user_id, document.template_slug)
        printed_with, printed_version = template.slug, template.version
    else:
        printed_with, printed_version = document.template_slug, 1
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
            template_slug=printed_with,
            template_version=printed_version,
        )
        generation.outcome = GenerationOutcome.draft_replaced
        if document.kind in RENDERED_KINDS:  # a proposal is text only: nothing renders it
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
            template_slug=printed_with,
            template_version=printed_version,
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
    *,
    after_finish: Callable[[Session, DocumentGeneration], None] | None = None,
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
            if after_finish is not None:
                after_finish(db, generation)
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


def letter_ceiling(db: Session, cv_generation: DocumentGeneration) -> int | None:
    """The token ceiling of the letter that follows `cv_generation`, and of the CV's one
    shortening pass (module docstring, 4): the pair's ceiling minus the tokens its run spent
    (the run's own count, never an estimate), never under zero. A budget of zero lets the
    letter's run make no call: it stops at its limit and is retried as one. None when the
    spend is unknown (no run, or a count missing): the letter gets no budget then, never the
    whole pair's (`no_budget`). With None or zero, no shortening pass starts."""
    run = db.get(AgentRun, cv_generation.run_id) if cv_generation.run_id is not None else None
    if run is None or run.input_tokens is None or run.output_tokens is None:
        return None
    return max(0, cv_generation.token_ceiling - run.input_tokens - run.output_tokens)


def no_budget(db: Session, generation: DocumentGeneration, at: datetime) -> None:
    """A letter whose pair has no budget it can show: `failed` at its limit, with no run, so the
    person sees "Try again" and the tick's one retry after a stop at the ceiling applies."""
    generation.status = GenerationStatus.failed
    generation.wait_reason = None
    generation.error = "token_limit"
    generation.finished_at = at
    db.flush()
    logger.info(
        "documents.generation.failed",
        extra={"generation_id": str(generation.id), "code": generation.error},
    )


def start_letter(
    sessions: sessionmaker[Session],
    run_id: uuid.UUID,
    at: datetime,
    settings: Settings,
) -> uuid.UUID | None:
    """Create and start the letter that follows a CV (module docstring, 3): the CV's `posting`
    generation of run `run_id` is done, of an `auto` or `click` trigger, the posting is not past
    `cv_ready` and its letter has no version yet, or the click asked for both kinds (its
    estimate carries LETTER_FOLLOWS: the letter is written again, from the new CV). The letter's
    generation takes the CV's estimate and base CV, what the pair's ceiling has left, and its
    document the CV's template. Returns the run to enqueue, else None: nothing to chain, a
    generation already open for the letter, or a letter left for the tick to start (waiting for
    the consent or a credential, or `queued` while the person's runs are all busy)."""
    from app.documents import writer_base  # writer_base imports this module

    with sessions() as db:
        found = db.execute(
            sa.select(
                DocumentGeneration.user_id, DocumentGeneration.document_id, Document.posting_id
            )
            .join(Document, Document.id == DocumentGeneration.document_id)
            .where(DocumentGeneration.run_id == run_id)
        ).one_or_none()
        if found is None or found.posting_id is None:
            return None
        # Lock 2 before lock 3 (store.py): the letter's row and its run reference the posting.
        posting_status = store.lock_posting(db, found.user_id, found.posting_id)
        cv = store.lock_document(db, found.user_id, found.document_id)
        parent = db.execute(
            sa.select(DocumentGeneration).where(DocumentGeneration.run_id == run_id)
        ).scalar_one()
        if (
            posting_status is None
            or cv.scope != Scope.posting
            or cv.kind != Kind.cv
            or parent.mode is not GenerationMode.posting
            or parent.status is not GenerationStatus.done
            or parent.trigger not in (GenerationTrigger.auto, GenerationTrigger.click)
        ):
            return None
        asked = parent.trigger is GenerationTrigger.click and bool(
            parent.estimate.get(LETTER_FOLLOWS)
        )
        if posting_status not in eligibility.LETTER_STATUSES and not asked:
            return None  # staged, sent or set aside: a letter now is not what was sent (D14)
        letter = store.get_or_create(db, cv.user_id, found.posting_id, Kind.letter, at=at)
        if _has_version(db, letter) and not asked:
            return None  # the person's letter is not written again behind their back
        figures = {
            k: v
            for k, v in parent.estimate.items()
            if k not in (LETTER_FOLLOWS, LETTER_SCRIPT, WRITE_TEMPLATE)
        }
        reason = writer_base.wait_reason(db, cv.user_id)
        ceiling = letter_ceiling(db, parent)
        try:
            generation = create(
                db,
                letter,
                GenerationMode.posting,
                parent.trigger,
                parent.base_document_id or cv.base_document_id,
                figures,
                ceiling or 0,
                at=at,
                status=GenerationStatus.waiting if reason else GenerationStatus.queued,
                wait_reason=reason,
            )
        except store.GenerationOpenError:
            db.rollback()
            return None
        if ceiling is None:
            no_budget(db, generation, at)
            store.commit(db)
            return None
        started: uuid.UUID | None = None
        # The person's runs all busy: the letter stays `queued` and the tick starts it.
        with contextlib.suppress(agent_runs.ActiveRunsError):
            if reason is None:
                started = start(
                    db,
                    generation,
                    at=at,
                    script=parent.estimate.get(LETTER_SCRIPT),
                    settings=settings,
                ).id
        store.commit(db)
        return started


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
    if final.agent not in (writer_cv.NAME, writer_letter.NAME, writer_proposal.NAME):
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


class FitRefusedError(Exception):
    """Why no shortening pass starts: `reason` is one of FIT_REFUSALS."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def shortening_ceiling(db: Session, parent: DocumentGeneration) -> int | None:
    """What a shortening pass after the write `parent` may spend (module docstring, 4 and 5):
    what the pair has left after the CV's run (`letter_ceiling`), less what every shortening
    pass since that write spent, never under zero. None when one of those counts is missing
    (no run, or a count the runner did not write): no pass then."""
    left = letter_ceiling(db, parent)
    if left is None:
        return None
    passes = db.execute(
        sa.select(AgentRun.input_tokens, AgentRun.output_tokens)
        .select_from(DocumentGeneration)
        .outerjoin(AgentRun, AgentRun.id == DocumentGeneration.run_id)
        .where(
            DocumentGeneration.document_id == parent.document_id,
            DocumentGeneration.mode == GenerationMode.shorten,
            DocumentGeneration.created_at >= parent.created_at,
        )
    ).all()
    for spent_in, spent_out in passes:
        if spent_in is None or spent_out is None:
            return None
        left -= spent_in + spent_out
    return max(0, left)


def _pass_ceiling(
    db: Session, user_id: uuid.UUID, parent: DocumentGeneration | None
) -> tuple[DocumentGeneration, int]:
    """The write a shortening pass draws on and its ceiling, else FitRefusedError: the consent
    and a credential first (`writer_base.wait_reason`), then a write to draw on and a known
    ceiling left."""
    from app.documents import writer_base  # writer_base imports this module

    waiting = writer_base.wait_reason(db, user_id)
    if waiting is not None:
        raise FitRefusedError(waiting.value)
    if parent is None:
        raise FitRefusedError("not_written")
    ceiling = shortening_ceiling(db, parent)
    if ceiling is None:
        raise FitRefusedError("spend_unknown")
    if ceiling == 0:
        raise FitRefusedError("token_ceiling")
    return parent, ceiling


def shorten(
    sessions: sessionmaker[Session],
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    at: datetime,
    settings: Settings,
) -> uuid.UUID | None:
    """Create and start the one `shorten` generation of an overflowing version that a writer's
    `posting` generation made, with the consent, a credential and a ceiling left (module
    docstring, 4). Returns the run to enqueue, else None."""
    with sessions() as db:
        found = db.execute(
            sa.select(Document.user_id, Document.posting_id).where(Document.id == document_id)
        ).one_or_none()
        if found is None or found.posting_id is None:
            return None
        owner = found.user_id
        # Lock 2 before lock 3 (store.py): the run's row references the posting.
        store.lock_posting(db, owner, found.posting_id)
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
        try:
            _, ceiling = _pass_ceiling(db, owner, parent)
        except FitRefusedError as refused:
            logger.info(
                "documents.generation.shorten_skipped",
                extra={"document_id": str(document_id), "reason": refused.reason},
            )
            return None
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
                ceiling,
                at=at,
            )
            run = start(db, generation, at=at, long_fields=fields, settings=settings)
        except (store.GenerationOpenError, agent_runs.ActiveRunsError):
            db.rollback()
            return None
        store.commit(db)
        return run.id


def _latest_version(db: Session, document: Document) -> DocumentVersion | None:
    found: DocumentVersion | None = db.execute(
        sa.select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.number.desc())
        .limit(1)
    ).scalar_one_or_none()
    return found


def make_fit(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    at: datetime,
    settings: Settings | None = None,
) -> tuple[DocumentGeneration, AgentRun]:
    """The `shorten` generation of "Make it fit" (module docstring, 5) for a posting CV whose
    latest version overflows, started. The caller commits, then `enqueue_run`. Raises
    store.NotFoundError for a document that is not the person's, store.GenerationOpenError while
    one is open, FitRefusedError with its reason otherwise."""
    found = store.get(db, user_id, document_id)
    if found is None:
        raise store.NotFoundError(document_id)
    if found.scope != Scope.posting or found.kind != Kind.cv or found.posting_id is None:
        raise FitRefusedError("not_shortened")
    # Lock 2 before lock 3 (store.py): the run's row references the posting.
    if store.lock_posting(db, user_id, found.posting_id) is None:
        raise store.NotFoundError(document_id)
    document = store.lock_document(db, user_id, document_id)
    current = store.open_generation(db, user_id, document_id)
    if current is not None:
        raise store.GenerationOpenError(current)
    latest = _latest_version(db, document)
    drawn = c.content_hash(document.kind, c.parse(document.kind, document.draft))
    if latest is None or latest.fit is None or latest.content_hash != drawn:
        raise FitRefusedError("not_drawn")  # the page the person sees is not this draft's yet
    if latest.fit is FitResult.one_page:
        raise FitRefusedError("fits")
    parent, ceiling = _pass_ceiling(db, user_id, last_write(db, document))
    template = render_job.template_of(db, user_id, document.template_slug)
    fields = long_fields(c.parse("cv", latest.content), template.kinds["cv"].soft_limits)
    generation = create(
        db,
        document,
        GenerationMode.shorten,
        GenerationTrigger.click,
        parent.base_document_id,
        parent.estimate,
        ceiling,
        at=at,
    )
    try:
        run = start(db, generation, at=at, long_fields=fields, settings=settings)
    except agent_runs.ActiveRunsError:
        raise FitRefusedError("runs_busy") from None
    logger.info(
        "documents.generation.fit",
        extra={"generation_id": str(generation.id), "run_id": str(run.id)},
    )
    return generation, run


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
