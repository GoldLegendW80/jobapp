"""Per-person platform consent and durable sweep history (opportunities O7b, browser B14).

Consent is read from the person's current `search` section: the Free-Work entry of
`browser_sources`, and `platform_consents` for Indeed and Welcome to the Jungle. History is
the existing sweep source entries, no new table: `opened_at` (the first page of a platform
was about to open), `checkpoint_at` (a checkpoint page was met) and `platform` (the
platform code classified from the address, so a custom label still counts). Every matching
entry of every sweep of the person counts, not only the first one. The B14 writers
`reserve` and `record_wall` lock the sweep row, write these fields and commit before the
caller goes on; `jobs.sweep._stats_json` keeps them against stale progress. A checkpoint
pauses the platform for PAUSE from the moment it was met; no profile save, import or switch
removes it, because it lives in the sweep history and not in the profile.
"""

import copy
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.orm import Session

from app.jobs import sweep
from app.jobs.models import Sweep, SweepStatus
from app.profile.schemas import BrowserSource, PlatformConsent

TEXT = (
    "This site's terms forbid automated use; your account may be limited or closed; "
    "the app keeps to a person's pace, but the risk is yours."
)
TEXT_VERSION = 1
PAUSE = timedelta(days=7)


@dataclass(frozen=True)
class Platform:
    name: str
    text: str
    text_version: int
    terms_url: str | None = None
    checked_on: str | None = None


RISK_TEXT = (
    "Automated browsing may result in account restrictions or closure. "
    "Enable it only if you accept this risk."
)
PLATFORMS = {
    # Its terms allow personal browsing only and forbid extracting its offers (checked on
    # 2026-10-20).
    "freework": Platform(
        "Free-Work", TEXT, TEXT_VERSION, "https://www.free-work.com/fr/terms", "2026-10-20"
    ),
    "indeed": Platform("Indeed", RISK_TEXT, 1),
    "welcome_to_the_jungle": Platform("Welcome to the Jungle", RISK_TEXT, 1),
}
KINDS = frozenset(PLATFORMS)


def metadata(kind: str) -> Platform:
    item = PLATFORMS[kind]
    return replace(item, text=TEXT, text_version=TEXT_VERSION) if kind == "freework" else item


@dataclass(frozen=True)
class State:
    source: BrowserSource | PlatformConsent | None
    accepted: bool
    paused_until: datetime | None = None
    last_sweep_at: datetime | None = None
    needs_signin: bool = False

    @property
    def enabled(self) -> bool:
        return self.source is not None and self.source.enabled

    @property
    def allowed(self) -> bool:
        return self.enabled and self.accepted and self.paused_until is None


# The platform codes whose history these queries read; a code comes from this set only, never
# from a page or an unchecked route parameter, so the paths below are built from constants.
HISTORY_KINDS = KINDS | {"linkedin"}
SIGNIN = "platform_signin"  # the sources' code of a sign in page on a protected platform


def _path(kind: str) -> str:
    if kind not in HISTORY_KINDS:
        raise ValueError(kind)
    return f'$.sources[*] ? (@.kind == "{kind}" || @.platform == "{kind}")'


def _entries(kind: str) -> Any:
    """Every source entry of a sweep that belongs to the platform, as a row set joined to the
    sweep (a function in FROM is lateral in PostgreSQL)."""
    return (
        sa.func.jsonb_path_query(Sweep.stats, sa.cast(_path(kind), JSONPATH))
        .table_valued(sa.column("value", JSONB), joins_implicitly=True)
        .render_derived(name=f"history_{kind}", with_types=False)
    )


def _at(value: Any, name: str) -> Any:
    return value[name].astext.cast(sa.DateTime(timezone=True))


def state(db: Session, user_id: uuid.UUID, kind: str, now: datetime | None = None) -> State:
    if kind not in KINDS:
        return State(None, False)
    now = now or datetime.now(UTC)
    search = sweep.user_search(db, user_id)
    source = None
    if search:
        source = (
            next((s for s in search.browser_sources if s.kind == kind), None)
            if kind == "freework"
            else search.platform_consents.get(kind)
        )
    accepted = bool(
        source
        and source.accepted_at is not None
        and source.text_version == metadata(kind).text_version
    )
    met = checkpoint_at(db, user_id, kind)
    found = _entries(kind)
    opened = _at(found.c.value, "opened_at")
    last = db.execute(
        sa.select(opened, found.c.value["error"].astext)
        .select_from(Sweep)
        .join(found, sa.true())
        .where(Sweep.user_id == user_id, opened.is_not(None))
        .order_by(opened.desc(), Sweep.id.desc())
        .limit(1)
    ).first()
    return State(
        source,
        accepted,
        met + PAUSE if met is not None and met + PAUSE > now else None,
        last[0] if last else None,
        bool(last and last[1] in (f"{kind}_signin", SIGNIN)),
    )


