"""The documents api that needs no writer (task D6; spec, "API").

Three routers, included by `routers.py` in its order: `settings_router` and `templates_router`
hold literal paths and come first; `router` holds `/api/documents/{document_id}` and what is
under it, and comes last, since FastAPI does not fall through to a later route when a path
segment fails its type.

Every route is the signed in person's: a document, a version or a posting of another person
answers 404 as if it did not exist (the store reads the person's rows only). Every answer is
`Cache-Control: private, no-store`, except a bundled template's thumbnail, which is public data
of the app. A PDF or a page image is read from the version row: nothing of the request reaches
the file system, and a thumbnail's file is the registry's, found by its slug.

Never blocked: a draft with notes is saved and rendered (the notes come back in the answer); an
overflow is stored. What can be refused is capacity (429, renders waiting) and a stale save
(409 with the current draft).
"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import anyio.from_thread
import sqlalchemy as sa
from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app import queue as queues
from app.db import get_session
from app.documents import content as c
from app.documents import corrections, render_client, render_job, store, templates
from app.documents.models import (
    Author,
    Document,
    DocumentVersion,
    FitResult,
    FreezeReason,
    GenerationStatus,
    GenerationWait,
    Kind,
    Language,
    Origin,
    RenderState,
    Scope,
)
from app.documents.settings import DocumentSettings
from app.identity import deps
from app.identity.models import User
from app.jobs.models import Job
from app.profile import service as profile_service

logger = logging.getLogger("app.documents")

PRIVATE = {"Cache-Control": "private, no-store"}
FILE_HEADERS = {**PRIVATE, "X-Content-Type-Options": "nosniff"}
THUMBNAIL_MAX_AGE_S = 86_400  # the template's version is in the address
PREVIEW_WIDTH = 600
PREVIEW_MIN, PREVIEW_MAX = 120, 1200
PREVIEWS_AT_ONCE = 2  # page images drawn at once in this process
NAME_MAX = 120

NOT_FOUND = "Document not found."
VERSION_NOT_FOUND = "Version not found."
POSTING_NOT_FOUND = "Job not found."
NO_PDF = "This version has no PDF yet."
TEMPLATE_NOT_FOUND = "Template not found."
UNKNOWN_TEMPLATE = "Unknown template."
STALE = "This document changed since you opened it. Here is the current draft."
NO_REVISION = "Say which revision you saved from (If-Match)."
BAD_REVISION = "If-Match must be the draft's revision, a number."
QUEUE_FULL = "Several previews are already waiting. Try again in a moment."
FROZEN = "This version was sent: it keeps its own mark."

_previews = threading.BoundedSemaphore(PREVIEWS_AT_ONCE)


def _private(response: Response) -> None:
    response.headers.update(PRIVATE)


Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

router = APIRouter(tags=["documents"], dependencies=[Depends(_private)])
settings_router = APIRouter(
    prefix="/api/documents", tags=["documents"], dependencies=[Depends(_private)]
)
templates_router = APIRouter(prefix="/api/document-templates", tags=["documents"])


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail, headers=PRIVATE)


# --- answers ------------------------------------------------------------------------------------


class RenderOut(BaseModel):
    state: RenderState
    revision: int | None
    error: str | None  # a code of render_job.ERRORS
    message: str | None  # what the person reads


class VersionSummary(BaseModel):
    number: int
    origin: Origin
    created_at: datetime
    language: Language
    template_slug: str
    template_version: int
    fit: FitResult | None
    fit_step: int | None
    pages: int | None
    has_pdf: bool
    approved_at: datetime | None
    frozen_at: datetime | None
    frozen_reason: FreezeReason | None
    corrections_count: int
    unprintable_count: int


class VersionOut(VersionSummary):
    content: dict[str, Any]
    corrections: list[dict[str, Any]]  # every record stored with the version
    unprintable: list[dict[str, Any]]  # those of them that replaced a character (not_printable)


class DocumentOut(BaseModel):
    id: uuid.UUID
    scope: Scope
    kind: Kind
    posting_id: uuid.UUID | None
    base_document_id: uuid.UUID | None
    name: str | None
    language: Language
    template_slug: str
    draft: dict[str, Any]
    draft_revision: int
    draft_written_by: Author
    draft_updated_at: datetime
    notes: list[dict[str, Any]]  # the checks of the draft at this revision
    render: RenderOut
    latest: VersionSummary | None


class GenerationOut(BaseModel):
    status: GenerationStatus
    wait_reason: GenerationWait | None


class PostingDocumentOut(DocumentOut):
    approved: int | None
    sent: list[int]
    generation: GenerationOut | None


class PostingDocumentsOut(BaseModel):
    documents: list[PostingDocumentOut]


class QueuedOut(BaseModel):
    render: RenderOut


class DocumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Language | None = None
    template_slug: Annotated[str, Field(max_length=64)] | None = None  # the column's bound


class TemplateOut(BaseModel):
    slug: str
    version: int
    user: bool
    name: dict[str, str]
    description: dict[str, str]
    tags: list[str]
    photo: str
    accent: str | None
    kinds: list[str]
    thumbnails: dict[str, str]  # kind -> address; empty for a person's own


class TemplatesOut(BaseModel):
    templates: list[TemplateOut]


# --- helpers ------------------------------------------------------------------------------------


def _render_out(document: Document) -> RenderOut:
    error = document.render_error
    return RenderOut(
        state=document.render_state,
        revision=document.render_revision,
        error=error,
        message=render_job.MESSAGES.get(error) if error else None,
    )


def _unprintable(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if record.get("code") == corrections.NOT_PRINTABLE]


def _summary(version: DocumentVersion) -> dict[str, Any]:
    records = list(version.corrections or [])
    return {
        "number": version.number,
        "origin": version.origin,
        "created_at": version.created_at,
        "language": version.language,
        "template_slug": version.template_slug,
        "template_version": version.template_version,
        "fit": version.fit,
        "fit_step": version.fit_step,
        "pages": version.pages,
        "has_pdf": version.pdf_sha256 is not None and version.legacy_file_id is None,
        "approved_at": version.approved_at,
        "frozen_at": version.frozen_at,
        "frozen_reason": version.frozen_reason,
        "corrections_count": len(records),
        "unprintable_count": len(_unprintable(records)),
    }


def _version_out(version: DocumentVersion) -> VersionOut:
    records = list(version.corrections or [])
    return VersionOut(
        **_summary(version),
        content=version.content,
        corrections=records,
        unprintable=_unprintable(records),
    )


def _fields(db: Session, document: Document) -> dict[str, Any]:
    found = store.versions(db, document.user_id, document.id)
    latest = max(found, key=lambda v: v.number) if found else None
    current = document.checks_revision == document.draft_revision
    return {
        "id": document.id,
        "scope": document.scope,
        "kind": document.kind,
        "posting_id": document.posting_id,
        "base_document_id": document.base_document_id,
        "name": document.name,
        "language": document.language,
        "template_slug": document.template_slug,
        "draft": document.draft,
        "draft_revision": document.draft_revision,
        "draft_written_by": document.draft_written_by,
        "draft_updated_at": document.draft_updated_at,
        "notes": list(document.checks or []) if current else [],
        "render": _render_out(document),
        "latest": VersionSummary(**_summary(latest)) if latest is not None else None,
    }


def _document_out(db: Session, document: Document) -> DocumentOut:
    return DocumentOut(**_fields(db, document))


def _own(db: Session, user: User, document_id: uuid.UUID) -> Document:
    document = store.get(db, user.id, document_id)
    if document is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return document


def _enqueue(request: Request, document_id: uuid.UUID, revision: int) -> None:
    """After the commit. Redis down is not the person's problem: the row stays `queued` and the
    worker's resend queues it."""
    try:
        anyio.from_thread.run(render_job.enqueue, request.app.state.queue, document_id, revision)
    except queues.QueueUnavailableError:
        logger.warning("documents.render.enqueue_failed", extra={"document_id": str(document_id)})


