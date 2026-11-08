"""Your search in numbers (SPEC-opportunities.md, "Your search in numbers"; owner, 2026-11-07).

No model: counts over the person's own rows only (`jobs`, their `status` and `note` events,
`applications`), facts about those rows, never advice. The floors and the silent window are
career-ops' (MIT, NOTICE): under its floor a rate or a median is None, which the page shows as
"too few to say".

What a posting reached is its status now, plus every status its status events moved it from
and to, less a status an undo took back (via `undo` or `*_undo`). From that:
- applied: reached applied, interview or rejected, or `applied_on` is set, or an application of
  it was clicked; liked: applied, or an application of it exists (a Like or Apply started one);
- answered: reached interview or rejected; interview: reached interview;
- passed: skipped now by a swipe Pass (its last status event), with that Pass's reason if any.

Silent: still `applied`, never answered, and nothing for SILENT_DAYS days or more since the
latest of `applied_on`, a status change, a note and a click of one of its applications. One
with none of these dates is counted apart (`undated`) and never listed. `close_silent` is the
person's click on the rows they ticked: each is read again under its lock and closed through
the jobs module's bulk change (its Undo too) only while it is still silent.
"""

import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from statistics import median
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import guardrails
from app.apply.models import Application, State
from app.jobs import service as jobs_service
from app.jobs import undo as undo_tokens
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from app.opportunities import passes

CLAIM_MIN = 20
BAND_MIN = 5
GROUP_MIN = 8
MEDIAN_MIN = 3
SILENT_DAYS = 60
BANDS = ("80_plus", "60_79", "under_60", "none")
_ANSWERS = frozenset({JobStatus.interview.value, JobStatus.rejected.value})
_APPLIED = _ANSWERS | {JobStatus.applied.value}
_CLICKED = frozenset({State.submitted, State.submit_unknown})


@dataclass
class Row:
    """One posting as the numbers read it."""

    id: uuid.UUID
    company: str
    title: str
    status: JobStatus
    fit: int | None
    channel: str
    ats: str
    reached: set[str]
    applied: bool
    liked: bool
    applied_on: date | None
    answered_on: date | None
    last_signal: date | None
    passed: bool
    pass_reason: str | None

    @property
    def answered(self) -> bool:
        return bool(self.reached & _ANSWERS)

    @property
    def interview(self) -> bool:
        return JobStatus.interview.value in self.reached

    @property
    def waiting(self) -> bool:
        return self.status is JobStatus.applied and not self.answered


@dataclass(frozen=True)
class Funnel:
    found: int
    liked: int
    applied: int
    answered: int
    interview: int
    rejected: int
    passed: int
    waiting: int
    answer_rate: int | None
    interview_rate: int | None


@dataclass(frozen=True)
class Group:
    key: str
    applied: int
    answered: int
    interviews: int
    rate: int | None


@dataclass(frozen=True)
class FirstAnswer:
    median_days: float | None
    measured: int
    undated: int
    waiting: int


@dataclass(frozen=True)
class ReasonCount:
    reason: str
    count: int


@dataclass(frozen=True)
class Silent:
    id: uuid.UUID
    company: str
    title: str
    applied_on: date | None
    last_signal: date
    days: int


@dataclass(frozen=True)
class Insights:
    silent_days: int
    funnel: Funnel
    by_fit: list[Group]
    by_ats: list[Group]
    by_source: list[Group]
    first_answer: FirstAnswer
    pass_reasons: list[ReasonCount]
    passes_without_reason: int
    silent: list[Silent]
    silent_undated: int


def reached(status: str, moves: Sequence[dict[str, Any]]) -> set[str]:
    """The statuses a posting held: `status` now and those its status events name, in order,
    less one an undo moved it out of."""
    held: set[str] = set()
    for data in moves:
        before, after, via = data.get("from"), data.get("to"), str(data.get("via") or "")
        if not held and isinstance(before, str):
            held.add(before)
        if via == "undo" or via.endswith("_undo"):
            held.discard(str(before))
        if isinstance(after, str):
            held.add(after)
    held.add(status)
    return held


def rate(part: int, whole: int, floor: int) -> int | None:
    """`part` of `whole` in percent, None under `floor`."""
    return round(100 * part / whole) if whole >= floor and whole > 0 else None


