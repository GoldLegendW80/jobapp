"""/api/profile: read the caller's profile, save one section, browse past revisions, export or
import the whole profile as one JSON file (format in schemas.py, "export and import"), and upload,
read or remove the photo.

The photo (PUT, GET, DELETE /api/profile/photo):
- PUT takes the image as the raw request body (not multipart), at most PHOTO_MAX_BYTES (2 MB,
  413 above, checked on Content-Length and again while the body streams). The Content-Type and
  any file name are ignored: the type comes from the bytes. It goes through `photo.normalize`
  (PNG or JPEG only, fully decoded, bomb guard, EXIF orientation applied, at most 1024 px,
  re-encoded without any metadata): 415 for anything that is not a complete PNG or JPEG, 422 for
  too many pixels. The answer describes the stored image and carries its ETag.
- GET serves the stored bytes with `Cache-Control: private, no-cache` and a strong ETag (the
  SHA-256 of the bytes), 304 when If-None-Match matches, 404 without a photo.
- DELETE is idempotent: 204 whether or not there was a photo.
The photo is not a section: no revision is recorded, `updated_at` moves.

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
from app.profile import boards, service
from app.profile import photo as photos
from app.profile.models import Profile
from app.profile.schemas import (
    IMPORT_MAX_BYTES,
    PHOTO_MAX_BYTES,
    REVISION_MAX,
    SECTIONS,
    ConflictOut,
    DocumentError,
    ExportDocument,
    ExportSections,
    ImportIn,
    PhotoData,
    PhotoOut,
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
# POST /api/profile/boards/check (boards.py).
router.include_router(boards.router)

UNKNOWN_SECTION = "Unknown profile section."
REVISION_NOT_FOUND = "Revision not found."
STALE = "The profile changed since you loaded it. Review the current version and save again."
TOO_LARGE = "The import file is larger than 2 MB."
NO_PHOTO = "No photo."
PHOTO_CACHE = "private, no-cache"

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such section or revision"}}
_409 = {409: {"model": ConflictOut, "description": "base_revision is stale"}}
_422 = {422: {"model": ErrorOut, "description": "The data breaks a rule of the section"}}


def _out(profile: Profile, db: Session) -> ProfileOut:
    sections = service.sections_view(profile)
    state = service.photo_state(db, profile.user_id)
    return ProfileOut(
        revision=profile.revision,
        updated_at=profile.updated_at,
        sections=sections,
        completeness=completeness({k: v for k, v in sections.items() if v is not None}),
        has_photo=state is not None,
        photo_etag=state.etag if state else None,
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
    return _out(profile, db)


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


async def _limited_body(request: Request, limit: int, message: str) -> bytes:
    """The request body, refused with 413 as soon as it is known to exceed `limit` bytes: from
    Content-Length when declared, else while it streams in (nothing larger is buffered)."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=message)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=message)
        chunks.append(chunk)
    return b"".join(chunks)


async def _import_body(request: Request) -> bytes:
    return await _limited_body(request, IMPORT_MAX_BYTES, TOO_LARGE)


async def _photo_body(request: Request) -> bytes:
    return await _limited_body(request, PHOTO_MAX_BYTES, photos.TOO_LARGE)


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
        try:
            photo = photos.normalize(document.photo.image())
        except photos.PhotoError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"photo: {exc.message}"
            ) from None
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
    return _out(profile, db)


# --- photo --------------------------------------------------------------------------------------
# Declared before PUT /{section}, which would otherwise take /photo for an unknown section.

_PHOTO_ERRORS = {
    413: {"model": ErrorOut, "description": "The body is larger than 2 MB"},
    415: {"model": ErrorOut, "description": "Not a complete PNG or JPEG image"},
    422: {"model": ErrorOut, "description": "The image has too many pixels"},
}


@router.put(
    "/photo",
    response_model=PhotoOut,
    responses={**_401, **_PHOTO_ERRORS},
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The image file itself as the raw body; its type is read from its "
            "bytes, the Content-Type header is ignored.",
            "content": {
                media: {"schema": {"type": "string", "format": "binary"}}
                for media in ("image/png", "image/jpeg", "application/octet-stream")
            },
        }
    },
)
def upload_photo(
    raw: Annotated[bytes, Depends(_photo_body)],
    user: CurrentUser,
    request: Request,
    response: Response,
    db: Db,
) -> PhotoOut:
    """Replace the photo with the uploaded PNG or JPEG, re-encoded without metadata."""
    try:
        photo = photos.normalize(raw)  # outside the row lock: decoding takes a while
    except photos.PhotoError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    profile = service.set_photo(db, user.id, photo, deps.clock(request))
    db.commit()
    response.headers["ETag"] = photo.etag
    return PhotoOut(
        mime=photo.mime,
        width=photo.width,
        height=photo.height,
        size=len(photo.data),
        etag=photo.etag,
        updated_at=profile.updated_at,
    )


def _matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 weak comparison of If-None-Match with the current ETag (`*` matches any)."""
    if if_none_match is None:
        return False
    for candidate in if_none_match.split(","):
        tag = candidate.strip()
        if tag == "*" or tag.removeprefix("W/") == etag:
            return True
    return False


@router.get(
    "/photo",
    responses={
        **_401,
        200: {"content": {"image/png": {}, "image/jpeg": {}}, "description": "The image"},
        304: {"description": "If-None-Match matches the current photo"},
        404: {"model": ErrorOut, "description": "No photo"},
    },
)
def read_photo(user: CurrentUser, request: Request, db: Db) -> Response:
    """The stored photo, revalidated on every use (private, no-cache) through its ETag."""
    state = service.photo_state(db, user.id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_PHOTO)
    headers = {"Cache-Control": PHOTO_CACHE, "ETag": state.etag}
    if _matches(request.headers.get("if-none-match"), state.etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    found = service.get_photo(db, user.id)
    if found is None:  # removed between the two reads
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_PHOTO)
    data, mime = found
    extension = "png" if mime == "image/png" else "jpg"
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Cache-Control": PHOTO_CACHE,
            "ETag": photos.etag(data),
            "Content-Disposition": f'inline; filename="photo.{extension}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/photo", status_code=status.HTTP_204_NO_CONTENT, responses=_401, response_class=Response
)
def delete_photo(user: CurrentUser, request: Request, db: Db) -> Response:
    """Remove the photo. Idempotent: 204 whether or not there was one."""
    service.delete_photo(db, user.id, deps.clock(request))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)


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
    return _out(profile, db)
