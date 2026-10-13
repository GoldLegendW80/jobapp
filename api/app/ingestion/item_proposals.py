"""Profile items as proposals: stored, reviewed and applied to the profile (SPEC-ingestion.md,
"Proposals mapping"; tasks/ingestion/todo.md, "Proposal kinds and targets" and task G5).

A profile item (`app/ingestion/items.py`) lives in the `proposals` table of the agents module
with `target = "profile"`, `target_id = null`, `kind = "profile_item"`, `run_id = null` and

    payload = {ingestion_run_id, section, key, action, value, value_hash, citations[],
               base_revision, base_hash, flags[], conflict?, depends_on?, applied_revision?}

`value` is the item's cleaned value and `value_hash` its hash: `propose` takes an `items.Item`,
so nothing reaches the table that the section model has not validated. `base_revision` is the
profile's revision when the item was proposed and `base_hash` the hash of what the profile held
at the item's key then (the hash of null when it held nothing). `applied_revision` is written by
an accept: the one profile revision it made, or null when the profile already held the item.

What the words mean here:
- **equal to the profile**: applying the item would change nothing. Such an item is not stored
  (`propose` answers `equal_to_profile`), and accepting one that became equal since (the user
  typed the same thing) marks it accepted without a revision.
- **stale**: the profile's value at the item's key is not what it was when the item was
  proposed, and the item would still change it. Computed when read (`review`), never stored. An
  accept of a stale item is refused (`StaleItemError`, nothing applied) unless the caller sends
  the profile revision the user was looking at and it is still the current one: the user then
  saw the current value next to the proposed one, and the item is applied onto the current
  profile. So a later edit of the user is never overwritten unseen. Bulk accept leaves stale
  items out.
- **profile conflict**: the section as it is now cannot take the item (a list at its cap, a
  name another entry holds, a list written as regular expressions). Nothing is applied and the
  item stays pending: the user makes room, edits the item or rejects it.
- **invalid**: the stored value no longer fits its section model. The proposal is marked
  `invalid` by the system, with the reason, and cannot be accepted.
- **conflict** (a flag, with `payload.conflict`): two documents disagree. The user picks one
  value, sent as `edited_value`; an accept without one is refused.

Accepting changes the profile by exactly that item, as one revision, through
`profile.service.save_section`, in the caller's transaction, so the revision and the proposal's
status are committed together or not at all. Lock order (the plan's list): the `profiles` row
(2), then `proposals` rows in id order (8). `propose`, `supersede_older` and `reject_many` take
proposals rows only, and read the profile without locking it: the merge calls them while it
holds its run row (3), which comes after the profile in the order.

Every function takes the caller's user id and touches that user's rows only: another user's
proposal is not found. The caller owns the transaction. Nothing here logs.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agents import proposals
from app.agents.models import Proposal, ProposalDecider, ProposalStatus
from app.ingestion import items
from app.ingestion.citations import SPAN_SOURCE_MAX
from app.profile import service as profile_service
from app.profile.models import Profile

CITATIONS_MAX = 60  # citations kept on one proposal, all documents together
DEPENDS_ON_MAX = 100

# Outcomes of `propose`.
CREATED = "created"
MERGED = "merged"  # the same value was already pending at that key: its citations were joined
EQUAL_TO_PROFILE = "equal_to_profile"  # dropped: the profile already holds it
REJECTED_BEFORE = "rejected_before"  # dropped: the user rejected this value at this key

# Why a bulk call left a proposal out.
NOT_FOUND = "not_found"
NOT_PENDING = "not_pending"
OTHER_RUN = "other_run"
OTHER_SECTION = "other_section"
LOCKED = "locked"
FLAGGED = "flagged"
STALE = "stale"
INVALID = "invalid"
PROFILE_CONFLICT = "profile_conflict"
SECTION_FAILED = "section_failed"  # another item of its section failed: nothing of it applied

CHOOSE = "this item holds several values: choose one of them"
NO_LONGER_FITS = "This value no longer fits the profile: {reason}."

_MISSING: Any = proposals._MISSING


@dataclass(frozen=True)
class Outcome:
    """What `propose` did: `status` is created, merged, equal_to_profile or rejected_before,
    `proposal` the row that now carries the item (None when it was dropped) and `superseded`
    the number of older pending proposals at the same key that it replaced."""

    status: str
    proposal: Proposal | None = None
    superseded: int = 0


@dataclass(frozen=True)
class Review:
    """A pending item against the profile as it is now, for the review page.

    `before` and `after` are what the profile holds at the item's key now and what it would
    hold once the item is accepted (None for nothing). `noop`: the profile already holds it.
    `stale`: see the module. `locked`: a locked fact, accepted on its own. `bulk`: why "accept
    all" leaves it out (locked, flagged, stale, invalid, profile_conflict), or None when it
    takes it. `problem`: the sentence when the item is invalid or does not fit the section."""

    before: Any
    after: Any
    noop: bool
    stale: bool
    locked: bool
    bulk: str | None
    problem: str | None = None


@dataclass(frozen=True)
class SectionFailure:
    """The item that stopped a section in a bulk accept: nothing of that section was applied."""

    proposal_id: uuid.UUID
    reason: str  # invalid (the proposal is now marked so) or profile_conflict
    message: str


@dataclass
class BulkResult:
    """`accepted` (or rejected) proposals, `skipped` ones with the reason each was left out,
    `failed` sections with the item that stopped each, and `revisions`: the one profile revision
    made per section (a section whose items changed nothing has none)."""

    done: list[uuid.UUID] = field(default_factory=list)
    skipped: dict[uuid.UUID, str] = field(default_factory=dict)
    failed: dict[str, SectionFailure] = field(default_factory=dict)
    revisions: dict[str, int] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        """How many were left out, by reason."""
        return dict(Counter(self.skipped.values()))


# --- reading --------------------------------------------------------------------------------------


def _items_of(user_id: uuid.UUID) -> sa.Select[tuple[Proposal]]:
    return proposals.proposals_of(user_id).where(
        Proposal.target == items.TARGET, Proposal.kind == items.KIND
    )


def _at(section: str, key: str) -> tuple[sa.ColumnElement[bool], ...]:
    return (
        Proposal.payload["section"].astext == section,
        Proposal.payload["key"].astext == key,
    )


def _lock_profile(db: Session, user_id: uuid.UUID, now: datetime) -> Profile:
    """The user's profile row, locked until the transaction ends (created empty when the user
    never opened it)."""
    profile_service.get_profile(db, user_id, now)
    return db.execute(
        sa.select(Profile)
        .where(Profile.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def _flags(payload: Mapping[str, Any]) -> list[str]:
    flags = payload.get("flags")
    return [flag for flag in flags if isinstance(flag, str)] if isinstance(flags, list) else []


def bulk_exclusion(payload: Mapping[str, Any]) -> str | None:
    """Why "accept all" never takes this item, whatever the profile holds: `locked` (a locked
    fact) or `flagged` (it reads like an instruction, is derived, cites a transcription, or
    holds a conflict). None when nothing of the kind applies."""
    section, key = payload.get("section"), payload.get("key")
    if not isinstance(section, str) or not isinstance(key, str) or items.is_locked(section, key):
        return LOCKED
    if _flags(payload) or payload.get("conflict"):
        return FLAGGED
    return None


def _hash_at(section: str, data: Mapping[str, Any], key: str) -> str:
    return items.value_hash(items.read(section, data, key))


def review(payload: Mapping[str, Any], section_data: Mapping[str, Any] | None) -> Review:
    """A pending item's payload against the stored section it is about (None when that section
    was never saved). Pure: reads nothing and changes nothing."""
    excluded = bulk_exclusion(payload)
    locked = excluded == LOCKED
    try:
        item = items.from_payload(payload)
    except items.ItemError as exc:
        return Review(None, None, False, False, locked, INVALID, exc.reason)
    current = items.canonical(item.section, section_data)
    before = items.read(item.section, current, item.key)
    stale = items.value_hash(before) != payload.get("base_hash")
    try:
        applied = items.apply(item.section, current, item)
    except items.ProfileConflict as exc:
        return Review(before, None, False, stale, locked, PROFILE_CONFLICT, exc.message)
    noop = applied == current
    stale = stale and not noop
    return Review(
        before,
        items.read(item.section, applied, item.key),
        noop,
        stale,
        locked,
        excluded or (STALE if stale else None),
    )


# --- proposing ------------------------------------------------------------------------------------


def _citation(raw: Mapping[str, Any]) -> dict[str, Any]:
    document_id = raw["document_id"]
    start, end, excerpt = raw["start"], raw["end"], raw["excerpt"]
    if type(start) is not int or type(end) is not int or not 0 <= start <= end:
        raise ValueError("a citation has offsets 0 <= start <= end")
    if not isinstance(excerpt, str) or len(excerpt) > SPAN_SOURCE_MAX:
        raise ValueError(f"an excerpt is a text of at most {SPAN_SOURCE_MAX} characters")
    return {
        "document_id": str(document_id if isinstance(document_id, uuid.UUID) else uuid.UUID(document_id)),
        "excerpt": excerpt,
        "start": start,
        "end": end,
        "transcribed": raw.get("transcribed") is True,
    }


def _joined(stored: Iterable[Mapping[str, Any]], more: Iterable[Mapping[str, Any]]) -> list[Any]:
    """Citations without repeats (one per document and place), the stored ones first."""
    seen: set[tuple[Any, Any, Any]] = set()
    joined: list[Any] = []
    for citation in [*stored, *more]:
        place = (citation.get("document_id"), citation.get("start"), citation.get("end"))
        if place not in seen and len(joined) < CITATIONS_MAX:
            seen.add(place)
            joined.append(dict(citation))
    return joined


def _checked_flags(flags: Iterable[str]) -> list[str]:
    unknown = [flag for flag in flags if flag not in items.FLAGS]
    if unknown:
        raise ValueError("unknown flag: the flags are " + ", ".join(items.FLAGS))
    return [flag for flag in items.FLAGS if flag in set(flags)]


def propose(
    db: Session,
    user_id: uuid.UUID,
    item: items.Item,
    now: datetime,
    *,
    ingestion_run_id: uuid.UUID | None,
    citations: Sequence[Mapping[str, Any]],
    flags: Iterable[str] = (),
    conflict: Sequence[tuple[items.Item, Sequence[Mapping[str, Any]]]] | None = None,
    depends_on: Sequence[str] | None = None,
) -> Outcome:
    """Store `item` as a pending proposal of the user, or say why it is not stored.

    - The profile already holds it (applying it would change nothing): dropped,
      `equal_to_profile`. An item with a `conflict` is never dropped this way.
    - The user rejected this value at this key before: dropped, `rejected_before`.
    - The same value and action is already pending at this key: `merged`, the citations and the
      flags are joined on that proposal, which now belongs to this run.
    - Otherwise `created`. Every other pending proposal at this key is superseded.

    `citations` are `{document_id, excerpt, start, end, transcribed?}` as the citation checker
    gave them; a transcribed one flags the item. `conflict` lists the other values documents
    give for this key, each an item of its own with its citations: it flags the item. Raises
    ValueError for a citation, a flag or a conflict of the wrong shape (the caller's mistake,
    never the model's: the value went through `items.build`).

    One writer at a time per user and key. The profile is read, not locked."""
    section, key = item.section, item.key
    cited = _joined((), (_citation(raw) for raw in citations))
    kept_flags = set(_checked_flags(flags))
    if any(citation["transcribed"] for citation in cited):
        kept_flags.add(items.FLAG_TRANSCRIBED)
    others: list[dict[str, Any]] = []
    for other, other_citations in conflict or ():
        if (other.section, other.key) != (section, key):
            raise ValueError("a conflict is between values of one key")
        others.append(
            {
                "value": other.value,
                "value_hash": other.value_hash,
                "citations": _joined((), (_citation(raw) for raw in other_citations)),
            }
        )
    if others:
        kept_flags.add(items.FLAG_CONFLICT)
    depends = [str(name) for name in depends_on or ()]
    if len(depends) > DEPENDS_ON_MAX or any(len(name) > items.KEY_MAX for name in depends):
        raise ValueError("depends_on names at most 100 keys")

    lock = f"proposals:{user_id}:{items.TARGET}:{section}:{key}"
    db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(lock, 0))))
    profile = profile_service.get_profile(db, user_id, now)
    current = items.canonical(section, (profile.sections or {}).get(section))
    if not others:
        try:
            if items.apply(section, current, item) == current:
                return Outcome(EQUAL_TO_PROFILE)
        except items.ProfileConflict:
            pass  # it does not fit today: the user sees it and decides
    rejected = db.scalar(
        _items_of(user_id)
        .where(
            Proposal.status == ProposalStatus.rejected,
            *_at(section, key),
            Proposal.payload["value_hash"].astext == item.value_hash,
            Proposal.payload["action"].astext == item.action.value,
        )
        .limit(1)
    )
    if rejected is not None:
        return Outcome(REJECTED_BEFORE)

    pending = db.scalars(
        _items_of(user_id)
        .where(Proposal.status == ProposalStatus.pending, *_at(section, key))
        .order_by(Proposal.id)
        .with_for_update()
    ).all()
    same: Proposal | None = None
    superseded = 0
    for older in pending:
        stored = older.payload
        if (
            same is None
            and not others
            and not stored.get("conflict")
            and stored.get("value_hash") == item.value_hash
            and stored.get("action") == item.action.value
        ):
            same = older
        else:
            proposals._decide(
                older, ProposalStatus.superseded, ProposalDecider.system, now, proposals.SUPERSEDED
            )
            superseded += 1
    run = None if ingestion_run_id is None else str(ingestion_run_id)
    if same is not None:
        stored = same.payload
        merged_flags = _checked_flags({*kept_flags, *_flags(stored)} & set(items.FLAGS))
        same.payload = {
            **stored,
            "ingestion_run_id": run,
            "citations": _joined(stored.get("citations") or (), cited),
            "flags": merged_flags,
        }
        db.flush()
        return Outcome(MERGED, same, superseded)

    payload: dict[str, Any] = {
        "ingestion_run_id": run,
        "section": section,
        "key": key,
        "action": item.action.value,
        "value": item.value,
        "value_hash": item.value_hash,
        "citations": cited,
        "base_revision": profile.revision,
        "base_hash": _hash_at(section, current, key),
        "flags": _checked_flags(kept_flags),
    }
    if others:
        payload["conflict"] = others
    if depends:
        payload["depends_on"] = depends
    proposal = Proposal(
        user_id=user_id,
        run_id=None,
        target=items.TARGET,
        target_id=None,
        kind=items.KIND,
        payload=payload,
        status=ProposalStatus.pending,
        created_at=now,
    )
    db.add(proposal)
    db.flush()
    return Outcome(CREATED, proposal, superseded)


def propose_payload(
    db: Session, user_id: uuid.UUID, payload: Mapping[str, Any], now: datetime
) -> Outcome:
    """`propose` from a raw payload `{section, key, action, value, citations, ingestion_run_id?,
    flags?, conflict?: [{value, citations}], depends_on?}`: what `proposals.create` calls for the
    target "profile". The values are built like an agent's (or like the merge's when the flags
    say `derived`). Raises proposals.InvalidValueError when an item is refused."""
    flags = payload.get("flags") or ()
    origin = items.Origin.derived if items.FLAG_DERIVED in flags else items.Origin.agent
    section, key, action = payload.get("section"), payload.get("key"), payload.get("action")
    try:
        item = items.build(section, key, action, payload.get("value"), origin=origin)
        conflict = [
            (items.build(section, key, action, other["value"], origin=origin), other["citations"])
            for other in payload.get("conflict") or ()
        ]
    except items.ItemError as exc:
        raise proposals.InvalidValueError(f"{exc.index}: {exc.reason}") from None
    run = payload.get("ingestion_run_id")
    return propose(
        db,
        user_id,
        item,
        now,
        ingestion_run_id=None if run is None else uuid.UUID(str(run)),
        citations=payload.get("citations") or (),
        flags=flags,
        conflict=conflict or None,
        depends_on=payload.get("depends_on"),
    )


def supersede_older(
    db: Session, user_id: uuid.UUID, now: datetime, *, keep_run: uuid.UUID | None
) -> int:
    """Mark every pending profile item of the user that does not belong to `keep_run` as
    superseded by the system, in id order. For the merge of a newer run. Returns how many."""
    older = db.scalars(
        _items_of(user_id)
        .where(Proposal.status == ProposalStatus.pending)
        .order_by(Proposal.id)
        .with_for_update()
    ).all()
    keep = None if keep_run is None else str(keep_run)
    count = 0
    for proposal in older:
        if keep is None or proposal.payload.get("ingestion_run_id") != keep:
            proposals._decide(
                proposal,
                ProposalStatus.superseded,
                ProposalDecider.system,
                now,
                proposals.SUPERSEDED,
            )
            count += 1
    db.flush()
    return count


# --- accepting ------------------------------------------------------------------------------------


def _invalid(db: Session, proposal: Proposal, now: datetime, reason: str) -> None:
    proposals._decide(
        proposal,
        ProposalStatus.invalid,
        ProposalDecider.system,
        now,
        NO_LONGER_FITS.format(reason=reason),
    )
    db.flush()


def _accepted(proposal: Proposal, now: datetime, revision: int | None) -> None:
    proposals._decide(proposal, ProposalStatus.accepted, ProposalDecider.user, now)
    proposal.payload = {**proposal.payload, "applied_revision": revision}


def _edited(payload: Mapping[str, Any], value: Any) -> items.Item:
    """The user's own value for a proposed item: validated by the section model alone."""
    try:
        return items.build(
            payload.get("section"),
            payload.get("key"),
            payload.get("action"),
            value,
            origin=items.Origin.user,
        )
    except items.ItemError as exc:
        loc = tuple(
            int(part) if part.isdigit() else part
            for part in exc.index.replace("]", "").replace("[", ".").split(".")[1:]
        )
        raise proposals.EditedValueError(exc.reason, loc if exc.index.startswith("value") else ())


