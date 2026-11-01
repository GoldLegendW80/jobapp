"""Reading and saving a user's profile, with revisions and optimistic concurrency.

Every function takes the caller's `user_id` and touches that user's rows only: there is no
function that reads a profile or a revision by anything else. A profile row is created lazily
(INSERT ... ON CONFLICT DO NOTHING) the first time it is read or saved, so a new account needs
no setup step and two first requests at once never collide.

A save locks the profile row (SELECT ... FOR UPDATE) before comparing `base_revision` with the
stored revision, so two concurrent saves based on the same revision are serialised: the first
increments the revision and appends its profile_revisions row, the second then sees the new
revision and is refused as stale. The unique (user_id, revision) constraint is a second guard.
An import takes the same lock and writes all seven sections at once, one revision each.
`preferences.onboarding` belongs to the first login wizard (opportunities O5): only
`record_onboarding` and `record_onboarding_step` write it. A save that changes a date is
refused (ReservedFieldError); a key left out of the save, and the steps passed whatever the
save holds, keep their stored value (a page loaded before the steps existed, or before the last
step, still saves). An import keeps the stored value (the dates and the steps passed). A step
passed is no revision: it updates `updated_at` only, like the photo, so a second tab editing
the profile is not sent a 409 by each step of the wizard.
The photo is not a section: storing or removing it adds no revision, but updates `updated_at`.
It is only ever stored as a `photo.Photo`, the output of `photo.normalize` (re-encoded, metadata
stripped), by an upload and by an import alike.
The LinkedIn consent (browser B10) cannot be written through a section: on the `search`
section's `linkedin` entry, `enabled`, `accepted_at`, `text_version` and `interactive_only` keep
their stored values
(off and none when nothing is stored) on every save and import, unless the caller passes
`linkedin_consent=True`, which only PUT /api/browser/linkedin does (it shows the sentence and
records the person's accept). The words and the location of the entry are saved as sent, and an
entry left out removes it, which turns LinkedIn off, unless it is a permission-only row.
Permission-only LinkedIn rows are retained so an ordinary save cannot clear their reader intent.
Free-Work consent follows the same dedicated-route rule, and goes further: once stored, its words
and location also keep their stored values on every other save and import (a generic save that
creates the entry keeps them as sent, off and unaccepted), and an omitted Free-Work entry is
retained, so generic Search saves and imports cannot remove or rewrite its protected settings.
Restoring it must still satisfy the Search rules, including the ten-source limit, before anything
is written.
The separate `platform_consents` map belongs to the browser: generic saves and imports keep it,
and a dedicated platform writer can update only its own entry under the same revision lock.
Two kinds of search field are kept when a save or an import leaves the key out
(`schemas.KEPT_WHEN_OMITTED`: the interactive website fields and the unwanted places,
`location_exclude`): a page loaded before the field existed, an export made before it, or a
History revision restored from before it keeps the stored value, and a key sent with a list,
the empty list included, replaces it.
The caller owns the transaction and commits it. Nothing here logs profile contents.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.profile.models import Profile, ProfileRevision
from app.profile.photo import Photo
from app.profile.schemas import (
    KEPT_WHEN_OMITTED,
    PLATFORM_CONSENT_KINDS,
    SECTION_MODELS,
    SECTIONS,
    WIZARD_STEPS,
    validate_section,
    without_deleted_sources,
    without_starter,
)

REVISIONS_PAGE_MAX = 100
logger = logging.getLogger(__name__)


ONBOARDING_FIELDS = ("completed_at", "dismissed_at")


class ReservedFieldError(Exception):
    """A save tried to change a field only the app writes (preferences.onboarding)."""


class PreservedSearchError(Exception):
    """Preserving protected settings would break a Search rule, before any profile write."""

    def __init__(self, error: ValidationError) -> None:
        super().__init__("preserved search does not validate")
        self.error = error


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


def get_profile(db: Session, user_id: uuid.UUID, now: datetime, *, lock: bool = False) -> Profile:
    """The user's profile, created empty on first read."""
    if lock:
        return _locked(db, user_id, now)
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
    view = {name: stored.get(name) for name in SECTIONS}
    # What was deleted on 2026-10-28 is not shown, though a section saved before still holds it.
    view["search"] = without_deleted_sources(view["search"])
    view["preferences"] = without_starter(view["preferences"])
    return view


