"""Sweeps: search the job websites a user ticked in Find jobs and keep the new postings
(SPEC-jobs.md, Sweep). Runs in the worker (app/worker.py).

One sweep, `run_sweep(sweep_id)`:
1. Claim. The sweep row (made `running` by POST /api/jobs/sweeps, the first login wizard or a
   scheduled tick) is locked and checked: still running, never picked before (a row picked by
   a worker that then died is failed at once instead of half run again). The user comes from
   the row, never from the queue message. New runs use the search and skills frozen under the
   profile row lock at creation; pre-v1 rows without `stats.search_scope` explicitly retain
   claim-time profile reads. Without a ticked website, or with a search section that no longer
   validates, the sweep fails with a clear message.
2. Search. The browser hook (app/browser/sources, `Runner`) runs every ticked website through
   the bounded interactive finder (app/browser/finder.py), one website after the other, within
   the sweep's time limit.
3. Store. Each batch of findings the finder verified on its detail page is stored as it is
   reported (`Commits`, jobs J11 and J11b, below): scored against the user's skills, moved to
   `skipped` when a never apply rule matches, counted as a duplicate when the user has it.
4. Progress. The sweep row's `stats` is rewritten as the run goes, so GET /api/jobs/sweeps/{id}
   and its event stream show it live; `heartbeat_at` moves with it.
5. End. `done` with the duration, or `failed` with a short error (time limit, cancellation, a
   bug); the row is never left running by a sweep that returns.

Company boards (Ashby, Greenhouse, Lever), freelance feeds and the fixed browser readers were
deleted (owner, 2026-10-28: "Delete it"). Their saved settings are dropped from the profile on
the next read or save (app/profile/schemas.py). A sweep row they wrote keeps its stats as
stored (`boards_*`, the feed and reader entries of `sources`), and the history reads them; new
rows still write `boards_total` and the other board counters, at zero, so the release before
this one reads every row.

never_apply rules are free text in the profile. A rule's terms are what follows its first colon,
split on commas and semicolons, remarks in parentheses removed: "No cybersecurity roles: cyber,
security, pentest, threat (Harald, 2026-10-06)" has the terms cyber, security, pentest, threat.
They use apply's shared matcher (`jobs.never_apply`) on the company and title: case, accents,
compatibility forms, hidden characters and lookalike letters are folded, and terms match whole
words, optional plurals and the start or end of a glued word. A rule without a colon has no
terms and matches nothing. The app's own rule against cybersecurity (`never_apply.CYBER_RULE`,
CLAUDE.md rule 2) is always added.

Stale sweeps: a running sweep beats every HEARTBEAT_S (`heartbeat_at`), whatever its websites do.
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
import contextlib
import copy
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.identity.models import User
from app.jobs import schedule, scoring, service
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
from app.jobs.never_apply import CYBER_RULE, NeverApplyRule
from app.opportunities import pay as pays
from app.profile import matching
from app.profile.models import Profile
from app.profile.schemas import SearchSection, SkillsSection, SweepSchedule

logger = logging.getLogger("app.sweep")

SWEEP_TZ = ZoneInfo("Europe/Paris")
HEARTBEAT_S = 15.0
STALE_AFTER = timedelta(minutes=2)
NOT_PICKED_AFTER = timedelta(minutes=20)
SWEEP_TIMEOUT_S = 15 * 60
DESCRIPTION_CHARS = 300  # engine: description=text[:300]
POSTING_TEXT_MAX = 200_000
NEW_JOBS_LISTED = 200

CRASHED = "The worker stopped during this sweep. Start it again."
NOT_PICKED = "The worker did not pick up this sweep. Start it again."
TIMED_OUT = "The sweep took too long and was stopped."
INTERRUPTED = "The sweep was interrupted (the worker restarted). Start it again."
INTERNAL = "The sweep failed because of an internal error."
NO_WEBSITES = "Tick at least one job website in Find jobs, then start the search again."
SEARCH_INVALID = (
    "The search section of your profile no longer passes the checks. "
    "Open the Search tab of your profile and save it again."
)
QUEUE_DOWN = "The sweep could not be queued. Try again in a minute."


# --- filters ------------------------------------------------------------------------------------


def never_apply_rules(search: SearchSection) -> list[NeverApplyRule]:
    return [NeverApplyRule.parse(text) for text in search.never_apply]


def check_terms(search: SearchSection) -> None:
    """Raise re.error when a stored term list no longer compiles.

    The profile refuses such lists (schemas.check_joined compiles them exactly as
    matching.compile_terms does), so a sweep never sees one. Should one get through anyway (a
    regex engine that changed between the save and the sweep), `claim` fails the sweep with
    SEARCH_INVALID rather than search with a weakened exclude list."""
    for terms in (
        search.title_include,
        search.title_exclude,
        search.location,
        search.location_exclude,
    ):
        matching.compile_terms(list(terms.terms), terms.advanced_regex)


@dataclass(frozen=True)
class Posting:
    """One posting a job website showed, as a new job row takes it."""

    title: str
    location: str
    url: str
    published: str  # YYYY-MM-DD, "" when the page states no date
    text: str


def published_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# --- what a sweep reads -------------------------------------------------------------------------


class InvalidSearchError(ValueError):
    """The stored search section does not pass the profile's checks any more."""


