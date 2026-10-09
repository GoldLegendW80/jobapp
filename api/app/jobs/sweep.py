"""Sweeps: read every enabled board of a user's search section and keep the new matching postings,
as engine/jobs.py `cmd_find` does (SPEC-jobs.md, Sweep). Runs in the worker (app/worker.py).

One sweep, `run_sweep(sweep_id)`:
1. Claim. The sweep row (made `running` by POST /api/jobs/sweeps or a scheduled tick) is locked
   and checked: still running, never picked before (a row picked by a worker that then died is
   failed at once instead of half run again). The user comes from the row, never from the queue
   message. The search section is read; without an enabled board the sweep fails with a clear
   message.
2. Fetch. Boards are read through ats.AtsClient, CONCURRENCY at a time, each failure recorded
   with its code; the others go on.
3. Keep. For each posting, in the engine's order: the title matches `title_include` (an empty
   list keeps every title), matches neither `title_exclude` nor the board's `extra_exclude`, the
   location matches `location` (empty: any), and the published date is within `days` of today
   (Europe/Paris; a posting without a date is left out, as the engine's string comparison did).
   Then one step the engine did not have: the contract type read from the title, location and
   URL (engine `kind`, app/jobs/importer.py) must be one of `contract_types` (empty: any).
   Term lists match through app/profile/matching.py, so the regexes converted from boards.json
   decide exactly as the engine's (tests/jobs/test_sweep_parity.py).
4. Store. A kept posting already in the user's list (slug.py: same URL once normalised, or same
   company, title and location; postings of the same sweep included) is counted as a duplicate.
   A new one becomes a `found` job: company = the board's name, source "<ats>:<org>",
   description = the first 300 characters of the text, the posting text, its published date,
   with `created` and `sweep` events. It is scored at once against the user's skills
   (scoring.score on the text with the title, as `cmd_find` scored it), unless the profile has
   none. A job matching a never_apply rule is then moved to `skipped`, the rule as its note.
   Each board is one transaction.
5. Progress. After each board (and when one starts) the sweep row's `stats` is rewritten, so
   GET /api/jobs/sweeps/{id} and its event stream show it live; `heartbeat_at` moves with it.
6. End. `done` with the duration, or `failed` with a short error (time limit, cancellation, a
   bug); the row is never left running by a sweep that returns.

never_apply rules are free text in the profile. A rule's terms are what follows its first colon,
split on commas and semicolons, remarks in parentheses removed: "No cybersecurity roles: cyber,
security, pentest, threat (Harald, 2026-10-06)" has the terms cyber, security, pentest, threat.
They match as plain terms (whole word, optional plural, case-insensitive) on the company and the
title. A rule without a colon has no terms and matches nothing.

Stale sweeps: a running sweep beats every HEARTBEAT_S (`heartbeat_at`), whatever its boards do.
One picked by a worker whose heartbeat is older than STALE_AFTER (the worker died), or one never
picked within NOT_PICKED_AFTER of its start (the worker is down, or the message was lost), is
failed by `reap_stale`: the worker runs it every minute and at start, the api before it starts a
new sweep for the user. A worker crash therefore blocks a user's next sweep for about two
minutes at most, and a sweep waiting behind other users' sweeps is not failed for waiting.
Schedules (app/jobs/schedule.py): automatic sweeps are off unless the user enables
`search.sweep_schedule`. `scheduled_due` lists the (user, slot) pairs a worker tick must start,
`start_scheduled` starts one under an advisory lock, recording the slot in the sweep's stats, so
each slot fires once.

Nothing here logs posting texts.
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.identity.models import User
from app.jobs import ats, schedule, scoring, service
from app.jobs import slug as slugs
from app.jobs.importer import kind
from app.jobs.models import (
    ContractType,
    Job,
    JobEvent,
    JobEventKind,
    JobStatus,
    Sweep,
    SweepStatus,
    SweepTrigger,
)
from app.profile import matching
from app.profile.models import Profile
from app.profile.schemas import Board, SearchSection, SkillsSection, SweepSchedule

logger = logging.getLogger("app.sweep")

SWEEP_TZ = ZoneInfo("Europe/Paris")
CONCURRENCY = 4
HEARTBEAT_S = 15.0
STALE_AFTER = timedelta(minutes=2)
NOT_PICKED_AFTER = timedelta(minutes=20)
SWEEP_TIMEOUT_S = 15 * 60
DESCRIPTION_CHARS = 300  # engine: description=text[:300]
POSTING_TEXT_MAX = 200_000
NEW_JOBS_LISTED = 200

ERROR_MESSAGES = {
    "not_found": "board not found",
    "blocked": "the ATS refused this server",
    "rate_limited": "rate limited by the ATS",
    "upstream_error": "the ATS answered with an error",
    "timeout": "no answer in time",
    "network": "could not connect",
    "too_large": "the board is too large",
    "invalid_response": "unexpected answer from the ATS",
    "store_failed": "its postings could not be saved",
}
CRASHED = "The worker stopped during this sweep. Start it again."
NOT_PICKED = "The worker did not pick up this sweep. Start it again."
TIMED_OUT = "The sweep took too long and was stopped."
INTERRUPTED = "The sweep was interrupted (the worker restarted). Start it again."
INTERNAL = "The sweep failed because of an internal error."
NO_BOARDS = "No board is enabled in the search section of your profile."
QUEUE_DOWN = "The sweep could not be queued. Try again in a minute."


# --- filters ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NeverApplyRule:
    text: str
    terms: tuple[str, ...]
    pattern: re.Pattern[str] | None

    @classmethod
    def parse(cls, text: str) -> "NeverApplyRule":
        _, colon, body = text.partition(":")
        body = re.sub(r"\([^)]*\)", " ", body) if colon else ""
        terms = tuple(
            term
            for term in (part.strip(" .!\t") for part in re.split(r"[,;]", body))
            if term and len(term) <= 200
        )
        return cls(text=text, terms=terms, pattern=matching.compile_terms(terms, False))

    def matches(self, company: str, title: str) -> bool:
        return self.pattern is not None and self.pattern.search(f"{company} {title}") is not None


def never_apply_rules(search: SearchSection) -> list[NeverApplyRule]:
    return [NeverApplyRule.parse(text) for text in search.never_apply]


class AnyOf:
    """The terms of a list that would not compile together, each compiled alone: a search
    finds what any of them finds (the same decisions as the list, minus the broken terms)."""

    def __init__(self, patterns: list[re.Pattern[str]]) -> None:
        self.patterns = patterns

    def search(self, text: str) -> re.Match[str] | None:
        for pattern in self.patterns:
            found = pattern.search(text)
            if found is not None:
                return found
        return None


Matcher = re.Pattern[str] | AnyOf


def term_matcher(terms: Sequence[str], advanced_regex: bool, where: str) -> Matcher | None:
    """The matcher of a stored term list, None for an empty one (no restriction). A list that
    does not compile as a whole (stored before the profile refused such lists, as scoring.py
    tolerates too) never fails the sweep: its terms are compiled one by one, those that fail
    alone are dropped, with one warning naming the list's place, never its contents. A list
    left with no term matches nothing, so a broken include list keeps nothing rather than
    everything."""
    try:
        return matching.compile_terms(list(terms), advanced_regex)
    except re.error:
        pass
    kept = []
    for term in terms:
        try:
            pattern = matching.compile_terms([term], advanced_regex)
        except re.error:
            continue
        if pattern is not None:
            kept.append(pattern)
    logger.warning(
        "search term list does not compile, broken terms skipped",
        extra={"list": where, "terms": len(terms), "kept": len(kept)},
    )
    return AnyOf(kept)


@dataclass(frozen=True)
class SearchFilter:
    """The keep decision of step 3 for one user's search section."""

    include: Matcher | None
    exclude: Matcher | None
    location: Matcher | None
    contract_types: frozenset[str]
    days: int

    @classmethod
    def of(cls, search: SearchSection) -> "SearchFilter":
        def compile_list(terms: Any, where: str) -> Matcher | None:
            return term_matcher(terms.terms, terms.advanced_regex, where)

        return cls(
            include=compile_list(search.title_include, "title_include"),
            exclude=compile_list(search.title_exclude, "title_exclude"),
            location=compile_list(search.location, "location"),
            contract_types=frozenset(search.contract_types),
            days=search.days,
        )

    @staticmethod
    def board_exclude(board: Board) -> Matcher | None:
        extra = board.extra_exclude
        return term_matcher(extra.terms, extra.advanced_regex, f"extra_exclude of {board.org}")

    def since(self, today: date) -> str:
        return (today - timedelta(days=self.days)).isoformat()

    def terms_reason(
        self, title: str, location: str, published: str, board_exclude: Matcher | None,
        since: str,
    ) -> str | None:  # fmt: skip
        """Why the engine's filter leaves this posting out, or None when it keeps it."""
        if self.include is not None and not self.include.search(title):
            return "title"
        if self.exclude is not None and self.exclude.search(title):
            return "excluded"
        if board_exclude is not None and board_exclude.search(title):
            return "board_excluded"
        if self.location is not None and not self.location.search(location):
            return "location"
        if not published >= since:
            return "too_old"
        return None

    def reason(self, posting: ats.Posting, board_exclude: Matcher | None, since: str) -> str | None:
        found = self.terms_reason(
            posting.title, posting.location, posting.published, board_exclude, since
        )
        if found is None and self.contract_types:
            contract = kind(posting.title, posting.location, posting.url)
            if contract.value not in self.contract_types:
                return "contract"
        return found


