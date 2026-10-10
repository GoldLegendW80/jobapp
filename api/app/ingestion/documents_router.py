"""/api/ingestion/documents: add a file or a pasted text, list, read the text back, confirm a
secret warning, delete (SPEC-ingestion.md, API; the rules are in documents.py).

- POST /api/ingestion/documents: the file itself as the raw request body (no multipart), its
  name in `X-Filename` (RFC 5987, `UTF-8''caf%C3%A9.txt`; display only, missing or malformed is
  "Untitled"). The Content-Type is ignored: the type comes from the bytes. 201 with the document,
  200 when this user already has the same file (`duplicate: true`, nothing written).
  413 over 20 MiB or over the user's 100 MB, 415 for a type that is not read, 422 for an empty
  file, a file without text or a 201st document, 408 when the body stalls, 400 when the client
  goes away, 503 with Retry-After when both upload slots stay busy.
- POST /api/ingestion/documents/text {text, name?}: a pasted text, same answers; 413 over
  100,000 characters.
- GET /api/ingestion/documents: the caller's documents, newest first, what they take and the
  limits.
- GET /api/ingestion/documents/{id}/text?offset=&limit=: the stored text, a page at a time;
  409 while the document has no text.
- POST /api/ingestion/documents/{id}/confirm: the user keeps a document that carries a secret
  warning. Idempotent.
- DELETE /api/ingestion/documents/{id}: 204, the document is gone for good; 409 with the reason
  when a guard refuses (none in this task).

How an upload is read (`_read_body`), since this is the one route that accepts 20 MiB:
- A declared Content-Length over the limit is refused before a byte is read.
- At most UPLOAD_SLOTS uploads are in memory at once in this process: the others wait their
  turn without their body being read, and give up with 503 after SLOT_WAIT_S.
- The body is read chunk by chunk into one buffer, hashed as it comes, and counted: the read
  stops with 413 at the first byte past the limit, whatever Content-Length said. No second copy
  is made: documents.py sends the buffer to Postgres in pieces.
- A body that sends nothing for IDLE_TIMEOUT_S, or is not complete after TOTAL_TIMEOUT_S, ends
  with 408, and the connection is closed, so a slot is never held for longer than that.

Every route needs a session (the app-wide default deny) and takes the user from it, never from
the request. Another user's document answers the same 404 as one that does not exist, an id that
is not a UUID too. Every answer is `Cache-Control: no-store`. Nothing is logged but ids, sizes,
types, statuses and rule names.
"""

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.ingestion import documents
from app.ingestion.models import (
    FILE_MAX_BYTES,
    NAME_MAX_CHARS,
    TEXT_MAX_CHARS,
    IngestionDocument,
    TextSource,
    TextStatus,
)

logger = logging.getLogger("app.ingestion")

NO_STORE = {"Cache-Control": "no-store"}
CLOSE = {"Connection": "close"}

UPLOAD_SLOTS = 2  # uploads held in memory at once, in this process
SLOT_WAIT_S = 20.0
RETRY_AFTER_S = 5
IDLE_TIMEOUT_S = 30.0  # the longest silence inside a body
TOTAL_TIMEOUT_S = 600.0  # the whole body: 20 MiB at 280 kbit/s
# The JSON of a paste: 100,000 characters, each at most 12 bytes when escaped.
PASTE_BODY_MAX_BYTES = 2 * 1024 * 1024
PREVIEW_DEFAULT = 20_000
PREVIEW_MAX = 50_000

NOT_FOUND = "Document not found."
NO_TEXT_YET = "This document has no text yet."
STALLED = "The upload stopped arriving. Check your connection and add the file again."
INTERRUPTED = "The upload was interrupted."
BUSY = "Other uploads are in progress. Try again in a moment."
PASTE_BODY_TOO_LARGE = documents.PASTE_TOO_LONG
BAD_JSON = "The request is not valid JSON."


def _no_store(response: Response) -> None:
    """Documents are private: no browser or proxy keeps a copy of an answer."""
    response.headers.update(NO_STORE)


router = APIRouter(
    prefix="/api/ingestion/documents", tags=["ingestion"], dependencies=[Depends(_no_store)]
)
Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such document (or not yours)"}}
_STORE_ERRORS: dict[int | str, dict[str, Any]] = {
    200: {"description": "This user already has the same file: it is returned, nothing written"},
    413: {"model": ErrorOut, "description": "Over the size limit or the user's quota"},
    422: {"model": ErrorOut, "description": "Empty, no text in it, or the 201st document"},
}


class DocumentOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: documents.Kind
    mime: str
    size: int
    status: TextStatus
    source: TextSource
    # False: no reader for this kind of file in this version, the document waits for one.
    readable: bool
    note: str | None
    truncated: bool
    pages: int | None
    pages_text: int | None
    pages_image: int | None
    failed_reason: str | None
    secret_warning: bool
    secret_confirmed_at: datetime | None
    created_at: datetime


class StoredOut(DocumentOut):
    # True: the same file was already among the user's documents; this is that document.
    duplicate: bool


class UsageOut(BaseModel):
    bytes: int
    documents: int


