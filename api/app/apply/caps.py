"""Company caps, the person's pause, Full auto's one per company rule and safety ceiling, and the
pace per ATS host (SPEC-apply, "Guardrails", 1 and 5). Apply A11 owns this file.

`check(db, application, now)` is the gate's step 5 (`gate.decide`) and runs again before the click
(`submit.recheck`). The caller holds the posting's row lock and the person's apply lock, so two
decisions or clicks of one person go one at a time and the second counts the first. It answers
the code of the first refusal, or None; every code leaves the application `ready` (`KEEP`):

1. `paused_by_you`: the person's "Pause automatic applications" (`apply_settings.paused`).
2. `company_cap`: a cap of the person names the company and `max_count` applications are counted
   in its last `window_days` days; `company_cap_role`: the same role (the company and the
   normalised title) was counted in its last `same_role_days` days.
3. Full auto only: `full_auto_company`, one application per company in `company_days` days unless
   a cap of the person names the company; `safety_ceiling`, the person's submissions since
   midnight in the time zone of `search.sweep_schedule.timezone`.

What is counted: the person's other applications the app clicked (`submitted`, `submit_unknown`,
at `submitted_at`) or is clicking (`submitting`, at `now`). At the decision (the application not
yet `approved` or `held`) the other decided ones (`approved`, `held`) count too, at `now`, so of
two approvals racing for the last slot one passes; before the click they do not, so two approved
applications never refuse each other and the first to click takes the slot. A click exactly
`window_days` days ago still counts.

A company matches a cap by its key (`company_key`) on the posting's company or on the ATS
organisation of the form address (jobs.ashbyhq.com/<org>/..., boards.greenhouse.io/<org>/...,
jobs.lever.co/<org>/...).

The pace per ATS host (`next_slot`, `reserve`) is shared by every person, who all go out through
the Jetson's one address: one click every `APPLY_HOST_MIN_INTERVAL_S` and at most
`APPLY_HOST_DAILY_MAX` in any 24 hours per host (LinkedIn: its own two settings). A host is the ATS
for the known ones (every Greenhouse form is one host), else the form address's host. The clicks
are the `click` events the submit lease writes in the transaction that moves the application to
`submitting`: `reserve` takes a transaction lock per host and reads them again under it, so two
people's clicks go one at a time and the second sees the first. A paced application stays
`approved` and waits, with the time it goes (`submit.py`).

Declared open points (`company_key`, each toward refusing more, except the last three):
- two different companies whose names differ only by legal forms, joining words or spacing are
  one company ("Example Group" and "Example");
- a company written in two scripts ("Пример" and "Primer") or under an abbreviation ("P&G" and
  "Procter and Gamble") is two companies, unless the ATS organisation matches;
- a name followed by a place or a division ("Example Robotics Paris") is another company than
  the cap's, unless the ATS organisation matches;
- postings marked applied by hand, without an application of the app, are not counted (the spec
  counts `submitted` and `submit_unknown` rows).
"""

import hashlib
import json
import uuid
import zoneinfo
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.apply import guardrails, service
from app.apply.caps_models import (
    COUNT_MAX,
    DAYS_MAX,
    LABEL_MAX,
    ApplySettings,
    CompanyCap,
    LikeMode,
)
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.jobs import service as jobs_service
from app.jobs import sweep
from app.jobs.models import Job
from app.profile.schemas import SweepSchedule

S = State
SENTENCES: dict[str, str] = {
    "paused_by_you": (
        "You paused automatic applications. Turn them back on in Settings, Applications."
    ),
    "company_cap": (
        "You set a limit of applications for this company and it is reached. You can change it "
        "in Settings, Applications."
    ),
    "company_cap_role": (
        "You chose to wait before applying again to this role at this company. You can change it "
        "in Settings, Applications."
    ),
    "full_auto_company": "Full auto already applied to this company recently.",
    "safety_ceiling": "Today's safety ceiling of submissions is reached. This one waits for you.",
}
KEEP = frozenset(SENTENCES)
CAPS_MAX = 100
DAY = timedelta(hours=24)
CLICKING = (S.submitting, S.submitted, S.submit_unknown)
DECIDED = (S.approved, S.held)
KNOWN_HOSTS = frozenset({"ashby", "greenhouse", "lever"})
_ORG_HOSTS = {"ashby": "ashbyhq.com", "greenhouse": "greenhouse.io", "lever": "lever.co"}
_LEGAL_FORMS = frozenset(
    {
        "inc", "incorporated", "ltd", "limited", "llc", "llp", "plc", "sas", "sasu", "sa", "sarl",
        "eurl", "sci", "gmbh", "ag", "kg", "ug", "bv", "nv", "corp", "corporation", "co",
        "company", "se", "spa", "srl", "sl", "ab", "as", "asa", "aps", "oy", "kk", "pty", "pte",
        "group", "holding", "holdings",
    }
)  # fmt: skip
_JOINING = frozenset({"and", "et", "und", "the"})