def _queue(request: Request, db: Session, user: User, document_id: uuid.UUID) -> Document:
    """Ask for a render of the draft and commit; enqueue when it is a new one."""
    try:
        document, fresh = render_job.mark_queued(db, user.id, document_id)
    except render_job.RenderQueueFullError:
        db.rollback()
        raise _error(status.HTTP_429_TOO_MANY_REQUESTS, QUEUE_FULL) from None
    revision = document.draft_revision
    store.commit(db)
    if fresh:
        _enqueue(request, document_id, revision)
    return document


def _checked(db: Session, document: Document, at: datetime) -> Any:
    return render_job.sources_of(render_job.read_context(db, document, at), document)


def ascii_name(*parts: str) -> str:
    """Words of ASCII letters and digits joined by `_`: accents folded, the rest dropped."""
    words: list[str] = []
    for part in parts:
        folded = unicodedata.normalize("NFKD", part).encode("ascii", "ignore").decode()
        words += re.findall(r"[A-Za-z0-9]+", folded)
    return "_".join(words)[:NAME_MAX].strip("_")


def file_name(db: Session, document: Document, at: datetime) -> str:
    """`<Name>_<Company>_CV.pdf`: the person's name and the posting's company (the base CV's
    name for a base CV), ASCII folded."""
    identity = profile_service.sections_view(profile_service.get_profile(db, document.user_id, at))
    full_name = str((identity.get("identity") or {}).get("full_name") or "")
    where = document.name or ""
    if document.posting_id is not None:
        where = (
            db.execute(
                sa.select(Job.company).where(
                    Job.user_id == document.user_id, Job.id == document.posting_id
                )
            ).scalar_one_or_none()
            or ""
        )
    what = "Letter" if document.kind == Kind.letter else "CV"
    return f"{ascii_name(full_name, where, what) or what}.pdf"


