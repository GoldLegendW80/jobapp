"""Saved searches (jobs J32, SPEC-jobs "Saved searches"): the person's other searches beside the
main one. The main search is the profile's search section, where it always was: nothing is moved
or copied, and the api lists it first with the id `main`.

A saved search holds the keys of one search (CRITERIA), checked by the profile's own search
schema, so it can never hold a key or a value the main search could not, and its own schedule
(days and times; the time zone is the person's). The rest of what a sweep reads stays the
person's, in the profile, for every search (PERSON): never apply rules, minimum fit, seniority,
industries, company types, pay, documents, each website's permission, how LinkedIn is searched,
the time zone. A sweep of a saved search reads its criteria over the person's settings
(`merged`), frozen at its start (sweep.freeze_scope); permissions are read live, as for the main
search. A saved search lists only websites whose permission the person gave (`unpermitted`).

The line: one sweep runs per person. A run asked while one runs waits as a `sweep_requests` row,
one per search (a second ask answers the same row), of a saved search or of the main search, by
hand or by a schedule slot that met the running sweep (`add_request`). The oldest starts when the
sweep before it ends, or at the next schedule tick (sweep.start_next). A run taken back that holds
a slot is kept, marked `taken_back_at`, so its slot stays taken until it leaves its window
(`take_back`, `forget_taken_back`).
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Annotated, Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.identity.models import User
from app.jobs import criteria_badge
from app.jobs.models import SavedSearch, Sweep, SweepRequest, SweepTrigger
from app.profile.models import Profile
from app.profile.schemas import (
    SCHEDULE_TIMES_MAX,
    WEEKDAYS,
    SearchSection,
    SweepSchedule,
    Weekday,
)

MAIN = "main"
MAIN_NAME = "Main search"
SAVED_MAX = 20
# The keys of one search: each saved search has its own; the main search's are its section's.
CRITERIA = (
    "title_include",
    "title_exclude",
    "location",
    "location_exclude",
    "remote_stance",
    "contract_types",
    "days",
    "interactive_sites",
    "company_boards",
    "job_feeds",
    "web_search",
    "interactive_instructions",
)
# The person's, for every search: read from the profile, never stored in a saved search.
PERSON = (
    "never_apply",
    "min_fit",
    "seniority",
    "industries",
    "company_types",
    "compensation",
    "documents",
    "browser_sources",
    "platform_consents",
    "linkedin_search",
    "alert_emails",
    "sweep_schedule",
)


class SavedSchedule(BaseModel):
    """A saved search's schedule: the shape of `search.sweep_schedule` without its time zone,
    which is the person's (`of`). Off by default."""

    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = False
    days: Annotated[list[Weekday], Field(min_length=1, max_length=7)] = Field(
        default_factory=lambda: list(WEEKDAYS)
    )
    times: Annotated[list[str], Field(min_length=1, max_length=SCHEDULE_TIMES_MAX)] = Field(
        default_factory=lambda: ["06:17"]
    )

    @model_validator(mode="after")
    def _as_the_main_one(self) -> "SavedSchedule":
        # The main schedule's own checks and order (week order, HH:MM, sorted, once each).
        normal = self.of("UTC")
        self.days, self.times = list(normal.days), list(normal.times)
        return self

    def of(self, timezone: str) -> SweepSchedule:
        return SweepSchedule(
            enabled=self.enabled, days=list(self.days), times=list(self.times), timezone=timezone
        )


class CriteriaError(ValueError):
    """The criteria of a saved search do not pass the profile's checks (the message names the
    place of the first error, never a value)."""


def criteria_of(data: Mapping[str, Any]) -> dict[str, Any]:
    """The stored criteria of one search: every key of CRITERIA, checked by the profile's search
    schema. A key outside CRITERIA (a person's setting or an unknown one) is refused."""
    extra = sorted(set(data) - set(CRITERIA))
    if extra:
        raise CriteriaError(f"criteria.{extra[0][:40]}: not a key of one search")
    try:
        search = SearchSection.model_validate(dict(data))
    except ValidationError as exc:
        first = exc.errors()[0]
        place = ".".join(str(part) for part in first["loc"])
        raise CriteriaError(f"criteria.{place}: {first['msg']}") from None
    dumped = search.model_dump(mode="json")
    return {name: dumped[name] for name in CRITERIA}