def band(fit: int | None) -> str:
    if fit is None:
        return "none"
    return "80_plus" if fit >= 80 else "60_79" if fit >= 60 else "under_60"


def channel(source: str | None) -> str:
    """The kind of the first source that found the posting ("linkedin" of
    "linkedin:ai engineer/Paris", "ashby" of a board, "mail" of alert e-mails)."""
    first = (source or "").split(", ")[0]
    return first.split(":")[0].strip().lower() or "unknown"


def _first_day(
    moves: Sequence[tuple[datetime, dict[str, Any]]], statuses: frozenset[str]
) -> date | None:
    return next((at.date() for at, data in moves if data.get("to") in statuses), None)


def _ats_of_address(url: str | None) -> str:
    return guardrails.ats_of(url) if url else "unknown"


def _rows(db: Session, user_id: uuid.UUID, ids: Sequence[uuid.UUID] | None = None) -> list[Row]:
    jobs = sa.select(
        Job.id, Job.company, Job.title, Job.status, Job.fit, Job.source, Job.url, Job.applied_on
    ).where(Job.user_id == user_id)
    events = (
        sa.select(JobEvent.job_id, JobEvent.kind, JobEvent.at, JobEvent.data)
        .where(
            JobEvent.user_id == user_id,
            JobEvent.kind.in_((JobEventKind.status, JobEventKind.note)),
        )
        .order_by(JobEvent.id)
    )
    applications = (
        sa.select(
            Application.posting_id, Application.ats, Application.state, Application.submitted_at
        )
        .where(Application.user_id == user_id)
        .order_by(Application.created_at)
    )
    if ids is not None:
        jobs = jobs.where(Job.id.in_(ids))
        events = events.where(JobEvent.job_id.in_(ids))
        applications = applications.where(Application.posting_id.in_(ids))
    moves: dict[uuid.UUID, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    signals: dict[uuid.UUID, list[date]] = defaultdict(list)
    for job_id, kind, at, data in db.execute(events):
        if kind is JobEventKind.status:
            moves[job_id].append((at, data))
        signals[job_id].append(at.date())
    started: dict[uuid.UUID, list[tuple[str, State, datetime | None]]] = defaultdict(list)
    for posting_id, ats, state, submitted_at in db.execute(applications):
        started[posting_id].append((ats, state, submitted_at))
    rows: list[Row] = []
    for job in db.execute(jobs):
        own = moves[job.id]
        held = reached(job.status.value, [data for _, data in own])
        clicks = [at.date() for _, state, at in started[job.id] if state in _CLICKED and at]
        applied_on = (
            job.applied_on or _first_day(own, frozenset({"applied"})) or min(clicks, default=None)
        )
        dates = [d for d in (applied_on, *signals[job.id], *clicks) if d is not None]
        last = own[-1][1] if own else {}
        passed = (
            job.status is JobStatus.skipped
            and last.get("via") == passes.VIA
            and last.get("to") == JobStatus.skipped.value
        )
        clicked = [ats for ats, state, _ in started[job.id] if state in _CLICKED]
        apps = [ats for ats, _, _ in started[job.id]]
        applied = bool(held & _APPLIED) or job.applied_on is not None or bool(clicks)
        ats = (clicked or apps or [_ats_of_address(job.url) if applied else ""])[-1]
        rows.append(
            Row(
                id=job.id,
                company=job.company,
                title=job.title,
                status=job.status,
                fit=job.fit,
                channel=channel(job.source),
                ats=ats,
                reached=held,
                applied=applied,
                liked=applied or bool(apps),
                applied_on=applied_on,
                answered_on=_first_day(own, _ANSWERS),
                last_signal=max(dates, default=None),
                passed=passed,
                pass_reason=last.get("reason")
                if passed and last.get("reason") in passes.REASONS
                else None,
            )
        )
    return rows


def is_silent(row: Row, today: date) -> bool:
    return (
        row.waiting
        and row.last_signal is not None
        and (today - row.last_signal).days >= SILENT_DAYS
    )


def _groups(
    rows: Sequence[Row], key: Callable[[Row], str], floor: int, order: Sequence[str] = ()
) -> list[Group]:
    members: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        members[key(row)].append(row)
    keys = list(order) or sorted(members, key=lambda k: (-len(members[k]), k))
    out: list[Group] = []
    for name in keys:
        group = members[name]
        interviews = sum(row.interview for row in group)
        out.append(
            Group(
                key=name,
                applied=len(group),
                answered=sum(row.answered for row in group),
                interviews=interviews,
                rate=rate(interviews, len(group), floor),
            )
        )
    return out


def _first_answer(applied: Sequence[Row]) -> FirstAnswer:
    days = [
        (row.answered_on - row.applied_on).days
        for row in applied
        if row.answered
        and row.answered_on is not None
        and row.applied_on is not None
        and row.answered_on >= row.applied_on
    ]
    answered = sum(row.answered for row in applied)
    return FirstAnswer(
        median_days=float(median(days)) if len(days) >= MEDIAN_MIN else None,
        measured=len(days),
        undated=answered - len(days),
        waiting=sum(row.waiting for row in applied),
    )


def insights(db: Session, user_id: uuid.UUID, today: date) -> Insights:
    """The person's numbers as of `today` (the caller reads them in one snapshot)."""
    rows = _rows(db, user_id)
    applied = [row for row in rows if row.applied]
    answered = sum(row.answered for row in applied)
    interviews = sum(row.interview for row in applied)
    passed = [row for row in rows if row.passed]
    reasons = Counter(row.pass_reason for row in passed if row.pass_reason)
    silent = sorted(
        (row for row in applied if is_silent(row, today)),
        key=lambda row: (row.last_signal, row.company, row.title),
    )
    return Insights(
        silent_days=SILENT_DAYS,
        funnel=Funnel(
            found=len(rows),
            liked=sum(row.liked for row in rows),
            applied=len(applied),
            answered=answered,
            interview=interviews,
            rejected=sum(JobStatus.rejected.value in row.reached for row in applied),
            passed=len(passed),
            waiting=sum(row.waiting for row in applied),
            answer_rate=rate(answered, len(applied), CLAIM_MIN),
            interview_rate=rate(interviews, len(applied), CLAIM_MIN),
        ),
        by_fit=_groups(applied, lambda row: band(row.fit), BAND_MIN, BANDS),
        by_ats=_groups(applied, lambda row: row.ats, GROUP_MIN),
        by_source=_groups(applied, lambda row: row.channel, GROUP_MIN),
        first_answer=_first_answer(applied),
        pass_reasons=[ReasonCount(reason, count) for reason, count in reasons.most_common()],
        passes_without_reason=len(passed) - sum(reasons.values()),
        silent=[
            Silent(
                id=row.id,
                company=row.company,
                title=row.title,
                applied_on=row.applied_on,
                last_signal=row.last_signal,
                days=(today - row.last_signal).days,
            )
            for row in silent
            if row.last_signal is not None
        ],
        silent_undated=sum(row.waiting and row.last_signal is None for row in applied),
    )


@dataclass(frozen=True)
class Closed:
    closed: list[uuid.UUID]
    skipped: list[uuid.UUID]
    undo: list[undo_tokens.Previous]


def close_silent(
    db: Session, user_id: uuid.UUID, ids: Sequence[uuid.UUID], now: datetime
) -> Closed:
    """Close the ticked postings that are still silent, in the caller's transaction; the others
    (answered, moved, not the person's) are `skipped` and stay as they are."""
    wanted = list(dict.fromkeys(ids))
    try:
        jobs_service.lock_jobs(db, user_id, wanted)
    except jobs_service.MissingJobsError as exc:
        wanted = [job_id for job_id in wanted if job_id not in set(exc.missing)]
        jobs_service.lock_jobs(db, user_id, wanted)
    still = [row.id for row in _rows(db, user_id, wanted) if is_silent(row, now.date())]
    if not still:
        return Closed(closed=[], skipped=list(dict.fromkeys(ids)), undo=[])
    outcome = jobs_service.bulk_update(db, user_id, still, now, status=JobStatus.closed)
    closed = [result.job.id for result in outcome.results if result.changed]
    return Closed(
        closed=closed,
        skipped=[job_id for job_id in dict.fromkeys(ids) if job_id not in set(closed)],
        undo=outcome.undo,
    )
