"""The worker's side of the parser service (task G2a of the ingestion plan).

The worker never parses a document and never decodes an image: it sends the bytes to the
`parser` service (compose.yml; app/ingestion/sandbox/server.py holds the protocol) and gets an
answer back. This module is that call, and above all the reading of the answer.

**Every byte of the answer is hostile.** The parser exists because a parser of hostile files is
where exploits live: what comes back may have been written by a process a file took over. So
nothing of it is stored, logged or shown before it has passed here:

- sizes first: a header line of at most 4096 bytes, a body of exactly the `length` announced
  and of at most 4 MiB, nothing after it;
- then the JSON, read strictly: UTF-8, no duplicate key, no NaN, no float (nothing here is
  one), a depth and a size of number the decoder refuses by itself (ValueError and
  RecursionError are both caught);
- then the shape: known keys only, every value of its type, every count in its range, and the
  caps of the columns the result is stored in (500,000 characters of text, 300,000 bytes and
  2,000 pixels a side per image; migration 0011), so that a result that passes can be stored;
- the text holds no U+0000 and no lone surrogate (Postgres refuses both, and the job would
  fail again on every try), and exactly as many form feeds as its page counts say;
- every image is base64 that decodes to bytes starting with the JPEG magic, whose frame
  header declares the very width and height the answer states (`_jpeg_size`);
- a reason and a note are codes from the closed lists below, with integers beside them: the
  parser never sends a sentence, the application writes them.

Anything else is a ParserMalformedError, and nothing is kept of the answer.

What the caller gets is a Parsed, or one of five errors, each saying whose failure it is
(`failure`: "file" or "server", the two values of `ingestion_documents.failed_kind`):

    ParserDownError       nobody answers: connection refused, name unknown, closed before a
                          word. The parser is stopped, restarting, or was restarted mid job.
    ParserBusyError       it answers `busy`: its queue is full, or the wait for the job before
                          this one passed 75 s.
    ParserTimeoutError    too slow: this client's own 75 s, or the parser's wall clock or CPU
                          limit on the child (`code` says which).
    ParserRefusedError    an answer that says no. `failure == "file"`: the document itself
                          cannot be read (protected, unreadable, a bomb), trying again is
                          pointless. `failure == "server"`: the parser could not do it (the
                          child died, the kind is not supported yet, the memory limit).
    ParserMalformedError  the answer broke a rule above (`code` names the rule).

All but a file refusal are failures of the server: the job is tried once more, later.

Nothing of a document or of an answer is ever logged: one line per call, with the kind, the
size sent, the outcome as a code and the duration.

    python -m app.ingestion.parser_client ping [--bytes N]     from the worker's container:
        a round trip, the sandbox as its child reports it, one line of JSON, exit 0 when the
        sandbox is in force and the bytes came back counted and hashed right
"""

import argparse
import asyncio
import base64
import binascii
import contextlib
import functools
import hashlib
import json
import logging
import re
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NoReturn, TypeVar

_Checked = TypeVar("_Checked")

logger = logging.getLogger("app.ingestion.parser_client")

HOST = "parser"
PORT = 7070
KINDS = (
    "pdf_text", "docx", "linkedin_zip", "image", "pdf_render", "mail_html", "design_pdf",
    "design_docx", "ping",
)  # fmt: skip

MIB = 1024 * 1024
MAX_HEADER = 4096
MAX_SIZE = 20 * MIB  # one upload; the parser refuses more, and this client never sends it
MAX_ANSWER = 4 * MIB
TIMEOUT_S = 75.0  # a whole call: the parser kills a child at 60 s
CONNECT_TIMEOUT_S = 5.0
CLOSE_TIMEOUT_S = 2.0  # how long the end of the stream may take to follow the body
PING_TIMEOUT_S = 15.0

# Caps on a result. None is looser than the column that stores it (migration 0011).
MAX_TEXT_CHARS = 500_000  # ingestion_documents.text
MAX_READ_PAGES = 200  # pages of one PDF that are read at all, so page slots in the text
MAX_PAGES = 100_000  # pages a file may claim to have
MAX_IMAGES = 8  # one answer: rendering runs in batches of 8 pages
MAX_IMAGE_BYTES = 300_000  # ingestion_images.jpeg
MAX_IMAGE_SIDE = 2000  # ingestion_images.width and height
MAX_NOTES = 16
JPEG_MAGIC = b"\xff\xd8\xff"
# The HTML of a mail (kind `mail_html`, mail M3): the caps of sandbox/mail_html.py. Of an answer
# of that kind the worker reads at most 1.5 MB, not the 4 MiB of a document.
MAIL_HTML_MAX_ANSWER = 1_500_000
MAX_MAIL_HTML_BYTES = 500_000  # mail_messages.body_html_sanitized
MAX_MAIL_TEXT_CHARS = 500_000  # mail_messages.body_text
MAX_MAIL_IMAGES = 100_000
MAIL_LINK_REL = "noopener noreferrer nofollow"
PAGE_BREAK = "\f"

# Why the server answered without a result (sandbox/server.py, CODES), and what a child may say
# of itself (sandbox/main.py). Closed lists: a code that is not here is a malformed answer.
SERVER_CODES = frozenset(
    {
        "bad_request",
        "header_too_long",
        "unknown_kind",
        "too_large",
        "read_timeout",
        "busy",
        "wall_limit",
        "cpu_limit",
        "output_cap",
        "sandbox",
        "no_child",
        "crashed",
    }
)
CHILD_SERVER_CODES = frozenset(
    {"not_supported", "handler_error", "memory_limit", "output_cap", "too_large"}
)
# Why a file cannot be read. The tasks that add a parser add their codes here, and the sentence
# each one becomes for the user where they store the result.
# G2b (sandbox/pdf_text.py, sentences in app/ingestion/jobs.py): `encryption` is a PDF protected
# with AES, which the parser cannot open without `cryptography`; `too_slow` one whose first page
# was not read when the time budget ended.
# G7 (sandbox/zipcaps.py, docx.py and linkedin_zip.py): `not_docx` is a ZIP that is not a Word
# document (the worker then asks for the export), `not_export` one that is not an export either,
# `macros` a document carrying macros, `nested_archive` an archive inside the archive,
# `not_utf8` an export whose CSV is not UTF-8 text.
FILE_CODES = frozenset(
    {
        "unreadable",
        "protected",
        "encryption",
        "no_text",
        "bomb",
        "too_slow",
        "too_many_pixels",
        "not_docx",
        "not_export",
        "macros",
        "nested_archive",
        "not_utf8",
    }
)
# The files of a LinkedIn export that become documents (sandbox/linkedin_zip.py, FILES: a test
# keeps the two lists in step). A closed list: the name of a stored document never comes from
# the archive.
LINKEDIN_FILES = (
    "Profile.csv",
    "Profile Summary.csv",
    "Positions.csv",
    "Education.csv",
    "Skills.csv",
    "Languages.csv",
    "Certifications.csv",
    "Courses.csv",
    "Projects.csv",
    "Honors.csv",
    "Publications.csv",
    "Patents.csv",
    "Organizations.csv",
    "Volunteering.csv",
    "Test Scores.csv",
)
MAX_EXPORT_CHARS = 900_000  # every text of one export together (linkedin_zip.EXPORT_MAX_CHARS)
# What was not read, on a document that was. Each comes with integers named in NOTE_VALUES.
# `pages_not_read` is the page cap, `text_limit` the character cap (G2b), `time_limit` the time
# budget: each names the pages left out (from, to); `scans_not_read` counts the image pages.
# G8 (sandbox/pdf_render.py): `pages_not_rendered` counts the pages of a batch that could not be
# drawn (a size outside the guards, or a page PDFium cannot load).
NOTE_CODES = frozenset(
    {"pages_not_read", "text_limit", "time_limit", "scans_not_read", "pages_not_rendered"}
)
NOTE_VALUES = frozenset({"from", "to", "count"})
_TIMEOUT_CODES = frozenset({"wall_limit", "cpu_limit", "read_timeout"})
# The kinds whose child hands a user's file to a C library that nothing stops from inside
# (libjpeg and libpng behind Pillow, PDFium), and what the death of that child means for them.
# PDFium ends the process when memory is refused to it, and a decoder that spins is ended by
# the CPU limit: the child died of the file it was reading, and reading it again would kill it
# again. So for these kinds the three deaths below are the file's failure, with the file's
# code: never a failure of the server, and so never a second try (review of PR #157, B2 and
# B3). The server's own log line still says `crashed`, `cpu_limit` or `wall_limit`, and so
# does the line this client logs. Every other kind, and every other code, is as before.
# design_pdf (documents D25a) hands the file to PDFium as pdf_render does.
FILE_DEATH_KINDS = frozenset({"image", "pdf_render", "design_pdf"})
FILE_DEATHS = MappingProxyType(
    {"crashed": "bomb", "cpu_limit": "too_slow", "wall_limit": "too_slow"}
)

