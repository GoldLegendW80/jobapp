"""The timer of the liveness check (jobs J37, SPEC-jobs "Liveness"): the worker checks by itself
the pages of the open postings, a few at a time, so a closed posting leaves the open list without
a click. No second checker and no rule of its own: each check is app/jobs/liveness_check.py's,
stored as a click's is.

- Which: the person's postings `found` or `shortlisted` (OPEN) with an address, never LinkedIn's
  (`host_key`): first those a closing date proposes whose page was not asked since, then those
  never checked (the day published, or found when it gives none, oldest first), then the oldest
  check first; a posting checked less than `again_days` ago is not due, unless its closing date
  proposes it. A posting the person acted on (cv_ready, staged, applied and later) is the
  clicks' and the apply run's, as before.
- Bounds (Settings, JOBS_LIVENESS_*): at most `per_tick` checks a tick in all, the persons in
  turn; at most `per_day` checks of a person's postings in the last 24 hours, counted from the
  checks stored on them (`jobs.liveness.checked_at`, a click's included); one posting of a host
  a tick; a host whose answer to a check of anyone's posting in the last `rest_hours` was a wall
  (REST_REASONS) is left alone for every person (the checks all leave from the machine's one
  address), and so is for DOWN_REST a host whose two latest checks found it down (DOWN_REASONS);
  a host a running search reads (its sources' hosts) is left alone while it runs.
  Each request is paced by the shared pacer, as a click's. A person with no open posting costs
  no query of her own and no request.
- One tick at a time, whatever the number of workers: the tick holds a transaction advisory lock
  (LOCK) for its whole run on a session of its own, and a tick that finds it held does nothing.
  A dead worker's connection ends, and its lock with it.
- A closing date the posting states (`closes_on`, app/jobs/signals.py) that passed everywhere
  (LAST_ZONE) only proposes it (`may_be_closed`, reason `closing_date`, the sentence the date was
  read from), no request made, while no check was stored after the date; the person closes it
  in one click or keeps it. Its page is then asked first: a closed page closes it as any check
  does; a page that neither closes nor proposes leaves the proposal standing, with the page's
  answer (`liveness_check.store`, `keep_date`).
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

from app.jobs import liveness, liveness_check
from app.jobs import service as jobs_service
from app.jobs.liveness import MAY_BE_CLOSED, UNCONFIRMED, Finding
from app.jobs.models import Job, JobStatus, Sweep, SweepStatus

logger = logging.getLogger("app.jobs.liveness")

# The postings whose attempt the worker could not store, by id: their person and when.
type Unstored = dict[uuid.UUID, tuple[uuid.UUID, datetime]]

OPEN = frozenset({JobStatus.found, JobStatus.shortlisted})
# Stored when a check's own finding cannot be: the page was asked all the same.
NOT_STORED = Finding(UNCONFIRMED, "check_failed")
# The answers that say the host turned the app away: it is asked again only after a rest.
REST_REASONS = frozenset({"rate_limited", "bot_check", "refused", "sign_in"})
# The answers of a host that is down (5xx, the time, the network): twice in a row, a rest.
DOWN_REASONS = frozenset({"server_error", "timeout", "network"})
DOWN_REST = timedelta(hours=1)
# The findings stored without a request: they leave the day's room as it was.
NO_REQUEST = (liveness_check.CLOSING_DATE, "linkedin", "no_address")
LOCK = 0x6A6F_624C_6976_65
TICK_SECOND = 50
# A closing date has passed everywhere once its next day began at UTC-12.
LAST_ZONE = timedelta(hours=12)
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


# The worst tick: the most checks a tick the setting accepts, each at its whole budget.
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


# --- which postings a tick checks ----------------------------------------------------------------


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


def answered() -> sa.ColumnElement[str]:
    """What the posting's last check found of its page: the page's reason a closing date's
    proposal keeps (`page`), else the finding's reason (`closing_date` when no page was asked)."""
    return sa.func.coalesce(Job.liveness["page"].astext, Job.liveness["reason"].astext)


def checks_today(db: Session, user_id: uuid.UUID, now: datetime) -> int:
    """The person's postings checked in the 24 hours before `now`, by the timer or a click."""
    return (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(Job)
            .where(
                Job.user_id == user_id,
                checked_at() > now - timedelta(hours=24),
                answered().not_in(NO_REQUEST),
            )
        )
        or 0
    )