# --- what a sweep reads -------------------------------------------------------------------------


def user_search(db: Session, user_id: uuid.UUID) -> SearchSection | None:
    sections = db.execute(
        sa.select(Profile.sections).where(Profile.user_id == user_id)
    ).scalar_one_or_none()
    raw = (sections or {}).get("search")
    if not isinstance(raw, dict):
        return None
    try:
        return SearchSection.model_validate(raw)
    except ValueError:
        return None


def enabled_boards(search: SearchSection | None) -> list[Board]:
    return [board for board in search.boards if board.enabled] if search else []


# --- stats --------------------------------------------------------------------------------------


@dataclass
class Stats:
    boards_total: int = 0
    boards_done: int = 0
    boards_ok: int = 0
    boards_failed: list[dict[str, str]] = field(default_factory=list)
    current_board: str | None = None
    seen: int = 0
    matched: int = 0
    new: int = 0
    duplicates: int = 0
    skipped_by_rule: int = 0
    invalid: int = 0
    duration: float | None = None
    picked_at: str | None = None
    heartbeat_at: str | None = None
    # The slot of a scheduled sweep (schedule.Slot.key), kept for its whole life: what makes
    # a slot fire once.
    schedule_slot: str | None = None

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


# --- duplicates ---------------------------------------------------------------------------------


