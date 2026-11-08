"""What the person should know about a posting before the click (owner, 2026-11-07, "build all" of
the career-ops analysis; SPEC-jobs, "Before the click"), shown on the posting page, the Discover
card and the Apply confirmation:

- the posting's own signals (app/jobs/signals.py) read against the person's profile: the work
  authorisation of `answers` for the posting's country (its location, through the gazetteer) or
  the place it names, and the pay the person aims for (`search.compensation`, compared only in
  the same currency and period);
- its closing date;
- a posting of another company with nearly the same text (`signals.same_text`);
- the person's applications to the same company (folded as `guardrails.company_key` folds).

Information only, never a refusal (A19, A20; the app adds no rule of its own about a kind of
role, 2026-11-05): a notice is a sentence of facts, with the posting's own words quoted, never a
verdict. It reads the person's own profile and rows only.
"""

import contextlib
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply.answers import EU, EU_MEMBERS, posting_countries
from app.apply.guardrails import company_key
from app.jobs import signals as texts
from app.jobs.models import ContractType, Job, JobStatus
from app.jobs.sweep import SWEEP_TZ
from app.opportunities.pay import pay_text
from app.profile.models import Profile
from app.profile.schemas import AnswersSection, PayRange, SearchSection, WorkAuthorization

Kind = Literal["signal", "closing", "cross_listing", "history"]
Level = Literal["info", "warning"]

SOON_DAYS = 7  # a closing date this close is shown first on the deck
APPLIED = frozenset({JobStatus.applied, JobStatus.interview, JobStatus.rejected})
_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)  # fmt: skip
_WITH_THE = frozenset(
    {
        "United Kingdom",
        "United States",
        EU,
        "European Economic Area",
        "Schengen Area",
        "United Arab Emirates",
        "Netherlands",
    }
)


@dataclass(frozen=True)
class Notice:
    kind: Kind
    code: str
    text: str
    quote: str | None = None
    level: Level = "info"
    job_id: uuid.UUID | None = None  # the other posting it names


@dataclass(frozen=True)
class Person:
    """What of the profile a notice compares with."""

    work: tuple[WorkAuthorization, ...] = ()
    salary: PayRange | None = None
    daily_rate: PayRange | None = None


@dataclass(frozen=True)
class Row:
    """Another posting of the person, by its light columns."""

    id: uuid.UUID
    company: str
    title: str
    status: JobStatus
    applied_on: date | None
    found_on: date
    text_fingerprint: int | None


def today_of(now: datetime) -> date:
    """The day a posting is found on (the sweep's time zone), for the closing date."""
    return now.astimezone(SWEEP_TZ).date()


def long_date(day: date) -> str:
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def closes_soon(closes_on: date | None, today: date) -> bool:
    return closes_on is not None and 0 <= (closes_on - today).days <= SOON_DAYS


def _place(name: str) -> str:
    return f"the {name}" if name in _WITH_THE else name


# --- the profile and the rows -------------------------------------------------------------------


def person_of(db: Session, user_id: uuid.UUID) -> Person:
    """The work authorisation and the pay aimed for, each empty when its section was never saved
    or no longer validates."""
    sections = (
        db.execute(
            sa.select(Profile.sections).where(Profile.user_id == user_id)
        ).scalar_one_or_none()
        or {}
    )
    work: tuple[WorkAuthorization, ...] = ()
    salary = daily_rate = None
    with contextlib.suppress(ValueError):
        work = tuple(
            AnswersSection.model_validate(sections.get("answers") or {}).work_authorization
        )
    with contextlib.suppress(ValueError):
        pay = SearchSection.model_validate(sections.get("search") or {}).compensation
        salary, daily_rate = pay.salary, pay.daily_rate
    return Person(work=work, salary=salary, daily_rate=daily_rate)


def rows_of(db: Session, user_id: uuid.UUID) -> list[Row]:
    columns = (
        Job.id, Job.company, Job.title, Job.status, Job.applied_on, Job.found_on,
        Job.text_fingerprint,
    )  # fmt: skip
    return [Row(*row) for row in db.execute(sa.select(*columns).where(Job.user_id == user_id))]


# --- the posting's signals ----------------------------------------------------------------------


def _entry(person: Person, country: str) -> WorkAuthorization | None:
    """The person's entry for a country, or for an EU member state without one, the EU's."""
    by_place = {texts.place_of(entry.country): entry for entry in person.work}
    entry = by_place.get(country)
    if entry is None and country in EU_MEMBERS:
        entry = by_place.get(EU)
    return entry


