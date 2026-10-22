"""Sent versions are frozen (task D14; spec, "Versions and freezing", Decision 8).

A version that went to a company is what the company received: it is never edited, rendered
again, re-templated or deleted on its own (the trigger of 0015 refuses it below the code). This
module is the one place that decides which version that is, and what the apply module reads:

- `on_status`: registered with the jobs service when this module is imported, and called inside
  `set_status`, under the posting's row lock, after the event is written. A posting reaching
  `applied` by any path (the job page, a bulk change, the undo of a later status, a mail's
  status change, the apply module) freezes, per document, the approved version, else the latest
  rendered one, with the reason `applied`. A document that already has a frozen version keeps
  it: the sent version is the fact, and a status that goes back to `applied` after an `interview`
  or an undo freezes nothing more, so a later edit never becomes "what was sent". Leaving
  `applied` freezes and unfreezes nothing. The freeze commits or rolls back with the status.
- `on_rendered`: the approved version whose PDF is still on its way when the posting reaches
  `applied` is the one to freeze, not an older rendered one, and the trigger of 0015 refuses a
  PDF on a frozen row. So `on_status` leaves that document, and the render job calls this after
  it stores a PDF, under the document's lock: when the version just stored is the approved one,
  the posting is `applied` and nothing of the document is frozen, it freezes that version. The
  render of any other version freezes nothing: a version not approved never becomes what was
  sent by landing, and an approval given after `applied` freezes only when the render of that
  version lands after it.
- `current`: what the apply module sends, the approved version else the latest rendered one,
  of the posting's CV and letter, with the reason a document is not there yet and whether the
  approved version is still rendering (the apply module waits for it rather than fill an older
  one).
- `freeze`: the apply module attaches a version to a submission. It never refuses on content
  (an overflowing version, one with corrections, one with notes), and returns the version's fit
  and corrections for the run report. Freezing a frozen version changes nothing.
- `pdf`: the bytes of a version, on demand (a list never loads them).

Imported by `freeze_router` (the api) and, as a registration module, by the worker, so the hook
is registered in both processes that move statuses. Logs carry ids, numbers and codes, never a
document's text.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, undefer

from app.documents import store
from app.documents.models import (
    Document,
    DocumentVersion,
    FitResult,
    FreezeReason,
    GenerationStatus,
    Kind,
    RenderState,
    Scope,
)
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobFile, JobStatus

logger = logging.getLogger("app.documents")

# Why a document is not there yet (`Slot.coming`): a generation waiting has its own
# `GenerationWait` value (daily_ceiling, consent, credential, base_cv); these two are the others.
WRITING = "writing"  # a generation is queued or running
RENDERING = "rendering"  # the draft is queued or being rendered


@dataclass(frozen=True)
class SentVersion:
    """One version as the apply module and the run report read it. `corrections` are the records
    stored with the version, `fit` is None while it has no PDF."""

    document_id: uuid.UUID
    version_id: uuid.UUID
    number: int
    kind: Kind
    approved: bool
    frozen: bool
    frozen_reason: FreezeReason | None
    frozen_at: datetime | None
    template_slug: str
    template_version: int
    language: str
    fit: FitResult | None
    fit_step: int | None
    pages: int | None
    corrections: tuple[dict[str, Any], ...]
    pdf_sha256: bytes | None


@dataclass(frozen=True)
class Slot:
    """One document of the posting. `version` is the approved version, else the latest rendered
    one, else None. `coming` says a document is still on its way (a `GenerationWait` value,
    `WRITING` or `RENDERING`), whether or not an older version is there: the apply module waits
    on `version is None and coming is not None`, ends `no_documents` on both None.
    `awaits_render`: the approved version has no PDF yet and a render of the document is queued
    or running, so `version` is an older one (or None); the apply module waits on it too."""

    version: SentVersion | None
    coming: str | None
    awaits_render: bool = False


@dataclass(frozen=True)
class Current:
    cv: Slot
    letter: Slot


def rendered(version: DocumentVersion) -> bool:
    """A PDF exists: rendered by the app, or the command line's file of an imported version."""
    return version.pdf_sha256 is not None or version.legacy_file_id is not None


def choose(versions: Sequence[DocumentVersion]) -> DocumentVersion | None:
    """The approved version, else the latest rendered one: the highest number of each. An
    approved version without a PDF is not a candidate (nothing could be sent)."""
    ready = sorted((v for v in versions if rendered(v)), key=lambda v: v.number, reverse=True)
    approved = [v for v in ready if v.approved_at is not None]
    if approved:
        return approved[0]
    return ready[0] if ready else None


def _sent(version: DocumentVersion, kind: Kind) -> SentVersion:
    return SentVersion(
        document_id=version.document_id,
        version_id=version.id,
        number=version.number,
        kind=kind,
        approved=version.approved_at is not None,
        frozen=version.frozen_at is not None,
        frozen_reason=version.frozen_reason,
        frozen_at=version.frozen_at,
        template_slug=version.template_slug,
        template_version=version.template_version,
        language=version.language.value,
        fit=version.fit,
        fit_step=version.fit_step,
        pages=version.pages,
        corrections=tuple(dict(record) for record in (version.corrections or [])),
        pdf_sha256=bytes(version.pdf_sha256) if version.pdf_sha256 is not None else None,
    )


def _coming(db: Session, user_id: uuid.UUID, document: Document) -> str | None:
    generation = store.open_generation(db, user_id, document.id)
    if generation is not None:
        if generation.status is GenerationStatus.waiting and generation.wait_reason is not None:
            return generation.wait_reason.value
        return WRITING
    if document.render_state in (RenderState.queued, RenderState.rendering):
        return RENDERING
    return None