BROWSER_FIELDS = ("enabled", "accepted_at", "text_version", "interactive_only")
LINKEDIN = "linkedin"
PROTECTED_SOURCES = frozenset({LINKEDIN, "freework"})


def _keep_consent(
    stored: dict[str, Any] | None, data: dict[str, Any], trusted: str | None = None
) -> dict[str, Any]:
    """Keep protected browser fields; only each kind's dedicated route may change them."""
    consents = dict((stored or {}).get("platform_consents") or {})
    if trusted in PLATFORM_CONSENT_KINDS and trusted in data.get("platform_consents", {}):
        consents[trusted] = data["platform_consents"][trusted]
    # Old editors and imports cannot accidentally erase the saved search fields they do not know
    # (KEPT_WHEN_OMITTED): a key left out keeps its stored value, a key sent replaces it.
    data = {
        **{
            name: (stored or {})[name]
            for name in KEPT_WHEN_OMITTED
            if name not in data and name in (stored or {})
        },
        **data,
        "platform_consents": consents,
    }
    if not consents:
        # No dedicated decision yet: store no map at all (schemas._stored_section).
        del data["platform_consents"]
    alerts = data.get("alert_emails")
    if isinstance(alerts, dict) and not any(alerts.values()):
        # Every alert e-mail off, sent or kept: stored as absent (read as off), so a section
        # without one stays readable by the release before them.
        del data["alert_emails"]
    if data.get("job_feeds") == []:
        # Sent empty, it replaced the stored list (above); stored only once a feed is ticked, as
        # the release before this one refuses the key.
        del data["job_feeds"]
    if data.get("vie_businessfrance") is False:
        # Off, sent or kept: stored as absent (read as off), as the release before this one
        # refuses the key.
        del data["vie_businessfrance"]
    if "company_boards" in data and data["company_boards"] is None:
        # Sent as null, it replaced the stored list (above). Null means never saved, which is the
        # key's absence: never stored as null. An empty list is a choice and is stored.
        del data["company_boards"]
    sources = data.get("browser_sources")
    if not isinstance(sources, list):
        return data
    previous = {
        s["kind"]: s
        for s in (stored or {}).get("browser_sources") or []
        if isinstance(s, dict) and s.get("kind") in PROTECTED_SOURCES
    }
    restored = [
        before
        for kind, before in previous.items()
        if (kind == "freework" or before.get("interactive_only") is True)
        and not any(isinstance(source, dict) and source.get("kind") == kind for source in sources)
    ]
    if restored:
        sources = [*sources, *restored]
    kept = []
    for source in sources:
        if not isinstance(source, dict) or source.get("kind") not in PROTECTED_SOURCES - {trusted}:
            kept.append(source)
            continue
        before = previous.get(source["kind"], {})
        browser = {key: before.get(key) for key in BROWSER_FIELDS}
        browser["enabled"] = browser["enabled"] is True
        browser["interactive_only"] = browser["interactive_only"] is True
        if (browser["accepted_at"] is None) != (browser["text_version"] is None):
            browser.update(enabled=False, accepted_at=None, text_version=None)
        if source["kind"] == "freework" and source["kind"] in previous:
            # A stored Free-Work entry's words change through its own route only: a stale draft
            # (a 409 then Keep editing, an old export) must not undo a newer dedicated save.
            browser |= {key: before.get(key) for key in ("query", "location")}
        kept.append({**source, **browser})
    result = {**data, "browser_sources": kept}
    try:
        return validate_section("search", result)
    except ValidationError as exc:
        raise PreservedSearchError(exc) from None


