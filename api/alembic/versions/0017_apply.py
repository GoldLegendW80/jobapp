"""Apply A2: the tables of the apply module, and those of "Complete my profile on this site".

`applications` (one per application, at most one live per posting and per form address of a
person), `application_answers`, `application_events`, `application_screenshots`,
`apply_answer_bank`, `apply_authorisations`, and the three tables of A18a and A18b
(`site_profile_runs`, `site_profile_fields`, `site_profile_shots`), created here so the module's
migration stays one (app/apply/models.py says why each column and constraint exists).

Every child points at its parent through `(<parent>_id, user_id)` against the parent's
`(id, user_id)`, and an application at its posting through `(posting_id, user_id)` against
`jobs(id, user_id)`, all `ON DELETE CASCADE`: deleting a person or a posting removes everything
of theirs here.

Expand only: new types and new tables. The release before this one reads and writes none of it
(CONTRIBUTING.md, Migrations).

Written by hand to match app/apply/models.py (the tests run `alembic check` against it). Every
statement is plain DDL so `alembic upgrade head --sql` works without a database.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-14
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHA256_BYTES = 32
SCREENSHOT_MAX_BYTES = 2 * 1024 * 1024
LIVE = "state NOT IN ('failed', 'rehearsed', 'cancelled', 'blocked', 'posting_closed')"

ENUMS = {
    "application_mode": ("manual", "review", "full_auto"),
    "application_state": (
        "queued",
        "filling",
        "needs_person",
        "ready",
        "approved",
        "held",
        "submitting",
        "submitted",
        "submit_unknown",
        "refused_by_site",
        "failed",
        "rehearsed",
        "cancelled",
        "blocked",
        "posting_closed",
    ),
    "application_answer_source": (
        "identity",
        "document",
        "answers",
        "bank",
        "agent",
        "person",
        "left",
    ),
    "application_shot_kind": ("before", "after", "step", "error"),
    "apply_authorisation_kind": ("person_approval", "full_auto_like", "profile_section"),
    "site_profile_kind": ("workday", "linkedin", "generic"),
    "site_profile_run_state": (
        "queued",
        "filling",
        "needs_person",
        "ready",
        "saving",
        "done",
        "cancelled",
        "failed",
    ),
    "site_profile_field_source": (
        "identity",
        "answers",
        "experience",
        "education",
        "skills",
        "document",
        "agent",
        "person",
        "left",
    ),
    "site_profile_shot_kind": ("before", "after", "step"),
}


def _enum(name: str) -> pg.ENUM:
    return pg.ENUM(*ENUMS[name], name=name, create_type=False)


def _uuid(name: str, **more: Any) -> sa.Column[Any]:
    return sa.Column(name, pg.UUID(as_uuid=True), **more)


def _uuid_pk() -> sa.Column[Any]:
    return _uuid("id", server_default=sa.text("gen_random_uuid()"), nullable=False)


def _at(name: str, nullable: bool = True, now: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if now else None,
    )


def _object(name: str) -> sa.Column[Any]:
    return sa.Column(name, pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb"))


def _flag(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())


def _count(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0"))


def _user_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["user_id"], ["users.id"], name=op.f(f"fk_{table}_user_id_users"), ondelete="CASCADE"
    )


def _owned_by(table: str, column: str, parent: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column, "user_id"],
        [f"{parent}.id", f"{parent}.user_id"],
        name=f"fk_{table}_{column}_{parent}",
        ondelete="CASCADE",
    )


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _image(table: str) -> tuple[sa.CheckConstraint, ...]:
    return (
        _check(table, "jpeg_size", f"octet_length(jpeg) BETWEEN 1 AND {SCREENSHOT_MAX_BYTES}"),
        _check(table, "sha256_length", f"octet_length(sha256) = {SHA256_BYTES}"),
    )


def upgrade() -> None:
    for name, labels in ENUMS.items():
        quoted = ", ".join(f"'{label}'" for label in labels)
        op.execute(f"CREATE TYPE {name} AS ENUM ({quoted})")

    t = "applications"
    op.create_table(
        t,
        _uuid_pk(),
        _uuid("user_id", nullable=False),
        _uuid("posting_id", nullable=False),
        sa.Column("mode", _enum("application_mode"), nullable=False),
        _flag("dry_run"),
        sa.Column("state", _enum("application_state"), nullable=False, server_default="queued"),
        sa.Column("ats", sa.Text(), nullable=False),
        sa.Column("form_url", sa.Text(), nullable=False),
        sa.Column("canonical_key", sa.Text(), nullable=False),
        _object("document_versions"),
        sa.Column("answers_hash", pg.BYTEA(), nullable=True),
        _count("attempt"),
        sa.Column("needs", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        _object("cost"),
        _at("created_at", nullable=False, now=True),
        _at("updated_at", nullable=False, now=True),
        _at("submitted_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
        _user_fk(t),
        sa.UniqueConstraint("id", "user_id", name="uq_applications_id_user_id"),
        sa.ForeignKeyConstraint(
            ["posting_id", "user_id"],
            ["jobs.id", "jobs.user_id"],
            name="fk_applications_posting_id_jobs",
            ondelete="CASCADE",
        ),
        _check(t, "manual_is_never_dry", "NOT (dry_run AND mode = 'manual')"),
        _check(t, "attempt_not_negative", "attempt >= 0"),
        _check(t, "ats_not_empty", "ats <> ''"),
        _check(t, "form_url_not_empty", "form_url <> ''"),
        _check(t, "canonical_key_not_empty", "canonical_key <> ''"),
        _check(
            t,
            "answers_hash_length",
            f"answers_hash IS NULL OR octet_length(answers_hash) = {SHA256_BYTES}",
        ),
        _check(
            t,
            "submitted_at_when_clicked",
            "state NOT IN ('submitted', 'submit_unknown') OR submitted_at IS NOT NULL",
        ),
        _check(t, "document_versions_is_object", "jsonb_typeof(document_versions) = 'object'"),
        _check(t, "cost_is_object", "jsonb_typeof(cost) = 'object'"),
    )
    op.create_index(
        "uq_applications_live_posting", t, ["posting_id"], unique=True, postgresql_where=LIVE
    )
    op.create_index(
        "uq_applications_live_form",
        t,
        ["user_id", "canonical_key"],
        unique=True,
        postgresql_where=LIVE,
    )
    op.create_index(
        "ix_applications_user_id_created_at", t, ["user_id", sa.text("created_at DESC")]
    )
    op.create_index("ix_applications_user_id_canonical_key", t, ["user_id", "canonical_key"])
    op.create_index("ix_applications_posting_id", t, ["posting_id"])

    t = "application_answers"
    op.create_table(
        t,
        _uuid("application_id", nullable=False),
        _uuid("user_id", nullable=False),
        sa.Column("field_key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", pg.JSONB(), nullable=True),
        sa.Column("source", _enum("application_answer_source"), nullable=False),
        _flag("required"),
        _flag("approved"),
        sa.PrimaryKeyConstraint("application_id", "field_key", name=op.f("pk_application_answers")),
        _user_fk(t),
        _owned_by(t, "application_id", "applications"),
        _check(t, "field_key_not_empty", "field_key <> ''"),
    )
    op.create_index("ix_application_answers_user_id", t, ["user_id"])

    t = "application_events"
    op.create_table(
        t,
        sa.Column("id", sa.BigInteger(), autoincrement=True),  # bigserial
        _uuid("application_id", nullable=False),
        _uuid("user_id", nullable=False),
        _at("at", nullable=False, now=True),
        sa.Column("kind", sa.Text(), nullable=False),
        _object("data"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_events")),
        _user_fk(t),
        _owned_by(t, "application_id", "applications"),
        _check(t, "data_is_object", "jsonb_typeof(data) = 'object'"),
        _check(t, "kind_not_empty", "kind <> ''"),
    )
    op.create_index("ix_application_events_application_id_at", t, ["application_id", "at"])
    op.create_index("ix_application_events_user_id", t, ["user_id"])

    t = "application_screenshots"
    op.create_table(
        t,
        _uuid_pk(),
        _uuid("application_id", nullable=False),
        _uuid("user_id", nullable=False),
        sa.Column("kind", _enum("application_shot_kind"), nullable=False),
        sa.Column("jpeg", pg.BYTEA(), nullable=False),
        sa.Column("sha256", pg.BYTEA(), nullable=False),
        _at("created_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_screenshots")),
        _user_fk(t),
        _owned_by(t, "application_id", "applications"),
        *_image(t),
    )
    op.create_index("ix_application_screenshots_application_id", t, ["application_id"])
    op.create_index("ix_application_screenshots_user_id", t, ["user_id"])

    t = "apply_answer_bank"
    op.create_table(
        t,
        _uuid("user_id", nullable=False),
        sa.Column("question_key", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", pg.JSONB(), nullable=False),
        _at("updated_at", nullable=False, now=True),
        _count("used_count"),
        sa.PrimaryKeyConstraint("user_id", "question_key", name=op.f("pk_apply_answer_bank")),
        _user_fk(t),
        _check(t, "question_key_length", "char_length(question_key) BETWEEN 1 AND 200"),
        _check(t, "used_count_not_negative", "used_count >= 0"),
    )

    t = "site_profile_runs"
    op.create_table(
        t,
        _uuid_pk(),
        _uuid("user_id", nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("site_kind", _enum("site_profile_kind"), nullable=False),
        sa.Column(
            "state", _enum("site_profile_run_state"), nullable=False, server_default="queued"
        ),
        _flag("autosave"),
        _object("cost"),
        _at("created_at", nullable=False, now=True),
        _at("updated_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_profile_runs")),
        _user_fk(t),
        sa.UniqueConstraint("id", "user_id", name="uq_site_profile_runs_id_user_id"),
        _check(t, "host_not_empty", "host <> ''"),
        _check(t, "cost_is_object", "jsonb_typeof(cost) = 'object'"),
    )
    op.create_index(
        "ix_site_profile_runs_user_id_created_at", t, ["user_id", sa.text("created_at DESC")]
    )

    t = "apply_authorisations"
    op.create_table(
        t,
        _uuid_pk(),
        _uuid("user_id", nullable=False),
        _uuid("application_id", nullable=True),
        _uuid("site_run_id", nullable=True),
        sa.Column("kind", _enum("apply_authorisation_kind"), nullable=False),
        sa.Column("answers_hash", pg.BYTEA(), nullable=False),
        _at("created_at", nullable=False, now=True),
        _at("expires_at", nullable=False),
        _at("used_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_apply_authorisations")),
        _user_fk(t),
        _owned_by(t, "application_id", "applications"),
        _owned_by(t, "site_run_id", "site_profile_runs"),
        _check(t, "one_subject", "(application_id IS NULL) <> (site_run_id IS NULL)"),
        _check(t, "kind_matches_subject", "(kind = 'profile_section') = (site_run_id IS NOT NULL)"),
        _check(t, "hash_length", f"octet_length(answers_hash) = {SHA256_BYTES}"),
        _check(t, "expires_after_creation", "expires_at > created_at"),
        _check(t, "used_after_creation", "used_at IS NULL OR used_at >= created_at"),
    )
    op.create_index("ix_apply_authorisations_application_id", t, ["application_id"])
    op.create_index("ix_apply_authorisations_site_run_id", t, ["site_run_id"])
    op.create_index("ix_apply_authorisations_user_id", t, ["user_id"])

    t = "site_profile_fields"
    op.create_table(
        t,
        _uuid("run_id", nullable=False),
        _uuid("user_id", nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("field_key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", pg.JSONB(), nullable=True),
        sa.Column("current_value", pg.JSONB(), nullable=True),
        sa.Column("source", _enum("site_profile_field_source"), nullable=False),
        _flag("replace"),
        _flag("approved"),
        sa.PrimaryKeyConstraint(
            "run_id", "section", "field_key", name=op.f("pk_site_profile_fields")
        ),
        _user_fk(t),
        _owned_by(t, "run_id", "site_profile_runs"),
        _check(t, "field_key_not_empty", "field_key <> ''"),
    )
    op.create_index("ix_site_profile_fields_user_id", t, ["user_id"])

    t = "site_profile_shots"
    op.create_table(
        t,
        _uuid_pk(),
        _uuid("run_id", nullable=False),
        _uuid("user_id", nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("kind", _enum("site_profile_shot_kind"), nullable=False),
        sa.Column("jpeg", pg.BYTEA(), nullable=False),
        sa.Column("sha256", pg.BYTEA(), nullable=False),
        _at("created_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_profile_shots")),
        _user_fk(t),
        _owned_by(t, "run_id", "site_profile_runs"),
        *_image(t),
    )
    op.create_index("ix_site_profile_shots_run_id", t, ["run_id"])
    op.create_index("ix_site_profile_shots_user_id", t, ["user_id"])


def downgrade() -> None:
    for table in (
        "site_profile_shots",
        "site_profile_fields",
        "apply_authorisations",
        "site_profile_runs",
        "apply_answer_bank",
        "application_screenshots",
        "application_events",
        "application_answers",
        "applications",
    ):
        op.drop_table(table)
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE {name}")
