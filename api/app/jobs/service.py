"""Reading and writing a user's jobs, files and sweeps: the queries the J2+ routes build on.

Every function takes the caller's `user_id` and touches that user's rows only: there is no
function that reads a job, a file, an event or a sweep by id alone. A job of another user is
indistinguishable from a missing one (None), so the routes answer 404 for both.
The caller owns the transaction and commits it; functions flush so ids and constraint errors
show up at once. `now` is passed in (the app's clock), never read here. Nothing here logs
posting texts or file contents.
"""

import base64
import binascii
import enum
import json
import re
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, SessionTransaction, undefer

from app.agents.models import Proposal, ProposalDecider, ProposalStatus
from app.jobs import criteria_badge, scoring, searches, signals
from app.jobs import slug as slugs
from app.jobs import undo as undo_tokens
from app.jobs.models import (
    VISIBLE_COLUMNS,
    ContractType,
    FileOrigin,
    Job,
    JobEvent,
    JobEventKind,
    JobFile,
    JobStatus,
    PayPeriod,
    SavedSearch,
    Sweep,
    SweepStatus,
    SweepTrigger,
    WorkMode,
)
from app.jobs.never_apply import NeverApplyRule
from app.jobs.schemas import ImportFile, ImportJob
from app.jobs.scoring import ScoreResult
from app.jobs.visible_text import visible
from app.profile import matching
from app.profile.models import Profile
from app.profile.schemas import SearchSection, SkillsSection


class DuplicateJobError(Exception):
    """The posting is already in the user's list. Carries the existing job."""

    def __init__(self, job: Job) -> None:
        super().__init__("duplicate posting")
        self.job = job


class SweepRunningError(Exception):
    """The user already has a running sweep."""


# --- reading ------------------------------------------------------------------------------------


def jobs_of(user_id: uuid.UUID) -> sa.Select[tuple[Job]]:
    """The base query of every job read: the user's jobs, nothing else."""
    return sa.select(Job).where(Job.user_id == user_id)


