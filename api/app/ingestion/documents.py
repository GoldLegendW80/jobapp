"""A user's documents: what an upload accepts, how it is stored, listed, read back and deleted
(SPEC-ingestion.md, Documents; tasks/ingestion/todo.md, Storage of uploads).

What an upload is, decided from its bytes alone (`sniff`), never from its name or a declared type:
- `%PDF-` is a PDF, `PK\\x03\\x04` a ZIP (a DOCX or a LinkedIn export: the parser tells them
  apart, the api opens no archive), the PNG and JPEG magic an image. These are binary kinds:
  the file is kept as it is with the status `pending`, and a parser reads it later.
- A known type this app does not read (legacy Office, RTF, OpenDocument, HEIC, GIF, WebP, a
  program) is refused with 415 and one sentence saying what to do.
- Anything else must be text: UTF-8 (a BOM accepted) without NUL bytes, or UTF-16 with a BOM.
  Text is complete at once: decoded, cleaned (`clean_text`), cut at TEXT_MAX_CHARS, scanned for
  secrets, stored `done`. Its bytes are never stored. A pasted text takes the same path.

READABLE_KINDS says which kinds have a reader in this version: text since G1, since G2b the
PDF, whose upload queues a parse job (jobs.py: the worker sends the file to the parser service
and stores the text it gets back), and since G7 the archive: a ZIP that the parser finds to be
a Word document becomes its text (the row then carries DOCX_MIME), and one it finds to be a
LinkedIn export becomes one document per CSV it knows (`store_export`: the row of the archive
is deleted in the same transaction). Since G8 an image has a reader too (`Kind.image`): the
parser normalises it to one page image, and the document then waits for an agent run to
transcribe it (`awaiting_transcription`), as a PDF with scanned pages does. The images stored
before G8 were kept `pending` with their bytes: the reaper of jobs.py queues them on its first
tick. The list tells the user which documents wait for a later version (a type no reader
exists for), and the reaper which waiting rows to queue.

Storing (`store_file`, `store_paste`), in one transaction that the caller commits:
1. The same file already there for this user (`unique(user_id, sha256)`): it is returned and
   nothing is written, unless the row is replaceable (it failed, or an older parser left it
   `done` with a note): then the new upload takes the row over, same id, back to the start. A
   replacement empties the old text and page images, so it asks the replacement checks first:
   a row a check protects (G15: a run has read it) is returned as it is, with the check's
   sentence.
   For a new file the document count and the quota are checked here a first time, before a
   byte goes to Postgres: a user at the limit costs the database nothing. The check that
   decides is the one of step 3.
2. The bytes of a binary kind travel to Postgres in pieces (`_stage`): a binary COPY into a
   temporary table, 128 KiB at a time, each piece gone before the next is handed over, then one
   UPDATE moves them into the row on the server. The api process therefore holds an upload once,
   in the buffer the route filled. Measured in the api image on 2026-10-11, three files of
   20 MiB on one connection: sent this way the process grows by 0 MiB; sent as a statement
   parameter it grows by 40 MiB, which libpq then keeps for as long as the connection lives.
   An upload refused after this step (step 3 says no) rolls back: the staged bytes are then
   dead rows of that connection's temporary table, at most one file, invisible to every query
   and never in a dump, until the connection's next upload truncates the table or it closes.
3. Under the user's advisory lock: the dedupe again, the document count, the quota (bytes, text
   and page images currently stored), then the row. Two uploads of one file at once give one row
   and two successes; two uploads that together pass the quota give one success and one 413.

Every function takes the caller's `user_id` and filters on it: another user's document is
indistinguishable from one that does not exist. Lock order (plan, "Lock order"): the user's
documents advisory lock comes before a document row (4), and nothing that holds a document row
takes it.

Deleting is a hard delete, done by `deletion.delete` (G15) together with what was drawn from the
document: `lock_document` is the row lock it takes (4).

Nothing here logs a file name, a text or a match: ids, sizes, types, statuses and rule names.
"""

