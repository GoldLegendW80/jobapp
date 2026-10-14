"""The document rules (SPEC-documents.md, "Being careful", steps 1 to 3): pure functions.

What M9's `factcheck_rules.check` does not know about documents lives here: the structure of
the content and the employer keys (step 1), the soft lengths of a template (counters, never
refused), the dash rule and the banned phrases of the person's `DocumentSettings`, and the
cutting of a text into sentences that the automatic correction removes one at a time.

Pure and deterministic: no I/O, no clock, no model, no global state. The same input gives the
same results in the same order. Linear time: every scan walks the text once with a bounded look
back, and the only regular expressions are single classes under one quantifier.

Offsets are Python string indices (code points) into the field's text, `end` excluded, the
same convention as `factcheck_rules.Issue`; a combining mark stays inside the span of its base
letter.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.documents import content as c

# "invalid": refused at submit (the writer is answered, nothing is stored). "claim": the fact
# check or a banned phrase, corrected. "separator": a dash the dash rule fixes by code. "note":
# a counter or an empty field, shown and never acted on.
Level = Literal["invalid", "claim", "separator", "note"]
LEVELS: tuple[Level, ...] = ("invalid", "claim", "separator", "note")

DASH_SEPARATOR = "dash_separator"
BANNED_PHRASE = "banned_phrase"
UNKNOWN_EMPLOYER = "unknown_employer"
OVER_SOFT_LENGTH = "over_soft_length"
REQUIRED_EMPTY = "required_empty"
CREDENTIAL = "credential_not_in_profile"
CHANGE = "change_not_in_profile"
DECADE = "decade_not_in_profile"


@dataclass(frozen=True, slots=True)
class Issue:
    """One finding on one field of a document. `field` is a path such as `experience.1.bullets.0`
    (empty for the whole content), `start` and `end` index the field's text (both 0 for a rule
    on the field as a whole), `code` names the rule."""

    field: str
    start: int
    end: int
    code: str
    level: Level


# --- step 1: structure --------------------------------------------------------------------------


def structure(
    kind: c.Kind, data: object
) -> tuple[c.CvContent | c.LetterContent | None, list[Issue]]:
    """The content validated, or None and one `invalid` issue per field error, in order."""
    try:
        return c.parse(kind, data), []
    except c.ContentInvalidError as exc:
        return None, [Issue(e.path, 0, 0, e.code, "invalid") for e in exc.errors]


def employer_keys(content: BaseModel, allowed: Iterable[str]) -> list[Issue]:
    """An experience entry whose key is no employer of the fixed facts (the profile's, else the
    base CV's) is invalid: the template would have nothing to print above its bullets."""
    if not isinstance(content, c.CvContent):
        return []
    known = set(allowed)
    return [
        Issue(f"experience.{i}.employer_key", 0, 0, UNKNOWN_EMPLOYER, "invalid")
        for i, role in enumerate(content.experience)
        if role.employer_key not in known
    ]


# --- soft lengths and empty fields --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SoftLimits:
    """The counters of a template's manifest, in characters; None: no counter. The defaults are
    the command line's figures (the spec's start): the main employer is the first entry."""

    headline: int | None = None
    summary: int | None = 330
    highlight: int | None = 60
    competency: int | None = None
    main_bullet: int | None = 190
    bullet: int | None = 100
    paragraph: int | None = None


DEFAULT_SOFT_LIMITS = SoftLimits()


def _soft_limit(path: str, limits: SoftLimits) -> int | None:
    head = path.split(".", 1)[0]
    if head == "experience":
        return limits.main_bullet if path.startswith("experience.0.") else limits.bullet
    return {
        "headline": limits.headline,
        "summary": limits.summary,
        "highlights": limits.highlight,
        "competencies": limits.competency,
        "paragraphs": limits.paragraph,
    }.get(head)


def soft_lengths(content: BaseModel, limits: SoftLimits) -> list[Issue]:
    """A note on each printed field longer than its counter: the excess is the span."""
    out: list[Issue] = []
    for path, text in c.text_fields(content):
        limit = _soft_limit(path, limits)
        if limit is not None and len(text) > limit:
            out.append(Issue(path, limit, len(text), OVER_SOFT_LENGTH, "note"))
    return out


def required_empty(content: BaseModel) -> list[Issue]:
    """The fields a document must not print empty: a CV's headline and summary, a letter's
    subject and at least one paragraph. The automatic correction gives them a fallback."""
    if isinstance(content, c.LetterContent):
        missing = [p for p, v in (("subject", content.subject),) if not v]
        if not content.paragraphs:
            missing.append("paragraphs")
    elif isinstance(content, c.CvContent):
        missing = [
            p for p, v in (("headline", content.headline), ("summary", content.summary)) if not v
        ]
    else:
        missing = []
    return [Issue(path, 0, 0, REQUIRED_EMPTY, "note") for path in missing]


# --- folding (banned phrases) -------------------------------------------------------------------

_DASHES = frozenset("\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d")
_APOSTROPHES = frozenset("\u2018\u2019\u201b\u02bc\u2032\u00b4`")


@dataclass(frozen=True, slots=True)
class Folded:
    """A text with accents, case and spacing folded, and for each folded character the span of
    the original it came from (`starts[i]`, `ends[i]`)."""

    text: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]

    def span(self, start: int, end: int) -> tuple[int, int]:
        return self.starts[start], self.ends[end - 1]


