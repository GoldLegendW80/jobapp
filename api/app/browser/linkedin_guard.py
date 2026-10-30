"""Whether LinkedIn work may run for a person (browser B10, SPEC-browser decision 2: "Full use").

LinkedIn is a switch per person, off by default. Turning it on shows TEXT, which the person
accepts once; the acceptance is recorded with its date and TEXT_VERSION in the person's `search`
section (the `browser_sources` entry of kind `linkedin`: `enabled`, `accepted_at`,
`text_version`; PUT /api/browser/linkedin writes it). `allowed(db, user_id)` is true only with
the acceptance of the current version, the switch on and no pause active. Every LinkedIn path
calls it before it opens a LinkedIn page: the job website search (`sweep_refusal`, through
app/browser/platform_policy.py), apply's Easy Apply (A16) and apply's profile work (A18a,
A18b). A new version of the sentence (TEXT_VERSION raised) makes every earlier acceptance
stale: nothing runs until the person accepts again.

The pause: a checkpoint or unusual activity page met on LinkedIn stops all LinkedIn work for
PAUSE from the moment it was met. `record_checkpoint(db, user_id, at)` is the one way to start
it: it adds a row to `linkedin_checkpoints` (app/browser/models.py), which no route writes or
deletes, so the pause is kept in Postgres across any restart and no request of the person can
lift it. The job website search records it through app/browser/platform_policy.py as soon as
it reads the page (before the page reaches the model, so a kill after it keeps the pause);
apply's Easy Apply (A16) and apply's profile work (A18a) call `record_checkpoint` the same
way, with their `origin`, in a transaction they commit before anything else. Nothing lifts it
early: the owner may delete the person's rows of `linkedin_checkpoints` in SQL
(docs/runbooks/sweeps-and-worker.md).
No daily limit (owner, 2026-10-28: "i don't want limit on search by day", LinkedIn too): the
switch and the pause are what refuse a sweep; `linkedin_daily` stays readable in older sweeps.

Every query is by the person's own id: another person's switch, acceptance or pause is never
read.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.orm import Session

from app.browser.models import CHECKPOINT_ORIGINS, LinkedInCheckpoint
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

# The sweeps whose stats hold a LinkedIn source that opened a page in the browser, and one that
# met the wall. LinkedIn's public job search (mode `api`, sources/linkedin_guest.py) opens no page
# of the person's browser, so it neither counts as the last sweep nor clears `needs_signin`; an
# entry stored without a mode (older rows) is the browser's.
_BROWSER = '@.kind == "linkedin" && !(@.mode == "api")'
_OPENED = f"$.sources[*] ? ({_BROWSER} && @.result_pages >= 1)"
_SIGNIN = f'$.sources[*] ? ({_BROWSER} && @.error == "linkedin_signin")'


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


def record_checkpoint(db: Session, user_id: uuid.UUID, at: datetime, origin: str = "sweep") -> None:
    """Start the person's PAUSE from `at`: a LinkedIn checkpoint was met by `origin` (the sweep,
    `easy_apply` for A16, `profile` for A18a). The caller commits, before anything else."""
    if origin not in CHECKPOINT_ORIGINS:
        raise ValueError(f"unknown origin {origin!r}")
    db.add(LinkedInCheckpoint(user_id=user_id, at=at, origin=origin))
    db.flush()


def paused_until(db: Session, user_id: uuid.UUID, now: datetime) -> datetime | None:
    """The end of the pause a checkpoint started, None when no pause is active."""
    met = db.execute(
        sa.select(sa.func.max(LinkedInCheckpoint.at)).where(
            LinkedInCheckpoint.user_id == user_id,
            LinkedInCheckpoint.at > now - PAUSE,
        )
    ).scalar_one_or_none()
    if met is None or met + PAUSE <= now:
        return None
    return met + PAUSE


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


def sweep_refusal(
    db: Session, user_id: uuid.UUID, sweep_id: uuid.UUID | None, now: datetime
) -> str | None:
    """A sweep's refusal: the switch or the pause (`refusal`); no daily limit (2026-10-28)."""
    del sweep_id  # every sweep is judged alike, whatever the person's sweeps of the day
    return refusal(db, user_id, now)
