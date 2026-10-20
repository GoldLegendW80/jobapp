"""The swipe deck (opportunities O1; SPEC-opportunities, "The swipe deck").

**What is in it.** The person's postings with status `found` or `shortlisted`; no application of
the person on the posting in any state but `cancelled` (a live one, a submitted one and an ended
one all keep the posting out; a cancelled Like puts it back); a fit at or above "Show postings
from" (`search.min_fit`, 70 by default), or no fit yet on a posting without text (nothing to
score it from); and no never apply rule of the person matching its company or title through
apply's folded matcher (`apply.guardrails.matching_rule`, its patterns built once per call),
so a title the sweep's whole word match missed (a soft hyphen inside a word, full width letters)
never shows. When the stored rules cannot be read, the deck is empty and says so
(`rules_unreadable`): it fails closed.

**Order.** Fit, highest first (no fit last), then the newest `found_on`, then the id: a total
order, so a reload shows the same card and the cursor names a place in it.

**Pages.** At most LIMIT_MAX cards per call. The cursor is the sort key of the last card given
(fit, found_on, id), not an offset: the next page holds the cards after that key in the deck as
it is now, so a Pass, a new posting or a threshold changed between two pages never makes a card
come twice, and the walk always ends. `left` counts the cards from the start of this page on.

**Filters**, any combination: contract types (`ContractType` values), `remote` or `on_site`
(until O3 stores the work mode, `remote` is the sweep's own rule: the location names "remote"),
and the source (`ashby` matches every `ashby:<org>` board, `ashby:mistral` that board alone).

**The card.** Id, title, company, location, contract type, source and address, fit, found_on,
the match reason (`match_reason`: up to three competencies the posting asks that are not gaps,
and the first gap), and `pay` and `work_mode`, None until O3 lands them.

The never apply match runs in Python over the light columns of every candidate (id, company,
title, fit, found_on): linear in the person's postings, one fold per title and rule. Only the
page's rows are read in full.
"""

import base64
import binascii
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import caps, guardrails
from app.apply.models import Application, State
from app.jobs import sweep
from app.jobs.models import ContractType, Job, JobStatus
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


@dataclass(frozen=True)
class Card:
    posting: Job
    reason: Reason


@dataclass(frozen=True)
class Deck:
    cards: list[Card]
    next_cursor: str | None
    left: int
    mode: str
    min_fit: int
    rules_unreadable: bool


SortKey = tuple[bool, int, int, str]


def sort_key(fit: int | None, found_on: date, posting_id: uuid.UUID) -> SortKey:
    """The deck's order as one ascending tuple: fit descending with no fit last, found_on
    descending, then the id (its canonical text orders as Postgres orders uuids)."""
    return (fit is None, -(fit or 0), -found_on.toordinal(), str(posting_id))