def criteria_from(search: SearchSection | None) -> dict[str, Any]:
    """The main search's criteria, as the api lists them beside the saved ones."""
    dumped = (search or SearchSection()).model_dump(mode="json")
    return {name: dumped[name] for name in CRITERIA}


def merged(person: Mapping[str, Any] | None, criteria: Mapping[str, Any]) -> dict[str, Any]:
    """The raw search section a sweep of a saved search reads: the person's settings with the
    saved criteria over them (every key of CRITERIA, so nothing of the main search's leaks)."""
    raw = {key: value for key, value in (person or {}).items() if key not in CRITERIA}
    raw.update({key: criteria[key] for key in CRITERIA if key in criteria})
    return raw


def schedule_of(row: SavedSearch) -> SavedSchedule:
    """The stored schedule; one that no longer validates reads as off."""
    try:
        return SavedSchedule.model_validate(row.schedule)
    except ValidationError:
        return SavedSchedule()


def unpermitted(
    db: Session, user_id: uuid.UUID, criteria: Mapping[str, Any], now: datetime
) -> list[str]:
    """The hosts of the ticked websites of `criteria` whose platform the person has not given
    permission to search (J17: given by ticking it in Find jobs and saving). A paused platform
    keeps its permission; a website of the person's own needs none."""
    from urllib.parse import urlsplit

    from app.browser import platform_policy
    from app.profile.schemas import InteractiveSite

    missing = []
    for raw in criteria.get("interactive_sites") or []:
        site = InteractiveSite.model_validate(raw)
        if not site.enabled:
            continue
        platform = site.effective_platform
        if platform not in platform_policy.PLATFORMS:
            platform = platform_policy.platform_of(site.url)
        if platform not in platform_policy.PLATFORMS:
            continue
        refused = platform_policy.refusal(db, user_id, platform, None, now)
        if refused is not None and not refused.endswith("_paused"):
            missing.append(urlsplit(site.url).hostname or site.url)
    return missing


# --- the store ----------------------------------------------------------------------------------


class LimitError(Exception):
    """The person has SAVED_MAX saved searches already."""


class NameTakenError(Exception):
    """Another of the person's searches has this name (case aside), or it is the main one's."""


class RevisionError(Exception):
    def __init__(self, revision: int) -> None:
        super().__init__("revision")
        self.revision = revision


def listed(db: Session, user_id: uuid.UUID) -> list[SavedSearch]:
    return list(
        db.execute(
            sa.select(SavedSearch)
            .where(SavedSearch.user_id == user_id)
            .order_by(SavedSearch.created_at, SavedSearch.id)
        ).scalars()
    )


def get(
    db: Session, user_id: uuid.UUID, search_id: uuid.UUID, *, lock: bool = False
) -> SavedSearch | None:
    query = sa.select(SavedSearch).where(
        SavedSearch.user_id == user_id, SavedSearch.id == search_id
    )
    return db.execute(query.with_for_update() if lock else query).scalar_one_or_none()


def _lock_person(db: Session, user_id: uuid.UUID) -> None:
    key = f"saved-searches:{user_id}"
    db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(key, 0))))


def _name_taken(db: Session, user_id: uuid.UUID, name: str, but: uuid.UUID | None = None) -> bool:
    if name.casefold() == MAIN_NAME.casefold():
        return True
    query = sa.select(sa.literal(True)).where(
        SavedSearch.user_id == user_id, sa.func.lower(SavedSearch.name) == sa.func.lower(name)
    )
    if but is not None:
        query = query.where(SavedSearch.id != but)
    return bool(db.execute(query.limit(1)).scalar())