def _needs_sponsorship(person: Person, countries: Iterable[str]) -> str | None:
    """The first of the countries where the profile says the person needs sponsorship."""
    for country in countries:
        entry = _entry(person, country)
        if entry is not None and entry.needs_sponsorship:
            return country
    return None


def _work_permit(person: Person, place: str, quote: str) -> Notice:
    entry = _entry(person, place)
    asks = f"This posting asks for the right to work in {_place(place)}"
    if entry is not None and entry.needs_sponsorship:
        text = f"{asks}, and your profile says you need sponsorship there."
        return Notice("signal", "work_permit", text, quote, "warning")
    if entry is None and person.work:
        text = f"{asks}; your profile names no work authorisation there."
        return Notice("signal", "work_permit", text, quote)
    return Notice("signal", "work_permit", f"{asks}.", quote)


_PLAIN: dict[str, tuple[str, Level]] = {
    "sponsorship": ("This posting says it offers visa sponsorship.", "info"),
    "degree": ("This posting asks for a degree.", "info"),
    "contractor": ("This posting hires as an independent contractor.", "info"),
    "fixed_term": ("This posting mentions a fixed-term contract.", "info"),
    "unpaid": ("This posting says the role is unpaid.", "warning"),
    "candidate_fee": ("This posting asks the candidate to pay a fee.", "warning"),
}


def signal_notices(job: Job, said: Sequence[texts.Signal], person: Person) -> list[Notice]:
    """The posting's signals as sentences, read against the person's profile. A contract the
    stored contract type already says (Freelance, a fixed term other than CDI) is not repeated."""
    countries = posting_countries(job.location)
    needs = _needs_sponsorship(person, countries)
    found: list[Notice] = []
    for signal in said:
        if signal.code == "no_sponsorship":
            text = "This posting says it does not sponsor visas"
            if needs is not None:
                text += f", and your profile says you need sponsorship in {_place(needs)}"
            level: Level = "warning" if needs is not None else "info"
            found.append(Notice("signal", signal.code, f"{text}.", signal.quote, level))
        elif signal.code == "work_permit" and signal.value:
            found.append(_work_permit(person, signal.value, signal.quote))
        elif (signal.code == "contractor" and job.contract_type is ContractType.Freelance) or (
            signal.code == "fixed_term" and job.contract_type is not ContractType.CDI
        ):
            continue
        elif signal.code in _PLAIN:
            text, level = _PLAIN[signal.code]
            found.append(Notice("signal", signal.code, text, signal.quote, level))
    stated = {"no_sponsorship", "sponsorship", "work_permit"} & {s.code for s in said}
    if needs is not None and not stated:
        found.append(
            Notice(
                "signal",
                "sponsorship_unstated",
                "This posting says nothing about visa sponsorship, and your profile says you "
                f"need it in {_place(needs)}.",
            )
        )
    pay = _pay_below(job, person)
    if pay is not None:
        found.append(pay)
    return found


def _pay_below(job: Job, person: Person) -> Notice | None:
    """The stated pay's top under the pay the person aims for, in the same currency and
    period only (a yearly salary, or a daily rate)."""
    if job.pay_max is None or job.pay_currency is None or job.pay_period is None:
        return None
    period = str(job.pay_period)
    floor = person.salary if period == "year" else person.daily_rate if period == "day" else None
    if floor is None or floor.currency != job.pay_currency or job.pay_max >= floor.min:
        return None
    top = pay_text(job.pay_max, job.pay_max, job.pay_currency, period)
    aim = pay_text(floor.min, floor.min, floor.currency, period)
    return Notice(
        "signal",
        "pay_below",
        f"The stated pay tops out at {top}, under the {aim} you aim for.",
        level="warning",
    )


def closing_notice(closes_on: date, quote: str | None, today: date) -> Notice:
    days = (closes_on - today).days
    when = long_date(closes_on)
    if days < 0:
        text = f"This posting gave {when} as its closing date."
    elif days == 0:
        text = f"This posting closes today, {when}."
    elif days == 1:
        text = f"This posting closes tomorrow, {when}."
    elif days <= SOON_DAYS:
        text = f"This posting closes on {when}, in {days} days."
    else:
        text = f"This posting closes on {when}."
    level: Level = "warning" if days <= SOON_DAYS else "info"
    return Notice("closing", "closes_on", text, quote, level)


# --- the person's other postings ----------------------------------------------------------------


def _applied(row: Row) -> bool:
    return row.applied_on is not None or row.status in APPLIED


def _outcome(row: Row) -> str:
    if row.status is JobStatus.interview:
        return "interview"
    if row.status is JobStatus.rejected:
        return "rejected"
    if row.status in (JobStatus.closed, JobStatus.skipped):
        return "closed"
    return "waiting"


