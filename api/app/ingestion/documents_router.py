"""/api/ingestion/documents: add a file or a pasted text, list, read the text back, confirm a
secret warning, delete (SPEC-ingestion.md, API; the rules are in documents.py).

- POST /api/ingestion/documents: the file itself as the raw request body (no multipart), its
  name in `X-Filename` (RFC 5987, `UTF-8''caf%C3%A9.txt`; display only, missing or malformed is
  "Untitled"). The Content-Type is ignored: the type comes from the bytes. 201 with the document,
  200 when this user already has the same file (`duplicate: true`, nothing written).
  413 over 20 MiB or over the user's 100 MB, 415 for a type that is not read, 422 for an empty
  file, a file without text or a 201st document, 408 when the body stalls, 400 when the client
  goes away, 429 with Retry-After while another body of the same user is still on its way, 503
  with Retry-After when both upload slots stay busy.
- POST /api/ingestion/documents/text {text, name?}: a pasted text, same answers (it takes no
  upload slot, so never 503); 413 over 100,000 characters.
- GET /api/ingestion/documents: the caller's documents, newest first, what they take and the
  limits.
- GET /api/ingestion/documents/{id}/text?offset=&limit=: the stored text, a page at a time;
  409 while the document has no text.
- POST /api/ingestion/documents/{id}/confirm: the user keeps a document that carries a secret
  warning. Idempotent.
- DELETE /api/ingestion/documents/{id}: 204, the document is gone for good; 409 with the reason
  when a guard refuses (none in this task).

How a body is admitted and read (`_Gate`, `_read_body`), since these are the routes that take
a body of some size, one of them 20 MiB:
- A declared Content-Length over the limit is refused before a byte is read.
- One body per user at a time in this process, a file or a paste: a second one of the same user
  waits for the first without its body being read, and is answered 429 after SLOT_WAIT_S. This
  turn is taken before anything else, so one account never holds more than one upload slot.
- At most UPLOAD_SLOTS files are in memory at once in this process: the others wait without
  their body being read, and give up with 503 after the same SLOT_WAIT_S in all. A paste is at
  most 2 MiB and takes no slot.
- The body is read chunk by chunk into one buffer, hashed as it comes, and counted: the read
  stops with 413 at the first byte past the limit, whatever Content-Length said. No second copy
  is made: documents.py sends the buffer to Postgres in pieces.
- A body ends with 408 and a closed connection when it brings less than PROGRESS_BYTES in
  IDLE_TIMEOUT_S without being complete (silence, or a byte now and then: neither keeps a turn
  or a slot), or when it is not complete after its total time. So a client that sends nothing
  useful holds its turn and its slot for IDLE_TIMEOUT_S, never for the ten minutes a slow but
  honest upload may take.
What this does not prevent: two accounts acting together can each hold one slot for as long as
each keeps sending at that pace, which is the whole of the two slots. An account is invited by
the owner, and what it sends counts against its 100 MB.

Every route needs a session (the app-wide default deny) and takes the user from it, never from
the request. Another user's document answers the same 404 as one that does not exist, an id that
is not a UUID too. Every answer under /api/ingestion is `Cache-Control: no-store`, the 401,
403, 405 and 422 that are built outside this router included (`no_store` in routers.py, a
middleware of the app). Nothing is logged but ids, sizes, types, statuses and rule names.
"""

import asyncio
import contextlib
import hashlib
import logging
import uuid
from collections.abc import AsyncIterator
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

UPLOAD_SLOTS = 2  # files held in memory at once, in this process
SLOT_WAIT_S = 20.0  # how long a body waits for its user's turn and for a slot, in all
RETRY_AFTER_S = 5
IDLE_TIMEOUT_S = 30.0  # the longest a body may go without bringing PROGRESS_BYTES more
PROGRESS_BYTES = 256 * 1024  # what counts as progress: about 70 kbit/s sustained
TOTAL_TIMEOUT_S = 600.0  # the whole file: 20 MiB at 280 kbit/s
PASTE_TIMEOUT_S = 60.0  # the whole JSON of a paste
# The JSON of a paste: 100,000 characters, each at most 12 bytes when escaped.
PASTE_BODY_MAX_BYTES = 2 * 1024 * 1024
PREVIEW_DEFAULT = 20_000
PREVIEW_MAX = 50_000

