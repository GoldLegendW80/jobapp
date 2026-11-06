"""Tables of the profile module: profiles (one row per user) and profile_revisions (append-only).

`profiles.sections` holds one JSONB document per section (identity, experience, facts, skills,
search, answers), each validated by its Pydantic model in `schemas.py` before it is written.
`revision` counts saves: every save of a section increments it by one and appends the saved
section to `profile_revisions` under that number, so any past version can be shown or restored.
The photo lives in its own columns, never in the JSON, and is not a section: storing or removing
it adds no revision (see photo.py for what is stored). `profile_title_suggestions` keeps the job
titles agent's last answer per person (titles.py), and `profile_about` the "About you" document
of each person with its last reading by the model (about.py).
Migrations: alembic/versions/0003_profile.py, 0032_profile_title_suggestions.py and
0034_profile_about.py, which must
stay in step with this file (`alembic check` in the tests compares them).
"""

import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models_base import Base

PHOTO_MIMES = ("image/png", "image/jpeg")


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        sa.CheckConstraint("revision >= 0", name="revision_not_negative"),
        sa.CheckConstraint("jsonb_typeof(sections) = 'object'", name="sections_is_object"),
        sa.CheckConstraint(
            "(photo IS NULL) = (photo_mime IS NULL)", name="photo_and_mime_together"
        ),
        sa.CheckConstraint(
            "photo_mime IS NULL OR photo_mime IN ('image/png', 'image/jpeg')",
            name="photo_mime_known",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sections: Mapped[dict[str, Any]] = mapped_column(
        pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    # Loaded only when read (up to 1 MB): reading the sections never pulls the image along.
    photo: Mapped[bytes | None] = mapped_column(pg.BYTEA, deferred=True)
    photo_mime: Mapped[str | None] = mapped_column(sa.Text)
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class ProfileRevision(Base):
    """One saved section: the data as it was written by save number `revision`."""

    __tablename__ = "profile_revisions"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "revision", name="uq_profile_revisions_user_id_revision"),
        sa.CheckConstraint("revision > 0", name="revision_positive"),
    )

    # bigserial
    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    section: Mapped[str] = mapped_column(sa.Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


# The history of one section, newest first (GET /api/profile/revisions?section=).
sa.Index(
    "ix_profile_revisions_user_id_section_revision",
    ProfileRevision.user_id,
    ProfileRevision.section,
    ProfileRevision.revision.desc(),
)


class TitleSuggestions(Base):
    """The job titles agent's last suggestions for one person, and what bounds its runs
    (app/profile/titles.py). Not a section: no revision, never exported. Migration 0032."""

    __tablename__ = "profile_title_suggestions"
    __table_args__ = (
        sa.CheckConstraint("jsonb_typeof(suggestions) = 'array'", name="suggestions_is_array"),
        sa.CheckConstraint("asks >= 0", name="asks_not_negative"),
        sa.CheckConstraint("error IS NULL OR char_length(error) <= 40", name="error_length"),
        sa.CheckConstraint("basis IS NULL OR char_length(basis) = 64", name="basis_length"),
        sa.CheckConstraint("jsonb_typeof(dismissed) = 'array'", name="dismissed_is_array"),
        sa.CheckConstraint("jsonb_typeof(messages) = 'array'", name="messages_is_array"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # `[{title, reason, language}]`, as the checks kept them when they were stored, oldest first
    # (at most titles.LIST_MAX).
    suggestions: Mapped[list[dict[str, Any]]] = mapped_column(
        pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    refreshed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # The SHA-256 of what the run that stored them read: an automatic refresh over the same
    # input is skipped.
    basis: Mapped[str | None] = mapped_column(sa.Text)
    # The run asked and not ended here yet; null once its end is recorded.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    asked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # The last automatic run (one a day at most), and how far the tick has read the triggers.
    auto_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    seen_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # "More ideas" clicks on the UTC day `asks_on`.
    asks_on: Mapped[date | None] = mapped_column(sa.Date)
    asks: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    # Why the last ask gave nothing (a code of titles.ERRORS, or `nothing_new` of titles.NOTES);
    # null after an answer that added ideas.
    error: Mapped[str | None] = mapped_column(sa.Text)
    # The titles the person said are not for them ("Not for me"), the last titles.DISMISSED_MAX.
    dismissed: Mapped[list[str]] = mapped_column(
        pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    # What the person wrote to the agent, `[{text, at}]`, the last titles.MESSAGES_MAX.
    messages: Mapped[list[dict[str, Any]]] = mapped_column(
        pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )


class ProfileAbout(Base):
    """The person's "About you" document, the Markdown they write, and what the model read in its
    last part (app/profile/about.py). Not a section: no revision, never exported; what it changes
    in the profile goes through the sections' own saves. Migration 0034."""

    __tablename__ = "profile_about"
    __table_args__ = (
        sa.CheckConstraint("char_length(text) <= 300000", name="text_length"),
        sa.CheckConstraint("version >= 0", name="version_not_negative"),
        sa.CheckConstraint("jsonb_typeof(parts) = 'object'", name="parts_is_object"),
        sa.CheckConstraint("dones >= 0", name="dones_not_negative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Counts the saves of the text: a save sends the version it was based on (two tabs).
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    # By part key: {applied: the digest of the part last in step with the profile, basis: the
    # digest of what the profile held of it then, blocks: the fact block titles it holds}.
    parts: Mapped[dict[str, Any]] = mapped_column(
        pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    # The last Done: the part as it was sent, its run, and once read the checked changes.
    interpretation: Mapped[dict[str, Any] | None] = mapped_column(pg.JSONB)
    # The last Apply, until the next one: what each section held before, and after.
    undo: Mapped[dict[str, Any] | None] = mapped_column(pg.JSONB)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    # Done clicks that called the model on the UTC day `dones_on`.
    dones_on: Mapped[date | None] = mapped_column(sa.Date)
    dones: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
