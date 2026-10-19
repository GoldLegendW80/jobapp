"""The base CVs and the consent of the documents module (task D9; SPEC-documents.md, "Base CVs",
"API"). Included by `routers.py` before D6's `/api/documents/{document_id}`.

- `GET /api/documents/base-cvs`: the list, oldest first, with source, template, default mark,
  the date of the last version and the state of the open or last generation.
- `POST /api/documents/base-cvs {from, name?, copy_of?, language?}`:
  - `from: profile`: a base CV built from the profile (`writer_base.build_from_profile`): the
    skeleton row at once and its generation, started now when the consent and a credential are
    there, else `waiting` with `consent` or `credential`. 202. An empty profile gives the
    skeleton with a note and no generation: nothing refused.
  - `from: copy`: a copy of one of the person's base CVs (its draft, language and template;
    source `built`), rendered. 201. Another person's id answers 404.
  - `from: upload` (D24) with `ingestion_document_id`: a base CV converted from the person's
    uploaded CV (`writer_base.build_from_upload`): the empty row at once and its `base_upload`
    generation, started or waiting as above. 202. A file that is not the person's answers 404;
    one whose text failed 422 with ingestion's own sentence; a file still being read 409; pages
    of pictures, a secret warning not confirmed, or a file neither PDF nor DOCX 422, each with
    its sentence. Each upload makes a new base CV, the default when the current default was
    built from the profile.
  - the base CV past `DOCUMENTS_BASE_CVS_MAX`: 409 with the sentence.
- `PATCH /api/documents/base-cvs/{id} {name?, default?}`: rename; `default: true` makes it the
  default (twice is the same answer); `default: false` is 422 (make another one the default).
- `DELETE /api/documents/base-cvs/{id}`: 204. The posting documents written from it stay;
  deleting the default makes the oldest other one the default; deleting the last is allowed.
- `GET /api/documents/consent`: the text, its version, whether it is accepted, and the
  estimates of one posting and of a typical week at the threshold (`consent.figures`).
- `POST /api/documents/consent {version}`: records it (422 for another version than the
  current one), then starts the base CV generations that waited for it.

Every route is the signed in person's: another person's base CV answers 404. Every answer is
`Cache-Control: private, no-store`. Logs carry ids and codes, never a text.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

import anyio.from_thread
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy.orm import Session

from app import queue as queues
from app.db import get_session
from app.documents import consent, eligibility, generate, render_job, store, writer_base
from app.documents.models import (
    Author,
    BaseSource,
    Document,
    DocumentGeneration,
    DocumentVersion,
    GenerationStatus,
    GenerationWait,
    Language,
    RenderState,
    Scope,
)
from app.identity import deps
from app.identity.models import User
from app.ingestion import pricing
from app.ingestion.estimate import RunEstimate

logger = logging.getLogger("app.documents")

PRIVATE = {"Cache-Control": "private, no-store"}
NOT_FOUND = "CV not found."
COPY_OF_MISSING = "Say which CV to copy (copy_of)."
UPLOAD_MISSING = "Say which file to convert (ingestion_document_id)."
UPLOAD_NOT_FOUND = "Uploaded file not found."
# Why an uploaded file cannot be converted, by writer_base.UploadRefusedError code (status, text).
UPLOAD_REFUSED = {
    "not_found": (404, UPLOAD_NOT_FOUND),
    "failed": (422, "This file could not be read. Upload it again, or another file."),
    "reading": (409, "Your CV is still being read. Try again in a moment."),
    "pictures": (
        422,
        "This CV is made of pictures of pages, so it has no text to convert. Export it as a "
        "PDF with text, or as a Word document, and upload that.",
    ),
    "secret": (
        422,
        "This file may hold a password or a key. Confirm that you keep it, or upload another file.",
    ),
    "not_a_cv": (422, "Upload your CV as a PDF or a Word document (DOCX)."),
}
NOT_DEFAULT = "Make another CV the default instead."
BAD_NAME = "A CV's name has 1 to 100 characters."
COPY_PREFIX = "Copy of "


def _private(response: Response) -> None:
    response.headers.update(PRIVATE)


Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

router = APIRouter(prefix="/api/documents", tags=["documents"], dependencies=[Depends(_private)])


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail, headers=PRIVATE)


def too_many(limit: int) -> str:
    return f"You have {limit} CVs, the most the app keeps. Delete one to add another."


# --- answers ------------------------------------------------------------------------------------


class GenerationOut(BaseModel):
    id: uuid.UUID
    status: GenerationStatus
    wait_reason: GenerationWait | None
    error: str | None  # a code of generate.ERRORS


class BaseCvOut(BaseModel):
    id: uuid.UUID
    name: str
    source: BaseSource
    template_slug: str
    language: Language
    is_default: bool
    created_at: datetime
    updated_at: datetime
    last_version_at: datetime | None
    render_state: RenderState
    generation: GenerationOut | None  # the open one, else the last one


class BaseCvsOut(BaseModel):
    base_cvs: list[BaseCvOut]
    max: int


class BuiltOut(BaseModel):
    base_cv: BaseCvOut


class BaseCvIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: Literal["profile", "copy", "upload"] = Field(alias="from")
    name: Annotated[str, Field(max_length=400)] | None = None
    copy_of: uuid.UUID | None = None
    ingestion_document_id: uuid.UUID | None = None
    language: Language | None = None
    # The fake backend's recorded stream (tests and journeys); ignored on the real model.
    script: (
        Annotated[str, Field(max_length=60, pattern=r"^documents_base_[a-z0-9_]{1,30}$")] | None
    ) = None


class BaseCvPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=400)] | None = None
    default: StrictBool | None = None


class TokensOut(BaseModel):
    low: int
    high: int


class CostOut(BaseModel):
    currency: str
    low: Decimal
    high: Decimal


class EstimateOut(BaseModel):
    tokens: TokensOut
    cost: CostOut | None  # an API key only


class WeekOut(EstimateOut):
    postings: int
    postings_30_days: int


class ConsentOut(BaseModel):
    version: int
    text: str
    accepted: bool
    accepted_at: datetime | None
    model: str | None
    min_fit: int
    posting: EstimateOut
    week: WeekOut
    cost_note: str


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1, le=2**31 - 1)


# --- helpers ------------------------------------------------------------------------------------


def _generation(db: Session, document: Document) -> GenerationOut | None:
    found = db.execute(
        sa.select(DocumentGeneration)
        .where(
            DocumentGeneration.user_id == document.user_id,
            DocumentGeneration.document_id == document.id,
        )
        .order_by(DocumentGeneration.created_at.desc(), DocumentGeneration.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if found is None:
        return None
    return GenerationOut(
        id=found.id, status=found.status, wait_reason=found.wait_reason, error=found.error
    )


def _out(db: Session, document: Document) -> BaseCvOut:
    last = db.scalar(
        sa.select(sa.func.max(DocumentVersion.created_at)).where(
            DocumentVersion.user_id == document.user_id,
            DocumentVersion.document_id == document.id,
        )
    )
    return BaseCvOut(
        id=document.id,
        name=document.name or "",  # never null on a base CV (check base_has_a_name)
        source=document.base_source or BaseSource.built,  # never null either (base_has_source)
        template_slug=document.template_slug,
        language=document.language,
        is_default=document.is_default,
        created_at=document.created_at,
        updated_at=document.updated_at,
        last_version_at=last,
        render_state=document.render_state,
        generation=_generation(db, document),
    )


def _own_base(db: Session, user: User, document_id: uuid.UUID) -> Document:
    found = store.get(db, user.id, document_id)
    if found is None or found.scope != Scope.base:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return found


def _max() -> int:
    from app.documents.writer_cv import setting

    return int(setting("documents_base_cvs_max"))


def _enqueue_runs(request: Request, run_ids: list[uuid.UUID]) -> None:
    """After the commit. Redis down leaves the run `queued`, as ingestion's start does."""
    for run_id in run_ids:
        try:
            anyio.from_thread.run(generate.enqueue_run, request.app.state.queue, run_id)
        except queues.QueueUnavailableError:
            logger.warning("documents.base.run_not_queued", extra={"run_id": str(run_id)})