import codecs
import enum
import logging
import re
import struct
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from urllib.parse import unquote_to_bytes

import sqlalchemy as sa
from psycopg.abc import Buffer
from psycopg.copy import LibpqWriter
from psycopg.generators import copy_to
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ingestion import secrets
from app.ingestion.models import (
    FILE_MAX_BYTES,
    NAME_MAX_CHARS,
    TEXT_MAX_CHARS,
    IngestionDocument,
    IngestionImage,
    TextSource,
    TextStatus,
)
from app.profile.schemas import sniff_photo

logger = logging.getLogger("app.ingestion")

USER_MAX_BYTES = 100 * 1024 * 1024  # per user, shown as "100 MB"
USER_MAX_DOCUMENTS = 200
PASTE_MAX_CHARS = 100_000
# Raised by a task whose parser reads more than the one before (G8): a `done` document that an
# older version left with a note is then read again when the user uploads it again.
# 2 since G8: the scanned pages of a PDF are rendered, so a PDF that version 1 left `done`
# with "pages hold no text and wait for the image reader" is read again on a new upload. That
# replacement empties the old text and so asks the replacement checks first (`_store`): a
# document whose text a later task protects is returned as it is.
PARSER_VERSION = 2

UNTITLED = "Untitled"
PASTED_NAME = "Pasted text"
TEXT_MIME = "text/plain"


class Kind(enum.StrEnum):
    text = "text"
    pdf = "pdf"
    archive = "archive"  # a ZIP, or what a parser found it to be: a DOCX
    image = "image"
    other = "other"  # a type this module does not know: shown as a file, never readable


CSV_MIME = "text/csv"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# Every type a document may carry. A task that stores another one adds it here (G7: the CSV
# files of a LinkedIn export are text/csv, a ZIP found to be a DOCX is DOCX_MIME).
KIND_OF_MIME: dict[str, Kind] = {
    TEXT_MIME: Kind.text,
    CSV_MIME: Kind.text,
    "application/pdf": Kind.pdf,
    "application/zip": Kind.archive,
    DOCX_MIME: Kind.archive,
    "image/png": Kind.image,
    "image/jpeg": Kind.image,
}

# The kinds a reader exists for in this version (module docstring).
READABLE_KINDS: frozenset[Kind] = frozenset({Kind.text, Kind.pdf, Kind.archive, Kind.image})

TOO_LARGE = f"The file is larger than {FILE_MAX_BYTES // (1024 * 1024)} MB."
EMPTY = "The file is empty."
NO_TEXT = "There is no text in this file."
NO_TEXT_PASTED = "There is no text to add."
BAD_TEXT = "The text holds characters that cannot be stored. Copy it again and paste it."
PASTE_TOO_LONG = (
    f"The pasted text is longer than {PASTE_MAX_CHARS:,} characters. "
    "Save it as a text file and add the file instead."
)
QUOTA = (
    f"Your documents would take more than the {USER_MAX_BYTES // (1024 * 1024)} MB you can "
    "keep here. Delete one to make room."
)
TOO_MANY = (
    f"You already keep {USER_MAX_DOCUMENTS} documents, the most allowed. Delete one to add another."
)
EXPORT_TOO_MANY = (
    f"This export would take you past the {USER_MAX_DOCUMENTS} documents you can keep here. "
    "Delete some documents, then add the export again."
)
TRUNCATED_NOTE = f"Only the first {TEXT_MAX_CHARS:,} characters were read."
UNSUPPORTED = (
    "This type of file cannot be read. Accepted: PDF, Word (DOCX), plain text, Markdown, JSON, "
    "CSV, PNG, JPEG and the LinkedIn export (ZIP)."
)
_AGAIN = "and add it again."
REFUSED = {
    "legacy-office": f"Old Office files (.doc, .xls, .ppt) cannot be read. Save the file as DOCX "
    f"or PDF {_AGAIN}",
    "rtf": f"RTF files cannot be read. Save the file as DOCX, PDF or plain text {_AGAIN}",
    "opendocument": f"OpenDocument files (.odt) cannot be read. Save the file as DOCX or PDF "
    f"{_AGAIN}",
    "heic": f"HEIC and AVIF photos cannot be read. Convert the photo to JPEG or PNG {_AGAIN}",
    "gif": f"GIF images cannot be read. Convert the image to PNG or JPEG {_AGAIN}",
    "webp": f"WebP images cannot be read. Convert the image to PNG or JPEG {_AGAIN}",
    "program": "This is a program, not a document. It was not kept.",
    "unsupported": UNSUPPORTED,
}


