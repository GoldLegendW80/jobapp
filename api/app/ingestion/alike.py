"""When values that documents give are one fact: the rules A and B of the merge (`merge.py`).

Pure: no database, no model call. The merge calls it on cleaned values (what `items.build` gives)
and on the entries of the profile.

**Rule B, one entry under two keys.** The key of an entry is literal (`items.entry_key`), so the
same job written "NOVACARE" in one document and "NOVACARE GIE" in another, or "English" and
"Anglais", lands under two keys. `same_entry` says when two entries of one slot are the same entry:
- `experience.employers`: the organisations agree (`fold` equal, or the words of one are the first
  words of the other: "novacare" and "novacare gie"), the starts agree (equal, or a year alone and a
  month of that year), and the ends agree the same way. Two missing ends agree; one missing end
  and one given do not: those are two positions.
- `experience.education`: names `fold` equal whose years agree (equal where both are given, a
  missing start or end agrees with anything); or names that differ but are one school (the words
  of one inside the other, or the distinctive words of the shorter, two at least once the words
  every school name has are removed, all in the longer: "Université de Valmont Haute-Rive" and
  "University of Valmont Haute-Rive") over the same years: a start or an end given on both
  sides, and every year given on both sides equal. A name that differs is never matched on a
  missing year: "ENS Paris" without years is not "ENS Paris-Saclay" of 2015 (owner, 2026-11-08:
  "schools with the same years").
- `identity.languages`: one language through `LANGUAGES` (its name in English, in French and in
  itself), or names `fold` equal.
- `skills.have`, `adjacent` and `none`: names `fold` equal ("React Native", "react-native").
Every other slot is matched by its key alone.

**Rule A, values that agree.** `combine` makes one value of several values of one key, field by
field, or answers None when one field disagrees:
- texts `fold` equal: the one not written in capitals only, else the first;
- one empty (null, "", an empty list) and the other given: the given one;
- lists of words: their union, without a word `fold` equal to one before it, in first order;
- phone numbers whose digits agree once a national leading 0 and a country code are reconciled:
  the international form;
- the name of an entry that rule B matched by containment ("NOVACARE", "NOVACARE GIE"): the longer
  one;
- everything else (dates, choices, numbers, addresses): equal only. "2021" and "2021-03" are two
  values.
With `base`, the first value is what the profile holds: wherever it agrees, its own wording is
kept, so a document never rewrites what the person typed only to change its capitals.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.ingestion import items
from app.ingestion.citations import FieldType

__all__ = [
    "LANGUAGES",
    "Shape",
    "combine",
    "fold",
    "matched",
    "same_entry",
    "same_phone",
    "shape",
]

# Signs that are part of a name, not separators: "C#", "AT&T", "100%".
_KEPT_SIGNS = frozenset("#%&*@")
_MINUS = chr(0x2212)


@lru_cache(maxsize=4096)
def _plain(char: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", char) if not unicodedata.combining(c))


def fold(text: str) -> str:
    """A text as rules A and B compare it: NFKC, no invisible character, case folded, accents
    removed, every dash and punctuation mark but `# % & * @` read as a space, spaces collapsed.
    "Jane-Ann Doe", "JANE-ANN DOE" and "Jane Ann Doe" fold alike; "C#" and "C" do not."""
    out: list[str] = []
    for char in items.fold(text):
        for part in _plain(char) if not char.isascii() else char:
            category = unicodedata.category(part)
            spaced = (category[0] == "P" and part not in _KEPT_SIGNS) or part == _MINUS
            out.append(" " if spaced else part)
    return " ".join("".join(out).split())


def _capitals(text: str) -> bool:
    cased = [char for char in text if char.isupper() or char.islower()]
    return bool(cased) and all(char.isupper() for char in cased)


# --- phone numbers ----------------------------------------------------------------------------

_TRUNK = re.compile(r"\(\s*0\s*\)")
PHONE_DIGITS_MIN = 6
_COUNTRY_MAX = 3


def _phone(text: str) -> tuple[bool, str]:
    """Whether the number is written with its country (`+` or `00`), and its digits without the
    `00` and without a `(0)`."""
    cleaned = _TRUNK.sub("", text)
    digits = "".join(str(unicodedata.decimal(char)) for char in cleaned if char.isdecimal())
    international = cleaned.lstrip().startswith("+") or digits.startswith("00")
    return international, digits[2:] if digits.startswith("00") else digits


def same_phone(first: str, second: str) -> bool:
    """Whether two phone numbers are one: the same digits, or the national form (with or without
    its leading 0) is the international one without a country code of one to three digits."""
    first_world, first_digits = _phone(first)
    second_world, second_digits = _phone(second)
    if min(len(first_digits), len(second_digits)) < PHONE_DIGITS_MIN:
        return False
    if first_world == second_world:
        return first_digits == second_digits
    world, national = (
        (first_digits, second_digits) if first_world else (second_digits, first_digits)
    )
    rest = national[1:] if national.startswith("0") else national
    country = len(world) - len(rest)
    return len(rest) >= PHONE_DIGITS_MIN and 1 <= country <= _COUNTRY_MAX and world.endswith(rest)


def _international(text: str) -> bool:
    return _phone(text)[0]


# --- languages --------------------------------------------------------------------------------

# Each language under its name in English, in French and in itself (folded when read).
LANGUAGES: Mapping[str, tuple[str, ...]] = {
    "ar": ("Arabic", "Arabe", "العربية"),
    "ca": ("Catalan", "Català"),
    "cs": ("Czech", "Tchèque", "Čeština"),
    "da": ("Danish", "Danois", "Dansk"),
    "de": ("German", "Allemand", "Deutsch"),
    "el": ("Greek", "Grec", "Ελληνικά"),
    "en": ("English", "Anglais"),
    "es": ("Spanish", "Espagnol", "Español", "Castellano"),
    "fa": ("Persian", "Persan", "Farsi", "فارسی"),
    "fi": ("Finnish", "Finnois", "Suomi"),
    "fr": ("French", "Français"),
    "he": ("Hebrew", "Hébreu", "עברית"),
    "hi": ("Hindi", "हिन्दी"),
    "hu": ("Hungarian", "Hongrois", "Magyar"),
    "it": ("Italian", "Italien", "Italiano"),
    "ja": ("Japanese", "Japonais", "日本語"),
    "ko": ("Korean", "Coréen", "한국어"),
    "nl": ("Dutch", "Néerlandais", "Nederlands"),
    "no": ("Norwegian", "Norvégien", "Norsk"),
    "pl": ("Polish", "Polonais", "Polski"),
    "pt": ("Portuguese", "Portugais", "Português"),
    "ro": ("Romanian", "Roumain", "Română"),
    "ru": ("Russian", "Russe", "Русский"),
    "sv": ("Swedish", "Suédois", "Svenska"),
    "tr": ("Turkish", "Turc", "Türkçe"),
    "uk": ("Ukrainian", "Ukrainien", "Українська"),
    "vi": ("Vietnamese", "Vietnamien", "Tiếng Việt"),
    "zh": ("Chinese", "Chinois", "Mandarin", "中文", "汉语", "漢語"),
}
_LANGUAGE_OF = {fold(name): code for code, names in LANGUAGES.items() for name in names}

# --- schools ----------------------------------------------------------------------------------

# Words that many school names hold, in English and in French (folded): two schools that share
# only these are not shown to be one.
_SCHOOL_TEXT = (
    "a an and at au aux d de des du en et for in l la le les of the"
    " university université universities universités universidad universität"
    " school schools école écoles institute institut institution college collège"
    " lycée high faculty faculté academy académie department département center centre"
    " national nationale supérieure supérieur higher graduate business engineering"
    " ingénieurs engineers management technology technologie sciences science studies"
    " études international internationale public private privé privée state"
)
_SCHOOL_WORDS = frozenset(fold(word) for word in _SCHOOL_TEXT.split())
_DISTINCTIVE_MIN = 2


# --- rule B: one entry ------------------------------------------------------------------------

_NAME_FIELD = {
    "employers": "organisation",
    "education": "school",
    "languages": "name",
    "have": "name",
    "adjacent": "name",
    "none": "name",
}


def matched(slot: items.Slot) -> bool:
    """Whether rule B finds the entries of this slot by more than their key."""
    return slot.kind is items.SlotKind.entries and slot.name in _NAME_FIELD


@dataclass(frozen=True)
class Shape:
    """What rule B compares of one entry, computed once per entry."""

    words: tuple[str, ...]
    language: str
    start: str | None
    end: str | None


def shape(slot: items.Slot, value: Mapping[str, Any]) -> Shape:
    """The parts of an entry of a `matched` slot that rule B reads."""
    name = value.get(_NAME_FIELD[slot.name])
    folded = fold(name) if isinstance(name, str) else ""
    language = _LANGUAGE_OF.get(folded, folded) if slot.name == "languages" else ""
    start, end = value.get("start"), value.get("end")
    return Shape(
        tuple(folded.split()),
        language,
        start if isinstance(start, str) and start else None,
        end if isinstance(end, str) and end else None,
    )


def _periods_agree(first: str | None, second: str | None) -> bool:
    """Equal, or a year alone and a month of that year. Two missing agree, one missing does not."""
    if first is None or second is None:
        return first is second
    shorter, longer = sorted((first, second), key=len)
    return first == second or (len(shorter) == 4 and longer.startswith(f"{shorter}-"))


def _years_agree(first: str | None, second: str | None) -> bool:
    return first is None or second is None or first[:4] == second[:4]


def _leading(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    shorter, longer = sorted((first, second), key=len)
    return bool(shorter) and longer[: len(shorter)] == shorter


def _inside(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    shorter, longer = sorted((first, second), key=len)
    size = len(shorter)
    return bool(shorter) and any(
        longer[at : at + size] == shorter for at in range(len(longer) - size + 1)
    )


def _distinctive(words: tuple[str, ...]) -> frozenset[str]:
    return frozenset(word for word in words if word not in _SCHOOL_WORDS and len(word) > 1)


def _one_school(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    """Two names that differ but name one school."""
    if _inside(first, second):
        return True
    shorter, longer = sorted((_distinctive(first), _distinctive(second)), key=len)
    return len(shorter) >= _DISTINCTIVE_MIN and shorter <= longer


def _same_years(first: Shape, second: Shape) -> bool:
    both = [
        (one, other)
        for one, other in ((first.start, second.start), (first.end, second.end))
        if one is not None and other is not None
    ]
    return bool(both) and all(one[:4] == other[:4] for one, other in both)


def same_entry(slot: items.Slot, first: Shape, second: Shape) -> bool:
    """Whether two entries of a `matched` slot are one entry (rule B, in the module's text)."""
    if slot.name == "employers":
        return (
            _leading(first.words, second.words)
            and _periods_agree(first.start, second.start)
            and _periods_agree(first.end, second.end)
        )
    if slot.name == "education":
        if not (first.words and second.words):
            return False
        if first.words == second.words:
            return _years_agree(first.start, second.start) and _years_agree(first.end, second.end)
        return _one_school(first.words, second.words) and _same_years(first, second)
    if slot.name == "languages":
        return bool(first.language) and first.language == second.language
    return bool(first.words) and first.words == second.words


# --- rule A: one value ------------------------------------------------------------------------


class _DisagreeError(Exception):
    pass


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _union(lists: Sequence[list[Any]]) -> list[Any]:
    seen: set[Any] = set()
    joined: list[Any] = []
    for words in lists:
        for word in words:
            mark = fold(word) if isinstance(word, str) else items.value_hash(word)
            if mark not in seen:
                seen.add(mark)
                joined.append(word)
    return joined


def _agree(kind: FieldType | None, first: Any, second: Any) -> bool:
    if isinstance(first, str) and isinstance(second, str):
        if kind is FieldType.PHONE:
            return same_phone(first, second)
        if kind is FieldType.EMAIL:
            return first.strip().casefold() == second.strip().casefold()
        if kind is FieldType.URL:
            return items._address(first) == items._address(second)
        if kind in (FieldType.TEXT, FieldType.NAME, FieldType.TERM):
            return fold(first) == fold(second)
    if isinstance(first, bool) or isinstance(second, bool):
        return first is second
    return bool(first == second)


def _pick(kind: FieldType | None, given: Sequence[Any]) -> Any:
    if kind is FieldType.PHONE:
        return next((one for one in given if _international(one)), given[0])
    if kind in (FieldType.TEXT, FieldType.NAME, FieldType.TERM):
        return next((one for one in given if not _capitals(one)), given[0])
    return given[0]


def _field(kind: FieldType | None, values: Sequence[Any], base: bool, name_of: str) -> Any:
    """One field of several values (rule A). Raises _DisagreeError."""
    given = [one for one in values if not _empty(one)]
    if not given:
        return values[0]
    if all(isinstance(one, list) for one in given):
        return _union(given)
    if any(isinstance(one, list | dict) for one in given):
        if any(one != given[0] for one in given):
            raise _DisagreeError
        return given[0]
    contained = False
    for at, first in enumerate(given):
        for second in given[at + 1 :]:
            if _agree(kind, first, second):
                continue
            if not (name_of and isinstance(first, str) and isinstance(second, str)):
                raise _DisagreeError
            if not _names_agree(name_of, first, second):
                raise _DisagreeError
            contained = True
    if base and not _empty(values[0]):
        return values[0]
    if contained and name_of == "languages":
        return given[0]
    if contained:
        return max(given, key=lambda one: (len(fold(one).split()), len(one)))
    return _pick(kind, given)


def _names_agree(slot: str, first: str, second: str) -> bool:
    """The names that rule B joins and rule A then reads as one: an organisation whose words
    start the other's, a school inside the other, the same language."""
    words = tuple(fold(first).split()), tuple(fold(second).split())
    if slot == "employers":
        return _leading(*words)
    if slot == "education":
        return _inside(*words)
    if slot == "languages":
        code = _LANGUAGE_OF.get(fold(first))
        return code is not None and code == _LANGUAGE_OF.get(fold(second))
    return False


def _types(slot: items.Slot) -> dict[str, FieldType]:
    return {path.removesuffix("[]"): kind for path, kind in slot.field_types.items()}


def combine(slot: items.Slot, values: Sequence[Any], *, base: bool = False) -> Any:
    """One value for several values of one key that agree (rule A, in the module's text), or
    None when a field disagrees. `values` are cleaned values of the slot, in document order;
    with `base`, the first is what the profile holds, and its wording wins where it agrees."""
    if not values:
        return None
    types = _types(slot)
    try:
        if slot.kind in (items.SlotKind.entries, items.SlotKind.terms):
            if not all(isinstance(one, Mapping) for one in values):
                return None
            name_of = slot.name if slot.name in _NAME_FIELD else ""
            fields = list(dict.fromkeys(name for one in values for name in one))
            out: dict[str, Any] = {}
            for name in fields:
                given = [one.get(name) for one in values]
                if name in slot.setting_fields:
                    out[name] = (
                        given[0] if base else next((g for g in given if g is not None), None)
                    )
                    continue
                named = name_of if name == _NAME_FIELD.get(slot.name) else ""
                out[name] = _field(types.get(name), given, base, named)
            return out
        return _field(types.get(""), values, base, "")
    except _DisagreeError:
        return None
