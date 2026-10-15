"""The documents the command line made (task D15; SPEC-documents.md, Decisions 5 and 7, "Versions
and freezing").

    read(kind, raw, employer_keys) -> Reading                  pure
    import_legacy(db, user_id, at) -> Summary                  the caller commits or rolls back

The jobs import attached each posting folder's `cv.json`, `letter.json`, `cv.pdf` and
`letter.pdf` as `job_files` with origin `legacy` (app.jobs.importer). This module turns them into
the posting's CV and letter, once:

- **A JSON file** becomes the document of its kind: template `classic`, the draft written by
  `import`, and version 1 with origin `legacy`. The language is the one of the content's printed
  text (`store.language_of`), the posting text's when the content prints nothing.
- **A sent posting** (the jobs module's `is_sent`: `applied`, or an `applied_on` date, as its
  hard rule 9) with its PDF: version 1 points at that file (`legacy_file_id`, the file's
  SHA-256) and is frozen with `legacy_applied`, so the PDF the company received is served byte
  for byte and never rendered again. Any other posting: no PDF is attached, and a render of the
  draft (unchanged) gives version 1 its PDF.
- **A PDF without its JSON**: nothing is imported, the file is counted. A JSON without its PDF is
  imported, counted, and renders like any draft.
- **Decision 7**: a `role` of an experience entry (the title the command line printed under the
  employer) is never content: it moves to `notes`, one line per role, and the template prints
  the profile's title. An employer key is matched exactly to the profile's keys; one that is not
  there is kept in the content, which never prints it, with the note `unknown_employer` (the
  rule of `rules.employer_keys`). A key is never guessed.
- **Content that does not validate** is never dropped: the document is made, its draft holds
  every field and list item that validates on its own (in the order of the file, the hard limits
  of `content.py` included), each one left out is noted `legacy_invalid` with its path in the
  file and the content code, and the file stays attached to the posting. Such a draft gets no
  version, except on a sent posting with its PDF, whose PDF is frozen as above all the same.
  A file over `content.RAW_MAX_BYTES`, or not a JSON object, is not read: the draft is empty.
- **Idempotent**: a posting that already has a document of a kind (imported before, written by
  a writer, or by the person) is left as it is and counted. The person's row is locked first,
  so two imports of one person run one after the other; each posting row is locked before its
  documents (the lock order of store.py), so a status change waits for the import.

Imported postings are never written again by the automatic generation: `eligibility` refuses a
posting with legacy files. Logs carry ids and counts, never a document's text.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Collection
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, undefer

from app.documents import content as c
from app.documents import profile_view, rules, store, templates
from app.documents.models import (
    Author,
    Document,
    FreezeReason,
    Kind,
    Origin,
    Scope,
)
from app.jobs import service as jobs_service
from app.jobs.models import FileOrigin, Job, JobFile
from app.profile import service as profile_service

logger = logging.getLogger("app.documents")

LEGACY_INVALID = "legacy_invalid"
NOT_JSON = "not_json"
NOT_OBJECT = "not_object"
# The notes of one draft name at most this many left out paths; the rest are counted in the last.
NOTED_MAX = 50
ROLE_LINE = "Role in the imported CV for {key}: {role}"
FILES = {Kind.cv: ("cv.json", "cv.pdf"), Kind.letter: ("letter.json", "letter.pdf")}
_LIST_MAX = {
    "highlights": c.HIGHLIGHTS_MAX,
    "competencies": c.COMPETENCIES_MAX,
    "experience": c.EMPLOYERS_MAX,
    "skills": c.SKILLS_MAX,
    "paragraphs": c.PARAGRAPHS_MAX,
}


# --- reading one file (pure) --------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """What a legacy file gives: `content` always validates; `left_out` holds `(path in the
    file, content code)` of what could not be kept; `roles` counts the roles moved to notes."""

    content: c.CvContent | c.LetterContent
    left_out: tuple[tuple[str, str], ...]
    roles: int
    unknown_employers: tuple[rules.Issue, ...]

    @property
    def valid(self) -> bool:
        return not self.left_out

    def notes(self) -> list[dict[str, Any]]:
        """The draft's notes: one `legacy_invalid` per left out path (at most NOTED_MAX, on the
        content's top level field it belongs to), then one `unknown_employer` per entry."""
        names = _names(type(self.content))
        out = []
        for path, code in self.left_out[:NOTED_MAX]:
            top = path.split(".", 1)[0]
            out.append(_note(top if top in names else "", path, code))
        if len(self.left_out) > NOTED_MAX:
            out.append(_note("", "", f"{len(self.left_out) - NOTED_MAX} more"))
        return out + [asdict(issue) for issue in self.unknown_employers]


