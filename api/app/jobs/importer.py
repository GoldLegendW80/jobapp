"""Harald's job data converted into one jobs import document.

    convert(data_dir, now) -> JobsImportDocument

A pure function: it reads `<data_dir>/jobs.csv` and the folders `<data_dir>/jobs/<id>/`, and
returns the document (schemas.py). It writes nothing, touches no database and never imports
engine code. The CLI (`python -m app.jobs.cli convert`) writes the document out and
`python -m app.jobs.cli import` loads it for one user (service.import_jobs).

The CSV (read as UTF-8, a byte order mark ignored, quoted fields may hold commas, quotes and
newlines). Every column is mapped:
- id -> slug (the engine's id). An id that is not a valid slug is re-slugged; an id used twice
  keeps its first row, the later ones get the next free slug (`-2`, ...). Both with a warning and
  the original id in legacy["id"]. An empty id is built from company and title.
- company, title, location, url (empty -> null), description, notes: as they are.
- type -> contract_type. Empty: read from title, location and url by `kind`, the engine's own
  rule. Any other spelling is matched without case ("vie" -> VIE); an unknown value falls back
  to `kind` with a warning and the original in legacy["type"].
- status: matched without case and with spaces or hyphens read as underscores ("CV ready" ->
  cv_ready), then the closest valid status (difflib, cutoff 0.75), else found. A changed value
  warns and keeps the original in legacy["status"]. Empty is found, silently (the engine's
  default).
- found_on, applied_on: ISO dates (a datetime keeps its date). Anything else is null with a
  warning and the original in legacy["<column>"]; found_on null means today for a new row.
- fit: an integer 0 to 100 ("85.0" is 85); anything else is null with a warning and the
  original in legacy["fit"].
- competencies, gaps: the engine writes ", " between names; split on commas, trimmed, empties
  dropped.
- jd, cv, letter, report (paths into ../data): kept as they are in legacy. A jd, cv or letter
  path whose file does not exist warns.
- Columns the engine does not write are kept in legacy under their own name.

posting.txt (data/jobs/<id>/posting.txt): the header lines `KEY: value` up to the first blank
line (COMPANY, TITLE, LOCATION, URL, PUBLISHED, SOURCE; any upper-case key is read) go to
legacy["posting_header"]; the rest is posting_text. PUBLISHED gives published_on (empty is
null, a bad date warns). SOURCE gives `source` (its first word: "indeed", "linkedin"); without
it the URL names the board ("ashby:mistral", "greenhouse:anthropic", "lever:org"), else "import".
A file that is not UTF-8 is read as Latin-1 with a warning. No posting.txt: posting_text null.

Files of the folder, attached with origin legacy: FILE_NAMES (cv.pdf, letter.pdf, cv.json,
letter.json, answers.json, review.json, apply.py). cv.html and letter.html are skipped
silently (rebuilt from the JSON), any other file is skipped with a warning, and so is a PDF
that does not start with %PDF-. Folders with no CSV row warn.
"""

import csv
import difflib
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from app.jobs import slug as slugs
from app.jobs.models import SLUG_PATTERN, ContractType, JobStatus
from app.jobs.schemas import (
    FILE_MAX_BYTES,
    PDF_MAGIC,
    ImportFile,
    ImportJob,
    JobsImportDocument,
    describe_validation_error,
)

ENGINE_COLUMNS = (
    "id",
    "company",
    "title",
    "location",
    "type",
    "url",
    "found_on",
    "status",
    "applied_on",
    "fit",
    "competencies",
    "gaps",
    "description",
    "jd",
    "cv",
    "letter",
    "report",
    "notes",
)
LEGACY_COLUMNS = ("jd", "cv", "letter", "report")
REQUIRED_COLUMNS = ("id", "company", "title")
FILE_NAMES = (
    "cv.pdf",
    "letter.pdf",
    "cv.json",
    "letter.json",
    "answers.json",
    "review.json",
    "apply.py",
)
SKIPPED_SILENTLY = frozenset({"posting.txt", "cv.html", "letter.html", ".DS_Store"})
STATUS_CUTOFF = 0.75
_SLUG_RE = re.compile(SLUG_PATTERN)
_HEADER_LINE = re.compile(r"^([A-Z][A-Z_]*):[ \t]?(.*)$")
_FOLDER_NAME = re.compile(r"[A-Za-z0-9._-]+")

# engine/jobs.py `kind`, rule for rule: the first match in this order wins, else CDI.
_KINDS = (
    (ContractType.VIE, re.compile(r"\bVIE\b|\bV\.I\.E\b|mon-vie-via", re.I)),
    (ContractType.Alternance, re.compile(r"\balternance\b|\bapprenti", re.I)),
    (ContractType.Stage, re.compile(r"\bintern(ship)?\b|\bstage\b|\bstagiaire\b", re.I)),
    (ContractType.CDD, re.compile(r"\bCDD\b|fixed[- ]term|\btemporary\b", re.I)),
    (ContractType.Freelance, re.compile(r"\bfreelance\b|\bcontractor\b", re.I)),
)
_ATS_HOSTS = {
    "jobs.ashbyhq.com": "ashby",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "job-boards.eu.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever",
}