class DocumentError(Exception):
    """An upload refused: the HTTP status, one sentence for the user, and the rule's name for
    the log (never the content)."""

    def __init__(self, status: int, message: str, rule: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.rule = rule


class NoTextError(Exception):
    """The document has no text yet (it waits for a parser, or it failed)."""


@dataclass(frozen=True)
class Stored:
    document: IngestionDocument
    created: bool  # False: the same file was already there and is returned as it is
    refused: str | None = None  # the sentence, when a check kept the upload from replacing it


@dataclass(frozen=True)
class Usage:
    bytes: int  # bytes, text and page images currently stored
    documents: int


@dataclass(frozen=True)
class TextPage:
    text: str
    offset: int
    next_offset: int | None
    total: int


# --- the type, from the bytes --------------------------------------------------------------

SNIFF_BYTES = 512
_ZIP_FIRST_NAME = slice(30, 38)  # the name of the first entry, after the 30 byte local header
_HEIF_BRANDS = frozenset(
    {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}
    | {b"avif", b"avis"}
)
_PROGRAMS = (
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
)


def _refuse(rule: str) -> DocumentError:
    return DocumentError(415, REFUSED[rule], rule)


def sniff(head: bytes) -> str | None:
    """The stored type of a file from its first bytes: the mime of a binary kind, or None when
    the file can only be text (the caller then decodes it). Raises DocumentError (415) for a
    type that is recognised and not read."""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        # An OpenDocument or EPUB container announces itself in a first entry named mimetype.
        if head[_ZIP_FIRST_NAME] == b"mimetype":
            raise _refuse("opendocument" if b"opendocument" in head[38:128] else "unsupported")
        return "application/zip"
    image = sniff_photo(head)
    if image is not None:
        return image
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise _refuse("legacy-office")
    if head.startswith(b"{\\rtf"):
        raise _refuse("rtf")
    if head.startswith((b"GIF87a", b"GIF89a")):
        raise _refuse("gif")
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        raise _refuse("webp")
    if head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS:
        raise _refuse("heic")
    if head.startswith(_PROGRAMS) or (head.startswith(b"MZ") and b"\x00" in head[:64]):
        raise _refuse("program")
    return None


def kind_of(mime: str) -> Kind:
    """The kind of a stored document. A type that is not in KIND_OF_MIME is `other`, never
    taken for one that has a reader."""
    return KIND_OF_MIME.get(mime, Kind.other)


# --- text ----------------------------------------------------------------------------------

_SLICE = 256 * 1024
# Decoded characters kept before cleaning: cleaning only ever shortens what the cut needs.
_RAW_MAX_CHARS = 2 * TEXT_MAX_CHARS
_NEWLINES = re.compile("\r\n?|[\x0b\x0c\x85\u2028\u2029]")
_CONTROLS = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x9f]")


