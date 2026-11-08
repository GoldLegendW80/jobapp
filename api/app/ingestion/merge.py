"""The merge of an ingestion run (G12): from the items its steps stored to proposals.

`engine.advance` calls `run` once, under the run's row lock (3), in the transaction that closes
the run `done`. The merge is code: no model is called, nothing a model wrote decides what is
compared, and nothing is written to the profile. It only writes `proposals` rows (8), through
G5's `item_proposals`, which the user then accepts, edits or rejects one click at a time.

The rules, in the order they apply:

1. **The items read.** The rows of `reuse.source_steps` that pass `stored_items.of_stored_text()`
   (the one condition every reader applies). The rows of those steps that fail it cite a text
   that is no longer stored: they are deleted here, in the same transaction, and never proposed.
   A row that removes an entry, a line or a whole list (`items.removes_a_list`) is dropped: the
   extraction refuses such a removal since PR #398, and a step finished before it was reused
   without being read again while `plan.EXTRACTOR_VERSION` stayed 1 (owner, 2026-10-21, #408).
   It is 2 since the extraction prompt changed (owner, 2026-11-08: the fields of a skill
   category, the years a document states), so a step of version 1 is read again on the next
   reading; the drop stays as a guard.
2. **Older proposals.** Every pending profile item of the user that belongs to another run is
   superseded, in id order, before anything is proposed. So the pending items after a merge are
   exactly this run's, and the proposals are a function of the items, the profile and the user's
   earlier decisions alone.
3. **One key, one proposal.** The items are grouped by section and key. Equal values (the
   section model's cleaned form, by `value_hash`) become one proposal that carries every
   citation, in the order the documents were selected, then by place. A removal at a key
   where a document gives a value is dropped: a value with a checked citation wins over the
   absence of one.
   - **One entry under two keys** (`alike.same_entry`, rule B): the entries of a job, a school,
     a language or a skill category that are one entry written two ways ("NOVACARE" and "NOVACARE
     GIE" over the same dates, "English" and "Anglais", "React Native" and "react-native")
     form one group, in which every two entries are one entry: a school without years that
     is like two degrees of that school joins neither. A group that matches an entry the
     profile holds takes that entry's key, so it updates that entry or is dropped, never adds
     a second one; an item like two entries that are not one entry (two of the profile among
     them) is left under its own key, and so are the items like an entry of the profile that
     are not one entry with each other. The key format of `items.entry_key` is unchanged.
   - **Values that agree** (`alike.combine`, rule A): the values of one group that agree field
     by field (the same text but for its capitals, dashes or accents, one field empty, word
     lists joined, one phone number written two ways, an organisation inside the other's name)
     become one value, built again by the section model (`items.build`) and hashed as such,
     that carries every citation. Only additions combine; a value combines only when every
     field does, else the group goes on to rule 5 with the values as the documents gave them.
4. **Against the profile.** A value the profile already holds at that key is dropped, and so is
   a value the user rejected before at that key (or at the key the item had before rule B), or
   turned down by writing their own (an edit at accept, or the other values of a conflict they
   settled). A value that agrees with what the profile holds (rule A, the profile's wording
   kept) is held already; one that adds to it is proposed as the profile's entry with what it
   adds. This is decided here, by applying the item to the stored section in code
   (`items.apply`), and again by `propose`.
5. **Conflicts.** When several different values are left at one key, they become one proposal
   flagged `conflict`: the first value by hash order as the item, the others beside it, each with
   its own citations. A year alone and a month of that year are different values, so a conflict.
   Nothing picks for the user: an item with a conflict is accepted only with the value chosen
   (or by "Accept all additions" with `fill_empty`, which picks the CV's value for an empty slot;
   the values it leaves are not turned down, so a later run may offer them again).
6. **Flags.** `looks_like_instruction` is carried from the stored rows (a hint, never a safety
   control), `transcribed` is set on a citation into a page a model wrote from an image, and
   `derived` and `conflict` are set by this code only: no flag of a stored row can set them.
7. **The years of experience.** A figure a document states (`skills.years_experience`, cited
   and checked as a number of years) is proposed like any other value. Only when no document
   states one, the years are computed here (see `years_of`: the union of the periods, an overlap
   counted once) from the employers of the profile with this run's employer items applied, and
   proposed with the origin `derived` and `depends_on` naming those items, only when the profile
   has no value yet.

An item this code cannot propose (its section model changed since it was stored, the stored
section no longer fits its form) is left out and counted: one bad row never fails the merge,
because a merge that raises five times closes the run as stuck (`engine.STUCK`).

This module is imported by the api, whose image holds no Claude Agent SDK: it imports
`stored_items`, never `tools`.
"""