def _fold_char(char: str) -> str:
    if char.isspace() or unicodedata.category(char)[0] == "Z":
        return " "
    if char in _DASHES:
        return "-"
    if char in _APOSTROPHES:
        return "'"
    lowered = unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", char).casefold())
    return "".join(ch for ch in lowered if unicodedata.category(ch) not in ("Mn", "Cf", "Cc"))


def fold(text: str) -> Folded:
    """Case folded, accents and zero width characters dropped, compatibility forms decomposed
    (full width letters, ligatures), every run of white space one space, curly apostrophes and
    dash look-alikes unified. One pass; a dropped mark extends its base letter's span."""
    cache: dict[str, str] = {}
    parts: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for index, char in enumerate(text):
        folded = cache.get(char)
        if folded is None:
            folded = cache[char] = _fold_char(char)
        if not folded:
            if ends:
                ends[-1] = index + 1
            continue
        for out in folded:
            if out == " " and parts and parts[-1] == " ":
                ends[-1] = index + 1  # one space for a run
                continue
            parts.append(out)
            starts.append(index)
            ends.append(index + 1)
    return Folded("".join(parts), tuple(starts), tuple(ends))


def banned(text: str, phrases: Iterable[str]) -> list[tuple[int, int]]:
    """The spans of `text` where a banned phrase stands as whole words, case and accents
    folded, in order of position. A phrase is never matched inside a longer word."""
    folded = fold(text)
    haystack = folded.text
    found: set[tuple[int, int]] = set()
    for phrase in phrases:
        needle = fold(phrase).text.strip()
        if not needle:
            continue
        at = haystack.find(needle)
        while at != -1:
            stop = at + len(needle)
            left_ok = at == 0 or not haystack[at - 1].isalnum()
            right_ok = stop == len(haystack) or not haystack[stop].isalnum()
            if left_ok and right_ok:
                found.add(folded.span(at, stop))
            at = haystack.find(needle, at + 1)
    return sorted(found)


# --- the dash rule ------------------------------------------------------------------------------

# Long dashes are separators wherever they stand (between two digits, a range). A hyphen and its
# look-alikes are separators only with white space on both sides: "full-stack" passes.
_LONG_DASHES = frozenset("\u2012\u2013\u2014\u2015\u2e3a\u2e3b\ufe31\ufe32\ufe58")
_HYPHENS = frozenset("-\u2010\u2011\u2212\ufe63\uff0d")
_ANY_DASH = _LONG_DASHES | _HYPHENS
_PUNCT_BEFORE = frozenset(",;:(")
_PUNCT_AFTER = frozenset(",;:.!?)")


