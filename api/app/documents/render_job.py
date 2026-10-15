"""The render queue of the documents module (task D6; spec, "Rendering on the Jetson", "Versions
and freezing", "Failure modes").

A render turns a document's draft at one revision into a stored PDF. Who asks for one calls
`mark_queued` ("Render", bounded per person) or `mark_edited` (a save, a restore, a language or
template switch: never refused, so the edit is never lost) in its own transaction, then
`enqueue` after the commit, or `queue_render` for both (D7's `generate.finish`). The job,
`render_document(document_id, revision)` on the queue `jobapp:documents` (app/worker.py), takes
ids only and returns None:

1. **Claim** (one transaction, the document locked): the render still asked for is this one
   (`render_revision` is the revision and the state `queued`), else it was replaced by a later
   request and the job ends; a revision older than the draft is skipped. The draft is
   snapshotted (`store.snapshot`, which reuses the latest version when nothing changed) with
   the records of what the template cannot print (`templates.printable` on the content and
   `printable_profile` on the profile's own text): they are stored with the version, for the
   person to see. A version that already has its PDF is not rendered again (a retried job, a
   second click), and a draft equal to a frozen latest version keeps that version and its PDF
   byte for byte: a frozen version is never rendered again. The state becomes `rendering`.
2. **Render**, outside any transaction: the template's density steps through the fit loop
   (`fit.fit`) and the renderer client, at most DOCUMENTS_RENDERS_AT_ONCE at once per process
   (render_client's gate; the queue's own worker runs one job at a time). The renderer down or
   restarting (`renderer_unavailable`) is tried once more after RETRY_AFTER_S; a `timeout`, a
   `too_large` or a `render_failed` is not, being the same on a second try.
3. **Store** (one transaction): the PDF on the version with its fit, step and pages; the state
   back to `idle` unless a newer render was asked meanwhile (a save during a render: that one
   stays `queued`; a language or template switch, which keeps the draft revision: `switch`
   puts the state back to `queued` and the same job renders the switched version next, since
   the job id of the revision is its own until it ends). The first version of a posting's CV
   that fits one page moves the posting from `found` or `shortlisted` to `cv_ready` in the same
   transaction (`lock_job` first, the lock order of store.py, then `set_status` with `via:
   "documents"`). A posting locked by another transaction is tried once more, then the PDF is
   stored without the move and the skip is logged. An overflow stores its PDF all the same
   (never a refusal) and calls `on_overflow`.

A render that fails puts the state back to `idle` with a code of `ERRORS` in `render_error`,
which the api turns into a plain sentence (`MESSAGES`); nothing raises to arq. A worker stopped
mid render puts it back as `interrupted` (on the cancellation, and at the next start for a
process that died: `reset_interrupted`). A queued render whose job was lost is queued again by
`resend_queued`, a cron every minute; the job id (app.queue.render_job_id) makes that harmless.

Logs carry ids, revisions, version numbers, sizes, durations and codes, never content.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.documents import content as c
from app.documents import fit as fitting
from app.documents import freeze, profile_view, render_client, store, templates
from app.documents import photo as photos
from app.documents.corrections import Correction
from app.documents.models import (
    Author,
    Document,
    DocumentVersion,
    FitResult,
    Kind,
    Language,
    Origin,
    RenderState,
    Scope,
)
from app.documents.render_client import Rendered, RenderError
from app.documents.sources import DocumentSources, PostingView, sources_for
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus
from app.profile import service as profile_service

logger = logging.getLogger("app.documents.render")

FUNCTION = "render_document"
# Renders waiting per person, across their documents (each document has one at most): the
# queue is one renderer's, and one person's burst never fills it for everybody.
QUEUED_PER_USER = 10
BOUND_LOCK = "documents.render_bound"
ATTEMPTS = 2  # for RETRIED only
FOLLOW_UPS = 1  # renders of a version switched during the job's render, in the same job
RETRIED = frozenset({"renderer_unavailable"})
RETRY_AFTER_S = 2.0
# The longest a job may take: a fit of eight steps walked one by one, each render at its
# timeout plus the client's margin, tried twice, and the two transactions; then all of it once
# more for a version switched during it (FOLLOW_UPS).
JOB_TIMEOUT_S = (1 + FOLLOW_UPS) * (8 * ATTEMPTS * (30 + render_client.MARGIN_S) + 60)
RESEND_BATCH = 100
MOVES_FROM = frozenset({JobStatus.found, JobStatus.shortlisted})
ERRORS = (*render_client.CODES, "interrupted")
UNAVAILABLE = "Preview not available right now."
MESSAGES = {
    "renderer_unavailable": UNAVAILABLE,
    "timeout": UNAVAILABLE,
    "too_large": "The PDF is over the size limit. Try a smaller photo or a shorter text.",
    "render_failed": "The preview could not be made. Try again.",
    "interrupted": "The preview was interrupted. Try again.",
}
# What a version made by a render records as its origin, by who wrote the draft.
ORIGINS = {
    Author.user: Origin.edited,
    Author.writer: Origin.generated,
    Author.chat: Origin.chat,
    Author.correction: Origin.edited,
    Author.restore: Origin.restored,
    Author.import_: Origin.legacy,
    Author.upload: Origin.uploaded,
}

Render = Callable[[str], Awaitable[Rendered]]


class RenderQueueFullError(Exception):
    """QUEUED_PER_USER renders of this person already wait (the api's 429)."""


class Enqueuer(Protocol):
    async def enqueue(self, function: str, *args: Any, queue: str, job_id: str) -> bool: ...


class PoolQueue:
    """The worker's arq pool behind the api Queue's `enqueue` (D7's `generate.finish`, the
    cron that queues a lost render again)."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def enqueue(self, function: str, *args: Any, queue: str, job_id: str) -> bool:
        job = await self.pool.enqueue_job(function, *args, _job_id=job_id, _queue_name=queue)
        return job is not None


async def on_overflow(document_id: uuid.UUID, version_id: uuid.UUID) -> None:
    """Called once after a render whose PDF overflows at the tightest step was stored, outside
    any transaction. A no op here: D7 sets `render_job.on_overflow` to its own function (sync
    or async), which starts the one automatic `shorten` pass. D6 never imports a writer."""
    return None


# --- what a render reads ------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    """What a document is rendered and checked with, read in one transaction."""

    template: templates.Template
    kind: str  # the template's kind: "cv" (a base CV too) or "letter"
    content: c.CvContent | c.LetterContent
    view: profile_view.ProfileView
    base: c.BaseCvContent | None
    posting: PostingView | None
    photo: str | None
    corrections: list[Correction]  # what the template cannot print, content then profile


def content_kind(document: Document) -> c.Kind:
    return "base" if document.scope == Scope.base else document.kind.value  # type: ignore[return-value]


def template_of(db: Session, user_id: uuid.UUID, slug: str) -> templates.Template:
    """The document's template; one that disappeared (a person's own, deleted) renders with
    the default rather than not at all."""
    found = templates.resolve(db, user_id, slug)
    return found if found is not None else templates.registry()[templates.DEFAULT]


def _base_of(db: Session, document: Document, content: c.CvContent | c.LetterContent) -> Any:
    if document.scope == Scope.base:
        # Its fixed block only: a base CV's own sentences never source themselves, so a claim
        # added by hand stays a note at every save (D9).
        fixed = content.fixed if isinstance(content, c.BaseCvContent) else c.Fixed()
        return c.BaseCvContent(fixed=fixed)
    if document.base_document_id is None:
        return None
    base = store.get(db, document.user_id, document.base_document_id)
    return c.parse("base", base.draft) if base is not None else None


def read_context(db: Session, document: Document, at: datetime) -> Context:
    user_id = document.user_id
    content = c.parse(content_kind(document), document.draft)
    base = _base_of(db, document, content)
    profile = profile_service.get_profile(db, user_id, at)
    view = profile_view.build(profile_service.sections_view(profile), profile.revision, base)
    template = template_of(db, user_id, document.template_slug)
    kind = "letter" if document.kind == Kind.letter else "cv"
    posting = None
    if document.posting_id is not None:
        row = db.execute(
            sa.select(Job.company, Job.title).where(
                Job.user_id == user_id, Job.id == document.posting_id
            )
        ).one_or_none()
        posting = PostingView(company=row[0] or "", title=row[1] or "") if row else None
    photo = None
    if template.photo != "none":
        stored = profile_service.get_photo(db, user_id)
        photo = photos.embed(bytes(stored[0])) if stored is not None else None
    _, own = templates.printable(template, kind, content)
    _, profiles = templates.printable_profile(template, view)
    return Context(template, kind, content, view, base, posting, photo, [*own, *profiles])


def sources_of(context: Context, document: Document) -> DocumentSources:
    """The fact check sources of a hand edit of this document (its notes)."""
    kind = content_kind(document)
    return sources_for(context.view, context.base, None, context.posting, kind=kind)


# --- asking for a render ------------------------------------------------------------------------


def mark_queued(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    revision: int | None = None,
    bounded: bool = True,
) -> tuple[Document, bool]:
    """Ask for a render of the draft, in the caller's transaction: `(document, fresh)`, where
    `fresh` says a job must be enqueued after the commit. The same revision asked again while
    it waits or renders is one render (not fresh); a later revision replaces a waiting one. A
    `revision` older than the draft asks for nothing. `bounded` refuses the QUEUED_PER_USER+1st
    waiting render of a person (RenderQueueFullError), counted under the person's bound lock.
    Only "Render" is bounded: an edit uses `mark_edited`, and a writer's result is never
    refused."""
    document = store.lock_document(db, user_id, document_id)
    current = document.draft_revision
    if revision is not None and revision != current:
        return document, False
    if document.render_revision == current and document.render_state != RenderState.idle:
        return document, False
    if bounded and document.render_state == RenderState.idle:
        _lock_bound(db, user_id)
        if _others_waiting(db, user_id, document_id) >= QUEUED_PER_USER:
            raise RenderQueueFullError(document_id)
    document.render_state = RenderState.queued
    document.render_revision = current
    document.render_error = None
    db.flush()
    return document, True


def mark_edited(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> tuple[Document, bool]:
    """`mark_queued` for an edit (a save, a restore, a language or template switch), in the
    edit's own transaction. Never refused, since a refusal would roll the edit back, and the
    spec's one limit is one waiting render per document. When QUEUED_PER_USER renders of the
    person already wait elsewhere, `fresh` is False: the render stays `queued` and
    `resend_queued` sends it within a minute, behind the others, rather than now."""
    full = _others_waiting(db, user_id, document_id) >= QUEUED_PER_USER
    document, fresh = mark_queued(db, user_id, document_id, bounded=False)
    if fresh and full:
        logger.info("documents.render.deferred", extra={"document_id": str(document_id)})
        return document, False
    return document, fresh


def _others_waiting(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> int:
    query = (
        sa.select(sa.func.count())
        .select_from(Document)
        .where(
            Document.user_id == user_id,
            Document.id != document_id,
            Document.render_state == RenderState.queued,
        )
    )
    return int(db.execute(query).scalar_one())


def _lock_bound(db: Session, user_id: uuid.UUID) -> None:
    """The person's render bound, held to the commit, so two asks on two documents count one
    after the other. An advisory lock taken last (after lock 3 of store.py, the document), so
    its holder never waits on another lock; it waits at most the lock timeout the document's
    lock set, then raises store.DocumentBusyError."""
    try:
        db.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{BOUND_LOCK}:{user_id}"},
        )
    except sa.exc.DBAPIError as exc:
        if store.is_busy(exc):
            raise store.DocumentBusyError(store.BUSY) from exc
        raise


async def enqueue(queue: Enqueuer, document_id: uuid.UUID, revision: int) -> bool:
    """Queue the job, after the commit of `mark_queued`. False when that render is already
    queued. Raises queues.QueueUnavailableError; the row stays `queued` and `resend_queued`
    queues it within a minute."""
    return await queue.enqueue(
        FUNCTION,
        str(document_id),
        revision,
        queue=queues.DOCUMENTS,
        job_id=queues.render_job_id(document_id, revision),
    )


async def queue_render(
    document_id: uuid.UUID,
    revision: int,
    *,
    sessions: sessionmaker[Session],
    queue: Enqueuer,
) -> bool:
    """A render of the draft at `revision` (D7's `generate.finish`, after its commit): marks it
    and queues it. False when nothing was queued (the same render waits already, or the draft
    moved on and its own render was asked by whoever moved it)."""

    def mark() -> bool:
        with sessions() as db:
            owner = _owner(db, document_id)
            if owner is None:
                return False
            _, fresh = mark_queued(db, owner, document_id, revision=revision, bounded=False)
            store.commit(db)
            return fresh

    if not await asyncio.to_thread(mark):
        return False
    return await enqueue(queue, document_id, revision)


# --- switches that make a version -----------------------------------------------------------------


def switch(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    at: datetime,
    language: Language | None = None,
    template_slug: str | None = None,
) -> DocumentVersion | None:
    """Change the language or the template: a new version of the draft (origin `template` for
    a template switch, `edited` for a language), with what the new template cannot print
    recorded on it; a frozen version is never touched (the snapshot makes a new one after it).
    An unknown template, or another person's own, is `store.TemplateUnknownError`. None when
    nothing changed. The caller then asks for its render."""
    if template_slug is not None and templates.resolve(db, user_id, template_slug) is None:
        raise store.TemplateUnknownError(template_slug)
    document = store.lock_document(db, user_id, document_id)
    origin = None
    if language is not None and language != document.language:
        store.set_language(db, user_id, document_id, language, at)
        origin = Origin.edited
    if template_slug is not None and template_slug != document.template_slug:
        document.template_slug = template_slug
        document.updated_at = at
        db.flush()
        origin = Origin.template
    if origin is None:
        return None
    if document.render_state == RenderState.rendering:
        # The render in progress is of this revision with the old look: back to `queued`, so
        # its store leaves the state for the render of the new version, which the same job
        # makes next (`_render`).
        document.render_state = RenderState.queued
        db.flush()
    context = read_context(db, document, at)
    version, _ = store.snapshot(
        db,
        user_id,
        document_id,
        origin,
        at=at,
        corrections=context.corrections,
        template_version=context.template.version,
    )
    return version


# --- the job ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    user_id: uuid.UUID
    document_id: uuid.UUID
    revision: int
    number: int  # the version rendered
    version_id: uuid.UUID
    posting_id: uuid.UUID | None
    is_cv: bool
    steps: tuple[templates.Step, ...]
    build: Callable[[templates.Step], str]


def _owner(db: Session, document_id: uuid.UUID) -> uuid.UUID | None:
    query = sa.select(Document.user_id).where(Document.id == document_id)
    owner: uuid.UUID | None = db.execute(query).scalar_one_or_none()
    return owner


def _latest(db: Session, document: Document) -> DocumentVersion | None:
    query = (
        sa.select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.number.desc())
        .limit(1)
    )
    found: DocumentVersion | None = db.execute(query).scalar_one_or_none()
    return found


def _idle(document: Document, error: str | None = None) -> None:
    document.render_state = RenderState.idle
    document.render_error = error


def _claim(
    sessions: sessionmaker[Session], document_id: uuid.UUID, revision: int, at: datetime
) -> Claim | str:
    with sessions() as db:
        owner = _owner(db, document_id)
        if owner is None:
            return "gone"
        document = store.lock_document(db, owner, document_id)
        if document.render_revision != revision or document.render_state != RenderState.queued:
            return "replaced"
        if document.draft_revision != revision:
            _idle(document)
            store.commit(db)
            return "stale"
        context = read_context(db, document, at)
        kind = content_kind(document)
        latest = _latest(db, document)
        if (
            latest is not None
            and latest.frozen_at is not None
            and latest.pdf_sha256 is not None
            and latest.content_hash == c.content_hash(kind, context.content)
            and latest.language == document.language
            and latest.template_slug == document.template_slug
            and latest.template_version == context.template.version
        ):
            _idle(document)
            store.commit(db)
            return "frozen"
        version, _ = store.snapshot(
            db,
            owner,
            document_id,
            ORIGINS.get(document.draft_written_by, Origin.edited),
            at=at,
            corrections=context.corrections,
            template_version=context.template.version,
        )
        if version.pdf_sha256 is not None:
            _idle(document)
            store.commit(db)
            return "rendered"
        document.render_state = RenderState.rendering
        document.render_error = None
        claim = Claim(
            user_id=owner,
            document_id=document_id,
            revision=revision,
            number=version.number,
            version_id=version.id,
            posting_id=document.posting_id if document.scope == Scope.posting else None,
            is_cv=document.kind == Kind.cv,
            steps=context.template.steps(context.kind),
            build=_builder(context, document.language.value, at.date()),
        )
        store.commit(db)
        return claim


def _builder(context: Context, language: str, rendered_on: date) -> Callable[[Any], str]:
    def build(step: templates.Step) -> str:
        return templates.build_html(
            context.template,
            context.kind,
            context.content,
            context.view,
            language,
            step,
            rendered_on,
            photo=context.photo,
        )

    return build


def _retrying(render: Render, retry_after_s: float) -> Render:
    async def once_more(html: str) -> Rendered:
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return await render(html)
            except RenderError as error:
                if error.code not in RETRIED or attempt == ATTEMPTS:
                    raise
                logger.info("documents.render.retry", extra={"code": error.code})
                await asyncio.sleep(retry_after_s)
        raise AssertionError("unreachable")

    return once_more


class _PostingBusyError(Exception):
    pass


@dataclass(frozen=True)
class Stored:
    outcome: str  # "one_page", "overflow", or a code of ERRORS
    moved: bool = False
    again: bool = False  # a switch during the render: the same revision is queued again


def _store_once(
    db: Session, claim: Claim, fitted: fitting.Fitted, at: datetime, move: bool
) -> Stored:
    job = None
    if move and fitted.fit and claim.is_cv and claim.posting_id is not None:
        try:
            job = jobs_service.lock_job(db, claim.user_id, claim.posting_id)
        except jobs_service.JobBusyError as error:
            if isinstance(error, store.DocumentBusyError):
                raise
            raise _PostingBusyError from None
    document = store.lock_document(db, claim.user_id, claim.document_id)
    mine = document.render_revision == claim.revision
    current = mine and document.render_state == RenderState.rendering
    again = mine and document.render_state == RenderState.queued
    try:
        version = store.attach_pdf(
            db,
            claim.user_id,
            claim.document_id,
            claim.number,
            fitted.pdf,
            fit=FitResult.one_page if fitted.fit else FitResult.overflow,
            fit_step=fitted.step,
            pages=fitted.pages,
        )
    except store.PdfTooLargeError:
        if current:
            _idle(document, "too_large")
        store.commit(db)
        return Stored("too_large", again=again)
    except (store.FrozenVersionError, store.NotFoundError):
        # Frozen or pruned while it rendered: the PDF of record stays what it was.
        if current:
            _idle(document)
        store.commit(db)
        return Stored("replaced", again=again)
    if current:
        _idle(document)
    freeze.on_rendered(db, document, at)
    moved = False
    if job is not None and job.status in MOVES_FROM:
        earlier = db.execute(
            sa.select(sa.func.count())
            .select_from(DocumentVersion)
            .where(
                DocumentVersion.document_id == claim.document_id,
                DocumentVersion.fit == FitResult.one_page,
                DocumentVersion.id != version.id,
            )
        ).scalar_one()
        if earlier == 0:
            moved = jobs_service.set_status(db, job, JobStatus.cv_ready, at, via="documents")
    store.commit(db)
    return Stored("one_page" if fitted.fit else "overflow", moved, again)


def _store(
    sessions: sessionmaker[Session], claim: Claim, fitted: fitting.Fitted, at: datetime
) -> Stored:
    for _ in range(2):
        with sessions() as db:
            try:
                return _store_once(db, claim, fitted, at, move=True)
            except _PostingBusyError:
                db.rollback()
    logger.warning(
        "documents.render.status_skipped",
        extra={"document_id": str(claim.document_id), "reason": "posting_busy"},
    )
    with sessions() as db:
        return _store_once(db, claim, fitted, at, move=False)


def _fail(
    sessions: sessionmaker[Session],
    document_id: uuid.UUID,
    revision: int,
    code: str,
    *,
    rendering_only: bool = False,
) -> None:
    """The render of `revision` ends with `code`. `rendering_only` (a claimed render, or a stop
    of the worker) keeps a `queued` state: a switch asked for another render meanwhile, or
    the job never claimed it, and resend_queued sends it again."""
    with sessions() as db:
        owner = _owner(db, document_id)
        if owner is None:
            return
        document = store.lock_document(db, owner, document_id)
        waiting = RenderState.queued if rendering_only else RenderState.idle
        if document.render_revision == revision and document.render_state not in (
            RenderState.idle,
            waiting,
        ):
            _idle(document, code)
        store.commit(db)


async def _render_once(ctx: dict[str, Any], document_id: uuid.UUID, revision: int) -> Stored:
    sessions: sessionmaker[Session] = ctx["sessions"]
    clock: Callable[[], datetime] = ctx["clock"]
    try:
        claim = await asyncio.to_thread(_claim, sessions, document_id, revision, clock())
    except store.DocumentBusyError:
        return Stored("busy")  # still queued: resend_queued queues it again
    if isinstance(claim, str):
        return Stored(claim)
    render = _retrying(
        ctx.get("documents_render") or render_client.render,
        ctx.get("documents_retry_after_s", RETRY_AFTER_S),
    )
    try:
        fitted = await fitting.fit(claim.steps, claim.build, render=render)
    except RenderError as error:
        await asyncio.to_thread(
            _fail, sessions, document_id, revision, error.code, rendering_only=True
        )
        return Stored(error.code)
    stored = await asyncio.to_thread(_store, sessions, claim, fitted, clock())
    if stored.outcome == "overflow":
        called = on_overflow(document_id, claim.version_id)
        if inspect.isawaitable(called):
            await called
    return stored


async def _render(ctx: dict[str, Any], document_id: uuid.UUID, revision: int) -> str:
    """One render, then, while a language or template switch during it left the same revision
    `queued`, the render of the switched version (at most FOLLOW_UPS more, the rest is the
    cron's): the job id of that revision is still this job's, so a new job could not be queued
    before this one ends."""
    outcomes = []
    for _ in range(1 + FOLLOW_UPS):
        stored = await _render_once(ctx, document_id, revision)
        outcomes.append(stored.outcome + (":moved" if stored.moved else ""))
        if not stored.again:
            break
    return ",".join(outcomes)


async def render_document(ctx: dict[str, Any], document_id: str, revision: int) -> None:
    """The arq job. Nothing is raised to arq, which would log and keep the exception."""
    started = time.monotonic()
    found = uuid.UUID(document_id)
    try:
        outcome = await _render(ctx, found, int(revision))
    except asyncio.CancelledError:
        # A stop of the worker, or arq's own timeout: the person sees an interrupted preview.
        await asyncio.shield(
            asyncio.to_thread(
                _fail, ctx["sessions"], found, int(revision), "interrupted", rendering_only=True
            )
        )
        raise
    except Exception as error:
        # No traceback: it could print a value.
        logger.error(
            "documents.render.error",
            extra={"document_id": document_id, "error": type(error).__name__},
        )
        try:
            await asyncio.to_thread(_fail, ctx["sessions"], found, int(revision), "render_failed")
        except Exception as again:
            logger.error(
                "documents.render.error",
                extra={"document_id": document_id, "error": type(again).__name__},
            )
        outcome = "error"
    logger.info(
        "documents.render.job",
        extra={
            "document_id": document_id,
            "revision": int(revision),
            "outcome": outcome,
            "ms": int((time.monotonic() - started) * 1000),
        },
    )


# --- the worker's housekeeping ------------------------------------------------------------------


def reset_interrupted(sessions: sessionmaker[Session]) -> int:
    """Every render left `rendering` by a process that died goes back to `idle`, `interrupted`.
    Run once at the worker's start, before any job: none of them can be running then."""
    with sessions() as db:
        done = db.execute(
            sa.update(Document)
            .where(Document.render_state == RenderState.rendering)
            .values(render_state=RenderState.idle, render_error="interrupted")
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return int(done.rowcount or 0)  # type: ignore[attr-defined]


async def reset_interrupted_at_start(ctx: dict[str, Any]) -> int:
    count = await asyncio.to_thread(reset_interrupted, ctx["sessions"])
    if count:
        logger.info("documents.render.reset", extra={"count": count})
    return count


def _waiting(sessions: sessionmaker[Session]) -> list[tuple[uuid.UUID, int]]:
    with sessions() as db:
        rows = db.execute(
            sa.select(Document.id, Document.render_revision)
            .where(
                Document.render_state == RenderState.queued,
                Document.render_revision.is_not(None),
            )
            .order_by(Document.updated_at)
            .limit(RESEND_BATCH)
        ).all()
    return [(row[0], int(row[1])) for row in rows]


async def resend_queued(ctx: dict[str, Any]) -> int:
    """The cron: every render marked `queued` whose job is not in Redis (lost, refused while
    Redis was down, or its claim found the document busy) is queued again. A job still there
    keeps its id, so it is never queued twice. Answers a count, nothing else."""
    pool = ctx.get("redis")
    if pool is None:
        return 0
    queue = PoolQueue(pool)
    sent = 0
    for document_id, revision in await asyncio.to_thread(_waiting, ctx["sessions"]):
        if await enqueue(queue, document_id, revision):
            sent += 1
    if sent:
        logger.info("documents.render.resent", extra={"count": sent})
    return sent
