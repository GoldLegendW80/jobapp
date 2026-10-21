"""Profile items: one fact proposed for the profile (SPEC-ingestion.md, "Extraction agent" and
"Proposals mapping"; tasks/ingestion/todo.md, "Proposal kinds and targets" and task G5).

An item is `(section, key, action, value)`: an employer with its dates, a school, a language, a
contact detail, a link, a fact block, a skill category, a search list, an answer. This module is
pure (no database, no network, no model call): what an item is, what makes two items the same
fact, how one is applied to a section, and the field type of every path for the citation checker.
`app/ingestion/item_proposals.py` stores items as proposals and applies them to the profile.

    item = items.build("experience", "employers", "add", value)       # raises ItemError
    citations.check_item(item.field_types, item.value, document, excerpts)
    after = items.apply("experience", stored_section, item)           # raises ProfileConflict

**The value is validated by the section models of the profile, and nothing else.** `build` puts
the value alone into its section and validates that with the section's own Pydantic model, so an
item that would not save is refused here with the model's sentence, and what an item holds is the
model's cleaned form (NFC, stripped, an empty optional as null). The hash is computed on that.

**Slots and keys.** The first part of a key is a slot, a field of the section. A slot that holds
one value is its own key (`full_name`, `remote_stance`, `title_include`, `contract_types`). A slot
that holds entries has one key per entry, `<slot>:<identity>`, and the identity is computed by
code from the cleaned value, never typed by the model:

    identity    languages:<name>          links:<address>
    experience  employers:<organisation and year of start, as a slug>
                education:<school>:<year of start, or nothing>
    facts       blocks:<title>
    skills      have:<name>  adjacent:<name>  none:<name>  gates:<name>
    search      boards:<ats>:<org>        never_apply:<hash of the text>
    answers     work_authorization:<country>

A name is folded for its key (NFKC, case fold, invisible characters removed, spaces collapsed), so
"Français" and "FRANÇAIS " are one language. Decisions that differ from the plan's first table,
each with its reason:
- A link is keyed by its address (no scheme, no "www.", the host in lower case, no final slash),
  not by its label: the label is the model's wording, the address is the fact.
- An employer is keyed by its organisation and the year of its start, not by the organisation
  alone: two positions at one company are two facts, and "2021" and "2021-03" for one start
  still share a key and are shown as a conflict. Stated cost: two positions started in the same
  year at one organisation share a key, and two documents that disagree on the year make two
  entries. A school is keyed the same way (the year of its start, not the month).
- The `key` of a stored employer (a slug other modules refer to) is never proposed: a new
  employer takes the identity above as its `key` (with a number added if another employer
  already holds it), and an existing one keeps its own.
An entry of the profile is found by computing the same identity on it, so a key never depends on
the position of an entry. One function computes every key (`entry_key`), and its result is a fixed
point of `parse_key`: for every value the section model accepts, `parse_key` accepts the key and
returns it unchanged, so the entry is found again at the key its item carries. An identity that
comes out empty or longer than IDENTITY_MAX characters (a name of compatibility characters that
expand under NFKC, a very long address) is held as `~` and a hash, and "www.www.example.com" and
"example.com" are one address.

**Actions.** What an item does to the section, for the agent and for a user's edit alike:
- `add`: the key holds the value afterwards, and nothing is erased. A new entry is inserted
  (below); on an existing entry, the fields the value gives replace the stored ones, the fields
  it leaves null keep what is stored (a document that does not state a location does not erase
  it), and words (terms, contract types, lines to leave blank) are joined to the stored ones.
- `replace`: the key holds exactly the value afterwards: null fields clear the stored ones and
  words replace the stored ones. Settings of the stored entry that the value does not name
  (a gate's penalty, a board's switch) are kept. On a missing key it inserts.
- `remove`: nothing is at the key afterwards: the entry with that key is removed, a single
  value is cleared, a list is emptied. On a missing key it changes nothing.
Applying an item twice gives what applying it once gives.

**One entry per item.** An item never changes more than one entry. When several stored entries
share a key (two positions started the same year at one organisation, two titles that differ by
two spaces), no item applies at that key, whatever its action: `apply` raises ProfileConflict
with SHARED_KEY, the proposal stays pending and shows as a conflict, and "accept all" applies
nothing of that section. The user tells the entries apart (or keeps one) and decides again; the
item is then stale unless the entry left at the key is the one it was proposed against. `read`
shows the first entry with the key and `held` answers None, so no reader treats several
entries as one.

**Where a new entry goes.** Existing entries are never reordered. A new employer or school is
inserted newest first: before the first stored entry that is certainly older, after every entry
of the same rank, at the end when none is older. The rank is the end then the start, each read
with `period_key` (a year alone ranks below the months of its year, so it lists after them), an
employer without an end (present) above every dated end, a school without an end read at its
start, and a school with no date at all last. Every other list appends.

**Never proposable.** A setting is the user's choice about how the app behaves, not a claim a
document supports: a gate's `penalty`, a board's `enabled` and `extra_exclude`, every
`advanced_regex`, `search.min_fit`, `search.days`, `search.browser_sources`,
`search.sweep_schedule`, `search.documents`, `search.company_types` and `search.compensation`
(wishes and pay a document does not state for the person), the whole `preferences` section (how
the app behaves, and the wizard's dates), and the apply fields of `answers` (EEO, salary stance
and ranges, consents, preferred name and the like). They are typed `setting` in `FIELD_TYPES`,
and `build` refuses them for an agent whatever is cited. `search.seniority` (a CV shows the
levels held) and `search.industries` (a CV names the sectors worked in) may be proposed. The
photo is not a section and has no key. `skills.years_experience` is computed by the merge,
never read by the agent: only `Origin.derived` may build it. A user's edit (`Origin.user`) is
validated by the section model alone and may set a setting inside the entry it edits.

**What is stored of a hostile value.** A value comes from a model that read a hostile document.
Before the section model reads it: at most `VALUE_NODES_MAX` parts, `VALUE_CHARS_MAX` characters
and `VALUE_DEPTH_MAX` levels; a key of the value must be a field of its type; and every invisible
character is removed from every string (soft hyphen, zero width space, bidi controls and
overrides, tag characters, variation selectors, the grapheme joiner), since the citation checker
compares without them and a reader does not see them. Two exceptions keep honest spellings: a
zero width joiner or non-joiner between two letters that are not ASCII (Persian, Indic scripts,
emoji sequences) and an emoji presentation selector after such a character. A user's edit is not
changed this way: it goes through the section model alone. Nothing here logs.
"""