def load_search(db: Session, user_id: uuid.UUID) -> SearchSection | None:
    """The user's search section, None when there is none. Raises InvalidSearchError when the
    stored one no longer validates (never with its contents)."""
    sections = db.execute(
        sa.select(Profile.sections).where(Profile.user_id == user_id)
    ).scalar_one_or_none()
    raw = (sections or {}).get("search")
    if not isinstance(raw, dict):
        return None
    try:
        return SearchSection.model_validate(raw)
    except ValueError:
        raise InvalidSearchError("the stored search section does not validate") from None


def user_search(db: Session, user_id: uuid.UUID) -> SearchSection | None:
    """load_search for read-only views: a section that does not validate reads as none."""
    try:
        return load_search(db, user_id)
    except InvalidSearchError:
        return None


def has_websites(search: SearchSection | None) -> bool:
    return bool(search and any(site.enabled for site in search.interactive_sites))


def alert_keys(search: SearchSection | None) -> list[str]:
    """The providers whose job alert e-mails the person turned on (app/mail/alerts)."""
    if search is None:
        return []
    return [key for key, on in search.alert_emails.model_dump().items() if on]


def alert_entry(key: str) -> dict[str, Any]:
    """The frozen source of one provider's alert e-mails: its stats entry is "mail:<key>"."""
    return {
        "source_id": _source_id("mail", key),
        "mode": "mail",
        "kind": "alerts",
        "host": f"mail:{key}",
    }


def has_sources(search: SearchSection | None) -> bool:
    """Whether a sweep of `search` has something to read: an enabled website, or the alert
    e-mails of a provider."""
    return has_websites(search) or bool(alert_keys(search))


def _source_id(mode: str, *key: str) -> str:
    encoded = json.dumps([mode, *key], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:24]


def freeze_scope(profile: Profile | None, trigger: SweepTrigger, now: datetime) -> dict[str, Any]:
    """Private v1 intent, captured under the profile lock before the queue can see the run.

    Permissions are not frozen authority. Browser guards still read current server state.
    Old rows without search_scope retain the explicit claim-time legacy path below.
    `websites_only` is always true: the release before the deletion of the boards (owner,
    2026-10-28) reads it, and a row it claims after a rollback then reads no board either.
    """
    sections = copy.deepcopy(profile.sections or {}) if profile else {}
    raw = sections.get("search")
    sources: list[dict[str, Any]] = []
    try:
        search = SearchSection.model_validate(raw) if isinstance(raw, dict) else None
    except ValueError:
        search = None  # a direct internal start still fails closed when claimed
    if search is not None:
        raw = search.model_dump(mode="json")
    if search is not None:
        sources.extend(
            {
                **site.model_dump(mode="json"),
                "platform": site.effective_platform,
                "source_id": _source_id(site.mode, site.url),
                "host": urlsplit(site.url).hostname,
            }
            for site in search.interactive_sites
            if site.enabled
        )
        sources.extend(alert_entry(key) for key in alert_keys(search))
    return {
        "version": 1,
        "profile_revision": profile.revision if profile else 0,
        "created_at": now.isoformat(),
        "trigger": trigger.value,
        "websites_only": True,
        "search": raw,
        "skills": sections.get("skills"),
        "sources": sources,
    }


def initial_sources(scope: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        {
            **{
                key: source[key]
                for key in ("source_id", "mode", "host", "query", "location")
                if key in source
            },
            "kind": source.get("kind", source.get("platform", "")),
            "phase": "pending",
            "outcome": "not_attempted",
            "reason": None,
        }
        for source in (scope or {}).get("sources", [])
    ]


# --- stats --------------------------------------------------------------------------------------


@dataclass
class Stats:
    # The board counters stay, at zero: the release before the deletion of the boards (owner,
    # 2026-10-28) and the history read them on every row.
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
    # One entry per frozen selected source; pre-v1 rows retain reader-only entries.
    sources: list[dict[str, Any]] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def bind_source(
    stats: Stats, mode: str, key: Sequence[str], entry: dict[str, Any]
) -> dict[str, Any]:
    """Reuse the frozen slot; pre-v1 readers keep their original unbound entry format."""
    source_id = _source_id(mode, *key)
    for selected in stats.sources:
        if selected.get("source_id") == source_id:
            selected.update(entry, phase="running", outcome=None, reason=None)
            return selected
    stats.sources.append(entry)
    return entry


def finish_source(entry: dict[str, Any]) -> None:
    if not entry.get("source_id"):
        return
    error = entry.get("error")
    found = entry.get("found", 0)
    if error == "not_started":
        outcome = "not_attempted"
    elif error:
        outcome = "partial" if found else "failed"
    else:
        outcome = "complete" if found else "no_matches"
    entry.update(phase="done", outcome=outcome, reason=error)


# --- duplicates ---------------------------------------------------------------------------------

# The company of a posting a job website showed without naming its employer (jobs J11): stated
# as unknown, never the website's name, and only its address makes it a duplicate (two such
# postings with the same title are two employers).
UNKNOWN_EMPLOYER = "Employer not stated"
_UNNAMED_KEY = slugs.PostingKey.of(UNKNOWN_EMPLOYER, "", "", None).identity[0]


