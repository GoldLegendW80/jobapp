"""Reading an uploaded document: the parse job and its reaper, on the queue `jobapp:ingestion`
(task G2b of the ingestion plan; the queue is declared in app/worker.py, one job at a time).

A document of a kind that has a reader (documents.READABLE_KINDS; PARSER_KIND says which reader)
is stored `pending` with its bytes. Then:

1. The upload route queues `parse_document(document_id)` once its transaction has committed
   (`enqueue`). When Redis cannot take the job the document is failed at once, as a failure of
   the server (`fail_unqueued`): the user adds the file again.
2. The job claims the row (`attempts` + 1, `pending_since` = now), reads the bytes, sends them
   to the parser service (parser_client.parse: the worker itself parses nothing) and gets back
   a result that was checked as hostile data, or an error that says whose failure it is.
3. One transaction writes the outcome, under the user's advisory lock and then the row's own:
   - a text: `text`, the page counts, `truncated`, `note`, `text_status = done`, the secret
     warning of secrets.scan, and the bytes set to NULL;
   - a failure of the file (protected, damaged, no text, too heavy): `failed` for good, with a
     sentence the page shows, and the bytes set to NULL. A new upload of the file replaces it;
   - a failure of the server (the parser down, busy, too slow, a malformed answer): the row
     stays `pending` with `failed_kind = server` as the mark of a try that failed, and the
     reaper queues it once more. At the second failure (MAX_ATTEMPTS) it is `failed` with
     `failed_kind = server`, and the bytes go.
   **It writes only to the upload it claimed**: the row must still be `pending` with the
   `attempts` and the `pending_since` of the claim. A document deleted while it was read is no
   row at all; one deleted and added again is another row; one replaced has another
   `pending_since`. In each case the job writes nothing and answers `superseded`.

The reaper (`reap_documents`, every REAP_SECONDS and once when the worker starts) looks at the
`pending` rows whose type maps into a readable kind (there is no kind column: the types come
from documents.KIND_OF_MIME), oldest `pending_since` first, REAP_BATCH at a time:
- never tried (`attempts = 0`): queued. This is the row whose job was lost, and the row that
  was stored before its kind had a reader (a PDF kept by G1): read on the first tick after the
  deploy, without a new upload. Queueing twice is harmless: a job id names one try of one
  upload (queue.document_job_id), and Redis refuses an id it already holds;
- a try that failed for the server, RETRY_AFTER ago: queued again;
- claimed STALE_AFTER ago and never finished (the worker died): queued again, or failed when
  it was the last try.
A row of a kind without a reader is never selected: it is not queued, not failed, and keeps its
bytes, as the page promised.

The claim is what makes all of this safe to repeat: a second job for a row that another job
holds (claimed less than STALE_AFTER ago, no failure recorded) answers `busy` and does nothing.

Lock order (plan, "Lock order"): the user's documents advisory lock, then the document row.
Every transaction here that writes a row takes them in that order, and nothing else.

Privacy: nothing of a document is logged, returned or left in Redis. The job takes an id and
returns a word; its result is not kept (`keep_result=0` in app/worker.py); the log lines hold
ids, sizes, counts, codes and rule names; an unexpected error is logged as its type and the
line that raised it, never its message.
"""

import asyncio
import logging
import re
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.ingestion import documents, parser_client, secrets
from app.ingestion.documents import Kind
from app.ingestion.models import (
    PAGES_MAX,
    TEXT_MAX_CHARS,
    FailedKind,
    IngestionDocument,
    TextStatus,
)

logger = logging.getLogger("app.ingestion")

# kind of document -> kind of parser job (sandbox/main.py, HANDLERS). G7 adds the archive, G8
# the image and the rendering of the pages a PDF has no text for.
PARSER_KIND: dict[Kind, str] = {Kind.pdf: "pdf_text"}

MAX_ATTEMPTS = 2  # tries of one upload: the first, and one more after a failure of the server
# A whole job: the bytes read (the statement timeout), the parser (parser_client.TIMEOUT_S, 75 s,
# the wait behind another job included), the result written.
JOB_TIMEOUT_S = 120
RETRY_AFTER = timedelta(seconds=30)  # after a failure of the server: the parser restarts
STALE_AFTER = timedelta(seconds=JOB_TIMEOUT_S + 60)  # a claim this old belongs to a dead job
REAP_SECONDS = (0, 15, 30, 45)  # the cron of the reaper
REAP_BATCH = 50  # rows one tick looks at