def _blank(char: str) -> bool:
    """White space on the line (a newline is not: it ends the line)."""
    return char in " \t" or unicodedata.category(char) == "Zs"


@dataclass(frozen=True, slots=True)
class Separator:
    """A dash used as a separator: the span to replace (its surrounding blanks included) and
    the replacement (", ", or "" at the start or the end of a line, or "-" in a range)."""

    start: int
    end: int
    replacement: str
    compact: str  # never longer than the span: used when the field would exceed its limit


def separators(text: str) -> list[Separator]:
    """Every dash used as a separator in `text`, in order of position. Linear: each character
    is looked at a bounded number of times (a blank at most once from each side)."""
    out: list[Separator] = []
    n = len(text)
    last_end = 0
    i = 0
    while i < n:
        if text[i] not in _ANY_DASH:
            i += 1
            continue
        j = i
        while j < n and text[j] in _ANY_DASH:
            j += 1
        is_long = j - i >= 2 or any(ch in _LONG_DASHES for ch in text[i:j])
        a = i
        while a > last_end and _blank(text[a - 1]):
            a -= 1
        b = j
        while b < n and _blank(text[b]):
            b += 1
        before = text[a - 1] if a > 0 else "\n"
        after = text[b] if b < n else "\n"
        adjacent = bool(out) and a > 0 and out[-1].end == a  # right after another separator
        spaced_left = a < i or before == "\n" or adjacent
        spaced_right = b > j or after == "\n"
        if not is_long and not (spaced_left and spaced_right):
            i = j  # a hyphenated word, a minus sign: not a separator
            continue
        if adjacent:
            # A dash right after another one ("a \u2014 \u2014 b"): one separator for both.
            prev = out.pop()
            out.append(Separator(prev.start, b, prev.replacement, prev.compact))
            last_end = i = b
            continue
        if not spaced_left and not spaced_right and before.isdigit() and after.isdigit():
            out.append(Separator(i, j, "-", "-"))  # a range, 2019\u20132021
        elif before == "\n" or after == "\n":
            out.append(Separator(a, b, "", ""))  # a dash that opens or closes a line
        elif before in _PUNCT_BEFORE:
            out.append(Separator(a, b, " ", " "))
        elif after in _PUNCT_AFTER:
            out.append(Separator(a, b, "", ""))
        else:
            out.append(Separator(a, b, ", ", ", " if b - a >= 2 else ","))
        last_end = b
        i = b
    return out


def apply_separators(text: str, found: list[Separator], compact: bool = False) -> str:
    """The text with each separator replaced, white space at its ends stripped."""
    parts: list[str] = []
    at = 0
    for sep in found:
        parts.append(text[at : sep.start])
        parts.append(sep.compact if compact else sep.replacement)
        at = sep.end
    parts.append(text[at:])
    return "".join(parts).strip()


# --- sentences ----------------------------------------------------------------------------------

_ENDS = frozenset(".!?\u2026")
_CLOSERS = frozenset("\"')]\u00bb\u201d\u2019")
# A period after one of these does not end a sentence ("e.g. Python").
_ABBREVIATIONS = frozenset(("e.g", "i.e", "vs", "cf", "p.ex", "approx", "resp"))


def _abbreviation(text: str, dot: int) -> bool:
    """Is the period at `dot` the end of a known abbreviation? Looks back at most 8 characters."""
    start = dot
    while start > 0 and dot - start < 8 and not text[start - 1].isspace():
        start -= 1
    word = text[start:dot].lower().lstrip("(")
    return word in _ABBREVIATIONS


