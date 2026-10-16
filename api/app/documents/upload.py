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
  month goes ("2019-03" becomes "2019"); an absent year drops the date;
- an employer's start without an end prints as "to present", so it stays only when the reader
  left the end empty and the text writes that start open (`_open_starts`): an open end right
  after it, on its line or after "to", "until", "à", "jusqu'à" or a dash ("2023 to present",
  "02/2023" then a dash and "Present", "mars 2022 à aujourd'hui", "2022 à ce jour", "2023 to
  date"), or "since", "depuis", "from", "starting" or "dès" right before it with no end after
  it ("Since 2023", "Depuis mars 2022"). Otherwise the start goes too and the period prints
  nothing: an end the text does not state ("2018 to 2020" read with the end "2021") never makes
  an ended role a current one. The cost of refusing when unsure: a start followed by a dash and
  nothing else, or an open end on the next line with no dash or "to" before it, loses its
  period. A school's empty end stays empty (it prints its start alone).

Every change is a `Correction` by code, of code FIXED_NOT_IN_UPLOAD and reason NOT_IN_UPLOAD.

**The language** (`language_of`): French when the text holds more French function words than
English ones, else English, the app's default (an empty text, a tie).

What this does not see, stated: a two digit year ("03/19") is not read as its year, so such a
date is dropped, and an employer whose end it was loses its start too ("2018 to 06/20" prints
no period, never "2018 to present"); a month written in a third language is not read, so it
goes; open ends outside the words above lose the period ("à nos jours", "to the present",
"ongoing", "2022 a ce jour" without its accent), and so do "Depuis 2018 à Lyon" (an "à" after a
since date reads as an end) and "de 2018" alone ("de" is also "of"); a name the file writes in
two different ways is kept only in the way the reader copied, when that way is in the text;
each value is looked for in the whole text, not beside its organisation (a PDF's lines may come
out of order), so a title or a year of one role stays on another, an open end written beside
the same start of another role keeps a role current, and a name the file writes in another
context (a client) stays as an employer. Pure: no I/O. Linear in the length of the text: one
fold and one pass to cut tokens, one pass over the words for the open starts, then a substring
test per value and a dictionary lookup per date.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.documents import checks, rules
from app.documents import content as c
from app.documents.corrections import Correction
from app.documents.models import Language

FIXED_NOT_IN_UPLOAD = "fixed_not_in_upload"
NOT_IN_UPLOAD = "Not in your uploaded CV"  # the reason shown beside each of its corrections

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


def _breaks_line(gap: str) -> bool:
    return "".join(gap.splitlines()) != gap


def _ends_open(text: str, words: list[rules.Word], k: int) -> bool:
    """Does an open end follow the date that ends before word `k`?"""
    if k >= len(words):
        return False
    gap = text[words[k - 1].end : words[k].start]
    mark = gap.strip()
    until = checks._role(text, words[k]) == "until" and not mark
    dash = bool(mark) and all(ch in checks._RANGE_DASHES for ch in mark)
    if until:
        k += 1
        if words[k - 1].folded == "jusqu" and k < len(words) and words[k].folded in ("a", "au"):
            k += 1
    elif not dash and (mark or _breaks_line(gap)):
        return False
    # "date" closes a period only after "to" or "à": alone it is a word ("Date of birth").
    return checks._open_end(words, k, len(words)) is not None and (
        until or words[k].folded != "date"
    )


def _ends_after(text: str, words: list[rules.Word], k: int) -> bool:
    """Does an end follow the date that ends before word `k` ("from 2018 to 2020")?"""
    if k >= len(words):
        return False
    mark = text[words[k - 1].end : words[k].start].strip()
    if mark and all(ch in checks._RANGE_DASHES for ch in mark):
        return True
    return checks._role(text, words[k]) == "until"


@dataclass(frozen=True, slots=True)
class _OpenStarts:
    """The dates a text writes as the start of an open period: (year, month or None)."""

    dates: frozenset[tuple[int, int | None]]
    years: frozenset[int]

    def hold(self, start: str) -> bool:
        """Is `start` ("2023-02", "2023") one of them, its month counted when both give one?"""
        year, _, month = start.partition("-")
        if not month:
            return int(year) in self.years
        return (int(year), int(month)) in self.dates or (int(year), None) in self.dates


def _open_starts(text: str) -> _OpenStarts:
    """The starts `text` writes open (module docstring), read in one pass over its words with
    the period vocabulary of `checks`."""
    words = rules.words_of(text)
    found: set[tuple[int, int | None]] = set()
    i = 0
    while i < len(words):
        role = checks._role(text, words[i])
        j = i + 1 if role else i
        date = checks._date_at(text, words, j)
        if date is None or not all(w.folded.isascii() for w in words[j : date[1]]):
            i += 1
            continue
        claim, k = date
        # "de 2018" is as often "of 2018": only "de 2018 à aujourd'hui" reads as open.
        since = role == "since" and words[i].folded != "de" and not _ends_after(text, words, k)
        if role != "until" and (since or _ends_open(text, words, k)):
            found.add(claim)
        i = k
    return _OpenStarts(frozenset(found), frozenset(year for year, _ in found))


@dataclass(frozen=True)
class Checked:
    """The fixed block as the text states it, the employer keys it dropped, and the record."""

    fixed: c.Fixed
    dropped_keys: frozenset[str]
    corrections: tuple[Correction, ...]


def _record(path: str, before: str, after: str) -> Correction:
    return Correction(path, before, after, FIXED_NOT_IN_UPLOAD, "code", NOT_IN_UPLOAD)


class _Fields:
    """Empties or shortens the fields of one entry the text does not state, recording each."""

    def __init__(self, text: _Text, path: str, records: list[Correction]) -> None:
        self.text, self.path, self.records = text, path, records

    def line(self, name: str, value: str) -> str:
        if not value or present(value, self.text):
            return value
        self.records.append(_record(f"{self.path}.{name}", value, ""))
        return ""

    def dates(
        self, start: str | None, end: str | None, opens: _OpenStarts | None = None
    ) -> tuple[str | None, str | None]:
        """The start and end the text states. With an employer's `opens`, a start left without
        an end stays only when the reader's end was empty and the text writes that start open."""
        kept_start, kept_end = period(start, self.text), period(end, self.text)
        if opens is not None and kept_start and not kept_end:
            kept_start = kept_start if not end and opens.hold(kept_start) else None
        for name, before, after in (("start", start, kept_start), ("end", end, kept_end)):
            if after != before:
                self.records.append(_record(f"{self.path}.{name}", before or "", after or ""))
        return kept_start, kept_end


def check_fixed(fixed: c.Fixed, text: str) -> Checked:
    """The fixed block of an upload, kept as far as `text` states it (module docstring)."""
    read = _read(text)
    opens = _open_starts(text)
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
        title = f.line("title", e.title)
        start, end = f.dates(e.start, e.end, opens)
        employers.append(
            e.model_copy(
                update={
                    "title": title,
                    "start": start,
                    "end": end,
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
        degree = f.line("degree", s.degree)
        start, end = f.dates(s.start, s.end)
        schools.append(
            s.model_copy(
                update={
                    "degree": degree,
                    "start": start,
                    "end": end,
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
            if not isinstance(key, str) or key not in checked.dropped_keys:
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