class Known:
    """The user's postings as duplicate keys and slugs, loaded once per sweep and kept up to date
    with what the sweep adds (slug.py rules; dict lookups instead of a scan per posting)."""

    def __init__(self, db: Session, user_id: uuid.UUID) -> None:
        self.urls: set[str] = set()
        self.identities: set[tuple[str, str, str]] = set()
        self.slugs: set[str] = set()
        rows = db.execute(
            sa.select(Job.company, Job.title, Job.location, Job.url, Job.slug).where(
                Job.user_id == user_id
            )
        )
        for company, title, location, url, slug in rows:
            self.add(slugs.PostingKey.of(company, title, location, url), slug)

    def add(self, key: slugs.PostingKey, slug: str | None) -> None:
        if key.url is not None:
            self.urls.add(key.url)
        self.identities.add(key.identity)
        if slug:
            self.slugs.add(slug)

    def has(self, key: slugs.PostingKey) -> bool:
        return (key.url is not None and key.url in self.urls) or key.identity in self.identities


# --- one board ----------------------------------------------------------------------------------


@dataclass
class Plan:
    """What a claimed sweep runs with."""

    sweep_id: uuid.UUID
    user_id: uuid.UUID
    boards: list[Board]
    filter: SearchFilter
    rules: list[NeverApplyRule]
    skills: SkillsSection | None
    known: Known
    schedule_slot: str | None = None


INSERT_CHUNK = 500


