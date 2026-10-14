"""Tables of the documents module (D5, migration 0015; SPEC-documents.md, "Data model").

- `documents`: one CV or letter of a person. A base CV (`scope = base`) has a name, a source and
  maybe the default mark (one default per person, a partial unique index); a posting's document
  (`scope = posting`) points at its posting through `(posting_id, user_id)`, so it can never
  point at another person's posting, and there is one of each kind per posting (a partial
  unique index). The draft is the one mutable copy, saved against `draft_revision`.
- `document_versions`: immutable snapshots of a draft, numbered per document. A frozen version
  (sent with an application) is protected by a trigger of 0015: its content, PDF and fit can
  never change, `frozen_at` never clears, and it is deleted only with its document. The PDF
  bytes live in the row and are deferred, so no list loads them.
- `document_generations`: one writer run on a document; at most one waiting, queued or running
  per document (a partial unique index).
- `document_messages`: the chat of a document, per channel.
- `document_consents`, `document_settings`: the consent to the writers' use of the profile and
  the person's writing rules (`settings.DocumentSettings`, defaults when the row is missing).
- `document_user_templates`: the templates captured from an uploaded CV (`upload-<8>`).

Ownership is in the schema: every row carries `user_id`, and a version, a generation and a
message point at their document through `(document_id, user_id)`, so none can belong to one
person and hang under another's document. Every other pointer between two rows is two columns
as well (`_same_owner`): a base CV, a generation or an upload named by another person's row is
a foreign key violation. The legacy file of a version (`job_files` has no `user_id`) is checked
by a trigger of 0015: it must be one of the files of the version's own posting. Everything goes
with its user: `users` cascades to
documents, consents, settings and user templates, a document to its versions, generations and
messages, a posting to its documents. Deleting a base CV sets `base_document_id` to null on
what was written from it; deleting an uploaded file (`ingestion_documents`) or an agent run
sets the pointer to null and keeps the row.
"""

import enum
import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models_base import Base


class Scope(enum.StrEnum):
    base = "base"
    posting = "posting"


class BaseSource(enum.StrEnum):
    profile = "profile"
    built = "built"
    uploaded = "uploaded"


class Kind(enum.StrEnum):
    cv = "cv"
    letter = "letter"


class Language(enum.StrEnum):
    en = "en"
    fr = "fr"


class Author(enum.StrEnum):
    user = "user"
    writer = "writer"
    chat = "chat"
    correction = "correction"
    restore = "restore"
    import_ = "import"
    upload = "upload"


class RenderState(enum.StrEnum):
    idle = "idle"
    queued = "queued"
    rendering = "rendering"


class Origin(enum.StrEnum):
    generated = "generated"
    edited = "edited"
    chat = "chat"
    restored = "restored"
    template = "template"
    uploaded = "uploaded"
    legacy = "legacy"


class FitResult(enum.StrEnum):
    one_page = "one_page"
    overflow = "overflow"


class FreezeReason(enum.StrEnum):
    applied = "applied"
    marked_sent = "marked_sent"
    apply = "apply"
    legacy_applied = "legacy_applied"


class GenerationMode(enum.StrEnum):
    base_upload = "base_upload"
    base_profile = "base_profile"
    posting = "posting"
    chat = "chat"
    shorten = "shorten"
    template_capture = "template_capture"
    template_fix = "template_fix"


class GenerationTrigger(enum.StrEnum):
    auto = "auto"
    click = "click"
    chat = "chat"


class GenerationStatus(enum.StrEnum):
    waiting = "waiting"
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class GenerationWait(enum.StrEnum):
    daily_ceiling = "daily_ceiling"
    consent = "consent"
    credential = "credential"
    base_cv = "base_cv"


class GenerationOutcome(enum.StrEnum):
    draft_replaced = "draft_replaced"
    kept_as_version = "kept_as_version"


class Channel(enum.StrEnum):
    content = "content"
    design = "design"


class Role(enum.StrEnum):
    user = "user"
    assistant = "assistant"


OPEN_GENERATION = (GenerationStatus.waiting, GenerationStatus.queued, GenerationStatus.running)
SHA256_BYTES = 32


def _enum(kind: type[enum.StrEnum], name: str) -> sa.Enum:
    return sa.Enum(kind, name=name, values_callable=lambda members: [m.value for m in members])