def accept(
    db: Session,
    user_id: uuid.UUID,
    proposal_id: uuid.UUID,
    now: datetime,
    *,
    edited_value: Any = _MISSING,
    profile_revision: int | None = None,
) -> tuple[Proposal, Profile] | None:
    """Apply one pending profile item to the user's profile and mark it accepted by the user.
    None when it is not one of the user's proposals. `proposals.accept` calls this for the
    target "profile".

    `edited_value` is the user's own value for the item: it replaces the proposed one at the
    proposal's key, is validated by the section model alone (it may hold a setting, or what the
    citation checker would refuse) and is recorded on the proposal. `profile_revision` is the
    revision of the profile the user was looking at.

    Raises, nothing applied: proposals.NotPendingError; proposals.InvalidValueError when the kind
    is not a profile item, when the edited value does not fit (EditedValueError, with the field)
    or when the item holds several values and none was chosen; proposals.ItemInvalidError when
    the stored value no longer fits its section model, after marking the proposal invalid (the
    caller commits that); proposals.ProfileConflictError when the section as it is cannot take
    the item (the caller rolls back); proposals.StaleItemError when the profile changed at the
    item's key since it was proposed and `profile_revision` is not the current revision (the
    caller rolls back, and shows the current value)."""
    proposal = proposals.get(db, user_id, proposal_id)
    if proposal is None:
        return None
    if proposal.status is not ProposalStatus.pending:
        raise proposals.NotPendingError(proposal)
    if proposal.kind != items.KIND:
        raise proposals.InvalidValueError(proposals.UNKNOWN_KIND)
    edited = None if edited_value is _MISSING else _edited(proposal.payload, edited_value)

    profile = _lock_profile(db, user_id, now)
    proposal = proposals.get(db, user_id, proposal_id, lock=True)
    if proposal is None:
        return None
    if proposal.status is not ProposalStatus.pending:
        raise proposals.NotPendingError(proposal)
    payload = proposal.payload
    try:
        item = items.from_payload(payload)
    except items.ItemError as exc:
        _invalid(db, proposal, now, exc.reason)
        raise proposals.ItemInvalidError(proposal) from None
    if edited is None and payload.get("conflict"):
        raise proposals.InvalidValueError(CHOOSE)

    section = item.section
    current = items.canonical(section, (profile.sections or {}).get(section))
    try:
        after = items.apply(section, current, edited or item, at=item.key)
    except items.ProfileConflict as exc:
        raise proposals.ProfileConflictError(proposal, exc.message) from None
    revision: int | None = None
    if after != current:
        changed = _hash_at(section, current, item.key) != payload.get("base_hash")
        if changed and profile_revision != profile.revision:
            raise proposals.StaleItemError(proposal, profile.revision)
        profile = profile_service.save_section(db, user_id, section, after, profile.revision, now)
        revision = profile.revision
    _accepted(proposal, now, revision)
    if edited is not None:
        proposal.edited_value = edited.value
    db.flush()
    return proposal, profile


