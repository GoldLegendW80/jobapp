"""Reading and saving a user's profile, with revisions and optimistic concurrency.

Every function takes the caller's `user_id` and touches that user's rows only: there is no
function that reads a profile or a revision by anything else. A profile row is created lazily
(INSERT ... ON CONFLICT DO NOTHING) the first time it is read or saved, so a new account needs
no setup step and two first requests at once never collide.

A save locks the profile row (SELECT ... FOR UPDATE) before comparing `base_revision` with the
stored revision, so two concurrent saves based on the same revision are serialised: the first
increments the revision and appends its profile_revisions row, the second then sees the new
revision and is refused as stale. The unique (user_id, revision) constraint is a second guard.
An import takes the same lock and writes all six sections at once, one revision each.
The photo given to an import is stored as received (type sniffed by the caller); task P3 adds
the re-encoding that strips metadata.
The caller owns the transaction and commits it. Nothing here logs profile contents.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.profile.models import Profile, ProfileRevision
from app.profile.schemas import SECTION_MODELS, SECTIONS

REVISIONS_PAGE_MAX = 100


class StaleRevisionError(Exception):
    """The save was based on an older (or unknown) revision. Carries the current profile."""

    def __init__(self, profile: Profile) -> None:
        super().__init__("stale base revision")
        self.profile = profile


def _ensure(db: Session, user_id: uuid.UUID, now: datetime) -> None:
    db.execute(
        insert(Profile)
        .values(user_id=user_id, sections={}, revision=0, updated_at=now)
        .on_conflict_do_nothing(index_elements=[Profile.user_id])
    )


def get_profile(db: Session, user_id: uuid.UUID, now: datetime) -> Profile:
    """The user's profile, created empty on first read."""
    _ensure(db, user_id, now)
    profile = db.execute(
        sa.select(Profile)
        .where(Profile.user_id == user_id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    return profile


def _locked(db: Session, user_id: uuid.UUID, now: datetime) -> Profile:
    _ensure(db, user_id, now)
    return db.execute(
        sa.select(Profile)
        .where(Profile.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def sections_view(profile: Profile) -> dict[str, dict[str, Any] | None]:
    """Every section by name, null for one never saved."""
    stored = profile.sections or {}
    return {name: stored.get(name) for name in SECTIONS}


def save_section(
    db: Session,
    user_id: uuid.UUID,
    section: str,
    data: dict[str, Any],
    base_revision: int,
    now: datetime,
) -> Profile:
    """Replace one section with already validated `data` and record the revision.

    Raises StaleRevisionError (with the current profile, still locked) when `base_revision`
    is not the stored revision.
    """
    if section not in SECTIONS:
        raise ValueError(f"unknown section {section!r}")
    profile = _locked(db, user_id, now)
    if profile.revision != base_revision:
        raise StaleRevisionError(profile)
    profile.sections = {**(profile.sections or {}), section: data}
    profile.revision = profile.revision + 1
    profile.updated_at = now
    db.add(
        ProfileRevision(
            user_id=user_id,
            revision=profile.revision,
            section=section,
            data=data,
            created_at=now,
        )
    )
    db.flush()
    return profile


def import_profile(
    db: Session,
    user_id: uuid.UUID,
    sections: dict[str, dict[str, Any]],
    photo: tuple[bytes, str] | None,
    base_revision: int | None,
    now: datetime,
) -> Profile:
    """Replace all six sections at once with already validated data, one revision each (in
    SECTIONS order), under the same row lock as a save, in the caller's transaction.

    `photo` is (image bytes, mime), already sniffed; None leaves the stored photo as it is.
    With a `base_revision` that is not the stored one, raises StaleRevisionError; without one
    the import is applied after any save in progress (serialised by the lock).
    """
    if set(sections) != set(SECTIONS):
        raise ValueError("an import replaces exactly the six sections")
    profile = _locked(db, user_id, now)
    if base_revision is not None and profile.revision != base_revision:
        raise StaleRevisionError(profile)
    revision = profile.revision
    for name in SECTIONS:
        revision += 1
        db.add(
            ProfileRevision(
                user_id=user_id,
                revision=revision,
                section=name,
                data=sections[name],
                created_at=now,
            )
        )
    profile.sections = {name: sections[name] for name in SECTIONS}
    profile.revision = revision
    profile.updated_at = now
    if photo is not None:
        profile.photo, profile.photo_mime = photo
    db.flush()
    return profile


def export_sections(profile: Profile) -> dict[str, dict[str, Any]]:
    """Every section in its canonical stored form (a section never saved is its empty form),
    keys in model order: Postgres JSONB does not keep the order they were written in."""
    stored = profile.sections or {}
    return {
        name: SECTION_MODELS[name].model_validate(stored.get(name, {})).model_dump(mode="json")
        for name in SECTIONS
    }


@dataclass(frozen=True)
class RevisionPage:
    items: list[ProfileRevision]
    next_before: int | None


def list_revisions(
    db: Session,
    user_id: uuid.UUID,
    section: str | None = None,
    limit: int = 20,
    before: int | None = None,
) -> RevisionPage:
    """The user's revisions, newest first, `limit` at a time; `before` (exclusive) pages back."""
    limit = max(1, min(limit, REVISIONS_PAGE_MAX))
    query = sa.select(ProfileRevision).where(ProfileRevision.user_id == user_id)
    if section is not None:
        query = query.where(ProfileRevision.section == section)
    if before is not None:
        query = query.where(ProfileRevision.revision < before)
    rows = list(
        db.execute(query.order_by(ProfileRevision.revision.desc()).limit(limit + 1)).scalars()
    )
    more = len(rows) > limit
    items = rows[:limit]
    return RevisionPage(items=items, next_before=items[-1].revision if more else None)


def get_revision(db: Session, user_id: uuid.UUID, revision: int) -> ProfileRevision | None:
    """One past save of this user, or None (whether it does not exist or is someone else's)."""
    return db.execute(
        sa.select(ProfileRevision).where(
            ProfileRevision.user_id == user_id, ProfileRevision.revision == revision
        )
    ).scalar_one_or_none()
