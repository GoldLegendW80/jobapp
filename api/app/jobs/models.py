"""Tables of the jobs module: jobs, job_events, job_files, sweeps (SPEC-jobs.md, Data model).

- jobs: one posting of one user. `slug` is the engine's id (`<company>-<role>`), unique per user;
  `url`, when known, is unique per user too (a partial unique index, so many rows may have none).
  `fit` is 0 to 100 or null (never scored); `fit_overridden` marks a fit set by hand, which a
  rescore keeps unless the user asks. `competencies` and `gaps` are what scoring named.
  `legacy` keeps the CSV columns the import cannot map. `search` is a generated tsvector over
  company, title, location and notes with the 'simple' configuration (no stemming, no stop
  words: the postings are French and English), indexed with GIN for the `q` filter. 'simple'
  lowercases but keeps accents, so "developpeur" does not find "développeur".
- job_events: append-only history of a job (created, status, fit, note, edit, import, sweep).
- job_files: files attached to a job, stored in the row (`bytes`), with their size and SHA-256
  checked by the database (size = octet_length(bytes), sha256 is 32 bytes).
- sweeps: one search of the job websites of one user; at most one `running` sweep per user
  (partial unique index), and `finished_at` is set exactly when the sweep is no longer running.

Large columns (`posting_text`, `bytes`, `search`) are deferred so list queries never load them.
Migration: alembic/versions/0004_jobs.py, which must stay in step with this file
(`alembic check` in the tests compares them).
"""

import enum
import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models_base import Base

SHA256_BYTES = 32
SLUG_PATTERN = "^[a-z0-9]+(-[a-z0-9]+)*$"
# The generated full-text column, written exactly as Postgres prints it back, so `alembic
# check` sees no difference between this model and the migrated schema.
SEARCH_EXPRESSION = (
    "to_tsvector('simple'::regconfig, ((((((COALESCE(company, ''::text) || ' '::text) || "
    "COALESCE(title, ''::text)) || ' '::text) || COALESCE(location, ''::text)) || ' '::text) || "
    "COALESCE(notes, ''::text)))"
)


# The pay of a posting (opportunities O3, migration 0021): all four columns or none, the figures
# whole numbers from 1 to PAY_MAX with the minimum at most the maximum, the currency three capital
# letters. A pay without its currency or its period is never stored.
PAY_MAX = 10_000_000
PAY_CHECK = (
    "(pay_min IS NULL AND pay_max IS NULL AND pay_currency IS NULL AND pay_period IS NULL) OR "
    "(pay_min IS NOT NULL AND pay_max IS NOT NULL AND pay_currency IS NOT NULL AND "
    f"pay_period IS NOT NULL AND pay_min >= 1 AND pay_min <= pay_max AND pay_max <= {PAY_MAX} "
    "AND pay_currency ~ '^[A-Z]{3}$')"
)


class ContractType(enum.StrEnum):
    CDI = "CDI"
    CDD = "CDD"
    VIE = "VIE"
    Stage = "Stage"
    Alternance = "Alternance"
    Freelance = "Freelance"


class JobStatus(enum.StrEnum):
    """Pipeline order, as engine/jobs.py STATUSES."""

    found = "found"
    shortlisted = "shortlisted"
    cv_ready = "cv_ready"
    staged = "staged"
    applied = "applied"
    interview = "interview"
    rejected = "rejected"
    closed = "closed"
    skipped = "skipped"


class JobEventKind(enum.StrEnum):
    created = "created"
    status = "status"
    fit = "fit"
    note = "note"
    edit = "edit"
    import_ = "import"
    sweep = "sweep"


class FileOrigin(enum.StrEnum):
    legacy = "legacy"
    generated = "generated"
    upload = "upload"


class PayPeriod(enum.StrEnum):
    """What a posting's pay is counted per (opportunities O3)."""

    year = "year"
    month = "month"
    day = "day"
    hour = "hour"