def decode_text(data: bytes | bytearray) -> tuple[str, bool] | None:
    """The beginning of `data` as text and whether there was more, or None when `data` is not
    text: invalid UTF-8, a NUL, or UTF-16 without its BOM. The whole file is checked, 256 KiB at
    a time, and only its first characters are kept: a 20 MiB file never becomes a 20 million
    character string."""
    codec, start = "utf-8", 0
    if data.startswith(codecs.BOM_UTF8):
        start = len(codecs.BOM_UTF8)
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        codec = "utf-16"
    elif data.find(b"\x00") != -1:
        return None
    decoder = codecs.getincrementaldecoder(codec)("strict")
    view = memoryview(data)
    pieces: list[str] = []
    kept, more = 0, False
    try:
        for offset in range(start, len(data), _SLICE):
            piece = decoder.decode(bytes(view[offset : offset + _SLICE]))
            if "\x00" in piece:
                return None
            if kept < _RAW_MAX_CHARS:
                pieces.append(piece)
                kept += len(piece)
            elif piece:
                more = True
        decoder.decode(b"", final=True)  # a sequence cut at the end of the file
    except UnicodeDecodeError:
        return None
    return "".join(pieces), more


def clean_text(raw: str) -> str:
    """Text as it is stored: NFC, every line break a line feed (form feeds too: only a PDF has
    pages), control characters removed, tabs kept."""
    text = unicodedata.normalize("NFC", raw)
    text = _NEWLINES.sub("\n", text)
    return _CONTROLS.sub("", text).lstrip("\ufeff")


@dataclass(frozen=True)
class _Content:
    """A text ready to store."""

    text: str
    truncated: bool
    secret_rules: list[str]

    @property
    def stored_bytes(self) -> int:
        return len(self.text.encode("utf-8"))


def _content(raw: str, more: bool, empty: str) -> _Content:
    cleaned = clean_text(raw)
    text = cleaned[:TEXT_MAX_CHARS]
    if not text.strip():
        raise DocumentError(422, empty, "no-text")
    return _Content(
        text=text,
        truncated=more or len(cleaned) > TEXT_MAX_CHARS,
        secret_rules=secrets.scan(text),
    )


# --- names ---------------------------------------------------------------------------------