def store_board(
    db: Session,
    plan: Plan,
    board: Board,
    result: ats.BoardResult,
    stats: Stats,
    now: datetime,
) -> None:
    """Keep, dedupe, score, rule-check and insert one board's postings; the caller commits.

    New jobs go in with INSERT ... ON CONFLICT DO NOTHING, INSERT_CHUNK at a time: a posting
    the database already holds (added meanwhile by another request, caught by the unique slug or
    URL index) is not inserted and counts as a duplicate. Their events follow in one batch."""
    since = plan.filter.since(now.astimezone(SWEEP_TZ).date())
    board_exclude = SearchFilter.board_exclude(board)
    source = f"{board.ats}:{board.org}"
    stats.invalid += result.invalid
    pending: list[_NewJob] = []
    for posting in result.postings:
        stats.seen += 1
        if plan.filter.reason(posting, board_exclude, since) is not None:
            continue
        stats.matched += 1
        key = slugs.PostingKey.of(board.company, posting.title, posting.location, posting.url)
        if plan.known.has(key):
            stats.duplicates += 1
            continue
        new = _new_job(plan, board, posting, source, now)
        plan.known.add(key, new.row["slug"])
        pending.append(new)
    for first in range(0, len(pending), INSERT_CHUNK):
        chunk = pending[first : first + INSERT_CHUNK]
        inserted = set(
            db.execute(
                pg_insert(Job)
                .values([new.row for new in chunk])
                .on_conflict_do_nothing()
                .returning(Job.id)
            ).scalars()
        )
        events = [event for new in chunk if new.row["id"] in inserted for event in new.events]
        if events:
            db.execute(sa.insert(JobEvent), events)
        for new in chunk:
            if new.row["id"] in inserted:
                stats.new += 1
                stats.skipped_by_rule += new.rule is not None
            else:
                stats.duplicates += 1


@dataclass
class _NewJob:
    row: dict[str, Any]
    events: list[dict[str, Any]]
    rule: NeverApplyRule | None


def _new_job(plan: Plan, board: Board, posting: ats.Posting, source: str, now: datetime) -> _NewJob:
    """The row of a new job, already scored and rule-checked, and its events: created, sweep,
    then the ones service.apply_score, set_status and set_notes would have written."""
    text = posting.text[:POSTING_TEXT_MAX]
    rule = next((r for r in plan.rules if r.matches(board.company, posting.title)), None)
    score = (
        scoring.score(text, plan.skills, title=posting.title)
        if plan.skills is not None and text.strip()
        else None
    )
    job_id = uuid.uuid4()
    slug = slugs.unique_slug(slugs.slugify(board.company, posting.title), plan.known.slugs)
    row = {
        "id": job_id,
        "user_id": plan.user_id,
        "slug": slug,
        "company": board.company,
        "title": posting.title,
        "location": posting.location,
        "url": posting.url,
        "contract_type": ContractType(kind(posting.title, posting.location, posting.url)),
        "source": source,
        "status": JobStatus.skipped if rule is not None else JobStatus.found,
        "fit": score.fit if score is not None else None,
        "fit_overridden": False,
        "competencies": list(score.competencies) if score is not None else [],
        "gaps": list(score.gaps) if score is not None else [],
        "description": text[:DESCRIPTION_CHARS] or None,
        "posting_text": text or None,
        "published_on": ats.published_date(posting.published),
        "found_on": now.astimezone(SWEEP_TZ).date(),
        "notes": rule.text if rule is not None else "",
        "legacy": {},
        "created_at": now,
        "updated_at": now,
    }
    data: list[tuple[JobEventKind, dict[str, Any]]] = [
        (JobEventKind.created, {"slug": slug, "via": "sweep"}),
        (JobEventKind.sweep, {"sweep_id": str(plan.sweep_id), "board": source}),
    ]
    if score is not None:
        data.append(
            (JobEventKind.fit, {"from": None, "to": score.fit, "manual": False, "via": "sweep"})
        )
    if rule is not None:
        data.append((JobEventKind.status, {"from": "found", "to": "skipped", "via": "never_apply"}))
        data.append((JobEventKind.note, {"from": "", "to": rule.text, "via": "never_apply"}))
    events = [
        {"job_id": job_id, "user_id": plan.user_id, "at": now, "kind": kind_, "data": payload}
        for kind_, payload in data
    ]
    return _NewJob(row=row, events=events, rule=rule)


# --- the sweep row ------------------------------------------------------------------------------


def _finish(sweep: Sweep, status: SweepStatus, now: datetime, error: str | None = None) -> None:
    sweep.status = status
    sweep.finished_at = max(now, sweep.started_at)
    sweep.error = error


def fail_sweep(
    sessions: sessionmaker[Session], sweep_id: uuid.UUID, error: str, now: datetime,
    stats: Stats | None = None,
) -> None:  # fmt: skip
    """Mark a sweep failed if it is still running (keeping the stats gathered so far)."""
    with sessions() as db:
        sweep = db.get(Sweep, sweep_id, with_for_update=True)
        if sweep is None or sweep.status is not SweepStatus.running:
            return
        if stats is not None:
            sweep.stats = stats.as_json()
        _finish(sweep, SweepStatus.failed, now, error)
        db.commit()