def _selected(
    db: Session,
    user_id: uuid.UUID,
    result: BulkResult,
    proposal_ids: Sequence[uuid.UUID] | None,
    ingestion_run_id: uuid.UUID | None,
    section: str | None,
) -> list[Proposal]:
    """The pending profile items a bulk call is about, locked in id order. With `proposal_ids`,
    each id that is not one of them is recorded in `result.skipped` with the reason."""
    query = _items_of(user_id)
    if proposal_ids is None:
        query = query.where(Proposal.status == ProposalStatus.pending)
        if ingestion_run_id is not None:
            query = query.where(
                Proposal.payload["ingestion_run_id"].astext == str(ingestion_run_id)
            )
        if section is not None:
            query = query.where(Proposal.payload["section"].astext == section)
    else:
        query = query.where(Proposal.id.in_(list(proposal_ids)))
    rows = db.scalars(
        query.order_by(Proposal.id).with_for_update().execution_options(populate_existing=True)
    ).all()
    found = {row.id for row in rows}
    for proposal_id in proposal_ids or ():
        if proposal_id not in found:
            result.skipped[proposal_id] = NOT_FOUND
    run = None if ingestion_run_id is None else str(ingestion_run_id)
    selected = []
    for row in rows:
        if row.status is not ProposalStatus.pending:
            result.skipped[row.id] = NOT_PENDING
        elif run is not None and row.payload.get("ingestion_run_id") != run:
            result.skipped[row.id] = OTHER_RUN
        elif section is not None and row.payload.get("section") != section:
            result.skipped[row.id] = OTHER_SECTION
        else:
            selected.append(row)
    return selected