_RFC5987 = re.compile(r"(?i)utf-8'[a-z0-9-]{0,35}'(.*)", re.DOTALL)
# Control characters, the BOM, and the bidi controls that let a name read as another one.
_NAME_DROP = re.compile("[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_SEPARATORS = re.compile(r"[/\\]")


def clean_name(raw: str) -> str:
    """A display name: the last path component, no control character, single spaces, at most
    NAME_MAX_CHARS characters, "Untitled" when nothing is left. Never used as a path."""
    name = _NAME_DROP.sub("", unicodedata.normalize("NFC", raw))
    parts = [part for part in _SEPARATORS.split(name) if part.strip(" .")]
    name = " ".join((parts[-1] if parts else "").split())
    return name[:NAME_MAX_CHARS].strip() or UNTITLED


def decode_filename(header: str | None) -> str:
    """The display name from the X-Filename header: RFC 5987 (`UTF-8''caf%C3%A9.txt`), or a plain
    value. Missing, malformed or empty is "Untitled", never an error: the name is display only."""
    if not header or not header.strip():
        return UNTITLED
    value = header.strip()
    encoded = _RFC5987.fullmatch(value)
    try:
        if encoded:
            return clean_name(unquote_to_bytes(encoded.group(1)).decode("utf-8"))
        # A plain value arrives as latin-1: read it as the UTF-8 the client most likely sent.
        return clean_name(value.encode("latin-1").decode("utf-8"))
    except UnicodeError:
        return UNTITLED


# --- storing -------------------------------------------------------------------------------

_STAGE = sa.table("ingestion_upload", sa.column("data", sa.LargeBinary))
_STAGE_DDL = sa.text(
    "CREATE TEMP TABLE IF NOT EXISTS ingestion_upload (data bytea NOT NULL) ON COMMIT DELETE ROWS"
)
_STAGE_EMPTY = sa.text("TRUNCATE ingestion_upload")
_STAGE_COPY = "COPY ingestion_upload (data) FROM STDIN (FORMAT BINARY)"
# PostgreSQL binary COPY: the signature, no flags, no header extension; then one tuple of one
# field (its count, the field's length, its bytes); then the trailer.
_COPY_HEADER = b"PGCOPY\n\xff\r\n\x00" + struct.pack("!ii", 0, 0)
_COPY_TRAILER = struct.pack("!h", -1)
_COPY_PIECE = 128 * 1024


class _SteadyWriter(LibpqWriter):
    """Hands COPY data to libpq a piece at a time and waits until each piece has left for the
    server. psycopg's own writer waits like this on macOS only: elsewhere libpq may queue
    whatever the server has not read yet in its send buffer, which grows towards the size of
    the file when the server is slow and is never given back while the connection lives."""

    def write(self, data: Buffer) -> None:
        for start in range(0, len(data), _COPY_PIECE):
            piece = data[start : start + _COPY_PIECE]
            self.connection.wait(copy_to(self._pgconn, piece, flush=True))


def _stage(db: Session, body: bytes | bytearray) -> None:
    """Send `body` to a temporary table of this transaction, in pieces, so nothing on the client
    grows with the file. The table then holds this body alone (whatever an earlier statement of
    the same transaction staged goes first), and it is empty again at commit or rollback."""
    db.execute(_STAGE_DDL)
    db.execute(_STAGE_EMPTY)
    cursor = db.connection().connection.cursor()
    try:
        with cursor.copy(_STAGE_COPY, writer=_SteadyWriter(cursor)) as copy:
            copy.write(_COPY_HEADER + struct.pack("!hi", 1, len(body)))
            copy.write(memoryview(body))
            copy.write(_COPY_TRAILER)
    finally:
        cursor.close()


def _attach(db: Session, document_id: uuid.UUID) -> None:
    """Move the staged bytes into the document's row, on the server."""
    db.execute(
        sa.update(IngestionDocument)
        .where(IngestionDocument.id == document_id)
        .values(raw=sa.select(_STAGE.c.data).scalar_subquery())
    )


def lock_user(db: Session, user_id: uuid.UUID) -> None:
    """Serialises everything that changes what one user's documents take: the dedupe, the
    count, the quota and the insert of an upload, and the parse job's writes (jobs.py). Taken
    before any document row (lock order)."""
    key = f"ingestion-documents:{user_id}"
    db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(key, 0))))