def _note(field: str, path: str, reason: str) -> dict[str, Any]:
    issue = rules.Issue(field, 0, 0, LEGACY_INVALID, "invalid")
    return {**asdict(issue), "legacy_path": path, "reason": reason}


def _names(model: type[c.CvContent | c.LetterContent]) -> list[str]:
    """The content's field names as JSON spells them (`schema` for the schema version)."""
    return [info.alias or name for name, info in model.model_fields.items()]


def _first_error(kind: c.Kind, data: dict[str, Any]) -> c.FieldError | None:
    try:
        c.parse(kind, data)
    except c.ContentInvalidError as invalid:
        return invalid.errors[0]
    return None


def _decode(raw: bytes) -> tuple[dict[str, Any] | None, str | None]:
    if len(raw) > c.RAW_MAX_BYTES:
        return None, "content_too_large"
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, NOT_JSON
    if not isinstance(data, dict):
        return None, NOT_OBJECT
    return data, None


def _roles(data: dict[str, Any], left: list[tuple[str, str]]) -> int:
    """Decision 7: each experience entry's `role` leaves the entry and becomes a line of
    `notes`. A role that is not text is noted, an empty one holds nothing to keep."""
    entries = data.get("experience")
    if not isinstance(entries, list):
        return 0
    lines = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or "role" not in entry:
            continue
        entry = dict(entry)
        role = entry.pop("role")
        entries[i] = entry
        if isinstance(role, str):
            text = " ".join(role.split())
            if text:
                key = entry.get("employer_key")
                lines.append(ROLE_LINE.format(key=key if isinstance(key, str) else "?", role=text))
        else:
            left.append((f"experience.{i}.role", "wrong_type"))
    if lines:
        notes = data.get("notes")
        if isinstance(notes, str) or notes is None:
            data["notes"] = "\n\n".join(p for p in ((notes or "").strip(), "\n".join(lines)) if p)
        else:
            left.append(("notes", "wrong_type"))
            data["notes"] = "\n".join(lines)
    return len(lines)


def _items(kind: c.Kind, name: str, value: list[Any], left: list[tuple[str, str]]) -> list[Any]:
    """The items of a list field that validate, each alone and then after the ones kept (a
    duplicate employer, one too many): linear, every check on a list of at most the field's
    maximum count."""
    kept: list[Any] = []
    prefix = f"{name}.0"
    for i, item in enumerate(value):
        if len(kept) >= _LIST_MAX[name]:
            left.append((f"{name}.{i}", "too_many"))
            continue
        error = _first_error(kind, {name: [item]}) or _first_error(kind, {name: [*kept, item]})
        if error is None:
            kept.append(item)
            continue
        tail = error.path[len(prefix) :] if error.path.startswith(prefix) else ""
        left.append((f"{name}.{i}{tail}", error.code))
    return kept


def _salvage(kind: c.Kind, data: dict[str, Any], left: list[tuple[str, str]]) -> dict[str, Any]:
    """Every field of `data` that validates, in the content's order; a list field that does not
    keeps the items that do. An unknown field is left out."""
    names = _names(c.MODELS[kind])
    left += [(str(key), "unknown_field") for key in data if key not in names]
    kept: dict[str, Any] = {}
    for name in (n for n in names if n in data):
        value = data[name]
        if _first_error(kind, {name: value}) is not None and name in _LIST_MAX:
            value = _items(kind, name, value, left) if isinstance(value, list) else value
        error = _first_error(kind, {**kept, name: value})
        if error is None:
            kept[name] = value
        else:
            left.append((error.path or name, error.code))
    return kept