class WorkMode(enum.StrEnum):
    """Where a posting's work is done, as the ATS or the location states it (opportunities O3)."""

    remote = "remote"
    hybrid = "hybrid"
    office = "office"


class SweepTrigger(enum.StrEnum):
    manual = "manual"
    schedule = "schedule"


class SweepStatus(enum.StrEnum):
    running = "running"
    done = "done"
    failed = "failed"


def _enum(cls: type[enum.Enum], name: str) -> sa.Enum:
    # Postgres enum named explicitly; the stored labels are the values, not the names.
    return sa.Enum(cls, name=name, values_callable=lambda members: [m.value for m in members])


CONTRACT_TYPE_ENUM = _enum(ContractType, "contract_type")
JOB_STATUS_ENUM = _enum(JobStatus, "job_status")
JOB_EVENT_KIND_ENUM = _enum(JobEventKind, "job_event_kind")
FILE_ORIGIN_ENUM = _enum(FileOrigin, "file_origin")
PAY_PERIOD_ENUM = _enum(PayPeriod, "pay_period")
WORK_MODE_ENUM = _enum(WorkMode, "work_mode")
SWEEP_TRIGGER_ENUM = _enum(SweepTrigger, "sweep_trigger")
SWEEP_STATUS_ENUM = _enum(SweepStatus, "sweep_status")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        pg.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )


def _now_column() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