import dataclasses
import logging
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agents import proposals
from app.agents.models import Proposal
from app.ingestion import alike, item_proposals, items, reuse, stored_items
from app.ingestion.models import IngestionDocument, TextSource
from app.ingestion.runs_models import IngestionItem, IngestionRun
from app.profile import schemas
from app.profile import service as profile_service

log = logging.getLogger(__name__)

YEARS_SECTION = "skills"
YEARS_KEY = "years_experience"
EMPLOYERS_SECTION = "experience"
EMPLOYERS_SLOT = "employers"
PAGE_BREAK = "\f"
DECIDED_MAX = 20_000  # the newest values the user turned down, read to drop them again
SAME_ENTRY_MAX = 400  # entries of one slot compared two by two (rule B): past that, keys alone

_REFUSED = (items.ItemError, items.ProfileConflict, proposals.InvalidValueError, ValueError)


@dataclass
class Variant:
    """One value documents give for a key: the item, every citation and every flag of its rows."""

    item: items.Item
    citations: list[dict[str, Any]] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    place: tuple[Any, ...] = ()  # where its first row stands: document order, then the text


@dataclass
class Group:
    """The values of one fact: `key` is where it is proposed, None while the values of a group
    that rule B joined from several keys decide it; `natural` are the keys its items carry."""

    section: str
    key: str | None
    variants: list[Variant]
    removal: Variant | None = None
    natural: tuple[str, ...] = ()


@dataclass(frozen=True)
class Merged:
    """What one merge did, for the log and the tests: nothing here holds a value."""

    proposals: int = 0
    conflicts: int = 0
    equal_to_profile: int = 0
    rejected_before: int = 0
    removals_dropped: int = 0
    removals_refused: int = 0  # list removals stored before the extraction refused them (#408)
    left_out: int = 0
    superseded: int = 0
    deleted: int = 0
    derived: bool = False


# --- the derived years of experience --------------------------------------------------------------


def _months_of(entry: Mapping[str, Any], open_end: int) -> tuple[int, int] | None:
    """The months of one employer as a half open range, read at its shortest: a start that is a
    year alone counts from December, an end that is a year alone counts to January, no end ends
    at `open_end` (the month the run started). None when the entry has no usable start."""
    try:
        start = schemas.period_span(entry["start"])[1]
        end = schemas.period_span(entry["end"])[0] if entry.get("end") else open_end
    except (KeyError, TypeError, ValueError):
        return None
    return start, max(start, end)  # a period that comes out negative counts zero


def months_of(employers: Iterable[Mapping[str, Any]], open_end: int) -> int:
    """The months worked: the union of the employers' periods, an overlap counted once."""
    spans = sorted(span for span in (_months_of(one, open_end) for one in employers) if span)
    total, reached = 0, None
    for start, end in spans:
        if reached is None or start > reached:
            total += end - start
            reached = end
        elif end > reached:
            total += end - reached
            reached = end
    return total