def unread(kind: Kind, code: str) -> Reading:
    """A file that could not be read at all: an empty draft, noted."""
    return Reading(c.parse(kind.value, {}), (("", code),), 0, ())  # type: ignore[arg-type]


def read(kind: Kind, raw: bytes, employer_keys: Collection[str]) -> Reading:
    """A legacy `cv.json` or `letter.json` as content (module text). Pure and linear in the
    size of the file, which is capped at `content.RAW_MAX_BYTES`."""
    which: c.Kind = kind.value  # type: ignore[assignment]
    left: list[tuple[str, str]] = []
    data, problem = _decode(raw)
    if data is None:
        return unread(kind, problem or NOT_JSON)
    roles = _roles(data, left) if kind is Kind.cv else 0
    content = c.parse(which, _salvage(which, data, left))
    unknown = tuple(rules.employer_keys(content, employer_keys))
    return Reading(content, tuple(left), roles, unknown)


# --- the import ---------------------------------------------------------------------------------


@dataclass
class Summary:
    """Counts of one import (never a text, never a path of the person's files)."""

    postings: int = 0  # postings holding a legacy cv or letter file, JSON or PDF
    cvs: int = 0  # CVs imported
    letters: int = 0  # letters imported
    versions: int = 0  # versions 1 made
    frozen: int = 0  # of them, frozen with legacy_applied (a sent posting's PDF)
    invalid: int = 0  # drafts with something left out, noted legacy_invalid
    json_without_pdf: int = 0  # imported, nothing to freeze: they render again
    pdf_without_json: int = 0  # not imported
    existing: int = 0  # a document of that kind was there already: left as it is
    roles: int = 0  # role fields moved to notes
    unknown_employers: int = 0  # experience entries whose key is no employer of the profile

    def as_dict(self) -> dict[str, int]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _lock_person(db: Session, user_id: uuid.UUID) -> None:
    """Lock 1 of store.py, for the whole import: a second import of the person waits for this
    one, then finds every document made and leaves it."""
    db.execute(
        sa.text("select set_config('lock_timeout', :ms, true)"),
        {"ms": f"{store.LOCK_TIMEOUT_MS}ms"},
    )
    try:
        db.execute(sa.text("SELECT 1 FROM users WHERE id = :u FOR NO KEY UPDATE"), {"u": user_id})
    except DBAPIError as exc:
        if store.is_busy(exc):
            raise store.DocumentBusyError(store.BUSY) from exc
        raise


Files = dict[str, tuple[uuid.UUID, bytes]]  # a posting's legacy files: name -> (id, sha256)


def _legacy_files(db: Session, user_id: uuid.UUID) -> dict[uuid.UUID, Files]:
    """The person's postings with a legacy cv or letter file, by posting id, in id order."""
    names = [name for pair in FILES.values() for name in pair]
    rows = db.execute(
        sa.select(JobFile.id, JobFile.job_id, JobFile.name, JobFile.sha256)
        .join(Job, Job.id == JobFile.job_id)
        .where(
            Job.user_id == user_id,
            JobFile.origin == FileOrigin.legacy,
            JobFile.name.in_(names),
        )
        .order_by(JobFile.job_id, JobFile.name, JobFile.created_at, JobFile.id)
    ).all()
    found: dict[uuid.UUID, Files] = {}
    for row in rows:
        # The first file of a name is the one read (the jobs import keeps names unique).
        found.setdefault(row.job_id, {}).setdefault(row.name, (row.id, bytes(row.sha256)))
    return found


def _employer_keys(db: Session, user_id: uuid.UUID, at: datetime) -> tuple[str, ...]:
    """The employer keys the template prints for a posting document with no base CV: the
    profile's."""
    profile = profile_service.get_profile(db, user_id, at)
    view = profile_view.build(profile_service.sections_view(profile), profile.revision, None)
    return view.employer_keys


def _reading(
    db: Session, kind: Kind, file_id: uuid.UUID, employer_keys: Collection[str]
) -> Reading:
    """The file read, its bytes loaded only when under the cap."""
    size = db.execute(sa.select(JobFile.size).where(JobFile.id == file_id)).scalar_one()
    if size > c.RAW_MAX_BYTES:
        return unread(kind, "content_too_large")
    file = db.execute(
        sa.select(JobFile).where(JobFile.id == file_id).options(undefer(JobFile.content))
    ).scalar_one()
    return read(kind, bytes(file.content), employer_keys)


