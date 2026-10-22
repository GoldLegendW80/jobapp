"""Tables of the apply module (A2, migration 0017; SPEC-apply.md).

- `applications`: one application of a person to one of their postings. It points at its posting
  through `(posting_id, user_id)`, so it can never name another person's posting, and goes with
  it. At most one live application per posting and one per canonical form address of a person
  (two partial unique indexes; "live" is every state but the ends in `states.ENDED`).
  `submitted_at` is the time of the app's click on Submit: set in `submitted` and
  `submit_unknown`, and kept when "Try again" sends a `submit_unknown` back to `ready`. The 180
  day rule on the same role reads it. A rehearsal (`dry_run`) is never a manual application.
- `application_answers`: the fields of the filled form, with the value, its source and whether
  it is required and approved.
- `application_events`: the timeline of an application: ids, codes and counts only, never an
  answer, typed text or image (the run report reads them).
- `application_screenshots`: JPEG images, 2 MB at most each, with their SHA-256.
- `apply_answer_bank`: a person's own answers by question key (app/apply/bank.py).
- `apply_authorisations`: what lets the gate click: a person's approval or a Like in Full auto,
  bound to the hash of the answers and documents it covered; or the approval of one section of
  "Complete my profile on this site". Exactly one of `application_id` and `site_run_id` is set,
  the second when the kind is `profile_section`.
- `site_profile_runs`, `site_profile_fields`, `site_profile_shots`: "Complete my profile on this
  site" (A18a, A18b), created here so the module's migration stays one.

Ownership is in the schema, as in the documents module: every row carries `user_id`, and every
child points at its parent through `(<parent>_id, user_id)` against the parent's `(id, user_id)`,
so no row belongs to one person and hangs under another's. Everything goes with its user, an
application with its posting, and every child with its parent.
Migration: alembic/versions/0017_apply.py, which must stay in step with this file (`alembic
check` in the tests compares them).
"""

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models_base import Base

SHA256_BYTES = 32
SCREENSHOT_MAX_BYTES = 2 * 1024 * 1024
QUESTION_KEY_MAX = 200


class Mode(enum.StrEnum):
    manual = "manual"
    review = "review"
    full_auto = "full_auto"


class State(enum.StrEnum):
    """SPEC-apply, "States". The allowed moves are in app/apply/states.py."""

    queued = "queued"
    filling = "filling"
    needs_person = "needs_person"
    ready = "ready"
    approved = "approved"
    held = "held"
    submitting = "submitting"
    submitted = "submitted"
    submit_unknown = "submit_unknown"
    refused_by_site = "refused_by_site"
    failed = "failed"
    rehearsed = "rehearsed"
    cancelled = "cancelled"
    blocked = "blocked"
    posting_closed = "posting_closed"


# The states in which an application is over and no longer holds its posting: a new one may
# start. `submitted` and `submit_unknown` are over too, but they hold the posting for good.
ENDED = (
    State.failed,
    State.rehearsed,
    State.cancelled,
    State.blocked,
    State.posting_closed,
)
_LIVE = "state NOT IN (" + ", ".join(f"'{state.value}'" for state in ENDED) + ")"


class AnswerSource(enum.StrEnum):
    identity = "identity"
    document = "document"
    answers = "answers"
    bank = "bank"
    agent = "agent"
    person = "person"
    left = "left"


class ShotKind(enum.StrEnum):
    before = "before"
    after = "after"
    step = "step"
    error = "error"


class AuthorisationKind(enum.StrEnum):
    person_approval = "person_approval"
    full_auto_like = "full_auto_like"
    profile_section = "profile_section"


class SiteKind(enum.StrEnum):
    workday = "workday"
    linkedin = "linkedin"
    generic = "generic"


class SiteRunState(enum.StrEnum):
    queued = "queued"
    filling = "filling"
    needs_person = "needs_person"
    ready = "ready"
    saving = "saving"
    done = "done"
    cancelled = "cancelled"
    failed = "failed"


class SiteFieldSource(enum.StrEnum):
    identity = "identity"
    answers = "answers"
    experience = "experience"
    education = "education"
    skills = "skills"
    document = "document"
    agent = "agent"
    person = "person"
    left = "left"