def _identity(key: slugs.PostingKey) -> tuple[str, str, str] | None:
    """The company, title and location rule's key, None for an unknown employer."""
    return None if key.identity[0] == _UNNAMED_KEY else key.identity


class Known:
    """The user's postings as duplicate keys and slugs, loaded once per sweep and kept up to date
    with what the sweep adds (slug.py rules; dict lookups instead of a scan per posting)."""

    def __init__(self, db: Session, user_id: uuid.UUID) -> None:
        self.urls: set[str] = set()
        self.identities: set[tuple[str, str, str]] = set()
        self.slugs: set[str] = set()
        # Which job a key is and the sources it names (browser B10: one posting found by two
        # sources is one row naming both). Filled by the constructor and `add`.
        self.jobs: dict[str | tuple[str, str, str], uuid.UUID] = {}
        self.sources: dict[uuid.UUID, str] = {}
        rows = db.execute(
            sa.select(Job.company, Job.title, Job.location, Job.url, Job.slug, Job.id, Job.source)
            .where(Job.user_id == user_id)
            .order_by(Job.created_at)
        )
        for company, title, location, url, slug, job_id, source in rows:
            self.add(slugs.PostingKey.of(company, title, location, url), slug, job_id, source)

    def add(
        self,
        key: slugs.PostingKey,
        slug: str | None,
        job_id: uuid.UUID | None = None,
        source: str | None = None,
    ) -> None:
        if key.url is not None:
            self.urls.add(key.url)
        identity = _identity(key)
        if identity is not None:
            self.identities.add(identity)
        if slug:
            self.slugs.add(slug)
        if job_id is not None:
            if key.url is not None:
                self.jobs.setdefault(key.url, job_id)
            if identity is not None:
                self.jobs.setdefault(identity, job_id)
            self.sources[job_id] = source or ""

    def copy(self) -> "Known":
        """An independent copy: a commit stages its keys there and publishes them after it."""
        other = Known.__new__(Known)
        other.urls, other.identities = set(self.urls), set(self.identities)
        other.slugs, other.jobs = set(self.slugs), dict(self.jobs)
        other.sources = dict(self.sources)
        return other

    def job_of(self, key: slugs.PostingKey) -> uuid.UUID | None:
        """The job a duplicate is: by URL first, else by identity."""
        found = self.jobs.get(key.url) if key.url is not None else None
        identity = _identity(key)
        if found is None and identity is not None:
            found = self.jobs.get(identity)
        return found

    def note(self, job_id: uuid.UUID, source: str) -> str | None:
        """The job's sources with `source` added, None when it names it already or is full."""
        names = noted_sources(self.sources.get(job_id, ""))
        if not source or source in names or len(names) >= SOURCES_MAX:
            return None
        joined = SOURCES_SEP.join([*names, source])
        if len(joined) > SOURCES_CHARS:
            return None
        self.sources[job_id] = joined
        return joined

    def has(self, key: slugs.PostingKey) -> bool:
        return (key.url is not None and key.url in self.urls) or _identity(key) in self.identities


# A job's `source` names every source that found it, the first one first, joined by SOURCES_SEP
# (browser B10), at most SOURCES_MAX names and SOURCES_CHARS characters.
SOURCES_SEP = ", "
SOURCES_MAX = 4
SOURCES_CHARS = 600


def noted_sources(source: str | None) -> list[str]:
    return [name for name in (source or "").split(SOURCES_SEP) if name]


# --- one new job ---------------------------------------------------------------------------------


@dataclass
class Plan:
    """What a claimed sweep runs with."""

    sweep_id: uuid.UUID
    user_id: uuid.UUID
    rules: list[NeverApplyRule]
    skills: SkillsSection | None
    known: Known
    schedule_slot: str | None = None
    search: SearchSection | None = None  # for the score's bonus (scoring.bonus)
    search_scope: dict[str, Any] | None = None
    # The run's commits of verified findings (jobs J11b), set by run_sweep.
    commits: "Commits | None" = None


@dataclass
class _NewJob:
    row: dict[str, Any]
    events: list[dict[str, Any]]
    rule: NeverApplyRule | None


