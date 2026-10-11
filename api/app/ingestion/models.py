"""Tables of the ingestion module, first half (task G1): ingestion_documents, ingestion_images and
ingestion_consents, as the DDL of SPEC-ingestion.md (Documents) writes them.

They hold every column a later task stores into, so no later task adds a migration for them:
G2b, G7 and G8 write `text`, `note`, `truncated`, the page counts, `parser_version`, the failure
columns, `attempts` and `pending_since`; G8 writes `ingestion_images`; G14 writes
`ingestion_images.text`, assembles the document's text and writes `transcribed_pages`; G16
writes `ingestion_consents`. Task G1 itself writes a document at upload and deletes it.

How a document's content is kept:
- `raw` (column `bytes`) holds the uploaded file until its text or its images are stored, then
  NULL. A text file or a paste never has it: its text is stored at upload.
- `text` holds the pages of a PDF in order, joined by a form feed (U+000C); a page waiting for
  its transcription is the empty string between two form feeds.
- `ingestion_images.position` is the zero based page index of a PDF page, 0 for an uploaded image.
- `transcribed_pages` lists the page indexes whose text a model wrote from an image. The images
  are deleted once transcribed, so this is what still says, afterwards, which pages of a
  mixed PDF to show as "transcribed, check it".
- `pending_since` is when the document last started to wait for its reader: the upload, a
  replacement (the row keeps its `created_at`), a new try. It is how a reaper tells a document
  queued a moment ago from one that was lost. NULL for a document that never waited.
Both content columns are deferred: listing a user's documents never loads them.

Everything cascades from `users`: a deleted account leaves no document, image or consent behind.
`unique(user_id, sha256)` is the per user dedupe; two users may hold the same file, and neither
can learn it from the other. An image carries its user as well as its document, and the two
are tied by one foreign key on `(document_id, user_id)`: an image filed under another user
than its document's cannot exist, whatever code writes it.

The CHECK constraints are backstops on sizes, set at the product limits where a later task
cannot want more (the 20 MiB file, the 500,000 characters, the 300,000 byte page image the
runner can carry). The limits themselves and their messages live in `documents.py`.
Migration: alembic/versions/0011_ingestion_documents.py, which must stay in step with this file
(`alembic check` in the tests compares them).
"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models_base import Base

FILE_MAX_BYTES = 20 * 1024 * 1024  # 20 MiB, shown as "20 MB"
TEXT_MAX_CHARS = 500_000
NAME_MAX_CHARS = 200
SHA256_BYTES = 32
IMAGE_MAX_BYTES = 300_000  # one page image, the most one tool result carries (plan, G0)
IMAGE_MAX_SIDE = 2000  # the model's client resizes anything larger; G8 normalises to 1568
IMAGE_TEXT_MAX_CHARS = 20_000  # the transcription of one image (G14)
PAGES_MAX = 200  # pages of one PDF that are read at all, so that many page indexes at most


class TextSource(enum.StrEnum):
    """How the text of a document is obtained: read by a parser, written by the model from an
    image, or pasted by the user."""

    parsed = "parsed"
    transcribed = "transcribed"
    pasted = "pasted"


class TextStatus(enum.StrEnum):
    """pending: waits for a parser. awaiting_transcription: its images wait for a run.
    done: the text is there. failed: `failed_reason` says why."""

    pending = "pending"
    awaiting_transcription = "awaiting_transcription"
    done = "done"
    failed = "failed"


class FailedKind(enum.StrEnum):
    """file: the file itself cannot be read. server: the parser or the queue failed."""

    file = "file"
    server = "server"


def _enum(kind: type[enum.StrEnum], name: str) -> sa.Enum:
    return sa.Enum(kind, name=name, values_callable=lambda members: [m.value for m in members])


TEXT_SOURCE_ENUM = _enum(TextSource, "ingestion_text_source")
TEXT_STATUS_ENUM = _enum(TextStatus, "ingestion_text_status")
FAILED_KIND_ENUM = _enum(FailedKind, "ingestion_failed_kind")


class IngestionDocument(Base):
    __tablename__ = "ingestion_documents"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "sha256", name="uq_ingestion_documents_user_id_sha256"),
        # What the foreign key of ingestion_images points at: a document together with its user.
        sa.UniqueConstraint("id", "user_id", name="uq_ingestion_documents_id_user_id"),
        sa.CheckConstraint(
            f"char_length(name) >= 1 AND char_length(name) <= {NAME_MAX_CHARS}", name="name_length"
        ),
        sa.CheckConstraint(f"size >= 1 AND size <= {FILE_MAX_BYTES}", name="size_range"),
        sa.CheckConstraint(f"octet_length(sha256) = {SHA256_BYTES}", name="sha256_length"),
        sa.CheckConstraint(
            f"bytes IS NULL OR octet_length(bytes) <= {FILE_MAX_BYTES}", name="bytes_size"
        ),
        sa.CheckConstraint(
            f"text IS NULL OR char_length(text) <= {TEXT_MAX_CHARS}", name="text_length"
        ),
        sa.CheckConstraint(
            "(pages IS NULL OR pages >= 0) AND (pages_text IS NULL OR pages_text >= 0) "
            "AND (pages_image IS NULL OR pages_image >= 0)",
            name="pages_not_negative",
        ),
        sa.CheckConstraint(
            f"transcribed_pages IS NULL OR cardinality(transcribed_pages) <= {PAGES_MAX}",
            name="transcribed_pages_count",
        ),
        sa.CheckConstraint("parser_version >= 1", name="parser_version_positive"),
        sa.CheckConstraint("attempts >= 0", name="attempts_not_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Display only, cleaned by documents.clean_name: never a path, never trusted for the type.
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Decided from the first bytes of the file, never from its name or a declared type.
    mime: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Bytes of the upload as received (for a paste: of its UTF-8 text).
    size: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    sha256: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False)
    raw: Mapped[bytes | None] = mapped_column("bytes", pg.BYTEA, deferred=True)
    text: Mapped[str | None] = mapped_column(sa.Text, deferred=True)
    text_source: Mapped[TextSource] = mapped_column(TEXT_SOURCE_ENUM, nullable=False)
    text_status: Mapped[TextStatus] = mapped_column(
        TEXT_STATUS_ENUM, nullable=False, server_default=TextStatus.pending.value
    )
    # What was not read, on a document that is not failed ("pages 31 to 44 were not read").
    note: Mapped[str | None] = mapped_column(sa.Text)
    truncated: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    pages: Mapped[int | None] = mapped_column(sa.Integer)
    pages_text: Mapped[int | None] = mapped_column(sa.Integer)
    pages_image: Mapped[int | None] = mapped_column(sa.Integer)
    transcribed_pages: Mapped[list[int] | None] = mapped_column(pg.ARRAY(sa.Integer))
    parser_version: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    failed_reason: Mapped[str | None] = mapped_column(sa.Text)
    failed_kind: Mapped[FailedKind | None] = mapped_column(FAILED_KIND_ENUM)
    attempts: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False, server_default="0")
    # The text matches a secret pattern (secrets.py): no run reads it until the user confirms.
    secret_warning: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    secret_confirmed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    pending_since: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class IngestionImage(Base):
    """One page image of a document, a normalised JPEG kept until a run has transcribed it."""

    __tablename__ = "ingestion_images"
    __table_args__ = (
        sa.UniqueConstraint(
            "document_id", "position", name="uq_ingestion_images_document_id_position"
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["ingestion_documents.id", "ingestion_documents.user_id"],
            name="fk_ingestion_images_document_id_ingestion_documents",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("position >= 0", name="position_not_negative"),
        sa.CheckConstraint(
            f"octet_length(jpeg) >= 1 AND octet_length(jpeg) <= {IMAGE_MAX_BYTES}", name="jpeg_size"
        ),
        sa.CheckConstraint(
            f"width >= 1 AND width <= {IMAGE_MAX_SIDE} "
            f"AND height >= 1 AND height <= {IMAGE_MAX_SIDE}",
            name="side_range",
        ),
        sa.CheckConstraint(
            f"text IS NULL OR char_length(text) <= {IMAGE_TEXT_MAX_CHARS}", name="text_length"
        ),
        # The user's quota adds up the images of the user (documents.usage).
        sa.Index("ix_ingestion_images_user_id", "user_id"),
    )

    # bigserial
    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    jpeg: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False, deferred=True)
    width: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    height: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    # The model's transcription, until the last image of the document has one (G14).
    text: Mapped[str | None] = mapped_column(sa.Text, deferred=True)


class IngestionConsent(Base):
    """The user accepted version `text_version` of the consent text (decision 6, written by G16):
    the selected documents are sent to Anthropic through the user's own credential."""

    __tablename__ = "ingestion_consents"
    __table_args__ = (sa.CheckConstraint("text_version >= 1", name="text_version_positive"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    text_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