def sentences(text: str) -> list[tuple[int, int]]:
    """The sentences of `text` as spans that cover it end to end, each with its trailing white
    space. A sentence ends at a run of ".", "!", "?" or an ellipsis (with closing quotes or
    brackets) followed by white space, or at a newline; a decimal point ("3.5") or a known
    abbreviation does not end one."""
    spans: list[tuple[int, int]] = []
    n = len(text)
    start = 0
    i = 0
    while i < n:
        ch = text[i]
        if ch == "\n":
            spans.append((start, i + 1))
            start = i + 1
            i += 1
            continue
        if ch not in _ENDS:
            i += 1
            continue
        j = i
        while j < n and (text[j] in _ENDS or text[j] in _CLOSERS):
            j += 1
        if j < n and not text[j].isspace():
            i = j
            continue
        if ch == "." and j == i + 1 and _abbreviation(text, i):
            i = j
            continue
        while j < n and text[j].isspace() and text[j] != "\n":
            j += 1
        if j < n and text[j] == "\n":
            j += 1  # the newline that ends the line belongs to its last sentence
        spans.append((start, j))
        start = j
        i = j
    if start < n:
        spans.append((start, n))
    return spans


# --- the claims M9 does not see: credentials, change words, decades -----------------------------
#
# A credential (a degree, a certification, an award, a school one graduated from), a change word
# that implies a metric ("doubled", "x10", "by half") and a duration in decades are claims that
# M9's rules leave alone. Each is sourced only when the person's own text states it: the same
# words in the profile or the base CV, folded. These scanners walk the words of the text once,
# each with a bounded look around (at most `_REACH` words), so they are linear.

_WORD_RE = re.compile(r"[^\W_]+")  # one class under one quantifier: linear
_REACH = 12


@dataclass(frozen=True, slots=True)
class _Word:
    start: int
    end: int
    folded: str
    capital: bool
    digits: bool


def _fold_word(word: str) -> str:
    return "".join(_fold_char(ch) for ch in word)


def _words(text: str) -> list[_Word]:
    out = []
    for found in _WORD_RE.finditer(text):
        word = found.group()
        out.append(
            _Word(
                found.start(),
                found.end(),
                _fold_word(word),
                word[0].isupper(),
                any(ch.isdigit() for ch in word),
            )
        )
    return out


def corpus(texts: Iterable[str]) -> tuple[str, ...]:
    """The person's texts as padded folded word lists, one entry per text, so that " a b " in
    an entry is a phrase search that never matches inside a word or across two texts."""
    return tuple(" " + " ".join(w.folded for w in _words(text)) + " " for text in texts if text)


def _stated(phrase: str, entries: Sequence[str]) -> bool:
    return any(f" {phrase} " in entry for entry in entries)


def corpus_states(text: str, entries: Sequence[str]) -> bool:
    """Do the person's texts (`corpus`) hold the words of `text`, folded, as a phrase?"""
    phrase = " ".join(w.folded for w in _words(text))
    return bool(phrase) and _stated(phrase, entries)


# A degree, a certification, an award. `_GENERIC` words name no credential by themselves ("a
# degree", "graduated"): only the names around them are checked. `_CAPITAL_ONLY` are keys only
# when capitalised ("Master", "Licence", "Prix"), as their lowercase forms are common words
# ("master branch", "software licence", "prix fixe").
_KEYS = frozenset(
    [
        "phd",
        "doctorate",
        "doctorat",
        "mba",
        "msc",
        "bsc",
        "meng",
        "mres",
        "master",
        "masters",
        "mastere",
        "bachelor",
        "bachelors",
        "licence",
        "bts",
        "dut",
        "deug",
        "ingenieur",
        "certified",
        "certification",
        "certifie",
        "certifiee",
        "award",
        "awards",
        "prize",
        "prix",
        "laureat",
        "laureate",
        "degree",
        "diploma",
        "diplome",
        "diplomee",
        "diplomes",
        "diplomees",
        "graduated",
        "graduate",
    ]
)
_GENERIC = frozenset(
    [
        "degree",
        "diploma",
        "diplome",
        "diplomee",
        "diplomes",
        "diplomees",
        "graduated",
        "graduate",
        "s",
    ]
)
_CAPITAL_ONLY = frozenset(
    [
        "master",
        "masters",
        "mastere",
        "licence",
        "bts",
        "dut",
        "deug",
        "ingenieur",
        "prix",
        "certified",
        "certifie",
        "certifiee",
    ]
)
# A key that names a thing with the lowercase word just before it ("hackathon award").
_GLUE_LEFT = frozenset(["certification", "award", "awards", "prize"])
# The words that join a credential to its field or its school ("MSc in Data Science from
# Exemple University", "diplômée de l'École Exemple").
_JOINS = frozenset(["in", "of", "from", "at", "en", "de", "du", "des", "d", "l", "la", "le", "the"])
_STOPS = frozenset(
    [
        "a",
        "an",
        "the",
        "my",
        "his",
        "her",
        "their",
        "our",
        "your",
        "and",
        "or",
        "with",
        "for",
        "to",
        "as",
        "on",
        "by",
        "by",
        "un",
        "une",
        "le",
        "la",
        "les",
        "mon",
        "ma",
        "mes",
        "son",
        "sa",
        "ses",
        "et",
        "ou",
        "avec",
        "pour",
        "par",
        "sur",
        "au",
        "aux",
    ]
)
_SENTENCE_STOPS = frozenset(".!?:;\n\u2022")