# What a job answers. Words only: nothing of a document.
DONE = "done"
FAILED = "failed"  # for good
RETRY = "retry"  # a failure of the server, the reaper queues it again
SUPERSEDED = "superseded"  # deleted or replaced while it was read: nothing written
SKIPPED = "skipped"  # no longer pending, or of a kind without a reader
BUSY = "busy"  # another job holds the row
GONE = "gone"
ERROR = "error"  # an unexpected error, logged; the reaper takes the row up again

_AGAIN = "Add the file again"
FILE_SENTENCES = {
    "unreadable": "This PDF could not be read. It may be damaged: export it again from the "
    "application that made it, and add the new file.",
    "protected": "This PDF is protected by a password. Remove the protection, export it "
    "again, and add the new file.",
    "encryption": "This PDF is protected in a way this version cannot open. Remove the "
    "protection, export it again, and add the new file.",
    "no_text": "This PDF holds no text, only images of its pages. Pages like these will be "
    f"read by a later version. {_AGAIN} then.",
    "bomb": "This PDF asks for more work than a file may take here. Export it again from "
    "the application that made it, and add the new file.",
    "too_slow": "This PDF took too long to read. Export it again, or split it, and add the "
    "new file.",
}
FILE_FALLBACK = "This file could not be read."
OVER_QUOTA = (
    f"The text of this file would take your documents over the "
    f"{documents.USER_MAX_BYTES // (1024 * 1024)} MB you can keep here. Delete a document, "
    "then add this file again."
)
SERVER_FAILED = (
    f"The reader was not available, so this file was not read. {_AGAIN} to try once more."
)

Parse = Callable[[str, bytes], Awaitable[parser_client.Parsed]]


# --- sentences ----------------------------------------------------------------------------------


def _pages(first: int, last: int) -> str:
    return (
        f"Page {first} was not read."
        if first == last
        else f"Pages {first} to {last} were not read."
    )


def note_sentence(note: parser_client.Note) -> str:
    """One note of the parser in plain words. The parser sends a code and integers; the words
    are written here."""
    values = note.values
    left_out = _pages(values["from"], values["to"]) if "from" in values and "to" in values else ""
    if note.code == "pages_not_read":
        return f"Only the first {PAGES_MAX} pages of a PDF are read. {left_out}".strip()
    if note.code == "text_limit":
        return f"Only the first {TEXT_MAX_CHARS:,} characters were read. {left_out}".strip()
    if note.code == "time_limit":
        return f"This PDF took too long to read to its end. {left_out}".strip()
    if note.code == "scans_not_read":
        count = values.get("count", 0)
        if count == 1:
            return "1 page holds no text and waits for the image reader."
        return f"{count} pages hold no text and wait for the image reader."
    return "A part of this file was not read."  # a code of a later task, before its sentence


def note_of(parsed: parser_client.Parsed) -> str | None:
    """What was not read, for `ingestion_documents.note`: a sentence per note, None for none."""
    return " ".join(note_sentence(note) for note in parsed.notes) or None


def file_sentence(code: str) -> str:
    return FILE_SENTENCES.get(code, FILE_FALLBACK)


# --- text as it is stored -----------------------------------------------------------------------

_LINE_BREAKS = re.compile("\r\n?|[\x0b\x85\u2028\u2029]")
_CONTROLS = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x9f]")


def clean_pages(text: str) -> str:
    """The text of a parser as it is stored: line breaks as line feeds, no control character,
    **form feeds kept**: they are the page separators, and the page counts were checked against
    them (parser_client.check_parsed). Not documents.clean_text, which turns them into line
    feeds. The reader in the sandbox already writes its text this way; this is the worker's own
    guarantee, whatever a parser sends. It only ever removes or replaces one character by one,
    so the text stays within the length that was checked."""
    return _CONTROLS.sub("", _LINE_BREAKS.sub("\n", text))


# --- what the api does: queue the job of an upload ----------------------------------------------


