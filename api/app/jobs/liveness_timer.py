"""The timer of the liveness check (jobs J37, SPEC-jobs "Liveness"): the worker checks by itself
the pages of the open postings, a few at a time, so a closed posting leaves the open list without
a click. No second checker and no rule of its own: each check is app/jobs/liveness_check.py's,
stored as a click's is.

- Which: the person's postings `found` or `shortlisted` (OPEN) with an address, never LinkedIn's
  (`host_key`), never checked first, then by the day published (or found when it
  gives none) oldest first, then by the oldest check; a posting checked less than `again_days`
  ago is not due. A posting the person acted on (cv_ready, staged, applied and later) is the
  clicks' and the apply run's, as before.
- Bounds (Settings, JOBS_LIVENESS_*): at most `per_tick` checks a tick in all, the persons in
  turn; at most `per_day` checks of a person's postings in the last 24 hours, counted from the
  checks stored on them (`jobs.liveness.checked_at`, a click's included); one posting of a host
  a tick; a host whose answer to a check of the person in the last `rest_hours` was a wall
  (REST_REASONS) is left alone for every posting of hers; a host a running search reads
  (its sources' hosts) is left alone while it runs. Each request is paced by the shared pacer, as
  a click's. A person with no open posting costs no query of her own and no request.
- One tick at a time, whatever the number of workers: the tick holds a transaction advisory lock
  (LOCK) for its whole run on a session of its own, and a tick that finds it held does nothing.
  A dead worker's connection ends, and its lock with it.
- A closing date the posting states (`closes_on`, app/jobs/signals.py) that passed everywhere
  (LAST_ZONE) closes it as a closed page does (`liveness_check.store`, reason `closing_date`), no
  request made; a posting the app closed once and the person reopened is only proposed, once.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import liveness_check
from app.jobs import service as jobs_service
from app.jobs.liveness import CLOSED, Finding
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus, Sweep, SweepStatus

logger = logging.getLogger("app.jobs.liveness")

OPEN = frozenset({JobStatus.found, JobStatus.shortlisted})
# The answers that say the host turned the app away: it is asked again only after a rest.
REST_REASONS = frozenset({"rate_limited", "bot_check", "refused", "sign_in"})
# The findings stored without a request: they leave the day's room as it was.
NO_REQUEST = ("closing_date", "linkedin", "no_address")
LOCK = 0x6A6F_624C_6976_65
TICK_SECOND = 50
# A closing date has passed everywhere once its next day began at UTC-12.
LAST_ZONE = timedelta(hours=12)
DATE_FINDING = Finding(CLOSED, "closing_date")
_ISO = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$"


@dataclass(frozen=True)
class Bounds:
    per_day: int = 150
    per_tick: int = 10
    rest_hours: int = 24
    again_days: int = 7

    @classmethod
    def of(cls, settings: Any) -> "Bounds":
        return cls(
            per_day=settings.jobs_liveness_checks_per_day,
            per_tick=settings.jobs_liveness_checks_per_tick,
            rest_hours=settings.jobs_liveness_host_rest_hours,
            again_days=settings.jobs_liveness_again_days,
        )


# The worst tick: every check at its whole budget, plus the database.
TICK_TIMEOUT_S = 20 * liveness_check.BUDGET_S + 30


@dataclass(frozen=True)
class Pick:
    user_id: uuid.UUID
    job_id: uuid.UUID
    url: str
    host: str


def host_key(url: str) -> str:
    """The host as the pacer keys it (a protected platform is one key), `www.` folded; empty
    for LinkedIn, whose postings are never asked (liveness_check, `linkedin`)."""
    # Imported here, as in liveness_check: it registers the browser finder's agent at import.
    from app.browser import platform_policy

    try:
        host = (urlsplit(url.strip()).hostname or "").removeprefix("www.")
    except ValueError:
        return ""
    platform = platform_policy.platform_of(url)
    return "" if platform == "linkedin" else platform_policy.pace_key(platform, host)


def checked_at() -> sa.ColumnElement[datetime]:
    """When the posting's last check ran, null when none or when the stored time is malformed."""
    raw = Job.liveness["checked_at"].astext
    return sa.case(
        (raw.regexp_match(_ISO), sa.cast(raw, sa.DateTime(timezone=True))),
        else_=sa.null(),
    )


def _stored_at(stored: dict[str, Any] | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str((stored or {})["checked_at"]))
    except (KeyError, TypeError, ValueError):
        return None