SCOPE_ENUM = _enum(Scope, "document_scope")
BASE_SOURCE_ENUM = _enum(BaseSource, "base_source")
KIND_ENUM = _enum(Kind, "document_kind")
LANGUAGE_ENUM = _enum(Language, "document_language")
AUTHOR_ENUM = _enum(Author, "document_author")
RENDER_STATE_ENUM = _enum(RenderState, "render_state")
ORIGIN_ENUM = _enum(Origin, "version_origin")
FIT_ENUM = _enum(FitResult, "fit_result")
FREEZE_REASON_ENUM = _enum(FreezeReason, "freeze_reason")
MODE_ENUM = _enum(GenerationMode, "generation_mode")
TRIGGER_ENUM = _enum(GenerationTrigger, "generation_trigger")
STATUS_ENUM = _enum(GenerationStatus, "generation_status")
WAIT_ENUM = _enum(GenerationWait, "generation_wait")
OUTCOME_ENUM = _enum(GenerationOutcome, "generation_outcome")
CHANNEL_ENUM = _enum(Channel, "message_channel")
ROLE_ENUM = _enum(Role, "message_role")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        pg.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )


def _uuid(nullable: bool = False) -> Mapped[Any]:
    return mapped_column(pg.UUID(as_uuid=True), nullable=nullable)


def _user_fk(primary_key: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=primary_key,
        nullable=False,
    )


def _set_null_fk(target: str) -> Mapped[uuid.UUID | None]:
    return mapped_column(pg.UUID(as_uuid=True), sa.ForeignKey(target, ondelete="SET NULL"))


def _now() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _later() -> Mapped[datetime | None]:
    return mapped_column(sa.DateTime(timezone=True))


def _same_owner(
    table: str, column: str, referred: str, name: str | None = None
) -> sa.ForeignKeyConstraint:
    """`(column, user_id)` points at `referred(id, user_id)`: the row and what it names always
    have the same owner. A delete sets `column` to null and leaves `user_id` alone."""
    return sa.ForeignKeyConstraint(
        [column, "user_id"],
        [f"{referred}.id", f"{referred}.user_id"],
        name=name or f"fk_{table}_{column}_{referred}",
        ondelete=f"SET NULL ({column})",
    )