def checkpoint_at(db: Session, user_id: uuid.UUID, kind: str) -> datetime | None:
    """The latest checkpoint met on the platform in any entry of any of the person's sweeps."""
    found = _entries(kind)
    return db.scalar(
        sa.select(sa.func.max(_at(found.c.value, "checkpoint_at")))
        .select_from(Sweep)
        .join(found, sa.true())
        .where(Sweep.user_id == user_id)
    )


def allowed(db: Session, user_id: uuid.UUID, kind: str, now: datetime | None = None) -> bool:
    return refusal(db, user_id, kind, now) is None


def refusal(db: Session, user_id: uuid.UUID, kind: str, now: datetime | None = None) -> str | None:
    if kind not in KINDS:
        return "platform_off"
    current = state(db, user_id, kind, now)
    if not current.enabled or not current.accepted:
        return f"{kind}_off"
    return f"{kind}_paused" if current.paused_until is not None else None


def sweep_refusal(
    db: Session, user_id: uuid.UUID, kind: str, sweep_id: uuid.UUID | None, now: datetime
) -> str | None:
    """A sweep's refusal: consent or the 7 day pause (`refusal`). No daily limit (owner,
    2026-10-28: "i don't want limit on search by day"); `<kind>_daily` stays readable in older
    sweeps only."""
    del sweep_id  # every sweep is judged alike, whatever the person's sweeps of the day
    return refusal(db, user_id, kind, now)


# --- the B14 writers ------------------------------------------------------------------------------


def lock_platform(db: Session, user_id: uuid.UUID, kind: str) -> None:
    """Serialise the person's reservations of one platform across sweeps and workers, until the
    transaction ends, so a reservation reads consent and the pause with no other one between."""
    if kind not in HISTORY_KINDS:
        raise ValueError(kind)
    db.execute(
        sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(f"platform:{user_id}:{kind}")))
    )


def _locked_entries(
    db: Session,
    user_id: uuid.UUID,
    sweep_id: uuid.UUID,
    source_id: str,
    *,
    running: bool = True,
) -> tuple[Sweep, list[dict[str, Any]]] | None:
    """The person's sweep row, locked, and its entries of the source; None when the row is not
    the person's, has no such entry, or (with `running`) has stopped running."""
    row = db.get(Sweep, sweep_id, with_for_update=True)
    if row is None or row.user_id != user_id:
        return None
    if running and row.status is not SweepStatus.running:
        return None
    stats = copy.deepcopy(row.stats or {})
    entries = [
        e
        for e in stats.get("sources", [])
        if isinstance(e, dict) and e.get("source_id") == source_id
    ]
    if not entries:
        return None
    row.stats = stats
    return row, entries


def reserve(
    db: Session,
    user_id: uuid.UUID,
    sweep_id: uuid.UUID,
    source_id: str,
    kind: str,
    now: datetime,
    refusal_of: Callable[[Session], str | None],
) -> str | None:
    """Record that the source is about to open its first page of `kind`, under the platform lock
    and the sweep row's lock, after `refusal_of` (consent, the pause) read again
    under them. The caller commits before the first navigation. A sweep that is not running, or
    has no entry of the source, refuses: nothing opens without its durable record."""
    lock_platform(db, user_id, kind)
    refused = refusal_of(db)
    if refused is not None:
        return refused
    locked = _locked_entries(db, user_id, sweep_id, source_id)
    if locked is None:
        return "permission_unavailable"
    for item in locked[1]:
        item.setdefault("opened_at", now.isoformat())
        item["platform"] = kind
    db.flush()
    return None


def record_wall(
    db: Session,
    user_id: uuid.UUID,
    sweep_id: uuid.UUID,
    source_id: str,
    kind: str,
    code: str,
    now: datetime,
) -> None:
    """Record a checkpoint (`checkpoint_at`, the pause starts) or a sign in page met by the
    source, in every entry of the source; the caller commits before anything else. Whatever the
    row's status: a sweep reaped or cancelled while the source still reads keeps the wall, so
    the 7 day pause holds (the history reads every sweep of the person)."""
    locked = _locked_entries(db, user_id, sweep_id, source_id, running=False)
    if locked is None:
        return
    for item in locked[1]:
        item["platform"] = kind
        item.setdefault("opened_at", now.isoformat())
        item["error"] = code
        if code != SIGNIN and code != "linkedin_signin":
            item["checkpoint_at"] = now.isoformat()
    db.flush()