def accept_many(
    db: Session,
    user_id: uuid.UUID,
    now: datetime,
    *,
    proposal_ids: Sequence[uuid.UUID] | None = None,
    ingestion_run_id: uuid.UUID | None = None,
    section: str | None = None,
) -> BulkResult:
    """"Accept all": every pending profile item of the user (of one run, of one section, or the
    ids given, which must then also be of that run and section) except the locked and the
    flagged ones, in one profile revision per section.

    Left out, each with its reason in `result.skipped`: an id that is not the user's pending
    profile item (`not_found`, `not_pending`), one of another run or section, a locked fact, a
    flagged item (instruction, derived, transcribed, conflict), a stale one. What remains is
    applied section by section, in the order the items were proposed, all or nothing: when one
    item no longer fits its model (it is marked invalid) or the section cannot take it, nothing
    of that section is applied, `result.failed[section]` names the item, and its other items
    are skipped as `section_failed`. A section whose items change nothing gets no revision.

    The caller commits: the revisions, the accepted proposals and the ones marked invalid."""
    result = BulkResult()
    profile = _lock_profile(db, user_id, now)
    by_section: dict[str, list[Proposal]] = {}
    for row in _selected(db, user_id, result, proposal_ids, ingestion_run_id, section):
        excluded = bulk_exclusion(row.payload)
        if excluded is not None:
            result.skipped[row.id] = excluded
        else:
            by_section.setdefault(row.payload["section"], []).append(row)
    for name in sorted(by_section):
        batch = sorted(by_section[name], key=lambda row: (row.created_at, row.id))
        current = items.canonical(name, (profile.sections or {}).get(name))
        after = current
        taken: list[Proposal] = []
        failure: SectionFailure | None = None
        for row in batch:
            try:
                item = items.from_payload(row.payload)
            except items.ItemError as exc:
                _invalid(db, row, now, exc.reason)
                failure = SectionFailure(row.id, INVALID, exc.reason)
                break
            if _hash_at(name, current, item.key) != row.payload.get("base_hash"):
                try:
                    if items.apply(name, current, item) != current:
                        result.skipped[row.id] = STALE
                        continue
                except items.ProfileConflict:
                    pass  # reported below, by the apply onto the batch
            try:
                after = items.apply(name, after, item)
            except items.ProfileConflict as exc:
                failure = SectionFailure(row.id, PROFILE_CONFLICT, exc.message)
                break
            taken.append(row)
        if failure is not None:
            result.failed[name] = failure
            for row in batch:
                if row.id != failure.proposal_id and row.id not in result.skipped:
                    result.skipped[row.id] = SECTION_FAILED
            continue
        revision: int | None = None
        if after != current:
            profile = profile_service.save_section(db, user_id, name, after, profile.revision, now)
            revision = result.revisions[name] = profile.revision
        for row in taken:
            _accepted(row, now, revision)
            result.done.append(row.id)
    db.flush()
    return result


def reject_many(
    db: Session,
    user_id: uuid.UUID,
    now: datetime,
    *,
    proposal_ids: Sequence[uuid.UUID] | None = None,
    ingestion_run_id: uuid.UUID | None = None,
    section: str | None = None,
) -> BulkResult:
    """Reject several pending profile items at once, with no exclusion: locked and flagged ones
    too. Ids that are not the user's pending profile items of that run and section are left out
    with the reason. Nothing else changes."""
    result = BulkResult()
    for row in _selected(db, user_id, result, proposal_ids, ingestion_run_id, section):
        proposals._decide(row, ProposalStatus.rejected, ProposalDecider.user, now)
        result.done.append(row.id)
    db.flush()
    return result