def _flushed(db: Session, row: SavedSearch) -> SavedSearch:
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        if "ix_saved_searches_user_id_name" not in str(exc.orig):
            raise
        raise NameTakenError() from None
    return row


def create(
    db: Session,
    user_id: uuid.UUID,
    name: str,
    criteria: Mapping[str, Any],
    schedule: SavedSchedule,
) -> SavedSearch:
    """A new saved search; the caller commits. Raises LimitError or NameTakenError."""
    _lock_person(db, user_id)
    count = db.execute(
        sa.select(sa.func.count()).select_from(SavedSearch).where(SavedSearch.user_id == user_id)
    ).scalar_one()
    if count >= SAVED_MAX:
        raise LimitError()
    if _name_taken(db, user_id, name):
        raise NameTakenError()
    row = SavedSearch(
        user_id=user_id,
        name=name,
        criteria=criteria_of(criteria),
        schedule=schedule.model_dump(mode="json"),
        revision=1,
    )
    # Stamped by the database (server defaults): the list's order is the order of creation.
    return _flushed(db, row)


def change(
    db: Session,
    user_id: uuid.UUID,
    search_id: uuid.UUID,
    revision: int,
    name: str,
    criteria: Mapping[str, Any],
    schedule: SavedSchedule,
) -> SavedSearch | None:
    """The saved search replaced, its revision one more, and the badges of its open postings
    computed again against its new criteria (jobs J30); None when it is not the person's. Raises
    RevisionError (with the stored revision) when `revision` is not the stored one, and
    NameTakenError. The caller commits."""
    row = get(db, user_id, search_id, lock=True)
    if row is None:
        return None
    if row.revision != revision:
        raise RevisionError(row.revision)
    if _name_taken(db, user_id, name, but=row.id):
        raise NameTakenError()
    row.name, row.criteria = name, criteria_of(criteria)
    row.schedule = schedule.model_dump(mode="json")
    row.revision, row.updated_at = row.revision + 1, sa.func.now()
    row = _flushed(db, row)
    criteria_badge.refresh(db, user_id, row.id, criteria_badge.Criteria.read(row.criteria))
    return row


def delete(db: Session, user_id: uuid.UUID, search_id: uuid.UUID) -> bool:
    """Delete it, and its waiting request (cascade); its sweeps keep their name (frozen in their
    scope) and lose the reference (set null), and its postings follow the main search, their
    badges computed again against it (jobs J30). The profile row is locked first, the order in
    which the line's start (sweep.start_next) takes its locks. The caller commits."""
    db.execute(sa.select(Profile.user_id).where(Profile.user_id == user_id).with_for_update())
    row = get(db, user_id, search_id, lock=True)
    if row is None:
        return False
    db.delete(row)
    db.flush()
    criteria_badge.refresh(db, user_id, None, criteria_badge.criteria_for(db, user_id, None))
    return True


def last_runs(db: Session, user_id: uuid.UUID) -> dict[uuid.UUID | None, sa.Row[Any]]:
    """The newest sweep of each of the person's searches, in one query (jobs J33b): the main
    search's under None (the sweeps that froze no saved search, as service.added_by_search reads
    them), a saved search's under its id; a deleted search's sweeps are no search's. Each row has
    `id`, `status`, `started_at`, `finished_at` and `new` (the stats' count, as stored)."""
    frozen = sa.func.jsonb_typeof(Sweep.stats[("search_scope", "saved_search", "name")])
    main = sa.and_(Sweep.saved_search_id.is_(None), sa.func.coalesce(frozen, "") != "string")
    owner = sa.case((main, sa.literal(MAIN)), else_=sa.cast(Sweep.saved_search_id, sa.Text))
    ranked = (
        sa.select(
            Sweep.saved_search_id,
            Sweep.id,
            Sweep.status,
            Sweep.started_at,
            Sweep.finished_at,
            Sweep.stats["new"].label("new"),
            sa.func.row_number()
            .over(partition_by=owner, order_by=(Sweep.started_at.desc(), Sweep.id.desc()))
            .label("rank"),
        )
        .where(Sweep.user_id == user_id, sa.or_(main, Sweep.saved_search_id.is_not(None)))
        .subquery()
    )
    rows = db.execute(sa.select(ranked).where(ranked.c.rank == 1)).all()
    return {row.saved_search_id: row for row in rows}


