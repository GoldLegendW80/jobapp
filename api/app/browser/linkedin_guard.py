"""Whether LinkedIn work may run for a person (browser B10, SPEC-browser decision 2: "Full use").

LinkedIn is a switch per person, off by default. Turning it on shows TEXT, which the person
accepts once; the acceptance is recorded with its date and TEXT_VERSION in the person's `search`
section (the `browser_sources` entry of kind `linkedin`: `enabled`, `accepted_at`,
`text_version`; PUT /api/browser/linkedin writes it). `allowed(db, user_id)` is true only with
the acceptance of the current version, the switch on and no pause active. Every LinkedIn path
calls it before it opens a LinkedIn page: the sweep (`sweep_check`, through the sources' Runner),
apply's Easy Apply (A16) and apply's profile work (A18a, A18b). A new version of the sentence
(TEXT_VERSION raised) makes every earlier acceptance stale: nothing runs until the person
accepts again.

The pause: a checkpoint or unusual activity page met by the sweep (sources/linkedin.py answers
`linkedin_checkpoint`) stops all LinkedIn work for PAUSE from the end of that sweep (from its
start while it still runs). It is read from the sweeps table (the sweep's stats name the
code), so it is kept in Postgres across any restart and no request of the person can lift
it. One sweep a day at most: a sweep of the same day (SWEEP_TZ) that opened a LinkedIn page
(`result_pages` at least 1, a sign in wall or a checkpoint included) refuses the next one with
`linkedin_daily`.

Every query is by the person's own id: another person's switch, acceptance or pause is never
read.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import sweep as sweeps
from app.jobs.models import Sweep
from app.profile.schemas import BrowserSource, SearchSection

TEXT = (
    "LinkedIn's terms forbid automated use of its site, and your account may be limited or "
    "closed; the app keeps to the pace of a person, but the risk is yours."
)
TEXT_VERSION = 1
PAUSE = timedelta(days=7)
KIND = "linkedin"

# The sweeps whose stats hold a LinkedIn source that opened a page, and one that met a checkpoint.
_OPENED = '$.sources[*] ? (@.kind == "linkedin" && @.result_pages >= 1)'
_CHECKPOINT = '$.sources[*] ? (@.kind == "linkedin" && @.error == "linkedin_checkpoint")'
_SIGNIN = '$.sources[*] ? (@.kind == "linkedin" && @.error == "linkedin_signin")'


def _has(path: str) -> Any:
    return sa.func.jsonb_path_exists(Sweep.stats, sa.cast(path, JSONPATH))


@dataclass(frozen=True)
class State:
    """The person's LinkedIn switch as the card and the guard see it."""

    source: BrowserSource | None
    accepted: bool  # the current version of TEXT is accepted
    paused_until: datetime | None
    last_sweep_at: datetime | None  # the last sweep that opened a LinkedIn page
    needs_signin: bool  # that sweep met LinkedIn's sign in wall

    @property
    def enabled(self) -> bool:
        return self.source is not None and self.source.enabled

    @property
    def allowed(self) -> bool:
        return self.enabled and self.accepted and self.paused_until is None


def entry(search: SearchSection | None) -> BrowserSource | None:
    return next((s for s in (search.browser_sources if search else []) if s.kind == KIND), None)


def accepted(source: BrowserSource | None) -> bool:
    return (
        source is not None
        and source.accepted_at is not None
        and (source.text_version == TEXT_VERSION)
    )


def _ended() -> Any:
    return sa.func.coalesce(Sweep.finished_at, Sweep.started_at)


def paused_until(db: Session, user_id: uuid.UUID, now: datetime) -> datetime | None:
    """The end of the pause a checkpoint started, None when no pause is active."""
    ended = db.execute(
        sa.select(sa.func.max(_ended())).where(
            Sweep.user_id == user_id,
            Sweep.started_at > now - PAUSE - timedelta(days=1),
            _has(_CHECKPOINT),
        )
    ).scalar_one_or_none()
    if ended is None or ended + PAUSE <= now:
        return None
    return ended + PAUSE


def _last_sweep(db: Session, user_id: uuid.UUID) -> tuple[datetime, bool] | None:
    row = db.execute(
        sa.select(_ended(), _has(_SIGNIN))
        .where(Sweep.user_id == user_id, _has(_OPENED))
        .order_by(Sweep.started_at.desc())
        .limit(1)
    ).first()
    return None if row is None else (row[0], bool(row[1]))


def state(db: Session, user_id: uuid.UUID, now: datetime | None = None) -> State:
    now = now or datetime.now(UTC)
    source = entry(sweeps.user_search(db, user_id))
    last = _last_sweep(db, user_id)
    return State(
        source=source,
        accepted=accepted(source),
        paused_until=paused_until(db, user_id, now),
        last_sweep_at=last[0] if last else None,
        needs_signin=bool(last and last[1]),
    )


def allowed(db: Session, user_id: uuid.UUID, now: datetime | None = None) -> bool:
    """Whether any LinkedIn work (search, Easy Apply, profile) may start for this person now."""
    return refusal(db, user_id, now) is None


def refusal(db: Session, user_id: uuid.UUID, now: datetime | None = None) -> str | None:
    """`linkedin_off` (no switch, switched off, or the current sentence not accepted),
    `linkedin_paused` (a checkpoint's pause), or None when LinkedIn work may start."""
    now = now or datetime.now(UTC)
    source = entry(sweeps.user_search(db, user_id))
    if source is None or not source.enabled or not accepted(source):
        return "linkedin_off"
    if paused_until(db, user_id, now) is not None:
        return "linkedin_paused"
    return None


def swept_today(
    db: Session, user_id: uuid.UUID, now: datetime, but: uuid.UUID | None = None
) -> bool:
    """Whether a sweep of the person's day (SWEEP_TZ), other than `but`, opened a LinkedIn page."""
    day = now.astimezone(sweeps.SWEEP_TZ).date()
    start = datetime.combine(day, time(), sweeps.SWEEP_TZ)
    query = sa.select(Sweep.id).where(
        Sweep.user_id == user_id,
        Sweep.started_at >= start,
        Sweep.started_at < start + timedelta(days=1),
        _has(_OPENED),
    )
    if but is not None:
        query = query.where(Sweep.id != but)
    return db.execute(query.limit(1)).first() is not None


def sweep_refusal(
    db: Session, user_id: uuid.UUID, sweep_id: uuid.UUID | None, now: datetime
) -> str | None:
    return refusal(db, user_id, now) or (
        "linkedin_daily" if swept_today(db, user_id, now, sweep_id) else None
    )


def sweep_check(
    sessions: sessionmaker[Session], clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> Callable[[Any], Awaitable[str | None]]:
    """The sources' Runner check of a LinkedIn source (sources.Check): the guard, then the one
    sweep a day, read in a session of its own off the event loop."""

    def run(user_id: uuid.UUID, sweep_id: uuid.UUID | None) -> str | None:
        with sessions() as db:
            return sweep_refusal(db, user_id, sweep_id, clock())

    async def check(plan: Any) -> str | None:
        return await asyncio.to_thread(run, plan.user_id, getattr(plan, "sweep_id", None))

    return check