def _joined(text: str, left: _Word, right: _Word) -> bool:
    """Two words of one name: only spaces, an apostrophe or a hyphen between them."""
    gap = text[left.end : right.start]
    return len(gap) <= 3 and all(ch in " '\u2019-" for ch in gap)


def _is_key(word: _Word) -> bool:
    if word.folded not in _KEYS:
        return False
    return word.capital or word.folded not in _CAPITAL_ONLY


def _opens_sentence(text: str, word: _Word) -> bool:
    at = word.start
    while at > 0 and text[at - 1] == " ":
        at -= 1
    return at == 0 or text[at - 1] in _SENTENCE_STOPS


def _common_word(text: str, words: list[_Word], i: int) -> bool:
    """A key that is capitalised only because it opens the sentence, followed by a lowercase
    word that joins nothing: "Master data management", "Certified datasets for the audit"."""
    if not _opens_sentence(text, words[i]) or i + 1 >= len(words):
        return False
    after = words[i + 1]
    return not (after.capital or after.digits or after.folded in _JOINS or _is_key(after))


def _name_word(word: _Word) -> bool:
    return (word.capital or _is_key(word)) and not word.digits and word.folded not in _STOPS


def credentials(text: str, entries: Sequence[str]) -> list[tuple[int, int]]:
    """The spans of the credentials of `text` that the person's texts (`corpus`) do not state.

    A credential is a key word with the capitalised words around it ("AWS Certified Solutions
    Architect Associate"), then, through a joining word, its field or its school ("PhD in
    Computer Science", "MSc from Example Polytechnic", "Graduated from Stanford University").
    Each part but the generic words must stand in ONE of the person's texts: "MSc" and "Exemple
    University" in the school's entry. A capitalised word that opens the sentence ("Earned")
    may be left out of the first part."""
    words = _words(text)
    out: list[tuple[int, int]] = []
    i = 0
    n = len(words)
    while i < n:
        if not _is_key(words[i]):
            i += 1
            continue
        key = words[i]
        generic = key.folded in _GENERIC
        if key.folded in _CAPITAL_ONLY and _common_word(text, words, i):
            i += 1
            continue
        # Left: the capitalised words of the name, and one lowercase word before a key such as
        # "award" ("hackathon award").
        first = i
        while first > 0 and i - first < 4 and _joined(text, words[first - 1], words[first]):
            before = words[first - 1]
            if (
                before.folded == "s"
                or _name_word(before)
                or (first == i and key.folded in _GLUE_LEFT and before.folded not in _STOPS)
            ):
                first -= 1
            else:
                break
        # Right: the rest of the name, then joined parts.
        last = i
        while (
            last + 1 < n and last - first < _REACH and _joined(text, words[last], words[last + 1])
        ):
            after = words[last + 1]
            if not (after.folded == "s" or _name_word(after)):
                break
            last += 1
        parts = [words[first : last + 1]]
        while last + 2 < n and last - first < _REACH:
            j = last + 1
            if words[j].folded not in _JOINS or not _joined(text, words[last], words[j]):
                break
            if (
                j + 1 < n
                and words[j + 1].folded in _JOINS
                and _joined(text, words[j], words[j + 1])
            ):
                j += 1
            k = j + 1
            if k >= n or not _joined(text, words[j], words[k]):
                break
            part: list[_Word] = []
            while k < n and len(part) < 4 and (not part or _joined(text, words[k - 1], words[k])):
                word = words[k]
                lowercase_field = not generic and not word.digits
                if word.folded in _JOINS or word.folded in _STOPS:
                    break
                if not (_name_word(word) or lowercase_field):
                    break
                part.append(word)
                k += 1
            if not part:
                break
            parts.append(part)
            last = k - 1
        phrases = [" ".join(w.folded for w in part if w.folded not in _GENERIC) for part in parts]
        if any(phrases) and not _credential_stated(text, words[first], phrases, entries):
            out.append((words[first].start, words[last].end))
        i = last + 1
    return out