def _render(request: Request, db: Session, user: User, document_id: uuid.UUID) -> None:
    """Ask for the render of a base CV written by code (a copy, a skeleton), committed already,
    and commit. A full render queue leaves it unrendered: "Render" stays offered."""
    try:
        document, fresh = render_job.mark_queued(db, user.id, document_id)
    except render_job.RenderQueueFullError:
        db.rollback()
        return
    revision = document.draft_revision
    store.commit(db)
    if not fresh:
        return
    try:
        anyio.from_thread.run(render_job.enqueue, request.app.state.queue, document_id, revision)
    except queues.QueueUnavailableError:
        logger.warning("documents.render.enqueue_failed", extra={"document_id": str(document_id)})


def _estimate_out(found: RunEstimate, model: str | None, priced: bool) -> EstimateOut:
    cost = None
    if priced and model is not None:
        costs = pricing.costs(found, model)
        cost = CostOut(currency=pricing.CURRENCY, low=costs.low, high=costs.high)
    return EstimateOut(tokens=TokensOut(low=found.low, high=found.high), cost=cost)


def _consent_out(db: Session, user: User, at: datetime) -> ConsentOut:
    row = consent.current(db, user.id)
    figures = consent.figures(db, user.id, eligibility.found_day(at))
    week = _estimate_out(figures.week, figures.model, figures.priced)
    return ConsentOut(
        version=consent.VERSION,
        text=consent.text(consent.VERSION),
        accepted=row is not None,
        accepted_at=row.accepted_at if row is not None else None,
        model=figures.model,
        min_fit=figures.min_fit,
        posting=_estimate_out(figures.posting, figures.model, figures.priced),
        week=WeekOut(
            **week.model_dump(),
            postings=figures.week_postings,
            postings_30_days=figures.postings_30_days,
        ),
        cost_note=pricing.ESTIMATE_NOTE if figures.priced else pricing.QUOTA_NOTE,
    )