class SiteShotKind(enum.StrEnum):
    before = "before"
    after = "after"
    step = "step"


def _enum(cls: type[enum.Enum], name: str) -> sa.Enum:
    # Postgres enum named explicitly; the stored labels are the values, not the names.
    return sa.Enum(cls, name=name, values_callable=lambda members: [m.value for m in members])


MODE_ENUM = _enum(Mode, "application_mode")
STATE_ENUM = _enum(State, "application_state")
ANSWER_SOURCE_ENUM = _enum(AnswerSource, "application_answer_source")
SHOT_KIND_ENUM = _enum(ShotKind, "application_shot_kind")
AUTHORISATION_KIND_ENUM = _enum(AuthorisationKind, "apply_authorisation_kind")
SITE_KIND_ENUM = _enum(SiteKind, "site_profile_kind")
SITE_RUN_STATE_ENUM = _enum(SiteRunState, "site_profile_run_state")
SITE_FIELD_SOURCE_ENUM = _enum(SiteFieldSource, "site_profile_field_source")
SITE_SHOT_KIND_ENUM = _enum(SiteShotKind, "site_profile_shot_kind")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        pg.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )


def _user_fk(primary_key: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=primary_key,
        nullable=False,
    )


def _now() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _object() -> Mapped[dict[str, Any]]:
    return mapped_column(
        pg.JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )


def _flag() -> Mapped[bool]:
    return mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.false())


def _owned_by(table: str, column: str, parent: str) -> sa.ForeignKeyConstraint:
    """`(column, user_id)` points at `parent(id, user_id)`: same owner, and it goes with it."""
    return sa.ForeignKeyConstraint(
        [column, "user_id"],
        [f"{parent}.id", f"{parent}.user_id"],
        name=f"fk_{table}_{column}_{parent}",
        ondelete="CASCADE",
    )