def years_of(employers: Iterable[Mapping[str, Any]], started: datetime) -> float | None:
    """`floor(months / 12 * 10) / 10` over the union of the periods, None when there is no
    employer with a start or when no month can be counted. Never rounded up."""
    listed = [one for one in employers if isinstance(one, Mapping) and one.get("start")]
    if not listed:
        return None
    months = months_of(listed, started.year * 12 + started.month - 1)
    return (months * 10 // 12) / 10 if months > 0 else None


# --- reading the items ----------------------------------------------------------------------------


def _delete_replaced(db: Session, steps: Sequence[uuid.UUID]) -> int:
    """Delete the rows of these steps that cite a text no longer stored. Answers how many."""
    if not steps:
        return 0
    deleted = db.execute(
        sa.delete(IngestionItem)
        .where(IngestionItem.step_id.in_(steps), sa.not_(stored_items.of_stored_text()))
        .execution_options(synchronize_session=False)
    )
    return int(deleted.rowcount or 0)


def _transcribed_pages(db: Session, document_ids: Iterable[uuid.UUID]) -> dict[str, Any]:
    """Per document id: True when every page was written by a model from an image, or the
    sorted character offsets at which its transcribed pages start and end."""
    ids = sorted(set(document_ids))
    if not ids:
        return {}
    document = IngestionDocument
    marks: dict[str, Any] = {}
    rows = db.execute(
        sa.select(document.id, document.text_source, document.transcribed_pages).where(
            document.id.in_(ids)
        )
    ).all()
    for document_id, source, pages in rows:
        if source is TextSource.transcribed:
            marks[str(document_id)] = True
        elif pages:
            text = db.scalar(sa.select(document.text).where(document.id == document_id)) or ""
            bounds, start = [], 0
            for index, page in enumerate(text.split(PAGE_BREAK)):
                if index in set(pages):
                    bounds.append((start, start + len(page)))
                start += len(page) + 1
            marks[str(document_id)] = bounds
    return marks


def _is_transcribed(marks: Mapping[str, Any], citation: Mapping[str, Any]) -> bool:
    mark = marks.get(citation["document_id"])
    if mark is True:
        return True
    # half open ranges: a citation that ends where a transcribed page starts is not in it
    return any(citation["start"] < end and citation["end"] > start for start, end in mark or ())


def _variants(
    rows: Sequence[IngestionItem], marks: Mapping[str, Any], order: Mapping[str, int]
) -> tuple[dict[tuple[str, str], dict[str, Variant]], int, int]:
    """The stored rows grouped by (section, key) then by value, how many rows no longer build
    (their section model changed since they were stored), and how many list removals were
    dropped (rule 1)."""
    groups: dict[tuple[str, str], dict[str, Variant]] = {}
    left_out = refused = 0
    built: list[tuple[tuple[int, str, int, int], IngestionItem, items.Item]] = []
    for row in rows:
        if items.removes_a_list(row.section, row.key, row.action):
            refused += 1
            continue
        try:
            item = stored_items.as_item(row)
        except _REFUSED:
            left_out += 1
            continue
        starts = [one["start"] for one in stored_items.as_citations(row)]
        document = str(row.document_id)
        place = (order.get(document, len(order)), document, min(starts, default=0), row.id)
        built.append((place, row, item))
    # The item kept for a value is the first in document order (the order the user selected
    # the documents, then the place in the text), not the first row written.
    built.sort(key=lambda one: one[0])
    for place, row, item in built:
        removal = item.action is items.Action.remove
        variants = groups.setdefault((item.section, item.key), {})
        name = "" if removal else item.value_hash
        variant = variants.get(name)
        if variant is None or (
            variant.item.action is not items.Action.add and item.action is items.Action.add
        ):
            # the same value proposed as `add` and as `replace`: `add` keeps what it does not name
            kept = (variant.citations, variant.flags, variant.place) if variant else ((), (), place)
            variant = Variant(item, list(kept[0]), set(kept[1]), kept[2])
            variants[name] = variant
        for citation in stored_items.as_citations(row):
            if _is_transcribed(marks, citation):
                citation["transcribed"] = True
            variant.citations.append(citation)
        # the stored flag is a hint and the only one a row may carry: `derived`, `conflict` and
        # `transcribed` are set by code, whatever a row says
        if items.FLAG_INSTRUCTION in (row.flags or ()):
            variant.flags.add(items.FLAG_INSTRUCTION)
    for variants in groups.values():
        for variant in variants.values():
            variant.citations.sort(
                key=lambda one: (
                    order.get(one["document_id"], len(order)),
                    one["document_id"],
                    one["start"],
                    one["end"],
                )
            )
    return groups, left_out, refused


def _holds(section_data: Mapping[str, Any], item: items.Item) -> bool:
    """Whether the stored section already holds the item: applying it changes nothing."""
    try:
        return bool(items.apply(item.section, dict(section_data), item) == section_data)
    except items.ProfileConflict:
        return False  # it does not fit today: the user sees it and decides


# --- rule B: one entry under two keys -------------------------------------------------------------


def _groups(
    by_key: Mapping[tuple[str, str], Mapping[str, Variant]],
    section_of: Callable[[str], dict[str, Any] | None],
) -> list[Group]:
    """The items grouped by fact: by key, and for the slots rule B reads, by entry."""
    groups: list[Group] = []
    entries: dict[tuple[str, str], list[tuple[str, list[Variant]]]] = {}
    for (section, key), variants in by_key.items():
        slot = items.SLOTS[section][key.partition(":")[0]]
        valued = sorted((one for name, one in variants.items() if name), key=lambda v: v.place)
        if alike.matched(slot) and valued and section_of(section) is not None:
            entries.setdefault((section, slot.name), []).append((key, valued))
        else:
            groups.append(Group(section, key, valued, variants.get(""), (key,)))
    for (section, name), found in entries.items():
        groups.extend(_entries(items.SLOTS[section][name], found, section_of(section) or {}))
    return sorted(groups, key=lambda one: (one.section, one.key or min(one.natural)))


def _entries(
    slot: items.Slot, found: Sequence[tuple[str, list[Variant]]], current: Mapping[str, Any]
) -> list[Group]:
    """The groups of one slot: the keys of its items joined by rule B, with each other and with
    the entries the profile holds, only where every two keys of a group are one entry (rule B
    is not transitive). A key like two keys that are not one entry joins nothing."""
    nodes: list[tuple[str, list[Any], bool]] = []  # key, its values, an entry of the profile
    index: dict[str, int] = {}
    for entry in current.get(slot.name) or []:
        key = items.entry_key(slot, entry)
        if key not in index:
            index[key] = len(nodes)
            nodes.append((key, [], True))
        nodes[index[key]][1].append(entry)
    members: dict[int, list[Variant]] = {}
    for key, valued in found:
        if key not in index:
            index[key] = len(nodes)
            nodes.append((key, [], False))
        nodes[index[key]][1].extend(one.item.value for one in valued)
        members.setdefault(index[key], []).extend(valued)
    parent = list(range(len(nodes)))
    held = [key if stored else None for key, _, stored in nodes]
    together: dict[int, set[int]] = {at: {at} for at in range(len(nodes))}

    def root(at: int) -> int:
        while parent[at] != at:
            parent[at] = parent[parent[at]]
            at = parent[at]
        return at

    if len(nodes) <= SAME_ENTRY_MAX:
        shapes = [[alike.shape(slot, value) for value in values] for _, values, _ in nodes]

        def same(first: int, second: int) -> bool:
            return any(
                alike.same_entry(slot, one, other)
                for one in shapes[first]
                for other in shapes[second]
            )

        # Pairs that may share a group: one entry, and never two entries of the profile.
        like: list[set[int]] = [set() for _ in nodes]
        for at in sorted(members):
            for other in range(len(nodes)):
                if other == at or (other in members and other < at):
                    continue
                if nodes[at][2] and nodes[other][2]:
                    continue
                if same(at, other):
                    like[at].add(other)
                    like[other].add(at)
        torn = {
            at
            for at, near in enumerate(like)
            if not all(near - {other} <= like[other] for other in near)
        }
        for at in sorted(set(members) - torn):
            for other in sorted(like[at] - torn):
                first, second = root(at), root(other)
                if first == second:
                    continue
                if not all(like[one] >= together[second] for one in together[first]):
                    continue
                parent[second] = first
                together[first] |= together.pop(second)
                held[first] = held[first] or held[second]
    clusters: dict[int, list[int]] = {}
    for at in sorted(members):
        clusters.setdefault(root(at), []).append(at)
    groups = []
    for top, joined in clusters.items():
        variants = sorted((one for at in joined for one in members[at]), key=lambda v: v.place)
        natural = tuple(nodes[at][0] for at in joined)
        key = held[top] or (natural[0] if len(natural) == 1 else None)
        groups.append(Group(slot.section, key, variants, None, natural))
    return groups


# --- rule A: values that agree --------------------------------------------------------------------


def _rekeyed(variant: Variant, key: str) -> Variant:
    if variant.item.key == key:
        return variant
    return dataclasses.replace(variant, item=dataclasses.replace(variant.item, key=key))


def _without_settings(slot: items.Slot, value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    return {name: one for name, one in value.items() if name not in slot.setting_fields}


def _agrees_with(slot: items.Slot, held: Any, variant: Variant) -> bool:
    """Whether the profile holds this value already, in its own wording (rule A with the
    profile first)."""
    if held is None or variant.item.action is not items.Action.add:
        return False
    return bool(alike.combine(slot, [held, variant.item.value], base=True) == held)


def _combined(
    section: str, held: Any, kept: Sequence[Variant], order: Mapping[str, int]
) -> Variant | None:
    """One value for every value kept (rule A), what the profile holds first, or None when
    they disagree. A single value with nothing held is itself."""
    slot = kept[0].item.slot
    if any(one.item.action is not items.Action.add for one in kept):
        return kept[0] if len(kept) == 1 else None
    if len(kept) == 1 and held is None:
        return kept[0]
    values = (
        [held, *(one.item.value for one in kept)]
        if held is not None
        else [one.item.value for one in kept]
    )
    value = alike.combine(slot, values, base=held is not None)
    if value is None:
        return None
    try:
        item = items.build(section, slot.name, items.Action.add, _without_settings(slot, value))
    except items.ItemError:
        return None
    citations = sorted(
        (citation for one in kept for citation in one.citations),
        key=lambda one: (
            order.get(one["document_id"], len(order)),
            one["document_id"],
            one["start"],
            one["end"],
        ),
    )
    flags = set().union(*(one.flags for one in kept))
    return Variant(item, citations, flags, kept[0].place)


def count_of_run(db: Session, user_id: uuid.UUID, run_id: uuid.UUID) -> int:
    """How many proposals belong to this run, whatever was decided on them since."""
    return int(
        db.scalar(
            sa.select(sa.func.count())
            .select_from(Proposal)
            .where(
                Proposal.user_id == user_id,
                Proposal.target == items.TARGET,
                Proposal.kind == items.KIND,
                Proposal.payload["ingestion_run_id"].astext == str(run_id),
            )
        )
        or 0
    )


# --- the merge ------------------------------------------------------------------------------------


def run(db: Session, ingestion_run: IngestionRun, now: datetime) -> int:
    """Turn the run's items into proposals and answer how many were written. The caller holds
    the run's row lock and commits."""
    return merge(db, ingestion_run, now).proposals


def merge(db: Session, ingestion_run: IngestionRun, now: datetime) -> Merged:
    """`run`, with the counts of what was dropped and why."""
    user_id, run_id = ingestion_run.user_id, ingestion_run.id
    steps = reuse.source_steps(db, run_id)
    deleted = _delete_replaced(db, steps)
    superseded = item_proposals.supersede_older(db, user_id, now, keep_run=run_id)
    rows = (
        db.scalars(
            sa.select(IngestionItem)
            .where(
                IngestionItem.user_id == user_id,
                IngestionItem.step_id.in_(steps),
                stored_items.of_stored_text(),
            )
            .order_by(IngestionItem.id)
        ).all()
        if steps
        else []
    )
    marks = _transcribed_pages(db, (row.document_id for row in rows))
    # citations in the order the user selected the documents, then by place in the text
    order = {str(one): index for index, one in enumerate(ingestion_run.document_ids or ())}
    by_key, left_out, refused_removals = _variants(rows, marks, order)

    profile = profile_service.get_profile(db, user_id, now)
    stored: dict[str, dict[str, Any] | None] = {}

    def section_of(name: str) -> dict[str, Any] | None:
        if name not in stored:
            try:
                stored[name] = items.canonical(name, (profile.sections or {}).get(name))
            except ValidationError:
                stored[name] = None  # no longer fits its form: nothing is proposed for it
        return stored[name]

    # Rejecting a conflict rejects each value it showed, not only the first one.
    # An item accepted as proposed is not read: the profile holds it, and a later change is the
    # user's. One accepted with the user's own value (an edit, or the pick of a conflict) turned
    # down the values the documents gave: all but the one the user kept.
    refused: set[tuple[Any, Any, Any]] = set()
    for one in item_proposals.turned_down(db, user_id, limit=DECIDED_MAX):
        refused.update((one.section, one.key, h) for h in one.hashes if h != one.kept)

    def turned_down(group: Group, variant: Variant) -> bool:
        keys = {variant.item.key, *group.natural, *([group.key] if group.key else [])}
        return any((group.section, key, variant.item.value_hash) in refused for key in keys)

    written = conflicts = equal = rejected = removals = 0
    employers: list[tuple[items.Item, list[dict[str, Any]]]] = []
    stated = False
    for group in _groups(by_key, section_of):
        section = group.section
        current = section_of(section)
        if current is None:
            left_out += len(group.variants) + (group.removal is not None)
            continue
        if (section, group.key) == (YEARS_SECTION, YEARS_KEY) and group.variants:
            stated = True
        if group.variants and group.removal is not None:
            removals += 1
        candidates = group.variants or [group.removal]
        slot = candidates[0].item.slot
        held = items.read(section, current, group.key) if group.key else None
        kept: list[Variant] = []
        for variant in candidates:
            placed = _rekeyed(variant, group.key) if group.key else variant
            if _holds(current, placed.item) or _agrees_with(slot, held, variant):
                equal += 1
            elif turned_down(group, variant):
                rejected += 1
            else:
                kept.append(variant)
        if not kept:
            continue
        one = _combined(section, held, kept, order)
        others: list[Variant] = []
        if one is not None:
            key = group.key or one.item.key
            first = _rekeyed(one, key)
            if one is not kept[0] and _holds(current, first.item):
                equal += 1
                continue
            if one is not kept[0] and turned_down(group, one):
                rejected += 1
                continue
        else:
            by_hash = sorted(kept, key=lambda v: v.item.value_hash)
            key = group.key or by_hash[0].item.key
            first = _rekeyed(by_hash[0], key)
            others = [_rekeyed(v, key) for v in by_hash[1 : 1 + item_proposals.CONFLICT_MAX]]
            left_out += len(kept) - 1 - len(others)
        flags = set().union(*(v.flags for v in (kept if one is None else [first])))
        try:
            outcome = item_proposals.propose(
                db,
                user_id,
                first.item,
                now,
                ingestion_run_id=run_id,
                citations=first.citations,
                flags=sorted(flags),
                conflict=[(v.item, v.citations) for v in others] or None,
            )
        except _REFUSED as exc:
            left_out += 1
            log.warning("ingestion merge: an item was left out (%s)", type(exc).__name__)
            continue
        superseded += outcome.superseded  # a pending item at this key it replaced
        if outcome.status == item_proposals.EQUAL_TO_PROFILE:
            equal += 1
        elif outcome.status == item_proposals.REJECTED_BEFORE:
            rejected += 1
        elif outcome.proposal is not None:
            written += 1
            conflicts += bool(others)
            if section == EMPLOYERS_SECTION and key.startswith(f"{EMPLOYERS_SLOT}:") and not others:
                employers.append((first.item, first.citations))

    derived = 0 if stated else _propose_years(db, ingestion_run, now, section_of, employers)
    result = Merged(
        proposals=written + derived,
        conflicts=conflicts,
        equal_to_profile=equal,
        rejected_before=rejected,
        removals_dropped=removals,
        removals_refused=refused_removals,
        left_out=left_out,
        superseded=superseded,
        deleted=deleted,
        derived=bool(derived),
    )
    log.info(
        "ingestion merge: run %s wrote %d proposals (%d conflicts, %d equal to the profile, "
        "%d rejected before, %d list removals refused, %d left out, %d superseded, "
        "%d items of a replaced text deleted)",
        run_id,
        result.proposals,
        conflicts,
        equal,
        rejected,
        refused_removals,
        left_out,
        superseded,
        deleted,
    )
    return result


def _propose_years(
    db: Session,
    ingestion_run: IngestionRun,
    now: datetime,
    section_of: Any,
    employers: Sequence[tuple[items.Item, list[dict[str, Any]]]],
) -> int:
    """Propose the derived `skills.years_experience` when the profile has none: 1 when a
    proposal was written, else 0. The value is this code's, the origin says so, and it names the
    employer items it counted so that the page shows it stale once one of them is rejected or
    edited."""
    skills, experience = section_of(YEARS_SECTION), section_of(EMPLOYERS_SECTION)
    if skills is None or experience is None:
        return 0
    if items.read(YEARS_SECTION, skills, YEARS_KEY) is not None:
        return 0
    used: list[str] = []
    citations: list[dict[str, Any]] = []
    for item, cited in employers:
        if item.action is items.Action.remove or len(used) >= item_proposals.DEPENDS_ON_MAX:
            continue
        try:
            experience = items.apply(EMPLOYERS_SECTION, experience, item)
        except _REFUSED:
            continue  # it does not fit the profile today: not counted
        used.append(item.key)
        citations.extend(cited)
    years = years_of(experience.get(EMPLOYERS_SLOT) or [], ingestion_run.created_at)
    if years is None:
        return 0
    payload = {
        "ingestion_run_id": str(ingestion_run.id),
        "section": YEARS_SECTION,
        "key": YEARS_KEY,
        "action": items.Action.add.value,
        "value": years,
        "citations": citations[: item_proposals.CITATIONS_MAX],
        "depends_on": used,
    }
    try:
        outcome = item_proposals.propose_payload(
            db, ingestion_run.user_id, payload, now, origin=items.Origin.derived
        )
    except _REFUSED as exc:
        log.warning("ingestion merge: the derived years were left out (%s)", type(exc).__name__)
        return 0
    return 1 if outcome.proposal is not None else 0