def get_job(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> Job | None:
    return db.execute(jobs_of(user_id).where(Job.id == job_id)).scalar_one_or_none()


def get_job_by_slug(db: Session, user_id: uuid.UUID, slug: str) -> Job | None:
    return db.execute(jobs_of(user_id).where(Job.slug == slug)).scalar_one_or_none()


class WorkModeChoice(enum.StrEnum):
    """The work mode filter: a mode the posting states, or none stated."""

    remote = "remote"
    hybrid = "hybrid"
    office = "office"
    not_stated = "not_stated"


class PayChoice(enum.StrEnum):
    stated = "stated"
    not_stated = "not_stated"


class ListingChoice(enum.StrEnum):
    """The listing filter (jobs J31): every posting, only those seen in a list (their own page
    not read), or those whose page was read."""

    all = "all"
    only = "only"
    without = "without"


# Jobs J31: what Write now and Apply answer for a posting seen only in a list.
LISTED_WAITS = (
    "This posting was seen only in a list: its text is needed first. It is ready once a search "
    "reads its own page."
)


def listed_refusal(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> str | None:
    """Jobs J31: the sentence of the 409 of Write now and Apply on a posting seen only in a list
    (its own page not read), else None (another person's posting, or none, too)."""
    listed = db.execute(
        sa.select(Job.list_url).where(Job.id == job_id, Job.user_id == user_id)
    ).scalar_one_or_none()
    return LISTED_WAITS if listed is not None else None


DESCRIPTION_CHARS = 300  # sweep.DESCRIPTION_CHARS
# Jobs J31b: reader.CUT_MIN_CHARS, under which a piece of a page is no posting's text.
TEXT_MIN_CHARS = 300
TEXT_SHORT = (
    "This text is too short to be the posting's: paste its whole text, from its title to how to "
    "apply."
)
PAGE_READ_ALREADY = "Its own page was read meanwhile: the posting already has its text."


# Jobs J36: a failed reading of the page of a posting seen only in a list, a `sweep` event of
# the posting holding this key, written once a day (Europe/Paris); after PAGE_TRIES_DAYS such
# days the app no longer opens its page.
PAGE_UNREAD = "page_unread"
PAGE_TRIES_DAYS = 3
PAGE_TRIES_TZ = "Europe/Paris"  # sweep.SWEEP_TZ


def unread_day() -> Any:
    """The day, in PAGE_TRIES_TZ, of a job event (J36)."""
    return sa.func.date(sa.func.timezone(PAGE_TRIES_TZ, JobEvent.at))


def given_up(user_id: uuid.UUID) -> sa.Select[tuple[uuid.UUID]]:
    """Jobs J36: the person's postings whose page the app failed to read on PAGE_TRIES_DAYS
    different days: no longer tried, their mention says so."""
    return (
        sa.select(JobEvent.job_id)
        .where(
            JobEvent.user_id == user_id,
            JobEvent.kind == JobEventKind.sweep,
            JobEvent.data.has_key(PAGE_UNREAD),
        )
        .group_by(JobEvent.job_id)
        .having(sa.func.count(sa.distinct(unread_day())) >= PAGE_TRIES_DAYS)
    )


def pages_given_up(
    db: Session, user_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Which of these postings the app no longer tries to read (J36, `given_up`), each with the
    reason of its latest failed reading (`walled`: its page asked to sign in, to pass a check
    or to slow down; else "")."""
    wanted = list(ids)
    if not wanted:
        return {}
    gone = given_up(user_id).where(JobEvent.job_id.in_(wanted))
    rows = db.execute(
        sa.select(JobEvent.job_id, JobEvent.data[PAGE_UNREAD]["reason"].astext)
        .where(
            JobEvent.user_id == user_id,
            JobEvent.kind == JobEventKind.sweep,
            JobEvent.data.has_key(PAGE_UNREAD),
            JobEvent.job_id.in_(gone),
        )
        .order_by(JobEvent.at.desc(), JobEvent.id.desc())
    )
    latest: dict[uuid.UUID, str] = {}
    for job_id, reason in rows:
        latest.setdefault(job_id, reason or "")
    return latest


def page_elsewhere(url: str | None, source: str | None) -> bool:
    """Jobs J36: whether no website's reading opens the page of this posting seen in a list:
    its address is on the own hosts (`finder.on_lease_hosts`) of none of the websites that
    found it (an employer's application system, another board, the web source)."""
    from urllib.parse import urlsplit

    from app.browser import finder

    try:
        parts = urlsplit(url or "")
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return True
    hosts = [name for name in (source or "").split(SOURCES_SEP) if name]
    return not any(
        finder.on_lease_hosts(url or "", f"{parts.scheme}://{host}{port}/") for host in hosts
    )


class TextRefusedError(Exception):
    """Jobs J31b: a text the person pasted that the posting does not take, with its code, its
    sentence and its status."""

    def __init__(self, code: str, detail: str, status: int) -> None:
        super().__init__(code)
        self.code, self.detail, self.status = code, detail, status


class SearchUnreadableError(Exception):
    """The stored search a posting's exclusions read no longer validates or compiles."""


def page_text(
    db: Session,
    job: Job,
    text: str,
    event: tuple[JobEventKind, dict[str, Any]],
    now: datetime,
    *,
    rule: NeverApplyRule | None,
    excluded: str,
    skills: SkillsSection | None,
    search: SearchSection | None,
    via: str,
    published: date | None = None,
    stated: ContractType | None = None,
) -> bool:
    """Jobs J31: the text of the own page of a posting kept from a card of a list replaces the
    card's (`job`, locked, its text loaded), as a later search's read (`sweep._page_read`) and
    the person's paste (`set_listed_text`) do: the never apply `rule` its title and company
    met, else the person's unwanted title or place it met (`excluded`), skips a posting still
    `found` with it as its note (a status the person set stays); the mention goes, the posting
    reader reads it again, its date and its stated contract are taken when it had none, and it
    is scored again (a fit the person set stays), the fit event `via` the text's source. Writes
    `event` first, then the others; the caller sets `updated_at` and flushes. Returns whether
    `rule` skipped it."""
    text = visible(text)
    events = [event]
    note, why = (rule.text, "never_apply") if rule is not None else (excluded, "unwanted")
    skipped = False
    if note and job.status is JobStatus.found:
        events.append((JobEventKind.status, {"from": "found", "to": "skipped", "via": why}))
        job.status = JobStatus.skipped
        skipped = rule is not None
        if not job.notes:
            events.append((JobEventKind.note, {"from": "", "to": note, "via": why}))
            job.notes = note
    job.list_url = None
    job.posting_text = text or None
    job.description = text[:DESCRIPTION_CHARS] or None
    for name, value in signals.stored(text).items():
        setattr(job, name, value)
    job.published_on = job.published_on or published
    if stated is not None and not job.contract_stated:  # a contract the person set is stated
        job.contract_type = stated
        job.contract_stated = True
    job.read_at = None
    job.read_attempts = 0
    score = (
        scoring.score(text, skills, title=job.title, search=search)
        if skills is not None and text.strip()
        else None
    )
    if score is not None and not job.fit_overridden:
        if job.fit != score.fit:
            events.append(
                (JobEventKind.fit, {"from": job.fit, "to": score.fit, "manual": False, "via": via})
            )
        job.fit, job.competencies, job.gaps = score.fit, list(score.competencies), list(score.gaps)
    for kind, payload in events:
        add_event(db, job, kind, payload, now)
    return skipped


def posting_search(
    db: Session, user_id: uuid.UUID, search_id: uuid.UUID | None
) -> SearchSection | None:
    """Jobs J31b: the search a posting's exclusions and fit read, the one its badge reads
    (`criteria_badge.criteria_for`): its saved search's criteria over the person's settings
    (`searches.merged`), else the main search; None when the person has none. Raises
    SearchUnreadableError when the stored one no longer validates."""
    sections = db.execute(
        sa.select(Profile.sections).where(Profile.user_id == user_id)
    ).scalar_one_or_none()
    raw = (sections or {}).get("search")
    if search_id is not None:
        saved = db.execute(
            sa.select(SavedSearch.criteria).where(
                SavedSearch.user_id == user_id, SavedSearch.id == search_id
            )
        ).scalar_one_or_none()
        if isinstance(saved, Mapping):
            raw = searches.merged(raw if isinstance(raw, dict) else None, saved)
    if not isinstance(raw, dict):
        return None
    try:
        return SearchSection.model_validate(raw)
    except ValueError:
        raise SearchUnreadableError from None


def unwanted_of(search: SearchSection | None, title: str, location: str) -> str:
    """The person's unwanted title or place a posting meets, as the note a search writes for it
    ("Unwanted title: Stage", `_Site._excluded` of the browser's sources), else "". Raises
    SearchUnreadableError when a stored term list no longer compiles."""
    if search is None:
        return ""
    for name, terms, text in (
        ("title", search.title_exclude, title),
        ("place", search.location_exclude, location),
    ):
        try:
            pattern = matching.compile_terms(list(terms.terms), terms.advanced_regex)
        except re.error:
            raise SearchUnreadableError from None
        if pattern is not None and (met := pattern.search(text)):
            return f"Unwanted {name}: {met.group(0)}"
    return ""


def set_listed_text(
    db: Session, user_id: uuid.UUID, job_id: uuid.UUID, text: str, now: datetime
) -> Job | None:
    """Jobs J31b: the text the person pasted for a posting seen only in a list replaces its
    card's (`page_text`), with an `edit` event naming the list; the never apply rules and the
    unwanted titles and places of the posting's search (`posting_search`) read its title,
    company and place as a search's read of its page does. None when the posting is not the
    person's. Raises TextRefusedError for a text too short to be a posting's (422,
    `text_too_short`) or a posting no longer seen in a list (409, `page_read`: a search read
    its page meanwhile), SearchUnreadableError when its search no longer reads; nothing is
    written then."""
    text = visible(text).strip()
    if len(" ".join(text.split())) < TEXT_MIN_CHARS:
        raise TextRefusedError("text_too_short", TEXT_SHORT, 422)
    job = lock_job(db, user_id, job_id)
    if job is None:
        return None
    if job.list_url is None:
        raise TextRefusedError("page_read", PAGE_READ_ALREADY, 409)
    search = posting_search(db, user_id, job.criteria_search_id)
    rules = [NeverApplyRule.parse(one) for one in search.never_apply] if search else []
    rule = next((r for r in rules if r.matches(job.company, job.title)), None)
    page_text(
        db,
        job,
        text,
        (JobEventKind.edit, {"text_pasted": {"list_url": job.list_url}}),
        now,
        rule=rule,
        excluded=unwanted_of(search, job.title, job.location),
        skills=user_skills(db, user_id),
        search=search,
        via="pasted",
    )
    touch(job, now)
    db.flush()
    return job


class CriteriaChoice(enum.StrEnum):
    """The criteria filter (jobs J30): every posting, those within the criteria of the search
    that found them (no badge, a row never computed included), or those outside (a badge)."""

    all = "all"
    within = "within"
    outside = "outside"


class ApplicationChoice(enum.StrEnum):
    """The application filter: what the posting's newest application says. How each state reads
    is apply's (`app/apply/list_filter.py`), which registers it."""

    none = "none"
    under_way = "under_way"
    waiting = "waiting"
    sent = "sent"
    failed = "failed"


# searches.MAIN: the main search is the profile's own section, with no row of its own.
MainSearch = Literal["main"]


@dataclass(frozen=True)
class JobFilters:
    """The list filters of GET /api/jobs. Empty means no filter on that field."""

    statuses: Sequence[JobStatus] = ()
    contract_types: Sequence[ContractType] = ()
    company: str | None = None  # substring, case-insensitive
    q: str | None = None  # full text on company, title, location, notes (web search syntax)
    fit_min: int | None = None
    fit_max: int | None = None
    found_from: date | None = None
    found_to: date | None = None  # inclusive
    places: Sequence[str] = ()  # the location holds one as whole words, case and accents aside
    work_modes: Sequence[WorkModeChoice] = ()
    sources: Sequence[str] = ()  # one of the source's names, or the part of one before ":"
    published_from: date | None = None  # inclusive
    published_undated: bool = False  # with published_from, the postings without a date stay
    pay: PayChoice | None = None
    pay_min: int | None = None  # with pay_period: the top of the posting's range reaches it
    pay_period: PayPeriod | None = None
    applications: Sequence[ApplicationChoice] = ()
    sweep: uuid.UUID | None = None  # the postings this sweep added
    search: uuid.UUID | MainSearch | None = None  # the postings this search's sweeps added
    criteria: CriteriaChoice = CriteriaChoice.all
    listing: ListingChoice = ListingChoice.all


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# The location's words for the place filter: what gazetteer.fold does (combining marks dropped
# after NFD, lower case), then every run of spaces and ASCII punctuation one space, padded.
_MARKS = "[\u0300-\u036f]"
_SEPARATORS = "[[:space:]!-/:-@[-`{-~]+"


def place_words(text: Any) -> Any:
    """`text` (a column or a value) as " word word ", folded as the place filter compares it."""
    folded = sa.func.lower(
        sa.func.regexp_replace(sa.func.normalize(text, sa.literal_column("NFD")), _MARKS, "", "g")
    )
    words = sa.func.btrim(sa.func.regexp_replace(folded, _SEPARATORS, " ", "g"))
    return sa.literal(" ") + words + sa.literal(" ")


# A job's `source` names every source that found it joined by this (sweep.SOURCES_SEP, which
# imports this module).
SOURCES_SEP = ", "


def source_condition(names: Sequence[str]) -> sa.ColumnElement[bool]:
    """A job one of whose source names is in `names`, or whose part before ":" is."""
    name = sa.func.unnest(sa.func.string_to_array(Job.source, SOURCES_SEP)).column_valued("name")
    wanted = list(names)
    return (
        sa.select(sa.literal(1))
        .where(sa.or_(name.in_(wanted), sa.func.split_part(name, ":", 1).in_(wanted)))
        .exists()
    )


def added_by(user_id: uuid.UUID, sweep_id: uuid.UUID) -> sa.ScalarSelect[uuid.UUID]:
    """The ids of the jobs a sweep added, by their `sweep` event."""
    return _added(user_id, JobEvent.data["sweep_id"].astext == str(sweep_id))


def added_by_search(
    user_id: uuid.UUID, search: uuid.UUID | MainSearch
) -> sa.ScalarSelect[uuid.UUID]:
    """The ids of the jobs the person's sweeps of one search added (jobs J32): `main`, the sweeps
    that froze no saved search, as sweep_router.search_ran reads them (a deleted search's sweep
    keeps its frozen name, so it is never the main search's); else those of the saved search."""
    ran = sa.select(sa.cast(Sweep.id, sa.Text)).where(Sweep.user_id == user_id)
    if search == "main":
        frozen = sa.func.jsonb_typeof(Sweep.stats[("search_scope", "saved_search", "name")])
        ran = ran.where(Sweep.saved_search_id.is_(None), sa.func.coalesce(frozen, "") != "string")
    else:
        ran = ran.where(Sweep.saved_search_id == search)
    return _added(user_id, JobEvent.data["sweep_id"].astext.in_(ran))


def _added(user_id: uuid.UUID, of_sweep: sa.ColumnElement[bool]) -> sa.ScalarSelect[uuid.UUID]:
    return (
        sa.select(JobEvent.job_id)
        .where(JobEvent.user_id == user_id, JobEvent.kind == JobEventKind.sweep, of_sweep)
        .scalar_subquery()
    )


# The application filter's condition, registered by apply (which depends on jobs): the jobs
# whose newest application reads as one of the choices.
ApplicationCondition = Callable[[Sequence[ApplicationChoice]], sa.ColumnElement[bool]]
_application_condition: list[ApplicationCondition] = []


def register_application_condition(condition: ApplicationCondition) -> None:
    _application_condition[:] = [condition]


def _work_mode_condition(choices: Sequence[WorkModeChoice]) -> sa.ColumnElement[bool]:
    stated = [WorkMode(c.value) for c in choices if c is not WorkModeChoice.not_stated]
    clauses: list[sa.ColumnElement[bool]] = [Job.work_mode.in_(stated)] if stated else []
    if WorkModeChoice.not_stated in choices:
        clauses.append(Job.work_mode.is_(None))
    return sa.or_(*clauses)


def filtered(user_id: uuid.UUID, filters: JobFilters) -> sa.Select[tuple[Job]]:
    """The user's jobs matching every filter given. A fit range leaves out unscored jobs."""
    query = _more_filters(jobs_of(user_id), user_id, filters)
    if filters.statuses:
        query = query.where(Job.status.in_(list(filters.statuses)))
    if filters.contract_types:
        query = query.where(Job.contract_type.in_(list(filters.contract_types)))
    if filters.company and filters.company.strip():
        pattern = f"%{_like_escape(filters.company.strip())}%"
        query = query.where(Job.company.ilike(pattern, escape="\\"))
    if filters.q and filters.q.strip():
        tsquery = sa.func.websearch_to_tsquery(
            sa.literal_column("'simple'::regconfig"), filters.q.strip()
        )
        query = query.where(Job.search.bool_op("@@")(tsquery))
    if filters.fit_min is not None:
        query = query.where(Job.fit >= filters.fit_min)
    if filters.fit_max is not None:
        query = query.where(Job.fit <= filters.fit_max)
    if filters.found_from is not None:
        query = query.where(Job.found_on >= filters.found_from)
    if filters.found_to is not None:
        query = query.where(Job.found_on <= filters.found_to)
    return query


def _more_filters(
    query: sa.Select[tuple[Job]], user_id: uuid.UUID, filters: JobFilters
) -> sa.Select[tuple[Job]]:
    """The filters of jobs J29: place, work mode, website, published, pay, application, sweep;
    the search of jobs J32; the criteria of jobs J30."""
    if filters.places:
        words = place_words(Job.location)
        query = query.where(
            sa.or_(*(sa.func.strpos(words, place_words(p)) > 0 for p in filters.places))
        )
    if filters.work_modes:
        query = query.where(_work_mode_condition(filters.work_modes))
    if filters.sources:
        query = query.where(source_condition(filters.sources))
    if filters.published_from is not None:
        dated = Job.published_on >= filters.published_from
        query = query.where(
            sa.or_(dated, Job.published_on.is_(None)) if filters.published_undated else dated
        )
    if filters.pay is not None:
        stated = Job.pay_max.is_not(None)
        query = query.where(stated if filters.pay is PayChoice.stated else ~stated)
    if filters.pay_min is not None and filters.pay_period is not None:
        query = query.where(Job.pay_period == filters.pay_period, Job.pay_max >= filters.pay_min)
    if filters.applications:
        if not _application_condition:
            raise RuntimeError("no application condition registered (apply registers it)")
        query = query.where(_application_condition[0](filters.applications))
    if filters.sweep is not None:
        query = query.where(Job.id.in_(added_by(user_id, filters.sweep)))
    if filters.search is not None:
        query = query.where(Job.id.in_(added_by_search(user_id, filters.search)))
    if filters.criteria is not CriteriaChoice.all:
        badged = sa.func.coalesce(sa.func.cardinality(Job.outside_criteria), 0) > 0
        query = query.where(badged if filters.criteria is CriteriaChoice.outside else ~badged)
    if filters.listing is not ListingChoice.all:
        listed = Job.list_url.is_not(None)
        query = query.where(listed if filters.listing is ListingChoice.only else ~listed)
    return query


FACETS_MAX = 50
# A location's places for the pickers: its parts between commas, semicolons, slashes, bars,
# brackets and spaced dashes.
_PLACE_PARTS = re.compile(r"[,;/|()\[\]\u00b7]|\s[-\u2013\u2014]\s")
_FACET_SEPARATORS = re.compile(r"[\s!-/:-@\[-`{-~]+")
PLACE_CHARS = 100


@dataclass(frozen=True)
class Facet:
    value: str
    count: int


def _place_key(part: str) -> str:
    """A place as the place filter compares it (place_words), for grouping spellings."""
    folded = unicodedata.normalize("NFD", part)
    folded = "".join(c for c in folded if not "\u0300" <= c <= "\u036f").lower()
    return _FACET_SEPARATORS.sub(" ", folded).strip()


def _places_of(location: str) -> dict[str, str]:
    parts = (part.strip() for part in _PLACE_PARTS.split(location))
    return {
        _place_key(part): part
        for part in parts
        if 0 < len(part) <= PLACE_CHARS and any(c.isalnum() for c in part)
    }


def _websites_of(source: str) -> dict[str, str]:
    names = (name.split(":", 1)[0].strip() for name in source.split(SOURCES_SEP))
    return {name: name for name in names if name}


def _ranked(rows: Iterable[Any], parts: Callable[[str], dict[str, str]]) -> list[Facet]:
    """The values `parts` reads in each text, by the jobs holding them (a job counts once per
    value), most first, each in its most frequent spelling, FACETS_MAX at most."""
    counts: dict[str, int] = defaultdict(int)
    spellings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for text, count in rows:
        for key, spelling in parts(text or "").items():
            counts[key] += count
            spellings[key][spelling] += count
    ranked = sorted(counts, key=lambda key: (-counts[key], key))[:FACETS_MAX]
    return [
        Facet(max(spellings[key], key=lambda s: (spellings[key][s], s)), counts[key])
        for key in ranked
    ]


def facets(db: Session, user_id: uuid.UUID, filters: JobFilters) -> tuple[list[Facet], list[Facet]]:
    """The places and the websites of the jobs matching `filters` but their own filter, with
    the number of jobs of each."""

    def grouped(column: Any, without: JobFilters) -> list[Any]:
        base = filtered(user_id, without).with_only_columns(column.label("text")).subquery()
        query = sa.select(base.c.text, sa.func.count()).group_by(base.c.text)
        return list(db.execute(query).all())

    places = _ranked(grouped(Job.location, replace(filters, places=())), _places_of)
    websites = _ranked(grouped(Job.source, replace(filters, sources=())), _websites_of)
    return places, websites


def list_events(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> list[JobEvent]:
    """The history of one of the user's jobs, oldest first."""
    query = (
        sa.select(JobEvent)
        .where(JobEvent.user_id == user_id, JobEvent.job_id == job_id)
        .order_by(JobEvent.at, JobEvent.id)
    )
    return list(db.execute(query).scalars())


def list_files(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> list[JobFile]:
    """The files of one of the user's jobs, without their bytes (deferred)."""
    query = (
        sa.select(JobFile)
        .join(Job, Job.id == JobFile.job_id)
        .where(Job.user_id == user_id, JobFile.job_id == job_id)
        .order_by(JobFile.created_at, JobFile.name)
    )
    return list(db.execute(query).scalars())


def get_file(
    db: Session, user_id: uuid.UUID, job_id: uuid.UUID, file_id: uuid.UUID
) -> JobFile | None:
    query = (
        sa.select(JobFile)
        .join(Job, Job.id == JobFile.job_id)
        .where(Job.user_id == user_id, JobFile.job_id == job_id, JobFile.id == file_id)
    )
    return db.execute(query).scalar_one_or_none()


# --- duplicates and slugs -----------------------------------------------------------------------


def find_duplicate(
    db: Session,
    user_id: uuid.UUID,
    *,
    company: str,
    title: str,
    location: str | None,
    url: str | None,
    exclude: uuid.UUID | None = None,
) -> Job | None:
    """The user's job that is the same posting (slug.py: same URL, or same company, title and
    location once normalised), or None; `exclude` leaves one job out (the one being edited).
    Compares in Python over the user's rows: the keys are not stored, and one user holds
    hundreds of rows, not millions."""
    key = slugs.PostingKey.of(company, title, location, url)
    query = jobs_of(user_id).order_by(Job.created_at, Job.id)
    if exclude is not None:
        query = query.where(Job.id != exclude)
    rows = db.execute(query).scalars()
    return slugs.find_duplicate(
        key, rows, lambda job: slugs.PostingKey.of(job.company, job.title, job.location, job.url)
    )


def free_slug(db: Session, user_id: uuid.UUID, base: str) -> str:
    """`base` or the first `base-N` the user does not have yet."""
    taken = set(
        db.execute(
            sa.select(Job.slug).where(
                Job.user_id == user_id,
                sa.or_(Job.slug == base, Job.slug.startswith(f"{base}-", autoescape=True)),
            )
        ).scalars()
    )
    return slugs.unique_slug(base, taken)


# --- writing ------------------------------------------------------------------------------------


TICK = timedelta(microseconds=1)


def touch(job: Job, now: datetime) -> None:
    """Mark the job changed: updated_at becomes `now`, or one microsecond past its current value
    when the clock has not moved on (a frozen clock, two writes in the same instant). updated_at
    therefore changes on every write, which makes it the job's version for optimistic
    concurrency (PATCH) and for single-use undo tokens."""
    current = job.updated_at
    job.updated_at = now if current is None or now > current else current + TICK


def add_event(
    db: Session, job: Job, kind: JobEventKind, data: dict[str, Any], now: datetime
) -> JobEvent:
    event = JobEvent(job_id=job.id, user_id=job.user_id, kind=kind, data=data, at=now)
    db.add(event)
    return event


@dataclass
class NewJob:
    company: str
    title: str
    location: str = ""
    url: str | None = None
    contract_type: ContractType = ContractType.CDI
    source: str | None = None
    description: str | None = None
    posting_text: str | None = None
    published_on: date | None = None
    notes: str = ""
    legacy: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Jobs J39: what create_job and its callers derive (the slug, the duplicate, the signals,
        # the fit) reads the texts as they are stored.
        for name in VISIBLE_COLUMNS:
            value = getattr(self, name)
            if isinstance(value, str):
                setattr(self, name, visible(value))


def create_job(db: Session, user_id: uuid.UUID, new: NewJob, now: datetime) -> Job:
    """Store a new posting as `found`, with a free slug and a `created` event.

    Raises DuplicateJobError with the existing job when the user already has this posting.
    Two concurrent creations of the same posting are stopped by the unique indexes (slug, url):
    the second raises IntegrityError, which the caller turns into a retry or a 409.
    """
    existing = find_duplicate(
        db, user_id, company=new.company, title=new.title, location=new.location, url=new.url
    )
    if existing is not None:
        raise DuplicateJobError(existing)
    job = Job(
        user_id=user_id,
        slug=free_slug(db, user_id, slugs.slugify(new.company, new.title)),
        company=new.company,
        title=new.title,
        location=new.location,
        url=new.url or None,
        contract_type=new.contract_type,
        source=new.source,
        description=new.description,
        posting_text=new.posting_text,
        published_on=new.published_on,
        **signals.stored(new.posting_text),
        found_on=now.date(),
        notes=new.notes,
        legacy=new.legacy,
        status=JobStatus.found,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()
    add_event(db, job, JobEventKind.created, {"slug": job.slug}, now)
    db.flush()
    return job


def apply_score(
    db: Session,
    job: Job,
    result: ScoreResult,
    now: datetime,
    *,
    overwrite_manual: bool = False,
    via: str | None = None,
) -> bool:
    """Record a scoring result. A fit set by hand is kept unless `overwrite_manual`; the
    competencies and gaps are always refreshed, as engine/jobs.py score_row does. Returns
    whether anything changed (fit, override flag, competencies or gaps); a `fit` event
    {from, to, manual: false} is recorded when the fit or its override flag changes, and the
    job is touched only when something changed."""
    keep = job.fit_overridden and not overwrite_manual
    changed = list(job.competencies) != list(result.competencies) or list(job.gaps) != list(
        result.gaps
    )
    job.competencies = list(result.competencies)
    job.gaps = list(result.gaps)
    if not keep and (job.fit != result.fit or job.fit_overridden):
        data: dict[str, Any] = {"from": job.fit, "to": result.fit, "manual": False}
        if job.fit_overridden:
            data["was_manual"] = True
        if via:
            data["via"] = via
        add_event(db, job, JobEventKind.fit, data, now)
        job.fit = result.fit
        job.fit_overridden = False
        changed = True
    if changed:
        touch(job, now)
    db.flush()
    return changed


# The hook of other modules on a status change (documents D14 freezes the sent CV and letter on
# `applied`). A callback gets the session, the job, the status it left, the status it reached and
# the clock's `now`; it runs inside set_status, under the job's row lock, after the event is
# written, so its writes commit or roll back with the status change. This module imports
# nothing of the modules that register.
StatusCallback = Callable[[Session, Job, JobStatus, JobStatus, datetime], None]
_status_callbacks: list[StatusCallback] = []


def register_status_callback(callback: StatusCallback) -> None:
    """Call `callback` after every status change, in registration order. Registering the same
    function again changes nothing. An exception from a callback leaves set_status like one of
    its own: the caller rolls the transaction back, the status change with it."""
    if callback not in _status_callbacks:
        _status_callbacks.append(callback)


def set_status(
    db: Session,
    job: Job,
    status: JobStatus,
    now: datetime,
    *,
    via: str | None = None,
    extra: dict[str, Any] | None = None,
    supersede: bool = True,
) -> bool:
    """Move a job to `status` with a `status` event. Moving to applied fills `applied_on` the
    first time only. Returns False (and records nothing) when the status is unchanged. `extra`
    joins the event's data (an accepted agent proposal records its id). `supersede=False` leaves
    the job's pending status proposals to the caller, which supersedes those of many jobs at
    once (supersede_status_proposals). The job must have been read under the row lock in this
    transaction (lock_job, lock_jobs, undo_bulk), else UnlockedJobError: two changes racing on
    an unlocked row would each record an event from a status the other had already moved. Once
    the change is written the registered status callbacks run (register_status_callback)."""
    if job.id not in _locked_ids(db):
        raise UnlockedJobError(
            f"job {job.id} was not read under the lock in this transaction: "
            "read it with lock_job before set_status"
        )
    if job.status == status:
        return False
    previous = job.status
    data: dict[str, Any] = {"from": previous.value, "to": status.value}
    if via:
        data["via"] = via
    if extra:
        data.update(extra)
    add_event(db, job, JobEventKind.status, data, now)
    job.status = status
    if status is JobStatus.applied and job.applied_on is None:
        job.applied_on = now.date()
    touch(job, now)
    if supersede:
        supersede_status_proposals(
            db, job.user_id, [job.id], now, keep=(extra or {}).get("proposal_id")
        )
    db.flush()
    for callback in tuple(_status_callbacks):
        callback(db, job, previous, status, now)
    return True


STATUS_MOVED = "The job's status was changed."


def supersede_status_proposals(
    db: Session,
    user_id: uuid.UUID,
    job_ids: Sequence[uuid.UUID],
    now: datetime,
    keep: Any = None,
) -> None:
    """Pending agent proposals to change these jobs' status were made for the status they had:
    once it changes (by hand, in bulk, by undo, by mail or by accepting another proposal), they
    are superseded by the system, in one statement whatever the number of jobs. The callers hold
    the jobs' locks (job first, then its proposals: one lock order, issue #25). `keep` is the
    proposal being accepted, if any. Undoing the status change later does not bring them back."""
    if not job_ids:
        return
    condition = sa.and_(
        Proposal.user_id == user_id,
        Proposal.target == "job",
        Proposal.target_id == sa.any_(sa.literal(list(job_ids), sa.ARRAY(sa.Uuid()))),
        Proposal.kind == "job_status",
        Proposal.status == ProposalStatus.pending,
    )
    if keep is not None:
        condition = sa.and_(condition, Proposal.id != uuid.UUID(str(keep)))
    # The rows are locked in id order before the update (an UPDATE locks them in scan order):
    # the second half of the one lock order, jobs by id, then proposals by id (issue #166).
    db.execute(
        sa.select(Proposal.id)
        .where(condition)
        .order_by(Proposal.id)
        .with_for_update(key_share=True)
    ).all()
    db.execute(
        sa.update(Proposal)
        .where(condition)
        .values(
            status=ProposalStatus.superseded,
            decided_by=ProposalDecider.system,
            decided_at=now,
            reason=STATUS_MOVED,
        )
        .execution_options(synchronize_session=False)
    )


# --- sweeps -------------------------------------------------------------------------------------


def running_sweep(db: Session, user_id: uuid.UUID) -> Sweep | None:
    return db.execute(
        sa.select(Sweep).where(Sweep.user_id == user_id, Sweep.status == SweepStatus.running)
    ).scalar_one_or_none()


def get_sweep(db: Session, user_id: uuid.UUID, sweep_id: uuid.UUID) -> Sweep | None:
    return db.execute(
        sa.select(Sweep).where(Sweep.user_id == user_id, Sweep.id == sweep_id)
    ).scalar_one_or_none()


def start_sweep(db: Session, user_id: uuid.UUID, trigger: SweepTrigger, now: datetime) -> Sweep:
    """A new running sweep. Raises SweepRunningError when one is running already, including
    one started at the same moment by another transaction (the partial unique index decides)."""
    sweep = Sweep(user_id=user_id, trigger=trigger, started_at=now)
    try:
        with db.begin_nested():
            db.add(sweep)
            db.flush()
    except IntegrityError as exc:
        if "ix_sweeps_one_running_per_user" not in str(exc.orig):
            raise
        raise SweepRunningError() from None
    return sweep


# --- list page ----------------------------------------------------------------------------------


class JobSort(enum.StrEnum):
    """Orders of GET /api/jobs. Every order ends with the id, so ties have one stable order and
    a cursor never skips or repeats a row."""

    found_on = "found_on"  # newest found first, then best fit
    fit = "fit"  # best fit first, unscored last, then newest found
    company = "company"  # company A to Z (case-insensitive), then title
    status = "status"  # pipeline order (found ... skipped), then best fit
    updated = "updated"  # last changed first
    published = "published"  # newest published first, undated last, then newest found
    # yearly pays first, then monthly, daily, hourly, each highest first; unstated last
    pay = "pay"


@dataclass(frozen=True)
class _SortKey:
    expression: Any
    descending: bool
    parse: Callable[[Any], Any]  # the cursor's JSON value back to a bound value
    value_of: Callable[[Job], Any]  # the row's value, as JSON


# An unscored job sorts as fit -1: after every scored one in a descending order.
_FIT = _SortKey(sa.func.coalesce(Job.fit, -1), True, int, lambda j: -1 if j.fit is None else j.fit)
_FOUND = _SortKey(Job.found_on, True, date.fromisoformat, lambda j: j.found_on.isoformat())
_ID = _SortKey(Job.id, False, lambda v: uuid.UUID(str(v)), lambda j: str(j.id))
_UNDATED = date(1, 1, 1)
_PUBLISHED = _SortKey(
    sa.func.coalesce(Job.published_on, _UNDATED),
    True,
    date.fromisoformat,
    lambda j: (j.published_on or _UNDATED).isoformat(),
)
_PERIODS = {PayPeriod.year: 0, PayPeriod.month: 1, PayPeriod.day: 2, PayPeriod.hour: 3}
_PERIOD = _SortKey(
    sa.case(*((Job.pay_period == p, rank) for p, rank in _PERIODS.items()), else_=len(_PERIODS)),
    False,
    int,
    lambda j: _PERIODS.get(j.pay_period, len(_PERIODS)),
)
_PAY = _SortKey(sa.func.coalesce(Job.pay_max, 0), True, int, lambda j: j.pay_max or 0)
SORT_KEYS: dict[JobSort, tuple[_SortKey, ...]] = {
    JobSort.found_on: (_FOUND, _FIT, _ID),
    JobSort.fit: (_FIT, _FOUND, _ID),
    JobSort.company: (
        _SortKey(sa.func.lower(Job.company), False, str, lambda j: j.company.lower()),
        _SortKey(sa.func.lower(Job.title), False, str, lambda j: j.title.lower()),
        _ID,
    ),
    JobSort.status: (_SortKey(Job.status, False, JobStatus, lambda j: j.status.value), _FIT, _ID),
    JobSort.updated: (
        _SortKey(Job.updated_at, True, datetime.fromisoformat, lambda j: j.updated_at.isoformat()),
        _ID,
    ),
    JobSort.published: (_PUBLISHED, _FOUND, _ID),
    JobSort.pay: (_PERIOD, _PAY, _FOUND, _ID),
}


class InvalidCursorError(ValueError):
    """The cursor was not made by this API for this sort."""


def encode_cursor(sort: JobSort, job: Job) -> str:
    """An opaque cursor after `job` in `sort`: base64url of the sort and the row's sort values."""
    values = [key.value_of(job) for key in SORT_KEYS[sort]]
    raw = json.dumps({"s": sort.value, "k": values}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(sort: JobSort, cursor: str) -> list[Any]:
    """The bound sort values a cursor carries. Raises InvalidCursorError for anything else."""
    keys = SORT_KEYS[sort]
    try:
        body = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(body, dict) or body.get("s") != sort.value:
            raise InvalidCursorError("The cursor belongs to another sort or is invalid.")
        values = body.get("k")
        if not isinstance(values, list) or len(values) != len(keys):
            raise InvalidCursorError("The cursor is invalid.")
        parsed = []
        for key, value in zip(keys, values, strict=True):
            if isinstance(value, (dict, list, bool)) or value is None:
                raise InvalidCursorError("The cursor is invalid.")
            parsed.append(key.parse(value))
        return parsed
    except InvalidCursorError:
        raise
    except (ValueError, TypeError, binascii.Error, RecursionError):
        raise InvalidCursorError("The cursor is invalid.") from None


def _after(keys: tuple[_SortKey, ...], values: list[Any]) -> sa.ColumnElement[bool]:
    """Rows strictly after `values` in the order of `keys` (keyset pagination with mixed
    directions: k1 beyond v1, or k1 = v1 and k2 beyond v2, ...)."""
    clauses = []
    for index, key in enumerate(keys):
        equal = [k.expression == v for k, v in zip(keys[:index], values[:index], strict=True)]
        value = values[index]
        beyond = key.expression < value if key.descending else key.expression > value
        clauses.append(sa.and_(*equal, beyond))
    return sa.or_(*clauses)


def _file_count() -> Any:
    return (
        sa.select(sa.func.count(JobFile.id))
        .where(JobFile.job_id == Job.id)
        .correlate(Job)
        .scalar_subquery()
    )


@dataclass
class ListPage:
    rows: list[tuple[Job, int]]  # each job with its number of files
    next_cursor: str | None


def list_page(
    db: Session,
    user_id: uuid.UUID,
    filters: JobFilters,
    sort: JobSort,
    limit: int,
    cursor: str | None = None,
) -> ListPage:
    """One page of the user's jobs matching `filters`, in `sort`, after `cursor`. Raises
    InvalidCursorError for a cursor this sort did not make."""
    keys = SORT_KEYS[sort]
    query = filtered(user_id, filters).add_columns(_file_count())
    if cursor:
        query = query.where(_after(keys, decode_cursor(sort, cursor)))
    order = [k.expression.desc() if k.descending else k.expression.asc() for k in keys]
    found = [(row[0], int(row[1])) for row in db.execute(query.order_by(*order).limit(limit + 1))]
    page = found[:limit]
    next_cursor = encode_cursor(sort, page[-1][0]) if len(found) > limit else None
    return ListPage(rows=page, next_cursor=next_cursor)


def status_counts(db: Session, user_id: uuid.UUID, filters: JobFilters) -> dict[JobStatus, int]:
    """Rows per status matching `filters` except their status filter, every status listed in
    pipeline order (0 when none), so status tabs can show what each would hold."""
    base = filtered(user_id, replace(filters, statuses=())).with_only_columns(Job.status).subquery()
    query = sa.select(base.c.status, sa.func.count()).group_by(base.c.status)
    counts = {JobStatus(status): count for status, count in db.execute(query).all()}
    return {status: int(counts.get(status, 0)) for status in JobStatus}


FIT_BUCKETS = 10  # 0-9, 10-19, ..., 80-89, 90-100


def fit_histogram(db: Session, user_id: uuid.UUID, filters: JobFilters) -> tuple[list[int], int]:
    """Rows matching `filters` per fit bucket (100 falls in the last one), and the number of
    unscored rows among them."""
    base = filtered(user_id, filters).with_only_columns(Job.fit).subquery()
    tens = base.c.fit // 10  # integer division; NULL stays NULL (LEAST would drop it)
    buckets = [0] * FIT_BUCKETS
    unscored = 0
    for ten, count in db.execute(sa.select(tens, sa.func.count()).group_by(tens)).all():
        if ten is None:
            unscored = int(count)
        else:
            buckets[min(int(ten), FIT_BUCKETS - 1)] += int(count)
    return buckets, unscored


def recent_events(
    db: Session, user_id: uuid.UUID, job_id: uuid.UUID, limit: int
) -> tuple[list[JobEvent], int]:
    """The newest `limit` events of one of the user's jobs, newest first, and how many exist."""
    scope = (JobEvent.user_id == user_id, JobEvent.job_id == job_id)
    newest = sa.select(JobEvent).where(*scope).order_by(JobEvent.at.desc(), JobEvent.id.desc())
    events = list(db.execute(newest.limit(limit)).scalars())
    total = db.execute(sa.select(sa.func.count()).select_from(JobEvent).where(*scope)).scalar_one()
    return events, int(total)


# --- import -------------------------------------------------------------------------------------

# The fields an import sets, compared one by one on an existing row.
IMPORTED_FIELDS = (
    "company",
    "title",
    "location",
    "contract_type",
    "url",
    "source",
    "status",
    "fit",
    "competencies",
    "gaps",
    "description",
    "posting_text",
    "published_on",
    "found_on",
    "applied_on",
    "notes",
    "legacy",
)


@dataclass
class ImportSummary:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    files_added: int = 0
    files_replaced: int = 0
    files_unchanged: int = 0
    files_kept: int = 0
    # Rows of the document whose fit was set by hand (imported_fit_is_manual), marked so.
    fits_manual: int = 0
    # False when the profile has no skills: then no fit could be told apart, none is marked.
    fits_checked: bool = True
    warnings: list[str] = field(default_factory=list)


def carried_fields(item: ImportJob) -> tuple[str, ...]:
    """The pay and work mode columns an import row sets (opportunities O3b): the four pay
    columns (PAY_FIELDS) when it carries a pay or nulls, the work mode when it carries one or
    null; a row written before they existed sets none, so the stored values stay."""
    return (PAY_FIELDS if item.carries_pay else ()) + (
        ("work_mode",) if item.carries_work_mode else ()
    )


def is_sent(job: Job) -> bool:
    """Whether the job's files went to the company (hard rule 9: they are never replaced nor
    deleted): its status is applied, or it has an applied_on date (interview, rejected...)."""
    return job.status is JobStatus.applied or job.applied_on is not None


class _Index:
    """The user's rows as the import goes: by slug, by URL, by duplicate key, and their files."""

    def __init__(self, db: Session, user_id: uuid.UUID) -> None:
        # Every row is locked first, in id order (the lock order below), because the import
        # updates many of them with plain UPDATEs: it must not wait for one row while holding
        # another in a different order than the documents tick (documents.tick.person).
        locked = _lock(db, jobs_of(user_id).options(undefer(Job.posting_text)).order_by(Job.id))
        self.jobs = sorted(locked, key=lambda job: (job.created_at, job.id))
        self.by_slug = {job.slug: job for job in self.jobs}
        self.by_url = {job.url: job for job in self.jobs if job.url}
        self.keys = {job.id: self.key_of(job) for job in self.jobs}
        self.files: dict[uuid.UUID, list[JobFile]] = defaultdict(list)
        query = sa.select(JobFile).join(Job, Job.id == JobFile.job_id).where(Job.user_id == user_id)
        for found in db.execute(query.order_by(JobFile.created_at, JobFile.id)).scalars():
            self.files[found.job_id].append(found)
        self.claimed: set[uuid.UUID] = set()

    @staticmethod
    def key_of(job: Job) -> slugs.PostingKey:
        return slugs.PostingKey.of(job.company, job.title, job.location, job.url)

    def match(self, item: ImportJob) -> Job | None:
        """The row this item updates: the same slug, else the same posting by the duplicate
        rules, never a row another item of this import already took."""
        job = self.by_slug.get(item.slug)
        if job is not None and job.id not in self.claimed:
            return job
        key = slugs.PostingKey.of(item.company, item.title, item.location, item.url)
        free = (j for j in self.jobs if j.id not in self.claimed)
        return slugs.find_duplicate(key, free, lambda j: self.keys[j.id])

    def track(self, job: Job, old_url: str | None) -> None:
        if job.id not in self.keys:
            self.jobs.append(job)
            self.by_slug[job.slug] = job
        if old_url and self.by_url.get(old_url) is job:
            del self.by_url[old_url]
        if job.url:
            self.by_url[job.url] = job
        self.keys[job.id] = self.key_of(job)
        self.claimed.add(job.id)


def _new_file(job: Job, item: ImportFile, now: datetime) -> JobFile:
    return JobFile(
        job_id=job.id,
        name=item.name,
        mime=item.mime,
        content=item.content,
        size=item.size,
        sha256=bytes.fromhex(item.sha256),
        origin=FileOrigin.legacy,
        created_at=now,
    )


def _create_imported(
    db: Session, user_id: uuid.UUID, item: ImportJob, index: _Index, summary: ImportSummary,
    now: datetime, manual: bool,
) -> Job:  # fmt: skip
    slug = item.slug
    if slug in index.by_slug:  # the row with this slug was taken by another item
        slug = slugs.unique_slug(slug, index.by_slug)
        summary.warnings.append(f"{item.slug}: slug taken by another row, stored as {slug}")
    job = Job(
        user_id=user_id,
        slug=slug,
        **{name: getattr(item, name) for name in IMPORTED_FIELDS if name != "found_on"},
        **{name: getattr(item, name) for name in carried_fields(item)},
        **signals.stored(item.posting_text),
        found_on=item.found_on or now.date(),
        fit_overridden=manual,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()
    add_event(db, job, JobEventKind.created, {"slug": job.slug, "via": "import"}, now)
    for file in item.files:
        db.add(_new_file(job, file, now))
    summary.created += 1
    summary.files_added += len(item.files)
    return job


def _update_imported(
    db: Session, job: Job, item: ImportJob, index: _Index, summary: ImportSummary, now: datetime,
    manual: bool,
) -> None:  # fmt: skip
    changed: dict[str, Any] = {}
    for name in IMPORTED_FIELDS:
        new = getattr(item, name)
        if name == "found_on" and new is None:
            continue
        if getattr(job, name) != new:
            changed[name] = new
    for name in carried_fields(item):
        if getattr(job, name) != getattr(item, name):
            changed[name] = getattr(item, name)
    if job.fit_overridden != manual:
        changed["fit_overridden"] = manual

    sent = is_sent(job) or item.status is JobStatus.applied
    current = index.files[job.id]
    by_name = {f.name: f for f in current}
    digests = {bytes(f.sha256) for f in current}
    added: list[str] = []
    replaced: list[str] = []
    for file in item.files:
        digest = bytes.fromhex(file.sha256)
        stored = by_name.get(file.name)
        if (stored is not None and bytes(stored.sha256) == digest) or (
            stored is None and digest in digests
        ):
            summary.files_unchanged += 1
        elif stored is None:
            new_file = _new_file(job, file, now)
            db.add(new_file)
            current.append(new_file)
            digests.add(digest)
            added.append(file.name)
        elif sent:
            summary.files_kept += 1
            summary.warnings.append(
                f"{job.slug}: {file.name} differs from the file already sent, the stored one "
                "is kept (files of applied rows never change)"
            )
        else:
            stored.content = file.content
            stored.size = file.size
            stored.sha256 = digest
            stored.mime = file.mime
            digests.add(digest)
            replaced.append(file.name)

    if not changed and not added and not replaced:
        summary.unchanged += 1
        return
    data: dict[str, Any] = {"changed": sorted(changed)}
    if "status" in changed:
        data["status"] = {"from": job.status.value, "to": changed["status"].value}
    if added:
        data["files_added"] = added
    if replaced:
        data["files_replaced"] = replaced
    for name, value in changed.items():
        setattr(job, name, value)
    if "url" in changed:
        job.liveness = sa.null()  # SQL NULL: the last check read the old address
    if "posting_text" in changed or job.signals is None:
        for name, value in signals.stored(job.posting_text).items():
            setattr(job, name, value)
    touch(job, now)
    add_event(db, job, JobEventKind.import_, data, now)
    summary.updated += 1
    summary.files_added += len(added)
    summary.files_replaced += len(replaced)


def _visible_item(item: ImportJob) -> ImportJob:
    """Jobs J39: the row's texts as they are stored, so its match, its fit and its changes read
    them; a row with nothing to drop is the same object."""
    changed = {
        name: visible(value)
        for name in VISIBLE_COLUMNS
        if isinstance(value := getattr(item, name), str) and visible(value) != value
    }
    return item.model_copy(update=changed) if changed else item


def import_jobs(
    db: Session, user_id: uuid.UUID, items: Sequence[ImportJob], now: datetime
) -> ImportSummary:
    """Load an import document's rows into the user's jobs; the caller commits (all or nothing).

    Idempotent: a row is matched by slug, else by the duplicate rules (same URL, or same company,
    title and location); a matched row gets only the fields that differ, with one `import` event
    naming them, and a row with nothing new is left alone (no event, updated_at kept). New rows
    are created with a `created` event. Files are matched by name, and a file whose bytes the job
    already holds (same SHA-256, any name) is not stored twice. A different file under a stored
    name replaces it, except on a sent row (`is_sent`), which keeps its file byte for byte with a
    warning. An import never deletes a row or a file. A row whose URL belongs to another row is
    skipped with a warning. The document wins on every field it carries, including status.

    Fits set by hand: a row whose fit differs from the one computed from its posting text
    (header included, scoring.engine_text) with the user's skills is stored with
    fit_overridden, so no rescore replaces it (imported_fit_is_manual; counted in
    `fits_manual`). The flag is compared like any field, so a second import changes nothing.
    Without a skills section nothing is marked (`fits_checked` is False).
    """
    summary = ImportSummary()
    index = _Index(db, user_id)
    skills = user_skills(db, user_id)
    summary.fits_checked = skills is not None
    for item in map(_visible_item, items):
        job = index.match(item)
        owner = index.by_url.get(item.url) if item.url else None
        if owner is not None and owner is not job:
            summary.skipped += 1
            summary.warnings.append(
                f"{item.slug}: its url belongs to the row {owner.slug}, skipped"
            )
            continue
        manual = imported_fit_is_manual(item, skills)
        summary.fits_manual += manual
        if job is None:
            job = _create_imported(db, user_id, item, index, summary, now, manual)
            index.track(job, None)
        else:
            old_url = job.url
            _update_imported(db, job, item, index, summary, now, manual)
            index.track(job, old_url)
    db.flush()
    return summary


# --- skills and scoring -------------------------------------------------------------------------


def user_skills(db: Session, user_id: uuid.UUID) -> SkillsSection | None:
    """The skills section of the user's profile, or None when it was never saved (or no longer
    validates). Reads the profile row without creating it."""
    sections = db.execute(
        sa.select(Profile.sections).where(Profile.user_id == user_id)
    ).scalar_one_or_none()
    raw = (sections or {}).get("skills")
    if not isinstance(raw, dict):
        return None
    try:
        return SkillsSection.model_validate(raw)
    except ValueError:
        return None


def scoring_text(job: Job) -> str | None:
    """What a rescore reads for this job: the posting text with the imported posting.txt header
    put back in front (scoring.engine_text), or None when the job has no posting text."""
    if job.posting_text is None or not job.posting_text.strip():
        return None
    return scoring.engine_text(job.posting_text, (job.legacy or {}).get("posting_header"))


def user_search(db: Session, user_id: uuid.UUID) -> SearchSection | None:
    """The search section of the user's profile, for the bonus of its seniority, industries and
    company types (scoring.bonus), or None when it was never saved or no longer validates: no
    bonus then, the fit is the skills' alone."""
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


def computed_score(
    job: Job, skills: SkillsSection, search: SearchSection | None = None
) -> ScoreResult | None:
    """The score of the job's text, with the bonus of `search` when it is given."""
    text = scoring_text(job)
    return None if text is None else scoring.score(text, skills, title=job.title, search=search)


DRAFT_ROWS_MAX = 2_000  # the postings a draft of the skills section is tried on, newest first


def draft_fits(
    db: Session,
    user_id: uuid.UUID,
    skills: SkillsSection,
    search: SearchSection | None,
    statuses: Iterable[JobStatus],
    since: date,
    limit: int = DRAFT_ROWS_MAX,
) -> dict[uuid.UUID, int]:
    """The fits a rescore with `skills` would store on the person's postings in `statuses` found
    on or after `since` (newest first, `limit` at most), by id, storing nothing. A fit set by
    hand is left out (a rescore keeps it), and so is a posting without text."""
    jobs = db.scalars(
        jobs_of(user_id)
        .where(
            Job.status.in_(list(statuses)),
            Job.found_on >= since,
            Job.fit_overridden.is_(False),
        )
        .options(undefer(Job.posting_text))
        .order_by(Job.found_on.desc(), Job.id)
        .limit(limit)
    )
    found: dict[uuid.UUID, int] = {}
    for job in jobs:
        result = computed_score(job, skills, search)
        if result is not None:
            found[job.id] = result.fit
    return found


def imported_fit_is_manual(item: ImportJob, skills: SkillsSection | None) -> bool:
    """Whether an imported row's fit was set by hand: it has a fit and a posting text, and the
    fit is not what the engine computes from that posting (header included). Without skills
    nothing can be told apart, so nothing is marked."""
    if skills is None or item.fit is None or item.posting_text is None:
        return False
    if not item.posting_text.strip():
        return False
    text = scoring.engine_text(item.posting_text, item.legacy.get("posting_header"))
    return scoring.score(text, skills, title=item.title).fit != item.fit


# --- editing (PATCH /api/jobs/{id}) -------------------------------------------------------------


class StaleJobError(Exception):
    """The edit was based on an older version of the job (its updated_at moved on)."""

    def __init__(self, job: Job) -> None:
        super().__init__("stale job")
        self.job = job


class NothingToComputeError(Exception):
    """A fit cannot be computed: no posting text, or no skills in the profile."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


EDITABLE_FIELDS = ("company", "title", "location", "contract_type", "url", "work_mode")
# The columns PATCH's `pay` sets together (opportunities O3), null all four to remove it.
PAY_FIELDS = ("pay_min", "pay_max", "pay_currency", "pay_period")
NOTE_COALESCE = timedelta(minutes=2)


# --- the row lock: what it marks, how long it waits (J8) ----------------------------------------

# The lock order (issue #166), for the rows of the jobs table: a transaction that locks several
# job rows takes them in id order, whatever it does to them next. In the jobs service that is
# lock_jobs, undo_bulk, rescore_batch, import_jobs and mark_overridden (they lock through _lock
# before any UPDATE); in the agents, the status proposals follow the jobs, in id order
# (supersede_status_proposals; accept locks its one job, then its one proposal), and the paths
# that lock proposals alone (agents.proposals.create and reject) hold no job row. In the
# documents, the tick pass takes its postings FOR SHARE in id order up front (store.lock_postings,
# called by tick.person); the single posting locks of a click or a letter hold one posting at a
# time. Paths outside these (a transaction that gathers several postings by another road) are
# not covered: Postgres breaks such a cycle (40P01) and the app answers 503, see is_retryable.
# The lock is FOR NO KEY UPDATE (issue #341): no holder deletes a job or changes its key, so a
# row that references a posting (an event, a document, a run) can be inserted while the posting
# is held, and the foreign key KEY SHARE of that insert is no longer half of a deadlock.
LOCK_TIMEOUT_MS = 5_000  # how long a lock on a job row waits, per transaction (SET LOCAL)
BUSY = "This job is being changed. Try again."
RETRY_BUSY = "The server is busy. Try again in a moment."
RETRY_AFTER_S = 2  # the Retry-After of a 503 RETRY_BUSY
_LOCK_NOT_AVAILABLE = "55P03"  # Postgres' lock_not_available, what lock_timeout raises
# query_canceled (the statement timeout, a wait on several rows one after the other) and
# deadlock_detected (Postgres broke a cycle the lock order did not prevent): both are retryable.
_RETRYABLE = frozenset({"57014", "40P01"})
_LOCKED_KEY = "jobs.locked_ids"


class JobBusyError(Exception):
    """Another transaction holds a job's row lock longer than LOCK_TIMEOUT_MS. The caller rolls
    back and answers 409 BUSY; the caller of a retry may try again."""


class UnlockedJobError(RuntimeError):
    """A programming error: set_status was given a job that was not read with lock_job, lock_jobs
    or undo_bulk in this transaction, so two changes could race on it."""


def is_lock_timeout(exc: BaseException) -> bool:
    """Whether `exc` is Postgres refusing to wait any longer for a lock (SQLSTATE 55P03) and
    nothing else: every other database error keeps its behaviour."""
    return (
        isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE
    )


def is_retryable(exc: BaseException) -> bool:
    """Whether `exc` is a statement timeout (SQLSTATE 57014) or a deadlock Postgres broke
    (40P01): the transaction was rolled back whole, so none of its database changes
    stayed. Every other database error keeps its behaviour."""
    return isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) in _RETRYABLE


def _marks(session: Session) -> dict[SessionTransaction, set[uuid.UUID]]:
    """The ids read under the lock, per transaction of this session: the root transaction and
    each open savepoint have their own. Kept on the session itself, so two sessions never share
    it."""
    marks: dict[SessionTransaction, set[uuid.UUID]] = session.info.setdefault(_LOCKED_KEY, {})
    return marks


def _innermost(session: Session) -> SessionTransaction | None:
    return session.get_nested_transaction() or session.get_transaction()


def _locked_ids(db: Session) -> set[uuid.UUID]:
    """The ids of the jobs this session read under the lock and still holds: those of its
    current transaction, of the savepoint it is in and of every enclosing one."""
    ids: set[uuid.UUID] = set()
    marks = db.info.get(_LOCKED_KEY, {})
    transaction = _innermost(db)
    while transaction is not None:
        ids |= marks.get(transaction, set())
        transaction = transaction.parent
    return ids


def _mark_locked(db: Session, ids: Iterable[uuid.UUID]) -> None:
    transaction = _innermost(db)
    if transaction is not None:  # always the case after the FOR UPDATE that returned them
        _marks(db).setdefault(transaction, set()).update(ids)


def _forget_ended(session: Session, transaction: SessionTransaction) -> None:
    """A transaction ended: its marks go. The root's end clears every mark of the session. A
    savepoint that was rolled back lost the locks taken inside it (Postgres releases them), so
    only its own marks go; those of the enclosing transaction stay, its locks are still held."""
    if transaction.parent is None:
        session.info.pop(_LOCKED_KEY, None)
    else:
        session.info.get(_LOCKED_KEY, {}).pop(transaction, None)


def _release_savepoint(session: Session) -> None:
    """A savepoint was released (committed into its parent): its locks are the parent's now."""
    transaction = session.get_nested_transaction()
    if transaction is not None and transaction.parent is not None:
        released = session.info.get(_LOCKED_KEY, {}).pop(transaction, None)
        if released:
            _marks(session).setdefault(transaction.parent, set()).update(released)


# Registered once, for every session: the row locks are gone when the transaction ends (commit
# or rollback), so the mark goes with them and a pooled session starts the next transaction with
# none. A savepoint keeps its own marks: rolled back, they go with the locks Postgres released;
# released, they pass to the enclosing transaction.
sa.event.listen(Session, "after_transaction_end", _forget_ended)
sa.event.listen(Session, "after_commit", _release_savepoint)


def _begin_locking(db: Session) -> None:
    """Make a wait for a row lock fail after LOCK_TIMEOUT_MS instead of the statement timeout.
    Transaction scoped (set_config with is_local): nothing is left on the pooled connection."""
    db.execute(
        sa.text("select set_config('lock_timeout', :ms, true)"), {"ms": f"{LOCK_TIMEOUT_MS}ms"}
    )


def _lock(db: Session, query: sa.Select[tuple[Job]], *, skip_locked: bool = False) -> list[Job]:
    """Run a `FOR NO KEY UPDATE` select under the lock timeout and mark the rows it returns as
    read under the lock. A lock wait over the timeout raises JobBusyError. With `skip_locked`
    a row another transaction holds is left out at once instead of waited for."""
    _begin_locking(db)
    locking = query.with_for_update(key_share=True, skip_locked=skip_locked)  # FOR NO KEY UPDATE
    try:
        jobs = list(db.execute(locking.execution_options(populate_existing=True)).scalars())
    except DBAPIError as exc:
        if is_lock_timeout(exc):
            raise JobBusyError(BUSY) from exc
        raise
    _mark_locked(db, (job.id for job in jobs))
    return jobs


def lock_job(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> Job | None:
    """One of the user's jobs, its row locked until the transaction ends, fresh from the
    database (with its posting text). Raises JobBusyError when the lock is not free in time."""
    jobs = _lock(db, jobs_of(user_id).where(Job.id == job_id).options(undefer(Job.posting_text)))
    return jobs[0] if jobs else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value.value if isinstance(value, enum.Enum) else value


def set_notes(
    db: Session,
    job: Job,
    notes: str,
    now: datetime,
    *,
    via: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Replace the notes with a `note` event {from, to}. Edits typed within NOTE_COALESCE of the
    previous note edit of the same job (autosave while typing) extend that event instead of
    adding one each: it keeps its `from` and takes the new `to`, and disappears when the text is
    back to where it started. Bulk appends and accepted proposals (`via`) always get their own
    event, with `extra` in its data."""
    if job.notes == notes:
        return False
    last = None
    if via is None:
        last = db.execute(
            sa.select(JobEvent)
            .where(JobEvent.job_id == job.id, JobEvent.user_id == job.user_id)
            .order_by(JobEvent.at.desc(), JobEvent.id.desc())
            .limit(1)
        ).scalar_one_or_none()
    if (
        last is not None
        and last.kind is JobEventKind.note
        and "via" not in last.data
        and last.at >= now - NOTE_COALESCE
    ):
        if last.data.get("from") == notes:
            db.delete(last)
        else:
            last.data = {**last.data, "to": notes}
            last.at = max(now, last.at)
    else:
        data: dict[str, Any] = {"from": job.notes, "to": notes}
        if via:
            data["via"] = via
        if extra:
            data.update(extra)
        add_event(db, job, JobEventKind.note, data, now)
    job.notes = notes
    touch(job, now)
    db.flush()
    return True


def set_manual_fit(db: Session, job: Job, fit: int, now: datetime) -> bool:
    """Set the fit by hand: kept by every rescore until cleared."""
    if job.fit == fit and job.fit_overridden:
        return False
    add_event(db, job, JobEventKind.fit, {"from": job.fit, "to": fit, "manual": True}, now)
    job.fit = fit
    job.fit_overridden = True
    touch(job, now)
    db.flush()
    return True


def clear_manual_fit(db: Session, job: Job, skills: SkillsSection | None, now: datetime) -> bool:
    """Go back to the computed fit. Raises NothingToComputeError when it cannot be computed."""
    if not job.fit_overridden:
        return False
    if skills is None:
        raise NothingToComputeError("The fit cannot be computed: your profile has no skills yet.")
    result = computed_score(job, skills, user_search(db, job.user_id))
    if result is None:
        raise NothingToComputeError("The fit cannot be computed: this job has no posting text.")
    return apply_score(db, job, result, now, overwrite_manual=True)


@dataclass
class JobChanges:
    """What a PATCH asks for. `fields` holds only the editable fields that were sent."""

    fields: dict[str, Any] = field(default_factory=dict)
    status: JobStatus | None = None
    notes: str | None = None
    set_fit: bool = False  # True when `fit` was sent (a number sets it, None clears it)
    fit: int | None = None


def edit_job(
    db: Session,
    job: Job,
    changes: JobChanges,
    now: datetime,
    *,
    expected_updated_at: datetime | None = None,
    skills: Callable[[], SkillsSection | None] = lambda: None,
) -> bool:
    """Apply a PATCH to a locked job (lock_job). Every change writes its event: `edit`
    {changes: {field: {from, to}}}, `status`, `note`, `fit`. Returns whether anything changed.

    Raises StaleJobError when `expected_updated_at` is given and is not the job's updated_at,
    DuplicateJobError when the edited company, title, location or url make it the same posting
    as another of the user's jobs, NothingToComputeError when clearing a manual fit that cannot
    be computed. Nothing is written when it raises (the caller rolls back).
    """
    if expected_updated_at is not None and job.updated_at != expected_updated_at:
        raise StaleJobError(job)
    edits = {
        name: {"from": _jsonable(getattr(job, name)), "to": _jsonable(value)}
        for name, value in changes.fields.items()
        if getattr(job, name) != value
    }
    if edits.keys() & {"company", "title", "location", "url"}:
        after = {name: changes.fields.get(name, getattr(job, name)) for name in EDITABLE_FIELDS}
        existing = find_duplicate(
            db,
            job.user_id,
            company=after["company"],
            title=after["title"],
            location=after["location"],
            url=after["url"],
            exclude=job.id,
        )
        if existing is not None:
            raise DuplicateJobError(existing)
    changed = False
    if edits:
        for name in edits:
            setattr(job, name, changes.fields[name])
        if "url" in edits:
            job.liveness = sa.null()  # SQL NULL: the last check read the old address
        if edits.keys() & {"contract_type", "work_mode"}:
            _badge_again(db, job, "contract_type" in edits)
        add_event(db, job, JobEventKind.edit, {"changes": edits}, now)
        touch(job, now)
        db.flush()
        changed = True
    if changes.status is not None:
        changed = set_status(db, job, changes.status, now) or changed
    if changes.notes is not None:
        changed = set_notes(db, job, changes.notes, now) or changed
    if changes.set_fit:
        if changes.fit is None:
            changed = clear_manual_fit(db, job, skills(), now) or changed
        else:
            changed = set_manual_fit(db, job, changes.fit, now) or changed
    return changed


def _badge_again(db: Session, job: Job, contract_set: bool) -> None:
    """Jobs J30: the person's own contract is a stated one, and the badge follows what the person
    set, against the current criteria of the posting's search (unreadable criteria keep it)."""
    if contract_set:
        job.contract_stated = True
    criteria = criteria_badge.criteria_for(db, job.user_id, job.criteria_search_id)
    if criteria is not None:
        job.outside_criteria = criteria_badge.codes(criteria_badge.facts_of(job), criteria)


# --- bulk ---------------------------------------------------------------------------------------


class MissingJobsError(Exception):
    """Some ids are not the user's jobs (or do not exist). Carries them, in request order."""

    def __init__(self, missing: list[uuid.UUID]) -> None:
        super().__init__("missing jobs")
        self.missing = missing


def lock_jobs(db: Session, user_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> list[Job]:
    """The user's jobs with these ids, in the order given (duplicates once), all locked.
    Raises MissingJobsError naming every id that is not one of them."""
    wanted = list(dict.fromkeys(ids))
    # One lock order for every caller (by id): no deadlock between two bulks.
    found = {
        job.id: job
        for job in _lock(db, jobs_of(user_id).where(Job.id.in_(wanted)).order_by(Job.id))
    }
    missing = [job_id for job_id in wanted if job_id not in found]
    if missing:
        raise MissingJobsError(missing)
    return [found[job_id] for job_id in wanted]


def append_note(existing: str, text: str) -> str:
    """`text` on a new line after the notes, or alone when there are none."""
    stripped = existing.rstrip()
    return f"{stripped}\n{text}" if stripped else text


@dataclass
class BulkResult:
    job: Job
    previous_status: JobStatus
    changed: bool


@dataclass
class BulkOutcome:
    results: list[BulkResult]
    undo: list[undo_tokens.Previous]


def bulk_update(
    db: Session,
    user_id: uuid.UUID,
    ids: Sequence[uuid.UUID],
    now: datetime,
    *,
    status: JobStatus | None = None,
    notes_append: str | None = None,
) -> BulkOutcome:
    """Set the status of and/or append a note to many of the user's jobs, all or nothing (the
    caller commits). Raises MissingJobsError, before any change, when an id is not the user's.
    Returns per job whether it changed, and what an undo of the status change restores."""
    jobs = lock_jobs(db, user_id, ids)
    results: list[BulkResult] = []
    previous: list[tuple[Job, JobStatus, date | None]] = []
    for job in jobs:
        before_status, before_applied = job.status, job.applied_on
        changed = False
        if status is not None and set_status(db, job, status, now, via="bulk", supersede=False):
            previous.append((job, before_status, before_applied))
            changed = True
        if notes_append is not None:
            changed = set_notes(db, job, append_note(job.notes, notes_append), now, via="bulk") or (
                changed
            )
        results.append(BulkResult(job=job, previous_status=before_status, changed=changed))
    supersede_status_proposals(db, user_id, [job.id for job, _, _ in previous], now)
    db.flush()
    undo = [
        undo_tokens.Previous(
            job_id=job.id, status=old, applied_on=applied, updated_at=job.updated_at
        )
        for job, old, applied in previous
    ]
    return BulkOutcome(results=results, undo=undo)


@dataclass
class UndoOutcome:
    restored: list[uuid.UUID]
    skipped: list[uuid.UUID]  # changed again since the bulk change, deleted, or already undone


def undo_bulk(
    db: Session, user_id: uuid.UUID, previous: Sequence[undo_tokens.Previous], now: datetime
) -> UndoOutcome:
    """Put back the status and applied_on each job had before a bulk change, for the jobs still
    exactly as that change left them (same updated_at). Restoring moves updated_at on, so the
    same token restores nothing the second time."""
    ids = [p.job_id for p in previous]
    jobs = {
        job.id: job for job in _lock(db, jobs_of(user_id).where(Job.id.in_(ids)).order_by(Job.id))
    }
    restored: list[uuid.UUID] = []
    skipped: list[uuid.UUID] = []
    for p in previous:
        job = jobs.get(p.job_id)
        if job is None or job.updated_at != p.updated_at:
            skipped.append(p.job_id)
            continue
        set_status(db, job, p.status, now, via="undo", supersede=False)
        job.applied_on = p.applied_on
        restored.append(job.id)
    # Every restored job changed status: the token holds only jobs the bulk moved, and an
    # unchanged updated_at means they still have the status the bulk gave them.
    supersede_status_proposals(db, user_id, restored, now)
    db.flush()
    return UndoOutcome(restored=restored, skipped=skipped)


# --- add by hand (POST /api/jobs) ---------------------------------------------------------------


def add_manual_job(db: Session, user_id: uuid.UUID, new: NewJob, now: datetime) -> tuple[Job, bool]:
    """Create a posting typed in by the user (create_job: DuplicateJobError when it is already
    there) and score it at once when it has a posting text and the profile has skills. Returns
    the job and whether it was scored."""
    job = create_job(db, user_id, new, now)
    if new.posting_text and new.posting_text.strip():
        skills = user_skills(db, user_id)
        if skills is not None:
            search = user_search(db, user_id)
            result = scoring.score(new.posting_text, skills, title=new.title, search=search)
            apply_score(db, job, result, now, via="add")
            return job, True
    return job, False


# --- rescore (POST /api/jobs/rescore) -----------------------------------------------------------


@dataclass
class RescoreOutcome:
    scored: int = 0
    changed: int = 0
    unchanged: int = 0
    skipped_manual: int = 0  # fit set by hand, kept (competencies and gaps still refreshed)
    skipped_no_text: int = 0
    skipped_busy: int = 0  # held by another transaction: not waited for, the next rescore does it
    last_id: uuid.UUID | None = None  # the last job looked at, where a next call resumes
    # With a `threshold`: the fits that reached it from under it (or from none), and those that
    # fell under it. `busy`: the ids counted in `skipped_busy`, for one more try.
    up: int = 0
    down: int = 0
    busy: list[uuid.UUID] = field(default_factory=list)


def rescore_batch(
    db: Session,
    user_id: uuid.UUID,
    skills: SkillsSection,
    now: datetime,
    *,
    ids: Sequence[uuid.UUID] | None,
    after: uuid.UUID | None,
    limit: int,
    overwrite_manual: bool,
    outcome: RescoreOutcome,
    threshold: int | None = None,
) -> int:
    """Rescore up to `limit` of the user's jobs in id order after `after` (only `ids` when
    given), adding to `outcome`. Returns how many jobs it looked at (0 when none are left).
    With a `threshold`, counts the fits that crossed it (`up`, `down`).

    A job another transaction holds (a status change, an accept) is not waited for: it keeps
    its score, counts in `skipped_busy` and is scored again by the next rescore. So a held row
    neither stalls the batch until the statement timeout nor fails the jobs around it."""
    window = sa.select(Job.id).where(Job.user_id == user_id).order_by(Job.id).limit(limit)
    if ids is not None:
        window = window.where(Job.id.in_(list(ids)))
    if after is not None:
        window = window.where(Job.id > after)
    looked = list(db.scalars(window))
    if not looked:
        return 0
    jobs = _lock(
        db,
        jobs_of(user_id)
        .where(Job.id.in_(looked))
        .options(undefer(Job.posting_text))
        .order_by(Job.id),
        skip_locked=True,
    )
    outcome.skipped_busy += len(looked) - len(jobs)
    held = {job.id for job in jobs}
    outcome.busy.extend(job_id for job_id in looked if job_id not in held)
    outcome.last_id = looked[-1]
    search = user_search(db, user_id)  # the bonus (scoring.bonus); None gives none
    for job in jobs:
        read_text(job, now)
        result = computed_score(job, skills, search)
        if result is None:
            outcome.skipped_no_text += 1
            continue
        outcome.scored += 1
        manual_kept = job.fit_overridden and not overwrite_manual
        before = job.fit
        changed = apply_score(
            db, job, result, now, overwrite_manual=overwrite_manual, via="rescore"
        )
        if threshold is not None:
            was = before is not None and before >= threshold
            now_in = job.fit is not None and job.fit >= threshold
            outcome.up += int(now_in and not was)
            outcome.down += int(was and not now_in)
        if manual_kept:
            outcome.skipped_manual += 1
        elif changed:
            outcome.changed += 1
        else:
            outcome.unchanged += 1
    db.flush()
    return len(looked)


def read_text(job: Job, now: datetime) -> bool:
    """Store what the job's text says (signals.stored) when it differs from what is stored, and
    touch the job then. Returns whether it did."""
    columns = signals.stored(job.posting_text)
    if all(getattr(job, name) == value for name, value in columns.items()):
        return False
    for name, value in columns.items():
        setattr(job, name, value)
    touch(job, now)
    return True


def count_after(
    db: Session, user_id: uuid.UUID, ids: Sequence[uuid.UUID] | None, after: uuid.UUID | None
) -> int:
    query = sa.select(sa.func.count()).select_from(Job).where(Job.user_id == user_id)
    if ids is not None:
        query = query.where(Job.id.in_(list(ids)))
    if after is not None:
        query = query.where(Job.id > after)
    return int(db.execute(query).scalar_one())


def missing_ids(db: Session, user_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    wanted = list(dict.fromkeys(ids))
    found = set(
        db.execute(sa.select(Job.id).where(Job.user_id == user_id, Job.id.in_(wanted))).scalars()
    )
    return [job_id for job_id in wanted if job_id not in found]


# --- hand overrides of imported rows (python -m app.jobs.cli mark-overrides) --------------------


@dataclass
class OverrideCandidate:
    job: Job
    computed: int


def override_candidates(
    db: Session, user_id: uuid.UUID, skills: SkillsSection
) -> list[OverrideCandidate]:
    """The user's jobs whose fit differs from the one computed from their posting text (header
    included) and that are not marked as set by hand yet, by slug. A rescore would replace
    these fits, so they are the ones the user set by hand (or scored on another text).

    The engine computed fits without the bonus of the search section (opportunities O4) and
    every scoring path since adds it, so a fit equal to either score is the engine's: a job is
    a candidate only when its fit differs from both. `computed` is the score with the bonus,
    the one a rescore would store now."""
    search = user_search(db, user_id)
    jobs = db.execute(
        jobs_of(user_id)
        .where(Job.fit.is_not(None), Job.fit_overridden.is_(False))
        .options(undefer(Job.posting_text))
        .order_by(Job.slug)
    ).scalars()
    found = []
    for job in jobs:
        text = scoring_text(job)
        if text is None:
            continue
        computed = scoring.score(text, skills, title=job.title, search=search).fit
        plain = scoring.score(text, skills, title=job.title).fit if search else computed
        if job.fit not in (computed, plain):
            found.append(OverrideCandidate(job=job, computed=computed))
    return found


def mark_overridden(db: Session, candidates: Sequence[OverrideCandidate], now: datetime) -> int:
    """Mark these fits as set by hand (fit unchanged), with a `fit` event each. The rows are
    locked first, in id order (the lock order below), then updated."""
    if candidates:
        _lock(
            db,
            sa.select(Job)
            .where(Job.id.in_([c.job.id for c in candidates]))
            .options(undefer(Job.posting_text))
            .order_by(Job.id),
        )
    for candidate in candidates:
        job = candidate.job
        add_event(
            db,
            job,
            JobEventKind.fit,
            {"from": job.fit, "to": job.fit, "manual": True, "via": "mark-overrides"},
            now,
        )
        job.fit_overridden = True
        touch(job, now)
    db.flush()
    return len(candidates)