NOT_FOUND = "Document not found."
NO_TEXT_YET = "This document has no text yet."
STALLED = (
    "The upload stopped arriving, or arrives too slowly. Check your connection and add it again."
)
INTERRUPTED = "The upload was interrupted."
BUSY = "Other uploads are in progress. Try again in a moment."
OWN_BUSY = "Another upload of yours is still on its way. Wait for it to finish, then add this one."
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
    400: {"model": ErrorOut, "description": "The client went away while sending"},
    408: {"model": ErrorOut, "description": "The body stalled or arrived too slowly"},
    413: {"model": ErrorOut, "description": "Over the size limit or the user's quota"},
    422: {"model": ErrorOut, "description": "Empty, no text in it, or the 201st document"},
    429: {"model": ErrorOut, "description": "Another body of this user is still on its way"},
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


# --- who may send a body now ---------------------------------------------------------------


class _Gate:
    """Admission of request bodies in this process (module docstring): one body at a time per
    user, and at most UPLOAD_SLOTS files at a time in all."""

    def __init__(self) -> None:
        self.slots = asyncio.Semaphore(UPLOAD_SLOTS)
        # Per user: the lock of the turn, and how many requests hold it or wait for it. An
        # entry leaves with its last request, so the table never grows with the users seen.
        self._turns: dict[uuid.UUID, tuple[asyncio.Lock, int]] = {}

    @property
    def users(self) -> int:
        """Users with a body in flight or waiting (0 when nothing is going on)."""
        return len(self._turns)

    def _leave(self, user_id: uuid.UUID) -> None:
        lock, count = self._turns[user_id]
        if count <= 1:
            del self._turns[user_id]
        else:
            self._turns[user_id] = (lock, count - 1)

    async def take_turn(self, user_id: uuid.UUID, wait_s: float) -> bool:
        """Wait at most `wait_s` for the user's turn. False when it did not come."""
        lock, count = self._turns.get(user_id, (asyncio.Lock(), 0))
        self._turns[user_id] = (lock, count + 1)
        try:
            await asyncio.wait_for(lock.acquire(), wait_s)
        except BaseException as exc:  # the wait ran out, or the request was cancelled
            self._leave(user_id)
            if isinstance(exc, TimeoutError):
                return False
            raise
        return True

    def give_turn(self, user_id: uuid.UUID) -> None:
        self._turns[user_id][0].release()
        self._leave(user_id)


def _gate(app: FastAPI) -> _Gate:
    """The gate of this process, made on first use (inside the running loop)."""
    gate: _Gate | None = getattr(app.state, "ingestion_gate", None)
    if gate is None:
        gate = _Gate()
        app.state.ingestion_gate = gate
    return gate


@contextlib.asynccontextmanager
async def _admitted(request: Request, user: User, size: int, slot: bool) -> AsyncIterator[None]:
    """Hold the user's turn, and one upload slot when `slot`, while the body is read and
    stored. Both waits share SLOT_WAIT_S; the body is not read while waiting. Answers 429 when
    the user's own turn does not come, 503 when no slot does."""
    gate = _gate(request.app)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SLOT_WAIT_S
    retry = {**CLOSE, "Retry-After": str(RETRY_AFTER_S)}
    if not await gate.take_turn(user.id, SLOT_WAIT_S):
        _refuse(user, status.HTTP_429_TOO_MANY_REQUESTS, OWN_BUSY, "upload-own-busy", size, **retry)
    try:
        if slot:
            try:
                await asyncio.wait_for(gate.slots.acquire(), max(0.0, deadline - loop.time()))
            except TimeoutError:
                _refuse(
                    user, status.HTTP_503_SERVICE_UNAVAILABLE, BUSY, "upload-busy", size, **retry
                )
        try:
            yield
        finally:
            if slot:
                gate.slots.release()
    finally:
        gate.give_turn(user.id)


def _declared(request: Request) -> int:
    """The Content-Length the client declared, 0 when it declared none or not a number."""
    declared = request.headers.get("content-length", "")
    return int(declared) if declared.isascii() and declared.isdigit() else 0


class _NotTakenError(Exception):
    """The body was not taken whole: the answer, the rule for the log, and how many bytes had
    arrived when the read stopped."""

    def __init__(self, code: int, detail: str, rule: str, size: int) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.rule = rule
        self.size = size