def resting_hosts(db: Session, now: datetime, bounds: Bounds) -> set[str]:
    """The hosts left alone this tick for every person, whose answer to a check of anyone's
    posting within the rest was a wall (REST_REASONS): every check leaves from the machine's
    one address, so the host turned that address away, whoever's posting it asked for."""
    urls = db.scalars(
        sa.select(Job.url).where(
            Job.url.is_not(None),
            answered().in_(REST_REASONS),
            checked_at() > now - timedelta(hours=bounds.rest_hours),
        )
    )
    return {host_key(url) for url in urls if url} | down_hosts(db, now)


def down_hosts(db: Session, now: datetime) -> set[str]:
    """The hosts whose two latest checks within a day, of anyone's postings, both found them
    down (DOWN_REASONS), the latest within DOWN_REST: a broken site is asked once a DOWN_REST."""
    when = checked_at()
    rows = db.execute(
        sa.select(Job.url, answered(), when)
        .where(
            Job.url.is_not(None),
            when > now - timedelta(hours=24),
            answered().not_in(NO_REQUEST),
        )
        .order_by(when.desc(), Job.id)
    )
    latest: dict[str, list[tuple[str, datetime]]] = {}
    for url, reason, at in rows:
        seen = latest.setdefault(host_key(url), [])
        if len(seen) < 2:
            seen.append((reason, at))
    return {
        host
        for host, seen in latest.items()
        if len(seen) == 2
        and all(reason in DOWN_REASONS for reason, _ in seen)
        and seen[0][1] > now - DOWN_REST
    }


def due(db: Session, user_id: uuid.UUID, now: datetime, bounds: Bounds) -> list[Any]:
    """The person's open postings a check may ask, in the order they are asked (module
    docstring): their id and address."""
    when = checked_at()
    proposed = answered() == liveness_check.CLOSING_DATE
    return list(
        db.execute(
            sa.select(Job.id, Job.url)
            .where(
                _open(user_id),
                sa.or_(proposed, when.is_(None), when <= now - timedelta(days=bounds.again_days)),
            )
            .order_by(
                sa.case((proposed, 0), else_=1),
                when.is_not(None),
                sa.nulls_first(when.asc()),
                sa.func.coalesce(Job.published_on, Job.found_on).asc(),
                Job.id,
            )
        )
    )


def plan(
    db: Session, now: datetime, bounds: Bounds, unstored: Unstored | None = None
) -> list[Pick]:
    """The checks of one tick: each person's next due postings within her day's room, the
    persons in turn, at most `per_tick` in all and one posting of a host. A posting whose
    attempt this worker could not store (`unstored`) waits `again_days` and counts for the day."""
    if bounds.per_day <= 0 or bounds.per_tick <= 0:
        return []
    people = list(
        db.scalars(
            sa.select(Job.user_id).where(_open()).group_by(Job.user_id).order_by(Job.user_id)
        )
    )
    if not people:
        return []
    unstored = _remembered(unstored or {}, now, bounds)
    taken = searched_hosts(db) | resting_hosts(db, now, bounds)
    lists: list[list[Pick]] = []
    for person in people:
        kept = sum(
            user == person and at > now - timedelta(hours=24) for user, at in unstored.values()
        )
        room = min(bounds.per_day - checks_today(db, person, now) - kept, bounds.per_tick)
        if room <= 0:
            continue
        picks: list[Pick] = []
        for row in due(db, person, now, bounds):
            host = host_key(row.url)
            if not host or host in taken or row.id in unstored:
                continue
            taken.add(host)
            picks.append(Pick(person, row.id, row.url, host))
            if len(picks) >= room:
                break
        lists.append(picks)
    return _in_turn(lists)[: bounds.per_tick]


def _remembered(unstored: Unstored, now: datetime, bounds: Bounds) -> Unstored:
    """The attempts not stored that still space their posting; the older ones forgotten."""
    for job_id, (_, at) in list(unstored.items()):
        if at <= now - timedelta(days=bounds.again_days):
            del unstored[job_id]
    return unstored


def _in_turn(lists: Iterable[list[Pick]]) -> list[Pick]:
    queues = [list(found) for found in lists if found]
    out: list[Pick] = []
    while queues:
        out += [found.pop(0) for found in queues]
        queues = [found for found in queues if found]
    return out


# --- a closing date that passed ------------------------------------------------------------------


def _date_due(job: Any, now: datetime) -> bool:
    """An open posting whose closing date passed and no check was stored after it: proposed
    once, a check after the date (the timer's, a click's) has the last word."""
    if job.status not in OPEN or job.closes_on is None or job.closes_on > last_passed(now):
        return False
    stored = _stored_at(job.liveness)
    return stored is None or stored.date() <= job.closes_on


