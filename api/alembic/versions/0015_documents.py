"""Documents D5: the tables of the documents module.

Seven tables and their sixteen enums, as SPEC-documents.md ("Data model") writes them:
`documents`, `document_generations`, `document_versions`, `document_messages`,
`document_consents`, `document_settings` and `document_user_templates`; the two partial unique
indexes on `documents` (one default base CV per person, one document of a kind per posting),
the partial unique index of open generations, the composite foreign key on `jobs(id, user_id)`
(its unique constraint exists since 0013), and the trigger that protects frozen versions.

Isolation is in the schema, as in 0013: a posting's document points at `jobs(id, user_id)`
through `(posting_id, user_id)`, and a version, a generation and a message point at
`documents(id, user_id)` through `(document_id, user_id)`, so no row belongs to one person and
hangs under another's posting or document.

The trigger `document_versions_frozen` (BEFORE UPDATE OR DELETE, per row) refuses on a frozen
version any change of the content, its hash, schema, language, template, corrections, PDF, fit
and identity, any change of `frozen_at` or `frozen_reason` (clearing them included), and any
delete while its document still exists: a frozen version goes only with its document (the
cascade of a document, a posting or a user). One referential action is let through: the legacy
file's `SET NULL` when the posting itself is being deleted, since the file goes with it.
Its errors carry SQLSTATE `23000` and the message `document_version_frozen`.

Revision 0014 is reserved for mail M8, which has not merged: this one chains on 0013, the head
of main, and M8 chains after the newest head when it resumes.

Expand only: new types, new tables, a new function and trigger. The release before this one
reads and writes none of it (CONTRIBUTING.md, Migrations).

Written by hand to match app/documents/models.py (the tests run `alembic check` against it).
Every statement is plain DDL so `alembic upgrade head --sql` works without a database.

Revision ID: 0015
Revises: 0013
Create Date: 2026-10-14
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0015"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = {
    "document_scope": ("base", "posting"),
    "base_source": ("profile", "built", "uploaded"),
    "document_kind": ("cv", "letter"),
    "document_language": ("en", "fr"),
    "document_author": ("user", "writer", "chat", "correction", "restore", "import", "upload"),
    "render_state": ("idle", "queued", "rendering"),
    "version_origin": ("generated", "edited", "chat", "restored", "template", "uploaded", "legacy"),
    "fit_result": ("one_page", "overflow"),
    "freeze_reason": ("applied", "marked_sent", "apply", "legacy_applied"),
    "generation_mode": (
        "base_upload",
        "base_profile",
        "posting",
        "chat",
        "shorten",
        "template_capture",
        "template_fix",
    ),
    "generation_trigger": ("auto", "click", "chat"),
    "generation_status": ("waiting", "queued", "running", "done", "failed", "cancelled"),
    "generation_wait": ("daily_ceiling", "consent", "credential", "base_cv"),
    "generation_outcome": ("draft_replaced", "kept_as_version"),
    "message_channel": ("content", "design"),
    "message_role": ("user", "assistant"),
}

# The columns a frozen version never changes (SPEC-documents.md, "Frozen means frozen"), with
# its identity and the freeze itself.
PROTECTED = (
    "document_id",
    "user_id",
    "number",
    "content",
    "content_schema",
    "content_hash",
    "language",
    "template_slug",
    "template_version",
    "corrections",
    "fit",
    "fit_step",
    "pages",
    "pdf",
    "pdf_sha256",
    "legacy_file_id",
    "frozen_at",
    "frozen_reason",
)

_old = ", ".join(f"OLD.{column}" for column in PROTECTED)
_new = ", ".join(f"NEW.{column}" for column in PROTECTED)
_unlinked = ", ".join(
    f"NEW.{column}" if column != "legacy_file_id" else "OLD.legacy_file_id" for column in PROTECTED
)

FROZEN_FUNCTION = f"""
CREATE FUNCTION document_versions_frozen() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.frozen_at IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM documents WHERE id = OLD.document_id) THEN
            RAISE EXCEPTION 'document_version_frozen' USING ERRCODE = '23000';
        END IF;
        RETURN OLD;
    END IF;
    IF ({_new}) IS NOT DISTINCT FROM ({_old}) THEN
        RETURN NEW;
    END IF;
    IF NEW.legacy_file_id IS NULL AND OLD.legacy_file_id IS NOT NULL
        AND ({_unlinked}) IS NOT DISTINCT FROM ({_old})
        AND NOT EXISTS (
            SELECT 1 FROM documents d JOIN jobs j ON j.id = d.posting_id
            WHERE d.id = OLD.document_id
        ) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'document_version_frozen' USING ERRCODE = '23000';