class LimitsOut(BaseModel):
    file_bytes: int = FILE_MAX_BYTES
    paste_characters: int = documents.PASTE_MAX_CHARS
    text_characters: int = TEXT_MAX_CHARS
    user_bytes: int = documents.USER_MAX_BYTES
    documents: int = documents.USER_MAX_DOCUMENTS


class DocumentList(BaseModel):
    items: list[DocumentOut]
    usage: UsageOut
    limits: LimitsOut


class TextOut(BaseModel):
    text: str
    offset: int
    next_offset: int | None
    total: int


class PasteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    # Wider than a stored name: documents.clean_name cuts it.
    name: str | None = Field(default=None, max_length=4 * NAME_MAX_CHARS)


def _fields(document: IngestionDocument) -> dict[str, Any]:
    kind = documents.kind_of(document.mime)
    return {
        "id": document.id,
        "name": document.name,
        "kind": kind,
        "mime": document.mime,
        "size": document.size,
        "status": document.text_status,
        "source": document.text_source,
        "readable": kind in documents.READABLE_KINDS,
        "note": document.note,
        "truncated": document.truncated,
        "pages": document.pages,
        "pages_text": document.pages_text,
        "pages_image": document.pages_image,
        "failed_reason": document.failed_reason,
        "secret_warning": document.secret_warning,
        "secret_confirmed_at": document.secret_confirmed_at,
        "created_at": document.created_at,
    }


def _error(code: int, detail: str, **headers: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail, headers={**NO_STORE, **headers})


def _id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None


def _refuse(user: User, code: int, detail: str, rule: str, size: int, **headers: str) -> NoReturn:
    """Answer a refusal and log it: who, which rule, how many bytes. Never what was sent."""
    logger.info(
        "document refused",
        extra={"user_id": str(user.id), "rule": rule, "status": code, "size": size},
    )
    raise _error(code, detail, **headers) from None


def _refused(exc: documents.DocumentError, user: User, size: int) -> NoReturn:
    _refuse(user, exc.status, exc.message, exc.rule, size)


def _stored(stored: documents.Stored, response: Response) -> StoredOut:
    if not stored.created:
        response.status_code = status.HTTP_200_OK
    return StoredOut(**_fields(stored.document), duplicate=not stored.created)


# --- upload --------------------------------------------------------------------------------


def _slots(app: FastAPI) -> asyncio.Semaphore:
    """The upload slots of this process, made on first use (inside the running loop)."""
    slots: asyncio.Semaphore | None = getattr(app.state, "ingestion_upload_slots", None)
    if slots is None:
        slots = asyncio.Semaphore(UPLOAD_SLOTS)
        app.state.ingestion_upload_slots = slots
    return slots


class _NotTakenError(Exception):
    """The body was not taken whole: the answer, the rule for the log, and how many bytes had
    arrived when the read stopped."""

    def __init__(self, code: int, detail: str, rule: str, size: int) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.rule = rule
        self.size = size


async def _read_body(request: Request) -> tuple[bytearray, bytes]:
    """The body in one buffer and its SHA-256 (module docstring: counted, hashed and timed
    while it is read). Raises _NotTakenError."""
    buffer = bytearray()
    digest = hashlib.sha256()
    chunks = request.stream().__aiter__()
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_S):
            while True:
                try:
                    chunk = await asyncio.wait_for(anext(chunks), IDLE_TIMEOUT_S)
                except StopAsyncIteration:
                    break
                if len(buffer) + len(chunk) > FILE_MAX_BYTES:
                    raise _NotTakenError(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        documents.TOO_LARGE,
                        "file-too-large",
                        len(buffer) + len(chunk),
                    )
                buffer += chunk
                digest.update(chunk)
    except TimeoutError:
        raise _NotTakenError(
            status.HTTP_408_REQUEST_TIMEOUT, STALLED, "upload-stalled", len(buffer)
        ) from None
    except ClientDisconnect:
        raise _NotTakenError(
            status.HTTP_400_BAD_REQUEST, INTERRUPTED, "upload-interrupted", len(buffer)
        ) from None
    return buffer, digest.digest()