from __future__ import annotations

import hashlib
import json
import re
import types
import typing
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from app.ingestion.citations import LOOKS_LIKE_INSTRUCTION, FieldType
from app.profile import schemas

__all__ = [
    "FIELD_TYPES",
    "FLAGS",
    "KIND",
    "SLOTS",
    "TARGET",
    "Action",
    "Item",
    "ItemError",
    "Origin",
    "ProfileConflict",
    "Slot",
    "SlotKind",
    "apply",
    "build",
    "canonical",
    "clean_text",
    "entry_key",
    "from_payload",
    "held",
    "held_hash",
    "is_locked",
    "parse_key",
    "read",
    "restore",
    "section_paths",
    "value_hash",
]

KIND = "profile_item"
TARGET = "profile"

VALUE_NODES_MAX = 2_000  # parts of one value, containers included
VALUE_CHARS_MAX = 250_000  # characters of text in one value (the fact base holds 200,000 bytes)
VALUE_DEPTH_MAX = 5  # an entry, a list inside it, an object inside that
FIELD_NAME_MAX = 64
KEY_MAX = 400

FLAG_INSTRUCTION = LOOKS_LIKE_INSTRUCTION
FLAG_DERIVED = "derived"
FLAG_TRANSCRIBED = "transcribed"
FLAG_CONFLICT = "conflict"
FLAGS = (FLAG_INSTRUCTION, FLAG_DERIVED, FLAG_TRANSCRIBED, FLAG_CONFLICT)


class Action(StrEnum):
    add = "add"
    replace = "replace"
    remove = "remove"


class Origin(StrEnum):
    """Who wrote the value: the extraction agent, the merge (the computed years of experience)
    or the user editing a proposed item."""

    agent = "agent"
    derived = "derived"
    user = "user"


class SlotKind(StrEnum):
    scalar = "scalar"  # one value: a line, an address, a choice, a number
    entries = "entries"  # a list of objects, one key per entry
    lines = "lines"  # a list of texts, one key per text
    terms = "terms"  # one list of terms with its advanced switch, as a whole
    words = "words"  # one list of words or choices, as a whole
    setting = "setting"  # never proposable


# --- refusals -------------------------------------------------------------------------------------

UNKNOWN_SECTION = "unknown_section"
UNKNOWN_KEY = "unknown_key"
UNKNOWN_ACTION = "unknown_action"
SETTING = "setting"
UNKNOWN_FIELD = "unknown_field"
VALUE_TOO_LARGE = "value_too_large"
VALUE_EMPTY = "value_empty"
VALUE_NOT_EXPECTED = "value_not_expected"
INVALID_VALUE = "invalid_value"

REASONS: Mapping[str, str] = {
    UNKNOWN_SECTION: "this is not a section of the profile",
    UNKNOWN_KEY: "this is not a key of this section",
    UNKNOWN_ACTION: "the action is add, replace or remove",
    SETTING: "this is a setting of the app, which only the user changes",
    UNKNOWN_FIELD: "the value holds a field that this kind of item does not have",
    VALUE_TOO_LARGE: "the value is too large or too deeply nested",
    VALUE_EMPTY: "the value is empty",
    VALUE_NOT_EXPECTED: "an item that removes carries no value",
}
ASCII_DATE = "a date is YYYY or YYYY-MM"
SHARED_KEY = (
    "several entries of the profile share this key, so the item cannot say which one it is "
    "about: tell them apart or keep one, then decide again"
)
ADVANCED_LIST = (
    "this list is written as regular expressions: its terms are edited by hand, not by an item"
)


class ItemError(ValueError):
    """An item that is refused before anything is stored. `rule` is a code, `index` says what
    broke it (`key`, `value`, `value.start`, `value.terms[3]`: field names and positions, never
    the model's text) and `reason` is one sentence: a constant, or for `invalid_value` the
    sentence of the section model, which never quotes the value of a proposable field."""

    def __init__(self, rule: str, index: str = "value", reason: str | None = None) -> None:
        self.rule = rule
        self.index = index
        self.reason = reason if reason is not None else REASONS[rule]
        super().__init__(f"{rule} at {index}")