def _setting(name: str) -> Any:
    from app.config import ConfigError, Settings, get_settings

    try:
        return getattr(get_settings(), name)
    except ConfigError:
        return Settings.model_fields[name].default


# --- the company ---------------------------------------------------------------------------------


def company_key(text: str) -> str:
    """The company as caps compare it: folded as the never apply rules fold (compatibility
    forms, accents, case, letters of other scripts drawn like Latin ones), cut into words of
    letters and digits, runs of single letters joined ("S.A.S." is "sas"), joining words and
    trailing legal forms dropped, the rest glued: "Exämple Robotics, Inc." and "example-robotics"
    are "examplerobotics". Empty when nothing is left."""
    folded = guardrails.fold(text or "")
    words = "".join(c if c.isalnum() else " " for c in folded).split()
    merged: list[str] = []
    letters = ""
    for word in words:
        if len(word) == 1 and word.isalpha():
            letters += word
            continue
        merged += [letters, word] if letters else [word]
        letters = ""
    merged += [letters] if letters else []
    words = [w for w in merged if w not in _JOINING] or merged
    while len(words) > 1 and words[-1] in _LEGAL_FORMS:
        words.pop()
    return "".join(words)[:LABEL_MAX]


def _org(ats: str, url: str) -> str | None:
    parts = urlsplit(guardrails.unstub(url or "", _setting("apply_stub_base_url")))
    host = (parts.hostname or "").lower().rstrip(".")
    domain = _ORG_HOSTS.get(ats)
    if domain is None or not (host == domain or host.endswith("." + domain)):
        return None
    segments = [s for s in parts.path.split("/") if s]
    if segments and segments[0] != "embed":
        return segments[0]
    return dict(parse_qsl(parts.query)).get("for")


def company_keys(company: str, ats: str, form_url: str) -> frozenset[str]:
    """The keys an application answers to: its posting's company and its form's ATS org."""
    keys = {company_key(company)}
    if (org := _org(ats, form_url)) is not None:
        keys.add(company_key(org))
    return frozenset(k for k in keys if k)


# --- the person's settings -----------------------------------------------------------------------


@dataclass(frozen=True)
class Values:
    """A person's Applications settings as they apply: the stored ones, else the defaults."""

    mode_after_like: LikeMode
    paused: bool
    min_fit: int
    min_fit_chosen: bool
    company_days: int
    safety_ceiling: int
    hold_s: int
    full_auto_since: datetime | None
    rehearsals_done: int
    timezone: str
    show_postings_from: int | None
    write_documents_from: int | None


def settings_of(db: Session, user_id: uuid.UUID) -> Values:
    row = db.get(ApplySettings, user_id)
    try:
        search = sweep.load_search(db, user_id)
    except sweep.InvalidSearchError:
        search = None
    write_from = search.documents.min_fit if search is not None else None
    fit_default = (
        write_from if write_from is not None else int(_setting("apply_full_auto_min_fit_default"))
    )

    def stored(value: int | None, name: str) -> int:
        return value if value is not None else int(_setting(name))

    return Values(
        mode_after_like=row.mode_after_like if row else LikeMode.review,
        paused=bool(row and row.paused),
        min_fit=row.min_fit if row and row.min_fit is not None else fit_default,
        min_fit_chosen=bool(row and row.min_fit is not None),
        company_days=stored(row and row.company_days, "apply_full_auto_company_days"),
        safety_ceiling=stored(row and row.safety_ceiling, "apply_full_auto_safety_ceiling"),
        hold_s=stored(row and row.hold_s, "apply_hold_s"),
        full_auto_since=row.full_auto_since if row else None,
        rehearsals_done=row.rehearsals_done if row else 0,
        timezone=(search.sweep_schedule if search else SweepSchedule()).timezone,
        show_postings_from=search.min_fit if search is not None else None,
        write_documents_from=write_from,
    )


def save_settings(
    db: Session, user_id: uuid.UUID, now: datetime, *, paused: bool | None, min_fit: int | None
) -> None:
    """The person's changes (None leaves a value as it is), in the caller's transaction."""
    values: dict[str, Any] = {"updated_at": now}
    if paused is not None:
        values["paused"] = paused
    if min_fit is not None:
        values["min_fit"] = min_fit
    row = insert(ApplySettings).values(user_id=user_id, **values)
    db.execute(row.on_conflict_do_update(index_elements=["user_id"], set_=values))