# --- the line -----------------------------------------------------------------------------------


WAITS = SweepRequest.taken_back_at.is_(None)


def waiting(db: Session, user_id: uuid.UUID) -> list[SweepRequest]:
    """The person's requests in the order they start."""
    return list(
        db.execute(
            sa.select(SweepRequest)
            .where(SweepRequest.user_id == user_id, WAITS)
            .order_by(SweepRequest.position)
        ).scalars()
    )


def place_of(line: Sequence[SweepRequest], request_id: uuid.UUID) -> int:
    """1 for the request that starts next."""
    return next(index for index, item in enumerate(line, 1) if item.id == request_id)


def request_of(
    db: Session, user_id: uuid.UUID, saved_search_id: uuid.UUID | None, *, lock: bool = False
) -> SweepRequest | None:
    query = sa.select(SweepRequest).where(
        SweepRequest.user_id == user_id,
        WAITS,
        SweepRequest.saved_search_id.is_(None)
        if saved_search_id is None
        else SweepRequest.saved_search_id == saved_search_id,
    )
    return db.execute(query.with_for_update() if lock else query).scalar_one_or_none()


def add_request(
    db: Session,
    user_id: uuid.UUID,
    saved_search_id: uuid.UUID | None,
    trigger: SweepTrigger,
    now: datetime,
    slot: str | None = None,
) -> SweepRequest:
    """The search's request in the line, made or the one already waiting. A schedule slot joins
    a waiting request that holds none, which then starts as that slot's sweep, so the slot is
    never started twice nor lost. The caller holds the person's profile lock and commits."""
    for _ in range(2):
        found = request_of(db, user_id, saved_search_id, lock=True)
        if found is not None:
            if slot is not None and found.schedule_slot is None:
                found.schedule_slot, found.trigger = slot, SweepTrigger.schedule
                db.flush()
            return found
        row = SweepRequest(
            user_id=user_id,
            saved_search_id=saved_search_id,
            trigger=SweepTrigger.schedule if slot is not None else trigger,
            schedule_slot=slot,
            requested_at=now,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            return row
        except IntegrityError as exc:
            if "ix_sweep_requests_one_" not in str(exc.orig):
                raise
    raise RuntimeError("the request of this search could not be read")


def take_back(
    db: Session, user_id: uuid.UUID, saved_search_id: uuid.UUID | None, now: datetime
) -> bool:
    """Take back the search's waiting request; False when none waits. One that holds a schedule
    slot is kept as taken back, so the next ticks do not put its slot in the line again
    (sweep.slot_taken); the others go. The caller commits."""
    found = request_of(db, user_id, saved_search_id, lock=True)
    if found is None:
        return False
    if found.schedule_slot is None:
        db.delete(found)
    else:
        found.taken_back_at = now
    db.flush()
    return True


def forget_taken_back(db: Session, now: datetime, window: timedelta) -> int:
    """Remove the runs taken back more than `window` ago: a slot is taken back after its instant,
    so it has left the tick's window by then. The caller commits."""
    gone = db.execute(
        sa.delete(SweepRequest)
        .where(SweepRequest.taken_back_at < now - window)
        .execution_options(synchronize_session=False)
    )
    return int(gone.rowcount or 0)


def users_waiting(db: Session) -> list[uuid.UUID]:
    """The active people with a run waiting (a disabled account's runs never start)."""
    return list(
        db.execute(
            sa.select(SweepRequest.user_id)
            .join(User, User.id == SweepRequest.user_id)
            .where(WAITS, User.is_active.is_(True))
            .distinct()
            .order_by(SweepRequest.user_id)
        ).scalars()
    )