def _new_job(plan: Plan, company: str, posting: Posting, source: str, now: datetime) -> _NewJob:
    """The row of a new job, already scored and rule-checked, and its events: created, sweep,
    then the ones service.apply_score, set_status and set_notes would have written."""
    text = posting.text[:POSTING_TEXT_MAX]
    rule = next((r for r in plan.rules if r.matches(company, posting.title)), None)
    score = (
        scoring.score(text, plan.skills, title=posting.title, search=plan.search)
        if plan.skills is not None and text.strip()
        else None
    )
    job_id = uuid.uuid4()
    slug = slugs.unique_slug(slugs.slugify(company, posting.title), plan.known.slugs)
    contract = kind(posting.title, posting.location, posting.url)
    # Pay and work mode (opportunities O3): what the text and the location state in an accepted
    # shape, else nothing.
    pay = pays.pay_of(text, freelance=contract is ContractType.Freelance)
    work_mode = pays.work_mode_of(posting.location)
    row = {
        "id": job_id,
        "user_id": plan.user_id,
        "slug": slug,
        "company": company,
        "title": posting.title,
        "location": posting.location,
        "url": posting.url,
        "contract_type": contract,
        "source": source,
        "status": JobStatus.skipped if rule is not None else JobStatus.found,
        "fit": score.fit if score is not None else None,
        "fit_overridden": False,
        "competencies": list(score.competencies) if score is not None else [],
        "gaps": list(score.gaps) if score is not None else [],
        "description": text[:DESCRIPTION_CHARS] or None,
        "posting_text": text or None,
        "published_on": published_date(posting.published),
        "pay_min": pay.min if pay else None,
        "pay_max": pay.max if pay else None,
        "pay_currency": pay.currency if pay else None,
        "pay_period": pay.period if pay else None,
        "work_mode": work_mode,
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
    if any(s.get("source_id") for s in sweep.stats.get("sources", [])):
        sources = copy.deepcopy(sweep.stats["sources"])
        for source in sources:
            if not source.get("source_id") or source.get("phase") == "done":
                continue
            if source.get("phase") == "running":
                source["error"] = (
                    "sweep_failed" if status is SweepStatus.failed else "reader_interrupted"
                )
                finish_source(source)
            else:
                source.update(phase="done", reason=source.get("reason") or "reader_not_attempted")
        sweep.stats = {
            **sweep.stats,
            "sources": sources,
        }


def _stats_json(sweep: Sweep, stats: Stats) -> dict[str, Any]:
    """Progress owns counters; the locked persisted row owns immutable/control fields."""
    data = stats.as_json()
    stored = sweep.stats or {}
    for name in ("search_scope", "schedule_slot"):
        if name in stored:
            data[name] = copy.deepcopy(stored[name])
    if isinstance(stored.get("search_scope"), dict):
        previous = {s.get("source_id"): s for s in stored.get("sources", [])}
        updates = {s.get("source_id"): s for s in data["sources"]}
        data["sources"] = [
            _with_authority(
                {
                    **source,
                    **previous.get(source["source_id"], {}),
                    **updates.get(source["source_id"], {}),
                },
                previous.get(source["source_id"], {}),
            )
            for source in initial_sources(stored["search_scope"])
        ]
    return data


# What the platform guard's writers (opportunities/platforms/guard.py, browser B14) record in a
# source entry under the row lock: the locked row's value wins over a stale progress or final
# write of the same entry, so a reservation or a checkpoint is never lost.
GUARD_AUTHORITY = ("opened_at", "checkpoint_at", "platform")


def _with_authority(entry: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    for name in GUARD_AUTHORITY:
        if stored.get(name) is not None:
            entry[name] = stored[name]
    return entry


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
            sweep.stats = _stats_json(sweep, stats)
        _finish(sweep, SweepStatus.failed, now, error)
        db.commit()


def claim(sessions: sessionmaker[Session], sweep_id: uuid.UUID, now: datetime) -> Plan | None:
    """Lock the sweep and turn it into a Plan, or None when there is nothing to run (gone, not
    running, picked before by a worker that died, or no ticked website: failed then)."""
    with sessions() as db:
        sweep = db.get(Sweep, sweep_id, with_for_update=True)
        if sweep is None or sweep.status is not SweepStatus.running:
            return None
        if sweep.stats.get("picked_at"):
            _finish(sweep, SweepStatus.failed, now, CRASHED)
            db.commit()
            return None
        try:
            scope = sweep.stats.get("search_scope")
            if "search_scope" not in sweep.stats:
                # Pre-v1 runs intentionally use claim-time profile values.
                search = load_search(db, sweep.user_id)
            elif not isinstance(scope, dict) or scope.get("version") != 1:
                raise InvalidSearchError("unsupported frozen scope")
            else:
                search = (
                    SearchSection.model_validate(scope["search"])
                    if scope["search"] is not None
                    else None
                )
            if search is not None:
                check_terms(search)
            error = NO_WEBSITES
        except (ValueError, KeyError, re.error):
            search, error = None, SEARCH_INVALID
        slot = sweep.stats.get("schedule_slot")
        stats = Stats(
            picked_at=now.isoformat(),
            heartbeat_at=now.isoformat(),
            schedule_slot=slot if isinstance(slot, str) else None,
        )
        sweep.stats = _stats_json(sweep, stats)
        if search is None or not has_sources(search):
            if error == SEARCH_INVALID:
                logger.warning(
                    "sweep failed: the stored search section does not validate",
                    extra={"sweep_id": str(sweep.id), "user_id": str(sweep.user_id)},
                )
            _finish(sweep, SweepStatus.failed, now, error)
            db.commit()
            return None
        skills = service.user_skills(db, sweep.user_id) if scope is None else None
        if isinstance(scope, dict) and scope.get("skills") is not None:
            with contextlib.suppress(ValueError):
                skills = SkillsSection.model_validate(scope["skills"])
        plan = Plan(
            sweep_id=sweep.id,
            user_id=sweep.user_id,
            rules=never_apply_rules(search),
            skills=skills,
            known=Known(db, sweep.user_id),
            schedule_slot=stats.schedule_slot,
            search=search,
            search_scope=copy.deepcopy(scope),
        )
        db.commit()
    return plan


def save_progress(
    sessions: sessionmaker[Session], sweep_id: uuid.UUID, stats: Stats, now: datetime
) -> None:
    stats.heartbeat_at = now.isoformat()
    with sessions() as db:
        row = db.get(Sweep, sweep_id, with_for_update=True)
        if row is None or row.status is not SweepStatus.running:
            return
        row.stats = _stats_json(row, stats)
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
        sweep.stats = _stats_json(sweep, stats)
        _finish(sweep, SweepStatus.done, now)
        db.commit()


# --- running a sweep ----------------------------------------------------------------------------


Clock = Callable[[], datetime]
# The job websites' run (app/browser/sources, `Runner`): given the plan, the stats, the progress
# save and the sweep's monotonic deadline. It stores what it finds through `plan.commits`. The
# alert e-mails' run (app/mail/alerts/source.py) has the same shape and runs first.
BrowserHook = Callable[[Plan, Stats, Callable[[], Awaitable[None]], float], Awaitable[None]]


async def run_sweep(
    sessions: sessionmaker[Session],
    sweep_id: uuid.UUID,
    clock: Clock,
    *,
    timeout_s: float = SWEEP_TIMEOUT_S,
    heartbeat_s: float = HEARTBEAT_S,
    browser: BrowserHook | None = None,
    alerts: BrowserHook | None = None,
) -> None:
    """Run one sweep to its end (module docstring). Never raises for a website's failure; any
    other error fails the sweep and is re-raised only for cancellation."""
    plan = await asyncio.to_thread(claim, sessions, sweep_id, clock())
    if plan is None:
        return
    started = time.monotonic()
    stats = Stats(
        picked_at=clock().isoformat(),
        schedule_slot=plan.schedule_slot,
        sources=initial_sources(plan.search_scope),
    )
    commits = plan.commits = Commits(sessions, plan, stats, clock)

    async def progress() -> None:
        await commits.progress(
            lambda: asyncio.to_thread(save_progress, sessions, sweep_id, stats, clock())
        )

    try:
        async with asyncio.timeout(timeout_s):

            async def beat() -> None:
                while True:
                    await asyncio.sleep(heartbeat_s)
                    await progress()

            beating = asyncio.create_task(beat())
            try:
                if alerts is not None:
                    await alerts(plan, stats, progress, started + timeout_s)
                if browser is not None:
                    await browser(plan, stats, progress, started + timeout_s)
            finally:
                beating.cancel()
        stats.duration = round(time.monotonic() - started, 2)
        if await _settled(commits, sweep_id):
            await asyncio.to_thread(finish_done, sessions, sweep_id, stats, clock())
            logger.info(
                "sweep done",
                extra={"sweep_id": str(sweep_id), **_summary(stats)},
            )
    except TimeoutError:
        stats.duration = round(time.monotonic() - started, 2)
        if await _settled(commits, sweep_id):
            await asyncio.to_thread(fail_sweep, sessions, sweep_id, TIMED_OUT, clock(), stats)
    except asyncio.CancelledError:
        stats.duration = round(time.monotonic() - started, 2)
        try:
            settled = await asyncio.shield(_settled(commits, sweep_id))
        except asyncio.CancelledError:
            # A second cancellation: the run lets go of the row at once (no publication, no
            # end written over a commit still running), and reap_stale ends it.
            commits.abandon()
            raise
        if settled:
            await asyncio.shield(
                asyncio.to_thread(fail_sweep, sessions, sweep_id, INTERRUPTED, clock(), stats)
            )
        raise
    except Exception:
        logger.exception("sweep failed", extra={"sweep_id": str(sweep_id)})
        stats.duration = round(time.monotonic() - started, 2)
        if await _settled(commits, sweep_id):
            await asyncio.to_thread(fail_sweep, sessions, sweep_id, INTERNAL, clock(), stats)


async def _settled(commits: "Commits", sweep_id: uuid.UUID) -> bool:
    """Close the run's commits; False (logged) when one outlived the wait: the sweep row then
    keeps its committed counts, running, until reap_stale ends it."""
    if await commits.settle(close=True):
        return True
    logger.warning(
        "sweep left to recovery: a commit of verified findings did not settle",
        extra={"sweep_id": str(sweep_id)},
    )
    return False


def _summary(stats: Stats) -> dict[str, Any]:
    return {
        "websites": sum(1 for source in stats.sources if source.get("host")),
        "seen": stats.seen,
        "matched": stats.matched,
        "new": stats.new,
        "skipped_by_rule": stats.skipped_by_rule,
        "duration": stats.duration,
    }


# --- verified findings of the job websites (jobs J11 and J11b) ----------------------------------
#
# The interactive finder (app/browser/finder.py) reports postings it verified on their detail
# page in batches (finder.VerifiedBatch). Each batch is stored before the finder is told it was
# kept, in ONE transaction that also writes its counters into the locked sweep row, so the
# row's counts are those of the jobs stored, whatever happens to the worker next:
# - the job: `_new_job`: scored, status `found`, or `skipped` with the
#   rule as its note when a never apply rule of the person or the app's own rule against
#   cybersecurity matches), the website's host as its source, the text code read on the detail
#   page, the employer and the published date only when the page states them (UNKNOWN_EMPLOYER
#   and no date otherwise, never the website's name or today), `legacy` empty;
# - what was observed: in the `sweep` event's `discovery` (the source, when the detail page was
#   read, the lines of the page that state the title, employer, date and location, and which of
#   those were unknown); never a page snapshot, the model's words, criteria or instructions;
# - duplicates: same address, or same employer, title and location, against the person's list
#   and this sweep's earlier postings. A row another request stored meanwhile (the insert's
#   conflict) is looked up, the person's own rows only, and is the duplicate this posting is;
# - the search's include, exclude, location and days filters are not applied again: the
#   criteria guided the browsing, and an unknown date or location is not a mismatch.
#
# `Commits` is the run's one path to that transaction. A batch is admitted only while its
# source is the one running; it then runs as a task of its own, so a cancelled caller (the
# model's tool call torn down, the source's time limit) does not abandon a commit half way:
# the commit runs to its end and its counters are published to the run's Stats and Known on the
# event loop, after it. Commits and the run's progress writes run one at a time, so a progress
# write never carries counts older than a commit it follows. Before the next source, and before
# the end of the sweep, `settle` closes admission and waits up to DRAIN_S for what was admitted.
# A commit still running then leaves the sweep row as it is (running, with the counts committed
# so far) for `reap_stale`: no progress, no end is written that the commit could contradict.
# A commit that fails may still have landed (its COMMIT reached the database, the answer was
# lost): it is never retried, and `recover_verified` reads the row and the person's list again,
# so the run's counts and duplicates are those stored.

DRAIN_S = 5.0
VERIFIED_COUNTERS = ("seen", "matched", "new", "duplicates", "skipped_by_rule")
SOURCE_COUNTERS = ("found", "postings", "duplicates", "skipped_by_rule")


class StoreRefusedError(Exception):
    """The batch was not stored: its source no longer admits batches, or the sweep row is no
    longer this run's (ended, reaped, gone)."""


def _counted(stats: Stats, source_id: str, delta: Stats, found: int) -> None:
    for name in VERIFIED_COUNTERS:
        setattr(stats, name, getattr(stats, name) + getattr(delta, name))
    for entry in stats.sources:
        if entry.get("source_id") == source_id:
            entry["found"] = entry.get("found", 0) + found
            entry["postings"] = entry.get("postings", 0) + delta.new
            entry["duplicates"] = entry.get("duplicates", 0) + delta.duplicates
            entry["skipped_by_rule"] = entry.get("skipped_by_rule", 0) + delta.skipped_by_rule


def commit_verified(
    sessions: sessionmaker[Session],
    plan: Plan,
    known: Known,
    snapshot: Stats,
    source_id: str,
    source: str,
    findings: Sequence[Any],
    now: datetime,
) -> Stats | None:
    """Store one verified batch (above) with `known`, a copy of the plan's, and `snapshot`, a
    copy of the run's stats, both updated in place. Returns the counters the batch added, or
    None when the row is not this run's any more: then nothing is written."""
    staged = replace(plan, known=known, rules=[*plan.rules, CYBER_RULE])
    delta = Stats()
    with sessions() as db:
        row = db.get(Sweep, plan.sweep_id, with_for_update=True, populate_existing=True)
        if row is None or row.user_id != plan.user_id or row.status is not SweepStatus.running:
            return None
        for finding in findings:
            _store_finding(db, staged, source_id, source, finding, delta, now)
        _counted(snapshot, source_id, delta, len(findings))
        snapshot.heartbeat_at = now.isoformat()
        row.stats = _stats_json(row, snapshot)
        db.commit()
    return delta


def recover_verified(
    sessions: sessionmaker[Session], plan: Plan, source_id: str
) -> tuple[dict[str, int], dict[str, int], Known]:
    """After a failed commit: the locked row's verified counters (the run's and its source's)
    and a fresh Known of the person's own rows. The row's counters were written in the same
    transaction as the jobs, and every other write of the run carries the run's counts, which
    only grow: the larger of the row's and the run's is what is stored."""
    with sessions() as db:
        row = db.get(Sweep, plan.sweep_id, with_for_update=True, populate_existing=True)
        mine = row is not None and row.user_id == plan.user_id
        stored = (row.stats or {}) if mine else {}
        entry = next((s for s in stored.get("sources", []) if s.get("source_id") == source_id), {})

        def ints(data: dict[str, Any], names: Sequence[str]) -> dict[str, int]:
            return {n: data[n] for n in names if type(data.get(n)) is int}

        found = ints(stored, VERIFIED_COUNTERS), ints(entry, SOURCE_COUNTERS)
        known = Known(db, plan.user_id)
        db.rollback()
    return *found, known


def _store_finding(
    db: Session, plan: Plan, source_id: str, source: str, finding: Any, delta: Stats,
    now: datetime,
) -> None:  # fmt: skip
    company = finding.company or UNKNOWN_EMPLOYER
    posting = Posting(
        finding.title, finding.location, finding.url, finding.published_on or "", finding.text
    )
    delta.seen += 1
    delta.matched += 1
    key = slugs.PostingKey.of(company, posting.title, posting.location, posting.url)
    for _ in range(2):
        if plan.known.has(key):
            break
        new = _new_job(plan, company, posting, source, now)
        new.events[1]["data"]["discovery"] = {
            "source_id": source_id,
            "detail_read_at": finding.detail_read_at,
            "evidence": dict(finding.evidence),
            "unknown": [
                name
                for name, value in (
                    ("company", finding.company),
                    ("published_on", finding.published_on),
                    ("location", finding.location),
                )
                if not value
            ],
        }
        inserted = db.execute(
            pg_insert(Job).values(new.row).on_conflict_do_nothing().returning(Job.id)
        ).scalar_one_or_none()
        if inserted is not None:
            db.execute(sa.insert(JobEvent), new.events)
            plan.known.add(key, new.row["slug"], new.row["id"], source)
            delta.new += 1
            delta.skipped_by_rule += new.rule is not None
            return
        # Another request stored this address or took this slug meanwhile: learn the person's
        # rows in the way, then decide again (a duplicate, or a free slug).
        rows = db.execute(
            sa.select(Job.company, Job.title, Job.location, Job.url, Job.slug, Job.id, Job.source)
            .where(Job.user_id == plan.user_id)
            .where(sa.or_(Job.url == new.row["url"], Job.slug == new.row["slug"]))
        )
        for company_, title, location, url, slug, job_id, noted in rows:
            plan.known.add(slugs.PostingKey.of(company_, title, location, url), slug, job_id, noted)
    delta.duplicates += 1
    job_id = plan.known.job_of(key)
    joined = plan.known.note(job_id, source) if job_id is not None else None
    if joined is not None:
        db.execute(
            sa.update(Job)
            .where(Job.id == job_id, Job.user_id == plan.user_id)
            .values(source=joined, updated_at=Job.updated_at)
        )


class Commits:
    """The run's commits of verified batches (above): admitted per source, one at a time, each
    finished and published even when its caller is cancelled."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        plan: Plan,
        stats: Stats,
        clock: Clock,
        drain_s: float | None = None,
    ) -> None:
        self.sessions, self.plan, self.stats, self.clock = sessions, plan, stats, clock
        self.drain_s = DRAIN_S if drain_s is None else drain_s
        self._lock = asyncio.Lock()
        self._admitted: str | None = None
        self._closed = False
        self._running: set[asyncio.Task[None]] = set()
        self.unsettled = False  # a commit outlived `settle`: the row is left to reap_stale

    def admit(self, source_id: str) -> None:
        if not self._closed:
            self._admitted = source_id

    async def save(self, source_id: str, source: str, batch: Any) -> None:
        """Store `batch` for the running source `source_id`; StoreRefusedError when it is not
        admitted or the row refused it. A cancelled caller leaves the commit running."""
        if self._closed or source_id != self._admitted:
            raise StoreRefusedError
        task = asyncio.get_running_loop().create_task(self._commit(source_id, source, batch))
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        task.add_done_callback(_retrieved)  # a caller cancelled meanwhile never reads it
        await asyncio.shield(task)

    async def _commit(self, source_id: str, source: str, batch: Any) -> None:
        async with self._lock:
            snapshot = Stats(**copy.deepcopy(self.stats.as_json()))
            known = self.plan.known.copy()
            try:
                delta = await asyncio.to_thread(
                    commit_verified, self.sessions, self.plan, known, snapshot, source_id,
                    source, batch.findings, self.clock(),
                )  # fmt: skip
            except Exception:
                await self._recover(source_id)
                raise
            if delta is None:
                raise StoreRefusedError
            if self.unsettled:
                return  # the run let go of the row: nothing more is published
            vars(self.plan.known).update(vars(known))
            _counted(self.stats, source_id, delta, len(batch.findings))

    async def _recover(self, source_id: str) -> None:
        """The failed commit may have landed: take the row's counts and the person's list as
        they are stored (`recover_verified`). When even that read fails, the run lets go of
        the row, whose counters are those of its jobs, for reap_stale."""
        try:
            counters, entry_counters, known = await asyncio.to_thread(
                recover_verified, self.sessions, self.plan, source_id
            )
        except Exception:
            logger.exception("reading a sweep again after a failed commit failed")
            self.abandon()
            return
        if self.unsettled:
            return
        for name, value in counters.items():
            setattr(self.stats, name, max(getattr(self.stats, name), value))
        for entry in self.stats.sources:
            if entry.get("source_id") == source_id:
                for name, value in entry_counters.items():
                    entry[name] = max(entry.get(name, 0), value)
        vars(self.plan.known).update(vars(known))

    def abandon(self) -> None:
        """Let go of the row: no batch is admitted, nothing is published or written again."""
        self._admitted = None
        self._closed = self.unsettled = True

    async def progress(self, write: Callable[[], Awaitable[None]]) -> None:
        """A progress write of the run: never beside a commit (it would write counts without
        the commit's), held to its end even when its caller is cancelled, and none once the run
        let go of the row."""
        if self.unsettled:
            return
        async with self._lock:
            if self.unsettled:
                return
            task = asyncio.ensure_future(write())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.wait([task])  # its thread ends before another write starts
                raise

    async def settle(self, *, close: bool = False) -> bool:
        """Stop admitting (for good with `close`) and wait up to drain_s for the commits
        admitted. False when one is still running: the run must not write the row's end."""
        self._admitted = None
        self._closed = self._closed or close
        pending = set(self._running)
        if pending:
            _, left = await asyncio.wait(pending, timeout=self.drain_s)
            if left:
                self.abandon()
        return not self.unsettled


def _retrieved(task: asyncio.Task[None]) -> None:
    """A refused or failed commit was told to its caller, when it still had one."""
    if not task.cancelled():
        task.exception()


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


INVALID_LOG_EVERY = timedelta(hours=1)
# user id -> when its invalid section was last logged by scheduled_due (this process only).
_invalid_logged: dict[uuid.UUID, datetime] = {}


def _log_due(user_id: uuid.UUID, now: datetime) -> bool:
    """Whether the invalid-section warning of this user is due again (at most hourly)."""
    last = _invalid_logged.get(user_id)
    if last is not None and timedelta(0) <= now - last < INVALID_LOG_EVERY:
        return False
    _invalid_logged[user_id] = now
    return True


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
    active users whose schedule is enabled, with a ticked website, for slots in the window
    that have no sweep yet. A disabled schedule, a section that does not validate, an inactive
    user or a user without a ticked website never appears; a section that does not validate
    while its stored schedule is on is logged (the user id, never the contents) at most once
    per user per INVALID_LOG_EVERY in this process, since the user expects sweeps that cannot
    run until the Search tab is saved again."""
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
            stored = raw.get("sweep_schedule")
            if (
                isinstance(stored, dict)
                and stored.get("enabled") is True
                and _log_due(user_id, now)
            ):
                logger.warning(
                    "scheduled sweep skipped: the stored search section does not validate, "
                    "the user must save the Search tab again",
                    extra={"user_id": str(user_id)},
                )
            continue
        if not search.sweep_schedule.enabled or not has_sources(search):
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
    row.stats = {**row.stats, "schedule_slot": due.slot.key}
    db.flush()
    return row


def schedule_of(search: SearchSection | None) -> SweepSchedule:
    return search.sweep_schedule if search is not None else SweepSchedule()


def start(
    db: Session,
    user_id: uuid.UUID,
    trigger: SweepTrigger,
    now: datetime,
) -> Sweep:
    """A new running sweep for the user, after failing a stale one of theirs. Raises
    service.SweepRunningError when one is running; the caller commits."""
    profile = db.execute(
        sa.select(Profile).where(Profile.user_id == user_id).with_for_update()
    ).scalar_one_or_none()
    reap_stale(db, now, user_id)
    row = service.start_sweep(db, user_id, trigger, now)
    scope = freeze_scope(profile, trigger, now)
    row.stats = {**row.stats, "search_scope": scope, "sources": initial_sources(scope)}
    db.flush()
    return row


BeginOutcome = Literal[
    "started",
    "running",
    "no_websites",
    "search_invalid",
    "queue_down",
    "revision_conflict",
]


@dataclass(frozen=True)
class Begun[T]:
    """What `begin` did. `sweep_id` is the new sweep (started) or the one already running
    (running), None otherwise; `view` is the caller's view of the new sweep, read before its
    commit."""

    outcome: BeginOutcome
    sweep_id: uuid.UUID | None = None
    view: T | None = None


def _insert[T](
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    now: datetime,
    view: Callable[[Sweep], T],
    profile_revision: int | None = None,
) -> Begun[T]:
    with sessions() as db:
        profile = db.execute(
            sa.select(Profile).where(Profile.user_id == user_id).with_for_update()
        ).scalar_one_or_none()
        if profile_revision is not None and profile_revision != (
            profile.revision if profile else 0
        ):
            return Begun("revision_conflict")
        try:
            search = load_search(db, user_id)
        except InvalidSearchError:
            return Begun("search_invalid")
        if not has_sources(search):
            return Begun("no_websites")
        try:
            row = start(db, user_id, SweepTrigger.manual, now)
            begun = Begun("started", row.id, view(row))
            db.commit()
        except service.SweepRunningError:
            db.rollback()
            running = service.running_sweep(db, user_id)
            if running is None:  # finished between the insert and this read
                return _insert(sessions, user_id, now, view, profile_revision)
            return Begun("running", running.id)
    return begun


async def begin[T](
    sessions: sessionmaker[Session],
    queue: queues.Queue,
    user_id: uuid.UUID,
    now: datetime,
    view: Callable[[Sweep], T],
    profile_revision: int | None = None,
) -> Begun[T]:
    """Start a sweep by hand and queue it for the worker: the one start of POST /api/jobs/sweeps
    and of the first login wizard's Start (opportunities O5). No sweep without an enabled
    website (`no_websites`), or with a
    search section that no longer validates; the running one when the user has one; a queue
    that is down fails the new sweep at once, so it never blocks the next try."""
    begun = await asyncio.to_thread(_insert, sessions, user_id, now, view, profile_revision)
    if begun.outcome != "started" or begun.sweep_id is None:
        return begun
    try:
        await queue.enqueue(
            "run_sweep",
            str(begun.sweep_id),
            queue=queues.SWEEPS,
            job_id=queues.sweep_job_id(begun.sweep_id),
        )
    except queues.QueueUnavailableError:
        await asyncio.to_thread(fail_sweep, sessions, begun.sweep_id, QUEUE_DOWN, now)
        return Begun("queue_down")
    return begun


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