def _by_hash(
    db: Session, user_id: uuid.UUID, digest: bytes, lock: bool = False
) -> IngestionDocument | None:
    query = sa.select(IngestionDocument).where(
        IngestionDocument.user_id == user_id, IngestionDocument.sha256 == digest
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.execute(query).scalar_one_or_none()


def replaceable(document: IngestionDocument) -> bool:
    """A new upload of the same file takes this row over: it failed, or an older parser left
    it done with a note about what it could not read."""
    return document.text_status is TextStatus.failed or (
        document.text_status is TextStatus.done
        and document.note is not None
        and document.parser_version < PARSER_VERSION
    )


def usage(db: Session, user_id: uuid.UUID) -> Usage:
    """What the user's documents take: the bytes, the text and the page images stored now.
    Neither sum reads a content: octet_length takes the size from the stored value's header."""
    doc = IngestionDocument
    count, stored = db.execute(
        sa.select(
            sa.func.count(),
            sa.func.coalesce(
                sa.func.sum(
                    sa.func.coalesce(sa.func.octet_length(doc.raw), 0)
                    + sa.func.coalesce(sa.func.octet_length(doc.text), 0)
                ),
                0,
            ),
        ).where(doc.user_id == user_id)
    ).one()
    images = db.execute(
        sa.select(
            sa.func.coalesce(
                sa.func.sum(
                    sa.func.octet_length(IngestionImage.jpeg)
                    + sa.func.coalesce(sa.func.octet_length(IngestionImage.text), 0)
                ),
                0,
            )
        ).where(IngestionImage.user_id == user_id)
    ).scalar_one()
    return Usage(bytes=int(stored) + int(images), documents=int(count))


def _check_room(used: Usage, cost: int, new: bool) -> None:
    """Raises DocumentError when `cost` more bytes, or one more document, do not fit."""
    if new and used.documents >= USER_MAX_DOCUMENTS:
        raise DocumentError(422, TOO_MANY, "too-many-documents")
    if used.bytes + cost > USER_MAX_BYTES:
        raise DocumentError(413, QUOTA, "quota")


def _store(
    db: Session,
    user_id: uuid.UUID,
    *,
    name: str,
    mime: str,
    source: TextSource,
    size: int,
    digest: bytes,
    payload: _Content | bytes | bytearray,
    now: datetime,
) -> Stored:
    """Steps 1 to 3 of the module docstring. `payload` is the file of a binary kind, kept as
    it is, or the text of a document that is complete at once."""
    content = payload if isinstance(payload, _Content) else None
    body = None if isinstance(payload, _Content) else payload
    cost = payload.stored_bytes if isinstance(payload, _Content) else len(payload)
    existing = _by_hash(db, user_id, digest)
    if existing is not None and not replaceable(existing):
        _log("document duplicate", existing)
        return Stored(existing, created=False)
    if existing is None:
        _check_room(usage(db, user_id), cost, new=True)
    if body is not None:
        _stage(db, body)
    lock_user(db, user_id)
    document = _by_hash(db, user_id, digest, lock=True)
    if document is not None and not replaceable(document):
        _log("document duplicate", document)
        return Stored(document, created=False)
    replaced = document is not None
    if document is not None:
        refused = next((one for one in (check(db, document) for check in _checks) if one), None)
        if refused:
            _log("document replacement refused", document)
            return Stored(document, created=False, refused=refused)
        # The row starts over: what the failed or partial read left goes first, so the quota
        # below counts the user's other documents only.
        db.execute(sa.delete(IngestionImage).where(IngestionImage.document_id == document.id))
        db.execute(
            sa.update(IngestionDocument)
            .where(IngestionDocument.id == document.id)
            .values(raw=None, text=None)
        )
    _check_room(usage(db, user_id), cost, new=not replaced)
    if document is None:
        document = IngestionDocument(user_id=user_id, sha256=digest, created_at=now)
        db.add(document)
    document.name = name
    document.mime = mime
    document.size = size
    document.text_source = source
    document.text_status = TextStatus.pending if content is None else TextStatus.done
    document.truncated = content is not None and content.truncated
    document.note = TRUNCATED_NOTE if document.truncated else None
    document.pages = document.pages_text = document.pages_image = None
    document.transcribed_pages = None
    # A replaced row keeps its created_at: this is what says since when it waits this time.
    document.pending_since = now if content is None else None
    document.parser_version = PARSER_VERSION
    document.failed_reason = document.failed_kind = None
    document.attempts = 0
    document.secret_warning = content is not None and bool(content.secret_rules)
    document.secret_confirmed_at = None
    if content is not None:
        document.text = content.text
    db.flush()
    if body is not None:
        _attach(db, document.id)
    _log(
        "document replaced" if replaced else "document stored",
        document,
        secret_rules=content.secret_rules if content else [],
    )
    return Stored(document, created=True)


def _guarded(store: Callable[[], Stored], db: Session, user_id: uuid.UUID, digest: bytes) -> Stored:
    """`store()`, or the row another transaction committed meanwhile: the unique constraint is
    the last word on a duplicate, whatever path wrote it."""
    try:
        with db.begin_nested():
            return store()
    except IntegrityError:
        found = _by_hash(db, user_id, digest)
        if found is None:
            raise
        return Stored(found, created=False)


def store_file(
    db: Session,
    user_id: uuid.UUID,
    name: str,
    body: bytes | bytearray,
    digest: bytes,
    now: datetime,
) -> Stored:
    """Keep an uploaded file for `user_id`. `digest` is the SHA-256 of `body`, computed while the
    body was read. Raises DocumentError: 413 over the file size or the user's quota, 415 for a
    type that is not read, 422 for an empty file, a file without text or a 201st document."""
    if not body:
        raise DocumentError(422, EMPTY, "empty")
    if len(body) > FILE_MAX_BYTES:
        raise DocumentError(413, TOO_LARGE, "file-too-large")
    mime = sniff(bytes(body[:SNIFF_BYTES]))
    content = None
    if mime is None:
        decoded = decode_text(body)
        if decoded is None:
            raise _refuse("unsupported")
        content = _content(*decoded, empty=NO_TEXT)
        mime = TEXT_MIME

    def store() -> Stored:
        return _store(
            db,
            user_id,
            name=name,
            mime=mime,
            source=TextSource.transcribed if kind_of(mime) is Kind.image else TextSource.parsed,
            size=len(body),
            digest=digest,
            payload=content if content is not None else body,
            now=now,
        )

    return _guarded(store, db, user_id, digest)


@dataclass(frozen=True)
class ExportEntry:
    """One CSV of a LinkedIn export, ready to store: `name` is a name of the parser's closed
    list (parser_client.LINKEDIN_FILES), `digest` and `size` those of the file in the archive."""

    name: str
    digest: bytes
    size: int
    content: _Content


def export_content(text: str, truncated: bool) -> _Content | None:
    """The text of one CSV of an export as it is stored, None when it holds no text."""
    try:
        return _content(text, truncated, NO_TEXT)
    except DocumentError:
        return None


def export_name(archive: str, file: str) -> str:
    """ "<archive name>: <file name>", the archive's name cut so that the file's is whole."""
    room = max(NAME_MAX_CHARS - len(file) - 2, 0)
    return clean_name(f"{archive[:room].strip()}: {file}")


def store_export(
    db: Session,
    user_id: uuid.UUID,
    archive: IngestionDocument,
    entries: Sequence[ExportEntry],
    now: datetime,
) -> list[Stored]:
    """The CSV files of a LinkedIn export, each a document of its own, and the row of the
    archive gone, in the caller's transaction. The caller holds the user's advisory lock and
    the archive's row.

    Each file is found again by its own hash: one that is already a document of this user is
    left as it is (the same export added twice makes no second copy), unless that row is
    replaceable. Raises DocumentError when the files do not fit the user's document count or
    quota, counted as if the archive were already gone; the caller then rolls back, so nothing
    of the export is kept and the archive's row stays."""
    fresh = cost = 0
    for entry in entries:
        existing = _by_hash(db, user_id, entry.digest)
        fresh += existing is None
        if existing is None or replaceable(existing):
            cost += entry.content.stored_bytes
    used = usage(db, user_id)
    if used.documents - 1 + fresh > USER_MAX_DOCUMENTS:
        raise DocumentError(422, EXPORT_TOO_MANY, "too-many-documents")
    if used.bytes - archive.size + cost > USER_MAX_BYTES:
        raise DocumentError(413, QUOTA, "quota")
    name = archive.name
    _log("document unpacked", archive, files=len(entries), new=fresh)
    db.delete(archive)
    db.flush()
    return [
        _store(
            db,
            user_id,
            name=export_name(name, entry.name),
            mime=CSV_MIME,
            source=TextSource.parsed,
            size=entry.size,
            digest=entry.digest,
            payload=entry.content,
            now=now,
        )
        for entry in entries
    ]


def store_paste(
    db: Session, user_id: uuid.UUID, name: str | None, text: str, now: datetime
) -> Stored:
    """Keep a pasted text. Raises DocumentError: 413 over PASTE_MAX_CHARS or the quota, 422 for
    a text with nothing in it or a 201st document."""
    if len(text) > PASTE_MAX_CHARS:
        raise DocumentError(413, PASTE_TOO_LONG, "paste-too-long")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:  # half of a surrogate pair: no database stores it
        raise DocumentError(422, BAD_TEXT, "bad-unicode") from None
    content = _content(text, False, empty=NO_TEXT_PASTED)
    digest = sha256(encoded).digest()

    def store() -> Stored:
        return _store(
            db,
            user_id,
            name=clean_name(name) if name and name.strip() else PASTED_NAME,
            mime=TEXT_MIME,
            source=TextSource.pasted,
            size=len(encoded),
            digest=digest,
            payload=content,
            now=now,
        )

    return _guarded(store, db, user_id, digest)


# --- reading -------------------------------------------------------------------------------


def list_documents(db: Session, user_id: uuid.UUID) -> list[IngestionDocument]:
    """The user's documents, newest first. Neither the bytes nor the text are loaded."""
    return list(
        db.execute(
            sa.select(IngestionDocument)
            .where(IngestionDocument.user_id == user_id)
            .order_by(IngestionDocument.created_at.desc(), IngestionDocument.id)
        ).scalars()
    )


def _locked(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> IngestionDocument | None:
    """The user's document, its row locked until the transaction ends (lock order: 4)."""
    return db.execute(
        sa.select(IngestionDocument)
        .where(IngestionDocument.id == document_id, IngestionDocument.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def read_text(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, offset: int, limit: int
) -> TextPage | None:
    """`limit` characters of the stored text from `offset` (form feeds between the pages of a
    PDF included: offsets are those of the text as stored). None when the user has no such
    document; NoTextError when it has no text yet."""
    row = db.execute(
        sa.select(
            sa.func.substr(IngestionDocument.text, offset + 1, limit),
            sa.func.char_length(IngestionDocument.text),
        ).where(IngestionDocument.id == document_id, IngestionDocument.user_id == user_id)
    ).one_or_none()
    if row is None:
        return None
    piece, total = row
    if total is None:
        raise NoTextError
    end = offset + len(piece)
    following = end if end < total else None
    return TextPage(text=piece, offset=offset, next_offset=following, total=total)


@dataclass(frozen=True)
class PageImageInfo:
    position: int
    width: int
    height: int


def list_images(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> list[PageImageInfo]:
    """The page images stored for this document of this user, by position: the zero based
    page index of a PDF page, 0 for an uploaded image. Empty for anyone else's document."""
    rows = db.execute(
        sa.select(IngestionImage.position, IngestionImage.width, IngestionImage.height)
        .where(IngestionImage.document_id == document_id, IngestionImage.user_id == user_id)
        .order_by(IngestionImage.position)
    ).all()
    return [PageImageInfo(position, width, height) for position, width, height in rows]


def read_image(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, position: int
) -> bytes | None:
    """The stored JPEG of one page image, None when this user has no such image."""
    return db.execute(
        sa.select(IngestionImage.jpeg).where(
            IngestionImage.document_id == document_id,
            IngestionImage.user_id == user_id,
            IngestionImage.position == position,
        )
    ).scalar_one_or_none()


def confirm_secret(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, now: datetime
) -> IngestionDocument | None:
    """The user read the warning and keeps the document. Idempotent: the first confirmation's
    date stays, and a document without a warning is returned unchanged."""
    document = _locked(db, user_id, document_id)
    if document is None:
        return None
    if document.secret_warning and document.secret_confirmed_at is None:
        document.secret_confirmed_at = now
        db.flush()
        _log("document secret warning confirmed", document)
    return document


# --- replacing what a run has read ---------------------------------------------------------------

# Returns the sentence that refuses to replace the document's text, or None to let it through.
ReplacementCheck = Callable[[Session, IngestionDocument], str | None]
_checks: list[ReplacementCheck] = []


def register_replacement_check(check: ReplacementCheck) -> None:
    """Called once at import by a later task's own module (G15's deletion.py), which the api
    imports from its router file. A check runs under the document's row lock: it may read, and
    must take no lock that comes before a document row in the lock order."""
    if check not in _checks:
        _checks.append(check)


def lock_document(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID
) -> IngestionDocument | None:
    """The user's document, its row locked until the transaction ends (4), or None."""
    return _locked(db, user_id, document_id)


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