def claim(sessions: sessionmaker[Session], sweep_id: uuid.UUID, now: datetime) -> Plan | None:
    """Lock the sweep and turn it into a Plan, or None when there is nothing to run (gone, not
    running, picked before by a worker that died, or no enabled board: failed then)."""
    with sessions() as db:
        sweep = db.get(Sweep, sweep_id, with_for_update=True)
        if sweep is None or sweep.status is not SweepStatus.running:
            return None
        if sweep.stats.get("picked_at"):
            _finish(sweep, SweepStatus.failed, now, CRASHED)
            db.commit()
            return None
        search = user_search(db, sweep.user_id)
        boards = enabled_boards(search)
        slot = sweep.stats.get("schedule_slot")
        stats = Stats(
            boards_total=len(boards),
            picked_at=now.isoformat(),
            heartbeat_at=now.isoformat(),
            schedule_slot=slot if isinstance(slot, str) else None,
        )
        sweep.stats = stats.as_json()
        if search is None or not boards:
            _finish(sweep, SweepStatus.failed, now, NO_BOARDS)
            db.commit()
            return None
        plan = Plan(
            sweep_id=sweep.id,
            user_id=sweep.user_id,
            boards=boards,
            filter=SearchFilter.of(search),
            rules=never_apply_rules(search),
            skills=service.user_skills(db, sweep.user_id),
            known=Known(db, sweep.user_id),
            schedule_slot=stats.schedule_slot,
        )
        db.commit()
    return plan


def save_progress(
    sessions: sessionmaker[Session], sweep_id: uuid.UUID, stats: Stats, now: datetime
) -> None:
    stats.heartbeat_at = now.isoformat()
    with sessions() as db:
        db.execute(
            sa.update(Sweep)
            .where(Sweep.id == sweep_id, Sweep.status == SweepStatus.running)
            .values(stats=stats.as_json())
        )
        db.commit()


def finish_done(
    sessions: sessionmaker[Session], sweep_id: uuid.UUID, stats: Stats, now: datetime
) -> None:
    stats.current_board = None
    stats.heartbeat_at = now.isoformat()
    with sessions() as db:
        sweep = db.get(Sweep, sweep_id, with_for_update=True)
        if sweep is None or sweep.status is not SweepStatus.running:
            return
        sweep.stats = stats.as_json()
        _finish(sweep, SweepStatus.done, now)
        db.commit()


# --- running a sweep ----------------------------------------------------------------------------


Clock = Callable[[], datetime]