def day_start(now: datetime, timezone: str) -> datetime:
    """Midnight of `now`'s day in `timezone` (UTC when the name is unknown)."""
    try:
        zone: Any = zoneinfo.ZoneInfo(timezone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        zone = zoneinfo.ZoneInfo("UTC")
    return datetime.combine(now.astimezone(zone).date(), time(0), tzinfo=zone)


# --- the check -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Counted:
    keys: frozenset[str]
    role: str
    at: datetime


def counted(
    db: Session,
    user_id: uuid.UUID,
    now: datetime,
    since: datetime,
    *,
    exclude: uuid.UUID | None = None,
    deciding: bool = False,
) -> list[Counted]:
    """The person's applications that count (module docstring) but `exclude`, clicked since
    `since`; `deciding` counts the decided ones too."""
    wanted = list(CLICKING) + (list(DECIDED) if deciding else [])
    rows = db.execute(
        sa.select(
            Job.company,
            Job.title,
            Application.ats,
            Application.form_url,
            Application.state,
            Application.submitted_at,
        )
        .join(Job, sa.and_(Job.id == Application.posting_id, Job.user_id == Application.user_id))
        .where(
            Application.user_id == user_id,
            Application.id != exclude if exclude is not None else sa.true(),
            Application.state.in_(wanted),
            sa.or_(
                Application.state.not_in((S.submitted, S.submit_unknown)),
                Application.submitted_at >= since,
            ),
        )
    ).all()
    return [
        Counted(
            company_keys(company, ats, url),
            guardrails.title_key(title),
            at if state in (S.submitted, S.submit_unknown) and at is not None else now,
        )
        for company, title, ats, url, state, at in rows
    ]


def caps_of(db: Session, user_id: uuid.UUID) -> list[CompanyCap]:
    return list(
        db.execute(
            sa.select(CompanyCap)
            .where(CompanyCap.user_id == user_id)
            .order_by(CompanyCap.label, CompanyCap.id)
        ).scalars()
    )


def check(db: Session, application: Application, now: datetime) -> str | None:
    """The code of the first rule the submission would break, or None (module docstring). The
    caller holds the posting's row lock and the person's apply lock."""
    values = settings_of(db, application.user_id)
    if values.paused:
        return "paused_by_you"
    posting = db.get(Job, application.posting_id)
    if posting is None:
        return None
    keys = company_keys(posting.company, application.ats, application.form_url)
    caps = [cap for cap in caps_of(db, application.user_id) if cap.company_key in keys]
    full_auto = application.mode is Mode.full_auto
    if not caps and not full_auto:
        return None
    days = [cap.window_days for cap in caps] + [cap.same_role_days or 0 for cap in caps]
    days += [values.company_days] if full_auto else []
    # Two days more than the longest window: a day of the person's time zone can last 25 hours.
    others = counted(
        db,
        application.user_id,
        now,
        now - timedelta(days=max(days) + 2),
        exclude=application.id,
        deciding=application.state not in DECIDED,
    )
    role = guardrails.title_key(posting.title)
    for cap in caps:
        mine = [o for o in others if cap.company_key in o.keys]
        if sum(o.at >= now - timedelta(days=cap.window_days) for o in mine) >= cap.max_count:
            return "company_cap"
        role_since = now - timedelta(days=cap.same_role_days or 0)
        if cap.same_role_days and any(o.role == role and o.at >= role_since for o in mine):
            return "company_cap_role"
    if not full_auto:
        return None
    company_since = now - timedelta(days=values.company_days)
    if not caps and any(o.keys & keys and o.at >= company_since for o in others):
        return "full_auto_company"
    today = day_start(now, values.timezone)
    if sum(o.at >= today for o in others) >= values.safety_ceiling:
        return "safety_ceiling"
    return None


# --- the caps the person edits -------------------------------------------------------------------


@dataclass(frozen=True)
class CapIn:
    label: str
    max_count: int
    window_days: int
    same_role_days: int | None = None


class CapsError(ValueError):
    """A list of caps the person cannot save: its code (`stale`, `no_company`, `same_company`,
    `too_many`, `out_of_range`) and the label it is about, if any."""

    def __init__(self, code: str, label: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.label = label


def revision(caps: list[CompanyCap]) -> str:
    """What the page showed: a hash of the caps, sent back with a change (409 when it moved)."""
    rows = sorted(
        (c.company_key, c.label, c.max_count, c.window_days, c.same_role_days) for c in caps
    )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def used(db: Session, user_id: uuid.UUID, caps: list[CompanyCap], now: datetime) -> list[int]:
    """For each cap, the applications counted in its window now (what the page shows)."""
    if not caps:
        return []
    longest = max(cap.window_days for cap in caps)
    others = counted(db, user_id, now, now - timedelta(days=longest))
    return [
        sum(c.company_key in o.keys and o.at >= now - timedelta(days=c.window_days) for o in others)
        for c in caps
    ]


def replace_caps(
    db: Session, user_id: uuid.UUID, items: list[CapIn], seen: str, now: datetime
) -> list[CompanyCap]:
    """The person's whole list of caps replaced by `items`, under the person's apply lock, if
    the list is still the one the page showed (`seen`, else CapsError `stale`). The caller
    commits. A cap lowered below what is counted refuses the next ones; nothing is undone."""
    db.execute(
        sa.text("select set_config('lock_timeout', :ms, true)"),
        {"ms": f"{jobs_service.LOCK_TIMEOUT_MS}ms"},
    )
    service._lock_person(db, user_id)
    current = caps_of(db, user_id)
    if revision(current) != seen:
        raise CapsError("stale")
    if len(items) > CAPS_MAX:
        raise CapsError("too_many")
    made: list[CompanyCap] = []
    keys: set[str] = set()
    for item in items:
        label = " ".join(item.label.split())[:LABEL_MAX]
        key = company_key(label)
        if not key:
            raise CapsError("no_company", label)
        if key in keys:
            raise CapsError("same_company", label)
        days = [item.window_days] + ([item.same_role_days] if item.same_role_days else [])
        if not 1 <= item.max_count <= COUNT_MAX or not all(1 <= d <= DAYS_MAX for d in days):
            raise CapsError("out_of_range", label)
        keys.add(key)
        made.append(
            CompanyCap(
                user_id=user_id,
                company_key=key,
                label=label,
                max_count=item.max_count,
                window_days=item.window_days,
                same_role_days=item.same_role_days or None,
                created_at=now,
            )
        )
    db.execute(sa.delete(CompanyCap).where(CompanyCap.user_id == user_id))
    db.add_all(made)
    db.flush()
    return caps_of(db, user_id)


# --- the pace per ATS host -----------------------------------------------------------------------


@dataclass(frozen=True)
class Pace:
    interval: timedelta
    daily_max: int


def host_of(ats: str, form_url: str) -> str:
    """The host a click is paced on: the ATS for the known ones, else the form's host."""
    if ats in KNOWN_HOSTS:
        return ats
    url = guardrails.unstub(form_url or "", _setting("apply_stub_base_url"))
    host = (urlsplit(url).hostname or "").lower().rstrip(".").removeprefix("www.")
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "linkedin"
    return host or "unknown"


def pace_of(host: str) -> Pace:
    if host == "linkedin":
        return Pace(
            timedelta(seconds=_setting("apply_linkedin_min_interval_s")),
            _setting("apply_linkedin_daily_max"),
        )
    return Pace(
        timedelta(seconds=_setting("apply_host_min_interval_s")), _setting("apply_host_daily_max")
    )


def next_slot(db: Session, application: Application, now: datetime) -> datetime | None:
    """When the application's host takes its next click, None when it takes one now. Reads every
    person's clicks of the last 24 hours; only `reserve` makes the answer hold until the click."""
    host = host_of(application.ats, application.form_url)
    pace = pace_of(host)
    rows = db.execute(
        sa.select(ApplicationEvent.at, Application.form_url)
        .join(
            Application,
            sa.and_(
                Application.id == ApplicationEvent.application_id,
                Application.user_id == ApplicationEvent.user_id,
            ),
        )
        .where(
            ApplicationEvent.kind == "click",
            ApplicationEvent.at > now - DAY,
            Application.ats == application.ats,
        )
    ).all()
    clicks = sorted(at for at, url in rows if host_of(application.ats, url) == host)
    waits = []
    if clicks and now < clicks[-1] + pace.interval:
        waits.append(clicks[-1] + pace.interval)
    if len(clicks) >= pace.daily_max:
        waits.append(clicks[-pace.daily_max] + DAY)
    return max(waits, default=None)


def reserve(db: Session, application: Application, now: datetime) -> datetime | None:
    """`next_slot` under the host's transaction lock: None means the caller's click event, written
    in this transaction, takes the slot; another person's reserve waits for this commit."""
    host = host_of(application.ats, application.form_url)
    key = f"apply_pace:{host}"
    try:
        with db.begin_nested():
            db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(key, 0))))
    except DBAPIError as exc:
        if not jobs_service.is_lock_timeout(exc):
            raise
        return now + pace_of(host).interval
    return next_slot(db, application, now)
