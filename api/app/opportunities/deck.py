"""The swipe deck (opportunities O1; SPEC-opportunities, "The swipe deck").

**What is in it.** The person's postings with status `found` or `shortlisted`; no application of
the person on the posting in any state but `cancelled` (a live one, a submitted one and an ended
one all keep the posting out; a cancelled Like puts it back); a fit at or above "Show postings
from" (`search.min_fit`, 70 by default), or no fit yet on a posting without text (nothing to
score it from); and not refused by apply's own decision, `apply.guardrails.never_apply`, in the
person's mode after a Like: a never apply rule folded onto its company or title (and in Full auto
onto the first FULL_AUTO_TEXT_CHARS of its text), or a sweep that once skipped it for a rule
(`swept`, whatever the rules say today). So a card the deck shows is never one a Like refuses as
`never_apply_role`, and a title the sweep's whole word match missed (a soft hyphen inside a word,
full width letters) never shows. When the stored rules cannot be read, the deck is empty and says
so (`rules_unreadable`): it fails closed.

**Order.** A posting whose closing date is within SOON_DAYS (`notices.closes_soon`, from today
on) first, the soonest first (owner, 2026-11-07, "build all" of the career-ops analysis); then
fit, highest first (no fit last), then the newest `found_on`, then the id: a total order, so a
reload shows the same card and the cursor names a place in it.

**Pages.** At most LIMIT_MAX cards per call. The cursor is the sort key of the last card given
(fit, found_on, id, closes_on) and the day the walk began, not an offset: the next page holds
the cards after that key in the deck as it is now, ordered on that same day, so a Pass, a new
posting, a threshold changed or midnight between two pages never makes a card come twice, and
the walk always ends. `left` counts the cards from the start of this page on.

**Filters**, any combination: contract types (`ContractType` values), `remote` or `on_site`
(the work mode the posting states, opportunities O3: `remote` keeps the remote ones, `on_site`
the office ones, and a hybrid posting matches neither; for a posting that states none, the
sweep's own rule: `remote` when the location names "remote", `on_site` otherwise),
and the source (`ashby` matches every `ashby:<org>` board, `ashby:mistral` that board alone).

**The card.** Id, title, company, location, contract type, source and address, fit, found_on,
the match reason (`match_reason`: up to three competencies the posting asks that are not gaps,
the first gap, and what of the person's seniority, industries and company types the posting
matches, the words of the score's bonus, scoring.bonus, read against the profile as it is
now), and the pay (`pay.pay_text`) and the work mode the posting states (O3), None when it
states none, and what to know before the click (`notices.notices_of`).

The never apply decision runs in Python over the light columns of every candidate (id, company,
title, fit, found_on, whether the sweep skipped it for a rule, and in Full auto the first
FULL_AUTO_TEXT_CHARS of the text, cut by Postgres `left`, which counts characters as Python
slicing counts code points): linear in the person's postings. Only the page's rows are read in
full.
"""

import base64
import binascii
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session, undefer

from app.apply import caps, guardrails
from app.apply.models import Application, Mode, State
from app.jobs import notices, scoring, sweep
from app.jobs.models import ContractType, Job, JobEvent, JobEventKind, JobStatus, WorkMode
from app.jobs.notices import Notice
from app.jobs.sweep import NeverApplyRule
from app.profile.schemas import SearchSection

DECK_STATUSES = (JobStatus.found, JobStatus.shortlisted)
LIMIT_MAX = 20
SOURCE_MAX = 200
CURSOR_MAX = 300
BLANK = " \t\r\n\f\v"  # what a posting text of nothing but spaces holds
MATCHES_SHOWN = 3
DEFAULT_MIN_FIT: int = SearchSection.model_fields["min_fit"].default

Remote = Literal["remote", "on_site"]


class InvalidCursorError(ValueError):
    """A cursor this module did not write (damaged, or of another endpoint)."""


@dataclass(frozen=True)
class Filters:
    types: tuple[ContractType, ...] = ()
    remote: Remote | None = None
    source: str | None = None


NO_FILTERS = Filters()


@dataclass(frozen=True)
class Reason:
    matches: tuple[str, ...]
    gap: str | None
    text: str | None
    also: tuple[str, ...] = ()  # what earned the score's bonus