def _empty_text_array() -> Mapped[list[str]]:
    return mapped_column(
        pg.ARRAY(sa.Text), nullable=False, default=list, server_default=sa.text("'{}'::text[]")
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "slug", name="uq_jobs_user_id_slug"),
        sa.UniqueConstraint("id", "user_id", name="uq_jobs_id_user_id"),
        sa.CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        sa.CheckConstraint("fit IS NULL OR (fit >= 0 AND fit <= 100)", name="fit_range"),
        sa.CheckConstraint("url IS NULL OR url <> ''", name="url_not_empty"),
        sa.CheckConstraint("jsonb_typeof(legacy) = 'object'", name="legacy_is_object"),
        sa.CheckConstraint(PAY_CHECK, name="pay_complete"),
        sa.Index(
            "ix_jobs_user_id_url", "user_id", "url", unique=True, postgresql_where="url IS NOT NULL"
        ),
        sa.Index("ix_jobs_user_id_status", "user_id", "status"),
        sa.Index("ix_jobs_user_id_found_on", "user_id", sa.text("found_on DESC")),
        sa.Index("ix_jobs_user_id_fit", "user_id", "fit"),
        sa.Index("ix_jobs_user_id_company", "user_id", "company"),
        sa.Index("ix_jobs_search", "search", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False)
    company: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    location: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    contract_type: Mapped[ContractType] = mapped_column(
        CONTRACT_TYPE_ENUM,
        nullable=False,
        default=ContractType.CDI,
        server_default=ContractType.CDI.value,
    )
    url: Mapped[str | None] = mapped_column(sa.Text)
    # Where the posting came from: a job website's host, "manual", "import", or a board
    # ("ashby:mistral") or feed of before 2026-10-28, ...
    source: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[JobStatus] = mapped_column(
        JOB_STATUS_ENUM,
        nullable=False,
        default=JobStatus.found,
        server_default=JobStatus.found.value,
    )
    fit: Mapped[int | None] = mapped_column(sa.Integer)
    fit_overridden: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    competencies: Mapped[list[str]] = _empty_text_array()
    gaps: Mapped[list[str]] = _empty_text_array()
    description: Mapped[str | None] = mapped_column(sa.Text)
    posting_text: Mapped[str | None] = mapped_column(sa.Text, deferred=True)
    published_on: Mapped[date | None] = mapped_column(sa.Date)
    # Pay and work mode (opportunities O3): what the ATS or the posting's text states, or the
    # person's own edit; null when nothing was stated. Never a guess (app/opportunities/pay.py).
    pay_min: Mapped[int | None] = mapped_column(sa.Integer)
    pay_max: Mapped[int | None] = mapped_column(sa.Integer)
    pay_currency: Mapped[str | None] = mapped_column(sa.CHAR(3))
    pay_period: Mapped[PayPeriod | None] = mapped_column(PAY_PERIOD_ENUM)
    work_mode: Mapped[WorkMode | None] = mapped_column(WORK_MODE_ENUM)
    found_on: Mapped[date] = mapped_column(
        sa.Date, nullable=False, server_default=sa.text("CURRENT_DATE")
    )
    applied_on: Mapped[date | None] = mapped_column(sa.Date)
    notes: Mapped[str] = mapped_column(sa.Text, nullable=False, default="", server_default="")
    legacy: Mapped[dict[str, Any]] = mapped_column(
        pg.JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    search: Mapped[Any] = mapped_column(
        pg.TSVECTOR, sa.Computed(SEARCH_EXPRESSION, persisted=True), deferred=True
    )
    created_at: Mapped[datetime] = _now_column()
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class JobEvent(Base):
    """One change of a job. `data` holds what changed, for instance {"from": ..., "to": ...}."""

    __tablename__ = "job_events"
    __table_args__ = (
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="data_is_object"),
        sa.Index("ix_job_events_job_id_at", "job_id", "at"),
        sa.Index("ix_job_events_user_id", "user_id"),
    )

    # bigserial
    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = _user_fk()
    at: Mapped[datetime] = _now_column()
    kind: Mapped[JobEventKind] = mapped_column(JOB_EVENT_KIND_ENUM, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(
        pg.JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )


class JobFile(Base):
    __tablename__ = "job_files"
    __table_args__ = (
        sa.CheckConstraint("size = octet_length(bytes)", name="size_matches_bytes"),
        sa.CheckConstraint(f"octet_length(sha256) = {SHA256_BYTES}", name="sha256_length"),
        sa.CheckConstraint("name <> ''", name="name_not_empty"),
        sa.CheckConstraint("mime <> ''", name="mime_not_empty"),
        sa.Index("ix_job_files_job_id", "job_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    mime: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Attribute `content`, column `bytes` (the spec's name; `bytes` would shadow the builtin).
    content: Mapped[bytes] = mapped_column("bytes", pg.BYTEA, nullable=False, deferred=True)
    size: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    sha256: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False)
    origin: Mapped[FileOrigin] = mapped_column(FILE_ORIGIN_ENUM, nullable=False)
    created_at: Mapped[datetime] = _now_column()


class Sweep(Base):
    __tablename__ = "sweeps"
    __table_args__ = (
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)", name="finished_unless_running"
        ),
        sa.CheckConstraint("jsonb_typeof(stats) = 'object'", name="stats_is_object"),
        sa.Index(
            "ix_sweeps_one_running_per_user",
            "user_id",
            unique=True,
            postgresql_where="status = 'running'",
        ),
        sa.Index("ix_sweeps_user_id_started_at", "user_id", sa.text("started_at DESC")),
        # The slot of a scheduled sweep (jobs J5, migration 0005): what the schedule tick checks.
        sa.Index(
            "ix_sweeps_user_id_schedule_slot",
            "user_id",
            sa.text("(stats ->> 'schedule_slot')"),
            postgresql_where=sa.text("trigger = 'schedule'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    started_at: Mapped[datetime] = _now_column()
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    trigger: Mapped[SweepTrigger] = mapped_column(SWEEP_TRIGGER_ENUM, nullable=False)
    status: Mapped[SweepStatus] = mapped_column(
        SWEEP_STATUS_ENUM,
        nullable=False,
        default=SweepStatus.running,
        server_default=SweepStatus.running.value,
    )
    stats: Mapped[dict[str, Any]] = mapped_column(
        pg.JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(sa.Text)