def _image_checks() -> tuple[sa.CheckConstraint, ...]:
    return (
        sa.CheckConstraint(
            f"octet_length(jpeg) BETWEEN 1 AND {SCREENSHOT_MAX_BYTES}", name="jpeg_size"
        ),
        sa.CheckConstraint(f"octet_length(sha256) = {SHA256_BYTES}", name="sha256_length"),
    )


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        sa.UniqueConstraint("id", "user_id", name="uq_applications_id_user_id"),
        sa.ForeignKeyConstraint(
            ["posting_id", "user_id"],
            ["jobs.id", "jobs.user_id"],
            name="fk_applications_posting_id_jobs",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("NOT (dry_run AND mode = 'manual')", name="manual_is_never_dry"),
        sa.CheckConstraint("attempt >= 0", name="attempt_not_negative"),
        sa.CheckConstraint("ats <> ''", name="ats_not_empty"),
        sa.CheckConstraint("form_url <> ''", name="form_url_not_empty"),
        sa.CheckConstraint("canonical_key <> ''", name="canonical_key_not_empty"),
        sa.CheckConstraint(
            f"answers_hash IS NULL OR octet_length(answers_hash) = {SHA256_BYTES}",
            name="answers_hash_length",
        ),
        sa.CheckConstraint(
            "state NOT IN ('submitted', 'submit_unknown') OR submitted_at IS NOT NULL",
            name="submitted_at_when_clicked",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(document_versions) = 'object'", name="document_versions_is_object"
        ),
        sa.CheckConstraint("jsonb_typeof(cost) = 'object'", name="cost_is_object"),
        sa.Index(
            "uq_applications_live_posting",
            "posting_id",
            unique=True,
            postgresql_where=sa.text(_LIVE),
        ),
        sa.Index(
            "uq_applications_live_form",
            "user_id",
            "canonical_key",
            unique=True,
            postgresql_where=sa.text(_LIVE),
        ),
        sa.Index("ix_applications_user_id_created_at", "user_id", sa.text("created_at DESC")),
        sa.Index("ix_applications_user_id_canonical_key", "user_id", "canonical_key"),
        sa.Index("ix_applications_posting_id", "posting_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    posting_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    mode: Mapped[Mode] = mapped_column(MODE_ENUM, nullable=False)
    dry_run: Mapped[bool] = _flag()
    state: Mapped[State] = mapped_column(
        STATE_ENUM, nullable=False, default=State.queued, server_default=State.queued.value
    )
    ats: Mapped[str] = mapped_column(sa.Text, nullable=False)
    form_url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    canonical_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    document_versions: Mapped[dict[str, Any]] = _object()
    answers_hash: Mapped[bytes | None] = mapped_column(pg.BYTEA)
    mail_send_id: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    attempt: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    needs: Mapped[str | None] = mapped_column(sa.Text)
    error_code: Mapped[str | None] = mapped_column(sa.Text)
    cost: Mapped[dict[str, Any]] = _object()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    submitted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class ApplicationAnswer(Base):
    """One field of the filled form. `value` is null for a field left empty."""

    __tablename__ = "application_answers"
    __table_args__ = (
        _owned_by("application_answers", "application_id", "applications"),
        sa.CheckConstraint("field_key <> ''", name="field_key_not_empty"),
        sa.Index("ix_application_answers_user_id", "user_id"),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    field_key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    label: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    value: Mapped[Any] = mapped_column(pg.JSONB, nullable=True)
    # The labels the form offers for a select, combobox or single choice (null when it offers
    # none, or none were read): what `set_answer` checks a choice against, and the approval view
    # shows.
    options: Mapped[list[str] | None] = mapped_column(pg.JSONB, nullable=True)
    source: Mapped[AnswerSource] = mapped_column(ANSWER_SOURCE_ENUM, nullable=False)
    required: Mapped[bool] = _flag()
    approved: Mapped[bool] = _flag()


class ApplicationEvent(Base):
    """One step of an application: ids, codes and counts only in `data`."""

    __tablename__ = "application_events"
    __table_args__ = (
        _owned_by("application_events", "application_id", "applications"),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="data_is_object"),
        sa.CheckConstraint("kind <> ''", name="kind_not_empty"),
        sa.Index("ix_application_events_application_id_at", "application_id", "at"),
        sa.Index("ix_application_events_user_id", "user_id"),
        # the clicks the pace per ATS host reads on every tick (`caps.clicks_since`)
        sa.Index(
            "ix_application_events_click_at", "at", postgresql_where=sa.text("kind = 'click'")
        ),
    )

    # bigserial
    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    application_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = _user_fk()
    at: Mapped[datetime] = _now()
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    data: Mapped[dict[str, Any]] = _object()


class ApplicationScreenshot(Base):
    __tablename__ = "application_screenshots"
    __table_args__ = (
        _owned_by("application_screenshots", "application_id", "applications"),
        *_image_checks(),
        sa.Index("ix_application_screenshots_application_id", "application_id"),
        sa.Index("ix_application_screenshots_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    application_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = _user_fk()
    kind: Mapped[ShotKind] = mapped_column(SHOT_KIND_ENUM, nullable=False)
    jpeg: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False, deferred=True)
    sha256: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False)
    created_at: Mapped[datetime] = _now()


class AnswerBankEntry(Base):
    __tablename__ = "apply_answer_bank"
    __table_args__ = (
        sa.CheckConstraint(
            f"char_length(question_key) BETWEEN 1 AND {QUESTION_KEY_MAX}",
            name="question_key_length",
        ),
        sa.CheckConstraint("used_count >= 0", name="used_count_not_negative"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk(primary_key=True)
    question_key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    question: Mapped[str] = mapped_column(sa.Text, nullable=False)
    answer: Mapped[Any] = mapped_column(pg.JSONB, nullable=False)
    updated_at: Mapped[datetime] = _now()
    used_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )


class Authorisation(Base):
    __tablename__ = "apply_authorisations"
    __table_args__ = (
        _owned_by("apply_authorisations", "application_id", "applications"),
        _owned_by("apply_authorisations", "site_run_id", "site_profile_runs"),
        sa.CheckConstraint("(application_id IS NULL) <> (site_run_id IS NULL)", name="one_subject"),
        sa.CheckConstraint(
            "(kind = 'profile_section') = (site_run_id IS NOT NULL)", name="kind_matches_subject"
        ),
        sa.CheckConstraint(f"octet_length(answers_hash) = {SHA256_BYTES}", name="hash_length"),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        sa.CheckConstraint("used_at IS NULL OR used_at >= created_at", name="used_after_creation"),
        sa.Index("ix_apply_authorisations_application_id", "application_id"),
        sa.Index("ix_apply_authorisations_site_run_id", "site_run_id"),
        sa.Index("ix_apply_authorisations_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    application_id: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    site_run_id: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    kind: Mapped[AuthorisationKind] = mapped_column(AUTHORISATION_KIND_ENUM, nullable=False)
    answers_hash: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class SiteProfileRun(Base):
    __tablename__ = "site_profile_runs"
    __table_args__ = (
        sa.UniqueConstraint("id", "user_id", name="uq_site_profile_runs_id_user_id"),
        sa.CheckConstraint("host <> ''", name="host_not_empty"),
        sa.CheckConstraint("jsonb_typeof(cost) = 'object'", name="cost_is_object"),
        sa.Index("ix_site_profile_runs_user_id_created_at", "user_id", sa.text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    host: Mapped[str] = mapped_column(sa.Text, nullable=False)
    site_kind: Mapped[SiteKind] = mapped_column(SITE_KIND_ENUM, nullable=False)
    state: Mapped[SiteRunState] = mapped_column(
        SITE_RUN_STATE_ENUM,
        nullable=False,
        default=SiteRunState.queued,
        server_default=SiteRunState.queued.value,
    )
    url: Mapped[str] = mapped_column(sa.Text, nullable=False, default="", server_default="")
    needs: Mapped[str | None] = mapped_column(sa.Text)
    error_code: Mapped[str | None] = mapped_column(sa.Text)
    autosave_confirmed: Mapped[bool] = _flag()
    autosave: Mapped[bool] = _flag()
    cost: Mapped[dict[str, Any]] = _object()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class SiteProfileField(Base):
    __tablename__ = "site_profile_fields"
    __table_args__ = (
        _owned_by("site_profile_fields", "run_id", "site_profile_runs"),
        sa.CheckConstraint("field_key <> ''", name="field_key_not_empty"),
        sa.Index("ix_site_profile_fields_user_id", "user_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    section: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    field_key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    label: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_path: Mapped[str] = mapped_column(sa.Text, nullable=False, default="", server_default="")
    required: Mapped[bool] = _flag()
    refusal: Mapped[str | None] = mapped_column(sa.Text)
    options: Mapped[list[str]] = mapped_column(
        pg.JSONB, nullable=False, default=list, server_default="[]"
    )
    value: Mapped[Any] = mapped_column(pg.JSONB, nullable=True)
    current_value: Mapped[Any] = mapped_column(pg.JSONB, nullable=True)
    source: Mapped[SiteFieldSource] = mapped_column(SITE_FIELD_SOURCE_ENUM, nullable=False)
    replace: Mapped[bool] = _flag()
    approved: Mapped[bool] = _flag()


class SiteProfileShot(Base):
    __tablename__ = "site_profile_shots"
    __table_args__ = (
        _owned_by("site_profile_shots", "run_id", "site_profile_runs"),
        *_image_checks(),
        sa.Index("ix_site_profile_shots_run_id", "run_id"),
        sa.Index("ix_site_profile_shots_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = _user_fk()
    section: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[SiteShotKind] = mapped_column(SITE_SHOT_KIND_ENUM, nullable=False)
    jpeg: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False, deferred=True)
    sha256: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False)
    created_at: Mapped[datetime] = _now()


class SiteProfileSection(Base):
    """A resumable section. Events contain codes, ids and counts, never field values."""

    __tablename__ = "site_profile_sections"
    __table_args__ = (
        _owned_by("site_profile_sections", "run_id", "site_profile_runs"),
        sa.CheckConstraint(
            "state IN ('ready', 'approved', 'saving', 'saved', 'skipped', 'left')",
            name="valid_state",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    section: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    label: Mapped[str] = mapped_column(sa.Text, nullable=False)
    save_name: Mapped[str] = mapped_column(sa.Text, nullable=False, default="", server_default="")
    state: Mapped[str] = mapped_column(
        sa.Text, nullable=False, default="ready", server_default="ready"
    )
    error_code: Mapped[str | None] = mapped_column(sa.Text)
    events: Mapped[list[dict[str, Any]]] = mapped_column(
        pg.JSONB, nullable=False, default=list, server_default="[]"
    )
    updated_at: Mapped[datetime] = _now()