def _version(
    db: Session, user: User, document_id: uuid.UUID, number: int, pdf: bool = False
) -> DocumentVersion:
    found = store.version(db, user.id, document_id, number, pdf=pdf)
    if found is None:
        raise _error(status.HTTP_404_NOT_FOUND, VERSION_NOT_FOUND)
    return found


def _revision(value: str | None) -> int:
    if value is None:
        raise _error(status.HTTP_428_PRECONDITION_REQUIRED, NO_REVISION)
    text = value.strip().removeprefix("W/").strip('"')
    if not text.isdigit():
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, BAD_REVISION)
    return int(text)


# --- a posting's documents ----------------------------------------------------------------------


@router.get("/api/jobs/{job_id}/documents", response_model=PostingDocumentsOut)
def posting_documents(job_id: uuid.UUID, user: CurrentUser, db: Db) -> PostingDocumentsOut:
    """The posting's CV and letter, as far as they exist: state, latest version, notes, the
    approved and sent versions, the open generation and why it waits."""
    owned = db.execute(
        sa.select(Job.id).where(Job.user_id == user.id, Job.id == job_id)
    ).scalar_one_or_none()
    if owned is None:
        raise _error(status.HTTP_404_NOT_FOUND, POSTING_NOT_FOUND)
    out = []
    for document in store.posting_documents(db, user.id, job_id):
        found = store.versions(db, user.id, document.id)
        approved = [v.number for v in found if v.approved_at is not None]
        generation = store.open_generation(db, user.id, document.id)
        out.append(
            PostingDocumentOut(
                **_fields(db, document),
                approved=max(approved) if approved else None,
                sent=sorted(v.number for v in found if v.frozen_at is not None),
                generation=(
                    GenerationOut(status=generation.status, wait_reason=generation.wait_reason)
                    if generation is not None
                    else None
                ),
            )
        )
    return PostingDocumentsOut(documents=out)


# --- one document -------------------------------------------------------------------------------


@router.get("/api/documents/{document_id}", response_model=DocumentOut)
def read_document(document_id: uuid.UUID, user: CurrentUser, db: Db) -> DocumentOut:
    """The draft with its notes and its render state."""
    return _document_out(db, _own(db, user, document_id))


@router.put(
    "/api/documents/{document_id}/draft",
    response_model=DocumentOut,
    responses={409: {"description": "Stale revision: the current draft"}, 428: {}},
)
def save_draft(
    document_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    db: Db,
    content: Annotated[dict[str, Any], Body()],
    if_match: Annotated[str | None, Header()] = None,
) -> Any:
    """Save the draft by hand (`If-Match: <draft_revision>`) and ask for its render. Notes are
    computed and returned; nothing is refused for what the text says."""
    revision = _revision(if_match)
    document = _own(db, user, document_id)
    at = deps.clock(request)
    try:
        sources = _checked(db, document, at)
        document = store.save_draft(
            db,
            user.id,
            document_id,
            content,
            revision,
            Author.user,
            at=at,
            sources=sources,
            today=at.date(),
        )
    except store.StaleDraftError:
        db.rollback()
        current = _own(db, user, document_id)
        body = {"detail": STALE, "document": _document_out(db, current).model_dump(mode="json")}
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body, headers=PRIVATE)
    except c.ContentInvalidError as invalid:
        db.rollback()
        errors = [{"path": e.path, "code": e.code, "message": e.message} for e in invalid.errors]
        raise HTTPException(status_code=422, detail=errors, headers=PRIVATE) from None
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    document = _queue(request, db, user, document_id)
    return _document_out(db, document)