END;
$$
"""  # noqa: S608, the columns are the constant PROTECTED

FROZEN_TRIGGER = (
    "CREATE TRIGGER document_versions_frozen BEFORE UPDATE OR DELETE ON document_versions "
    "FOR EACH ROW EXECUTE FUNCTION document_versions_frozen()"
)


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


def _int(name: str, nullable: bool = True, default: int | None = None) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.Integer(),
        nullable=nullable,
        server_default=None if default is None else sa.text(str(default)),
    )


def _user_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["user_id"], ["users.id"], name=op.f(f"fk_{table}_user_id_users"), ondelete="CASCADE"
    )


def _set_null(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    referred = target.split(".")[0]
    return sa.ForeignKeyConstraint(
        [column], [target], name=op.f(f"fk_{table}_{column}_{referred}"), ondelete="SET NULL"
    )


def _owned_by_document(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["document_id", "user_id"],
        ["documents.id", "documents.user_id"],
        name=f"fk_{table}_document_id_documents",
        ondelete="CASCADE",
    )


def _index(table: str, *columns: str) -> None:
    op.create_index(f"ix_{table}_{'_'.join(columns)}", table, list(columns))


def upgrade() -> None:
    for name, labels in ENUMS.items():
        quoted = ", ".join(f"'{label}'" for label in labels)
        op.execute(f"CREATE TYPE {name} AS ENUM ({quoted})")

    op.create_table(
        "documents",
        _uuid_pk(),
        _uuid("user_id", nullable=False),
        sa.Column("scope", _enum("document_scope"), nullable=False),
        _uuid("posting_id", nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("base_source", _enum("base_source"), nullable=True),
        _uuid("base_document_id", nullable=True),
        sa.Column("kind", _enum("document_kind"), nullable=False),
        sa.Column("language", _enum("document_language"), nullable=False),
        sa.Column("template_slug", sa.Text(), nullable=False),
        sa.Column("draft", pg.JSONB(), nullable=False),
        _int("draft_schema", nullable=False),
        _int("draft_revision", nullable=False, default=1),
        sa.Column("draft_written_by", _enum("document_author"), nullable=False),
        _at("draft_updated_at", nullable=False),
        sa.Column("checks", pg.JSONB(), nullable=True),
        _int("checks_revision"),
        sa.Column("render_state", _enum("render_state"), nullable=False, server_default="idle"),
        _int("render_revision"),
        sa.Column("render_error", sa.Text(), nullable=True),
        _uuid("source_document_id", nullable=True),
        _at("created_at", nullable=False, now=True),
        _at("updated_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        _user_fk("documents"),
        sa.UniqueConstraint("id", "user_id", name="uq_documents_id_user_id"),
        sa.ForeignKeyConstraint(
            ["posting_id", "user_id"],
            ["jobs.id", "jobs.user_id"],
            name="fk_documents_posting_id_jobs",
            ondelete="CASCADE",
        ),
        _set_null("documents", "base_document_id", "documents.id"),
        _set_null("documents", "source_document_id", "ingestion_documents.id"),
        sa.CheckConstraint(
            "(scope = 'base') = (posting_id IS NULL)", name=op.f("ck_documents_base_has_no_posting")
        ),
        sa.CheckConstraint(
            "(scope = 'base') = (name IS NOT NULL)", name=op.f("ck_documents_base_has_a_name")
        ),
        sa.CheckConstraint(
            "scope = 'base' OR (is_default IS false AND base_source IS NULL)",
            name=op.f("ck_documents_posting_is_not_a_base"),
        ),
        sa.CheckConstraint(
            "scope = 'posting' OR kind = 'cv'", name=op.f("ck_documents_base_is_a_cv")
        ),
        sa.CheckConstraint(
            "scope = 'posting' OR base_source IS NOT NULL",
            name=op.f("ck_documents_base_has_source"),
        ),
        sa.CheckConstraint(
            "scope = 'base' OR kind = 'cv' OR base_document_id IS NULL",
            name=op.f("ck_documents_letter_has_no_base"),
        ),
        sa.CheckConstraint(
            "name IS NULL OR (char_length(name) BETWEEN 1 AND 100)",
            name=op.f("ck_documents_name_length"),
        ),
        sa.CheckConstraint(
            "draft_revision >= 1", name=op.f("ck_documents_draft_revision_positive")
        ),
        sa.CheckConstraint(
            "char_length(template_slug) BETWEEN 1 AND 64",
            name=op.f("ck_documents_template_slug_set"),
        ),
    )
    for column in ("user_id", "posting_id", "base_document_id", "source_document_id"):
        _index("documents", column)
    op.create_index(
        "uq_documents_user_id_default",
        "documents",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'base' AND is_default"),
    )
    op.create_index(
        "uq_documents_user_id_posting_id_kind",
        "documents",
        ["user_id", "posting_id", "kind"],
        unique=True,
        postgresql_where=sa.text("scope = 'posting'"),
    )

    op.create_table(
        "document_generations",
        _uuid_pk(),
        _uuid("user_id", nullable=False),
        _uuid("document_id", nullable=False),
        _uuid("run_id", nullable=True),
        sa.Column("mode", _enum("generation_mode"), nullable=False),
        sa.Column("trigger", _enum("generation_trigger"), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        _int("base_draft_revision"),
        _uuid("base_document_id", nullable=True),
        sa.Column("estimate", pg.JSONB(), nullable=False),
        _int("token_ceiling", nullable=False),
        sa.Column("status", _enum("generation_status"), nullable=False),
        sa.Column("wait_reason", _enum("generation_wait"), nullable=True),
        sa.Column("ceiling_day", sa.Date(), nullable=True),
        sa.Column("outcome", _enum("generation_outcome"), nullable=True),
        _int("version_number"),
        _int("corrections", nullable=False, default=0),
        sa.Column("error", sa.Text(), nullable=True),
        _at("created_at", nullable=False, now=True),
        _at("started_at"),
        _at("finished_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_generations")),
        sa.UniqueConstraint("id", "user_id", name="uq_document_generations_id_user_id"),
        _owned_by_document("document_generations"),
        _set_null("document_generations", "run_id", "agent_runs.id"),
        _set_null("document_generations", "base_document_id", "documents.id"),
        sa.CheckConstraint(
            "token_ceiling >= 0 AND corrections >= 0",
            name=op.f("ck_document_generations_counts_not_negative"),
        ),
    )
    _index("document_generations", "document_id")
    _index("document_generations", "user_id", "ceiling_day")
    _index("document_generations", "run_id")
    _index("document_generations", "base_document_id")
    op.create_index(
        "uq_document_generations_document_id_open",
        "document_generations",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('waiting', 'queued', 'running')"),
    )

    op.create_table(
        "document_versions",
        _uuid_pk(),
        _uuid("document_id", nullable=False),
        _uuid("user_id", nullable=False),
        _int("number", nullable=False),
        sa.Column("content", pg.JSONB(), nullable=False),
        _int("content_schema", nullable=False),
        sa.Column("content_hash", pg.BYTEA(), nullable=False),
        sa.Column("language", _enum("document_language"), nullable=False),
        sa.Column("template_slug", sa.Text(), nullable=False),
        _int("template_version", nullable=False),
        sa.Column("origin", _enum("version_origin"), nullable=False),
        _uuid("generation_id", nullable=True),
        _int("profile_revision"),
        sa.Column("corrections", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("fit", _enum("fit_result"), nullable=True),
        _int("fit_step"),
        _int("pages"),
        sa.Column("pdf", pg.BYTEA(), nullable=True),
        sa.Column("pdf_sha256", pg.BYTEA(), nullable=True),
        _uuid("legacy_file_id", nullable=True),
        _at("approved_at"),
        _at("frozen_at"),
        sa.Column("frozen_reason", _enum("freeze_reason"), nullable=True),
        _at("created_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_versions")),
        sa.UniqueConstraint("document_id", "number", name="uq_document_versions_document_id"),
        _owned_by_document("document_versions"),
        _set_null("document_versions", "generation_id", "document_generations.id"),
        _set_null("document_versions", "legacy_file_id", "job_files.id"),
        sa.CheckConstraint(
            "(frozen_at IS NULL) = (frozen_reason IS NULL)",
            name=op.f("ck_document_versions_frozen_is_whole"),
        ),
        sa.CheckConstraint(
            "pdf IS NULL OR legacy_file_id IS NULL",
            name=op.f("ck_document_versions_one_pdf_source"),
        ),
        sa.CheckConstraint("number >= 1", name=op.f("ck_document_versions_number_positive")),
        sa.CheckConstraint(
            "octet_length(content_hash) = 32", name=op.f("ck_document_versions_content_hash_length")
        ),
        sa.CheckConstraint(
            "pdf_sha256 IS NULL OR octet_length(pdf_sha256) = 32",
            name=op.f("ck_document_versions_pdf_sha256_length"),
        ),
        sa.CheckConstraint(
            "pdf IS NULL OR pdf_sha256 IS NOT NULL",
            name=op.f("ck_document_versions_pdf_has_sha256"),
        ),
    )
    _index("document_versions", "generation_id")
    _index("document_versions", "legacy_file_id")
    op.execute(FROZEN_FUNCTION)
    op.execute(FROZEN_TRIGGER)

    op.create_table(
        "document_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        _uuid("document_id", nullable=False),
        _uuid("user_id", nullable=False),
        sa.Column("channel", _enum("message_channel"), nullable=False, server_default="content"),
        sa.Column("role", _enum("message_role"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        _uuid("generation_id", nullable=True),
        _int("version_number"),
        _at("at", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_messages")),
        _owned_by_document("document_messages"),
        sa.CheckConstraint(
            "char_length(text) >= 1", name=op.f("ck_document_messages_text_not_empty")
        ),
    )
    _index("document_messages", "document_id", "channel", "id")

    op.create_table(
        "document_consents",
        _uuid("user_id", nullable=False),
        _int("text_version", nullable=False),
        _at("accepted_at", nullable=False),
        sa.PrimaryKeyConstraint("user_id", "text_version", name=op.f("pk_document_consents")),
        _user_fk("document_consents"),
    )

    op.create_table(
        "document_settings",
        _uuid("user_id", nullable=False),
        sa.Column("no_dash_separators", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "banned_phrases",
            pg.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        _at("updated_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_document_settings")),
        _user_fk("document_settings"),
    )

    op.create_table(
        "document_user_templates",
        _uuid_pk(),
        _uuid("user_id", nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        _int("version", nullable=False, default=1),
        sa.Column("manifest", pg.JSONB(), nullable=False),
        sa.Column("cv_html", sa.Text(), nullable=False),
        sa.Column("letter_html", sa.Text(), nullable=False),
        sa.Column("css", sa.Text(), nullable=False),
        sa.Column("font_families", pg.ARRAY(sa.Text()), nullable=False),
        sa.Column("previous", pg.JSONB(), nullable=True),
        _uuid("source_document_id", nullable=True),
        _uuid("base_document_id", nullable=True),
        _at("created_at", nullable=False, now=True),
        _at("updated_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_user_templates")),
        _user_fk("document_user_templates"),
        sa.UniqueConstraint("user_id", "slug", name="uq_document_user_templates_user_id_slug"),
        _set_null("document_user_templates", "source_document_id", "ingestion_documents.id"),
        _set_null("document_user_templates", "base_document_id", "documents.id"),
        sa.CheckConstraint(
            "slug ~ '^upload-[a-z0-9]{8}$'", name=op.f("ck_document_user_templates_slug_is_upload")
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_document_user_templates_version_positive")
        ),
    )
    _index("document_user_templates", "base_document_id")
    _index("document_user_templates", "source_document_id")


def downgrade() -> None:
    op.drop_table("document_user_templates")
    op.drop_table("document_settings")
    op.drop_table("document_consents")
    op.drop_table("document_messages")
    op.execute("DROP TRIGGER document_versions_frozen ON document_versions")
    op.execute("DROP FUNCTION document_versions_frozen()")
    op.drop_table("document_versions")
    op.drop_table("document_generations")
    op.drop_table("documents")
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE {name}")