def date_sentence(signals: list[Any] | None) -> str | None:
    """The sentence the posting's closing date was read from (its `closing` signal)."""
    for signal in signals or []:
        if isinstance(signal, dict) and signal.get("code") == "closing":
            quote = signal.get("quote")
            return liveness.quote(quote) if isinstance(quote, str) and quote.strip() else None
    return None


def propose_passed(sessions: sessionmaker[Session], now: datetime) -> int:
    """Every open posting whose stated closing date passed, proposed, each under its row lock
    in its own transaction (one that fails fails alone). How many it proposed."""
    with sessions() as db:
        found = db.execute(
            sa.select(Job.user_id, Job.id, Job.status, Job.closes_on, Job.liveness).where(
                Job.status.in_(OPEN), Job.closes_on <= last_passed(now)
            )
        ).all()
    proposed = 0
    # Read again under the lock; a posting proposed already is not locked each tick.
    for row in [row for row in found if _date_due(row, now)]:
        try:
            proposed += _propose_one(sessions, row.user_id, row.id, now)
        except Exception as error:
            logger.warning(
                "jobs.liveness.date_failed",
                extra={"job_id": str(row.id), "error": type(error).__name__},
            )
    return proposed


def _propose_one(
    sessions: sessionmaker[Session], user_id: uuid.UUID, job_id: uuid.UUID, now: datetime
) -> int:
    with sessions() as db:
        job = jobs_service.lock_job(db, user_id, job_id)
        if job is None or not _date_due(job, now):
            db.rollback()
            return 0
        finding = Finding(MAY_BE_CLOSED, liveness_check.CLOSING_DATE, date_sentence(job.signals))
        liveness_check.store(db, job, finding, now, close_from=OPEN)
        db.commit()
    logger.info("jobs.liveness", extra={"job_id": str(job_id), "reason": finding.reason})
    return 1


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


async def _record(
    sessions: sessionmaker[Session], pick: Pick, finding: Finding, now: datetime
) -> bool:
    try:
        await asyncio.to_thread(
            _in_db,
            sessions,
            lambda db: liveness_check.record(
                db,
                pick.user_id,
                pick.job_id,
                finding,
                now,
                pick.url,
                close_from=OPEN,
                keep_date=True,
            ),
        )
    except Exception as error:
        logger.warning(
            "jobs.liveness.not_stored",
            extra={
                "job_id": str(pick.job_id),
                "reason": finding.reason,
                "error": type(error).__name__,
            },
        )
        return False
    return True


async def _check(
    checker: liveness_check.Checker,
    sessions: sessionmaker[Session],
    pick: Pick,
    clock: Callable[[], datetime],
    unstored: Unstored,
) -> bool:
    """One planned check, unless the posting left the open ones or was checked meanwhile.
    Whether it asked the page. Whatever stops the store of its finding, the attempt is stored
    as NOT_STORED in a transaction of its own, else kept in `unstored`: it counts for the day
    and spaces the next check of the posting as any check does."""
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
    if not await _record(sessions, pick, finding, now) and not await _record(
        sessions, pick, NOT_STORED, now
    ):
        unstored[pick.job_id] = (pick.user_id, now)
    return True


async def tick(ctx: dict[str, Any]) -> int:
    """The worker's cron (sweeps queue): the closing dates that passed proposed, then one tick's
    checks. How many pages it asked."""
    sessions: sessionmaker[Session] = ctx["sessions"]
    clock: Callable[[], datetime] = ctx["clock"]
    bounds = Bounds.of(ctx["settings"])
    unstored: Unstored = ctx.setdefault("liveness_unstored", {})
    lock = sessions()
    try:
        if not await asyncio.to_thread(_locked, lock):
            return 0
        proposed = await asyncio.to_thread(propose_passed, sessions, clock())
        picks = await asyncio.to_thread(
            _in_db, sessions, lambda db: plan(db, clock(), bounds, unstored)
        )
        checker = liveness_check.for_worker(ctx)
        asked = 0
        for pick in picks:
            try:
                asked += await _check(checker, sessions, pick, clock, unstored)
            except Exception as error:
                logger.warning(
                    "jobs.liveness.not_checked",
                    extra={"job_id": str(pick.job_id), "error": type(error).__name__},
                )
        if proposed or picks:
            logger.info(
                "jobs.liveness.timer",
                extra={"proposed_by_date": proposed, "planned": len(picks), "asked": asked},
            )
        return asked
    finally:
        await asyncio.to_thread(lock.close)