def _credential_stated(text: str, first: _Word, phrases: list[str], entries: Sequence[str]) -> bool:
    options = [phrases]
    head = phrases[0].split(" ", 1)
    if _opens_sentence(text, first) and first.folded == head[0] and len(head) == 2:
        options.append([head[1], *phrases[1:]])
    for option in options:
        needed = [phrase for phrase in option if phrase]
        if any(all(f" {phrase} " in entry for phrase in needed) for entry in entries):
            return True
    return False


# A change that implies a metric: "doubled the throughput" says by how much without a number.
_CHANGES = frozenset(
    [
        "doubled",
        "doubling",
        "tripled",
        "tripling",
        "quadrupled",
        "quadrupling",
        "halved",
        "halving",
        "tenfold",
        "twofold",
        "threefold",
        "decuple",
    ]
)
_CHANGES_ACCENTED = frozenset(("doublé", "doublée", "doublés", "triplé", "triplée", "triplés"))
_CHANGE_PAIRS = frozenset(
    (
        ("by", "half"),
        ("de", "moitie"),
        ("multiplied", "by"),
        ("divided", "by"),
        ("multiplie", "par"),
        ("multipliee", "par"),
        ("multiplies", "par"),
        ("divise", "par"),
        ("divisee", "par"),
    )
)


_NOT_FACTORS = frozenset(("x86", "x64", "x32"))


def _times(folded: str) -> bool:
    """ "x10", "2x", "10x": a factor ("x86" is an architecture)."""
    if len(folded) < 2 or len(folded) > 6 or folded in _NOT_FACTORS:
        return False
    if folded[0] == "x":
        return folded[1:].isdigit()
    return folded[-1] == "x" and folded[:-1].isdigit()


def changes(text: str, entries: Sequence[str]) -> list[tuple[int, int]]:
    """The spans of the change words of `text` that the person's texts do not state."""
    words = _words(text)
    out: list[tuple[int, int]] = []
    for i, word in enumerate(words):
        phrase = ""
        end = word.end
        original = text[word.start : word.end].lower()
        if word.folded in _CHANGES or _times(word.folded) or original in _CHANGES_ACCENTED:
            phrase = word.folded
        elif i + 1 < len(words) and (word.folded, words[i + 1].folded) in _CHANGE_PAIRS:
            phrase = f"{word.folded} {words[i + 1].folded}"
            end = words[i + 1].end
        if phrase and not _stated(phrase, entries):
            out.append((word.start, end))
    return out


_DECADES = {"decade": 10, "decades": 20, "decennie": 10, "decennies": 20}


def decades(text: str) -> list[tuple[int, int, int]]:
    """The durations in decades of `text`: `(start, end, the years they at least mean)`."""
    return [(w.start, w.end, _DECADES[w.folded]) for w in _words(text) if w.folded in _DECADES]
