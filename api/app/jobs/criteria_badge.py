"""What of a posting is outside the criteria of the search that found it (jobs J30, #829; SPEC-jobs,
"Kept with a badge"). Owner, 2026-11-26: "Je veux vraiment qu'on remonte un max de job", and for a
posting in a wanted place that fits the profile but is outside a criterion, "Garder avec un
badge": it is stored, its badge says what is outside, Discover and Full auto never take it.

The badge is computed by code from the posting's own fields, never taken from an agent (`codes`):
- `contract`: a contract the posting states, of a type the search's contract types do not hold
  (none saved: any contract). `importer.kind` answers CDI when no word says otherwise, so a CDI
  counts only when it was stated (`jobs.contract_stated`: a word of the title, location or
  address, a source, the posting reader or the person). The contract every posting of a website
  has by nature (B21, the VIE website's) is the person's choice of it when that website found it.
- `work_mode`: a work mode the posting states other than the saved remote stance ("any" or none
  saved: any mode). Within only when they are the same (WORK_MODES; a planner default).
- `older`: a published date more than the search's days before the day the posting was found.
An unstated field gives no code.

It runs at the store and when a stored posting is found again (sweep._store_finding, against the
sweep's frozen criteria), once the posting reader filled the fields (reader.save), and for the
person's open postings of a search when its criteria are saved (`refresh`: the main search through
the profile's section callback, a saved search through searches.change and searches.delete). A
posting's search is the one that found it last (`jobs.criteria_search_id`; null, or a saved search
deleted since, is the main search). A row stored before migration 0051 has no badge (null) until
one of those passes reaches it.
"""

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.browser.nature import nature_contract
from app.jobs.importer import kind
from app.jobs.models import ContractType, Job, JobStatus, SavedSearch
from app.profile import service as profile_service
from app.profile.models import Profile
from app.profile.schemas import SearchSection

CONTRACT, WORK_MODE, OLDER = "contract", "work_mode", "older"
CODES = (CONTRACT, WORK_MODE, OLDER)
OPEN = (JobStatus.found, JobStatus.shortlisted)
# The posting's work modes within each saved remote stance; "any" and none hold every mode.
WORK_MODES = {"remote": {"remote"}, "hybrid": {"hybrid"}, "office": {"office"}}
_CDI = re.compile(r"\bCDI\b", re.IGNORECASE)


@dataclass(frozen=True)
class Criteria:
    """The criteria of one search the badge reads: its contract types, its remote stance and its
    window in days."""

    contracts: frozenset[str] = frozenset()
    stance: str | None = None
    days: int | None = None

    @classmethod
    def of(cls, search: SearchSection | None) -> "Criteria":
        if search is None:
            return cls()
        contracts = frozenset(contract.value for contract in search.contract_types)
        return cls(contracts, search.remote_stance, search.days)

    @classmethod
    def read(cls, raw: object) -> "Criteria | None":
        """A stored search (the profile's section, a saved search's criteria): no section is no
        criterion; one that no longer validates is None, and the badges stay as they are."""
        if raw is None:
            return cls()
        try:
            return cls.of(SearchSection.model_validate(raw))
        except ValueError:
            return None


@dataclass(frozen=True)
class Facts:
    """What the badge reads of a posting: its contract when stated, its work mode, its published
    date, the day it was found, and the contract its website has by nature."""

    contract: str | None
    work_mode: str | None
    published_on: date | None
    found_on: date | None
    nature: str | None = None


def codes(facts: Facts, criteria: Criteria) -> list[str]:
    """The badge: the codes of CODES the posting is outside of, in that order; [] within."""
    found: list[str] = []
    if (
        facts.contract is not None
        and criteria.contracts
        and facts.contract not in criteria.contracts
        and facts.contract != facts.nature
    ):
        found.append(CONTRACT)
    within = WORK_MODES.get(criteria.stance or "")
    if facts.work_mode is not None and within is not None and facts.work_mode not in within:
        found.append(WORK_MODE)
    if (
        facts.published_on is not None
        and facts.found_on is not None
        and criteria.days is not None
        and (facts.found_on - facts.published_on).days > criteria.days
    ):
        found.append(OLDER)
    return found