def queueable(document: IngestionDocument) -> bool:
    """True for a document that waits for a reader that exists."""
    kind = documents.kind_of(document.mime)
    return (
        document.text_status is TextStatus.pending
        and kind in documents.READABLE_KINDS
        and kind in PARSER_KIND
    )


def job_id(document: IngestionDocument) -> str:
    return queues.document_job_id(document.id, document.attempts, document.pending_since)


async def enqueue(queue: queues.Queue, document: IngestionDocument) -> bool:
    """Queue the reading of `document`, after the transaction that stored it has committed.
    False when this try is already queued. Raises queues.QueueUnavailableError."""
    return await queue.enqueue(
        "parse_document", str(document.id), queue=queues.INGESTION, job_id=job_id(document)
    )


def _held(
    db: Session, document_id: uuid.UUID, attempts: int, pending_since: datetime | None
) -> IngestionDocument | None:
    """The row, locked, when it is still the upload that was claimed: pending, with the same
    `attempts` and the same `pending_since`. None for a row that was deleted, replaced, taken
    by another job or finished. The caller holds the user's advisory lock."""
    document = db.execute(
        sa.select(IngestionDocument)
        .where(IngestionDocument.id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        document is None
        or document.text_status is not TextStatus.pending
        or document.attempts != attempts
        or document.pending_since != pending_since
    ):
        return None
    return document


def _fail(db: Session, document: IngestionDocument, kind: FailedKind, reason: str) -> None:
    """Failed for good: the reason stays, the bytes go."""
    document.text_status = TextStatus.failed
    document.failed_kind = kind
    document.failed_reason = reason
    document.pending_since = None
    db.execute(
        sa.update(IngestionDocument).where(IngestionDocument.id == document.id).values(raw=None)
    )
    db.flush()


def fail_unqueued(
    sessions: sessionmaker[Session],
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    pending_since: datetime | None,
) -> IngestionDocument | None:
    """The queue could not take the job of an upload that was just stored: the document is
    failed as a failure of the server, unless something else happened to it meanwhile. Returns
    the document as it is now, None when it is gone."""
    with sessions() as db:
        documents.lock_user(db, user_id)
        document = _held(db, document_id, 0, pending_since)
        if document is not None:
            _fail(db, document, FailedKind.server, SERVER_FAILED)
            _log("document not queued", document, failure="server", code="queue_down")
        db.commit()
        return db.execute(
            sa.select(IngestionDocument).where(
                IngestionDocument.id == document_id, IngestionDocument.user_id == user_id
            )
        ).scalar_one_or_none()


# --- the job ------------------------------------------------------------------------------------


@dataclass
class _Claim:
    """One try of one upload, as the job holds it."""

    document_id: uuid.UUID
    user_id: uuid.UUID
    kind: Kind
    size: int
    attempts: int
    pending_since: datetime
    data: bytes


def _claimable(document: IngestionDocument, now: datetime) -> bool:
    """A try may start: the first, the one after a failure of the server, or the one after a
    job that never came back."""
    return (
        document.attempts == 0
        or document.failed_kind is FailedKind.server
        or document.pending_since is None
        or document.pending_since <= now - STALE_AFTER
    )


def _claim(sessions: sessionmaker[Session], document_id: uuid.UUID, now: datetime) -> _Claim | str:
    """Take the row for one try and read its bytes, or the word that says why not."""
    with sessions() as db:
        user_id = db.execute(
            sa.select(IngestionDocument.user_id).where(IngestionDocument.id == document_id)
        ).scalar_one_or_none()
        if user_id is None:
            return GONE
        documents.lock_user(db, user_id)
        document = db.execute(
            sa.select(IngestionDocument)
            .where(IngestionDocument.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if document is None:
            return GONE
        kind = documents.kind_of(document.mime)
        if not queueable(document):
            return SKIPPED
        if document.attempts >= MAX_ATTEMPTS or not _claimable(document, now):
            return BUSY
        data = db.execute(
            sa.select(IngestionDocument.raw).where(IngestionDocument.id == document_id)
        ).scalar_one()
        document.attempts += 1
        document.pending_since = now
        document.failed_kind = None
        if data is None:
            # A pending document without its bytes cannot be read, now or later.
            _fail(db, document, FailedKind.server, SERVER_FAILED)
            _log("document parse failed", document, failure="server", code="no_bytes")
            db.commit()
            return FAILED
        db.commit()
        return _Claim(
            document_id=document.id,
            user_id=user_id,
            kind=kind,
            size=document.size,
            attempts=document.attempts,
            pending_since=now,
            data=bytes(data),
        )


def _store_text(
    sessions: sessionmaker[Session], claim: _Claim, parsed: parser_client.Parsed, started: float
) -> str:
    """The result of a reader, written in one transaction (module docstring, 3)."""
    text = clean_pages(parsed.text or "")
    if not text.strip():
        return _store_failure(sessions, claim, "file", "no_text", started)
    # Before the locks: a scan of 500,000 characters takes its time.
    secret_rules = secrets.scan(text)
    stored_bytes = len(text.encode("utf-8"))
    with sessions() as db:
        documents.lock_user(db, claim.user_id)
        document = _held(db, claim.document_id, claim.attempts, claim.pending_since)
        if document is None:
            return SUPERSEDED
        # The bytes leave and the text comes: what the user keeps may grow (a small PDF can
        # hold a long text), so the quota is asked again, under the lock every upload takes.
        used = documents.usage(db, claim.user_id).bytes - claim.size
        if used + stored_bytes > documents.USER_MAX_BYTES:
            _fail(db, document, FailedKind.file, OVER_QUOTA)
            _log("document parse failed", document, failure="file", code="quota")
            db.commit()
            return FAILED
        document.text = text
        document.text_status = TextStatus.done
        document.pages = parsed.pages
        document.pages_text = parsed.pages_text
        document.pages_image = parsed.pages_image
        document.truncated = parsed.truncated
        document.note = note_of(parsed)
        document.parser_version = documents.PARSER_VERSION
        document.failed_kind = document.failed_reason = None
        document.pending_since = None
        document.secret_warning = bool(secret_rules)
        document.secret_confirmed_at = None
        db.flush()
        db.execute(
            sa.update(IngestionDocument).where(IngestionDocument.id == document.id).values(raw=None)
        )
        _log(
            "document parsed",
            document,
            characters=len(text),
            pages=parsed.pages,
            pages_text=parsed.pages_text,
            pages_image=parsed.pages_image,
            truncated=parsed.truncated,
            notes=[note.code for note in parsed.notes],
            secret_rules=secret_rules,
            attempts=claim.attempts,
            ms=int((time.monotonic() - started) * 1000),
            # What the parser's child said the reading cost: the figures its limits are set by.
            **{f"child_{name}": value for name, value in (parsed.usage or {}).items()},
        )
        db.commit()
    return DONE


def _store_failure(
    sessions: sessionmaker[Session], claim: _Claim, failure: str, code: str, started: float
) -> str:
    """A try that gave no text (module docstring, 3). `failure` is whose it is."""
    with sessions() as db:
        documents.lock_user(db, claim.user_id)
        document = _held(db, claim.document_id, claim.attempts, claim.pending_since)
        if document is None:
            return SUPERSEDED
        if failure == "file":
            _fail(db, document, FailedKind.file, file_sentence(code))
            outcome = FAILED
        elif claim.attempts >= MAX_ATTEMPTS:
            _fail(db, document, FailedKind.server, SERVER_FAILED)
            outcome = FAILED
        else:
            # Still pending, with its bytes: the mark of a try that failed, for the reaper.
            document.failed_kind = FailedKind.server
            db.flush()
            outcome = RETRY
        _log(
            "document parse failed",
            document,
            failure=failure,
            code=code,
            attempts=claim.attempts,
            outcome=outcome,
            ms=int((time.monotonic() - started) * 1000),
        )
        db.commit()
    return outcome


async def _parse(ctx: dict[str, Any], document_id: uuid.UUID) -> str:
    sessions: sessionmaker[Session] = ctx["sessions"]
    clock: Callable[[], datetime] = ctx["clock"]
    parse: Parse = ctx.get("ingestion_parse") or parser_client.parse
    started = time.monotonic()
    claim = await asyncio.to_thread(_claim, sessions, document_id, clock())
    if isinstance(claim, str):
        return claim
    data, claim.data = claim.data, b""
    try:
        parsed = await parse(PARSER_KIND[claim.kind], data)
    except parser_client.ParserError as error:
        del data
        return await asyncio.to_thread(
            _store_failure, sessions, claim, error.failure, error.code, started
        )
    del data
    if parsed.text is None or parsed.pages is None:
        # Not what a text reader answers: the parser's fault, not the file's.
        return await asyncio.to_thread(
            _store_failure, sessions, claim, "server", "no_text_in_answer", started
        )
    return await asyncio.to_thread(_store_text, sessions, claim, parsed, started)


def _where(error: BaseException) -> str:
    """The file and the line that raised, never the message: a message may quote anything."""
    frames = traceback.extract_tb(error.__traceback__)
    if not frames:
        return ""
    last = frames[-1]
    return f"{last.filename.rsplit('/', 1)[-1]}:{last.lineno}"


async def parse_document(ctx: dict[str, Any], document_id: str) -> str:
    """The arq job: read one document. Returns a word (module constants), never anything of
    the document; nothing is raised to arq, which would log and store the exception."""
    try:
        return await _parse(ctx, uuid.UUID(document_id))
    except Exception as error:
        # No traceback: it would print the message.
        logger.error(
            "document parse job failed",
            extra={
                "document_id": document_id,
                "error": type(error).__name__,
                "where": _where(error),
            },
        )
        return ERROR


# --- the reaper ---------------------------------------------------------------------------------


def readable_mimes() -> list[str]:
    """The stored types whose kind has a reader that a job runs."""
    return sorted(
        mime
        for mime, kind in documents.KIND_OF_MIME.items()
        if kind in documents.READABLE_KINDS and kind in PARSER_KIND
    )


@dataclass(frozen=True)
class Reaped:
    queue: list[tuple[uuid.UUID, str]]  # (document, job id) to queue
    failed: int  # rows whose last try never came back


def reap(sessions: sessionmaker[Session], now: datetime, batch: int = REAP_BATCH) -> Reaped:
    """One tick of the reaper, the part that reads and writes the database (module docstring)."""
    doc = IngestionDocument
    due = sa.or_(
        doc.attempts == 0,
        doc.pending_since.is_(None),
        sa.and_(doc.failed_kind == FailedKind.server, doc.pending_since <= now - RETRY_AFTER),
        doc.pending_since <= now - STALE_AFTER,
    )
    to_queue: list[tuple[uuid.UUID, str]] = []
    failed = 0
    with sessions() as db:
        rows = db.execute(
            sa.select(doc.id, doc.user_id, doc.attempts, doc.pending_since)
            .where(doc.text_status == TextStatus.pending, doc.mime.in_(readable_mimes()), due)
            .order_by(doc.pending_since.asc().nulls_first(), doc.id)
            .limit(batch)
        ).all()
    for document_id, user_id, attempts, pending_since in rows:
        if attempts < MAX_ATTEMPTS:
            to_queue.append(
                (document_id, queues.document_job_id(document_id, attempts, pending_since))
            )
            continue
        with sessions() as db:
            documents.lock_user(db, user_id)
            document = _held(db, document_id, attempts, pending_since)
            if document is not None:
                _fail(db, document, FailedKind.server, SERVER_FAILED)
                _log("document parse failed", document, failure="server", code="lost")
                failed += 1
            db.commit()
    return Reaped(queue=to_queue, failed=failed)


async def reap_documents(ctx: dict[str, Any]) -> int:
    """The cron: queue the documents that wait for nothing, fail those whose last try was lost.
    Returns how many jobs it queued."""
    sessions: sessionmaker[Session] = ctx["sessions"]
    clock: Callable[[], datetime] = ctx["clock"]
    found = await asyncio.to_thread(reap, sessions, clock())
    queued = 0
    for document_id, job in found.queue:
        accepted = await ctx["redis"].enqueue_job(
            "parse_document", str(document_id), _job_id=job, _queue_name=queues.INGESTION
        )
        queued += accepted is not None
    if queued or found.failed:
        logger.info("documents reaped", extra={"queued": queued, "failed": found.failed})
    return queued


def _log(event: str, document: IngestionDocument, **extra: object) -> None:
    logger.info(
        event,
        extra={
            "document_id": str(document.id),
            "user_id": str(document.user_id),
            "size": document.size,
            "mime": document.mime,
            "status": document.text_status.value,
            **extra,
        },
    )
