"""Per-person platform consent and durable sweep history (opportunities O7b).

Reads opened_at and checkpoint_at from existing sweep source history. Runtime writers
and multi-source history aggregation are supplied by the separate B14 integration.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import sweep
from app.jobs.models import Sweep
from app.profile.schemas import BrowserSource

KINDS = frozenset({"freework"})
TEXT = (
    "This site's terms forbid automated use; your account may be limited or closed; "
    "the app keeps to a person's pace, but the risk is yours."
)
TEXT_VERSION = 1
PAUSE = timedelta(days=7)


@dataclass(frozen=True)
class State:
    source: BrowserSource | None
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


def _history(kind: str) -> Any:
    # kind comes only from KINDS, not from a page or an unchecked route parameter.
    path = f'$.sources[*] ? (@.kind == "{kind}")'
    return sa.func.jsonb_path_query_first(Sweep.stats, sa.cast(path, JSONPATH), type_=JSONB)


def _opened(kind: str) -> Any:
    return _history(kind)["opened_at"].astext.cast(sa.DateTime(timezone=True))


def state(db: Session, user_id: uuid.UUID, kind: str, now: datetime | None = None) -> State:
    if kind not in KINDS:
        return State(None, False)
    now = now or datetime.now(UTC)
    search = sweep.user_search(db, user_id)
    source = next((s for s in search.browser_sources if s.kind == kind), None) if search else None
    accepted = bool(
        source and source.accepted_at is not None and source.text_version == TEXT_VERSION
    )
    met = db.scalar(
        sa.select(
            sa.func.max(_history(kind)["checkpoint_at"].astext.cast(sa.DateTime(timezone=True)))
        ).where(Sweep.user_id == user_id)
    )
    last = db.execute(
        sa.select(_opened(kind), _history(kind)["error"].astext)
        .where(Sweep.user_id == user_id, _opened(kind).is_not(None))
        .order_by(_opened(kind).desc(), Sweep.id.desc())
        .limit(1)
    ).first()
    return State(
        source,
        accepted,
        met + PAUSE if met is not None and met + PAUSE > now else None,
        last[0] if last else None,
        bool(last and last[1] == f"{kind}_signin"),
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


def swept_today(
    db: Session, user_id: uuid.UUID, kind: str, now: datetime, but: uuid.UUID | None = None
) -> bool:
    if kind not in KINDS:
        return True
    day = now.astimezone(sweep.SWEEP_TZ).date()
    start = datetime.combine(day, time(), sweep.SWEEP_TZ)
    end = datetime.combine(day + timedelta(days=1), time(), sweep.SWEEP_TZ)
    query = sa.select(Sweep.id).where(
        Sweep.user_id == user_id, _opened(kind) >= start, _opened(kind) < end
    )
    if but is not None:
        query = query.where(Sweep.id != but)
    return db.execute(query.limit(1)).first() is not None


def sweep_refusal(
    db: Session, user_id: uuid.UUID, kind: str, sweep_id: uuid.UUID | None, now: datetime
) -> str | None:
    return refusal(db, user_id, kind, now) or (
        f"{kind}_daily" if swept_today(db, user_id, kind, now, sweep_id) else None
    )


def sweep_check(
    sessions: sessionmaker[Session],
    kind: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Callable[[Any], Awaitable[str | None]]:
    def run(plan: Any) -> str | None:
        with sessions() as db:
            return sweep_refusal(db, plan.user_id, kind, getattr(plan, "sweep_id", None), clock())

    async def check(plan: Any) -> str | None:
        return await asyncio.to_thread(run, plan)

    return check