@router.patch("/api/documents/{document_id}", response_model=DocumentOut)
def patch_document(
    document_id: uuid.UUID, change: DocumentPatch, user: CurrentUser, request: Request, db: Db
) -> DocumentOut:
    """Change the language or the template: a new version, rendered. A frozen version stays as
    it is. An unknown template, or another person's own, is 422."""
    _own(db, user, document_id)
    try:
        render_job.switch(
            db,
            user.id,
            document_id,
            at=deps.clock(request),
            language=change.language,
            template_slug=change.template_slug,
        )
    except store.TemplateUnknownError:
        db.rollback()
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, UNKNOWN_TEMPLATE) from None
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    document = _queue(request, db, user, document_id)
    return _document_out(db, document)


@router.post(
    "/api/documents/{document_id}/render",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=QueuedOut,
    responses={429: {"description": "Too many renders of this person wait"}},
)
def render(document_id: uuid.UUID, user: CurrentUser, request: Request, db: Db) -> QueuedOut:
    """Queue a render of the draft. Asked again before it ran, it is the same render."""
    _own(db, user, document_id)
    document = _queue(request, db, user, document_id)
    return QueuedOut(render=_render_out(document))


# --- versions -----------------------------------------------------------------------------------


@router.get("/api/documents/{document_id}/versions", response_model=list[VersionSummary])
def list_versions(document_id: uuid.UUID, user: CurrentUser, db: Db) -> list[VersionSummary]:
    _own(db, user, document_id)
    found = sorted(store.versions(db, user.id, document_id), key=lambda v: -v.number)
    return [VersionSummary(**_summary(version)) for version in found]


@router.get("/api/documents/{document_id}/versions/{number}", response_model=VersionOut)
def read_version(
    document_id: uuid.UUID, number: Annotated[int, Path(ge=1)], user: CurrentUser, db: Db
) -> VersionOut:
    """A version with its content and every correction stored with it, those that replaced a
    character the template cannot print apart."""
    return _version_out(_version(db, user, document_id, number))


@router.get(
    "/api/documents/{document_id}/versions/{number}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def read_pdf(
    document_id: uuid.UUID,
    number: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    request: Request,
    db: Db,
) -> Response:
    """The version's PDF, inline, named after the person and the company. A frozen version's is
    the stored one, byte for byte."""
    document = _own(db, user, document_id)
    version = _version(db, user, document_id, number, pdf=True)
    if version.pdf is None:
        raise _error(status.HTTP_404_NOT_FOUND, NO_PDF)
    name = file_name(db, document, deps.clock(request))
    return Response(
        content=bytes(version.pdf),
        media_type="application/pdf",
        headers={**FILE_HEADERS, "Content-Disposition": f'inline; filename="{name}"'},
    )


@router.get(
    "/api/documents/{document_id}/versions/{number}/page1.png",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def read_page1(
    document_id: uuid.UUID,
    number: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    db: Db,
    width: Annotated[int, Query(ge=PREVIEW_MIN, le=PREVIEW_MAX)] = PREVIEW_WIDTH,
) -> Response:
    """Page 1 of the version's PDF as a PNG, for phones and cards."""
    version = _version(db, user, document_id, number, pdf=True)
    if version.pdf is None:
        raise _error(status.HTTP_404_NOT_FOUND, NO_PDF)
    pdf = bytes(version.pdf)
    db.close()  # the drawing takes a moment: the connection goes back to the pool first
    with _previews:
        png = render_client.page1_png(pdf, width)
    return Response(
        content=png,
        media_type="image/png",
        headers={**FILE_HEADERS, "Content-Disposition": "inline"},
    )


@router.post("/api/documents/{document_id}/versions/{number}/restore", response_model=DocumentOut)
def restore_version(
    document_id: uuid.UUID,
    number: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    request: Request,
    db: Db,
) -> DocumentOut:
    """Copy a version's content into the draft (a new revision), and render it."""
    document = _own(db, user, document_id)
    at = deps.clock(request)
    try:
        store.restore(
            db,
            user.id,
            document_id,
            number,
            at=at,
            sources=_checked(db, document, at),
            today=at.date(),
        )
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, VERSION_NOT_FOUND) from None
    document = _queue(request, db, user, document_id)
    return _document_out(db, document)