def encode_cursor(fit: int | None, found_on: date, posting_id: uuid.UUID) -> str:
    raw = json.dumps([fit, found_on.isoformat(), str(posting_id)], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(text: str) -> SortKey:
    if len(text) > CURSOR_MAX:
        raise InvalidCursorError("cursor too long")
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        fit, day, posting_id = json.loads(raw)
        if fit is not None and (type(fit) is not int or not 0 <= fit <= 100):
            raise ValueError(fit)
        return sort_key(fit, date.fromisoformat(day), uuid.UUID(posting_id))
    except (binascii.Error, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise InvalidCursorError("damaged cursor") from exc


def match_reason(competencies: Sequence[str], gaps: Sequence[str]) -> Reason:
    """Up to MATCHES_SHOWN competencies the posting asks that are not gaps, in the stored order,
    and the first gap: "Matches: Python, LLM evaluation, FastAPI. Gap: Kubernetes.". A part with
    nothing to say is left out; with neither, no text."""
    gap_set = set(gaps)
    matches = tuple(dict.fromkeys(c for c in competencies if c and c not in gap_set))
    shown = matches[:MATCHES_SHOWN]
    gap = next((g for g in gaps if g), None)
    parts = []
    if shown:
        parts.append(f"Matches: {', '.join(shown)}.")
    if gap is not None:
        parts.append(f"Gap: {gap}.")
    return Reason(matches=shown, gap=gap, text=" ".join(parts) or None)


def _candidates(user_id: uuid.UUID, min_fit: int, filters: Filters) -> sa.Select:
    text = sa.func.coalesce(sa.func.btrim(Job.posting_text, BLANK), "")
    applied = sa.exists().where(
        Application.user_id == user_id,
        Application.posting_id == Job.id,
        Application.state != State.cancelled,
    )
    query = sa.select(Job.id, Job.company, Job.title, Job.fit, Job.found_on).where(
        Job.user_id == user_id,
        Job.status.in_(DECK_STATUSES),
        sa.or_(Job.fit >= min_fit, sa.and_(Job.fit.is_(None), text == "")),
        ~applied,
    )
    if filters.types:
        query = query.where(Job.contract_type.in_(filters.types))
    if filters.remote is not None:
        names_remote = sa.func.strpos(sa.func.lower(Job.location), "remote") > 0
        query = query.where(names_remote if filters.remote == "remote" else ~names_remote)
    if filters.source is not None:
        query = query.where(
            sa.or_(
                Job.source == filters.source,
                sa.func.split_part(Job.source, ":", 1) == filters.source,
            )
        )
    return query


def _patterns(rules: Sequence[NeverApplyRule]) -> list[re.Pattern[str]]:
    """Apply's pattern of each rule (`guardrails.matching_rule` builds the same ones per call),
    built once per deck instead of once per posting."""
    built = (guardrails._term_pattern(rule.terms) for rule in rules)
    return [pattern for pattern in built if pattern is not None]


def _excluded(patterns: Sequence[re.Pattern[str]], company: str, title: str) -> bool:
    """`guardrails.matching_rule(rules, company, title) is not None`, on the built patterns."""
    if not patterns:
        return False
    folded = " \n ".join(guardrails.fold(text) for text in (company, title) if text)
    return any(pattern.search(folded) is not None for pattern in patterns)


def _search(db: Session, user_id: uuid.UUID) -> tuple[int, list[NeverApplyRule] | None]:
    """The threshold and the never apply rules; rules None when the section cannot be read."""
    try:
        search = sweep.load_search(db, user_id)
    except sweep.InvalidSearchError:
        return DEFAULT_MIN_FIT, None
    if search is None:
        return DEFAULT_MIN_FIT, []
    return search.min_fit, sweep.never_apply_rules(search)


def deck(
    db: Session,
    user_id: uuid.UUID,
    *,
    cursor: str | None = None,
    limit: int = LIMIT_MAX,
    filters: Filters = NO_FILTERS,
) -> Deck:
    """One page of the person's deck. Raises InvalidCursorError for a cursor it did not write."""
    after = decode_cursor(cursor) if cursor else None
    limit = max(1, min(limit, LIMIT_MAX))
    min_fit, rules = _search(db, user_id)
    mode = caps.settings_of(db, user_id).mode_after_like.value
    if rules is None:
        return Deck([], None, 0, mode, min_fit, rules_unreadable=True)

    patterns = _patterns(rules)
    rows = db.execute(_candidates(user_id, min_fit, filters)).all()
    keyed = sorted(
        (sort_key(row.fit, row.found_on, row.id), row)
        for row in rows
        if not _excluded(patterns, row.company, row.title)
    )
    if after is not None:
        keyed = [(key, row) for key, row in keyed if key > after]
    page = keyed[:limit]
    ids = [row.id for _, row in page]
    postings = {
        job.id: job
        for job in db.execute(
            sa.select(Job).where(Job.user_id == user_id, Job.id.in_(ids))
        ).scalars()
    }
    cards = [
        Card(postings[i], match_reason(postings[i].competencies, postings[i].gaps))
        for i in ids
        if i in postings
    ]
    last = page[-1][1] if page else None
    next_cursor = (
        encode_cursor(last.fit, last.found_on, last.id)
        if last is not None and len(keyed) > len(page)
        else None
    )
    return Deck(cards, next_cursor, len(keyed), mode, min_fit, rules_unreadable=False)