class ProfileConflict(ValueError):  # noqa: N818, named for what it says to the user
    """The section as it is now cannot take the item: a list at its cap, a name that another
    entry already holds, a list written as regular expressions. `message` is the section
    model's sentence with the path of the field, never a value."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


# --- field types ----------------------------------------------------------------------------------

_T = FieldType


def _terms(prefix: str, of_terms: FieldType = _T.TERM) -> dict[str, FieldType]:
    return {f"{prefix}.advanced_regex": _T.SETTING, f"{prefix}.terms[]": of_terms}


# Every path of every section, in the syntax of the citation checker (keys joined by full stops,
# `[]` for the items of a list), rooted at the section. `term` and `enum` only where the field is
# one: the checker never reads such a field. A link's label is `text`, not `name`: a label is a
# caption ("Portfolio") that the document seldom writes.
FIELD_TYPES: Mapping[str, FieldType] = types.MappingProxyType(
    {
        "identity.full_name": _T.NAME,
        "identity.headline": _T.TEXT,
        "identity.email": _T.EMAIL,
        "identity.phone": _T.PHONE,
        "identity.city": _T.TEXT,
        "identity.country": _T.TEXT,
        "identity.linkedin_url": _T.URL,
        "identity.website": _T.URL,
        "identity.links[].label": _T.TEXT,
        "identity.links[].url": _T.URL,
        "identity.languages[].name": _T.NAME,
        "identity.languages[].level": _T.TEXT,
        "experience.employers[].key": _T.SETTING,
        "experience.employers[].organisation": _T.NAME,
        "experience.employers[].title": _T.TEXT,
        "experience.employers[].start": _T.DATE,
        "experience.employers[].end": _T.DATE,
        "experience.employers[].location": _T.TEXT,
        "experience.employers[].contract": _T.ENUM,
        "experience.education[].school": _T.NAME,
        "experience.education[].degree": _T.TEXT,
        "experience.education[].start": _T.DATE,
        "experience.education[].end": _T.DATE,
        "experience.education[].location": _T.TEXT,
        "experience.education[].details": _T.TEXT,
        "facts.blocks[].title": _T.TEXT,
        "facts.blocks[].body": _T.TEXT,
        "skills.years_experience": _T.SETTING,
        **{
            path: kind
            for bucket in ("have", "adjacent", "none")
            for path, kind in {
                f"skills.{bucket}[].name": _T.TEXT,
                **_terms(f"skills.{bucket}[]"),
            }.items()
        },
        "skills.gates[].name": _T.TEXT,
        **_terms("skills.gates[]"),
        "skills.gates[].penalty": _T.SETTING,
        **_terms("search.title_include"),
        **_terms("search.title_exclude"),
        **_terms("search.location"),
        "search.contract_types[]": _T.ENUM,
        "search.remote_stance": _T.ENUM,
        "search.min_fit": _T.SETTING,
        "search.days": _T.SETTING,
        "search.boards[].company": _T.NAME,
        "search.boards[].ats": _T.ENUM,
        "search.boards[].org": _T.NAME,
        "search.boards[].enabled": _T.SETTING,
        **_terms("search.boards[].extra_exclude", _T.SETTING),
        "search.never_apply[]": _T.TEXT,
        # The browser sources (browser B8) are the person's own choice of sites and words to search
        # with, never a claim a document supports: ingestion can never write one.
        "search.browser_sources[].kind": _T.SETTING,
        "search.browser_sources[].query": _T.SETTING,
        "search.browser_sources[].location": _T.SETTING,
        "search.browser_sources[].enabled": _T.SETTING,
        "search.browser_sources[].accepted_at": _T.SETTING,
        "search.browser_sources[].text_version": _T.SETTING,
        "search.sweep_schedule.enabled": _T.SETTING,
        "search.sweep_schedule.days[]": _T.SETTING,
        "search.sweep_schedule.times[]": _T.SETTING,
        "search.sweep_schedule.timezone": _T.SETTING,
        "search.documents.auto": _T.SETTING,
        "search.documents.min_fit": _T.SETTING,
        "search.documents.daily_ceiling": _T.SETTING,
        # Opportunities O4: the levels and sectors a CV shows may be proposed; the company
        # types sought and the pay are the person's wishes, never read from a document.
        "search.seniority[]": _T.ENUM,
        **_terms("search.industries"),
        "search.company_types[]": _T.SETTING,
        **{
            f"search.compensation.{pay}.{part}": _T.SETTING
            for pay in ("salary", "daily_rate")
            for part in ("min", "max", "currency")
        },
        "preferences.onboarding.completed_at": _T.SETTING,
        "preferences.onboarding.dismissed_at": _T.SETTING,
        "preferences.onboarding.steps_passed[]": _T.SETTING,
        "preferences.documents_language": _T.SETTING,
        "preferences.documents_tone": _T.SETTING,
        "answers.work_authorization[].country": _T.TEXT,
        "answers.work_authorization[].answer": _T.TEXT,
        "answers.work_authorization[].needs_sponsorship": _T.ENUM,
        "answers.notice_period": _T.TEXT,
        "answers.earliest_start": _T.TEXT,
        "answers.salary_policy": _T.TEXT,
        "answers.relocation": _T.TEXT,
        "answers.travel": _T.TEXT,
        "answers.motivation": _T.TEXT,
        "answers.heard_about": _T.TEXT,
        "answers.leave_blank[]": _T.TEXT,
        # The apply module's choices (task A3): what a person decides about how applications
        # are answered, not a claim a document supports, so none is ever proposed.
        "answers.preferred_name": _T.SETTING,
        "answers.pronouns": _T.SETTING,
        "answers.years_experience_stated": _T.SETTING,
        "answers.highest_degree": _T.SETTING,
        "answers.highest_degree_year": _T.SETTING,
        "answers.willing_onsite": _T.SETTING,
        "answers.willing_hybrid": _T.SETTING,
        "answers.previously_applied_default": _T.SETTING,
        "answers.background_check_consent": _T.SETTING,
        "answers.reference_check_consent": _T.SETTING,
        "answers.eeo_policy": _T.SETTING,
        "answers.eeo_answers[].topic": _T.SETTING,
        "answers.eeo_answers[].answer": _T.SETTING,
        "answers.salary_stance": _T.SETTING,
        "answers.salary_ranges[].currency": _T.SETTING,
        "answers.salary_ranges[].minimum": _T.SETTING,
        "answers.salary_ranges[].maximum": _T.SETTING,
        "answers.salary_ranges[].period": _T.SETTING,
    }
)


def _model_paths(annotation: Any, prefix: str) -> Iterator[str]:
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        yield from _model_paths(typing.get_args(annotation)[0], prefix)
    elif origin in (typing.Union, types.UnionType):
        seen: set[str] = set()
        for option in typing.get_args(annotation):
            if option is not type(None):
                for path in _model_paths(option, prefix):
                    if path not in seen:
                        seen.add(path)
                        yield path
    elif origin is list:
        yield from _model_paths(typing.get_args(annotation)[0], f"{prefix}[]")
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        for name, field in annotation.model_fields.items():
            yield from _model_paths(field.annotation, f"{prefix}.{name}")
    else:
        yield prefix


def section_paths() -> list[str]:
    """Every path of every section model, read from the models themselves: a test compares it
    with `FIELD_TYPES`, so a field added to a section without a type fails the build."""
    return [
        path for name, model in schemas.SECTION_MODELS.items() for path in _model_paths(model, name)
    ]


# --- slots ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    """One field of a section, as items see it."""

    section: str
    name: str
    kind: SlotKind
    locked: bool = False  # a locked fact of SPEC-profile: accepted one by one, never in bulk
    derived: bool = False  # built by the merge only (and edited by the user)

    @property
    def keyed(self) -> bool:
        """True when the slot holds entries, each with a key of its own."""
        return self.kind in (SlotKind.entries, SlotKind.lines)

    @property
    def path(self) -> str:
        return f"{self.section}.{self.name}"

    @property
    def field_types(self) -> dict[str, FieldType]:
        """The type of each path of a value of this slot, for `citations.check_item`."""
        return _SLOT_TYPES[self.path]

    @property
    def setting_fields(self) -> frozenset[str]:
        """The fields of an entry (or of a term list) that are settings as a whole."""
        return _SLOT_SETTINGS[self.path]


def _slots(section: str, locked: bool = False, **kinds: SlotKind) -> dict[str, Slot]:
    return {name: Slot(section, name, kind, locked=locked) for name, kind in kinds.items()}


_K = SlotKind
SLOTS: Mapping[str, Mapping[str, Slot]] = types.MappingProxyType(
    {
        "identity": {
            **_slots(
                "identity",
                locked=True,
                full_name=_K.scalar,
                email=_K.scalar,
                phone=_K.scalar,
                city=_K.scalar,
                country=_K.scalar,
                linkedin_url=_K.scalar,
                website=_K.scalar,
                links=_K.entries,
                languages=_K.entries,
            ),
            **_slots("identity", headline=_K.scalar),
        },
        "experience": _slots("experience", locked=True, employers=_K.entries, education=_K.entries),
        "facts": _slots("facts", blocks=_K.entries),
        "skills": {
            "years_experience": Slot("skills", "years_experience", _K.scalar, derived=True),
            **_slots(
                "skills", have=_K.entries, adjacent=_K.entries, none=_K.entries, gates=_K.entries
            ),
        },
        "search": _slots(
            "search",
            title_include=_K.terms,
            title_exclude=_K.terms,
            location=_K.terms,
            contract_types=_K.words,
            remote_stance=_K.scalar,
            min_fit=_K.setting,
            days=_K.setting,
            boards=_K.entries,
            never_apply=_K.lines,
            browser_sources=_K.setting,
            sweep_schedule=_K.setting,
            documents=_K.setting,
            seniority=_K.words,
            industries=_K.terms,
            company_types=_K.setting,
            compensation=_K.setting,
        ),
        "answers": _slots(
            "answers",
            work_authorization=_K.entries,
            notice_period=_K.scalar,
            earliest_start=_K.scalar,
            salary_policy=_K.scalar,
            relocation=_K.scalar,
            travel=_K.scalar,
            motivation=_K.scalar,
            heard_about=_K.scalar,
            leave_blank=_K.words,
            preferred_name=_K.setting,
            pronouns=_K.setting,
            years_experience_stated=_K.setting,
            highest_degree=_K.setting,
            highest_degree_year=_K.setting,
            willing_onsite=_K.setting,
            willing_hybrid=_K.setting,
            previously_applied_default=_K.setting,
            background_check_consent=_K.setting,
            reference_check_consent=_K.setting,
            eeo_policy=_K.setting,
            eeo_answers=_K.setting,
            salary_stance=_K.setting,
            salary_ranges=_K.setting,
        ),
        "preferences": _slots(
            "preferences",
            onboarding=_K.setting,
            documents_language=_K.setting,
            documents_tone=_K.setting,
        ),
    }
)


def _relative(slot: Slot) -> dict[str, FieldType]:
    if slot.kind in (SlotKind.scalar, SlotKind.setting):
        prefix = slot.path
    elif slot.kind is SlotKind.terms:
        prefix = f"{slot.path}."
    elif slot.kind is SlotKind.entries:
        prefix = f"{slot.path}[]."
    elif slot.kind is SlotKind.words:
        return {"[]": FIELD_TYPES[f"{slot.path}[]"]}
    else:  # lines: each text is an item of its own
        return {"": FIELD_TYPES[f"{slot.path}[]"]}
    return {
        path[len(prefix) :]: kind
        for path, kind in FIELD_TYPES.items()
        if path == prefix or (prefix.endswith(".") and path.startswith(prefix))
    }


def _settings(relative: Mapping[str, FieldType]) -> frozenset[str]:
    heads: dict[str, bool] = {}
    for path, kind in relative.items():
        head = re.split(r"[.\[]", path, maxsplit=1)[0]
        heads[head] = heads.get(head, True) and kind is FieldType.SETTING
    return frozenset(head for head, all_settings in heads.items() if head and all_settings)


_SLOT_TYPES = {
    slot.path: _relative(slot) for section in SLOTS.values() for slot in section.values()
}
_SLOT_SETTINGS = {path: _settings(relative) for path, relative in _SLOT_TYPES.items()}


def is_locked(section: str, key: str) -> bool:
    """Whether the key is a locked fact (identity contact fields, languages and links, and all of
    experience): such an item is accepted on its own, never in bulk. An unknown key is locked."""
    slot = SLOTS.get(section, {}).get(key.split(":", 1)[0] if isinstance(key, str) else "")
    return slot is None or slot.locked


# --- text -----------------------------------------------------------------------------------------

# Format characters that a reader sees (the Arabic number signs, the end of ayah and their like):
# the same list as the citation checker's, which reads them as breaks and never removes them.
_FORMAT_SIGNS = frozenset("؀؁؂؃؄؅۝܏࢐࢑࣢᠎\U000110bd\U000110cd")
_JOINERS = "‌‍"
_PRESENTATION = "︎️"


def is_invisible(char: str) -> bool:
    """What the citation checker's normalisation removes: format characters without a glyph,
    variation selectors and the grapheme joiner."""
    return (
        (unicodedata.category(char) == "Cf" and char not in _FORMAT_SIGNS)
        or "︀" <= char <= "️"
        or "\U000e0100" <= char <= "\U000e01ef"
        or char == "͏"
    )


def clean_text(text: str) -> str:
    """`text` without its invisible characters. Kept: a zero width joiner or non-joiner between
    two characters that are not ASCII, and an emoji presentation selector after one."""
    if text.isascii():
        return text
    kept: list[str] = []
    for position, char in enumerate(text):
        if not is_invisible(char):
            kept.append(char)
            continue
        after_letter = bool(kept) and not kept[-1].isascii()
        if char in _PRESENTATION and after_letter:
            kept.append(char)
        elif char in _JOINERS and after_letter:
            following = text[position + 1 : position + 2]
            if following and not following.isascii() and not is_invisible(following):
                kept.append(char)
    return "".join(kept)


def fold(text: str) -> str:
    """A name as its key reads it: NFKC, no invisible character, case fold, single spaces."""
    plain = "".join(char for char in unicodedata.normalize("NFKC", text) if not is_invisible(char))
    return " ".join(plain.casefold().split())


def _digest(text: str, length: int) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:length]


_SLUG_BODY_MAX = 30


def slug(text: str) -> str:
    """A name as a slug of the profile (`schemas.SLUG_PATTERN`), at most 30 characters. A name
    that loses letters on the way (another script, or too long) carries six characters of its
    hash, so two names never share a slug by accident."""
    folded = fold(text)
    plain = "".join(
        char for char in unicodedata.normalize("NFKD", folded) if not unicodedata.combining(char)
    )
    body = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")
    lossy = any(char.isalnum() and not ("a" <= char <= "z" or "0" <= char <= "9") for char in plain)
    if not body or lossy or len(body) > _SLUG_BODY_MAX:
        head = body[: _SLUG_BODY_MAX - 7].strip("-")
        body = f"{head}-{_digest(folded, 6)}" if head else f"x-{_digest(folded, 6)}"
    return body


def _year(period: Any) -> str:
    return "" if not period else str(schemas.parse_period(period)[0])


def _address(url: str) -> str:
    rest = re.sub(r"^https?://", "", url.strip(), flags=re.IGNORECASE)
    cut = re.search(r"[/?#]", rest)
    host, tail = (rest[: cut.start()], rest[cut.start() :]) if cut else (rest, "")
    return (re.sub(r"^(?:www\.)+", "", host.lower()) + tail).rstrip("/")


IDENTITY_MAX = 300  # characters of the identity part of a key: a longer one is held as its hash
_SETTLE_PASSES = 4


def _settled(text: str, normal: Callable[[str], str]) -> str:
    """`text` in the one form a key holds it: `normal` applied until it changes nothing more.
    What comes out empty, longer than IDENTITY_MAX or still moving after a few passes is held as
    `~` and a hash instead. So every value the section model accepts has a key of bounded
    length, and the form is a fixed point: `_settled(_settled(x, f), f) == _settled(x, f)`,
    which is what lets `parse_key` read a key that `entry_key` computed and return that key."""
    settled = normal(text)
    for _ in range(_SETTLE_PASSES):
        again = normal(settled)
        if again == settled:
            if settled and len(settled) <= IDENTITY_MAX:
                return settled
            break
        settled = again
    return f"~{_digest(settled, 32)}"


def _name(text: str) -> str:
    return _settled(text, fold)


def _identity(slot: Slot, value: Any) -> str:
    """The identity part of the key of a cleaned entry of this slot, in its normal form."""
    if slot.kind is SlotKind.lines:
        return _digest(fold(value), 16)
    where = (slot.section, slot.name)
    if where == ("identity", "links"):
        return _settled(value["url"], _address)
    if where == ("experience", "employers"):
        return f"{slug(value['organisation'])}-{_year(value['start'])}"
    if where == ("experience", "education"):
        return f"{_name(value['school'])}:{_year(value.get('start'))}"
    if where == ("search", "boards"):
        return _name(f"{value['ats']}:{value['org'].lower()}")
    if where == ("facts", "blocks"):
        return _name(value["title"])
    if where == ("answers", "work_authorization"):
        return _name(value["country"])
    return _name(value["name"])  # languages, skill categories, gates


def _typed_identity(slot: Slot, identity: str) -> str:
    """The identity part of a key somebody wrote, in the normal form `_identity` gives: the same
    functions, so a key computed by `entry_key` comes back unchanged. Empty when it names
    nothing."""
    if slot.kind is SlotKind.lines:
        return identity.strip()
    if slot.name == "education":
        school, _, year = identity.rpartition(":")
        return f"{_name(school)}:{year.strip()}" if fold(school) else ""
    if slot.name == "links":
        return _settled(identity, _address) if _address(identity) else ""
    return _name(identity) if fold(identity) else ""


def entry_key(slot: Slot, value: Any) -> str:
    """The key of a cleaned value of this slot: the slot's name, or for an entry the name and
    its identity. The same function reads the entries of a stored section, and it is the only
    place a key is computed. For every value the section model accepts, `parse_key` accepts
    this key and returns it unchanged, so the entry is found again at the key an item carries."""
    return f"{slot.name}:{_identity(slot, value)}" if slot.keyed else slot.name


def parse_key(section: str, key: Any) -> tuple[Slot, str]:
    """The slot of a key of this section and the key in its normal form. Raises ItemError for a
    section, a slot or a shape that does not exist, and for a setting."""
    if section not in SLOTS:
        raise ItemError(UNKNOWN_SECTION, "section")
    if not isinstance(key, str) or not key or len(key) > KEY_MAX:
        raise ItemError(UNKNOWN_KEY, "key")
    name, colon, identity = key.partition(":")
    slot = SLOTS[section].get(name)
    if slot is None:
        raise ItemError(UNKNOWN_KEY, "key")
    if slot.kind is SlotKind.setting:
        raise ItemError(SETTING, "key")
    if not slot.keyed:
        if colon:
            raise ItemError(UNKNOWN_KEY, "key")
        return slot, name
    identity = _typed_identity(slot, identity)
    if not identity:
        raise ItemError(UNKNOWN_KEY, "key")
    return slot, f"{name}:{identity}"


# --- the value ------------------------------------------------------------------------------------


def _bounded(value: Any, relative: Mapping[str, FieldType] | None) -> Any:
    """A copy of `value` within the size limits. With `relative`, the types of an agent's value:
    a field that is not one of them or that is a setting is refused, and every string loses its
    invisible characters."""
    containers = (
        {path[:cut] for path in relative for cut, char in enumerate(path) if char in ".[" and cut}
        if relative is not None
        else set()
    )
    budget = {"nodes": 0, "chars": 0}
    setting: list[str] = []
    unknown: list[str] = []

    def walk(node: Any, path: str, index: str, depth: int) -> Any:
        budget["nodes"] += 1
        if depth > VALUE_DEPTH_MAX or budget["nodes"] > VALUE_NODES_MAX:
            raise ItemError(VALUE_TOO_LARGE)
        if isinstance(node, dict):
            copy: dict[str, Any] = {}
            for name, child in node.items():
                if not isinstance(name, str) or len(name) > FIELD_NAME_MAX:
                    raise ItemError(UNKNOWN_FIELD, index)
                below = f"{path}.{name}" if path else name
                if relative is not None and below not in relative and below not in containers:
                    unknown.append(index)
                    continue
                copy[name] = walk(child, below, f"{index}.{name}", depth + 1)
            return copy
        if isinstance(node, list):
            below = f"{path}[]"
            if relative is not None and below not in relative and below not in containers:
                unknown.append(index)
                return []
            return [
                walk(child, below, f"{index}[{position}]", depth + 1)
                for position, child in enumerate(node)
            ]
        if relative is not None:
            if path not in relative:
                unknown.append(index)
            elif relative[path] is FieldType.SETTING:
                setting.append(index)
        if isinstance(node, str):
            budget["chars"] += len(node)
            if budget["chars"] > VALUE_CHARS_MAX:
                raise ItemError(VALUE_TOO_LARGE)
            return clean_text(node) if relative is not None else node
        if node is None or isinstance(node, bool | int | float):
            return node
        raise ItemError(INVALID_VALUE, index, "a value is text, a number, a list or an object")

    copy = walk(value, "", "value", 1)
    if setting:
        raise ItemError(SETTING, setting[0])
    if unknown:
        raise ItemError(UNKNOWN_FIELD, unknown[0])
    return copy


_PLACEHOLDER_KEY = "x"
_ASCII_PERIOD = re.compile(r"[0-9]{4}(?:-[0-9]{2})?")


def _index(loc: tuple[Any, ...], skip: int) -> str:
    index = "value"
    for part in loc[skip:]:
        index += f"[{part}]" if isinstance(part, int) else f".{part}"
    return index


def _validated(slot: Slot, value: Any) -> Any:
    """`value` as the section model stores it, validated alone in its section."""
    skip = 1
    if slot.kind is SlotKind.entries:
        entry = dict(value) if isinstance(value, dict) else value
        if slot.name == "employers" and isinstance(entry, dict):
            entry.setdefault("key", _PLACEHOLDER_KEY)
        candidate, skip = {slot.name: [entry]}, 2
    elif slot.kind is SlotKind.lines:
        candidate, skip = {slot.name: [value]}, 2
    else:
        candidate = {slot.name: value}
    try:
        model = schemas.SECTION_MODELS[slot.section].model_validate(candidate)
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_url=False)[0]
        message = str(first.get("msg", "invalid value")).removeprefix("Value error, ")
        raise ItemError(INVALID_VALUE, _index(tuple(first.get("loc", ())), skip), message) from None
    stored = model.model_dump(mode="json")[slot.name]
    return stored[0] if slot.keyed else stored


def canonical(section: str, data: Mapping[str, Any] | None) -> dict[str, Any]:
    """A stored section in its canonical form (a section never saved is its empty form).
    Raises pydantic.ValidationError when what is stored no longer fits the model."""
    return schemas.validate_section(section, dict(data) if data is not None else {})


def _sorted_words(value: Any) -> Any:
    if isinstance(value, dict):
        return {name: _sorted_words(child) for name, child in value.items()}
    if isinstance(value, list):
        if all(isinstance(child, str) for child in value):
            return sorted(value, key=lambda word: (word.casefold(), word))
        return [_sorted_words(child) for child in value]
    return value


def value_hash(value: Any) -> str:
    """The SHA-256 of a cleaned value, in hexadecimal. A list of words counts as a set: the
    order the model typed them in does not make another value."""
    text = json.dumps(
        _sorted_words(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


@dataclass(frozen=True)
class Item:
    """One fact for the profile, cleaned: `value` is what the section model stores (null for a
    removal), `key` its stable key, `value_hash` the hash of the cleaned value."""

    section: str
    key: str
    action: Action
    value: Any
    value_hash: str

    @property
    def slot(self) -> Slot:
        return SLOTS[self.section][self.key.partition(":")[0]]

    @property
    def locked(self) -> bool:
        return self.slot.locked

    @property
    def field_types(self) -> dict[str, FieldType]:
        """What to give `citations.check_item` with `value`."""
        return self.slot.field_types


def build(
    section: Any, key: Any, action: Any, value: Any = None, *, origin: Origin = Origin.agent
) -> Item:
    """The item `(section, key, action, value)`, cleaned and keyed. Raises ItemError.

    `key` names the slot: `employers`, or a whole key such as `employers:acme-2021`. For an add or
    a replace the identity part is not read: the key is computed from the cleaned value, so the
    model's wording of a key changes nothing. A removal carries no value and needs the whole key
    (as `entry_key` gives it for the stored entry).
    """
    if not isinstance(section, str) or section not in SLOTS:
        raise ItemError(UNKNOWN_SECTION, "section")
    try:
        action = Action(action)
    except ValueError:
        raise ItemError(UNKNOWN_ACTION, "action") from None
    if not isinstance(key, str) or not key or len(key) > KEY_MAX:
        raise ItemError(UNKNOWN_KEY, "key")
    slot = SLOTS[section].get(key.partition(":")[0])
    if slot is None:
        raise ItemError(UNKNOWN_KEY, "key")
    if slot.kind is SlotKind.setting or (slot.derived and origin is Origin.agent):
        raise ItemError(SETTING, "key")
    if origin is Origin.derived and not slot.derived:
        raise ItemError(UNKNOWN_KEY, "key")
    if action is Action.remove:
        if value is not None:
            raise ItemError(VALUE_NOT_EXPECTED)
        _, whole = parse_key(section, key)
        return Item(section, whole, action, None, value_hash(None))
    if value is None:
        raise ItemError(VALUE_EMPTY)
    by_user = origin is Origin.user
    relative = slot.field_types
    raw = _bounded(value, None if by_user or slot.derived else relative)
    cleaned = _validated(slot, raw)
    if cleaned is None or cleaned == "":
        raise ItemError(VALUE_EMPTY)
    if isinstance(cleaned, dict):
        given = raw if isinstance(raw, dict) else {}
        cleaned = {
            name: child
            for name, child in cleaned.items()
            if name not in slot.setting_fields or name in given
        }
        if not by_user:
            for name, kind in relative.items():
                period = cleaned.get(name)
                if kind is FieldType.DATE and period and not _ASCII_PERIOD.fullmatch(period):
                    raise ItemError(INVALID_VALUE, f"value.{name}", ASCII_DATE)
    whole = entry_key(slot, cleaned)
    if parse_key(section, whole)[1] != whole:  # never, by construction: see `_settled`
        raise ItemError(INVALID_VALUE)
    return Item(section, whole, action, cleaned, value_hash(cleaned))


def from_payload(payload: Mapping[str, Any]) -> Item:
    """The item a stored proposal holds, validated again by today's section model. Raises
    ItemError when it no longer fits (the proposal is then invalid). The key is the stored one:
    it is where the item applies, whatever the value says now."""
    section, key = payload.get("section"), payload.get("key")
    flags = payload.get("flags")
    derived = isinstance(flags, list) and FLAG_DERIVED in flags
    item = build(
        section,
        key,
        payload.get("action"),
        payload.get("value"),
        origin=Origin.derived if derived else Origin.agent,
    )
    _, whole = parse_key(section, key)
    return Item(item.section, whole, item.action, item.value, item.value_hash)


# --- reading a section ----------------------------------------------------------------------------


def _matches(slot: Slot, entries: list[Any], key: str) -> list[int]:
    return [position for position, entry in enumerate(entries) if entry_key(slot, entry) == key]


def read(section: str, data: Mapping[str, Any] | None, key: str) -> Any:
    """What the section holds at this key, in canonical form: the value of a field, the first
    entry with that key, or None when there is nothing. `data` is a canonical section."""
    slot, key = parse_key(section, key)
    stored = (data or {}).get(slot.name)
    if not slot.keyed:
        if slot.kind is SlotKind.terms:
            return stored if stored and stored.get("terms") else None
        return stored if stored not in (None, [], "") else None
    entries = stored or []
    found = _matches(slot, entries, key)
    return entries[found[0]] if found else None


# --- applying -------------------------------------------------------------------------------------

_OPEN = (10_000, 0)  # an employer without an end: present, above every dated end


def _rank(slot: Slot, entry: Mapping[str, Any]) -> tuple[tuple[int, int], tuple[int, int]] | None:
    start = schemas.period_key(entry["start"]) if entry.get("start") else None
    if entry.get("end"):
        end = schemas.period_key(entry["end"])
    else:
        end = _OPEN if slot.name == "employers" else start
    if end is None:
        return None  # a school without any date
    return end, start if start is not None else end


def _insert(slot: Slot, entries: list[Any], entry: dict[str, Any]) -> None:
    if slot.section != "experience":
        entries.append(entry)
        return
    rank = _rank(slot, entry)
    if rank is not None:
        for position, stored in enumerate(entries):
            other = _rank(slot, stored)
            if other is None or other < rank:
                entries.insert(position, entry)
                return
    entries.append(entry)


def _join(stored: list[Any], given: list[Any]) -> list[Any]:
    seen = {word.casefold() if isinstance(word, str) else word for word in stored}
    joined = list(stored)
    for word in given:
        folded = word.casefold() if isinstance(word, str) else word
        if folded not in seen:
            seen.add(folded)
            joined.append(word)
    return joined


def _same_words(first: list[Any], second: list[Any]) -> bool:
    return _sorted_words(first) == _sorted_words(second)


def _with_terms(stored: Mapping[str, Any], value: Mapping[str, Any], exact: bool) -> list[Any]:
    """The terms of a term list or of a category after the item. A list that the user wrote as
    regular expressions is never changed by an item that does not say so itself."""
    before = list(stored.get("terms") or [])
    after = list(value["terms"]) if exact else _join(before, value["terms"])
    if stored.get("advanced_regex") and "advanced_regex" not in value:
        if not _same_words(before, after):
            raise ProfileConflict(ADVANCED_LIST)
        return before
    return after


def _merged(slot: Slot, stored: Mapping[str, Any], value: Mapping[str, Any], exact: bool) -> Any:
    if exact:
        entry = {name: stored[name] for name in slot.setting_fields if name in stored}
        entry.update(value)
    else:
        entry = dict(stored)
        entry.update({name: child for name, child in value.items() if child is not None})
    if "terms" in value:
        entry["terms"] = _with_terms(stored, value, exact)
    return entry


def _unused_key(entries: list[Any], wanted: str) -> str:
    taken = {entry.get("key") for entry in entries}
    key, number = wanted, 1
    while key in taken:
        number += 1
        key = f"{wanted}-{number}"
    return key


def apply(
    section: str, data: Mapping[str, Any] | None, item: Item, *, at: str | None = None
) -> dict[str, Any]:
    """The section after the item, validated by its model. `data` is the stored section (None
    when it was never saved). `at` is the key the item applies to when it is not the item's own:
    a user's edit of a proposed item replaces the entry the proposal was about, whatever the
    edited value is called now.

    An item changes one entry at most. When several stored entries share the key, whatever the
    action, nothing is applied: the item cannot say which entry it is about.

    Raises ProfileConflict when the section as it is cannot take the item (SHARED_KEY when
    several entries share the key), and pydantic.ValidationError when what is stored no longer
    fits its model.
    """
    after = canonical(section, data)
    slot, key = parse_key(section, item.key if at is None else at)
    exact = item.action is Action.replace
    value = item.value
    if slot.keyed and len(_matches(slot, after[slot.name], key)) > 1:
        raise ProfileConflict(SHARED_KEY)
    if item.action is Action.remove:
        if slot.keyed:
            after[slot.name] = [
                entry for entry in after[slot.name] if entry_key(slot, entry) != key
            ]
        elif slot.kind is SlotKind.terms:
            stored = after[slot.name]
            after[slot.name] = {**stored, "terms": _with_terms(stored, {"terms": []}, True)}
        else:
            after[slot.name] = [] if slot.kind is SlotKind.words else None
    elif slot.kind is SlotKind.scalar:
        after[slot.name] = value
    elif slot.kind is SlotKind.words:
        after[slot.name] = list(value) if exact else _join(after[slot.name], value)
    elif slot.kind is SlotKind.terms:
        after[slot.name] = _merged(slot, after[slot.name], value, exact)
    else:
        entries = list(after[slot.name])
        found = _matches(slot, entries, key)
        if slot.kind is SlotKind.lines:
            if not found:
                entries.append(value)
            elif exact:
                entries[found[0]] = value
        elif found:
            entries[found[0]] = _merged(slot, entries[found[0]], value, exact)
        else:
            entry = dict(value)
            if slot.name == "employers" and "key" not in entry:
                entry["key"] = _unused_key(entries, _identity(slot, entry))
            _insert(slot, entries, entry)
        after[slot.name] = entries
    try:
        return schemas.validate_section(section, after)
    except ValidationError as exc:
        raise ProfileConflict(schemas.describe_validation_error(exc)) from None


# --- taking an item back --------------------------------------------------------------------------


def held(section: str, data: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    """What a canonical section holds at a key, with its place, for an undo: `{"value": v}` for
    a slot that holds one value, `{"entries": [[position, entry]]}` for an entry (an empty list
    when there is none). None when several entries share the key: what an item did there cannot
    be taken back exactly."""
    slot, key = parse_key(section, key)
    stored = data.get(slot.name)
    if not slot.keyed:
        return {"value": stored}
    entries = list(stored or [])
    found = _matches(slot, entries, key)
    if len(found) > 1:
        return None
    return {"entries": [[position, entries[position]] for position in found]}


def held_hash(record: Mapping[str, Any]) -> str:
    """The hash of what `held` recorded, without the places: an entry that moved because another
    one was inserted above it is still the same entry."""
    if "entries" in record:
        return value_hash([entry for _, entry in record["entries"]])
    return value_hash(record.get("value"))


def restore(
    section: str, data: Mapping[str, Any] | None, key: str, record: Mapping[str, Any]
) -> dict[str, Any]:
    """The section with what `held` recorded put back at `key`: the inverse of one `apply`. An
    entry returns to the place of the entry that replaced it, or, when the item removed it, to
    its recorded position (the end when the list is shorter now). No other entry moves. Raises
    ProfileConflict when the section as it is cannot take it back."""
    after = canonical(section, data)
    slot, key = parse_key(section, key)
    if not slot.keyed:
        after[slot.name] = record.get("value")
    else:
        entries = list(after[slot.name])
        found = _matches(slot, entries, key)
        kept = [(position, entry) for position, entry in record["entries"]]
        if found:
            entries[found[0] : found[0] + 1] = [entry for _, entry in kept]
        else:
            for position, entry in kept:
                entries.insert(min(int(position), len(entries)), entry)
        after[slot.name] = entries
    try:
        return schemas.validate_section(section, after)
    except ValidationError as exc:
        raise ProfileConflict(schemas.describe_validation_error(exc)) from None
