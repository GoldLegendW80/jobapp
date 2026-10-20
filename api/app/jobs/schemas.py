"""Shapes of the jobs API and of the jobs import document.

The import document (POST /api/jobs/import, `python -m app.jobs.cli import`, and what
importer.convert builds from Harald's ../data) is one JSON object:

    {"format": "jobapp-jobs", "version": 1, "exported_at": "<ISO 8601>",
     "jobs": [<ImportJob>, ...], "warnings": ["<what the converter could not map>", ...]}

Each job carries the row's fields (slug, company, title, location, contract_type, url, source,
status, fit, competencies, gaps, description, posting_text, published_on, found_on, applied_on,
notes, legacy) and its files: {"name", "mime", "size", "sha256" (hex), "base64" (standard)}.
The document is checked whole before anything is written: slugs unique, URLs unique (by the
duplicate rules of slug.py), every file's size and SHA-256 equal to its decoded bytes, a PDF
starting with %PDF-. Errors name the place (`jobs.12.files.0.sha256: ...`), never the value.

Size limits: IMPORT_HTTP_MAX_BYTES (10 MiB, the same as Caddy's body limit, so the api never
accepts what the proxy would cut) for the HTTP route; IMPORT_CLI_MAX_BYTES (64 MiB) for the CLI,
which reads the document from stdin inside the api container. Harald's data is about 18 MB once
converted, so it goes through the CLI (./deploy.sh jobs import), never through HTTP.
"""

import base64
import binascii
import hashlib
import json
import re
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.jobs import slug as slugs
from app.jobs.models import SLUG_PATTERN, ContractType, FileOrigin, JobEventKind, JobStatus

IMPORT_FORMAT = "jobapp-jobs"
IMPORT_VERSION = 1
IMPORT_HTTP_MAX_BYTES = 10 * 1024 * 1024
IMPORT_CLI_MAX_BYTES = 64 * 1024 * 1024
FILE_MAX_BYTES = 10 * 1024 * 1024
FILES_PER_JOB_MAX = 50
JOBS_MAX = 20_000
TEXT_MAX = 100_000  # description, notes
POSTING_MAX = 1_000_000
FIELD_MAX = 1_000  # company, title, location, url, source
TERM_MAX = 200
TERMS_MAX = 100

PDF_MAGIC = b"%PDF-"
# What an imported file may declare. Anything else is refused, so nothing served by the api can
# claim to be HTML or script for a browser.
FILE_MIMES = (
    "application/pdf",
    "application/json",
    "text/plain",
    "text/markdown",
    "text/x-python",
    "application/octet-stream",
)
FILE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$"

Field1k = Annotated[str, StringConstraints(max_length=FIELD_MAX)]
Term = Annotated[str, StringConstraints(min_length=1, max_length=TERM_MAX)]


def mime_of(name: str) -> str:
    """The type a file is stored and served with, from its extension."""
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {
        "pdf": "application/pdf",
        "json": "application/json",
        "txt": "text/plain",
        "md": "text/markdown",
        "py": "text/x-python",
    }.get(extension, "application/octet-stream")


class ImportFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(pattern=FILE_NAME_PATTERN)]
    mime: Literal[FILE_MIMES]  # type: ignore[valid-type]
    size: Annotated[int, Field(ge=0, le=FILE_MAX_BYTES)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    base64: Annotated[str, StringConstraints(max_length=(FILE_MAX_BYTES + 2) // 3 * 4)]
    _content: bytes = PrivateAttr(default=b"")

    @model_validator(mode="after")
    def _decodes(self) -> "ImportFile":
        try:
            data = base64.b64decode(self.base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("base64 is invalid or truncated") from None
        if len(data) != self.size:
            raise ValueError("size does not match the decoded bytes")
        if hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError("sha256 does not match the decoded bytes")
        if self.mime == "application/pdf" and not data.startswith(PDF_MAGIC):
            raise ValueError("declared application/pdf but the bytes are not a PDF")
        self._content = data
        return self

    @property
    def content(self) -> bytes:
        return self._content

    @classmethod
    def of(cls, name: str, data: bytes, mime: str | None = None) -> "ImportFile":
        return cls(
            name=name,
            mime=mime or mime_of(name),
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            base64=base64.b64encode(data).decode("ascii"),
        )


class ImportJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: Annotated[str, StringConstraints(pattern=SLUG_PATTERN, max_length=200)]
    company: Field1k
    title: Field1k
    location: Field1k = ""
    contract_type: ContractType = ContractType.CDI
    url: Annotated[str, StringConstraints(min_length=1, max_length=FIELD_MAX)] | None = None
    source: Field1k | None = None
    status: JobStatus = JobStatus.found
    fit: Annotated[int, Field(ge=0, le=100)] | None = None
    competencies: Annotated[list[Term], Field(max_length=TERMS_MAX)] = []
    gaps: Annotated[list[Term], Field(max_length=TERMS_MAX)] = []
    description: Annotated[str, StringConstraints(max_length=TEXT_MAX)] | None = None
    posting_text: Annotated[str, StringConstraints(max_length=POSTING_MAX)] | None = None
    published_on: date | None = None
    # None: today for a new row, the stored date for an existing one.
    found_on: date | None = None
    applied_on: date | None = None
    notes: Annotated[str, StringConstraints(max_length=TEXT_MAX)] = ""
    legacy: dict[str, Any] = {}
    files: Annotated[list[ImportFile], Field(max_length=FILES_PER_JOB_MAX)] = []

    @field_validator("files")
    @classmethod
    def _unique_names(cls, files: list[ImportFile]) -> list[ImportFile]:
        names = [f.name for f in files]
        if len(names) != len(set(names)):
            raise ValueError("two files have the same name")
        return files


class JobsImportDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["jobapp-jobs"] = IMPORT_FORMAT
    version: Literal[1] = IMPORT_VERSION
    exported_at: datetime
    jobs: Annotated[list[ImportJob], Field(max_length=JOBS_MAX)]
    warnings: Annotated[list[str], Field(max_length=JOBS_MAX * 10)] = []

    @model_validator(mode="after")
    def _unique_rows(self) -> "JobsImportDocument":
        seen_slugs: dict[str, int] = {}
        seen_urls: dict[str, int] = {}
        for index, job in enumerate(self.jobs):
            if job.slug in seen_slugs:
                raise ValueError(f"jobs.{index}: slug already used by jobs.{seen_slugs[job.slug]}")
            seen_slugs[job.slug] = index
            key = slugs.url_key(job.url)
            if key is not None:
                if key in seen_urls:
                    raise ValueError(f"jobs.{index}: url already used by jobs.{seen_urls[key]}")
                seen_urls[key] = index
        return self

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=1) + "\n"


class DocumentError(ValueError):
    """The document is refused before its rows are validated: too large (413), not JSON, not a
    jobs import, or a version this app does not read (422)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def parse_document(raw: bytes, limit: int) -> JobsImportDocument:
    """The import document from raw bytes. Raises DocumentError (size, JSON, format, version, in
    that order) and pydantic.ValidationError for the rows."""
    if len(raw) > limit:
        raise DocumentError(413, f"The import file is larger than {_mib(limit)}.")
    try:
        document = json.loads(raw)
    except (ValueError, RecursionError):
        raise DocumentError(422, "The import file is not valid JSON.") from None
    if not isinstance(document, dict):
        raise DocumentError(422, "The import file must hold one JSON object.")
    if document.get("format") != IMPORT_FORMAT:
        raise DocumentError(
            422, f'Unknown file format: expected "format": "{IMPORT_FORMAT}", a jobs import.'
        )
    version = document.get("version")
    if type(version) is not int or version != IMPORT_VERSION:
        raise DocumentError(422, f"Unsupported version: this app reads version {IMPORT_VERSION}.")
    return JobsImportDocument.model_validate(document)


def _mib(size: int) -> str:
    mib = 1024 * 1024
    return f"{size // mib} MiB" if size % mib == 0 else f"{size} bytes"


_VALUE_ERROR = re.compile(r"^Value error, ")


def describe_validation_error(exc: Exception) -> str:
    """The first problem of a pydantic error as `path: message`, never the value."""
    errors = exc.errors(include_input=False, include_url=False)  # type: ignore[attr-defined]
    first = errors[0]
    message = _VALUE_ERROR.sub("", str(first.get("msg", "invalid value")))
    where = ".".join(str(part) for part in first.get("loc", ()))
    return f"{where}: {message}" if where else message


# --- API ----------------------------------------------------------------------------------------


class ImportSummaryOut(BaseModel):
    created: int
    updated: int
    unchanged: int
    skipped: int
    files_added: int
    files_replaced: int
    files_unchanged: int
    files_kept: int  # a different file arrived for a sent (applied) row: the stored one was kept
    # Rows whose fit differs from the computed one: stored as set by hand (fit_overridden).
    fits_manual: int = 0
    fits_checked: bool = True  # False: the profile has no skills, no fit could be checked
    warnings: list[str]


class JobListItem(BaseModel):
    """One row of the list: everything but the posting text, the legacy columns and the files."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    company: str
    title: str
    location: str
    contract_type: ContractType
    url: str | None
    source: str | None
    status: JobStatus
    fit: int | None
    fit_overridden: bool
    competencies: list[str]
    gaps: list[str]
    description: str | None
    published_on: date | None
    found_on: date
    applied_on: date | None
    notes: str
    file_count: int
    created_at: datetime
    updated_at: datetime


class FitBucket(BaseModel):
    low: int  # inclusive
    high: int  # inclusive
    count: int


class FitHistogram(BaseModel):
    buckets: list[FitBucket]  # 0-9, 10-19, ..., 80-89, 90-100
    unscored: int


class JobListPage(BaseModel):
    items: list[JobListItem]
    # Pass as `cursor` (with the same sort) for the next page; null on the last page.
    next_cursor: str | None
    total: int  # rows matching every filter
    # Rows per status matching every filter except the status filter, every status listed.
    counts_by_status: dict[JobStatus, int]
    fit_histogram: FitHistogram  # of the rows matching every filter


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    mime: str
    size: int
    sha256: str
    origin: FileOrigin
    created_at: datetime

    @field_validator("sha256", mode="before")
    @classmethod
    def _hex(cls, value: object) -> object:
        return value.hex() if isinstance(value, bytes) else value


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    kind: JobEventKind
    data: dict[str, Any]


class JobDetail(JobListItem):
    posting_text: str | None
    legacy: dict[str, Any]
    files: list[FileOut]
    events: list[EventOut]  # newest first, at most EVENTS_IN_DETAIL
    events_total: int


# --- editing, bulk, add, rescore (J4) -----------------------------------------------------------

BULK_MAX = 500
RESCORE_IDS_MAX = 5_000
URL_SCHEMES = ("http", "https")
PATCH_NULLABLE = frozenset({"updated_at", "fit", "url"})


def _clean_line(value: str) -> str:
    return " ".join(value.split())


def _http_url(value: str | None) -> str | None:
    """An absolute http(s) URL, trimmed; empty means none. Anything else is refused, so a link
    the app renders can never be javascript: or data:."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    if any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("url must not contain spaces or control characters")
    parts = urlsplit(value)
    if parts.scheme.lower() not in URL_SCHEMES or not parts.netloc:
        raise ValueError("url must be an absolute http or https address")
    return value


Line1k = Annotated[str, StringConstraints(max_length=FIELD_MAX)]
Url1k = Annotated[str, StringConstraints(max_length=FIELD_MAX)]
Notes = Annotated[str, StringConstraints(max_length=TEXT_MAX)]
Posting = Annotated[str, StringConstraints(max_length=POSTING_MAX)]


class JobPatchIn(BaseModel):
    """PATCH /api/jobs/{id}: only the fields sent change. `fit`: a number sets it by hand, null
    goes back to the computed fit. `url`: null or "" removes it. `updated_at`: the version the
    edit is based on (as last read); a different stored value is a 409."""

    model_config = ConfigDict(extra="forbid")

    updated_at: datetime | None = None
    status: JobStatus | None = None
    notes: Notes | None = None
    fit: Annotated[int, Field(ge=0, le=100, strict=True)] | None = None
    company: Line1k | None = None
    title: Line1k | None = None
    location: Line1k | None = None
    contract_type: ContractType | None = None
    url: Url1k | None = None

    @field_validator("company", "title")
    @classmethod
    def _required_line(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = _clean_line(value)
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("location")
    @classmethod
    def _line(cls, value: str | None) -> str | None:
        return None if value is None else _clean_line(value)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _http_url(value)

    @model_validator(mode="after")
    def _something(self) -> "JobPatchIn":
        for name in self.model_fields_set - PATCH_NULLABLE:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        if not self.model_fields_set - {"updated_at"}:
            raise ValueError("nothing to change")
        return self


class BulkSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: JobStatus | None = None
    notes_append: Annotated[str, StringConstraints(max_length=TEXT_MAX)] | None = None

    @field_validator("notes_append")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("the note is empty")
        return value

    @model_validator(mode="after")
    def _something(self) -> "BulkSet":
        if self.status is None and self.notes_append is None:
            raise ValueError("set a status or a note to append")
        return self


class BulkIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=BULK_MAX)]
    set_: Annotated[BulkSet, Field(alias="set")]


class BulkItemOut(BaseModel):
    id: uuid.UUID
    changed: bool
    status: JobStatus
    previous_status: JobStatus


class BulkOut(BaseModel):
    results: list["BulkItemOut"]
    changed: int
    unchanged: int
    # Present when some statuses changed: POST it to /api/jobs/bulk/undo before it expires.
    undo_token: str | None
    undo_expires_at: datetime | None


class UndoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]


class UndoOut(BaseModel):
    restored: list[uuid.UUID]
    # Jobs changed again since the bulk change (or deleted): left as they are.
    skipped: list[uuid.UUID]


class JobCreateIn(BaseModel):
    """POST /api/jobs: a posting added by hand. The contract type, when not given, is read from
    the title, location and url as the engine did."""

    model_config = ConfigDict(extra="forbid")

    company: Line1k
    title: Line1k
    location: Line1k = ""
    contract_type: ContractType | None = None
    url: Url1k | None = None
    posting_text: Posting | None = None

    @field_validator("company", "title")
    @classmethod
    def _required_line(cls, value: str) -> str:
        value = _clean_line(value)
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("location")
    @classmethod
    def _line(cls, value: str) -> str:
        return _clean_line(value)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _http_url(value)

    @field_validator("posting_text")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.replace("\r\n", "\n").strip()
        return value or None


class RescoreIn(BaseModel):
    """POST /api/jobs/rescore. Without ids: every job. `after`: resume after this id (the
    `next_after` of the previous answer)."""

    model_config = ConfigDict(extra="forbid")

    ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=RESCORE_IDS_MAX)] | None = None
    overwrite_manual: bool = False
    after: uuid.UUID | None = None


class RescoreOut(BaseModel):
    scored: int
    changed: int
    unchanged: int
    skipped_manual: int
    skipped_no_text: int
    # Jobs not looked at yet (the time budget or the row cap stopped this call): call again
    # with after = next_after. 0 and null when done.
    remaining: int
    next_after: uuid.UUID | None


class DuplicateOut(BaseModel):
    detail: str
    existing_id: uuid.UUID
    existing_slug: str


class MissingOut(BaseModel):
    detail: str
    missing: list[uuid.UUID]


class StaleOut(BaseModel):
    detail: str
    updated_at: datetime


# --- sweeps (J5) --------------------------------------------------------------------------------


class BoardFailureOut(BaseModel):
    company: str
    ats: str
    org: str
    # not_found, blocked, rate_limited, upstream_error, timeout, network, too_large,
    # invalid_response, store_failed (app/jobs/ats.py, app/jobs/sweep.py)
    error: str
    message: str


class SourceOut(BaseModel):
    """One browser source of the sweep (browser B8, app/browser/sources): the postings it read
    completely (`found`), the new ones among them, and why it stopped early (`error`: None, or
    layout_changed, store_failed, lease_budget, lease_ended, time_budget, page_failed,
    browser_busy, browser_unavailable)."""

    kind: str
    query: str
    location: str | None = None
    result_pages: int = 0
    found: int = 0
    new: int = 0
    error: str | None = None


class SweepOut(BaseModel):
    """A sweep and its progress: while it runs the counters grow board by board."""

    id: uuid.UUID
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    boards_total: int = 0
    boards_done: int = 0
    boards_ok: int = 0
    boards_failed: list[BoardFailureOut] = Field(default_factory=list)
    current_board: str | None = None
    seen: int = 0
    matched: int = 0
    new: int = 0
    duplicates: int = 0
    skipped_by_rule: int = 0
    duration: float | None = None
    sources: list[SourceOut] = Field(default_factory=list)


class SweepJobOut(BaseModel):
    id: uuid.UUID
    company: str
    title: str
    location: str
    fit: int | None
    status: str


class SweepDetail(SweepOut):
    # The jobs this sweep added, best fit first (at most 200).
    new_jobs: list[SweepJobOut] = Field(default_factory=list)


class ScheduleOut(BaseModel):
    """The caller's `search.sweep_schedule` and when it fires next. `active` is false when it
    is off or no board is enabled (then nothing runs whatever the schedule says)."""

    enabled: bool
    days: list[str]
    times: list[str]
    timezone: str
    active: bool
    next_run: datetime | None


class SweepList(BaseModel):
    items: list[SweepOut]
    schedule: ScheduleOut


class SweepConflictOut(BaseModel):
    detail: str
    sweep_id: uuid.UUID