class ConversionError(Exception):
    """The CSV is missing, unreadable or lacks a required column."""


def kind(title: str, location: str = "", url: str = "") -> ContractType:
    """The contract type read from the title, location and url only (engine/jobs.py `kind`)."""
    text = f"{title} {location} {url}"
    for contract, pattern in _KINDS:
        if pattern.search(text):
            return contract
    return ContractType.CDI


def source_of(url: str | None, header_source: str | None) -> str:
    """Where the posting came from: the SOURCE header's first word, the ATS board, or import."""
    if header_source and header_source.split():
        return header_source.split()[0].lower()
    if url:
        parts = urlsplit(url)
        ats = _ATS_HOSTS.get((parts.hostname or "").lower())
        org = parts.path.strip("/").split("/")[0] if parts.path.strip("/") else ""
        if ats and org:
            return f"{ats}:{org}"
    return "import"


@dataclass
class Posting:
    header: dict[str, str]
    text: str


def parse_posting(raw: str) -> Posting:
    """The header lines `KEY: value` up to the first blank line, and the text after it. A file
    whose first line is not a header line is all text."""
    lines = raw.replace("\r\n", "\n").split("\n")
    header: dict[str, str] = {}
    index = 0
    while index < len(lines):
        match = _HEADER_LINE.match(lines[index])
        if match is None:
            break
        header[match.group(1)] = match.group(2).strip()
        index += 1
    if header and index < len(lines) and lines[index].strip() == "":
        index += 1
    elif not header:
        index = 0
    return Posting(header=header, text="\n".join(lines[index:]).strip("\n"))


def _date(value: str) -> date | None:
    """An ISO date, or the date of an ISO datetime; None for anything else."""
    value = value.strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _fit(value: str) -> int | None:
    try:
        number = float(value.strip())
    except ValueError:
        return None
    if not number.is_integer() or not 0 <= number <= 100:
        return None
    return int(number)


def _terms(value: str) -> list[str]:
    return [term.strip() for term in value.split(",") if term.strip()]


def _contract(value: str) -> ContractType | None:
    by_lower = {c.value.lower(): c for c in ContractType}
    return by_lower.get(value.strip().lower())


def _status(value: str) -> JobStatus | None:
    """The status a spelling means, or None when nothing is close enough."""
    key = re.sub(r"[\s-]+", "_", value.strip().lower())
    names = [s.value for s in JobStatus]
    if key in names:
        return JobStatus(key)
    close = difflib.get_close_matches(key, names, n=1, cutoff=STATUS_CUTOFF)
    return JobStatus(close[0]) if close else None


