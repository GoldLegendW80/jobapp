"""Jobs: jobs, job_events, job_files, sweeps; their six enums; list and full-text indexes.

Written by hand to match app/jobs/models.py (the tests run `alembic check` against it).
Every statement is plain DDL so `alembic upgrade head --sql` works without a database.
Every table cascades from users (job_events and job_files also from jobs): a deleted account
leaves no posting, file or sweep behind.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = {
    "contract_type": ("CDI", "CDD", "VIE", "Stage", "Alternance", "Freelance"),
    "job_status": (
        "found",
        "shortlisted",
        "cv_ready",
        "staged",
        "applied",
        "interview",
        "rejected",
        "closed",
        "skipped",
    ),
    "job_event_kind": ("created", "status", "fit", "note", "edit", "import", "sweep"),
    "file_origin": ("legacy", "generated", "upload"),
    "sweep_trigger": ("manual", "schedule"),
    "sweep_status": ("running", "done", "failed"),
}

# As Postgres prints it back (app/jobs/models.py SEARCH_EXPRESSION).
SEARCH_EXPRESSION = (
    "to_tsvector('simple'::regconfig, ((((((COALESCE(company, ''::text) || ' '::text) || "
    "COALESCE(title, ''::text)) || ' '::text) || COALESCE(location, ''::text)) || ' '::text) || "
    "COALESCE(notes, ''::text)))"
)


def _enum(name: str) -> pg.ENUM:
    return pg.ENUM(*ENUMS[name], name=name, create_type=False)


def _tz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _now() -> sa.TextClause:
    return sa.text("now()")


def _user_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["user_id"], ["users.id"], name=f"fk_{table}_user_id_users", ondelete="CASCADE"
    )


def _job_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["job_id"], ["jobs.id"], name=f"fk_{table}_job_id_jobs", ondelete="CASCADE"
    )


def upgrade() -> None:
    for name, values in ENUMS.items():
        labels = ", ".join(f"'{value}'" for value in values)
        op.execute(f"CREATE TYPE {name} AS ENUM ({labels})")

    op.create_table(
        "jobs",
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False, server_default=""),
        sa.Column("contract_type", _enum("contract_type"), nullable=False, server_default="CDI"),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("status", _enum("job_status"), nullable=False, server_default="found"),
        sa.Column("fit", sa.Integer(), nullable=True),
        sa.Column("fit_overridden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "competencies",
            pg.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "gaps", pg.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("posting_text", sa.Text(), nullable=True),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("found_on", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE")),
        sa.Column("applied_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("legacy", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("search", pg.TSVECTOR(), sa.Computed(SEARCH_EXPRESSION, persisted=True)),
        sa.Column("created_at", _tz(), nullable=False, server_default=_now()),
        sa.Column("updated_at", _tz(), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        _user_fk("jobs"),
        sa.UniqueConstraint("user_id", "slug", name="uq_jobs_user_id_slug"),
        sa.CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="ck_jobs_slug_format"),
        sa.CheckConstraint("fit IS NULL OR (fit >= 0 AND fit <= 100)", name="ck_jobs_fit_range"),
        sa.CheckConstraint("url IS NULL OR url <> ''", name="ck_jobs_url_not_empty"),
        sa.CheckConstraint("jsonb_typeof(legacy) = 'object'", name="ck_jobs_legacy_is_object"),
    )
    op.create_index(
        "ix_jobs_user_id_url",
        "jobs",
        ["user_id", "url"],
        unique=True,
        postgresql_where=sa.text("url IS NOT NULL"),
    )
    op.create_index("ix_jobs_user_id_status", "jobs", ["user_id", "status"])
    op.create_index("ix_jobs_user_id_found_on", "jobs", ["user_id", sa.text("found_on DESC")])
    op.create_index("ix_jobs_user_id_fit", "jobs", ["user_id", "fit"])
    op.create_index("ix_jobs_user_id_company", "jobs", ["user_id", "company"])
    op.create_index("ix_jobs_search", "jobs", ["search"], postgresql_using="gin")

    op.create_table(
        "job_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True),  # bigserial
        sa.Column("job_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("at", _tz(), nullable=False, server_default=_now()),
        sa.Column("kind", _enum("job_event_kind"), nullable=False),
        sa.Column("data", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("id", name="pk_job_events"),
        _job_fk("job_events"),
        _user_fk("job_events"),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="ck_job_events_data_is_object"),
    )
    op.create_index("ix_job_events_job_id_at", "job_events", ["job_id", "at"])
    op.create_index("ix_job_events_user_id", "job_events", ["user_id"])

    op.create_table(
        "job_files",
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("bytes", pg.BYTEA(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", pg.BYTEA(), nullable=False),
        sa.Column("origin", _enum("file_origin"), nullable=False),
        sa.Column("created_at", _tz(), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_job_files"),
        _job_fk("job_files"),
        sa.CheckConstraint("size = octet_length(bytes)", name="ck_job_files_size_matches_bytes"),
        sa.CheckConstraint("octet_length(sha256) = 32", name="ck_job_files_sha256_length"),
        sa.CheckConstraint("name <> ''", name="ck_job_files_name_not_empty"),
        sa.CheckConstraint("mime <> ''", name="ck_job_files_mime_not_empty"),
    )
    op.create_index("ix_job_files_job_id", "job_files", ["job_id"])

    op.create_table(
        "sweeps",
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", _tz(), nullable=False, server_default=_now()),
        sa.Column("finished_at", _tz(), nullable=True),
        sa.Column("trigger", _enum("sweep_trigger"), nullable=False),
        sa.Column("status", _enum("sweep_status"), nullable=False, server_default="running"),
        sa.Column("stats", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sweeps"),
        _user_fk("sweeps"),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="ck_sweeps_finished_unless_running",
        ),
        sa.CheckConstraint("jsonb_typeof(stats) = 'object'", name="ck_sweeps_stats_is_object"),
    )
    op.create_index(
        "ix_sweeps_one_running_per_user",
        "sweeps",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "ix_sweeps_user_id_started_at", "sweeps", ["user_id", sa.text("started_at DESC")]
    )


def downgrade() -> None:
    # Dropping a table drops its indexes and constraints; children before parents.
    op.drop_table("sweeps")
    op.drop_table("job_files")
    op.drop_table("job_events")
    op.drop_table("jobs")
    for name in reversed(ENUMS):
        op.execute(f"DROP TYPE {name}")
