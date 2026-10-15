"""What code reads of an uploaded CV's text (task D24; SPEC-documents.md, "Content conversion of
an upload"): its language, and how much of the `fixed` block the base reader wrote the text
really states.

**The fixed block.** The base reader writes the employers, schools and languages as the file
states them, and that block then sources every CV written from the base CV. So code keeps of it
only what the uploaded text holds (`check_fixed`), comparing words, not meanings, and refusing
when unsure:

- a name (an employer's organisation, a school, a language) is kept when its words stand in the
  text in that order, whole, case, accents, punctuation and spacing folded ("Example Corp."
  matches "EXAMPLE CORP"; "Example Corporation" does not match "Example Corp", and "Corp" alone
  does not match "Example Corp"). An absent name drops its entry, and with an employer its
  experience entry (`apply`);
- a title, degree, location or level that is absent is emptied; its entry stays;
- a date keeps its year when the year stands alone in the text (never inside a longer number),
  and its month when the text writes that month next to that year: "03/2019", "3.2019",
  "2019-03", "2019/03", "March 2019", "Mar. 2019", "mars 2019", "févr. 2019". Otherwise the
  month goes ("2019-03" becomes "2019"); an absent year drops the date. An empty end stays empty
  (the schema reads it as "present, or not stated"): it is never checked.

Every change is a `Correction` by code, of code FIXED_NOT_IN_UPLOAD.

**The language** (`language_of`): French when the text holds more French function words than
English ones, else English, the app's default (an empty text, a tie).

What this does not see, stated: a two digit year ("03/19") is not read as its year, so such a
date is dropped; a month written in a third language is not read, so it goes; a name the file
writes in two different ways is kept only in the way the reader copied, when that way is in the
text. Pure: no I/O. Linear in the length of the text: one fold and one pass to cut tokens, then
a substring test per value and a dictionary lookup per date.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.documents import content as c
from app.documents import rules
from app.documents.corrections import Correction, reason_for
from app.documents.models import Language

FIXED_NOT_IN_UPLOAD = "fixed_not_in_upload"

_MONTH_NAMES: tuple[frozenset[str], ...] = tuple(
    frozenset(names.split())
    for names in (
        "january jan janvier janv",
        "february feb fevrier fevr fev",
        "march mar mars",
        "april apr avril avr",
        "may mai",
        "june jun juin",
        "july jul juillet juil",
        "august aug aout",
        "september sep sept septembre",
        "october oct octobre",
        "november nov novembre",
        "december dec decembre",
    )
)
# Between a month and its year: "/" "." "-" for figures, nothing, "." or "," after a name.
_FIGURE_SEPARATORS = frozenset("/.-")
_NAME_SEPARATORS = frozenset(("", ".", ","))

# Function words of each language, none of them a word of the other once accents are folded.
_FRENCH = frozenset(
    [
        "le",
        "la",
        "les",
        "des",
        "du",
        "et",
        "une",
        "pour",
        "avec",
        "dans",
        "sur",
        "aux",
        "chez",
        "depuis",
        "est",
        "sont",
        "nous",
        "je",
        "mon",
        "ma",
        "mes",
        "leur",
        "ou",
        "au",
        "par",
        "competences",
        "langues",
        "professionnelle",
        "professionnelles",
    ]
)
_ENGLISH = frozenset(
    [
        "the",
        "and",
        "of",
        "to",
        "for",
        "with",
        "in",
        "at",
        "since",
        "is",
        "are",
        "we",
        "my",
        "an",
        "our",
        "by",
        "from",
        "skills",
        "languages",
        "professional",
    ]
)


@dataclass(frozen=True, slots=True)
class _Text:
    """The folded text cut into words: `joined` is " w1 w2 ... wn ", for whole word lookups;
    `years` maps a word of four figures to its positions; `gaps[i]` is what stands between word
    i and word i+1, spaces removed."""

    words: tuple[str, ...]
    joined: str
    years: Mapping[str, tuple[int, ...]]
    gaps: tuple[str, ...]


def _cut(text: str) -> tuple[list[str], list[str]]:
    words: list[str] = []
    gaps: list[str] = []
    word: list[str] = []
    gap: list[str] = []
    for char in text:
        if char.isalnum():
            if not word:
                if words:
                    gaps.append("".join(gap).replace(" ", ""))
                gap = []
            word.append(char)
            continue
        if word:
            words.append("".join(word))
            word = []
        gap.append(char)
    if word:
        words.append("".join(word))
    return words, gaps


def _read(text: str) -> _Text:
    words, gaps = _cut(rules.fold(text).text)
    years: dict[str, list[int]] = {}
    for at, word in enumerate(words):
        if len(word) == 4 and word.isascii() and word.isdigit():
            years.setdefault(word, []).append(at)
    return _Text(
        tuple(words),
        " " + " ".join(words) + " ",
        {year: tuple(found) for year, found in years.items()},
        tuple(gaps),
    )


def _words(value: str) -> list[str]:
    return _cut(rules.fold(value).text)[0]


def present(value: str, text: _Text) -> bool:
    """Do the words of `value` stand in `text`, in order and whole?"""
    words = _words(value)
    return bool(words) and " " + " ".join(words) + " " in text.joined


def _month_at(text: _Text, at: int, month: int) -> bool:
    """Is `month` written next to the year that is word `at`?"""
    words, gaps = text.words, text.gaps
    if at > 0:
        before, gap = words[at - 1], gaps[at - 1]
        if before.isascii() and before.isdigit() and len(before) <= 2:
            if int(before) == month and gap in _FIGURE_SEPARATORS:
                return True
        elif before in _MONTH_NAMES[month - 1] and gap in _NAME_SEPARATORS:
            return True
    if at + 1 < len(words):
        after, gap = words[at + 1], gaps[at]
        if after.isascii() and len(after) == 2 and after.isdigit() and gap in _FIGURE_SEPARATORS:
            return int(after) == month
    return False


def period(value: str | None, text: _Text) -> str | None:
    """What of a stored period ("2019-03", "2019") the text states: the same, its year alone,
    or None."""
    if not value:
        return value
    year, _, month = value.partition("-")
    found = text.years.get(year, ())
    if not found:
        return None
    if not month:
        return year
    number = int(month)
    return value if any(_month_at(text, at, number) for at in found) else year


@dataclass(frozen=True)
class Checked:
    """The fixed block as the text states it, the employer keys it dropped, and the record."""

    fixed: c.Fixed
    dropped_keys: frozenset[str]
    corrections: tuple[Correction, ...]


def _record(path: str, before: str, after: str) -> Correction:
    reason = reason_for(FIXED_NOT_IN_UPLOAD)
    return Correction(path, before, after, FIXED_NOT_IN_UPLOAD, "code", reason)


class _Fields:
    """Empties or shortens the fields of one entry the text does not state, recording each."""

    def __init__(self, text: _Text, path: str, records: list[Correction]) -> None:
        self.text, self.path, self.records = text, path, records

    def line(self, name: str, value: str) -> str:
        if not value or present(value, self.text):
            return value
        self.records.append(_record(f"{self.path}.{name}", value, ""))
        return ""

    def date(self, name: str, value: str | None) -> str | None:
        kept = period(value, self.text)
        if kept != value:
            self.records.append(_record(f"{self.path}.{name}", value or "", kept or ""))
        return kept


def check_fixed(fixed: c.Fixed, text: str) -> Checked:
    """The fixed block of an upload, kept as far as `text` states it (module docstring)."""
    read = _read(text)
    records: list[Correction] = []
    employers: list[c.FixedEmployer] = []
    dropped: set[str] = set()
    for i, e in enumerate(fixed.employers):
        path = f"fixed.employers.{i}"
        if not present(e.organisation, read):
            records.append(_record(f"{path}.organisation", e.organisation, ""))
            dropped.add(e.key)
            continue
        f = _Fields(read, path, records)
        employers.append(
            e.model_copy(
                update={
                    "title": f.line("title", e.title),
                    "start": f.date("start", e.start),
                    "end": f.date("end", e.end),
                    "location": f.line("location", e.location),
                }
            )
        )
    schools: list[c.FixedSchool] = []
    for i, s in enumerate(fixed.schools):
        path = f"fixed.schools.{i}"
        if not present(s.school, read):
            records.append(_record(f"{path}.school", s.school, ""))
            continue
        f = _Fields(read, path, records)
        schools.append(
            s.model_copy(
                update={
                    "degree": f.line("degree", s.degree),
                    "start": f.date("start", s.start),
                    "end": f.date("end", s.end),
                    "location": f.line("location", s.location),
                }
            )
        )
    languages: list[c.FixedLanguage] = []
    for i, lang in enumerate(fixed.languages):
        path = f"fixed.languages.{i}"
        if not present(lang.name, read):
            records.append(_record(f"{path}.name", lang.name, ""))
            continue
        f = _Fields(read, path, records)
        languages.append(lang.model_copy(update={"level": f.line("level", lang.level)}))
    kept = c.Fixed(employers=employers, schools=schools, languages=languages)
    return Checked(kept, frozenset(dropped), tuple(records))


@dataclass(frozen=True)
class Applied:
    """A submit of the base reader with its fixed block checked: the data to submit, the fixed
    block kept (None when the one sent is not valid: the schema check names its fields), and the
    corrections made."""

    data: Any
    fixed: c.Fixed | None
    corrections: tuple[Correction, ...]


def apply(data: Any, text: str) -> Applied:
    """Check the fixed block of a submit against the upload's text, and drop the experience
    entries of the employers it dropped (their bullets recorded). Anything that is not the
    shape of a base CV is passed through, for the schema check to refuse."""
    if not isinstance(data, Mapping):
        return Applied(data, None, ())
    try:
        fixed = c.Fixed.model_validate(data.get("fixed") or {})
    except ValidationError:
        return Applied(data, None, ())
    checked = check_fixed(fixed, text)
    records = list(checked.corrections)
    experience = data.get("experience")
    if isinstance(experience, Sequence) and not isinstance(experience, str):
        kept: list[Any] = []
        for i, entry in enumerate(experience):
            key = entry.get("employer_key") if isinstance(entry, Mapping) else None
            if key not in checked.dropped_keys:
                kept.append(entry)
                continue
            bullets = entry.get("bullets") if isinstance(entry, Mapping) else None
            for j, bullet in enumerate(bullets if isinstance(bullets, list) else []):
                if isinstance(bullet, str):
                    records.append(_record(f"experience.{i}.bullets.{j}", bullet, ""))
        experience = kept
    out = {**data, "fixed": c.dump(checked.fixed)}
    if experience is not None:
        out["experience"] = experience
    return Applied(out, checked.fixed, tuple(records))


def language_of(text: str) -> Language:
    """French when the text holds more French function words than English ones, else English."""
    french = english = 0
    for word in _words(text):
        if word in _FRENCH:
            french += 1
        elif word in _ENGLISH:
            english += 1
    return Language.fr if french > english else Language.en