@dataclass(frozen=True)
class Card:
    posting: Job
    reason: Reason
    notices: tuple[Notice, ...] = ()


@dataclass(frozen=True)
class Deck:
    cards: list[Card]
    next_cursor: str | None
    left: int
    mode: str
    min_fit: int
    rules_unreadable: bool


SortKey = tuple[bool, int, bool, int, int, str]


def sort_key(
    fit: int | None,
    found_on: date,
    posting_id: uuid.UUID,
    closes_on: date | None = None,
    today: date | None = None,
) -> SortKey:
    """The deck's order as one ascending tuple: closing within SOON_DAYS of `today` first, the
    soonest first, then fit descending with no fit last, found_on descending, then the id (its
    canonical text orders as Postgres orders uuids)."""
    soon = today is not None and notices.closes_soon(closes_on, today)
    first = closes_on.toordinal() if soon and closes_on is not None else 0
    return (not soon, first, fit is None, -(fit or 0), -found_on.toordinal(), str(posting_id))


def encode_cursor(
    fit: int | None,
    found_on: date,
    posting_id: uuid.UUID,
    closes_on: date | None = None,
    today: date | None = None,
) -> str:
    values = [fit, found_on.isoformat(), str(posting_id)]
    if today is not None:
        values += [closes_on.isoformat() if closes_on else None, today.isoformat()]
    raw = json.dumps(values, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(text: str) -> tuple[SortKey, date | None]:
    """The key of the last card given and the day its walk began (None in a cursor written
    before the closing order, read as a deck with no closing date)."""
    if len(text) > CURSOR_MAX:
        raise InvalidCursorError("cursor too long")
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        values = json.loads(raw)
        if type(values) is not list or len(values) not in (3, 5):
            raise ValueError(values)
        fit, day, posting_id = values[:3]
        closes = date.fromisoformat(values[3]) if len(values) == 5 and values[3] else None
        today = date.fromisoformat(values[4]) if len(values) == 5 else None
        if fit is not None and (type(fit) is not int or not 0 <= fit <= 100):
            raise ValueError(fit)
        key = sort_key(fit, date.fromisoformat(day), uuid.UUID(posting_id), closes, today)
        return key, today
    except (binascii.Error, UnicodeDecodeError, AttributeError, TypeError, ValueError) as exc:
        raise InvalidCursorError("damaged cursor") from exc


def match_reason(
    competencies: Sequence[str], gaps: Sequence[str], also: Sequence[str] = ()
) -> Reason:
    """Up to MATCHES_SHOWN competencies the posting asks that are not gaps, in the stored order,
    the first gap, and what earned the bonus: "Matches: Python, LLM evaluation, FastAPI. Gap:
    Kubernetes. Also fits: senior, fintech, startup.". A part with nothing to say is left out;
    with none, no text."""
    gap_set = set(gaps)
    matches = tuple(dict.fromkeys(c for c in competencies if c and c not in gap_set))
    shown = matches[:MATCHES_SHOWN]
    gap = next((g for g in gaps if g), None)
    parts = []
    if shown:
        parts.append(f"Matches: {', '.join(shown)}.")
    if gap is not None:
        parts.append(f"Gap: {gap}.")
    extra = tuple(dict.fromkeys(a for a in also if a))
    if extra:
        parts.append(f"Also fits: {', '.join(extra)}.")
    return Reason(matches=shown, gap=gap, text=" ".join(parts) or None, also=extra)


def _candidates(user_id: uuid.UUID, min_fit: int, filters: Filters, mode: Mode) -> sa.Select:
    text = sa.func.coalesce(sa.func.btrim(Job.posting_text, BLANK), "")
    applied = sa.exists().where(
        Application.user_id == user_id,
        Application.posting_id == Job.id,
        Application.state != State.cancelled,
    )
    swept = sa.exists().where(  # the query of `guardrails.swept_never_apply`
        JobEvent.user_id == user_id,
        JobEvent.job_id == Job.id,
        JobEvent.kind == JobEventKind.status,
        JobEvent.data["via"].astext == "never_apply",
    )
    head = (
        sa.func.left(Job.posting_text, guardrails.FULL_AUTO_TEXT_CHARS)
        if mode is Mode.full_auto
        else sa.null()  # never_apply reads no text outside Full auto
    )
    columns = (Job.id, Job.company, Job.title, Job.fit, Job.found_on, Job.closes_on)
    query = sa.select(*columns, head.label("posting_text"), swept.label("swept")).where(
        Job.user_id == user_id,
        Job.status.in_(DECK_STATUSES),
        sa.or_(Job.fit >= min_fit, sa.and_(Job.fit.is_(None), text == "")),
        ~applied,
    )
    if filters.types:
        query = query.where(Job.contract_type.in_(filters.types))
    if filters.remote is not None:
        # The stated work mode (O3) first: remote is remote, on site is office, and a hybrid
        # posting is neither. A posting that states none, by the sweep's own rule.
        names_remote = sa.func.strpos(sa.func.lower(Job.location), "remote") > 0
        wanted = WorkMode.remote if filters.remote == "remote" else WorkMode.office
        by_location = names_remote if filters.remote == "remote" else ~names_remote
        query = query.where(
            sa.case((Job.work_mode.is_not(None), Job.work_mode == wanted), else_=by_location)
        )
    if filters.source is not None:
        query = query.where(
            sa.or_(
                Job.source == filters.source,
                sa.func.split_part(Job.source, ":", 1) == filters.source,
            )
        )
    return query


def _excluded(row: sa.Row, rules: Sequence[NeverApplyRule], mode: Mode) -> bool:
    """Apply's own decision on the candidate's light row, which carries every column
    `guardrails.never_apply` reads (company, title, and posting_text cut to its Full auto head)."""
    return guardrails.never_apply(row, rules, swept=row.swept, mode=mode) is not None


def _search(
    db: Session, user_id: uuid.UUID
) -> tuple[int, list[NeverApplyRule] | None, SearchSection | None]:
    """The threshold, the never apply rules and the section; rules None when the section cannot
    be read."""
    try:
        search = sweep.load_search(db, user_id)
    except sweep.InvalidSearchError:
        return DEFAULT_MIN_FIT, None, None
    if search is None:
        return DEFAULT_MIN_FIT, [], None
    return search.min_fit, sweep.never_apply_rules(search), search


def _also(posting: Job, search: SearchSection | None) -> tuple[str, ...]:
    """The words of the score's bonus this posting earns from the person's search section."""
    if search is None:
        return ()
    return scoring.bonus(posting.posting_text or "", posting.title, search).matched


def deck(
    db: Session,
    user_id: uuid.UUID,
    *,
    today: date,
    cursor: str | None = None,
    limit: int = LIMIT_MAX,
    filters: Filters = NO_FILTERS,
) -> Deck:
    """One page of the person's deck, ordered on `today` or on the day the cursor's walk began.
    Raises InvalidCursorError for a cursor it did not write."""
    after, began = decode_cursor(cursor) if cursor else (None, today)
    limit = max(1, min(limit, LIMIT_MAX))
    min_fit, rules, search = _search(db, user_id)
    mode = Mode(caps.settings_of(db, user_id).mode_after_like.value)
    if rules is None:
        return Deck([], None, 0, mode.value, min_fit, rules_unreadable=True)

    rows = db.execute(_candidates(user_id, min_fit, filters, mode)).all()
    keyed = sorted(
        (sort_key(row.fit, row.found_on, row.id, row.closes_on, began), row)
        for row in rows
        if not _excluded(row, rules, mode)
    )
    if after is not None:
        keyed = [(key, row) for key, row in keyed if key > after]
    page = keyed[:limit]
    ids = [row.id for _, row in page]
    postings = {
        job.id: job
        for job in db.execute(
            sa.select(Job)
            .where(Job.user_id == user_id, Job.id.in_(ids))
            .options(undefer(Job.posting_text))
        ).scalars()
    }
    said = notices.notices_of(db, user_id, list(postings.values()), today)
    cards = [
        Card(
            postings[i],
            match_reason(postings[i].competencies, postings[i].gaps, _also(postings[i], search)),
            tuple(said[i]),
        )
        for i in ids
        if i in postings
    ]
    last = page[-1][1] if page else None
    next_cursor = (
        encode_cursor(last.fit, last.found_on, last.id, last.closes_on, began)
        if last is not None and len(keyed) > len(page)
        else None
    )
    return Deck(cards, next_cursor, len(keyed), mode.value, min_fit, rules_unreadable=False)