async def _read_body(
    request: Request, limit: int, total_s: float, too_large: tuple[str, str]
) -> tuple[bytearray, bytes]:
    """The body in one buffer and its SHA-256 (module docstring: counted, hashed and timed
    while it is read). `too_large` is the sentence and the rule of a body over `limit`.
    Raises _NotTakenError."""
    buffer = bytearray()
    digest = hashlib.sha256()
    chunks = request.stream().__aiter__()
    loop = asyncio.get_running_loop()
    # The last moment the body had brought PROGRESS_BYTES more, and its size then.
    progress_at, progress_size = loop.time(), 0
    try:
        async with asyncio.timeout(total_s):
            while True:
                left = progress_at + IDLE_TIMEOUT_S - loop.time()
                if left <= 0:
                    raise TimeoutError
                try:
                    chunk = await asyncio.wait_for(anext(chunks), left)
                except StopAsyncIteration:
                    break
                if len(buffer) + len(chunk) > limit:
                    raise _NotTakenError(
                        status.HTTP_413_CONTENT_TOO_LARGE, *too_large, len(buffer) + len(chunk)
                    )
                buffer += chunk
                digest.update(chunk)
                if len(buffer) - progress_size >= PROGRESS_BYTES:
                    progress_at, progress_size = loop.time(), len(buffer)
    except TimeoutError:
        raise _NotTakenError(
            status.HTTP_408_REQUEST_TIMEOUT, STALLED, "upload-stalled", len(buffer)
        ) from None
    except ClientDisconnect:
        raise _NotTakenError(
            status.HTTP_400_BAD_REQUEST, INTERRUPTED, "upload-interrupted", len(buffer)
        ) from None
    return buffer, digest.digest()


# --- upload --------------------------------------------------------------------------------


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
    size = _declared(request)
    if size > FILE_MAX_BYTES:
        _refuse(
            user,
            status.HTTP_413_CONTENT_TOO_LARGE,
            documents.TOO_LARGE,
            "file-too-large",
            size,
            **CLOSE,
        )
    async with _admitted(request, user, size, slot=True):
        try:
            body, digest = await _read_body(
                request, FILE_MAX_BYTES, TOTAL_TIMEOUT_S, (documents.TOO_LARGE, "file-too-large")
            )
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
            # The rest of a body that was refused is not read: the connection ends with the
            # answer.
            _refuse(user, exc.code, exc.detail, exc.rule, exc.size, **CLOSE)
        except documents.DocumentError as exc:
            _refused(exc, user, size)
    return _stored(stored, response)


# --- paste ---------------------------------------------------------------------------------


def _paste(raw: bytes | bytearray) -> PasteIn:
    """The body of a paste. Raises the 422 of a body that is not what the route takes."""
    try:
        return PasteIn.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        if errors and errors[0].get("type") == "json_invalid":
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, BAD_JSON) from None
        raise RequestValidationError(
            [{**error, "loc": ("body", *error.get("loc", ()))} for error in errors]
        ) from None


def _store_paste(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    name: str | None,
    text: str,
    now: datetime,
) -> documents.Stored:
    """On the threadpool: one session, one transaction."""
    with sessions() as db:
        stored = documents.store_paste(db, user_id, name, text, now)
        db.commit()
        return stored


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
async def paste_document(request: Request, user: CurrentUser, response: Response) -> StoredOut:
    """Add a pasted text to the caller's documents."""
    size = _declared(request)
    too_large = (PASTE_BODY_TOO_LARGE, "paste-too-long")
    if size > PASTE_BODY_MAX_BYTES:
        _refuse(user, status.HTTP_413_CONTENT_TOO_LARGE, *too_large, size, **CLOSE)
    async with _admitted(request, user, size, slot=False):
        try:
            raw, _ = await _read_body(request, PASTE_BODY_MAX_BYTES, PASTE_TIMEOUT_S, too_large)
            size = len(raw)
            body = _paste(raw)
            stored = await run_in_threadpool(
                _store_paste,
                request.app.state.sessionmaker,
                user.id,
                body.name,
                body.text,
                deps.clock(request),
            )
        except _NotTakenError as exc:
            _refuse(user, exc.code, exc.detail, exc.rule, exc.size, **CLOSE)
        except documents.DocumentError as exc:
            _refused(exc, user, size)
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