async def run_sweep(
    sessions: sessionmaker[Session],
    sweep_id: uuid.UUID,
    client: ats.AtsClient,
    clock: Clock,
    *,
    concurrency: int = CONCURRENCY,
    timeout_s: float = SWEEP_TIMEOUT_S,
    heartbeat_s: float = HEARTBEAT_S,
) -> None:
    """Run one sweep to its end (module docstring). Never raises for a board's failure; any other
    error fails the sweep and is re-raised only for cancellation."""
    plan = await asyncio.to_thread(claim, sessions, sweep_id, clock())
    if plan is None:
        return
    started = time.monotonic()
    stats = Stats(
        boards_total=len(plan.boards),
        picked_at=clock().isoformat(),
        schedule_slot=plan.schedule_slot,
    )
    lock = asyncio.Lock()

    async def progress() -> None:
        async with lock:
            await asyncio.to_thread(save_progress, sessions, sweep_id, stats, clock())

    try:
        async with asyncio.timeout(timeout_s):
            gate = asyncio.Semaphore(concurrency)
            # Fetched boards wait here for the store step; bounded, so at most a few boards'
            # postings are in memory at once whatever the number of boards.
            done: asyncio.Queue[tuple[Board, ats.BoardResult | ats.BoardError]] = asyncio.Queue(
                maxsize=concurrency
            )

            async def fetch(board: Board) -> None:
                async with gate:
                    stats.current_board = board.company
                    await progress()
                    try:
                        outcome: ats.BoardResult | ats.BoardError = await client.fetch_board(
                            board.ats, board.org
                        )
                    except ats.BoardError as exc:
                        outcome = exc
                    await done.put((board, outcome))

            async def beat() -> None:
                while True:
                    await asyncio.sleep(heartbeat_s)
                    await progress()

            tasks = [asyncio.create_task(fetch(board)) for board in plan.boards]
            tasks.append(asyncio.create_task(beat()))
            try:
                for _ in plan.boards:
                    board, outcome = await done.get()
                    if isinstance(outcome, ats.BoardError):
                        stats.boards_failed.append(_failure(board, outcome.code))
                    else:
                        try:
                            await asyncio.to_thread(
                                _store, sessions, plan, board, outcome, stats, clock()
                            )
                            stats.boards_ok += 1
                        except Exception:
                            logger.exception(
                                "storing a board failed",
                                extra={"sweep_id": str(sweep_id), "board": board.org},
                            )
                            stats.boards_failed.append(_failure(board, "store_failed"))
                    stats.boards_done += 1
                    del outcome
                    await progress()
            finally:
                for task in tasks:
                    task.cancel()
        stats.duration = round(time.monotonic() - started, 2)
        await asyncio.to_thread(finish_done, sessions, sweep_id, stats, clock())
        logger.info(
            "sweep done",
            extra={"sweep_id": str(sweep_id), **_summary(stats)},
        )
    except TimeoutError:
        stats.duration = round(time.monotonic() - started, 2)
        await asyncio.to_thread(fail_sweep, sessions, sweep_id, TIMED_OUT, clock(), stats)
    except asyncio.CancelledError:
        stats.duration = round(time.monotonic() - started, 2)
        await asyncio.shield(
            asyncio.to_thread(fail_sweep, sessions, sweep_id, INTERRUPTED, clock(), stats)
        )
        raise
    except Exception:
        logger.exception("sweep failed", extra={"sweep_id": str(sweep_id)})
        stats.duration = round(time.monotonic() - started, 2)
        await asyncio.to_thread(fail_sweep, sessions, sweep_id, INTERNAL, clock(), stats)


def _summary(stats: Stats) -> dict[str, Any]:
    return {
        "boards_ok": stats.boards_ok,
        "boards_failed": len(stats.boards_failed),
        "seen": stats.seen,
        "matched": stats.matched,
        "new": stats.new,
        "skipped_by_rule": stats.skipped_by_rule,
        "duration": stats.duration,
    }


def _failure(board: Board, code: str) -> dict[str, str]:
    return {
        "company": board.company,
        "ats": board.ats,
        "org": board.org,
        "error": code,
        "message": ERROR_MESSAGES.get(code, code),
    }


COUNTERS = ("seen", "matched", "new", "duplicates", "skipped_by_rule", "invalid")


def _store(
    sessions: sessionmaker[Session],
    plan: Plan,
    board: Board,
    result: ats.BoardResult,
    stats: Stats,
    now: datetime,
) -> None:
    """One board in one transaction. When it fails nothing of the board is kept, and the
    counters and the duplicate keys are put back, so the stats count only what was stored."""
    counters = {name: getattr(stats, name) for name in COUNTERS}
    known = (set(plan.known.urls), set(plan.known.identities), set(plan.known.slugs))
    try:
        with sessions() as db:
            store_board(db, plan, board, result, stats, now)
            db.commit()
    except Exception:
        for name, value in counters.items():
            setattr(stats, name, value)
        plan.known.urls, plan.known.identities, plan.known.slugs = known
        raise


# --- maintenance --------------------------------------------------------------------------------


def reap_stale(
    db: Session,
    now: datetime,
    user_id: uuid.UUID | None = None,
    *,
    stale_after: timedelta = STALE_AFTER,
    not_picked_after: timedelta = NOT_PICKED_AFTER,
) -> int:
    """Fail every running sweep (of one user, or of everyone) that is stale (module docstring):
    picked, with a heartbeat older than `stale_after`; or never picked, started more than
    `not_picked_after` ago. Returns how many; the caller commits."""
    query = sa.select(Sweep).where(Sweep.status == SweepStatus.running).with_for_update()
    if user_id is not None:
        query = query.where(Sweep.user_id == user_id)
    reaped = 0
    for sweep in db.execute(query).scalars():
        picked = bool(sweep.stats.get("picked_at"))
        heartbeat = sweep.stats.get("heartbeat_at") if picked else None
        try:
            last = datetime.fromisoformat(heartbeat) if heartbeat else sweep.started_at
        except (TypeError, ValueError):
            last = sweep.started_at
        if now - last > (stale_after if picked else not_picked_after):
            _finish(sweep, SweepStatus.failed, now, CRASHED if picked else NOT_PICKED)
            reaped += 1
    db.flush()
    return reaped