def _owned_by_document(table: str) -> sa.ForeignKeyConstraint:
    """`(document_id, user_id)` points at `documents(id, user_id)`: the row and its document
    always have the same owner, and the row goes with the document."""
    return sa.ForeignKeyConstraint(
        ["document_id", "user_id"],
        ["documents.id", "documents.user_id"],
        name=f"fk_{table}_document_id_documents",
        ondelete="CASCADE",
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        sa.UniqueConstraint("id", "user_id", name="uq_documents_id_user_id"),
        sa.ForeignKeyConstraint(
            ["posting_id", "user_id"],
            ["jobs.id", "jobs.user_id"],
            name="fk_documents_posting_id_jobs",
            ondelete="CASCADE",
        ),
        _same_owner("documents", "base_document_id", "documents"),
        _same_owner("documents", "source_document_id", "ingestion_documents"),
        sa.CheckConstraint("(scope = 'base') = (posting_id IS NULL)", name="base_has_no_posting"),
        sa.CheckConstraint("(scope = 'base') = (name IS NOT NULL)", name="base_has_a_name"),
        sa.CheckConstraint(
            "scope = 'base' OR (is_default IS false AND base_source IS NULL)",
            name="posting_is_not_a_base",
        ),
        sa.CheckConstraint("scope = 'posting' OR kind = 'cv'", name="base_is_a_cv"),
        sa.CheckConstraint("scope = 'posting' OR base_source IS NOT NULL", name="base_has_source"),
        sa.CheckConstraint(
            "scope = 'base' OR kind = 'cv' OR base_document_id IS NULL", name="letter_has_no_base"
        ),
        sa.CheckConstraint(
            "name IS NULL OR (char_length(name) BETWEEN 1 AND 100)", name="name_length"
        ),
        sa.CheckConstraint("draft_revision >= 1", name="draft_revision_positive"),
        sa.CheckConstraint("char_length(template_slug) BETWEEN 1 AND 64", name="template_slug_set"),
        sa.Index("ix_documents_user_id", "user_id"),
        sa.Index("ix_documents_posting_id", "posting_id"),
        sa.Index("ix_documents_base_document_id", "base_document_id"),
        sa.Index("ix_documents_source_document_id", "source_document_id"),
        sa.Index(
            "uq_documents_user_id_default",
            "user_id",
            unique=True,
            postgresql_where=sa.text("scope = 'base' AND is_default"),
        ),
        sa.Index(
            "uq_documents_user_id_posting_id_kind",
            "user_id",
            "posting_id",
            "kind",
            unique=True,
            postgresql_where=sa.text("scope = 'posting'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    scope: Mapped[Scope] = mapped_column(SCOPE_ENUM, nullable=False)
    posting_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    name: Mapped[str | None] = mapped_column(sa.Text)
    is_default: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    base_source: Mapped[BaseSource | None] = mapped_column(BASE_SOURCE_ENUM)
    base_document_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    kind: Mapped[Kind] = mapped_column(KIND_ENUM, nullable=False)
    language: Mapped[Language] = mapped_column(LANGUAGE_ENUM, nullable=False)
    template_slug: Mapped[str] = mapped_column(sa.Text, nullable=False)
    draft: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False)
    draft_schema: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    draft_revision: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=1, server_default=sa.text("1")
    )
    draft_written_by: Mapped[Author] = mapped_column(AUTHOR_ENUM, nullable=False)
    draft_updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    checks: Mapped[list[dict[str, Any]] | None] = mapped_column(pg.JSONB)
    checks_revision: Mapped[int | None] = mapped_column(sa.Integer)
    render_state: Mapped[RenderState] = mapped_column(
        RENDER_STATE_ENUM,
        nullable=False,
        default=RenderState.idle,
        server_default=RenderState.idle.value,
    )
    render_revision: Mapped[int | None] = mapped_column(sa.Integer)
    render_error: Mapped[str | None] = mapped_column(sa.Text)  # a code of a fixed list
    source_document_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class DocumentGeneration(Base):
    __tablename__ = "document_generations"
    __table_args__ = (
        sa.UniqueConstraint("id", "user_id", name="uq_document_generations_id_user_id"),
        _owned_by_document("document_generations"),
        _same_owner("document_generations", "base_document_id", "documents"),
        sa.CheckConstraint("token_ceiling >= 0 AND corrections >= 0", name="counts_not_negative"),
        sa.Index("ix_document_generations_document_id", "document_id"),
        sa.Index("ix_document_generations_user_id_ceiling_day", "user_id", "ceiling_day"),
        sa.Index("ix_document_generations_run_id", "run_id"),
        sa.Index("ix_document_generations_base_document_id", "base_document_id"),
        sa.Index(
            "uq_document_generations_document_id_open",
            "document_id",
            unique=True,
            postgresql_where=sa.text("status IN ('waiting', 'queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _uuid()
    document_id: Mapped[uuid.UUID] = _uuid()
    run_id: Mapped[uuid.UUID | None] = _set_null_fk("agent_runs.id")
    mode: Mapped[GenerationMode] = mapped_column(MODE_ENUM, nullable=False)
    trigger: Mapped[GenerationTrigger] = mapped_column(TRIGGER_ENUM, nullable=False)
    message_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    base_draft_revision: Mapped[int | None] = mapped_column(sa.Integer)
    base_document_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    estimate: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False)
    token_ceiling: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[GenerationStatus] = mapped_column(STATUS_ENUM, nullable=False)
    wait_reason: Mapped[GenerationWait | None] = mapped_column(WAIT_ENUM)
    ceiling_day: Mapped[date | None] = mapped_column(sa.Date)
    outcome: Mapped[GenerationOutcome | None] = mapped_column(OUTCOME_ENUM)
    version_number: Mapped[int | None] = mapped_column(sa.Integer)
    corrections: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    error: Mapped[str | None] = mapped_column(sa.Text)  # a code of a fixed list
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = _later()
    finished_at: Mapped[datetime | None] = _later()


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        sa.UniqueConstraint("document_id", "number", name="uq_document_versions_document_id"),
        _owned_by_document("document_versions"),
        _same_owner("document_versions", "generation_id", "document_generations"),
        sa.CheckConstraint("(frozen_at IS NULL) = (frozen_reason IS NULL)", name="frozen_is_whole"),
        sa.CheckConstraint("pdf IS NULL OR legacy_file_id IS NULL", name="one_pdf_source"),
        sa.CheckConstraint("number >= 1", name="number_positive"),
        sa.CheckConstraint(
            f"octet_length(content_hash) = {SHA256_BYTES}", name="content_hash_length"
        ),
        sa.CheckConstraint(
            f"pdf_sha256 IS NULL OR octet_length(pdf_sha256) = {SHA256_BYTES}",
            name="pdf_sha256_length",
        ),
        sa.CheckConstraint("pdf IS NULL OR pdf_sha256 IS NOT NULL", name="pdf_has_sha256"),
        sa.Index("ix_document_versions_generation_id", "generation_id"),
        sa.Index("ix_document_versions_legacy_file_id", "legacy_file_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = _uuid()
    user_id: Mapped[uuid.UUID] = _uuid()
    number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False, deferred=True)
    content_schema: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    content_hash: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False)
    language: Mapped[Language] = mapped_column(LANGUAGE_ENUM, nullable=False)
    template_slug: Mapped[str] = mapped_column(sa.Text, nullable=False)
    template_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    origin: Mapped[Origin] = mapped_column(ORIGIN_ENUM, nullable=False)
    generation_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    profile_revision: Mapped[int | None] = mapped_column(sa.Integer)
    corrections: Mapped[list[dict[str, Any]]] = mapped_column(
        pg.JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    fit: Mapped[FitResult | None] = mapped_column(FIT_ENUM)
    fit_step: Mapped[int | None] = mapped_column(sa.Integer)
    pages: Mapped[int | None] = mapped_column(sa.Integer)
    pdf: Mapped[bytes | None] = mapped_column(pg.BYTEA, deferred=True)
    pdf_sha256: Mapped[bytes | None] = mapped_column(pg.BYTEA)
    legacy_file_id: Mapped[uuid.UUID | None] = _set_null_fk("job_files.id")
    approved_at: Mapped[datetime | None] = _later()
    frozen_at: Mapped[datetime | None] = _later()
    frozen_reason: Mapped[FreezeReason | None] = mapped_column(FREEZE_REASON_ENUM)
    created_at: Mapped[datetime] = _now()


class DocumentMessage(Base):
    __tablename__ = "document_messages"
    __table_args__ = (
        _owned_by_document("document_messages"),
        sa.CheckConstraint("char_length(text) >= 1", name="text_not_empty"),
        sa.Index("ix_document_messages_document_id_channel_id", "document_id", "channel", "id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=False), primary_key=True)
    document_id: Mapped[uuid.UUID] = _uuid()
    user_id: Mapped[uuid.UUID] = _uuid()
    channel: Mapped[Channel] = mapped_column(
        CHANNEL_ENUM, nullable=False, default=Channel.content, server_default=Channel.content.value
    )
    role: Mapped[Role] = mapped_column(ROLE_ENUM, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    generation_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    version_number: Mapped[int | None] = mapped_column(sa.Integer)
    at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class DocumentConsent(Base):
    __tablename__ = "document_consents"

    user_id: Mapped[uuid.UUID] = _user_fk(primary_key=True)
    text_version: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    accepted_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class DocumentSettingsRow(Base):
    __tablename__ = "document_settings"

    user_id: Mapped[uuid.UUID] = _user_fk(primary_key=True)
    no_dash_separators: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    banned_phrases: Mapped[list[str]] = mapped_column(
        pg.ARRAY(sa.Text), nullable=False, default=list, server_default=sa.text("'{}'::text[]")
    )
    updated_at: Mapped[datetime] = _now()


class DocumentUserTemplate(Base):
    __tablename__ = "document_user_templates"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "slug", name="uq_document_user_templates_user_id_slug"),
        _same_owner(
            "document_user_templates",
            "source_document_id",
            "ingestion_documents",
            "fk_document_user_templates_source_document_id_ingestion",  # 63 characters at most
        ),
        _same_owner("document_user_templates", "base_document_id", "documents"),
        sa.CheckConstraint("slug ~ '^upload-[a-z0-9]{8}$'", name="slug_is_upload"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.Index("ix_document_user_templates_base_document_id", "base_document_id"),
        sa.Index("ix_document_user_templates_source_document_id", "source_document_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=1, server_default=sa.text("1")
    )
    manifest: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False)
    cv_html: Mapped[str] = mapped_column(sa.Text, nullable=False, deferred=True)
    letter_html: Mapped[str] = mapped_column(sa.Text, nullable=False, deferred=True)
    css: Mapped[str] = mapped_column(sa.Text, nullable=False, deferred=True)
    font_families: Mapped[list[str]] = mapped_column(pg.ARRAY(sa.Text), nullable=False)
    previous: Mapped[dict[str, Any] | None] = mapped_column(pg.JSONB, deferred=True)
    source_document_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    base_document_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
