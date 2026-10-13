"""Reading an uploaded document: the parse job and its reaper, on the queue `jobapp:ingestion`
(task G2b of the ingestion plan; the queue is declared in app/worker.py, one job at a time).

A document of a kind that has a reader (documents.READABLE_KINDS; PARSER_KIND says which reader)
is stored `pending` with its bytes. Then:

(An archive, G7, is asked twice at most: the parser's `docx` reader first, and when that one
answers `not_docx`, its `linkedin_zip` reader. A Word document is stored as a text below, its
row then carrying DOCX_MIME. An export is stored by `_store_export`: one transaction makes a
document of each CSV, found again by its own hash, and deletes the row of the archive. A ZIP
that is neither is failed with its reason and keeps its row, as any file that cannot be read.)

1. The upload route queues `parse_document(document_id)` once its transaction has committed
   (`enqueue`). When Redis cannot take the job the document is failed at once, as a failure of
   the server (`fail_unqueued`): the user adds the file again.
2. The job claims the row (`attempts` + 1, `pending_since` = now), reads the bytes, sends them
   to the parser service (parser_client.parse: the worker itself parses nothing) and gets back
   a result that was checked as hostile data, or an error that says whose failure it is.
   A claim that raises before its commit (the database refuses something about this row) is
   counted as a try that failed for the server, in a transaction of its own that does not
   read the bytes (`_count_lost_claim`): otherwise `attempts` would stay 0 and the reaper
   would queue the row at every tick, without end.
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

Images and scanned pages (G8). An uploaded PNG or JPEG goes to the parser's `image` reader and
comes back as one normalised JPEG. A PDF is asked for its text first (`pdf_text`, or the text
job of `pdf_render` for a file protected with AES), then, when a page has no text (an empty
slot) or a short one (SECOND_TRIGGER_CHARS, the second trigger), for the images of those pages:
`pdf_render` in batches of RENDER_BATCH, RENDER_MAX images at most, all kept in this job's
memory (9 MB at most). A PDF whose every page has text never reaches the renderer. Then ONE
transaction (`_store_pages`) writes the text with an empty slot per page that waits, the
counts, `awaiting_transcription`, every image at its zero based page index (0 for an uploaded
image) and sets the bytes to NULL. One rule for a failure in any batch: the document fails as
it would have at the first call (for good for the file's fault, once more for the server's),
and no image of an earlier batch is kept, since none was written. A scanned page that got no
image (over RENDER_MAX, a size the renderer refuses, the time budget) stays an empty slot and
is counted in the note, never in `pages_image`: `pages_image` is the number of stored images.

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
    IngestionImage,
    TextStatus,
)

logger = logging.getLogger("app.ingestion")

# kind of document -> kind of parser job (sandbox/main.py, HANDLERS). An archive is asked as a
# Word document first, then as an export (module docstring). G8 adds the image and the
# rendering of the pages a PDF has no text for.
PARSER_KIND: dict[Kind, str] = {Kind.pdf: "pdf_text", Kind.archive: "docx", Kind.image: "image"}
RENDER_KIND = "pdf_render"  # the scanned pages of a PDF, and the text of one only PDFium opens
RENDER_MAX = 30  # pages of one PDF kept as images (plan, Limits): the rest is named in the note
RENDER_BATCH = parser_client.MAX_IMAGES  # images one child renders
# A page with a text this short is rendered too when an image covers half of it (the second
# trigger of the plan: a scan under a footer or a watermark). Counted without whitespace and
# without the line of link addresses the reader adds.
SECOND_TRIGGER_CHARS = 200
LINKS_LABEL = "Links: "
EXPORT_PARSER_KIND = "linkedin_zip"
NOT_DOCX = "not_docx"  # the answer of the `docx` reader that sends an archive to the other one

MAX_ATTEMPTS = 2  # tries of one upload: the first, and one more after a failure of the server
# A whole job: the bytes read (the statement timeout), the parser, the result written. A call to
# the parser takes parser_client.TIMEOUT_S at most (75 s, the wait behind another job included),
# and since G8 a PDF may take six: its text, its text again through PDFium when it is protected
# with AES, and four batches of RENDER_BATCH pages for RENDER_MAX images. Measured, an honest
# PDF of 30 scanned pages takes four calls of about a second each (plan, G8, Measurements).
JOB_TIMEOUT_S = 6 * 75 + 30
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
    "no_text": "This PDF holds no text, and its pages could not be turned into images. "
    "Export it again from the application that made it, and add the new file.",
    "bomb": "This PDF asks for more work than a file may take here. Export it again from "
    "the application that made it, and add the new file.",
    "too_slow": "This PDF took too long to read. Export it again, or split it, and add the "
    "new file.",
}
# The same codes for a Word document or a ZIP, and the codes only an archive has.
_NEITHER = (
    "This ZIP file is neither a Word document nor a LinkedIn data export, so there is "
    "nothing in it to read."
)
_SAVE_AGAIN = "Save it again from the application that made it, and add the new file."
ARCHIVE_SENTENCES = {
    "unreadable": f"This file could not be read. It may be damaged. {_SAVE_AGAIN}",
    "not_docx": _NEITHER,
    "not_export": _NEITHER,
    "macros": "This Word document carries macros, which are never opened here. Save it as "
    "a .docx file without macros, and add the new file.",
    "bomb": f"This file unpacks to more than a file may hold here. {_SAVE_AGAIN}",
    "nested_archive": "This file holds another archive inside it, which is never opened "
    "here. Add the files it holds one by one.",
    "protected": "This file is protected by a password. Remove the protection, and add "
    "the new file.",
    "not_utf8": "A file of this export is not text in UTF-8, so the export was not read. "
    "Download the export again from LinkedIn, and add the new file.",
    "no_text": "There is no text in this file.",
    "too_slow": f"This file took too long to read. {_SAVE_AGAIN}",
}
# The same codes for a PNG or a JPEG (G8, sandbox/image.py).
IMAGE_SENTENCES = {
    "unreadable": "This image could not be read. It may be damaged, or it is not a PNG or a "
    "JPEG: save it again as one of the two, and add the new file.",
    "too_many_pixels": "This image is too large: more than 25 megapixels, or more than 20,000 "
    "pixels on a side. Save a smaller copy, and add the new file.",
    "bomb": "This image asks for more work than a file may take here. Save it again as a PNG "
    "or a JPEG, and add the new file.",
    "too_slow": "This image took too long to read. Save a smaller copy, and add the new file.",
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

Parse = Callable[..., Awaitable[parser_client.Parsed]]  # (kind, data) or (kind, data, options)


# --- sentences ----------------------------------------------------------------------------------


def _pages(first: int, last: int) -> str:
    return (
        f"Page {first} was not read."
        if first == last
        else f"Pages {first} to {last} were not read."
    )


def note_sentence(note: parser_client.Note, kind: Kind = Kind.pdf) -> str:
    """One note of the parser in plain words. The parser sends a code and integers; the words
    are written here."""
    values = note.values
    left_out = _pages(values["from"], values["to"]) if "from" in values and "to" in values else ""
    if note.code == "pages_not_read":
        return f"Only the first {PAGES_MAX} pages of a PDF are read. {left_out}".strip()
    if note.code == "text_limit":
        return f"Only the first {TEXT_MAX_CHARS:,} characters were read. {left_out}".strip()
    if note.code == "time_limit":
        what = "PDF" if kind is Kind.pdf else "document"
        return f"This {what} took too long to read to its end. {left_out}".strip()
    if note.code == "scans_not_read":
        count = values.get("count", 0)
        if count == 1:
            return "1 page holds no text and waits for the image reader."
        return f"{count} pages hold no text and wait for the image reader."
    count = values.get("count", 0)
    scans = "1 scanned page was" if count == 1 else f"{count} scanned pages were"
    if note.code == "scans_over_cap":
        return (
            f"Only the first {RENDER_MAX} scanned pages of a PDF are kept as images. "
            f"{scans} left out."
        )
    if note.code == "pages_not_rendered":
        return f"{scans} left out: a page of that size or in that state cannot become an image."
    if note.code == "render_time_limit":
        return f"This PDF took too long to turn into images. {scans} left out."
    return "A part of this file was not read."  # a code of a later task, before its sentence


def note_of(parsed: parser_client.Parsed, kind: Kind = Kind.pdf) -> str | None:
    """What was not read, for `ingestion_documents.note`: a sentence per note, None for none."""
    return " ".join(note_sentence(note, kind) for note in parsed.notes) or None


def file_sentence(code: str, kind: Kind = Kind.pdf) -> str:
    sentences = {Kind.archive: ARCHIVE_SENTENCES, Kind.image: IMAGE_SENTENCES}.get(
        kind, FILE_SENTENCES
    )
    return sentences.get(code, FILE_FALLBACK)


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


def _take(db: Session, document_id: uuid.UUID, now: datetime) -> IngestionDocument | str:
    """The row, locked under its user's documents lock, when a job may try it now. Otherwise
    the word that says why not."""
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
    if not queueable(document):
        return SKIPPED
    if document.attempts >= MAX_ATTEMPTS or not _claimable(document, now):
        return BUSY
    return document


def _claim(sessions: sessionmaker[Session], document_id: uuid.UUID, now: datetime) -> _Claim | str:
    """Take the row for one try and read its bytes, or the word that says why not."""
    with sessions() as db:
        document = _take(db, document_id, now)
        if isinstance(document, str):
            return document
        user_id = document.user_id
        kind = documents.kind_of(document.mime)
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


def _count_lost_claim(
    sessions: sessionmaker[Session], document_id: uuid.UUID, now: datetime
) -> str:
    """The claim raised before its commit, so nothing says that a try took place. Count it, as
    a try that failed for the server: the row stays `pending` with the mark the reaper waits
    RETRY_AFTER on, and at MAX_ATTEMPTS it is failed for good. Same locks and same conditions
    as the claim, without reading the bytes, which may be what the database refused."""
    with sessions() as db:
        document = _take(db, document_id, now)
        if isinstance(document, str):
            return document
        document.attempts += 1
        if document.attempts >= MAX_ATTEMPTS:
            _fail(db, document, FailedKind.server, SERVER_FAILED)
            outcome = FAILED
        else:
            document.pending_since = now
            document.failed_kind = FailedKind.server
            db.flush()
            outcome = RETRY
        _log(
            "document parse failed",
            document,
            failure="server",
            code="claim",
            attempts=document.attempts,
            outcome=outcome,
        )
        db.commit()
    return outcome


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
        document.note = note_of(parsed, claim.kind)
        if claim.kind is Kind.archive:
            document.mime = documents.DOCX_MIME  # what the parser found the ZIP to be
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


def _store_pages(
    sessions: sessionmaker[Session], claim: _Claim, parsed: parser_client.Parsed, started: float
) -> str:
    """A document with page images, written in ONE transaction: its text with an empty slot
    per page that waits, its counts, `awaiting_transcription`, its images at their page index,
    and its bytes set to NULL. Under the same locks and the same guard as a text: a document
    deleted or replaced while it was rendered gets nothing, not even one image."""
    text = clean_pages(parsed.text or "")
    secret_rules = secrets.scan(text)
    stored_bytes = len(text.encode("utf-8")) + sum(len(image.jpeg) for image in parsed.images)
    with sessions() as db:
        documents.lock_user(db, claim.user_id)
        document = _held(db, claim.document_id, claim.attempts, claim.pending_since)
        if document is None:
            return SUPERSEDED
        # The bytes leave, the text and the images come: the quota is asked again.
        used = documents.usage(db, claim.user_id).bytes - claim.size
        if used + stored_bytes > documents.USER_MAX_BYTES:
            _fail(db, document, FailedKind.file, OVER_QUOTA)
            _log("document parse failed", document, failure="file", code="quota")
            db.commit()
            return FAILED
        document.text = text
        document.text_status = TextStatus.awaiting_transcription
        document.pages = parsed.pages
        document.pages_text = parsed.pages_text
        document.pages_image = len(parsed.images)
        document.transcribed_pages = None
        document.truncated = parsed.truncated
        document.note = note_of(parsed, claim.kind)
        document.parser_version = documents.PARSER_VERSION
        document.failed_kind = document.failed_reason = None
        document.pending_since = None
        document.secret_warning = bool(secret_rules)
        document.secret_confirmed_at = None
        db.flush()
        db.execute(
            sa.update(IngestionDocument).where(IngestionDocument.id == document.id).values(raw=None)
        )
        # None is there (an upload that replaces a row empties it), but the unique position
        # must hold whatever a row went through.
        db.execute(sa.delete(IngestionImage).where(IngestionImage.document_id == document.id))
        db.add_all(
            IngestionImage(
                document_id=document.id,
                user_id=claim.user_id,
                position=image.position,
                jpeg=image.jpeg,
                width=image.width,
                height=image.height,
            )
            for image in parsed.images
        )
        _log(
            "document rendered",
            document,
            characters=len(text),
            pages=parsed.pages,
            pages_text=parsed.pages_text,
            pages_image=len(parsed.images),
            image_bytes=stored_bytes - len(text.encode("utf-8")),
            truncated=parsed.truncated,
            notes=[note.code for note in parsed.notes],
            secret_rules=secret_rules,
            attempts=claim.attempts,
            ms=int((time.monotonic() - started) * 1000),
        )
        db.commit()
    return DONE


def _store_export(
    sessions: sessionmaker[Session],
    claim: _Claim,
    parsed: parser_client.Parsed,
    started: float,
    now: datetime,
) -> str:
    """The files of a LinkedIn export, written in one transaction: a document per CSV, and the
    row of the archive deleted. An export that does not fit keeps nothing but its row, failed
    with the reason."""
    entries = []
    secret_rules: set[str] = set()
    for file in parsed.documents:
        # Before the locks: the scan for secrets takes its time on a long text.
        content = documents.export_content(file.text, file.truncated)
        if content is not None:
            entries.append(documents.ExportEntry(file.name, file.sha256, file.size, content))
            secret_rules.update(content.secret_rules)
    if not entries:
        return _store_failure(sessions, claim, "file", "no_text", started)
    with sessions() as db:
        documents.lock_user(db, claim.user_id)
        document = _held(db, claim.document_id, claim.attempts, claim.pending_since)
        if document is None:
            return SUPERSEDED
        try:
            stored = documents.store_export(db, claim.user_id, document, entries, now)
        except documents.DocumentError as refused:
            # Nothing of the export is kept: the transaction starts over, for the row alone.
            db.rollback()
            documents.lock_user(db, claim.user_id)
            document = _held(db, claim.document_id, claim.attempts, claim.pending_since)
            if document is None:
                return SUPERSEDED
            _fail(db, document, FailedKind.file, refused.message)
            _log("document parse failed", document, failure="file", code=refused.rule)
            db.commit()
            return FAILED
        logger.info(
            "export unpacked",
            extra={
                "document_id": str(claim.document_id),
                "user_id": str(claim.user_id),
                "size": claim.size,
                "files": len(entries),
                "new_files": sum(1 for one in stored if one.created),
                "characters": sum(len(entry.content.text) for entry in entries),
                "secret_rules": sorted(secret_rules),
                "attempts": claim.attempts,
                "ms": int((time.monotonic() - started) * 1000),
                **{f"child_{name}": value for name, value in (parsed.usage or {}).items()},
            },
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
            _fail(db, document, FailedKind.file, file_sentence(code, claim.kind))
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


def _short(slot: str) -> bool:
    """True for a page whose own text is under SECOND_TRIGGER_CHARS characters."""
    lines = slot.split("\n")
    if lines[-1].startswith(LINKS_LABEL):
        lines.pop()
    return sum(len("".join(line.split())) for line in lines) < SECOND_TRIGGER_CHARS


def _all_scans(error: parser_client.ParserRefusedError) -> parser_client.Parsed:
    """A PDF of which no page has text, as the reader counted it: one empty slot per page."""
    count = error.values.get("count", 0)
    if not 1 <= count <= PAGES_MAX:
        raise error
    return parser_client.Parsed(
        text=parser_client.PAGE_BREAK * (count - 1), pages=count, pages_text=0, pages_image=count
    )


async def _read_pdf(parse: Parse, data: bytes) -> parser_client.Parsed:
    """The text of a PDF and the images of its scanned pages (module docstring, G8)."""
    try:
        parsed = await parse(PARSER_KIND[Kind.pdf], data)
    except parser_client.ParserRefusedError as error:
        if error.failure != "file" or error.code not in ("encryption", "no_text"):
            raise
        if error.code == "no_text":
            parsed = _all_scans(error)
        else:
            # AES: the reader cannot open it (no `cryptography` in the parser), PDFium can.
            try:
                parsed = await parse(RENDER_KIND, data, {"text": True})
            except parser_client.ParserRefusedError as inner:
                if inner.failure != "file" or inner.code != "no_text":
                    raise
                parsed = _all_scans(inner)
    if parsed.text is None or parsed.pages is None:
        return parsed  # not what a text reader answers: `_parse` says so
    return await _render(parse, data, parsed)


async def _render(parse: Parse, data: bytes, parsed: parser_client.Parsed) -> parser_client.Parsed:
    """`parsed` with the images of its scanned pages. A PDF whose every page has a text of
    SECOND_TRIGGER_CHARS characters comes back as it is, and the renderer is never asked."""
    slots = (parsed.text or "").split(parser_client.PAGE_BREAK)
    scans = [index for index, slot in enumerate(slots) if not slot]
    short = [index for index, slot in enumerate(slots) if slot and _short(slot)]
    if not scans and not short:
        return parsed
    pages, total = sorted(scans + short), parsed.pages
    images: list[parser_client.PageImage] = []
    not_rendered, stopped = 0, False
    while pages and len(images) < RENDER_MAX:
        limit = min(RENDER_BATCH, RENDER_MAX - len(images))
        options = {"pages": pages, "check": [i for i in short if i in pages], "limit": limit}
        try:
            batch = await parse(RENDER_KIND, data, options)
        except parser_client.ParserRefusedError as error:
            if error.failure != "file" or error.code != "too_slow":
                raise  # one rule: a batch that fails fails the document, whatever came before
            stopped = True  # the time budget, as a batch that stops in its middle says it
            break
        positions = [image.position for image in batch.images]
        if (
            batch.text is not None
            or batch.pages is None
            or len(positions) > limit
            or positions != sorted(positions)
            or not set(positions) <= set(pages)
        ):
            raise parser_client.ParserMalformedError("render_positions")
        images += batch.images
        total = batch.pages
        for note in batch.notes:
            if note.code == "pages_not_rendered":
                not_rendered += note.values.get("count", 0)
            stopped = stopped or note.code == "time_limit"
        if stopped or len(positions) < limit:
            pages = []  # nothing is left to ask for: the list was gone through, or time is up
        else:
            pages = [index for index in pages if index > positions[-1]]
    rendered = {image.position for image in images}
    left = len([index for index in scans if index not in rendered])
    for index in short:
        if index in rendered:
            slots[index] = ""  # the image holds the page, footer included
    texts = sum(1 for slot in slots if slot)
    if not images and not texts:
        raise parser_client.ParserRefusedError("too_slow" if stopped else "no_text", failure="file")
    notes = [note for note in parsed.notes if note.code != "scans_not_read"]
    if total > len(slots) and not any(note.code == "pages_not_read" for note in notes):
        notes.append(parser_client.Note("pages_not_read", {"from": len(slots) + 1, "to": total}))
    if left:
        not_rendered = min(not_rendered, left)
        if not_rendered:
            notes.append(parser_client.Note("pages_not_rendered", {"count": not_rendered}))
        if left > not_rendered:
            code = "render_time_limit" if stopped else "scans_over_cap"
            notes.append(parser_client.Note(code, {"count": left - not_rendered}))
    return parser_client.Parsed(
        text=parser_client.PAGE_BREAK.join(slots),
        pages=max(total, len(slots)),
        pages_text=texts,
        pages_image=len(images),
        truncated=parsed.truncated or bool(left),
        notes=tuple(notes),
        images=tuple(images),
        usage=parsed.usage,
    )


async def _read(parse: Parse, kind: Kind, data: bytes) -> parser_client.Parsed:
    """What the parser makes of `data`. An archive is a Word document or an export, and only
    the parser can tell: its `docx` reader is asked first, and the `linkedin_zip` one when the
    first answers that the ZIP is not a Word document. A PDF is asked for its text, then for
    the images of its pages that have none."""
    if kind is Kind.pdf:
        return await _read_pdf(parse, data)
    try:
        return await parse(PARSER_KIND[kind], data)
    except parser_client.ParserRefusedError as error:
        if kind is not Kind.archive or error.failure != "file" or error.code != NOT_DOCX:
            raise
    return await parse(EXPORT_PARSER_KIND, data)


async def _parse(ctx: dict[str, Any], document_id: uuid.UUID) -> str:
    sessions: sessionmaker[Session] = ctx["sessions"]
    clock: Callable[[], datetime] = ctx["clock"]
    parse: Parse = ctx.get("ingestion_parse") or parser_client.parse
    started = time.monotonic()
    now = clock()
    try:
        claim = await asyncio.to_thread(_claim, sessions, document_id, now)
    except Exception as error:
        # No traceback: it would print the message.
        logger.error(
            "document claim failed",
            extra={
                "document_id": str(document_id),
                "error": type(error).__name__,
                "where": _where(error),
            },
        )
        return await asyncio.to_thread(_count_lost_claim, sessions, document_id, now)
    if isinstance(claim, str):
        return claim
    data, claim.data = claim.data, b""
    try:
        parsed = await _read(parse, claim.kind, data)
    except parser_client.ParserError as error:
        del data
        return await asyncio.to_thread(
            _store_failure, sessions, claim, error.failure, error.code, started
        )
    del data
    if parsed.documents:
        return await asyncio.to_thread(_store_export, sessions, claim, parsed, started, now)
    if parsed.images:
        # An image, or a PDF with scanned pages: it waits for its transcription.
        if parsed.text is None or parsed.pages is None or claim.kind is Kind.archive:
            return await asyncio.to_thread(
                _store_failure, sessions, claim, "server", "images_in_answer", started
            )
        return await asyncio.to_thread(_store_pages, sessions, claim, parsed, started)
    if claim.kind is Kind.image:
        return await asyncio.to_thread(
            _store_failure, sessions, claim, "server", "no_image_in_answer", started
        )
    # A PDF comes with its page counts, a Word document has no pages.
    if parsed.text is None or (parsed.pages is None) is (claim.kind is Kind.pdf):
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