@dataclass(frozen=True)
class DueSweep:
    user_id: uuid.UUID
    slot: schedule.Slot


def slot_taken(db: Session, user_id: uuid.UUID, key: str) -> bool:
    """Whether the user already has a sweep for this schedule slot (whatever its status)."""
    return bool(
        db.execute(
            sa.select(sa.literal(True))
            .select_from(Sweep)
            .where(
                Sweep.user_id == user_id,
                Sweep.trigger == SweepTrigger.schedule,
                Sweep.stats["schedule_slot"].astext == key,
            )
            .limit(1)
        ).scalar()
    )


def scheduled_due(
    db: Session, now: datetime, window: timedelta = schedule.WINDOW
) -> list[DueSweep]:
    """Every (user, slot) the tick at `now` must start (module docstring), oldest slot first:
    active users whose schedule is enabled, with an enabled board, for slots in the window
    that have no sweep yet. A disabled schedule, a section that does not validate, an inactive
    user or a user without boards never appears."""
    rows = db.execute(
        sa.select(Profile.user_id, Profile.sections)
        .join(User, User.id == Profile.user_id)
        .where(User.is_active.is_(True))
        .order_by(Profile.user_id)
    ).all()
    found = []
    for user_id, sections in rows:
        raw = (sections or {}).get("search")
        if not isinstance(raw, dict):
            continue
        try:
            search = SearchSection.model_validate(raw)
        except ValueError:
            continue
        if not search.sweep_schedule.enabled or not enabled_boards(search):
            continue
        for slot in schedule.due(search.sweep_schedule, now, window):
            if not slot_taken(db, user_id, slot.key):
                found.append(DueSweep(user_id=user_id, slot=slot))
    return sorted(found, key=lambda item: (item.slot.at, str(item.user_id)))


def start_scheduled(db: Session, due: DueSweep, now: datetime) -> Sweep | None:
    """The `schedule` sweep of one slot, or None when the slot already has one. The check and
    the insert run under a transaction-level advisory lock on (user, slot), so two workers or
    two ticks can never both start it. Raises service.SweepRunningError when the user has a
    sweep running (the slot is then left for a later tick of the window); the caller commits."""
    key = f"sweep-slot:{due.user_id}:{due.slot.key}"
    db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(key, 0))))
    if slot_taken(db, due.user_id, due.slot.key):
        return None
    row = start(db, due.user_id, SweepTrigger.schedule, now)
    row.stats = Stats(schedule_slot=due.slot.key).as_json()
    db.flush()
    return row


def schedule_of(search: SearchSection | None) -> SweepSchedule:
    return search.sweep_schedule if search is not None else SweepSchedule()


def start(db: Session, user_id: uuid.UUID, trigger: SweepTrigger, now: datetime) -> Sweep:
    """A new running sweep for the user, after failing a stale one of theirs. Raises
    service.SweepRunningError when one is running; the caller commits."""
    reap_stale(db, now, user_id)
    return service.start_sweep(db, user_id, trigger, now)


def sweeps_of(db: Session, user_id: uuid.UUID, limit: int) -> Sequence[Sweep]:
    return list(
        db.execute(
            sa.select(Sweep)
            .where(Sweep.user_id == user_id)
            .order_by(Sweep.started_at.desc(), Sweep.id.desc())
            .limit(limit)
        ).scalars()
    )


def new_jobs(db: Session, user_id: uuid.UUID, sweep_id: uuid.UUID, limit: int) -> list[Job]:
    """The jobs a sweep added (by their `sweep` event), best fit first."""
    ids = (
        sa.select(JobEvent.job_id)
        .where(
            JobEvent.user_id == user_id,
            JobEvent.kind == JobEventKind.sweep,
            JobEvent.data["sweep_id"].astext == str(sweep_id),
        )
        .scalar_subquery()
    )
    return list(
        db.execute(
            service.jobs_of(user_id)
            .where(Job.id.in_(ids))
            .order_by(sa.func.coalesce(Job.fit, -1).desc(), Job.company, Job.title)
            .limit(limit)
        ).scalars()
    )