@dataclass
class _Row:
    """Conversion state of one CSV row."""

    number: int  # line of the CSV, header = 1
    label: str  # how warnings name the row
    warnings: list[str]
    legacy: dict[str, Any] = field(default_factory=dict)

    def warn(self, message: str) -> None:
        self.warnings.append(f"{self.label}: {message}")


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConversionError(f"cannot read {path.name}: {exc.strerror or exc}") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ConversionError(f"{path.name} is not UTF-8") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    columns = [c.strip() for c in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise ConversionError(f"{path.name} lacks the column(s) {', '.join(missing)}")
    reader.fieldnames = columns
    rows = []
    for row in reader:
        # A row with more cells than the header puts them under None; fewer leaves None values.
        extra = row.pop(None, None)  # type: ignore[call-overload]
        clean = {k: (v if isinstance(v, str) else "") for k, v in row.items()}
        if extra:
            clean["_extra_cells"] = ",".join(str(cell) for cell in extra)
        rows.append(clean)
    return columns, rows


def _read_posting(path: Path, row: _Row) -> Posting | None:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        row.warn("posting.txt is not UTF-8, read as Latin-1")
    return parse_posting(text)


def _read_files(folder: Path, row: _Row) -> list[ImportFile]:
    files: list[ImportFile] = []
    if not folder.is_dir():
        return files
    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        name = entry.name
        if name in SKIPPED_SILENTLY:
            continue
        if name not in FILE_NAMES or not entry.is_file() or entry.is_symlink():
            row.warn(f"{name} is not a known job file, skipped")
            continue
        data = entry.read_bytes()
        if len(data) > FILE_MAX_BYTES:
            row.warn(f"{name} is larger than {FILE_MAX_BYTES // (1024 * 1024)} MiB, skipped")
            continue
        if name.endswith(".pdf") and not data.startswith(PDF_MAGIC):
            row.warn(f"{name} is not a PDF, skipped")
            continue
        files.append(ImportFile.of(name, data))
    return files


def _slug(raw_id: str, company: str, title: str, taken: set[str], row: _Row) -> str:
    candidate = raw_id.strip()
    if not candidate:
        candidate = slugs.slugify(company, title)
        row.warn(f"no id, slug {candidate} built from company and title")
    elif not _SLUG_RE.match(candidate) or len(candidate) > 200:
        candidate = slugs.slug(candidate)
        row.legacy["id"] = raw_id
        row.warn(f"id is not a valid slug, stored as {candidate}")
    if candidate in taken:
        free = slugs.unique_slug(candidate, taken)
        row.legacy["id"] = raw_id
        row.warn(f"id already used by an earlier row, stored as {free}")
        candidate = free
    taken.add(candidate)
    return candidate


def _convert_row(
    values: dict[str, str],
    row: _Row,
    data_dir: Path,
    taken_slugs: set[str],
    taken_urls: set[str],
) -> ImportJob:
    get = values.get
    company = get("company", "") or ""
    title = get("title", "") or ""
    location = get("location", "") or ""
    url = (get("url", "") or "").strip() or None
    raw_id = get("id", "") or ""
    job_slug = _slug(raw_id, company, title, taken_slugs, row)
    row.label = job_slug

    if url is not None:
        key = slugs.url_key(url)
        if key in taken_urls:
            row.legacy["url"] = url
            row.warn("url already used by an earlier row, kept in legacy only")
            url = None
        else:
            taken_urls.add(key)  # type: ignore[arg-type]

    raw_type = (get("type", "") or "").strip()
    contract = _contract(raw_type) if raw_type else kind(title, location, url or "")
    if contract is None:
        contract = kind(title, location, url or "")
        row.legacy["type"] = raw_type
        row.warn(f"unknown type, stored as {contract.value}")

    raw_status = (get("status", "") or "").strip()
    status = JobStatus.found
    if raw_status:
        matched = _status(raw_status)
        status = matched or JobStatus.found
        if matched is None or matched.value != raw_status:
            row.legacy["status"] = raw_status
            row.warn(f"unknown status, stored as {status.value}")

    dates: dict[str, date | None] = {}
    for column in ("found_on", "applied_on"):
        raw_date = (get(column, "") or "").strip()
        dates[column] = _date(raw_date) if raw_date else None
        if raw_date and dates[column] is None:
            row.legacy[column] = raw_date
            row.warn(f"{column} is not a date, left empty")

    raw_fit = (get("fit", "") or "").strip()
    fit = _fit(raw_fit) if raw_fit else None
    if raw_fit and fit is None:
        row.legacy["fit"] = raw_fit
        row.warn("fit is not an integer from 0 to 100, left empty")

    for column in LEGACY_COLUMNS:
        value = get(column, "") or ""
        if value:
            row.legacy[column] = value
            if column != "report" and not (data_dir.parent / value).is_file():
                row.warn(f"the {column} column names a missing file")
    for column, value in values.items():
        if column not in ENGINE_COLUMNS and value:
            row.legacy[column] = value

    # The folder is read only for an id that is a plain name: never "..", never a path.
    plain = _FOLDER_NAME.fullmatch(raw_id.strip()) and raw_id.strip() not in (".", "..")
    folder = data_dir / "jobs" / raw_id.strip() if plain else None
    posting = _read_posting(folder / "posting.txt", row) if folder else None
    published_on = None
    header_source = None
    if posting is not None:
        row.legacy["posting_header"] = posting.header
        header_source = posting.header.get("SOURCE")
        published = posting.header.get("PUBLISHED", "")
        published_on = _date(published) if published else None
        if published and published_on is None:
            row.warn("PUBLISHED of posting.txt is not a date, left empty")
    files = _read_files(folder, row) if folder else []

    return ImportJob(
        slug=job_slug,
        company=company,
        title=title,
        location=location,
        contract_type=contract,
        url=url,
        source=source_of(url, header_source),
        status=status,
        fit=fit,
        competencies=_terms(get("competencies", "") or ""),
        gaps=_terms(get("gaps", "") or ""),
        description=(get("description", "") or None),
        posting_text=posting.text if posting is not None else None,
        published_on=published_on,
        found_on=dates["found_on"],
        applied_on=dates["applied_on"],
        notes=get("notes", "") or "",
        legacy=row.legacy,
        files=files,
    )


def convert(data_dir: Path, now: datetime | None = None) -> JobsImportDocument:
    """The import document of `data_dir` (the engine's data/ folder). Raises ConversionError when
    jobs.csv is missing or unusable; anything a row cannot map becomes a warning."""
    columns, rows = _read_csv(data_dir / "jobs.csv")
    del columns
    warnings: list[str] = []
    jobs: list[ImportJob] = []
    taken_slugs: set[str] = set()
    taken_urls: set[str] = set()
    for number, values in enumerate(rows, start=2):
        row = _Row(number=number, label=f"row {number}", warnings=warnings)
        try:
            jobs.append(_convert_row(values, row, data_dir, taken_slugs, taken_urls))
        except ValidationError as exc:
            raise ConversionError(
                f"row {number} ({row.label}) breaks a rule: {describe_validation_error(exc)}"
            ) from None
    folders = data_dir / "jobs"
    if folders.is_dir():
        ids = {(values.get("id") or "").strip() for values in rows}
        for orphan in sorted(p.name for p in folders.iterdir() if p.is_dir()):
            if orphan not in ids:
                warnings.append(f"jobs/{orphan}: folder with no CSV row, skipped")
    return JobsImportDocument(exported_at=now or datetime.now(UTC), jobs=jobs, warnings=warnings)