def _printed_text(content: c.CvContent | c.LetterContent) -> str:
    return "\n".join(text for _, text in c.text_fields(content) if text)


def _insert(
    db: Session,
    user_id: uuid.UUID,
    posting: Job,
    kind: Kind,
    reading: Reading,
    at: datetime,
) -> uuid.UUID | None:
    """The posting's document of `kind`, made from the reading; None when one exists (made by
    another transaction since the check: its insert waited, then did nothing)."""
    printed = _printed_text(reading.content)
    statement = (
        pg_insert(Document)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            scope=Scope.posting,
            posting_id=posting.id,
            kind=kind,
            language=store.language_of(printed or posting.posting_text or ""),
            template_slug=store.CLASSIC,
            draft=c.dump(reading.content),
            draft_schema=c.SCHEMA,
            draft_revision=1,
            draft_written_by=Author.import_,
            draft_updated_at=at,
            checks=reading.notes(),
            checks_revision=1,
            created_at=at,
            updated_at=at,
        )
        .on_conflict_do_nothing(
            index_elements=["user_id", "posting_id", "kind"],
            index_where=sa.text("scope = 'posting'"),
        )
        .returning(Document.id)
    )
    return db.execute(statement).scalar_one_or_none()


def _import_one(
    db: Session,
    user_id: uuid.UUID,
    posting: Job,
    kind: Kind,
    files: Files,
    sent: bool,
    employer_keys: Collection[str],
    at: datetime,
    summary: Summary,
) -> None:
    json_name, pdf_name = FILES[kind]
    source, pdf = files.get(json_name), files.get(pdf_name)
    if source is None:
        if pdf is not None:
            summary.pdf_without_json += 1
        return
    if any(d.kind is kind for d in store.posting_documents(db, user_id, posting.id)):
        summary.existing += 1
        return
    reading = _reading(db, kind, source[0], employer_keys)
    document_id = _insert(db, user_id, posting, kind, reading, at)
    if document_id is None:
        summary.existing += 1
        return
    if kind is Kind.cv:
        summary.cvs += 1
    else:
        summary.letters += 1
    summary.roles += reading.roles
    summary.unknown_employers += len(reading.unknown_employers)
    summary.invalid += 0 if reading.valid else 1
    if pdf is None:
        summary.json_without_pdf += 1
    sent_pdf = pdf if sent else None
    if not reading.valid and sent_pdf is None:
        return
    made, _ = store.snapshot(
        db,
        user_id,
        document_id,
        Origin.legacy,
        at=at,
        template_version=templates.registry()[store.CLASSIC].version,
        legacy_file_id=sent_pdf[0] if sent_pdf is not None else None,
        pdf_sha256=sent_pdf[1] if sent_pdf is not None else None,
    )
    summary.versions += 1
    if sent_pdf is not None:
        store.freeze(db, user_id, made.id, FreezeReason.legacy_applied, at)
        summary.frozen += 1


def import_legacy(db: Session, user_id: uuid.UUID, at: datetime) -> Summary:
    """Import the person's legacy documents (module text). Writes in the caller's transaction:
    the caller commits, or rolls back for a dry run, which counts exactly what a run would do.
    Raises `store.DocumentBusyError` (a `JobBusyError`) when a lock is not free in time."""
    _lock_person(db, user_id)
    summary = Summary()
    found = _legacy_files(db, user_id)
    if not found:
        return summary
    keys = _employer_keys(db, user_id, at)
    for posting_id in found:
        posting = jobs_service.lock_job(db, user_id, posting_id)
        if posting is None:  # deleted since the read: nothing to import
            continue
        sent = jobs_service.is_sent(posting)
        summary.postings += 1
        for kind in (Kind.cv, Kind.letter):
            _import_one(db, user_id, posting, kind, found[posting_id], sent, keys, at, summary)
    logger.info("documents.legacy.imported", extra={"user_id": str(user_id), **summary.as_dict()})
    return summary