_ONE_OUTCOME = {
    "interview": ", and an interview followed.",
    "rejected": ", and it ended in a rejection.",
    "closed": ", and that posting is now closed.",
}
_COUNTED = {
    "interview": ("interview", "interviews"),
    "rejected": ("rejection", "rejections"),
    "closed": ("closed", "closed"),
    "waiting": ("with no reply recorded", "with no reply recorded"),
}


def history_notice(job: Job, rows: Sequence[Row], today: date) -> Notice | None:
    """The person's applications to the posting's company, the latest first: one told whole,
    several counted by outcome. None when there is none."""
    key = company_key(job.company)
    if not key:
        return None
    mine = sorted(
        (r for r in rows if r.id != job.id and _applied(r) and company_key(r.company) == key),
        key=lambda r: (r.applied_on or date.min, r.found_on, str(r.id)),
        reverse=True,
    )
    if not mine:
        return None
    latest = mine[0]
    when = f"on {long_date(latest.applied_on)}" if latest.applied_on else "(date not recorded)"
    if len(mine) == 1:
        outcome = _outcome(latest)
        tail = _ONE_OUTCOME.get(outcome, ", with no reply recorded")
        if outcome == "waiting":
            days = (today - latest.applied_on).days if latest.applied_on else None
            tail += f" in {days} days." if days is not None and days > 0 else "."
        text = f"You applied to {job.company} {when} for {latest.title}{tail}"
    else:
        counts: dict[str, int] = {}
        for row in mine:
            counts[_outcome(row)] = counts.get(_outcome(row), 0) + 1
        parts = [
            f"{n} {_COUNTED[name][0] if n == 1 else _COUNTED[name][1]}"
            for name in ("interview", "rejected", "closed", "waiting")
            if (n := counts.get(name, 0))
        ]
        text = (
            f"You applied to {job.company} {len(mine)} times, most recently {when} for "
            f"{latest.title}: {', '.join(parts)}."
        )
    return Notice("history", "applied_before", text, job_id=latest.id)


def cross_listing_notice(job: Job, fingerprint: int | None, rows: Sequence[Row]) -> Notice | None:
    """A posting of another company whose text is nearly the same, the one applied to first."""
    key = company_key(job.company)
    same = sorted(
        (
            r
            for r in rows
            if r.id != job.id
            and company_key(r.company) != key
            and texts.same_text(fingerprint, r.text_fingerprint)
        ),
        key=lambda r: (not _applied(r), -r.found_on.toordinal(), str(r.id)),
    )
    if not same:
        return None
    first = same[0]
    named = f"Its text is nearly the same as {first.title} at {first.company}"
    level: Level = "warning" if _applied(first) else "info"
    if _applied(first):
        when = f" on {long_date(first.applied_on)}" if first.applied_on else ""
        text = f"{named}, which you applied to{when}."
    else:
        text = f"{named}, found on {long_date(first.found_on)}."
    if len(same) > 1:
        text += f" It matches {len(same) - 1} more of your postings too."
    return Notice("cross_listing", "same_text", text, level=level, job_id=first.id)


# --- all of them --------------------------------------------------------------------------------


def _reading(job: Job) -> tuple[list[texts.Signal], int | None]:
    """The stored signals and fingerprint, or, on a row never read since migration 0038, those
    of its text read now."""
    if job.signals is not None:
        said = [
            texts.Signal(str(s.get("code")), str(s.get("quote") or ""), s.get("value"))
            for s in job.signals
            if isinstance(s, dict)
        ]
        return said, job.text_fingerprint
    return texts.read(job.posting_text), texts.fingerprint(job.posting_text)


def notices_for(job: Job, person: Person, rows: Sequence[Row], today: date) -> list[Notice]:
    """Every notice of one posting, the warnings first."""
    said, fingerprint = _reading(job)
    found = signal_notices(job, said, person)
    closes = next((s for s in said if s.code == "closing" and s.value), None)
    if closes is not None:
        found.append(closing_notice(date.fromisoformat(str(closes.value)), closes.quote, today))
    for notice in (cross_listing_notice(job, fingerprint, rows), history_notice(job, rows, today)):
        if notice is not None:
            found.append(notice)
    return sorted(found, key=lambda n: n.level != "warning")


def notices_of(
    db: Session, user_id: uuid.UUID, jobs: Sequence[Job], today: date
) -> dict[uuid.UUID, list[Notice]]:
    """The notices of the person's postings, by id: the profile and the rows read once."""
    if not jobs:
        return {}
    person = person_of(db, user_id)
    rows = rows_of(db, user_id)
    return {job.id: notices_for(job, person, rows, today) for job in jobs}
