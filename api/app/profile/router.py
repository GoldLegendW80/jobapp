"""/api/profile: read the caller's profile, save one section, browse past revisions, and export
or import the whole profile as one JSON file (format in schemas.py, "export and import").

Every route needs a session (the app-wide default deny; none of them is public) and acts on the
caller's own profile only: the user id comes from `current_user`, never from the request. Another
user's revision answers the same 404 as one that does not exist.

Handlers are plain `def` (run on the threadpool, as the identity routes) and each owns one
transaction. Order of checks on a save: unknown section 404, invalid data 422 (one sentence,
`{"detail": "data.<field path>: <message>"}`, never an echo of the value), stale base_revision
409 with the current revision and sections so the client can show them and retry.
"""

import base64
from datetime import UTC
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.profile import service
from app.profile.models import Profile
from app.profile.schemas import (
    IMPORT_MAX_BYTES,
    REVISION_MAX,
    SECTIONS,
    ConflictOut,
    DocumentError,
    ExportDocument,
    ExportSections,
    ImportIn,
    PhotoData,
    ProfileOut,
    RevisionOut,
    RevisionPage,
    SaveSectionIn,
    SectionName,
    completeness,
    parse_document,
    validate_section,
)

NO_STORE = {"Cache-Control": "no-store"}


def _no_store(response: Response) -> None:
    """Profile data is private: no browser or proxy keeps a copy."""
    response.headers.update(NO_STORE)


router = APIRouter(prefix="/api/profile", tags=["profile"], dependencies=[Depends(_no_store)])

UNKNOWN_SECTION = "Unknown profile section."
REVISION_NOT_FOUND = "Revision not found."
STALE = "The profile changed since you loaded it. Review the current version and save again."
TOO_LARGE = "The import file is larger than 2 MB."

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such section or revision"}}
_409 = {409: {"model": ConflictOut, "description": "base_revision is stale"}}
_422 = {422: {"model": ErrorOut, "description": "The data breaks a rule of the section"}}


def _out(profile: Profile) -> ProfileOut:
    sections = service.sections_view(profile)
    return ProfileOut(
        revision=profile.revision,
        updated_at=profile.updated_at,
        sections=sections,
        completeness=completeness({k: v for k, v in sections.items() if v is not None}),
    )


def _invalid(exc: ValidationError, prefix: tuple[str, ...] = ("data",)) -> RequestValidationError:
    """The section's errors as a request validation error under body.data (body for an import),
    so the app's 422 handler formats them like every other one."""
    errors = []
    for error in exc.errors(include_input=False, include_url=False):
        message = str(error.get("msg", "invalid value")).removeprefix("Value error, ")
        errors.append({**error, "loc": ("body", *prefix, *error.get("loc", ())), "msg": message})
    return RequestValidationError(errors)


@router.get("", response_model=ProfileOut, responses=_401)
def read_profile(user: CurrentUser, request: Request, db: Db) -> ProfileOut:
    """All sections (null when never saved), the revision, and completeness per section."""
    profile = service.get_profile(db, user.id, deps.clock(request))
    db.commit()
    return _out(profile)


@router.get("/revisions", response_model=RevisionPage, responses=_401)
def list_revisions(
    user: CurrentUser,
    db: Db,
    section: SectionName | None = None,
    limit: Annotated[int, Query(ge=1, le=service.REVISIONS_PAGE_MAX)] = 20,
    before: Annotated[int | None, Query(ge=1, le=REVISION_MAX)] = None,
) -> RevisionPage:
    page = service.list_revisions(db, user.id, section=section, limit=limit, before=before)
    return RevisionPage.model_validate(
        {"items": page.items, "next_before": page.next_before}, from_attributes=True
    )


@router.get("/revisions/{revision}", response_model=RevisionOut, responses={**_401, **_404})
def read_revision(
    user: CurrentUser,
    db: Db,
    revision: Annotated[int, Path(ge=1, le=REVISION_MAX)],
) -> RevisionOut:
    found = service.get_revision(db, user.id, revision)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=REVISION_NOT_FOUND)
    return RevisionOut.model_validate(found, from_attributes=True)


def _conflict(exc: service.StaleRevisionError, db: Session) -> JSONResponse:
    conflict = ConflictOut(
        detail=STALE,
        revision=exc.profile.revision,
        sections=service.sections_view(exc.profile),
    )
    db.rollback()
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=conflict.model_dump(mode="json"),
        headers=NO_STORE,
    )


@router.get(
    "/export",
    responses={**_401, 200: {"content": {"application/json": {}}, "description": "The file"}},
)
def export_profile(user: CurrentUser, request: Request, db: Db) -> Response:
    """The whole profile as one JSON file, downloaded as profile-YYYY-MM-DD.json. Sections never
    saved are exported in their empty form, so the file always holds all six."""
    now = deps.clock(request)
    profile = service.get_profile(db, user.id, now)
    db.commit()
    photo = None
    if profile.photo is not None and profile.photo_mime is not None:
        photo = PhotoData(
            mime=profile.photo_mime, base64=base64.b64encode(profile.photo).decode("ascii")
        )
    document = ExportDocument(
        exported_at=now,
        sections=ExportSections.model_validate(service.export_sections(profile)),
        photo=photo,
    )
    filename = f"profile-{now.astimezone(UTC):%Y-%m-%d}.json"
    return Response(
        content=document.to_json().encode("utf-8"),
        media_type="application/json",
        headers={**NO_STORE, "Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _import_body(request: Request) -> bytes:
    """The request body, refused with 413 as soon as it is known to exceed IMPORT_MAX_BYTES:
    from Content-Length when declared, else while it streams in (nothing larger is buffered)."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > IMPORT_MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=TOO_LARGE)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > IMPORT_MAX_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/import",
    response_model=ProfileOut,
    responses={
        **_401,
        **_409,
        413: {"model": ErrorOut, "description": "The file is larger than 2 MB"},
        **_422,
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": ImportIn.model_json_schema()}},
        }
    },
)
def import_profile(
    raw: Annotated[bytes, Depends(_import_body)], user: CurrentUser, request: Request, db: Db
) -> Any:
    """Replace the whole profile with an export file: all six sections in one transaction, one
    revision each; the photo too when the file has one. With `base_revision`, a profile changed
    since then is a 409; without it the import simply applies after any save in progress."""
    try:
        document = parse_document(raw)
    except DocumentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    except ValidationError as exc:
        raise _invalid(exc, ()) from None
    photo = None
    if document.photo is not None:
        photo = (document.photo.image(), document.photo.mime)
    try:
        profile = service.import_profile(
            db,
            user.id,
            document.stored_sections(),
            photo,
            document.base_revision,
            deps.clock(request),
        )
    except service.StaleRevisionError as exc:
        return _conflict(exc, db)
    db.commit()
    return _out(profile)


@router.put(
    "/{section}",
    response_model=ProfileOut,
    responses={**_401, **_404, **_409, **_422},
)
def save_section(
    section: str, body: SaveSectionIn, user: CurrentUser, request: Request, db: Db
) -> Any:
    """Replace one section. `base_revision` is the revision the client loaded."""
    if section not in SECTIONS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=UNKNOWN_SECTION)
    try:
        data = validate_section(section, body.data)
    except ValidationError as exc:
        raise _invalid(exc) from None
    try:
        profile = service.save_section(
            db, user.id, section, data, body.base_revision, deps.clock(request)
        )
    except service.StaleRevisionError as exc:
        return _conflict(exc, db)
    db.commit()
    return _out(profile)
