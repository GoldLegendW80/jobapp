"""Reading and writing a user's jobs, files and sweeps: the queries the J2+ routes build on.

Every function takes the caller's `user_id` and touches that user's rows only: there is no
function that reads a job, a file, an event or a sweep by id alone. A job of another user is
indistinguishable from a missing one (None), so the routes answer 404 for both.
The caller owns the transaction and commits it; functions flush so ids and constraint errors
show up at once. `now` is passed in (the app's clock), never read here. Nothing here logs
posting texts or file contents.
"""

import base64
import binascii
import enum
import json
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, undefer

from app.jobs import slug as slugs
from app.jobs.models import (
    ContractType,
    FileOrigin,
    Job,
    JobEvent,
    JobEventKind,
    JobFile,
    JobStatus,
    Sweep,
    SweepStatus,
    SweepTrigger,
)
from app.jobs.schemas import ImportFile, ImportJob
from app.jobs.scoring import ScoreResult


class DuplicateJobError(Exception):
    """The posting is already in the user's list. Carries the existing job."""

    def __init__(self, job: Job) -> None:
        super().__init__("duplicate posting")
        self.job = job


class SweepRunningError(Exception):
    """The user already has a running sweep."""


# --- reading ------------------------------------------------------------------------------------


def jobs_of(user_id: uuid.UUID) -> sa.Select[tuple[Job]]:
    """The base query of every job read: the user's jobs, nothing else."""
    return sa.select(Job).where(Job.user_id == user_id)