# What a ping must show for the sandbox to be in force: the limits of sandbox/main.py and the
# names of its deny list (tests/ingestion/parser keeps the three files in step).
EXPECTED_LIMITS = MappingProxyType(
    {
        "as": 512 * MIB,
        "cpu": 45,
        "fsize": 0,
        "nofile": 16,
        "nproc": 0,
        "core": 0,
        "msgqueue": 0,
    }
)
DENIED_NAMES = (
    "socket",
    "socketpair",
    "bind",
    "connect",
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "kill",
    "tkill",
    "tgkill",
    "rt_sigqueueinfo",
    "rt_tgsigqueueinfo",
    "pidfd_open",
    "pidfd_send_signal",
    "execve",
    "execveat",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "shmget",
    "msgget",
    "semget",
    "mq_open",
    "unshare",
    "setns",
)

_SURROGATE = re.compile("[\ud800-\udfff]")
_ERRNO = re.compile(r"E[A-Z0-9?]{1,15}|ALLOWED")
_KERNEL = re.compile(r"[0-9A-Za-z.+_~-]{1,64}")
_PLACE = re.compile(r"/[0-9A-Za-z/._-]{0,80}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


# --- errors -------------------------------------------------------------------------------------


class ParserError(Exception):
    """A call to the parser that gave no result. `failure` is whose it is ("file" or "server"),
    `code` a word from a closed list, `values` the integers that came with it. Never a sentence
    of the parser's, never a byte of a document: safe to log and to store as it is."""

    failure = "server"

    def __init__(self, code: str, values: Mapping[str, int] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.values: Mapping[str, int] = MappingProxyType(dict(values or {}))

    def __str__(self) -> str:
        return f"{self.failure}/{self.code}"


class ParserDownError(ParserError):
    """Nobody answers: the parser is stopped, unreachable, or went away before a word."""


class ParserBusyError(ParserError):
    """The parser has no room for this job now."""


class ParserTimeoutError(ParserError):
    """Too slow: this client's deadline, or the parser's limit on the child."""


class ParserRefusedError(ParserError):
    """The parser answered, and the answer is no. `failure` says whether the file is at fault
    (do not try again) or the parser (try once more)."""

    def __init__(
        self, code: str, *, failure: str = "server", values: Mapping[str, int] | None = None
    ) -> None:
        super().__init__(code, values)
        self.failure = failure


def blame(kind: str, error: ParserError) -> ParserError:
    """`error`, or the refusal of the file it stands for: the death of a child of
    FILE_DEATH_KINDS, as the server's header reports it (FILE_DEATHS)."""
    if (
        kind in FILE_DEATH_KINDS
        and type(error) in (ParserRefusedError, ParserTimeoutError)
        and error.failure == "server"
        and error.code in FILE_DEATHS
    ):
        return ParserRefusedError(FILE_DEATHS[error.code], failure="file")
    return error


class ParserMalformedError(ParserError):
    """The answer broke the protocol or a rule of this module. `code` names the rule."""


def _bad(rule: str) -> NoReturn:
    raise ParserMalformedError(rule)


# --- results ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    """What was not read: a code of NOTE_CODES and its integers ("pages_not_read", from 31,
    to 44). The application writes the sentence."""

    code: str
    values: Mapping[str, int]


@dataclass(frozen=True)
class PageImage:
    """One normalised JPEG: an uploaded image (position 0) or a rendered page of a PDF (its
    zero based index). The bytes start with the JPEG magic; nothing here decoded them."""

    position: int
    width: int
    height: int
    jpeg: bytes


@dataclass(frozen=True)
class ExportFile:
    """One CSV of a LinkedIn export: a name of LINKEDIN_FILES, its text, and the SHA-256 and
    the size of the file as the archive held it."""

    name: str
    text: str
    sha256: bytes
    size: int
    truncated: bool


@dataclass(frozen=True)
class Parsed:
    """A result that passed every check: it fits the columns it is stored in."""

    text: str | None = None
    pages: int | None = None
    pages_text: int | None = None
    pages_image: int | None = None
    truncated: bool = False
    notes: tuple[Note, ...] = ()
    images: tuple[PageImage, ...] = ()
    # The files of a LinkedIn export, each a document of its own (kind `linkedin_zip` only).
    documents: tuple[ExportFile, ...] = ()
    # What the child said its run cost (CPU milliseconds, highest resident memory and highest
    # address space in kB), None when it said nothing. For the log and the measurements only.
    usage: Mapping[str, int] | None = None


@dataclass(frozen=True)
class PingReport:
    """The answer of a ping: the bytes the child received, counted and hashed, what its run
    cost, and the lockdown as the child measured it in itself. `problems` is empty when the
    sandbox is in force."""

    received: int
    sha256: str
    cpu_ms: int
    max_rss_kb: int
    sandbox: Mapping[str, Any]
    problems: tuple[str, ...]

    @property
    def in_force(self) -> bool:
        return not self.problems


# --- reading an answer: JSON, strictly ----------------------------------------------------------


class _RuleBrokenError(ValueError):
    def __init__(self, rule: str) -> None:
        super().__init__(rule)
        self.rule = rule


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found = dict(pairs)
    if len(found) != len(pairs):
        raise _RuleBrokenError("duplicate_key")
    return found


def _no_float(_: str) -> NoReturn:
    raise _RuleBrokenError("float")


def _no_constant(_: str) -> NoReturn:
    raise _RuleBrokenError("constant")


def loads(raw: bytes) -> Any:
    """`raw` as JSON, or ParserMalformedError naming what is wrong with it. Decoding 4 MiB that
    were written to cost the most (a million empty arrays) takes about 100 MB for the time of
    the call; the tests hold that figure."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _bad("not_utf8")
    try:
        return json.loads(
            text, object_pairs_hook=_pairs, parse_float=_no_float, parse_constant=_no_constant
        )
    except _RuleBrokenError as broken:
        _bad(broken.rule)
    except RecursionError:
        _bad("too_deep")
    except ValueError:  # JSONDecodeError, and an integer of more digits than Python converts
        _bad("not_json")


def _integer(value: Any, low: int, high: int, rule: str) -> int:
    """`value` when it is an integer of the range, bool excluded (True is an int in Python)."""
    if type(value) is not int or not low <= value <= high:
        _bad(rule)
    return value


def _keys(found: Mapping[str, Any], allowed: frozenset[str] | set[str], rule: str) -> None:
    if not set(found) <= allowed:
        _bad(rule)


def _values(found: Mapping[str, Any]) -> dict[str, int]:
    """The integers beside a code: names of NOTE_VALUES only, each a page number or a count."""
    _keys(found, NOTE_VALUES, "values")
    values = {name: _integer(value, 0, MAX_PAGES, "values") for name, value in found.items()}
    if "from" in values and "to" in values and values["from"] > values["to"]:
        _bad("values")
    return values


# --- reading an answer: the header --------------------------------------------------------------

_HEADER_KEYS = frozenset({"ok", "failure", "code", "length"})


def check_header(line: bytes, max_answer: int = MAX_ANSWER) -> int:
    """The length of the body a header line announces, at most `max_answer`. Raises the error
    a refusal stands for, or ParserMalformedError when the line is not a header."""
    header = loads(line)
    if not isinstance(header, dict) or set(header) != _HEADER_KEYS:
        _bad("header")
    ok, failure, code = header["ok"], header["failure"], header["code"]
    length = _integer(header["length"], 0, max_answer, "length")
    if ok is True:
        if failure is not None or code != "ok":
            _bad("header")
        return length
    if ok is not False or failure != "server" or length != 0:
        _bad("header")
    if not isinstance(code, str) or code not in SERVER_CODES:
        _bad("header")
    if code == "busy":
        raise ParserBusyError(code)
    if code in _TIMEOUT_CODES:
        raise ParserTimeoutError(code)
    raise ParserRefusedError(code)


# --- reading an answer: a result ----------------------------------------------------------------

_FAILURE_KEYS = frozenset({"ok", "failure", "code"}) | NOTE_VALUES
_RESULT_KEYS = frozenset(
    {
        "ok",
        "kind",
        "text",
        "pages",
        "pages_text",
        "pages_image",
        "truncated",
        "notes",
        "images",
        "documents",
        "usage",
    }
)
_EXPORT_KEYS = frozenset({"name", "text", "sha256", "size", "truncated"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_USAGE_KEYS = frozenset({"cpu_ms", "max_rss_kb", "max_vm_kb"})
_IMAGE_KEYS = frozenset({"position", "width", "height", "jpeg"})
# Base64 of MAX_IMAGE_BYTES: checked on the text before anything is decoded.
_MAX_IMAGE_BASE64 = (MAX_IMAGE_BYTES + 2) // 3 * 4


def _refusal(answer: dict[str, Any]) -> NoReturn:
    """A child's `ok: false`, as the error it stands for."""
    _keys(answer, _FAILURE_KEYS, "keys")
    failure, code = answer.get("failure"), answer.get("code")
    if not isinstance(failure, str) or not isinstance(code, str):
        _bad("failure")
    allowed = {"file": FILE_CODES, "server": CHILD_SERVER_CODES}.get(failure)
    if allowed is None or code not in allowed:
        _bad("failure")
    values = _values({name: answer[name] for name in NOTE_VALUES if name in answer})
    raise ParserRefusedError(code, failure=failure, values=values)


def _text(value: Any) -> str:
    if not isinstance(value, str):
        _bad("text")
    if len(value) > MAX_TEXT_CHARS:
        _bad("text_too_long")
    if "\x00" in value:
        _bad("nul")
    if _SURROGATE.search(value):
        _bad("surrogate")
    return value


def _notes(value: Any) -> tuple[Note, ...]:
    if not isinstance(value, list) or len(value) > MAX_NOTES:
        _bad("notes")
    notes = []
    for item in value:
        if not isinstance(item, dict):
            _bad("notes")
        code = item.get("code")
        # A sentence where a code belongs is refused like any unknown code: the parser never
        # writes for the user.
        if not isinstance(code, str) or code not in NOTE_CODES:
            _bad("note_code")
        rest = {name: number for name, number in item.items() if name != "code"}
        notes.append(Note(code, MappingProxyType(_values(rest))))
    return tuple(notes)


_JPEG_SEGMENTS = 64  # segments looked at before the frame header: an encoder writes a few
# The start of frame markers (SOF0 to SOF15), without the three codes of that range that are
# tables and not frames (DHT, JPG, DAC).
_JPEG_FRAMES = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _jpeg_size(jpeg: bytes) -> tuple[int, int]:
    """The width and the height the frame header of a JPEG declares. Nothing is decoded: the
    segments are walked by their lengths, a bounded number of them, and a file without a frame
    header among them is not a JPEG this application stores."""
    at = 2
    for _ in range(_JPEG_SEGMENTS):
        if at + 4 > len(jpeg) or jpeg[at] != 0xFF:
            break
        marker = jpeg[at + 1]
        if marker == 0xFF:  # a fill byte
            at += 1
            continue
        if marker in (0xD9, 0xDA):  # the end, or the pixels: no frame header came before
            break
        length = int.from_bytes(jpeg[at + 2 : at + 4], "big")
        if length < 2:
            break
        if marker in _JPEG_FRAMES:
            if length < 7 or at + 9 > len(jpeg):
                break
            height = int.from_bytes(jpeg[at + 5 : at + 7], "big")
            width = int.from_bytes(jpeg[at + 7 : at + 9], "big")
            return width, height
        at += 2 + length
    _bad("image_not_jpeg")


def _images(value: Any) -> tuple[PageImage, ...]:
    if not isinstance(value, list) or len(value) > MAX_IMAGES:
        _bad("images")
    images, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != _IMAGE_KEYS:
            _bad("images")
        position = _integer(item["position"], 0, MAX_READ_PAGES - 1, "image_position")
        if position in seen:
            _bad("image_position")
        seen.add(position)
        width = _integer(item["width"], 1, MAX_IMAGE_SIDE, "image_side")
        height = _integer(item["height"], 1, MAX_IMAGE_SIDE, "image_side")
        encoded = item["jpeg"]
        if not isinstance(encoded, str) or not 0 < len(encoded) <= _MAX_IMAGE_BASE64:
            _bad("image_size")
        try:
            jpeg = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            _bad("image_base64")
        if not 0 < len(jpeg) <= MAX_IMAGE_BYTES:
            _bad("image_size")
        if not jpeg.startswith(JPEG_MAGIC):
            _bad("image_not_jpeg")
        if _jpeg_size(jpeg) != (width, height):
            _bad("image_side")  # the sides the answer states are not the picture's own
        images.append(PageImage(position, width, height, jpeg))
    return tuple(images)


def _documents(value: Any) -> tuple[ExportFile, ...]:
    """The files of an export: each name of LINKEDIN_FILES at most once, each hash once, a
    text that fits its column and holds no form feed (a CSV has no pages)."""
    if not isinstance(value, list) or not 1 <= len(value) <= len(LINKEDIN_FILES):
        _bad("documents")
    files: list[ExportFile] = []
    for item in value:
        if not isinstance(item, dict):
            _bad("documents")
        _keys(item, _EXPORT_KEYS, "document_keys")
        name, digest, truncated = item.get("name"), item.get("sha256"), item.get("truncated")
        if not isinstance(name, str) or name not in LINKEDIN_FILES:
            _bad("document_name")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _bad("document_hash")
        if type(truncated) is not bool:
            _bad("truncated")
        text = _text(item.get("text"))
        if not text or PAGE_BREAK in text:
            _bad("document_text")
        size = _integer(item.get("size"), 1, MAX_SIZE, "document_size")
        files.append(ExportFile(name, text, bytes.fromhex(digest), size, truncated))
    if len({file.name for file in files}) != len(files):
        _bad("document_name")
    if len({file.sha256 for file in files}) != len(files):
        _bad("document_hash")
    if sum(len(file.text) for file in files) > MAX_EXPORT_CHARS:
        _bad("text_too_long")
    return tuple(files)


def _usage(value: Any) -> Mapping[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _USAGE_KEYS:
        _bad("usage")
    return MappingProxyType({name: _integer(value[name], 0, 10**10, "usage") for name in value})


def check_parsed(answer: Any, kind: str) -> Parsed:
    """The result an answer holds, or the error it stands for. Nothing of `answer` is trusted:
    this is the one door through which a parser's words enter the application."""
    if not isinstance(answer, dict) or type(answer.get("ok")) is not bool:
        _bad("ok")
    if not answer["ok"]:
        _refusal(answer)
    _keys(answer, _RESULT_KEYS, "keys")
    if answer.get("kind", kind) != kind:
        _bad("kind")
    text = _text(answer["text"]) if answer.get("text") is not None else None
    # An export is its files and nothing else; no other kind may answer with files.
    if kind == "linkedin_zip":
        if (_RESULT_KEYS - {"ok", "kind", "documents", "usage"}) & answer.keys():
            _bad("keys")
        documents = _documents(answer.get("documents"))
    elif "documents" in answer:
        _bad("keys")
    else:
        documents = ()
    truncated = answer.get("truncated", False)
    if type(truncated) is not bool:
        _bad("truncated")

    counts = [answer.get(name) for name in ("pages", "pages_text", "pages_image")]
    if all(count is None for count in counts):
        pages = pages_text = pages_image = None
        # No page count: the form feed is the page separator and nothing else.
        if text is not None and PAGE_BREAK in text:
            _bad("form_feeds")
    else:
        # All three or none: a count alone says nothing that can be checked.
        pages = _integer(counts[0], 0, MAX_PAGES, "pages")
        pages_text = _integer(counts[1], 0, MAX_READ_PAGES, "pages")
        pages_image = _integer(counts[2], 0, MAX_READ_PAGES, "pages")
        slots = pages_text + pages_image
        if slots > MAX_READ_PAGES or slots > pages:
            _bad("pages")
        # One slot per page read, joined by form feeds: a page too many or too few and every
        # citation offset and every page number shown would be wrong.
        if text is not None and text.count(PAGE_BREAK) != max(slots, 1) - 1:
            _bad("form_feeds")

    return Parsed(
        text=text,
        pages=pages,
        pages_text=pages_text,
        pages_image=pages_image,
        truncated=truncated,
        notes=_notes(answer.get("notes", [])),
        images=_images(answer.get("images", [])),
        documents=documents,
        usage=_usage(answer.get("usage")),
    )


# --- reading an answer: the HTML of a mail (mail M3) --------------------------------------------

_MAIL_KEYS = frozenset({"ok", "kind", "html", "text", "remote_images", "truncated", "usage"})
# The allow list of sandbox/mail_html.py, stated a second time on purpose: the child that
# sanitised the HTML may be the process a mail took over, so what it calls sanitised is read
# here again, by a few regular expressions and no parser. tests/mail keeps the two in step.
MAIL_TAGS = frozenset(
    [
        "p",
        "br",
        "div",
        "span",
        "a",
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ul",
        "ol",
        "li",
        "blockquote",
        "pre",
        "code",
        "table",
        "thead",
        "tbody",
        "tr",
        "td",
        "th",
        "h1",
        "h2",
        "h3",
        "h4",
        "hr",
        "img",
    ]
)
_MAIL_VOID = frozenset({"br", "hr", "img"})
# One piece of sanitised HTML as the sanitiser serialises it: text without `<`, or a tag in
# lower case whose attributes are each ` name="value"` with no quote and no angle bracket in
# the value (the serialiser writes those three as character references).
_MAIL_PIECE = re.compile(r'[^<]+|<(/?)([a-z][a-z0-9]*)((?: [a-z-]+="[^"<>]*")*)>')
_MAIL_ATTRIBUTE = re.compile(r' ([a-z-]+)="([^"<>]*)"')
_MAIL_LINK = re.compile(r"(?:https?://|mailto:)\S+", re.IGNORECASE)
_MAIL_REMOTE = re.compile(r"https?://\S+", re.IGNORECASE)
_MAIL_NUMBER = re.compile(r"[0-9]{1,4}")
_MAIL_VALUES = MappingProxyType(
    {
        ("a", "href"): _MAIL_LINK.fullmatch,
        ("a", "rel"): lambda value: value == MAIL_LINK_REL,
        ("img", "data-src"): _MAIL_REMOTE.fullmatch,
        ("img", "width"): _MAIL_NUMBER.fullmatch,
        ("img", "height"): _MAIL_NUMBER.fullmatch,
        ("img", "alt"): lambda value: True,
    }
)


@dataclass(frozen=True)
class MailHtml:
    """The HTML of a mail that passed every check: it can be stored and shown."""

    html: str  # the allow list and nothing else; no `src`: remote images wait in `data-src`
    text: str  # the plain text version, links written "label (address)"
    remote_images: int
    truncated: bool
    usage: Mapping[str, int] | None = None


def _mail_html(value: Any) -> str:
    """`value` when it is HTML of the allow list, piece by piece. One pass, no backtracking
    over more than one piece: a megabyte is read in a few milliseconds."""
    if not isinstance(value, str) or len(value) > MAX_MAIL_HTML_BYTES:
        _bad("html_too_long")
    if "\x00" in value:
        _bad("nul")
    if _SURROGATE.search(value):
        _bad("surrogate")
    if len(value.encode("utf-8")) > MAX_MAIL_HTML_BYTES:
        _bad("html_too_long")
    position = 0
    while position < len(value):
        piece = _MAIL_PIECE.match(value, position)
        if piece is None:
            _bad("html")
        position = piece.end()
        closing, tag, attributes = piece[1], piece[2], piece[3]
        if tag is None:
            continue
        if tag not in MAIL_TAGS:
            _bad("html_tag")
        if closing:
            if attributes or tag in _MAIL_VOID:
                _bad("html")
            continue
        seen = set()
        for name, content in _MAIL_ATTRIBUTE.findall(attributes):
            allowed = _MAIL_VALUES.get((tag, name))
            if allowed is None or name in seen or not allowed(content):
                _bad("html_attribute")
            seen.add(name)
    return value


def check_mail_html(answer: Any) -> MailHtml:
    """The sanitised HTML an answer of the kind `mail_html` holds, or the error it stands for.
    As for check_parsed, nothing of `answer` is trusted, the HTML least of all."""
    if not isinstance(answer, dict) or type(answer.get("ok")) is not bool:
        _bad("ok")
    if not answer["ok"]:
        _refusal(answer)
    if not _MAIL_KEYS - {"usage"} <= set(answer) <= _MAIL_KEYS:
        _bad("keys")
    if answer["kind"] != "mail_html":
        _bad("kind")
    text = answer["text"]
    if not isinstance(text, str) or len(text) > MAX_MAIL_TEXT_CHARS:
        _bad("text_too_long")
    if "\x00" in text:
        _bad("nul")
    if _SURROGATE.search(text):
        _bad("surrogate")
    if type(answer["truncated"]) is not bool:
        _bad("truncated")
    return MailHtml(
        html=_mail_html(answer["html"]),
        text=text,
        remote_images=_integer(answer["remote_images"], 0, MAX_MAIL_IMAGES, "remote_images"),
        truncated=answer["truncated"],
        usage=_usage(answer.get("usage")),
    )


# --- reading an answer: the layout of an uploaded CV (documents D25a) ---------------------------

# The caps of sandbox/design_pdf.py and design_docx.py, stated again (tests/ingestion/parser
# keeps them in step): the child that read the file may be the process the file took over.
# Together they hold an answer under DESIGN_MAX_ANSWER, the most a design call reads.
DESIGN_KINDS = ("design_pdf", "design_docx")
DESIGN_MAX_ANSWER = 1_000_000
DESIGN_PAGES = 2
DESIGN_RUNS = 1_500
DESIGN_SHAPES = 400
DESIGN_IMAGES = 50
DESIGN_TEXT = 40_000
DESIGN_RUN_TEXT = 300
DESIGN_FONTS = 64
DESIGN_FONT_NAME = 128
DESIGN_STYLES = 400
DESIGN_SECTIONS = 20
DESIGN_SHOWN = 40
DESIGN_NAME = 64
DESIGN_LENGTH = 144_000  # tenths of a point: the 200 inches of pdf_render.PAGE_MAX_INCHES
DESIGN_STROKE = 1_000  # tenths of a point: the widest stroke of a shape
DESIGN_COUNT = 10_000_000
DESIGN_NOTE_CODES = frozenset(
    {
        "time_limit", "text_limit", "runs_limit", "shapes_limit", "images_limit",
        "objects_limit", "chars_limit", "fonts_limit", "pages_not_read", "styles_limit",
        "sections_limit", "formats_limit",
    }
)  # fmt: skip
DESIGN_PROPS = (
    "font", "size", "bold", "italic", "caps", "colour", "before", "after", "line", "line_rule",
    "align", "border", "shading",
)  # fmt: skip
DESIGN_RUN_PROPS = ("font", "size", "bold", "italic", "caps", "colour")
_COLOUR = re.compile(r"#[0-9a-f]{6}")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\ud800-\udfff]")
_SCHEME = frozenset(
    (
        "dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5",
        "accent6", "hlink", "folHlink",
    )
)  # fmt: skip
_CHOICES = MappingProxyType(
    {
        "line_rule": frozenset({"auto", "exact", "at_least"}),
        "align": frozenset({"left", "center", "right", "justify"}),
    }
)


@dataclass(frozen=True)
class DesignFont:
    """A font of the runs, as the PDF declares it: `name` as embedded (a subset prefix
    included), `weight` 0 when the file does not say."""

    name: str
    weight: int
    italic: bool
    serif: bool
    monospace: bool
    symbolic: bool
    embedded: bool


@dataclass(frozen=True)
class TextRun:
    """Characters of one font, size and colour on one line. Lengths in tenths of a point,
    boxes [x0, y0, x1, y1] from the top left corner of the page."""

    text: str
    box: tuple[int, int, int, int]
    font: int  # an index of DesignPdf.fonts
    size: int
    colour: str


@dataclass(frozen=True)
class Shape:
    type: str  # fill, rule or outline
    box: tuple[int, int, int, int]
    colour: str
    width: int


@dataclass(frozen=True)
class DesignPage:
    index: int
    width: int
    height: int
    margins: tuple[int, int, int, int] | None  # top, right, bottom, left of the text
    runs: tuple[TextRun, ...]  # in reading order
    shapes: tuple[Shape, ...]
    images: tuple[tuple[int, int, int, int], ...]  # boxes only: no pixel is ever read


@dataclass(frozen=True)
class DesignPdf:
    """The layout of the first pages of a PDF (kind `design_pdf`)."""

    pages: int
    scanned: bool
    truncated: bool
    notes: tuple[Note, ...]
    fonts: tuple[DesignFont, ...]
    read: tuple[DesignPage, ...]
    usage: Mapping[str, int] | None = None


@dataclass(frozen=True)
class DesignDocx:
    """The styles of a Word document (kind `design_docx`). Each style, format and the defaults
    are read only mappings of the keys sandbox/design_docx.py names."""

    truncated: bool
    notes: tuple[Note, ...]
    theme: Mapping[str, Any] | None
    defaults: Mapping[str, Any]
    styles: tuple[Mapping[str, Any], ...]
    sections: tuple[Mapping[str, Any], ...]
    tables: int
    table_columns: int
    drawings: int
    paragraphs: int
    paragraph_styles: tuple[tuple[str, int], ...]
    formats: tuple[Mapping[str, Any], ...]
    usage: Mapping[str, int] | None = None


def _flag(value: Any, rule: str) -> bool:
    if type(value) is not bool:
        _bad(rule)
    return value


def _plain(value: Any, most: int, rule: str, *, empty: bool = False) -> str:
    """A string of at most `most` characters, without a control character or a surrogate."""
    if not isinstance(value, str) or len(value) > most or (not value and not empty):
        _bad(rule)
    if _CONTROL.search(value):
        _bad(rule)
    return value


def _colour(value: Any, rule: str = "colour") -> str:
    if not isinstance(value, str) or not _COLOUR.fullmatch(value):
        _bad(rule)
    return value


def _optional(value: Any, check: Callable[[Any], Any]) -> Any:
    return None if value is None else check(value)


def _exact(found: Any, keys: frozenset[str] | set[str], rule: str) -> dict[str, Any]:
    if not isinstance(found, dict) or set(found) != keys:
        _bad(rule)
    return found


def _list(value: Any, most: int, rule: str) -> list:
    if not isinstance(value, list) or len(value) > most:
        _bad(rule)
    return value


def _box(value: Any, width: int, height: int) -> tuple[int, int, int, int]:
    if not isinstance(value, list) or len(value) != 4:
        _bad("box")
    x0, y0, x1, y1 = (_integer(part, 0, DESIGN_LENGTH, "box") for part in value)
    if not (x0 <= x1 <= width and y0 <= y1 <= height):
        _bad("box")
    return x0, y0, x1, y1


def _design_notes(value: Any) -> tuple[Note, ...]:
    notes = []
    for item in _list(value, MAX_NOTES, "notes"):
        if not isinstance(item, dict) or not {"code"} <= set(item) <= {"code", "count"}:
            _bad("notes")
        if not isinstance(item["code"], str) or item["code"] not in DESIGN_NOTE_CODES:
            _bad("note_code")
        values = {}
        if "count" in item:
            values["count"] = _integer(item["count"], 1, DESIGN_PAGES, "values")
        notes.append(Note(item["code"], MappingProxyType(values)))
    return tuple(notes)


_FONT_KEYS = frozenset({"name", "weight", "italic", "serif", "monospace", "symbolic", "embedded"})
_PAGE_KEYS = frozenset({"index", "width", "height", "margins", "runs", "shapes", "images"})
_RUN_KEYS = frozenset({"text", "box", "font", "size", "colour"})
_SHAPE_KEYS = frozenset({"type", "box", "colour", "width"})
_DESIGN_PDF_KEYS = frozenset(
    {"ok", "kind", "pages", "scanned", "truncated", "notes", "fonts", "read", "usage"}
)


def _font(item: Any) -> DesignFont:
    item = _exact(item, _FONT_KEYS, "font")
    weight = _integer(item["weight"], 0, 900, "font")
    if 0 < weight < 100:
        _bad("font")
    return DesignFont(
        _plain(item["name"], DESIGN_FONT_NAME, "font", empty=True),
        weight,
        *(_flag(item[key], "font") for key in ("italic", "serif", "monospace", "symbolic")),
        _flag(item["embedded"], "font"),
    )


def _page(item: Any, fonts: int, seen: set[int], total: int) -> DesignPage:
    item = _exact(item, _PAGE_KEYS, "page")
    index = _integer(item["index"], 0, min(total, DESIGN_PAGES) - 1, "page")
    if seen and index <= max(seen):
        _bad("page")
    seen.add(index)
    width = _integer(item["width"], 1, DESIGN_LENGTH, "page")
    height = _integer(item["height"], 1, DESIGN_LENGTH, "page")
    runs = []
    for run in _list(item["runs"], DESIGN_RUNS, "runs"):
        run = _exact(run, _RUN_KEYS, "runs")
        runs.append(
            TextRun(
                _plain(run["text"], DESIGN_RUN_TEXT, "run_text"),
                _box(run["box"], width, height),
                _integer(run["font"], 0, fonts - 1, "runs"),
                _integer(run["size"], 0, DESIGN_LENGTH, "runs"),
                _colour(run["colour"]),
            )
        )
    shapes = []
    for shape in _list(item["shapes"], DESIGN_SHAPES, "shapes"):
        shape = _exact(shape, _SHAPE_KEYS, "shapes")
        if shape["type"] not in ("fill", "rule", "outline"):
            _bad("shapes")
        box = _box(shape["box"], width, height)
        shapes.append(
            Shape(
                shape["type"],
                box,
                _colour(shape["colour"]),
                _integer(shape["width"], 0, DESIGN_STROKE, "shapes"),
            )
        )
    images = tuple(
        _box(_exact(image, {"box"}, "images")["box"], width, height)
        for image in _list(item["images"], DESIGN_IMAGES, "images")
    )
    margins = item["margins"]
    if margins is not None:
        if not isinstance(margins, list) or len(margins) != 4 or not runs:
            _bad("margins")
        margins = tuple(_integer(side, 0, max(width, height), "margins") for side in margins)
    elif runs:
        _bad("margins")
    return DesignPage(index, width, height, margins, tuple(runs), tuple(shapes), images)


def check_design_pdf(answer: Any) -> DesignPdf:
    """The layout an answer of the kind `design_pdf` holds, or the error it stands for. As for
    check_parsed, nothing of `answer` is trusted: every box inside its page, every font an
    index of the table, every text within its caps, every colour `#rrggbb`."""
    if not isinstance(answer, dict) or type(answer.get("ok")) is not bool:
        _bad("ok")
    if not answer["ok"]:
        _refusal(answer)
    if not _DESIGN_PDF_KEYS - {"usage"} <= set(answer) <= _DESIGN_PDF_KEYS:
        _bad("keys")
    if answer["kind"] != "design_pdf":
        _bad("kind")
    total = _integer(answer["pages"], 1, MAX_PAGES, "pages")
    fonts = tuple(_font(item) for item in _list(answer["fonts"], DESIGN_FONTS, "fonts"))
    seen: set[int] = set()
    pages = tuple(
        _page(item, len(fonts), seen, total)
        for item in _list(answer["read"], DESIGN_PAGES, "pages")
    )
    if sum(len(run.text) for page in pages for run in page.runs) > DESIGN_TEXT:
        _bad("text_too_long")
    scanned = _flag(answer["scanned"], "scanned")
    if scanned and any(page.runs for page in pages):
        _bad("scanned")
    return DesignPdf(
        pages=total,
        scanned=scanned,
        truncated=_flag(answer["truncated"], "truncated"),
        notes=_design_notes(answer["notes"]),
        fonts=fonts,
        read=pages,
        usage=_usage(answer.get("usage")),
    )


_STYLE_KEYS = frozenset({"id", "name", "type", "based_on", "default", "outline", *DESIGN_PROPS})
_SECTION_KEYS = frozenset({"width", "height", "margins", "columns"})
_FORMAT_KEYS = frozenset({"style", "chars", *DESIGN_RUN_PROPS})
_DESIGN_DOCX_KEYS = frozenset(
    {
        "ok", "kind", "truncated", "notes", "theme", "defaults", "styles", "sections", "tables",
        "table_columns", "drawings", "paragraphs", "paragraph_styles", "formats", "usage",
    }
)  # fmt: skip
_NAME = functools.partial(_plain, most=DESIGN_NAME, rule="name")


def _prop(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key == "font":
        return _NAME(value)
    if key in ("bold", "italic", "caps"):
        return _flag(value, "props")
    if key in ("colour", "border", "shading"):
        return _colour(value, "props")
    if key in _CHOICES:
        if value not in _CHOICES[key]:
            _bad("props")
        return value
    return _integer(value, 0, 100_000, "props")


def _props(found: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: _prop(key, found[key]) for key in keys}


def _style(item: Any) -> Mapping[str, Any]:
    item = _exact(item, _STYLE_KEYS, "styles")
    if item["type"] not in ("paragraph", "character", "table", "numbering"):
        _bad("styles")
    return MappingProxyType(
        {
            "id": _NAME(item["id"]),
            "name": _optional(item["name"], _NAME),
            "type": item["type"],
            "based_on": _optional(item["based_on"], _NAME),
            "default": _flag(item["default"], "styles"),
            "outline": _optional(item["outline"], lambda v: _integer(v, 0, 9, "styles")),
            **_props(item, DESIGN_PROPS),
        }
    )


def _section(item: Any) -> Mapping[str, Any]:
    item = _exact(item, _SECTION_KEYS, "sections")
    length = functools.partial(_integer, low=0, high=DESIGN_LENGTH, rule="sections")
    margins = item["margins"]
    if margins is not None:
        if not isinstance(margins, list) or len(margins) != 4:
            _bad("sections")
        margins = tuple(length(side) for side in margins)
    return MappingProxyType(
        {
            "width": _optional(item["width"], length),
            "height": _optional(item["height"], length),
            "margins": margins,
            "columns": _integer(item["columns"], 1, 45, "sections"),
        }
    )


def _theme(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    value = _exact(value, {"major", "minor", "colours"}, "theme")
    colours = value["colours"]
    if not isinstance(colours, dict) or not set(colours) <= _SCHEME:
        _bad("theme")
    return MappingProxyType(
        {
            "major": _optional(value["major"], _NAME),
            "minor": _optional(value["minor"], _NAME),
            "colours": MappingProxyType({k: _colour(v, "theme") for k, v in colours.items()}),
        }
    )


def check_design_docx(answer: Any) -> DesignDocx:
    """The styles an answer of the kind `design_docx` holds, or the error it stands for. As for
    check_parsed, nothing of `answer` is trusted."""
    if not isinstance(answer, dict) or type(answer.get("ok")) is not bool:
        _bad("ok")
    if not answer["ok"]:
        _refusal(answer)
    if not _DESIGN_DOCX_KEYS - {"usage"} <= set(answer) <= _DESIGN_DOCX_KEYS:
        _bad("keys")
    if answer["kind"] != "design_docx":
        _bad("kind")
    defaults = _exact(answer["defaults"], set(DESIGN_PROPS), "defaults")
    count = functools.partial(_integer, low=0, high=DESIGN_COUNT, rule="counts")
    used = []
    for item in _list(answer["paragraph_styles"], DESIGN_SHOWN, "paragraph_styles"):
        item = _exact(item, {"style", "count"}, "paragraph_styles")
        used.append((_plain(item["style"], DESIGN_NAME, "name", empty=True), count(item["count"])))
    formats = []
    for item in _list(answer["formats"], DESIGN_SHOWN, "formats"):
        item = _exact(item, _FORMAT_KEYS, "formats")
        formats.append(
            MappingProxyType(
                {
                    "style": _optional(item["style"], _NAME),
                    **_props(item, DESIGN_RUN_PROPS),
                    "chars": count(item["chars"]),
                }
            )
        )
    return DesignDocx(
        truncated=_flag(answer["truncated"], "truncated"),
        notes=_design_notes(answer["notes"]),
        theme=_theme(answer["theme"]),
        defaults=MappingProxyType(_props(defaults, DESIGN_PROPS)),
        styles=tuple(_style(item) for item in _list(answer["styles"], DESIGN_STYLES, "styles")),
        sections=tuple(
            _section(item) for item in _list(answer["sections"], DESIGN_SECTIONS, "sections")
        ),
        tables=count(answer["tables"]),
        table_columns=count(answer["table_columns"]),
        drawings=count(answer["drawings"]),
        paragraphs=count(answer["paragraphs"]),
        paragraph_styles=tuple(used),
        formats=tuple(formats),
        usage=_usage(answer.get("usage")),
    )


# --- reading an answer: a ping ------------------------------------------------------------------

_PING_KEYS = frozenset({"ok", "kind", "sandbox", "received", "sha256", "usage"})
_SANDBOX_KEYS = frozenset(
    {
        "seccomp",
        "filters",
        "no_new_privs",
        "dumpable",
        "capabilities",
        "limits",
        "denied",
        "isolated",
        "environ",
        "uid",
        "descriptors",
        "sockets",
        "writable",
        "parent_hidden",
        "cryptography_blocked",
        "machine",
        "kernel",
    }
)
_BIG = 2**63


def sandbox_problems(sandbox: Mapping[str, Any]) -> tuple[str, ...]:
    """Why a checked sandbox report does not show the lockdown in force (empty when it does).
    The same judgement as the healthcheck of the service (sandbox/server.py)."""
    problems = []
    if sandbox["seccomp"] != 2:
        problems.append("seccomp_mode")
    denied = sandbox["denied"]
    problems += [f"allowed:{name}" for name in DENIED_NAMES if denied.get(name) != "EPERM"]
    if sandbox["limits"] != dict(EXPECTED_LIMITS):
        problems.append("limits")
    for name, wanted in (
        ("no_new_privs", 1),
        ("dumpable", 0),
        ("capabilities", 0),
        ("isolated", True),
        ("environ", 0),
        ("sockets", 0),
        ("descriptors", [0, 1, 2]),
        ("writable", []),
        ("parent_hidden", True),
        ("cryptography_blocked", True),
    ):
        if sandbox[name] != wanted:
            problems.append(name)
    if sandbox["uid"] <= 0:
        problems.append("uid")
    return tuple(problems)


def _sandbox(value: Any) -> dict[str, Any]:
    """A sandbox report with every field of its type. Still only what a child said of itself:
    it proves the sandbox to a healthcheck, it is nothing to build a decision of trust on."""
    if not isinstance(value, dict) or set(value) != _SANDBOX_KEYS:
        _bad("sandbox")
    checked: dict[str, Any] = {}
    for name in ("seccomp", "no_new_privs", "dumpable", "environ", "sockets", "uid"):
        checked[name] = _integer(value[name], 0, 2**32, "sandbox")
    checked["filters"] = _integer(value["filters"], -1, 2**16, "sandbox")
    checked["capabilities"] = _integer(value["capabilities"], 0, _BIG, "sandbox")
    for name in ("isolated", "parent_hidden", "cryptography_blocked"):
        if type(value[name]) is not bool:
            _bad("sandbox")
        checked[name] = value[name]
    limits, denied = value["limits"], value["denied"]
    if not isinstance(limits, dict) or set(limits) != set(EXPECTED_LIMITS):
        _bad("sandbox")
    checked["limits"] = {name: _integer(limits[name], -1, _BIG, "sandbox") for name in limits}
    if not isinstance(denied, dict) or not set(denied) <= set(DENIED_NAMES):
        _bad("sandbox")
    for answer in denied.values():
        if not isinstance(answer, str) or not _ERRNO.fullmatch(answer):
            _bad("sandbox")
    checked["denied"] = dict(denied)
    descriptors, writable = value["descriptors"], value["writable"]
    if not isinstance(descriptors, list) or len(descriptors) > 256:
        _bad("sandbox")
    checked["descriptors"] = [_integer(number, 0, 2**20, "sandbox") for number in descriptors]
    if not isinstance(writable, list) or len(writable) > 16:
        _bad("sandbox")
    for place in writable:
        if not isinstance(place, str) or not _PLACE.fullmatch(place):
            _bad("sandbox")
    checked["writable"] = list(writable)
    if value["machine"] not in ("aarch64", "x86_64"):
        _bad("sandbox")
    checked["machine"] = value["machine"]
    if not isinstance(value["kernel"], str) or not _KERNEL.fullmatch(value["kernel"]):
        _bad("sandbox")
    checked["kernel"] = value["kernel"]
    return checked


def check_ping(answer: Any) -> PingReport:
    if not isinstance(answer, dict) or set(answer) != _PING_KEYS:
        _bad("ping")
    if answer["ok"] is not True or answer["kind"] != "ping":
        _bad("ping")
    usage = answer["usage"]
    if not isinstance(usage, dict) or set(usage) != {"cpu_ms", "max_rss_kb"}:
        _bad("ping")
    digest = answer["sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        _bad("ping")
    sandbox = _sandbox(answer["sandbox"])
    return PingReport(
        received=_integer(answer["received"], 0, MAX_SIZE, "ping"),
        sha256=digest,
        cpu_ms=_integer(usage["cpu_ms"], 0, 10**9, "ping"),
        max_rss_kb=_integer(usage["max_rss_kb"], 0, 10**9, "ping"),
        sandbox=MappingProxyType(sandbox),
        problems=sandbox_problems(sandbox),
    )


# --- the call -----------------------------------------------------------------------------------


def request_line(kind: str, size: int, options: Mapping[str, Any] | None) -> bytes:
    """The header line of a request. ValueError for what the caller got wrong: these never
    reach the network."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind!r}")
    header = {"kind": kind, "size": size, "options": dict(options or {})}
    line = json.dumps(header, separators=(",", ":")).encode("ascii") + b"\n"
    if len(line) > MAX_HEADER:
        raise ValueError("the options do not fit the header line")
    return line


async def _exchange(
    line: bytes, data: bytes, host: str, port: int, max_answer: int = MAX_ANSWER
) -> bytes:
    """Send one request and return the body of the answer, its length checked. The caller holds
    the deadline."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, limit=MAX_HEADER), CONNECT_TIMEOUT_S
        )
    except (OSError, TimeoutError):  # refused, no such name, no route, no answer
        raise ParserDownError("unreachable") from None
    try:
        try:
            writer.write(line + data)
            await writer.drain()
        except ConnectionError:
            # The parser may have refused the header and stopped reading: its answer comes
            # first. If there is none, reading says so.
            pass
        try:
            answer = await reader.readuntil(b"\n")
        except asyncio.IncompleteReadError as exc:
            if exc.partial:
                _bad("truncated")
            raise ParserDownError("closed") from None
        except asyncio.LimitOverrunError:
            _bad("header_too_long")
        except ConnectionError:
            raise ParserDownError("closed") from None
        if len(answer) > MAX_HEADER:
            _bad("header_too_long")
        length = check_header(answer, max_answer)
        try:
            body = await reader.readexactly(length)
        except (asyncio.IncompleteReadError, ConnectionError):
            _bad("truncated")
        # The body ends at its length, and the stream right after it: the parser closes the
        # connection once it has answered. A peer that sends more, or that keeps the
        # connection open, is not the parser this protocol was written with.
        try:
            extra = await asyncio.wait_for(reader.read(1), CLOSE_TIMEOUT_S)
        except ConnectionError:
            extra = b""
        except TimeoutError:
            _bad("not_closed")
        if extra:
            _bad("trailing_bytes")
        return body
    finally:
        writer.close()
        # The answer is read or lost by now: how the connection ends changes nothing.
        with contextlib.suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), 1.0)


async def _call[Checked](
    kind: str,
    data: bytes,
    options: Mapping[str, Any] | None,
    host: str,
    port: int,
    timeout_s: float,
    check: Callable[[Any], Checked],
    max_answer: int = MAX_ANSWER,
) -> Checked:
    """One request and its answer, decoded and judged by `check`. Logs one line, whatever the
    end: the kind, the size sent, the outcome as a code, the duration."""
    line = request_line(kind, len(data), options)
    started = time.monotonic()
    outcome = "ok"
    try:
        if len(data) > MAX_SIZE:
            raise ParserRefusedError("too_large")
        try:
            async with asyncio.timeout(timeout_s):
                body = await _exchange(line, data, host, port, max_answer)
        except TimeoutError:
            raise ParserTimeoutError("deadline") from None
        # Off the event loop: an answer written to be slow to read must not stall the worker.
        return await asyncio.to_thread(lambda: check(loads(body)))
    except ParserError as error:
        outcome = f"{type(error).__name__}:{error.code}"
        blamed = blame(kind, error)
        if blamed is error:
            raise
        outcome += f":file/{blamed.code}"  # the log keeps how the child ended
        raise blamed from None
    finally:
        logger.info(
            "parser call",
            extra={
                "kind": kind,
                "size": len(data),
                "outcome": outcome,
                "ms": int((time.monotonic() - started) * 1000),
            },
        )


async def parse(
    kind: str,
    data: bytes,
    options: Mapping[str, Any] | None = None,
    *,
    host: str = HOST,
    port: int = PORT,
    timeout_s: float = TIMEOUT_S,
) -> Parsed:
    """Have `data` parsed as `kind` and return the checked result. Raises a ParserError
    otherwise; `error.failure` says whether the file or the server failed."""
    if kind == "ping":
        raise ValueError("ping is not a parse: use ping()")
    if kind == "mail_html":
        raise ValueError("mail_html is not a document: use sanitise_mail_html()")
    if kind in DESIGN_KINDS:
        raise ValueError(f"{kind} is a layout, not a text: use read_design()")
    return await _call(
        kind, data, options, host, port, timeout_s, lambda answer: check_parsed(answer, kind)
    )


async def ping(
    data: bytes = b"",
    *,
    host: str = HOST,
    port: int = PORT,
    timeout_s: float = PING_TIMEOUT_S,
) -> PingReport:
    """A round trip through a sandboxed child. Without bytes it is answered at once, even
    while a job runs; with bytes it waits its turn like a job, and the child answers their
    size and SHA-256."""

    def check(answer: Any) -> PingReport:
        report = check_ping(answer)
        if report.received != len(data) or report.sha256 != hashlib.sha256(data).hexdigest():
            _bad("echo")
        return report

    return await _call("ping", data, None, host, port, timeout_s, check)


async def sanitise_mail_html(
    html: str,
    *,
    host: str = HOST,
    port: int = PORT,
    timeout_s: float = TIMEOUT_S,
) -> MailHtml:
    """Have the HTML part of one mail sanitised by the parser service and return the checked
    result (mail M3; M7 owns the call). The HTML is a stranger's: it is parsed there, in the
    process that holds no token, and never here. Raises a ParserError otherwise, whatever it
    is: the caller then stores the mail with its plain text part, or its snippet, and no HTML.
    A mail is never lost because its HTML is hostile."""
    return await _call(
        "mail_html",
        html.encode("utf-8", "replace"),
        None,
        host,
        port,
        timeout_s,
        check_mail_html,
        MAIL_HTML_MAX_ANSWER,
    )


async def read_design(
    kind: str,
    data: bytes,
    *,
    host: str = HOST,
    port: int = PORT,
    timeout_s: float = TIMEOUT_S,
) -> DesignPdf | DesignDocx:
    """The layout of an uploaded CV for the design capture (documents D25a): `design_pdf` for a
    PDF, `design_docx` for a Word document. Raises a ParserError otherwise, as parse() does."""
    if kind not in DESIGN_KINDS:
        raise ValueError(f"not a design kind: {kind!r}")
    check = check_design_pdf if kind == "design_pdf" else check_design_docx
    return await _call(kind, data, None, host, port, timeout_s, check, DESIGN_MAX_ANSWER)


# --- command line -------------------------------------------------------------------------------


async def _ping_line(size: int, host: str, port: int, timeout_s: float) -> tuple[int, dict]:
    started = time.monotonic()
    try:
        report = await ping(bytes(size), host=host, port=port, timeout_s=timeout_s)
    except ParserError as error:
        return 1, {"ok": False, "error": type(error).__name__, "code": error.code}
    sandbox = report.sandbox
    line = {
        "ok": report.in_force,
        "problems": list(report.problems),
        "bytes": report.received,
        "ms": int((time.monotonic() - started) * 1000),
        "child_cpu_ms": report.cpu_ms,
        "child_max_rss_kb": report.max_rss_kb,
        "seccomp": sandbox["seccomp"],
        "filters": sandbox["filters"],
        "denied": sum(1 for answer in sandbox["denied"].values() if answer == "EPERM"),
        "machine": sandbox["machine"],
        "kernel": sandbox["kernel"],
    }
    return (0 if report.in_force else 1), line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ingestion.parser_client")
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("ping", help="a round trip through a sandboxed child")
    one.add_argument("--bytes", type=int, default=0, help="zero bytes to send and get counted")
    one.add_argument("--host", default=HOST)
    one.add_argument("--port", type=int, default=PORT)
    one.add_argument("--timeout", type=float, default=PING_TIMEOUT_S)
    args = parser.parse_args(argv)
    if not 0 <= args.bytes <= MAX_SIZE:
        parser.error(f"--bytes must be between 0 and {MAX_SIZE}")
    status, line = asyncio.run(_ping_line(args.bytes, args.host, args.port, args.timeout))
    print(json.dumps(line, separators=(",", ":")))
    return status


if __name__ == "__main__":  # pragma: no cover, the tests call main()
    sys.exit(main())
