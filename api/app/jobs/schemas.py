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