def save_section(
    db: Session,
    user_id: uuid.UUID,
    section: str,
    data: dict[str, Any],
    base_revision: int,
    now: datetime,
    *,
    linkedin_consent: bool = False,
    platform_consent: str | None = None,
    onboarding_sent: frozenset[str] | None = None,
) -> Profile:
    """Replace one section with already validated `data` and record the revision.

    Raises StaleRevisionError (with the current profile, still locked) when `base_revision`
    is not the stored revision, and ReservedFieldError when a `preferences` save changes the
    onboarding dates. `onboarding_sent` names the onboarding keys the client sent (None: all of
    them): a key left out keeps its stored value, and so do the steps passed. The LinkedIn
    consent of a `search` section is kept as stored unless `linkedin_consent`, and the Free-Work
    entry (consent, words and location) unless `platform_consent="freework"` (the module
    docstring).
    """
    if section not in SECTIONS:
        raise ValueError(f"unknown section {section!r}")
    profile = _locked(db, user_id, now)
    if profile.revision != base_revision:
        raise StaleRevisionError(profile)
    if platform_consent not in {None, "freework", *PLATFORM_CONSENT_KINDS}:
        raise ValueError("unknown platform consent")
    if linkedin_consent and platform_consent is not None:
        raise ValueError("one dedicated consent route per save")
    if section == "search":
        trusted = LINKEDIN if linkedin_consent else platform_consent
        data = _keep_consent((profile.sections or {}).get("search"), data, trusted)
    ignored_steps = False
    if section == "preferences":
        submitted = data.get("onboarding") or {}
        data = _keep_onboarding(profile, data, onboarding_sent)
        ignored_steps = (
            "steps_passed" in submitted
            and (onboarding_sent is None or "steps_passed" in onboarding_sent)
            and submitted["steps_passed"] != data["onboarding"]["steps_passed"]
        )
        if data.get("onboarding") != _onboarding(profile):
            raise ReservedFieldError("onboarding")
    saved = _write(db, profile, section, data, now)
    if ignored_steps:
        logger.info("profile_onboarding_steps_ignored")
    return saved


def _onboarding(profile: Profile) -> dict[str, Any]:
    """The stored onboarding part, in its stored form (null dates and no step when never
    written)."""
    stored = (profile.sections or {}).get("preferences")
    model = SECTION_MODELS["preferences"]
    return model.model_validate(stored or {}).model_dump(mode="json")["onboarding"]


def _keep_onboarding(
    profile: Profile, data: dict[str, Any], sent: frozenset[str] | None
) -> dict[str, Any]:
    """`data` (a validated preferences section) with the stored steps passed, and the stored
    value of every onboarding key the client left out."""
    stored = _onboarding(profile)
    given = data.get("onboarding") if isinstance(data.get("onboarding"), dict) else {}
    kept = {
        key: given[key] if key in given and (sent is None or key in sent) else value
        for key, value in stored.items()
        if key != "steps_passed"
    }
    return {**data, "onboarding": {**kept, "steps_passed": stored["steps_passed"]}}


def _write(
    db: Session, profile: Profile, section: str, data: dict[str, Any], now: datetime
) -> Profile:
    user_id = profile.user_id
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
    photo: Photo | None,
    base_revision: int | None,
    now: datetime,
) -> Profile:
    """Replace all seven sections at once with already validated data, one revision each (in
    SECTIONS order), under the same row lock as a save, in the caller's transaction.

    `photo` is the normalized photo of the file; None leaves the stored photo as it is.
    With a `base_revision` that is not the stored one, raises StaleRevisionError; without one
    the import is applied after any save in progress (serialised by the lock).
    """
    if set(sections) != set(SECTIONS):
        raise ValueError("an import replaces exactly the seven sections")
    profile = _locked(db, user_id, now)
    if base_revision is not None and profile.revision != base_revision:
        raise StaleRevisionError(profile)
    # An imported file never carries a LinkedIn acceptance (another account's, or an old one),
    # nor onboarding dates (the stored ones stay).
    sections = {
        **sections,
        "search": _keep_consent((profile.sections or {}).get("search"), sections["search"]),
        "preferences": {**sections["preferences"]},
    }
    sections["preferences"]["onboarding"] = _onboarding(profile)
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
        profile.photo, profile.photo_mime = photo.data, photo.mime
    db.flush()
    return profile


