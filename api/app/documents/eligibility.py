"""Which found postings get their documents without a click (task D10; SPEC-documents.md,
"Automatic generation", "Eligible posting", "The safety ceiling", "Waiting, never failing").

Pure: every function takes what it decides on as data (the posting's row as `Posting`, the
person's setting, the never apply rules, `today` and `now` passed in), reads no clock and no
database, and gives the same answer for the same input. `tick.py` reads the rows and acts.

**Eligible** (`refusal(...) is None`), all of:
- automatic generation is on (`search.documents.auto`);
- the posting is `found` or `shortlisted`;
- its fit score is present and at or above the threshold (`search.documents.min_fit`): a fit
  exactly at the threshold is written, one under is not, no fit is not;
- no never apply rule of the search section matches its company and title (the sweep moves a
  matching posting it inserts to `skipped`; a posting added by hand is matched here);
- it was found in the last `lookback_days` days, today included (30: `found_on` 29 days before
  today is in, 30 days before is out);
- it holds no document the command line made (an imported posting with legacy files).
The consent, the credential and the base CV are not part of the rule: a posting that meets it
gets its generation, `waiting` with the reason (`wait_reason`) until they exist.

**Order** (`order_key`): highest fit first, then the newest found, then the id, so two ticks
order the same postings the same way.

**The retry** (`next_try`): a posting's document whose automatic generation failed is written once
more, never twice: after `retry_after_s` (the provider down), or at once with the token ceiling
raised by one high estimate when the run stopped at its ceiling (`token_limit`). A generation
the person stopped (cancelled once started) is never retried by the tick.

**The day of the ceiling** (`ceiling_day`): the date of `now` in the person's time zone (the one
of `search.sweep_schedule`), so the first tick after midnight there starts what waited.
"""

from __future__ import annotations

import enum
import zoneinfo
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from app.jobs.models import JobStatus

OPEN_STATUSES = frozenset({JobStatus.found, JobStatus.shortlisted})
STOPPED_AT_CEILING = "token_limit"  # generate.ERRORS: the run stopped at its ceiling
RETRIES = 1  # a failed automatic generation is written once more, never twice
DEFAULT_TIMEZONE = "Europe/Paris"  # schemas.SweepSchedule's default


class Refusal(enum.StrEnum):
    """Why a posting gets no automatic generation (logged as a count, never with the posting)."""

    auto_off = "auto_off"
    status = "status"
    no_fit = "no_fit"
    under_threshold = "under_threshold"
    never_apply = "never_apply"
    too_old = "too_old"
    legacy = "legacy"


class Rule(Protocol):
    def matches(self, company: str, title: str) -> bool: ...


@dataclass(frozen=True)
class Setting:
    """The block `search.documents` of the profile, defaulted."""

    auto: bool
    min_fit: int
    daily_ceiling: int


@dataclass(frozen=True)
class Posting:
    status: JobStatus
    fit: int | None
    found_on: date
    company: str
    title: str
    legacy: bool = False  # holds files the command line made


def refusal(
    posting: Posting,
    setting: Setting,
    rules: Iterable[Rule],
    today: date,
    lookback_days: int,
) -> Refusal | None:
    """None when the posting is eligible, else the first reason it is not (module docstring)."""
    if not setting.auto:
        return Refusal.auto_off
    if posting.status not in OPEN_STATUSES:
        return Refusal.status
    if posting.fit is None:
        return Refusal.no_fit
    if posting.fit < setting.min_fit:
        return Refusal.under_threshold
    if posting.found_on < since(today, lookback_days):
        return Refusal.too_old
    if posting.legacy:
        return Refusal.legacy
    if any(rule.matches(posting.company, posting.title) for rule in rules):
        return Refusal.never_apply
    return None


def since(today: date, lookback_days: int) -> date:
    """The first day of the window: `lookback_days` days, today included."""
    return today - timedelta(days=lookback_days - 1)


def order_key(fit: int | None, found_on: date, ident: str) -> tuple[int, int, str]:
    """Highest fit, then newest, then the id: a total order."""
    return (-(fit if fit is not None else -1), -found_on.toordinal(), ident)


@dataclass(frozen=True)
class Failure:
    error: str | None
    finished_at: datetime
    token_ceiling: int


@dataclass(frozen=True)
class Retry:
    token_ceiling: int  # the ceiling of the new generation


def next_try(
    failures: Sequence[Failure],
    *,
    stopped: bool,
    now: datetime,
    retry_after_s: int,
    ceiling: int,
    high: int,
) -> Retry | None:
    """The next automatic try of a document, None when it gets none now. `failures` are its
    failed generations, oldest first; `stopped` says one was cancelled once started (the person
    stopped it); `ceiling` and `high` are the fresh estimate's. A first try takes `ceiling`."""
    if stopped:
        return None
    if not failures:
        return Retry(ceiling)
    if len(failures) > RETRIES:
        return None
    last = failures[-1]
    if last.error == STOPPED_AT_CEILING:
        return Retry(max(ceiling, last.token_ceiling + high))
    if now >= last.finished_at + timedelta(seconds=retry_after_s):
        return Retry(ceiling)
    return None


def zone(timezone: str | None) -> zoneinfo.ZoneInfo:
    """The person's time zone, the schedule's default for a name that does not resolve."""
    try:
        return zoneinfo.ZoneInfo(timezone or DEFAULT_TIMEZONE)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)


def ceiling_day(now: datetime, timezone: str | None) -> date:
    """The day of the safety ceiling: `now` in the person's time zone."""
    return now.astimezone(zone(timezone)).date()


def room(daily_ceiling: int, started_today: int) -> int:
    """How many more postings may start today (never under zero)."""
    return max(0, daily_ceiling - started_today)
