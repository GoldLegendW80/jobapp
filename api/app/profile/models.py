"""Tables of the profile module: profiles (one row per user) and profile_revisions (append-only).

`profiles.sections` holds one JSONB document per section (identity, experience, facts, skills,
search, answers), each validated by its Pydantic model in `schemas.py` before it is written.
`revision` counts saves: every save of a section increments it by one and appends the saved
section to `profile_revisions` under that number, so any past version can be shown or restored.
The photo lives in its own columns (task P3), never in the JSON.
Migration: alembic/versions/0003_profile.py, which must stay in step with this file
(`alembic check` in the tests compares them).
"""

import uuid
from datetime import datetime
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
    photo: Mapped[bytes | None] = mapped_column(pg.BYTEA)
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