def last_passed(now: datetime) -> date:
    """The latest closing date that has passed everywhere at `now`."""
    return (now.astimezone(UTC) - LAST_ZONE).date() - timedelta(days=1)


# --- which postings a tick checks -----------------------------------------------------------------


def _open(user_id: uuid.UUID | None = None) -> sa.ColumnElement[bool]:
    found = sa.and_(Job.status.in_(OPEN), Job.url.is_not(None))
    return found if user_id is None else sa.and_(found, Job.user_id == user_id)


def searched_hosts(db: Session) -> set[str]:
    """The hosts of the sources of every running search: a browser session may be on them."""
    hosts: set[str] = set()
    for stats in db.scalars(sa.select(Sweep.stats).where(Sweep.status == SweepStatus.running)):
        for source in (stats or {}).get("sources") or []:
            host = source.get("host") if isinstance(source, dict) else None
            if isinstance(host, str) and host.strip():
                hosts.add(host_key(f"https://{host.strip()}/"))
    return hosts


def checks_today(db: Session, user_id: uuid.UUID, now: datetime) -> int:
    """The person's postings checked in the 24 hours before `now`, by the timer or a click."""
    return (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(Job)
            .where(
                Job.user_id == user_id,
                checked_at() > now - timedelta(hours=24),
                Job.liveness["reason"].astext.not_in(NO_REQUEST),
            )
        )
        or 0
    )


def resting_hosts(db: Session, user_id: uuid.UUID, now: datetime, bounds: Bounds) -> set[str]:
    """The hosts whose answer to a check of one of the person's postings, within the rest, was
    a wall (REST_REASONS)."""
    urls = db.scalars(
        sa.select(Job.url).where(
            Job.user_id == user_id,
            Job.url.is_not(None),
            Job.liveness["reason"].astext.in_(REST_REASONS),
            checked_at() > now - timedelta(hours=bounds.rest_hours),
        )
    )
    return {host_key(url) for url in urls if url}


def due(db: Session, user_id: uuid.UUID, now: datetime, bounds: Bounds) -> list[Any]:
    """The person's open postings a check may ask, in the order they are asked (module
    docstring): their id and address."""
    when = checked_at()
    return list(
        db.execute(
            sa.select(Job.id, Job.url)
            .where(
                _open(user_id),
                sa.or_(when.is_(None), when <= now - timedelta(days=bounds.again_days)),
                sa.or_(Job.closes_on.is_(None), Job.closes_on > last_passed(now)),
            )
            .order_by(
                when.is_not(None),
                sa.func.coalesce(Job.published_on, Job.found_on).asc(),
                sa.nulls_first(when.asc()),
                Job.id,
            )
        )
    )


def plan(db: Session, now: datetime, bounds: Bounds) -> list[Pick]:
    """The checks of one tick: each person's next due postings within her day's room, the
    persons in turn, at most `per_tick` in all and one posting of a host."""
    if bounds.per_day <= 0 or bounds.per_tick <= 0:
        return []
    people = list(
        db.scalars(
            sa.select(Job.user_id).where(_open()).group_by(Job.user_id).order_by(Job.user_id)
        )
    )
    taken = searched_hosts(db)
    lists: list[list[Pick]] = []
    for person in people:
        room = min(bounds.per_day - checks_today(db, person, now), bounds.per_tick)
        if room <= 0:
            continue
        resting = resting_hosts(db, person, now, bounds)
        picks: list[Pick] = []
        for row in due(db, person, now, bounds):
            host = host_key(row.url)
            if not host or host in taken or host in resting:
                continue
            taken.add(host)
            picks.append(Pick(person, row.id, row.url, host))
            if len(picks) >= room:
                break
        lists.append(picks)
    return _in_turn(lists)[: bounds.per_tick]


def _in_turn(lists: Iterable[list[Pick]]) -> list[Pick]:
    queues = [list(found) for found in lists if found]
    out: list[Pick] = []
    while queues:
        out += [found.pop(0) for found in queues]
        queues = [found for found in queues if found]
    return out


# --- a closing date that passed -------------------------------------------------------------------