def get_job(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> Job | None:
    return db.execute(jobs_of(user_id).where(Job.id == job_id)).scalar_one_or_none()


def get_job_by_slug(db: Session, user_id: uuid.UUID, slug: str) -> Job | None:
    return db.execute(jobs_of(user_id).where(Job.slug == slug)).scalar_one_or_none()


@dataclass(frozen=True)
class JobFilters:
    """The list filters of GET /api/jobs. Empty means no filter on that field."""

    statuses: Sequence[JobStatus] = ()
    contract_types: Sequence[ContractType] = ()
    company: str | None = None  # substring, case-insensitive
    q: str | None = None  # full text on company, title, location, notes (web search syntax)
    fit_min: int | None = None
    fit_max: int | None = None
    found_from: date | None = None
    found_to: date | None = None  # inclusive


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def filtered(user_id: uuid.UUID, filters: JobFilters) -> sa.Select[tuple[Job]]:
    """The user's jobs matching every filter given. A fit range leaves out unscored jobs."""
    query = jobs_of(user_id)
    if filters.statuses:
        query = query.where(Job.status.in_(list(filters.statuses)))
    if filters.contract_types:
        query = query.where(Job.contract_type.in_(list(filters.contract_types)))
    if filters.company and filters.company.strip():
        pattern = f"%{_like_escape(filters.company.strip())}%"
        query = query.where(Job.company.ilike(pattern, escape="\\"))
    if filters.q and filters.q.strip():
        tsquery = sa.func.websearch_to_tsquery(
            sa.literal_column("'simple'::regconfig"), filters.q.strip()
        )
        query = query.where(Job.search.bool_op("@@")(tsquery))
    if filters.fit_min is not None:
        query = query.where(Job.fit >= filters.fit_min)
    if filters.fit_max is not None:
        query = query.where(Job.fit <= filters.fit_max)
    if filters.found_from is not None:
        query = query.where(Job.found_on >= filters.found_from)
    if filters.found_to is not None:
        query = query.where(Job.found_on <= filters.found_to)
    return query


def list_events(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> list[JobEvent]:
    """The history of one of the user's jobs, oldest first."""
    query = (
        sa.select(JobEvent)
        .where(JobEvent.user_id == user_id, JobEvent.job_id == job_id)
        .order_by(JobEvent.at, JobEvent.id)
    )
    return list(db.execute(query).scalars())


def list_files(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> list[JobFile]:
    """The files of one of the user's jobs, without their bytes (deferred)."""
    query = (
        sa.select(JobFile)
        .join(Job, Job.id == JobFile.job_id)
        .where(Job.user_id == user_id, JobFile.job_id == job_id)
        .order_by(JobFile.created_at, JobFile.name)
    )
    return list(db.execute(query).scalars())


def get_file(
    db: Session, user_id: uuid.UUID, job_id: uuid.UUID, file_id: uuid.UUID
) -> JobFile | None:
    query = (
        sa.select(JobFile)
        .join(Job, Job.id == JobFile.job_id)
        .where(Job.user_id == user_id, JobFile.job_id == job_id, JobFile.id == file_id)
    )
    return db.execute(query).scalar_one_or_none()


# --- duplicates and slugs -----------------------------------------------------------------------


def find_duplicate(
    db: Session,
    user_id: uuid.UUID,
    *,
    company: str,
    title: str,
    location: str | None,
    url: str | None,
) -> Job | None:
    """The user's job that is the same posting (slug.py: same URL, or same company, title and
    location once normalised), or None. Compares in Python over the user's rows: the keys are
    not stored, and one user holds hundreds of rows, not millions."""
    key = slugs.PostingKey.of(company, title, location, url)
    rows = db.execute(jobs_of(user_id).order_by(Job.created_at, Job.id)).scalars()
    return slugs.find_duplicate(
        key, rows, lambda job: slugs.PostingKey.of(job.company, job.title, job.location, job.url)
    )


def free_slug(db: Session, user_id: uuid.UUID, base: str) -> str:
    """`base` or the first `base-N` the user does not have yet."""
    taken = set(
        db.execute(
            sa.select(Job.slug).where(
                Job.user_id == user_id,
                sa.or_(Job.slug == base, Job.slug.startswith(f"{base}-", autoescape=True)),
            )
        ).scalars()
    )
    return slugs.unique_slug(base, taken)


# --- writing ------------------------------------------------------------------------------------


def add_event(
    db: Session, job: Job, kind: JobEventKind, data: dict[str, Any], now: datetime
) -> JobEvent:
    event = JobEvent(job_id=job.id, user_id=job.user_id, kind=kind, data=data, at=now)
    db.add(event)
    return event


@dataclass
class NewJob:
    company: str
    title: str
    location: str = ""
    url: str | None = None
    contract_type: ContractType = ContractType.CDI
    source: str | None = None
    description: str | None = None
    posting_text: str | None = None
    published_on: date | None = None
    notes: str = ""
    legacy: dict[str, Any] = field(default_factory=dict)


def create_job(db: Session, user_id: uuid.UUID, new: NewJob, now: datetime) -> Job:
    """Store a new posting as `found`, with a free slug and a `created` event.

    Raises DuplicateJobError with the existing job when the user already has this posting.
    Two concurrent creations of the same posting are stopped by the unique indexes (slug, url):
    the second raises IntegrityError, which the caller turns into a retry or a 409.
    """
    existing = find_duplicate(
        db, user_id, company=new.company, title=new.title, location=new.location, url=new.url
    )
    if existing is not None:
        raise DuplicateJobError(existing)
    job = Job(
        user_id=user_id,
        slug=free_slug(db, user_id, slugs.slugify(new.company, new.title)),
        company=new.company,
        title=new.title,
        location=new.location,
        url=new.url or None,
        contract_type=new.contract_type,
        source=new.source,
        description=new.description,
        posting_text=new.posting_text,
        published_on=new.published_on,
        found_on=now.date(),
        notes=new.notes,
        legacy=new.legacy,
        status=JobStatus.found,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()
    add_event(db, job, JobEventKind.created, {"slug": job.slug}, now)
    db.flush()
    return job


def apply_score(
    db: Session, job: Job, result: ScoreResult, now: datetime, *, overwrite_manual: bool = False
) -> bool:
    """Record a scoring result. A fit set by hand is kept unless `overwrite_manual`; the
    competencies and gaps are always refreshed, as engine/jobs.py score_row does. Returns
    whether the fit changed (a `fit` event is recorded then)."""
    keep = job.fit_overridden and not overwrite_manual
    job.competencies = list(result.competencies)
    job.gaps = list(result.gaps)
    changed = not keep and job.fit != result.fit
    if changed:
        add_event(db, job, JobEventKind.fit, {"from": job.fit, "to": result.fit}, now)
        job.fit = result.fit
    if not keep:
        job.fit_overridden = False
    job.updated_at = now
    db.flush()
    return changed


def set_status(db: Session, job: Job, status: JobStatus, now: datetime) -> bool:
    """Move a job to `status` with a `status` event. Moving to applied fills `applied_on` the
    first time only. Returns False (and records nothing) when the status is unchanged."""
    if job.status == status:
        return False
    add_event(db, job, JobEventKind.status, {"from": job.status.value, "to": status.value}, now)
    job.status = status
    if status is JobStatus.applied and job.applied_on is None:
        job.applied_on = now.date()
    job.updated_at = now
    db.flush()
    return True


# --- sweeps -------------------------------------------------------------------------------------


def running_sweep(db: Session, user_id: uuid.UUID) -> Sweep | None:
    return db.execute(
        sa.select(Sweep).where(Sweep.user_id == user_id, Sweep.status == SweepStatus.running)
    ).scalar_one_or_none()


def get_sweep(db: Session, user_id: uuid.UUID, sweep_id: uuid.UUID) -> Sweep | None:
    return db.execute(
        sa.select(Sweep).where(Sweep.user_id == user_id, Sweep.id == sweep_id)
    ).scalar_one_or_none()


def start_sweep(db: Session, user_id: uuid.UUID, trigger: SweepTrigger, now: datetime) -> Sweep:
    """A new running sweep. Raises SweepRunningError when one is running already, including
    one started at the same moment by another transaction (the partial unique index decides)."""
    sweep = Sweep(user_id=user_id, trigger=trigger, started_at=now)
    try:
        with db.begin_nested():
            db.add(sweep)
            db.flush()
    except IntegrityError as exc:
        if "ix_sweeps_one_running_per_user" not in str(exc.orig):
            raise
        raise SweepRunningError() from None
    return sweep


# --- list page ----------------------------------------------------------------------------------


class JobSort(enum.StrEnum):
    """Orders of GET /api/jobs. Every order ends with the id, so ties have one stable order and
    a cursor never skips or repeats a row."""

    found_on = "found_on"  # newest found first, then best fit
    fit = "fit"  # best fit first, unscored last, then newest found
    company = "company"  # company A to Z (case-insensitive), then title
    status = "status"  # pipeline order (found ... skipped), then best fit
    updated = "updated"  # last changed first


@dataclass(frozen=True)
class _SortKey:
    expression: Any
    descending: bool
    parse: Callable[[Any], Any]  # the cursor's JSON value back to a bound value
    value_of: Callable[[Job], Any]  # the row's value, as JSON


# An unscored job sorts as fit -1: after every scored one in a descending order.
_FIT = _SortKey(sa.func.coalesce(Job.fit, -1), True, int, lambda j: -1 if j.fit is None else j.fit)
_FOUND = _SortKey(Job.found_on, True, date.fromisoformat, lambda j: j.found_on.isoformat())
_ID = _SortKey(Job.id, False, lambda v: uuid.UUID(str(v)), lambda j: str(j.id))
SORT_KEYS: dict[JobSort, tuple[_SortKey, ...]] = {
    JobSort.found_on: (_FOUND, _FIT, _ID),
    JobSort.fit: (_FIT, _FOUND, _ID),
    JobSort.company: (
        _SortKey(sa.func.lower(Job.company), False, str, lambda j: j.company.lower()),
        _SortKey(sa.func.lower(Job.title), False, str, lambda j: j.title.lower()),
        _ID,
    ),
    JobSort.status: (_SortKey(Job.status, False, JobStatus, lambda j: j.status.value), _FIT, _ID),
    JobSort.updated: (
        _SortKey(Job.updated_at, True, datetime.fromisoformat, lambda j: j.updated_at.isoformat()),
        _ID,
    ),
}


class InvalidCursorError(ValueError):
    """The cursor was not made by this API for this sort."""


def encode_cursor(sort: JobSort, job: Job) -> str:
    """An opaque cursor after `job` in `sort`: base64url of the sort and the row's sort values."""
    values = [key.value_of(job) for key in SORT_KEYS[sort]]
    raw = json.dumps({"s": sort.value, "k": values}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(sort: JobSort, cursor: str) -> list[Any]:
    """The bound sort values a cursor carries. Raises InvalidCursorError for anything else."""
    keys = SORT_KEYS[sort]
    try:
        body = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(body, dict) or body.get("s") != sort.value:
            raise InvalidCursorError("The cursor belongs to another sort or is invalid.")
        values = body.get("k")
        if not isinstance(values, list) or len(values) != len(keys):
            raise InvalidCursorError("The cursor is invalid.")
        parsed = []
        for key, value in zip(keys, values, strict=True):
            if isinstance(value, (dict, list, bool)) or value is None:
                raise InvalidCursorError("The cursor is invalid.")
            parsed.append(key.parse(value))
        return parsed
    except InvalidCursorError:
        raise
    except (ValueError, TypeError, binascii.Error, RecursionError):
        raise InvalidCursorError("The cursor is invalid.") from None


def _after(keys: tuple[_SortKey, ...], values: list[Any]) -> sa.ColumnElement[bool]:
    """Rows strictly after `values` in the order of `keys` (keyset pagination with mixed
    directions: k1 beyond v1, or k1 = v1 and k2 beyond v2, ...)."""
    clauses = []
    for index, key in enumerate(keys):
        equal = [k.expression == v for k, v in zip(keys[:index], values[:index], strict=True)]
        value = values[index]
        beyond = key.expression < value if key.descending else key.expression > value
        clauses.append(sa.and_(*equal, beyond))
    return sa.or_(*clauses)


def _file_count() -> Any:
    return (
        sa.select(sa.func.count(JobFile.id))
        .where(JobFile.job_id == Job.id)
        .correlate(Job)
        .scalar_subquery()
    )


@dataclass
class ListPage:
    rows: list[tuple[Job, int]]  # each job with its number of files
    next_cursor: str | None


def list_page(
    db: Session,
    user_id: uuid.UUID,
    filters: JobFilters,
    sort: JobSort,
    limit: int,
    cursor: str | None = None,
) -> ListPage:
    """One page of the user's jobs matching `filters`, in `sort`, after `cursor`. Raises
    InvalidCursorError for a cursor this sort did not make."""
    keys = SORT_KEYS[sort]
    query = filtered(user_id, filters).add_columns(_file_count())
    if cursor:
        query = query.where(_after(keys, decode_cursor(sort, cursor)))
    order = [k.expression.desc() if k.descending else k.expression.asc() for k in keys]
    found = [(row[0], int(row[1])) for row in db.execute(query.order_by(*order).limit(limit + 1))]
    page = found[:limit]
    next_cursor = encode_cursor(sort, page[-1][0]) if len(found) > limit else None
    return ListPage(rows=page, next_cursor=next_cursor)


def status_counts(db: Session, user_id: uuid.UUID, filters: JobFilters) -> dict[JobStatus, int]:
    """Rows per status matching `filters` except their status filter, every status listed in
    pipeline order (0 when none), so status tabs can show what each would hold."""
    base = filtered(user_id, replace(filters, statuses=())).with_only_columns(Job.status).subquery()
    query = sa.select(base.c.status, sa.func.count()).group_by(base.c.status)
    counts = {JobStatus(status): count for status, count in db.execute(query).all()}
    return {status: int(counts.get(status, 0)) for status in JobStatus}


FIT_BUCKETS = 10  # 0-9, 10-19, ..., 80-89, 90-100


def fit_histogram(db: Session, user_id: uuid.UUID, filters: JobFilters) -> tuple[list[int], int]:
    """Rows matching `filters` per fit bucket (100 falls in the last one), and the number of
    unscored rows among them."""
    base = filtered(user_id, filters).with_only_columns(Job.fit).subquery()
    tens = base.c.fit // 10  # integer division; NULL stays NULL (LEAST would drop it)
    buckets = [0] * FIT_BUCKETS
    unscored = 0
    for ten, count in db.execute(sa.select(tens, sa.func.count()).group_by(tens)).all():
        if ten is None:
            unscored = int(count)
        else:
            buckets[min(int(ten), FIT_BUCKETS - 1)] += int(count)
    return buckets, unscored


def recent_events(
    db: Session, user_id: uuid.UUID, job_id: uuid.UUID, limit: int
) -> tuple[list[JobEvent], int]:
    """The newest `limit` events of one of the user's jobs, newest first, and how many exist."""
    scope = (JobEvent.user_id == user_id, JobEvent.job_id == job_id)
    newest = sa.select(JobEvent).where(*scope).order_by(JobEvent.at.desc(), JobEvent.id.desc())
    events = list(db.execute(newest.limit(limit)).scalars())
    total = db.execute(sa.select(sa.func.count()).select_from(JobEvent).where(*scope)).scalar_one()
    return events, int(total)


# --- import -------------------------------------------------------------------------------------

# The fields an import sets, compared one by one on an existing row.
IMPORTED_FIELDS = (
    "company",
    "title",
    "location",
    "contract_type",
    "url",
    "source",
    "status",
    "fit",
    "competencies",
    "gaps",
    "description",
    "posting_text",
    "published_on",
    "found_on",
    "applied_on",
    "notes",
    "legacy",
)


@dataclass
class ImportSummary:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    files_added: int = 0
    files_replaced: int = 0
    files_unchanged: int = 0
    files_kept: int = 0
    warnings: list[str] = field(default_factory=list)


def is_sent(job: Job) -> bool:
    """Whether the job's files went to the company (hard rule 9: they are never replaced nor
    deleted): its status is applied, or it has an applied_on date (interview, rejected...)."""
    return job.status is JobStatus.applied or job.applied_on is not None


class _Index:
    """The user's rows as the import goes: by slug, by URL, by duplicate key, and their files."""

    def __init__(self, db: Session, user_id: uuid.UUID) -> None:
        self.jobs = list(
            db.execute(
                jobs_of(user_id).options(undefer(Job.posting_text)).order_by(Job.created_at, Job.id)
            ).scalars()
        )
        self.by_slug = {job.slug: job for job in self.jobs}
        self.by_url = {job.url: job for job in self.jobs if job.url}
        self.keys = {job.id: self.key_of(job) for job in self.jobs}
        self.files: dict[uuid.UUID, list[JobFile]] = defaultdict(list)
        query = sa.select(JobFile).join(Job, Job.id == JobFile.job_id).where(Job.user_id == user_id)
        for found in db.execute(query.order_by(JobFile.created_at, JobFile.id)).scalars():
            self.files[found.job_id].append(found)
        self.claimed: set[uuid.UUID] = set()

    @staticmethod
    def key_of(job: Job) -> slugs.PostingKey:
        return slugs.PostingKey.of(job.company, job.title, job.location, job.url)

    def match(self, item: ImportJob) -> Job | None:
        """The row this item updates: the same slug, else the same posting by the duplicate
        rules, never a row another item of this import already took."""
        job = self.by_slug.get(item.slug)
        if job is not None and job.id not in self.claimed:
            return job
        key = slugs.PostingKey.of(item.company, item.title, item.location, item.url)
        free = (j for j in self.jobs if j.id not in self.claimed)
        return slugs.find_duplicate(key, free, lambda j: self.keys[j.id])

    def track(self, job: Job, old_url: str | None) -> None:
        if job.id not in self.keys:
            self.jobs.append(job)
            self.by_slug[job.slug] = job
        if old_url and self.by_url.get(old_url) is job:
            del self.by_url[old_url]
        if job.url:
            self.by_url[job.url] = job
        self.keys[job.id] = self.key_of(job)
        self.claimed.add(job.id)


def _new_file(job: Job, item: ImportFile, now: datetime) -> JobFile:
    return JobFile(
        job_id=job.id,
        name=item.name,
        mime=item.mime,
        content=item.content,
        size=item.size,
        sha256=bytes.fromhex(item.sha256),
        origin=FileOrigin.legacy,
        created_at=now,
    )


def _create_imported(
    db: Session, user_id: uuid.UUID, item: ImportJob, index: _Index, summary: ImportSummary,
    now: datetime,
) -> Job:  # fmt: skip
    slug = item.slug
    if slug in index.by_slug:  # the row with this slug was taken by another item
        slug = slugs.unique_slug(slug, index.by_slug)
        summary.warnings.append(f"{item.slug}: slug taken by another row, stored as {slug}")
    job = Job(
        user_id=user_id,
        slug=slug,
        **{name: getattr(item, name) for name in IMPORTED_FIELDS if name != "found_on"},
        found_on=item.found_on or now.date(),
        fit_overridden=False,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()
    add_event(db, job, JobEventKind.created, {"slug": job.slug, "via": "import"}, now)
    for file in item.files:
        db.add(_new_file(job, file, now))
    summary.created += 1
    summary.files_added += len(item.files)
    return job


def _update_imported(
    db: Session, job: Job, item: ImportJob, index: _Index, summary: ImportSummary, now: datetime
) -> None:
    changed: dict[str, Any] = {}
    for name in IMPORTED_FIELDS:
        new = getattr(item, name)
        if name == "found_on" and new is None:
            continue
        if getattr(job, name) != new:
            changed[name] = new

    sent = is_sent(job) or item.status is JobStatus.applied
    current = index.files[job.id]
    by_name = {f.name: f for f in current}
    digests = {bytes(f.sha256) for f in current}
    added: list[str] = []
    replaced: list[str] = []
    for file in item.files:
        digest = bytes.fromhex(file.sha256)
        stored = by_name.get(file.name)
        if (stored is not None and bytes(stored.sha256) == digest) or (
            stored is None and digest in digests
        ):
            summary.files_unchanged += 1
        elif stored is None:
            new_file = _new_file(job, file, now)
            db.add(new_file)
            current.append(new_file)
            digests.add(digest)
            added.append(file.name)
        elif sent:
            summary.files_kept += 1
            summary.warnings.append(
                f"{job.slug}: {file.name} differs from the file already sent, the stored one "
                "is kept (files of applied rows never change)"
            )
        else:
            stored.content = file.content
            stored.size = file.size
            stored.sha256 = digest
            stored.mime = file.mime
            digests.add(digest)
            replaced.append(file.name)

    if not changed and not added and not replaced:
        summary.unchanged += 1
        return
    data: dict[str, Any] = {"changed": sorted(changed)}
    if "status" in changed:
        data["status"] = {"from": job.status.value, "to": changed["status"].value}
    if added:
        data["files_added"] = added
    if replaced:
        data["files_replaced"] = replaced
    for name, value in changed.items():
        setattr(job, name, value)
    job.updated_at = now
    add_event(db, job, JobEventKind.import_, data, now)
    summary.updated += 1
    summary.files_added += len(added)
    summary.files_replaced += len(replaced)


def import_jobs(
    db: Session, user_id: uuid.UUID, items: Sequence[ImportJob], now: datetime
) -> ImportSummary:
    """Load an import document's rows into the user's jobs; the caller commits (all or nothing).

    Idempotent: a row is matched by slug, else by the duplicate rules (same URL, or same company,
    title and location); a matched row gets only the fields that differ, with one `import` event
    naming them, and a row with nothing new is left alone (no event, updated_at kept). New rows
    are created with a `created` event. Files are matched by name, and a file whose bytes the job
    already holds (same SHA-256, any name) is not stored twice. A different file under a stored
    name replaces it, except on a sent row (`is_sent`), which keeps its file byte for byte with a
    warning. An import never deletes a row or a file. A row whose URL belongs to another row is
    skipped with a warning. The document wins on every field it carries, including status.
    """
    summary = ImportSummary()
    index = _Index(db, user_id)
    for item in items:
        job = index.match(item)
        owner = index.by_url.get(item.url) if item.url else None
        if owner is not None and owner is not job:
            summary.skipped += 1
            summary.warnings.append(
                f"{item.slug}: its url belongs to the row {owner.slug}, skipped"
            )
            continue
        if job is None:
            job = _create_imported(db, user_id, item, index, summary, now)
            index.track(job, None)
        else:
            old_url = job.url
            _update_imported(db, job, item, index, summary, now)
            index.track(job, old_url)
    db.flush()
    return summary