def _store_file(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    name: str,
    body: bytearray,
    digest: bytes,
    now: datetime,
) -> documents.Stored:
    """On the threadpool: one session, one transaction."""
    with sessions() as db:
        stored = documents.store_file(db, user_id, name, body, digest, now)
        db.commit()
        return stored


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=StoredOut,
    responses={
        **_401,
        **_STORE_ERRORS,
        400: {"model": ErrorOut, "description": "The client went away during the upload"},
        408: {"model": ErrorOut, "description": "The body stalled"},
        415: {"model": ErrorOut, "description": "A type of file that is not read"},
        503: {"model": ErrorOut, "description": "Both upload slots are busy"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The file itself as the raw body, at most 20 MiB. Its type is read "
            "from its bytes; the Content-Type header is ignored. Name it with the X-Filename "
            "header (RFC 5987).",
            "content": {"application/octet-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def upload_document(request: Request, user: CurrentUser, response: Response) -> StoredOut:
    """Add a file to the caller's documents."""
    declared = request.headers.get("content-length")
    size = int(declared) if declared is not None and declared.isdigit() else 0
    if size > FILE_MAX_BYTES:
        _refuse(
            user,
            status.HTTP_413_CONTENT_TOO_LARGE,
            documents.TOO_LARGE,
            "file-too-large",
            size,
            **CLOSE,
        )
    slots = _slots(request.app)
    try:
        await asyncio.wait_for(slots.acquire(), SLOT_WAIT_S)
    except TimeoutError:
        _refuse(
            user,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            BUSY,
            "upload-busy",
            size,
            **CLOSE,
            **{"Retry-After": str(RETRY_AFTER_S)},
        )
    try:
        body, digest = await _read_body(request)
        size = len(body)
        name = documents.decode_filename(request.headers.get("x-filename"))
        stored = await run_in_threadpool(
            _store_file,
            request.app.state.sessionmaker,
            user.id,
            name,
            body,
            digest,
            deps.clock(request),
        )
    except _NotTakenError as exc:
        # The rest of a body that was refused is not read: the connection ends with the answer.
        _refuse(user, exc.code, exc.detail, exc.rule, exc.size, **CLOSE)
    except documents.DocumentError as exc:
        _refused(exc, user, size)
    finally:
        slots.release()
    return _stored(stored, response)


# --- paste ---------------------------------------------------------------------------------


async def _paste_body(request: Request) -> bytes:
    """The JSON of a paste, refused as soon as it is known to be over PASTE_BODY_MAX_BYTES."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > PASTE_BODY_MAX_BYTES:
        raise _error(status.HTTP_413_CONTENT_TOO_LARGE, PASTE_BODY_TOO_LARGE)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > PASTE_BODY_MAX_BYTES:
            raise _error(status.HTTP_413_CONTENT_TOO_LARGE, PASTE_BODY_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/text",
    status_code=status.HTTP_201_CREATED,
    response_model=StoredOut,
    responses={**_401, **_STORE_ERRORS},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": PasteIn.model_json_schema()}},
        }
    },
)
def paste_document(
    raw: Annotated[bytes, Depends(_paste_body)],
    user: CurrentUser,
    request: Request,
    response: Response,
    db: Db,
) -> StoredOut:
    """Add a pasted text to the caller's documents."""
    try:
        body = PasteIn.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        if errors and errors[0].get("type") == "json_invalid":
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, BAD_JSON) from None
        raise RequestValidationError(
            [{**error, "loc": ("body", *error.get("loc", ()))} for error in errors]
        ) from None
    try:
        stored = documents.store_paste(db, user.id, body.name, body.text, deps.clock(request))
    except documents.DocumentError as exc:
        db.rollback()
        _refused(exc, user, len(raw))
    db.commit()
    return _stored(stored, response)


# --- list, text, confirm, delete -----------------------------------------------------------


@router.get("", response_model=DocumentList, responses=_401)
def list_documents(user: CurrentUser, db: Db) -> DocumentList:
    """The caller's documents, newest first, with what they take and the limits."""
    rows = documents.list_documents(db, user.id)
    used = documents.usage(db, user.id)
    return DocumentList(
        items=[DocumentOut(**_fields(row)) for row in rows],
        usage=UsageOut(bytes=used.bytes, documents=used.documents),
        limits=LimitsOut(),
    )


@router.get(
    "/{document_id}/text",
    response_model=TextOut,
    responses={
        **_401,
        **_404,
        409: {"model": ErrorOut, "description": "The document has no text yet"},
    },
)
def read_text(
    document_id: str,
    user: CurrentUser,
    db: Db,
    offset: Annotated[int, Query(ge=0, le=TEXT_MAX_CHARS)] = 0,
    limit: Annotated[int, Query(ge=1, le=PREVIEW_MAX)] = PREVIEW_DEFAULT,
) -> TextOut:
    """The stored text of a document, `limit` characters from `offset`; `next_offset` is null
    at the end. Offsets count characters of the text as stored."""
    try:
        page = documents.read_text(db, user.id, _id(document_id), offset, limit)
    except documents.NoTextError:
        raise _error(status.HTTP_409_CONFLICT, NO_TEXT_YET) from None
    if page is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return TextOut(
        text=page.text, offset=page.offset, next_offset=page.next_offset, total=page.total
    )


@router.post("/{document_id}/confirm", response_model=DocumentOut, responses={**_401, **_404})
def confirm_document(document_id: str, user: CurrentUser, request: Request, db: Db) -> DocumentOut:
    """The caller read the secret warning and keeps the document."""
    document = documents.confirm_secret(db, user.id, _id(document_id), deps.clock(request))
    if document is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    db.commit()
    return DocumentOut(**_fields(document))


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        **_401,
        **_404,
        409: {"model": ErrorOut, "description": "The document cannot be deleted yet"},
    },
)
def delete_document(document_id: str, user: CurrentUser, db: Db) -> Response:
    """Delete a document for good: its file, its text and its page images."""
    try:
        found = documents.delete_document(db, user.id, _id(document_id))
    except documents.DeleteRefusedError as exc:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, exc.message) from None
    if not found:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
