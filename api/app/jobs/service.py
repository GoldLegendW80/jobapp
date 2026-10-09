"""Reading and writing a user's jobs, files and sweeps: the queries the J2+ routes build on.

Every function takes the caller's `user_id` and touches that user's rows only: there is no
function that reads a job, a file, an event or a sweep by id alone. A job of another user is
indistinguishable from a missing one (None), so the routes answer 404 for both.
The caller owns the transaction and commits it; functions flush so ids and constraint errors
show up at once. `now` is passed in (the app's clock), never read here. Nothing here logs
posting texts or file contents.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.jobs import slug as slugs
from app.jobs.models import (
    ContractType,
    Job,
    JobEvent,
    JobEventKind,
    JobFile,
    JobStatus,
    Sweep,
    SweepStatus,
    SweepTrigger,
)
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