# --- base CVs -----------------------------------------------------------------------------------


@router.get("/base-cvs", response_model=BaseCvsOut)
def list_base_cvs(user: CurrentUser, db: Db) -> BaseCvsOut:
    return BaseCvsOut(base_cvs=[_out(db, d) for d in store.base_cvs(db, user.id)], max=_max())


def _copy(db: Session, user: User, body: BaseCvIn, at: datetime) -> Document:
    if body.copy_of is None:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, COPY_OF_MISSING)
    source = _own_base(db, user, body.copy_of)
    name = body.name or (COPY_PREFIX + (source.name or ""))[: store.NAME_MAX]
    return store.create_base(
        db,
        user.id,
        name,
        BaseSource.built,
        at=at,
        content=source.draft,
        language=body.language or source.language,
        template_slug=source.template_slug,
        written_by=Author.user,
    )


def _from_upload(db: Session, user: User, body: BaseCvIn, at: datetime) -> writer_base.Built:
    if body.ingestion_document_id is None:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, UPLOAD_MISSING)
    try:
        return writer_base.build_from_upload(
            db, user.id, body.ingestion_document_id, at=at, name=body.name, language=body.language
        )
    except writer_base.UploadRefusedError as refused:
        db.rollback()
        code, sentence = UPLOAD_REFUSED[refused.code]
        if refused.code == "failed" and refused.reason:
            sentence = refused.reason
        logger.info(
            "documents.base.upload_refused", extra={"user_id": str(user.id), "code": refused.code}
        )
        raise _error(code, sentence) from None
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, UPLOAD_NOT_FOUND) from None


@router.post(
    "/base-cvs",
    response_model=BuiltOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        201: {"description": "A copy, made at once"},
        404: {"description": "The CV to copy is not the person's"},
        409: {"description": "The most base CVs are kept, or the upload is still being read"},
        422: {"description": "An upload that cannot be converted, with the reason"},
    },
)
def create_base_cv(
    body: BaseCvIn, user: CurrentUser, request: Request, response: Response, db: Db
) -> BuiltOut:
    at = deps.clock(request)
    runs: list[uuid.UUID] = []
    try:
        if body.from_ == "copy":
            document = _copy(db, user, body, at)
            response.status_code = status.HTTP_201_CREATED
            generation = None
        else:
            built = (
                _from_upload(db, user, body, at)
                if body.from_ == "upload"
                else writer_base.build_from_profile(
                    db, user.id, at=at, name=body.name, language=body.language or Language.en
                )
            )
            document, generation = built.document, built.generation
            if generation is not None and generation.status is GenerationStatus.queued:
                runs = writer_base.start_ready(db, user.id, at=at, script=body.script)
    except store.BaseLimitError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, too_many(_max())) from None
    except store.NameInvalidError:
        db.rollback()
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, BAD_NAME) from None
    document_id = document.id
    store.commit(db)
    if generation is None:
        _render(request, db, user, document_id)
    _enqueue_runs(request, runs)
    logger.info(
        "documents.base.created",
        extra={"document_id": str(document_id), "from": body.from_},
    )
    return BuiltOut(base_cv=_out(db, _own_base(db, user, document_id)))


@router.patch("/base-cvs/{document_id}", response_model=BaseCvOut)
def patch_base_cv(
    document_id: uuid.UUID, change: BaseCvPatch, user: CurrentUser, request: Request, db: Db
) -> BaseCvOut:
    _own_base(db, user, document_id)
    if change.default is False:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, NOT_DEFAULT)
    at = deps.clock(request)
    try:
        if change.name is not None:
            store.rename_base(db, user.id, document_id, change.name, at)
        if change.default:
            store.set_default(db, user.id, document_id, at)
    except store.NameInvalidError:
        db.rollback()
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, BAD_NAME) from None
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    store.commit(db)
    return _out(db, _own_base(db, user, document_id))


@router.delete("/base-cvs/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_base_cv(document_id: uuid.UUID, user: CurrentUser, request: Request, db: Db) -> None:
    _own_base(db, user, document_id)
    try:
        store.delete_base(db, user.id, document_id, deps.clock(request))
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    store.commit(db)


# --- the consent --------------------------------------------------------------------------------


@router.get("/consent", response_model=ConsentOut)
def read_consent(user: CurrentUser, request: Request, db: Db) -> ConsentOut:
    return _consent_out(db, user, deps.clock(request))


@router.post(
    "/consent",
    response_model=ConsentOut,
    responses={422: {"description": "Not the current version of the text"}},
)
def give_consent(body: ConsentIn, user: CurrentUser, request: Request, db: Db) -> ConsentOut:
    if body.version != consent.VERSION:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, consent.OUTDATED)
    at = deps.clock(request)
    consent.record(db, user.id, at)
    runs = writer_base.start_ready(db, user.id, at=at)
    store.commit(db)
    _enqueue_runs(request, runs)
    logger.info("documents.consent", extra={"user_id": str(user.id), "version": consent.VERSION})
    return _consent_out(db, user, at)
