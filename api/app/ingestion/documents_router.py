"""/api/ingestion/documents: add a file or a pasted text, list, read the text back, confirm a
secret warning, delete (SPEC-ingestion.md, API; the rules are in documents.py).

- POST /api/ingestion/documents: the file itself as the raw request body (no multipart), its
  name in `X-Filename` (RFC 5987, `UTF-8''caf%C3%A9.txt`; display only, missing or malformed is
  "Untitled"). The Content-Type is ignored: the type comes from the bytes. 201 with the document,
  200 when this user already has the same file (`duplicate: true`, nothing written).
  413 over 20 MiB or over the user's 100 MB, 415 for a type that is not read, 422 for an empty
  file, a file without text or a 201st document, 408 when the body stalls, 400 when the client
  goes away, 429 with Retry-After while another body of the same user is still on its way or
  when the user added too many files to read in a minute, 503 with Retry-After when both upload
  slots stay busy.
  A file that a reader must read (a PDF since G2b) is stored `pending`, and its reading is
  queued once the transaction has committed and the turn and the slot are given back
  (`_queue_reading`): the answer comes with the document still `pending`, and the list shows it
  ready or failed a moment later. When the queue cannot take the job the document is failed at
  once and the answer, still 201, says so in `failed_reason`.
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
  SLOT_WAIT_S is longer than IDLE_TIMEOUT_S on purpose: an upload cut off by a change of
  network holds its user's turn until its idle limit, and the file the page sends next must
  still be waiting when that turn comes back, not be refused for an upload nobody is sending.
- At most WAITING_MAX bodies of one user wait for its turn: one more is answered 429 at once.
  A waiting connection holds no turn, no slot and no database connection, but it costs the
  process about 226 KiB for as long as it waits (measured in the second review: 400 of them,
  89 MiB), so without this bound one account could fill the api's memory with connections.
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
  honest upload may take. The floor is 8.5 KiB per second; for a large file the total decides:
  20 MiB in TOTAL_TIMEOUT_S is 35 KiB per second (280 kbit/s).
- Which refusals close the connection. Closing a connection on which bytes are still arriving
  resets it, and Caddy, busy sending the body, then answers 502 in place of the refusal: the
  user reads neither the sentence nor Retry-After (seen in the journey with a 10 MiB paste).
  So the refusals given while a body that Caddy lets through may still be on its way keep the
  connection: 429, 503 and the 413 of a paste (2 MiB here, 10 MiB at Caddy). What is left of
  such a body is dropped by the server as it arrives, in no buffer, holding no turn and no slot,
  and Caddy bounds it. The refusals that end a body which will never be whole (408, 400) close
  it, and so does the 413 of a file, which Caddy refuses itself before the api can.
- A file that costs a reading is counted per user: READS_PER_MINUTE of them in any minute,
  then 429 with Retry-After and a sentence naming the wait (`_read_rate`). Each reading is a
  child process of the parser for up to a minute of its one job slot, which every user shares:
  without this bound one account could keep the reader to itself. It is counted once the body
  is whole and its first bytes say what it is, so a text file, which is read at once and costs
  no job, is never counted, and a file refused for the rate has been uploaded for nothing:
  the page says how long to wait. A duplicate counts too: it is told from a new file only
  later, under the user's lock. What it leaves: the 200 documents an account may keep do not
  cap its readings. A document that failed is replaced by a new upload of the same file, so
  one account can keep sending the same slow files as they fail, ten a minute, for as long as
  it likes. Each costs the single reader up to its budget of 35 s, or two tries of 60 s when
  the child is killed at the wall limit: that is a failure of the server, and its sentence
  invites the user to add the file again. Nothing here caps the documents one user has
  waiting, and the reader takes them in the order they came, whoever sent them.
What this does not prevent: two accounts acting together can each hold one slot for as long as
each keeps sending at that pace, which is the whole of the two slots. A body that ends with 408
stores nothing and counts for nothing, so the quota does not bound such an account: its one
slot does, and the owner, who invites every account and reads the user id with the rule
`upload-stalled` in the log.

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

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from app import queue as queues
from app.agents.ratelimit import RateLimiter
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.ingestion import deletion, documents, jobs
from app.ingestion.models import (
    FILE_MAX_BYTES,
    NAME_MAX_CHARS,
    PAGES_MAX,
    TEXT_MAX_CHARS,
    IngestionDocument,
    TextSource,
    TextStatus,
)

logger = logging.getLogger("app.ingestion")

NO_STORE = {"Cache-Control": "no-store"}
CLOSE = {"Connection": "close"}

UPLOAD_SLOTS = 2  # files held in memory at once, in this process
IDLE_TIMEOUT_S = 30.0  # the longest a body may go without bringing PROGRESS_BYTES more
# How long a body waits, unread, for its user's turn and for a slot, in all. Longer than
# IDLE_TIMEOUT_S (module docstring): the turn of an upload that died comes back within it.
SLOT_WAIT_S = 35.0
WAITING_MAX = 2  # bodies of one user that may wait for its turn; one more is refused at once
RETRY_AFTER_S = 5
PROGRESS_BYTES = 256 * 1024  # what counts as progress: about 70 kbit/s sustained
TOTAL_TIMEOUT_S = 600.0  # the whole file: 20 MiB at 280 kbit/s
PASTE_TIMEOUT_S = 60.0  # the whole JSON of a paste
# The JSON of a paste: 100,000 characters, each at most 12 bytes when escaped.
PASTE_BODY_MAX_BYTES = 2 * 1024 * 1024
# Files that cost a reading (a job of the parser), per user and per minute.
READS_PER_MINUTE = 10
READS_WINDOW_S = 60.0
PREVIEW_DEFAULT = 20_000
PREVIEW_MAX = 50_000

NOT_FOUND = "Document not found."
DELETE_BUSY = (
    "This document is being read or changed right now. Nothing was deleted. Try again in a moment."
)
NO_TEXT_YET = "This document has no text yet."
STALLED = (
    "The upload stopped arriving, or arrives too slowly. Check your connection and add it again."
)
INTERRUPTED = "The upload was interrupted."
BUSY = "Other uploads are in progress. Try again in a moment."
TOO_FAST = (
    f"You added {READS_PER_MINUTE} files to read in less than a minute, the most allowed. "
    "Wait {wait} seconds, then add this one again."
)
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
    429: {
        "model": ErrorOut,
        "description": "Another body of this user is still on its way, or too many files to "
        "read in a minute",
    },
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
    # The sentence to show when the upload was meant to replace that document and could not.
    refused: str | None = None


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


class PageImageOut(BaseModel):
    position: int  # the zero based page index of a PDF page, 0 for an uploaded image
    width: int
    height: int


class TextOut(BaseModel):
    text: str
    offset: int
    next_offset: int | None
    total: int
    # The page images that wait for their transcription, by position (G8): each is served by
    # GET /{document_id}/images/{position}.
    images: list[PageImageOut] = []


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
    return StoredOut(
        **_fields(stored.document), duplicate=not stored.created, refused=stored.refused
    )


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

    @property
    def bodies(self) -> int:
        """Bodies in flight or waiting for their user's turn, of all users."""
        return sum(count for _, count in self._turns.values())

    def crowded(self, user_id: uuid.UUID) -> bool:
        """True when WAITING_MAX bodies of this user already wait behind the one in flight."""
        entry = self._turns.get(user_id)
        return entry is not None and entry[1] > WAITING_MAX

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
    the user's own turn does not come, or at once when enough of its bodies wait already, and
    503 when no slot comes."""
    gate = _gate(request.app)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SLOT_WAIT_S
    # No `Connection: close`: the body may still be arriving (module docstring).
    retry = {"Retry-After": str(RETRY_AFTER_S)}
    if gate.crowded(user.id):
        _refuse(
            user, status.HTTP_429_TOO_MANY_REQUESTS, OWN_BUSY, "upload-own-crowd", size, **retry
        )
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


def _read_rate(app: FastAPI) -> RateLimiter:
    """The counter of the files each user gave the reader in the last minute, of this process
    (one api process), made on first use."""
    rate: RateLimiter | None = getattr(app.state, "ingestion_read_rate", None)
    if rate is None:
        rate = RateLimiter(READS_PER_MINUTE, READS_WINDOW_S)
        app.state.ingestion_read_rate = rate
    return rate


def _costs_a_reading(body: bytearray) -> bool:
    """True when storing this file would queue a job of the parser: its first bytes say it is
    of a kind a reader reads later. A text is read here and now, a type that is refused or
    kept for a later version costs nothing."""
    try:
        mime = documents.sniff(bytes(body[: documents.SNIFF_BYTES]))
    except documents.DocumentError:
        return False
    kind = documents.kind_of(mime) if mime is not None else documents.Kind.text
    return kind in documents.READABLE_KINDS and kind in jobs.PARSER_KIND


async def _queue_reading(request: Request, stored: documents.Stored) -> documents.Stored:
    """Queue the reading of a document that was just stored or replaced and waits for a reader.
    Called after the commit and outside the admission: a slow Redis holds no turn and no slot.
    When the queue cannot take it, the document is failed at once (jobs.fail_unqueued) and
    returned as it then is."""
    document = stored.document
    if not stored.created or not jobs.queueable(document):
        return stored
    try:
        await jobs.enqueue(request.app.state.queue, document)
    except queues.QueueUnavailableError:
        failed = await run_in_threadpool(
            jobs.fail_unqueued,
            request.app.state.sessionmaker,
            document.id,
            document.user_id,
            document.pending_since,
        )
        if failed is not None:
            return documents.Stored(failed, created=True)
    return stored


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
            if _costs_a_reading(body):
                wait = _read_rate(request.app).take(user.id)
                if wait is not None:
                    # The body is whole: the connection stays, the answer is read.
                    _refuse(
                        user,
                        status.HTTP_429_TOO_MANY_REQUESTS,
                        TOO_FAST.format(wait=wait),
                        "upload-rate",
                        size,
                        **{"Retry-After": str(wait)},
                    )
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
    return _stored(await _queue_reading(request, stored), response)


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
        # The connection stays: Caddy lets 10 MiB through on this route (module docstring).
        _refuse(user, status.HTTP_413_CONTENT_TOO_LARGE, *too_large, size)
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
            too_long = exc.code == status.HTTP_413_CONTENT_TOO_LARGE
            _refuse(user, exc.code, exc.detail, exc.rule, exc.size, **({} if too_long else CLOSE))
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
    images = documents.list_images(db, user.id, _id(document_id))
    return TextOut(
        text=page.text,
        offset=page.offset,
        next_offset=page.next_offset,
        total=page.total,
        images=[PageImageOut(**vars(image)) for image in images],
    )


@router.get(
    "/{document_id}/images/{position}",
    response_class=Response,
    responses={**_401, **_404, 200: {"content": {"image/jpeg": {}}}},
)
def read_image(
    document_id: str,
    position: Annotated[int, Path(ge=0, lt=PAGES_MAX)],
    user: CurrentUser,
    db: Db,
) -> Response:
    """One stored page image of a document, to its owner: the JPEG the parser made, and
    nothing a browser could take for anything else. 404 for anyone else and for a position
    that holds no image."""
    jpeg = documents.read_image(db, user.id, _id(document_id), position)
    if jpeg is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return Response(
        content=bytes(jpeg),
        media_type="image/jpeg",
        headers={
            **NO_STORE,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
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
        409: {"model": ErrorOut, "description": "A run or a decision holds it: try again"},
    },
)
def delete_document(document_id: str, user: CurrentUser, request: Request, db: Db) -> Response:
    """Delete a document for good: its file, its text, its page images and transcriptions, its
    citations in proposals and the events of the steps that read it (`deletion`). What the user
    accepted into the profile stays."""
    try:
        found = deletion.delete(db, user.id, _id(document_id), deps.clock(request))
    except deletion.DeletionBusyError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, DELETE_BUSY) from None
    if not found:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