def record_onboarding(db: Session, user_id: uuid.UUID, field: str, now: datetime) -> Profile:
    """Record `now` as the wizard's `completed_at` or `dismissed_at` (opportunities O5), one
    revision of `preferences`, whatever revision the caller loaded (the person never edits
    these dates, so there is nothing to conflict with)."""
    if field not in ONBOARDING_FIELDS:
        raise ValueError(f"unknown onboarding field {field!r}")
    profile = _locked(db, user_id, now)
    stored = (profile.sections or {}).get("preferences") or {}
    model = SECTION_MODELS["preferences"]
    data = model.model_validate(stored).model_dump(mode="json")
    data["onboarding"][field] = now.astimezone(UTC).isoformat()
    data = model.model_validate(data).model_dump(mode="json")
    return _write(db, profile, "preferences", data, now)


def record_onboarding_step(db: Session, user_id: uuid.UUID, step: str, now: datetime) -> Profile:
    """Add `step` to the wizard's `steps_passed` (opportunities O5), under the profile row lock.
    No revision: the steps are the wizard's bookkeeping, never the person's edit, so a tab that
    loaded the profile before the step still saves on its revision (a save keeps the stored
    steps). A step already passed, or any step once the wizard is finished (read under the
    lock), changes nothing."""
    if step not in WIZARD_STEPS:
        raise ValueError(f"unknown wizard step {step!r}")
    profile = _locked(db, user_id, now)
    stored = (profile.sections or {}).get("preferences") or {}
    model = SECTION_MODELS["preferences"]
    data = model.model_validate(stored).model_dump(mode="json")
    if data["onboarding"]["completed_at"] is not None or step in data["onboarding"]["steps_passed"]:
        return profile
    data["onboarding"]["steps_passed"] = [*data["onboarding"]["steps_passed"], step]
    data = model.model_validate(data).model_dump(mode="json")
    profile.sections = {**(profile.sections or {}), "preferences": data}
    profile.updated_at = now
    db.flush()
    return profile


# --- photo --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PhotoState:
    mime: str
    etag: str


def photo_state(db: Session, user_id: uuid.UUID) -> PhotoState | None:
    """The type and ETag of the user's photo, hashed by Postgres (the image is not transferred),
    or None without one."""
    row = db.execute(
        sa.select(Profile.photo_mime, sa.func.encode(sa.func.sha256(Profile.photo), "hex")).where(
            Profile.user_id == user_id, Profile.photo.is_not(None)
        )
    ).one_or_none()
    return None if row is None else PhotoState(mime=row[0], etag=f'"{row[1]}"')


def get_photo(db: Session, user_id: uuid.UUID) -> tuple[bytes, str] | None:
    """The user's photo (bytes, mime), or None without one."""
    row = db.execute(
        sa.select(Profile.photo, Profile.photo_mime).where(
            Profile.user_id == user_id, Profile.photo.is_not(None)
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])


def set_photo(db: Session, user_id: uuid.UUID, photo: Photo, now: datetime) -> Profile:
    """Store a normalized photo, replacing any previous one, under the profile row lock."""
    profile = _locked(db, user_id, now)
    profile.photo, profile.photo_mime = photo.data, photo.mime
    profile.updated_at = now
    db.flush()
    return profile


def delete_photo(db: Session, user_id: uuid.UUID, now: datetime) -> bool:
    """Remove the user's photo; False when there was none (nothing changes, not even
    updated_at, so a repeated delete is a no-op)."""
    result = db.execute(
        sa.update(Profile)
        .where(Profile.user_id == user_id, Profile.photo_mime.is_not(None))
        .values(photo=None, photo_mime=None, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)


def export_sections(profile: Profile) -> dict[str, dict[str, Any]]:
    """Every section in its canonical stored form (a section never saved is its empty form),
    keys in model order: Postgres JSONB does not keep the order they were written in. A search
    saved without the interactive keys exports without them, as GET /api/profile shows it, so an
    export imported back reads as unchanged."""
    stored = profile.sections or {}
    return {name: validate_section(name, stored.get(name, {})) for name in SECTIONS}


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
