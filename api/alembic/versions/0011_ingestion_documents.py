"""Ingestion G1: ingestion_documents, ingestion_images, ingestion_consents and their three enums.

The first half of the DDL of SPEC-ingestion.md (Documents); the other half (runs, steps, items)
is migration 0012, task G9. These three tables hold every column a later task stores into, so
none of G2b, G7, G8, G14 and G16 adds a migration: `note`, `truncated`, `pages`, `pages_text`,
`pages_image`, `transcribed_pages`, `parser_version`, `failed_reason`, `failed_kind`, `attempts`
and `pending_since` on documents, `user_id` and `text` on images, and the consent table that
only G16 writes.

All three cascade from users (a deleted account leaves nothing behind) and images cascade from
their document, through one foreign key on `(document_id, user_id)`, so an image can only be
filed under the user of its document. `unique(user_id, sha256)` is the per user dedupe. The
CHECK constraints are backstops on sizes (app/ingestion/models.py says which limit each one is).

Expand only: new types and new tables, which the release before this one never reads or writes
(CONTRIBUTING.md, Migrations). Deleting a user in that release cascades here through the
foreign keys, with no code involved.

The check constraints are named with `op.f()`: the name written here is the final one, the one
the convention of app/models_base.py gives the model's constraint. Without it Alembic applies
the convention a second time (`ck_<table>_ck_<table>_<name>`, as the tables of 0002 to 0009 have
it, cut and hashed when it passes 63 characters).

Written by hand to match app/ingestion/models.py (the tests run `alembic check` against it).
Every statement is plain DDL so `alembic upgrade head --sql` works without a database.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT_SOURCES = ("parsed", "transcribed", "pasted")
TEXT_STATUSES = ("pending", "awaiting_transcription", "done", "failed")
FAILED_KINDS = ("file", "server")

FILE_MAX_BYTES = 20 * 1024 * 1024
TEXT_MAX_CHARS = 500_000
NAME_MAX_CHARS = 200
IMAGE_MAX_BYTES = 300_000
IMAGE_MAX_SIDE = 2000
IMAGE_TEXT_MAX_CHARS = 20_000
PAGES_MAX = 200


def _tz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _labels(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(f"CREATE TYPE ingestion_text_source AS ENUM ({_labels(TEXT_SOURCES)})")
    op.execute(f"CREATE TYPE ingestion_text_status AS ENUM ({_labels(TEXT_STATUSES)})")
    op.execute(f"CREATE TYPE ingestion_failed_kind AS ENUM ({_labels(FAILED_KINDS)})")

    op.create_table(
        "ingestion_documents",
        sa.Column(
            "id", pg.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", pg.BYTEA(), nullable=False),
        sa.Column("bytes", pg.BYTEA(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "text_source",
            pg.ENUM(*TEXT_SOURCES, name="ingestion_text_source", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "text_status",
            pg.ENUM(*TEXT_STATUSES, name="ingestion_text_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("pages_text", sa.Integer(), nullable=True),
        sa.Column("pages_image", sa.Integer(), nullable=True),
        sa.Column("transcribed_pages", pg.ARRAY(sa.Integer()), nullable=True),
        sa.Column("parser_version", sa.SmallInteger(), nullable=False),
        sa.Column("failed_reason", sa.Text(), nullable=True),
        sa.Column(
            "failed_kind",
            pg.ENUM(*FAILED_KINDS, name="ingestion_failed_kind", create_type=False),
            nullable=True,
        ),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("secret_warning", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("secret_confirmed_at", _tz(), nullable=True),
        sa.Column("pending_since", _tz(), nullable=True),
        sa.Column("created_at", _tz(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_documents"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_ingestion_documents_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "sha256", name="uq_ingestion_documents_user_id_sha256"),
        sa.UniqueConstraint("id", "user_id", name="uq_ingestion_documents_id_user_id"),
        sa.CheckConstraint(
            f"char_length(name) >= 1 AND char_length(name) <= {NAME_MAX_CHARS}",
            name=op.f("ck_ingestion_documents_name_length"),
        ),
        sa.CheckConstraint(
            f"size >= 1 AND size <= {FILE_MAX_BYTES}",
            name=op.f("ck_ingestion_documents_size_range"),
        ),
        sa.CheckConstraint(
            "octet_length(sha256) = 32", name=op.f("ck_ingestion_documents_sha256_length")
        ),
        sa.CheckConstraint(
            f"bytes IS NULL OR octet_length(bytes) <= {FILE_MAX_BYTES}",
            name=op.f("ck_ingestion_documents_bytes_size"),
        ),
        sa.CheckConstraint(
            f"text IS NULL OR char_length(text) <= {TEXT_MAX_CHARS}",
            name=op.f("ck_ingestion_documents_text_length"),
        ),
        sa.CheckConstraint(
            "(pages IS NULL OR pages >= 0) AND (pages_text IS NULL OR pages_text >= 0) "
            "AND (pages_image IS NULL OR pages_image >= 0)",
            name=op.f("ck_ingestion_documents_pages_not_negative"),
        ),
        sa.CheckConstraint(
            f"transcribed_pages IS NULL OR cardinality(transcribed_pages) <= {PAGES_MAX}",
            name=op.f("ck_ingestion_documents_transcribed_pages_count"),
        ),
        sa.CheckConstraint(
            "parser_version >= 1", name=op.f("ck_ingestion_documents_parser_version_positive")
        ),
        sa.CheckConstraint(
            "attempts >= 0", name=op.f("ck_ingestion_documents_attempts_not_negative")
        ),
    )

    op.create_table(
        "ingestion_images",
        sa.Column("id", sa.BigInteger(), autoincrement=True),  # bigserial
        sa.Column("document_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("jpeg", pg.BYTEA(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_images"),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["ingestion_documents.id", "ingestion_documents.user_id"],
            name="fk_ingestion_images_document_id_ingestion_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_ingestion_images_user_id_users", ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "document_id", "position", name="uq_ingestion_images_document_id_position"
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_ingestion_images_position_not_negative")),
        sa.CheckConstraint(
            f"octet_length(jpeg) >= 1 AND octet_length(jpeg) <= {IMAGE_MAX_BYTES}",
            name=op.f("ck_ingestion_images_jpeg_size"),
        ),
        sa.CheckConstraint(
            f"width >= 1 AND width <= {IMAGE_MAX_SIDE} "
            f"AND height >= 1 AND height <= {IMAGE_MAX_SIDE}",
            name=op.f("ck_ingestion_images_side_range"),
        ),
        sa.CheckConstraint(
            f"text IS NULL OR char_length(text) <= {IMAGE_TEXT_MAX_CHARS}",
            name=op.f("ck_ingestion_images_text_length"),
        ),
    )
    op.create_index("ix_ingestion_images_user_id", "ingestion_images", ["user_id"])

    op.create_table(
        "ingestion_consents",
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("text_version", sa.Integer(), nullable=False),
        sa.Column("accepted_at", _tz(), nullable=False, server_default=sa.text("now()")),
        # One row per user and version of the text: a new version never overwrites what was
        # accepted before. The current consent is the row of the current version.
        sa.PrimaryKeyConstraint("user_id", "text_version", name="pk_ingestion_consents"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_ingestion_consents_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "text_version >= 1", name=op.f("ck_ingestion_consents_text_version_positive")
        ),
    )


def downgrade() -> None:
    op.drop_table("ingestion_consents")
    op.drop_table("ingestion_images")
    op.drop_table("ingestion_documents")
    op.execute("DROP TYPE ingestion_failed_kind")
    op.execute("DROP TYPE ingestion_text_status")
    op.execute("DROP TYPE ingestion_text_source")