def _reopened(db: Session, job_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    return set(
        db.scalars(
            sa.select(JobEvent.job_id).where(
                JobEvent.job_id.in_(job_ids),
                JobEvent.kind == JobEventKind.status,
                JobEvent.data["via"].astext == "liveness",
            )
        )
    )


def _date_due(job: Job, reopened: bool, now: datetime) -> bool:
    """An open posting whose closing date passed: closed; reopened by the person after the app
    closed it, proposed once (while no check was stored after its closing date)."""
    if job.status not in OPEN or job.closes_on is None or job.closes_on > last_passed(now):
        return False
    stored = _stored_at(job.liveness)
    return not reopened or stored is None or stored.date() <= job.closes_on


def close_passed(sessions: sessionmaker[Session], now: datetime) -> int:
    """Every open posting whose stated closing date passed, each under its row lock in its own
    transaction (one that fails fails alone). How many it closed."""
    with sessions() as db:
        rows = db.execute(
            sa.select(Job.user_id, Job.id).where(
                Job.status.in_(OPEN), Job.closes_on <= last_passed(now)
            )
        ).all()
        reopened = _reopened(db, [row.id for row in rows]) if rows else set()
    closed = 0
    for row in rows:
        try:
            closed += _close_one(sessions, row.user_id, row.id, row.id in reopened, now)
        except Exception as error:
            logger.warning(
                "jobs.liveness.date_failed",
                extra={"job_id": str(row.id), "error": type(error).__name__},
            )
    return closed


def _close_one(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    reopened: bool,
    now: datetime,
) -> int:
    with sessions() as db:
        job = jobs_service.lock_job(db, user_id, job_id)
        if job is None or not _date_due(job, reopened, now):
            db.rollback()
            return 0
        stored = liveness_check.store(db, job, DATE_FINDING, now, close_from=OPEN)
        db.commit()
    logger.info(
        "jobs.liveness",
        extra={"job_id": str(job_id), "reason": DATE_FINDING.reason, "closed": stored["moved"]},
    )
    return int(stored["moved"])


# --- the tick ------------------------------------------------------------------------------------


def _locked(db: Session) -> bool:
    return bool(db.scalar(sa.select(sa.func.pg_try_advisory_xact_lock(LOCK))))


def _in_db[T](sessions: sessionmaker[Session], step: Callable[[Session], T]) -> T:
    with sessions() as db:
        return step(db)


def _now_of(db: Session, pick: Pick) -> Any:
    return db.execute(
        sa.select(Job.url, Job.status, Job.liveness).where(
            Job.id == pick.job_id, Job.user_id == pick.user_id
        )
    ).one_or_none()


async def _check(
    checker: liveness_check.Checker,
    sessions: sessionmaker[Session],
    pick: Pick,
    clock: Callable[[], datetime],
) -> bool:
    """One planned check, unless the posting left the open ones or was checked meanwhile.
    Whether it asked the page."""
    now = clock()
    found = await asyncio.to_thread(_in_db, sessions, lambda db: _now_of(db, pick))
    if (
        found is None
        or found.status not in OPEN
        or found.url != pick.url
        or liveness_check.fresh(found.liveness, now)
    ):
        return False
    finding = await checker.check(pick.url)
    await asyncio.to_thread(
        _in_db,
        sessions,
        lambda db: liveness_check.record(
            db, pick.user_id, pick.job_id, finding, now, pick.url, close_from=OPEN
        ),
    )
    return True


async def tick(ctx: dict[str, Any]) -> int:
    """The worker's cron (sweeps queue): the closing dates that passed, then one tick's checks.
    How many pages it asked."""
    sessions: sessionmaker[Session] = ctx["sessions"]
    clock: Callable[[], datetime] = ctx["clock"]
    bounds = Bounds.of(ctx["settings"])
    lock = sessions()
    try:
        if not await asyncio.to_thread(_locked, lock):
            return 0
        closed = await asyncio.to_thread(close_passed, sessions, clock())
        picks = await asyncio.to_thread(_in_db, sessions, lambda db: plan(db, clock(), bounds))
        checker = liveness_check.for_worker(ctx)
        asked = 0
        for pick in picks:
            try:
                asked += await _check(checker, sessions, pick, clock)
            except Exception as error:
                logger.warning(
                    "jobs.liveness.not_checked",
                    extra={"job_id": str(pick.job_id), "error": type(error).__name__},
                )
        if closed or picks:
            logger.info(
                "jobs.liveness.timer",
                extra={"closed_by_date": closed, "planned": len(picks), "asked": asked},
            )
        return asked
    finally:
        await asyncio.to_thread(lock.close)