def current(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> Current:
    """The posting's CV and letter as the apply module sends them. Reads the person's rows
    only: a posting of another person has no documents here. Reads and writes nothing else."""
    slots: dict[Kind, Slot] = {kind: Slot(None, None) for kind in Kind}
    for document in store.posting_documents(db, user_id, posting_id):
        versions = store.versions(db, user_id, document.id)
        picked = choose(versions)
        slots[document.kind] = Slot(
            version=_sent(picked, document.kind) if picked is not None else None,
            coming=_coming(db, user_id, document),
            awaits_render=_awaits_render(document, versions),
        )
    return Current(cv=slots[Kind.cv], letter=slots[Kind.letter])


def freeze(
    db: Session, user_id: uuid.UUID, version_id: uuid.UUID, reason: FreezeReason, at: datetime
) -> SentVersion:
    """Freeze the version of one of the person's posting documents (`store.NotFoundError` for
    any other id, a base CV's included). Never refuses on content: an overflow, corrections and
    notes freeze like a clean one, and the answer carries the fit and the corrections. A frozen
    version stays as it is, its first reason and time kept. Call it in the transaction that
    records the submission; the caller commits, and a rollback takes the freeze with it."""
    owner = db.execute(
        sa.select(DocumentVersion.document_id).where(
            DocumentVersion.id == version_id, DocumentVersion.user_id == user_id
        )
    ).scalar_one_or_none()
    document = store.get(db, user_id, owner) if owner is not None else None
    if document is None or document.scope is not Scope.posting:
        raise store.NotFoundError(version_id)
    return _sent(store.freeze(db, user_id, version_id, reason, at), document.kind)


def pdf(db: Session, user_id: uuid.UUID, version_id: uuid.UUID) -> bytes | None:
    """The PDF of a version of the person's own: the stored bytes, else the command line's file
    of an imported version, else None (not rendered yet, or not theirs)."""
    found = db.execute(
        sa.select(DocumentVersion)
        .where(DocumentVersion.id == version_id, DocumentVersion.user_id == user_id)
        .options(undefer(DocumentVersion.pdf))
    ).scalar_one_or_none()
    if found is None:
        return None
    if found.pdf is not None:
        return bytes(found.pdf)
    if found.legacy_file_id is None:
        return None
    legacy = db.execute(
        sa.select(JobFile)
        .join(Job, Job.id == JobFile.job_id)
        .where(JobFile.id == found.legacy_file_id, Job.user_id == user_id)
        .options(undefer(JobFile.content))
    ).scalar_one_or_none()
    return bytes(legacy.content) if legacy is not None else None


def on_status(db: Session, job: Job, previous: JobStatus, status: JobStatus, at: datetime) -> None:
    """The jobs service's hook: a posting that reaches `applied` freezes the version each of its
    documents would send. See the module text for the rules; nothing happens for any other
    status. Runs under the posting's row lock, so the document locks come after it, in the
    order of the store (`kind`: the CV, then the letter)."""
    if status is not JobStatus.applied:
        return
    frozen = 0
    for document in store.posting_documents(db, job.user_id, job.id):
        frozen += _freeze_applied(db, job.user_id, document.id, at)
    logger.info(
        "documents.freeze.applied",
        extra={
            "job_id": str(job.id),
            "user_id": str(job.user_id),
            "from": previous.value,
            "frozen": frozen,
        },
    )


def _awaits_render(document: Document, versions: Sequence[DocumentVersion]) -> bool:
    """The approved version has no PDF yet and a render of the document is queued or running."""
    approved = [v for v in versions if v.approved_at is not None]
    return (
        bool(approved)
        and not rendered(approved[0])
        and document.render_state in (RenderState.queued, RenderState.rendering)
    )


def _freeze_applied(db: Session, user_id: uuid.UUID, document_id: uuid.UUID, at: datetime) -> bool:
    """The hook's choice for one document of a posting that is `applied`: True when it froze."""
    document = store.lock_document(db, user_id, document_id)
    versions = store.versions(db, user_id, document_id)
    if any(v.frozen_at is not None for v in versions) or _awaits_render(document, versions):
        return False
    picked = choose(versions)
    if picked is None:
        return False
    store.freeze(db, user_id, picked.id, FreezeReason.applied, at)
    return True


def on_rendered(db: Session, document: Document, version: DocumentVersion, at: datetime) -> None:
    """The render job, after it stored a PDF on `version` of `document`, under the document's
    lock and in the same transaction: see the module text. Only an approved `version` can
    freeze, and then it is the hook's choice (the one approved version, now rendered). Reads
    the posting's status after that lock, and `on_status` takes the document's lock before it
    reads the versions, so of a status change and a stored PDF the one that commits second
    sees the other."""
    if document.scope is not Scope.posting or document.posting_id is None:
        return
    if version.approved_at is None:
        return
    # The sessions do not autoflush, and `lock_document` reloads the row: the caller's state first.
    db.flush()
    status = db.execute(
        sa.select(Job.status).where(Job.id == document.posting_id, Job.user_id == document.user_id)
    ).scalar_one_or_none()
    if status is not JobStatus.applied:
        return
    if _freeze_applied(db, document.user_id, document.id, at):
        logger.info(
            "documents.freeze.rendered",
            extra={"document_id": str(document.id), "user_id": str(document.user_id)},
        )


jobs_service.register_status_callback(on_status)