@router.post(
    "/api/documents/{document_id}/versions/{number}/approve", response_model=VersionSummary
)
def approve_version(
    document_id: uuid.UUID,
    number: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    request: Request,
    db: Db,
) -> VersionSummary:
    """Mark the version approved (the one before loses the mark)."""
    _own(db, user, document_id)
    try:
        version = store.approve(db, user.id, document_id, number, deps.clock(request))
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, VERSION_NOT_FOUND) from None
    except store.FrozenVersionError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, FROZEN) from None
    store.commit(db)
    return VersionSummary(**_summary(version))


# --- settings -----------------------------------------------------------------------------------


@settings_router.get("/settings", response_model=DocumentSettings)
def read_settings(user: CurrentUser, db: Db) -> DocumentSettings:
    """The writing rules of Decision 9."""
    return store.settings(db, user.id)


@settings_router.put("/settings", response_model=DocumentSettings)
def save_settings(
    body: Annotated[dict[str, Any], Body()], user: CurrentUser, request: Request, db: Db
) -> DocumentSettings:
    try:
        value = DocumentSettings.model_validate(body)
    except ValidationError as invalid:
        fields = sorted({".".join(str(p) for p in error["loc"]) for error in invalid.errors()})
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid settings: " + ", ".join(fields)
        ) from None
    saved = store.save_settings(db, user.id, value, deps.clock(request))
    store.commit(db)
    return saved


# --- templates ----------------------------------------------------------------------------------


def _thumbnails(template: templates.Template) -> dict[str, str]:
    return {
        kind: f"/api/document-templates/{template.slug}/thumbnail?kind={kind}&v={template.version}"
        for kind in templates.KINDS
    }


@templates_router.get("", response_model=TemplatesOut)
def list_templates(user: CurrentUser, db: Db, response: Response) -> TemplatesOut:
    """The gallery: every bundled template (manifest fields, both languages), then the
    person's own, marked `user`."""
    response.headers.update(PRIVATE)
    out = [
        TemplateOut(
            slug=template.slug,
            version=template.version,
            user=False,
            name=dict(template.name),
            description=dict(template.description),
            tags=list(template.tags),
            photo=template.photo,
            accent=template.accent,
            kinds=list(template.kinds),
            thumbnails=_thumbnails(template),
        )
        for template in sorted(templates.registry().values(), key=lambda t: t.slug)
    ]
    for own in store.user_templates(db, user.id):
        out.append(
            TemplateOut(
                slug=own.slug,
                version=own.version,
                user=True,
                name={language: own.name for language in templates.LANGUAGES},
                description={},
                tags=[],
                photo=str((own.manifest or {}).get("photo", "none")),
                accent=None,
                kinds=list(templates.KINDS),
                thumbnails={},
            )
        )
    return TemplatesOut(templates=out)


@templates_router.get(
    "/{slug}/thumbnail",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def read_thumbnail(
    slug: str,
    user: CurrentUser,
    kind: Annotated[Literal["cv", "letter"], Query()] = "cv",
) -> Response:
    """A bundled template's thumbnail: public data of the app, cached. The file is the one the
    registry knows for that slug; a person's own template has none here (404)."""
    template = templates.registry().get(slug)
    if template is None or template.folder is None:
        raise _error(status.HTTP_404_NOT_FOUND, TEMPLATE_NOT_FOUND)
    name = templates.THUMBNAILS[templates.KINDS.index(kind)]
    data = (template.folder / name).read_bytes()
    return Response(
        content=data,
        media_type="image/png",
        headers={
            "Cache-Control": f"public, max-age={THUMBNAIL_MAX_AGE_S}",
            "X-Content-Type-Options": "nosniff",
        },
    )