def stated_contract(title: str, location: str = "", url: str = "") -> ContractType | None:
    """The contract the title, location or address states, else None (`kind` answers CDI when
    no word says otherwise)."""
    said = kind(title, location, url)
    if said is not ContractType.CDI:
        return said
    return ContractType.CDI if _CDI.search(f"{title} {location} {url}") else None


def nature_of(source: str | None) -> str | None:
    """The contract by nature of a website among the sources that found the posting (B21)."""
    for name in (source or "").split(", "):
        contract = nature_contract(f"https://{name.strip()}/") if name.strip() else None
        if contract is not None:
            return contract
    return None


def facts_of(row: Any, contract_read: bool = False) -> Facts:
    """A job row's facts (a Job or a row of its columns); `contract_read`: the posting reader has
    just quoted its contract. A contract stored before 0051 (stated unknown) counts as stated
    unless it is CDI, the one `kind` gives when nothing says it."""
    contract = getattr(row.contract_type, "value", row.contract_type)
    stated = True if contract_read else row.contract_stated
    if stated is False or (stated is None and contract == "CDI"):
        contract = None
    mode = getattr(row.work_mode, "value", row.work_mode)
    return Facts(contract, mode, row.published_on, row.found_on, nature_of(row.source))


def criteria_for(db: Session, user_id: uuid.UUID, search_id: uuid.UUID | None) -> Criteria | None:
    """The current criteria of the person's search `search_id` (None, or a saved search that is
    gone: the main search)."""
    if search_id is not None:
        saved = db.execute(
            sa.select(SavedSearch.criteria).where(
                SavedSearch.user_id == user_id, SavedSearch.id == search_id
            )
        ).scalar_one_or_none()
        if isinstance(saved, Mapping):
            return Criteria.read(dict(saved))
    sections = db.execute(
        sa.select(Profile.sections).where(Profile.user_id == user_id)
    ).scalar_one_or_none()
    return Criteria.read((sections or {}).get("search"))


def _of_search(user_id: uuid.UUID, search_id: uuid.UUID | None) -> sa.ColumnElement[bool]:
    if search_id is not None:
        return Job.criteria_search_id == search_id
    gone = ~(
        sa.select(SavedSearch.id)
        .where(SavedSearch.user_id == user_id, SavedSearch.id == Job.criteria_search_id)
        .exists()
    )
    return sa.or_(Job.criteria_search_id.is_(None), gone)


def refresh(
    db: Session, user_id: uuid.UUID, search_id: uuid.UUID | None, criteria: Criteria | None
) -> int:
    """Compute again the badge of the person's open postings (Found, Shortlisted) of one search,
    in the caller's transaction, `updated_at` kept (no edit of the person's). Criteria that cannot
    be read change nothing. The number of badges changed."""
    if criteria is None:
        return 0
    rows = db.execute(
        sa.select(
            Job.id,
            Job.contract_type,
            Job.contract_stated,
            Job.work_mode,
            Job.published_on,
            Job.found_on,
            Job.source,
            Job.outside_criteria,
        ).where(Job.user_id == user_id, Job.status.in_(OPEN), _of_search(user_id, search_id))
    ).all()
    changed = [
        {"badge_id": row.id, "badge": badge}
        for row in rows
        if (badge := codes(facts_of(row), criteria)) != row.outside_criteria
    ]
    if changed:
        table = Job.__table__
        db.execute(
            sa.update(table)
            .where(table.c.id == sa.bindparam("badge_id"))
            .values(outside_criteria=sa.bindparam("badge"), updated_at=table.c.updated_at),
            changed,
        )
    return len(changed)


def on_sections(
    db: Session, user_id: uuid.UUID, sections: frozenset[str], revision: int, now: datetime
) -> None:
    """The profile's callback: a change of the main search's section computes its badges again."""
    if "search" in sections:
        refresh(db, user_id, None, criteria_for(db, user_id, None))


profile_service.register_section_callback(on_sections)
