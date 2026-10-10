"""The citation checker (SPEC-ingestion.md, "Citation check"; tasks/ingestion/todo.md, "Citations").

An extraction agent proposes a profile item: a value, and excerpts of the document it read.
`check_item` says whether the document supports the value. It finds each excerpt in the document,
takes the passage it quotes (the cited span, never cutting a word or a number) and checks every
part of the value against those passages by its field type: an address must be written there
exactly, a name as whole words, a date with its month next to its year, a phone number whole, and
every other number must be a number of the passage.

Pure: no database, no network, no model call, standard library only. It is called on hostile
input, a document and a model's output, so it never raises on any text, gives the same answer for
the same input, and its cost is bounded (figures in tasks/ingestion/todo.md, "Measurements"):

- `prepare(document)` is one pass over the text, O(n); the caller keeps the result for the step.
- `find` is one substring search of the document per excerpt, O(n), then at most OCCURRENCES_MAX
  occurrences are looked at, each in constant time.
- A cited span holds at most SPAN_MAX characters, so every value rule works on bounded text and
  costs O(length of the value).
- No regular expression here can backtrack: each is an alternation of plain classes, or a chain
  in which the next character decides alone which branch goes on.

    document = citations.prepare(text)                       # once per step
    result = citations.check_item(field_types, value, document, excerpts)
    if isinstance(result, citations.Refusal):
        index, rule, reason = result                         # nothing is stored
    else:
        result.citations, result.flags                       # offsets in the stored text
"""

from __future__ import annotations

import re
import unicodedata
from array import array
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from itertools import pairwise
from typing import Any, NamedTuple

__all__ = [
    "EXCERPTS_MAX",
    "EXCERPT_MAX",
    "EXCERPT_MIN",
    "EXCERPT_SOURCE_MAX",
    "INSTRUCTION_PATTERNS",
    "LOOKS_LIKE_INSTRUCTION",
    "MONTH_ABBREVIATIONS",
    "MONTH_NAMES",
    "OCCURRENCES_MAX",
    "REASONS",
    "SKELETON_MIN",
    "SPAN_MAX",
    "SPAN_SOURCE_MAX",
    "VALUE_LEAVES_MAX",
    "Accepted",
    "Citation",
    "DateFacts",
    "Document",
    "FieldType",
    "Normalised",
    "Refusal",
    "Rule",
    "Skeleton",
    "Span",
    "check_item",
    "check_value",
    "dates_of",
    "find",
    "is_token_boundary",
    "looks_like_instruction",
    "normalise",
    "numbers_of",
    "phones_of",
    "prepare",
    "readings",
    "rule_address",
    "rule_date",
    "rule_name",
    "rule_numbers",
    "rule_phone",
    "skeleton",
]

# --- limits ---------------------------------------------------------------------------------------

EXCERPT_MIN = 20  # characters of N(excerpt); a shorter document is cited whole
EXCERPT_MAX = 600  # characters of N(excerpt)
SKELETON_MIN = 12  # characters of S(excerpt)
EXCERPT_SOURCE_MAX = 6_000  # characters of an excerpt as typed: a longer one is refused unread
EXCERPTS_MAX = 20  # excerpts of one item
OCCURRENCES_MAX = 64  # occurrences of an excerpt looked at for one that cuts no token
SPAN_MAX = 2_000  # characters of a cited span in N(document), tokens extended
SPAN_SOURCE_MAX = 8_000  # characters of the document under a cited span
VALUE_LEAVES_MAX = 2_000  # scalars of one value
CLUSTER_MAX = 32  # characters of one cluster: a longer run of combining marks is cut there
SHORT_NAME = 4  # a name whose skeleton is shorter keeps its capitals
PHONE_DIGITS_MIN = 6
CHAIN_LINKS_MAX = 12  # months of one range or list sharing a year

# --- field types, rules and reasons ---------------------------------------------------------------


class FieldType(StrEnum):
    """What a path of a profile section holds (the table of `app/ingestion/items.py`, task G5)."""

    EMAIL = "email"
    URL = "url"
    NAME = "name"
    DATE = "date"
    PHONE = "phone"
    TEXT = "text"
    TERM = "term"
    ENUM = "enum"
    SETTING = "setting"


class Rule(StrEnum):
    """The machine readable code of a refusal."""

    NO_CITATION = "no_citation"
    TOO_MANY_CITATIONS = "too_many_citations"
    EXCERPT_NOT_TEXT = "excerpt_not_text"
    EXCERPT_TOO_SHORT = "excerpt_too_short"
    EXCERPT_TOO_LONG = "excerpt_too_long"
    EXCERPT_TOO_THIN = "excerpt_too_thin"
    EXCERPT_NOT_WHOLE_DOCUMENT = "excerpt_not_whole_document"
    EXCERPT_NOT_FOUND = "excerpt_not_found"
    SPAN_TOO_LONG = "span_too_long"
    SETTING = "setting"
    UNKNOWN_FIELD = "unknown_field"
    VALUE_TOO_LARGE = "value_too_large"
    VALUE_NOT_TEXT = "value_not_text"
    VALUE_EMPTY = "value_empty"
    ADDRESS_NOT_IN_SOURCE = "address_not_in_source"
    NAME_NOT_IN_SOURCE = "name_not_in_source"
    DATE_FORMAT = "date_format"
    YEAR_NOT_IN_SOURCE = "year_not_in_source"
    MONTH_NOT_IN_SOURCE = "month_not_in_source"
    MONTH_APART_FROM_YEAR = "month_apart_from_year"
    TWO_DIGIT_YEAR = "two_digit_year"
    PHONE_TOO_SHORT = "phone_too_short"
    PHONE_NOT_IN_SOURCE = "phone_not_in_source"
    NUMBER_NOT_IN_SOURCE = "number_not_in_source"


# One sentence per rule, shown to the agent and to the user. Constants: a reason never quotes
# the document, the excerpt or the value.
REASONS: Mapping[Rule, str] = {
    Rule.NO_CITATION: "no excerpt: every item cites at least one excerpt of the document",
    Rule.TOO_MANY_CITATIONS: f"too many excerpts: an item cites at most {EXCERPTS_MAX}",
    Rule.EXCERPT_NOT_TEXT: "an excerpt is text",
    Rule.EXCERPT_TOO_SHORT: f"excerpt under {EXCERPT_MIN} characters",
    Rule.EXCERPT_TOO_LONG: f"excerpt over {EXCERPT_MAX} characters",
    Rule.EXCERPT_TOO_THIN: f"excerpt holds under {SKELETON_MIN} letters or digits",
    Rule.EXCERPT_NOT_WHOLE_DOCUMENT: "excerpt must be the whole document",
    Rule.EXCERPT_NOT_FOUND: "excerpt not found",
    Rule.SPAN_TOO_LONG: "the excerpt covers too long a passage of the document, quote less",
    Rule.SETTING: "not proposable by the agent",
    Rule.UNKNOWN_FIELD: "the value holds a field that cannot be checked",
    Rule.VALUE_TOO_LARGE: "the value holds too many parts to check",
    Rule.VALUE_NOT_TEXT: "the value must be text",
    Rule.VALUE_EMPTY: "the value is empty",
    Rule.ADDRESS_NOT_IN_SOURCE: "the address is not written in an excerpt",
    Rule.NAME_NOT_IN_SOURCE: "the name is not written in an excerpt as whole words",
    Rule.DATE_FORMAT: "a date is YYYY or YYYY-MM",
    Rule.YEAR_NOT_IN_SOURCE: "year not in the source",
    Rule.MONTH_NOT_IN_SOURCE: "month not in the source, propose the year alone",
    Rule.MONTH_APART_FROM_YEAR: (
        "a line break or a wide gap stands between the month and its year, propose the year alone"
    ),
    Rule.TWO_DIGIT_YEAR: "two digit year, not read",
    Rule.PHONE_TOO_SHORT: f"a phone number has at least {PHONE_DIGITS_MIN} digits",
    Rule.PHONE_NOT_IN_SOURCE: "the digits are not a phone number of an excerpt",
    Rule.NUMBER_NOT_IN_SOURCE: "a number of the value is not a number of an excerpt",
}


class Refusal(NamedTuple):
    """Why an item is not stored. `index` says what broke the rule: `excerpts[1]` for an excerpt,
    `value`, `value.start` or `value.terms[3]` for a part of the value. It is built from the
    caller's own field names and from positions, never from the model's text."""

    index: str
    rule: Rule
    reason: str


def _refuse(rule: Rule, index: str = "") -> Refusal:
    return Refusal(index, rule, REASONS[rule])


class Citation(NamedTuple):
    """What is stored for one excerpt: `start` and `end` are offsets in the document's text as
    stored, and `excerpt` is that slice, the document's own words, tokens whole."""

    start: int
    end: int
    excerpt: str


class Accepted(NamedTuple):
    """The citations to store, in the order of the excerpts, and the flags of the item."""

    citations: tuple[Citation, ...]
    flags: tuple[str, ...]


# --- normalisation N ------------------------------------------------------------------------------

_APOSTROPHES = "\u2018\u2019\u201a\u201b\u2032\u02bc`\u00b4\uff07"
_QUOTES = "\u201c\u201d\u201e\u201f\u2033\u00ab\u00bb\uff02"
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
_FOLDS = str.maketrans(
    {
        **dict.fromkeys(_APOSTROPHES, "'"),
        **dict.fromkeys(_QUOTES, '"'),
        **dict.fromkeys(_DASHES, "-"),
    }
)
_LINE_SEPARATORS = "\n\u2028\u2029"
# Printable ASCII but the space and the grave accent: N of such a run is the run in lower case.
_PLAIN = re.compile(r"[!-_a-~]+")


class Normalised(NamedTuple):
    """N(text), and for each character of it the index in `text` of the cluster it came from."""

    text: str
    origin: array[int]


class Skeleton(NamedTuple):
    """S: N without spaces, breaks and hyphens, and for each character its index in N."""

    text: str
    index: array[int]


def _extends(char: str) -> bool:
    """Whether `char` belongs to the cluster of the character before it: a combining mark, or
    the vowel or final consonant of a Hangul syllable written in parts."""
    return unicodedata.category(char)[0] == "M" or "\u1160" <= char <= "\u11ff"


def _cluster_end(text: str, start: int) -> int:
    end = start + 1
    stop = min(len(text), start + CLUSTER_MAX)
    while end < stop and text[end] > "\u02ff" and _extends(text[end]):
        end += 1
    return end


def _invisible(char: str, category: str) -> bool:
    """Format characters (soft hyphen, zero width, bidi controls, tags), variation selectors and
    the grapheme joiner: nothing a reader sees."""
    return (
        category == "Cf"
        or "\ufe00" <= char <= "\ufe0f"
        or "\U000e0100" <= char <= "\U000e01ef"
        or char == "\u034f"
    )


def _fold(cluster: str, casefold: bool) -> str:
    """N of one cluster. Whitespace comes out as a space or, for a break, a line feed."""
    kept = []
    for char in cluster.translate(_FOLDS):
        category = unicodedata.category(char)
        if category == "Cc":
            kept.append("\n")
        elif not _invisible(char, category):
            kept.append(char)
    # The folds again after NFKC, for the compatibility forms of the characters they fold.
    text = unicodedata.normalize("NFKC", "".join(kept)).translate(_FOLDS)
    if casefold:
        text = text.casefold()
    out = []
    for char in text:
        if char.isdecimal():
            out.append(str(unicodedata.decimal(char)))
        elif char.isspace():
            out.append("\n" if char in _LINE_SEPARATORS else " ")
        else:
            out.append(char)
    if out and not cluster[0].isdecimal():
        # A superscript, a fraction or a circled number is not a digit of the number beside it.
        if out[0].isdecimal():
            out.insert(0, "\n")
        if out[-1].isdecimal():
            out.append("\n")
    return "".join(out)


def normalise(text: str, *, casefold: bool = True) -> Normalised:
    """N(text), cluster by cluster (a character with the combining marks that follow it):

    1. apostrophes, quotes and dashes fold to their ASCII form, before NFKC can consume them;
    2. invisible characters go: format characters and variation selectors;
    3. NFKC, and every decimal digit of any script becomes its ASCII digit. A digit that comes
       from another character (a superscript, a fraction) is fenced by breaks;
    4. case fold (skipped with `casefold=False`, for names of fewer than four characters);
    5. whitespace: a run that holds a control character or a line separator, or that is three
       characters long or more, becomes one line feed, a break. Any other run becomes one space.
       None at the ends.
    """
    pieces: list[str] = []
    origin = array("I")
    cache: dict[str, str] = {}
    pending = 0  # whitespace characters since the last character kept
    hard = False  # that run holds a break
    pending_at = 0
    position, length = 0, len(text)
    while position < length:
        plain = _PLAIN.match(text, position)
        if plain is not None:
            end = plain.end()
            if end < length and text[end] > "\u02ff" and _extends(text[end]):
                end -= 1  # the last character carries a mark: a cluster of its own
            if end > position:
                if pending:
                    if pieces:
                        pieces.append("\n" if hard or pending >= 3 else " ")
                        origin.append(pending_at)
                    pending, hard = 0, False
                run = text[position:end]
                pieces.append(run.lower() if casefold else run)
                origin.extend(range(position, end))
                position = end
                continue
        end = _cluster_end(text, position)
        cluster = text[position:end]
        folded = cache.get(cluster)
        if folded is None:
            folded = cache[cluster] = _fold(cluster, casefold)
        for char in folded:
            if char in " \n":
                if not pending:
                    pending_at = position
                pending += 1
                hard = hard or char == "\n"
                continue
            if pending:
                if pieces:
                    pieces.append("\n" if hard or pending >= 3 else " ")
                    origin.append(pending_at)
                pending, hard = 0, False
            pieces.append(char)
            origin.append(position)
        position = end
    return Normalised("".join(pieces), origin)


_KEPT = re.compile(r"[^ \n-]+")


def skeleton(norm: str) -> Skeleton:
    """S of a normalised text: what is left once spaces, breaks and hyphens are gone, so that
    "full-\\nstack", "full-stack" and "fullstack" are the same."""
    pieces: list[str] = []
    index = array("I")
    for run in _KEPT.finditer(norm):
        pieces.append(run.group())
        index.extend(range(run.start(), run.end()))
    return Skeleton("".join(pieces), index)


@dataclass(frozen=True, slots=True, eq=False)
class Document:
    """A document prepared once for every check of a step: N, S and their maps."""

    source: str
    text: str
    origin: array[int]
    skeleton: str
    skeleton_index: array[int]


def prepare(text: str) -> Document:
    """The document ready to be searched. One pass, O(length); keep it for the step."""
    norm = normalise(text)
    bones = skeleton(norm.text)
    return Document(text, norm.text, norm.origin, bones.text, bones.index)


# --- tokens ---------------------------------------------------------------------------------------

# Scripts written without spaces between words: Thai and Lao, Tibetan, Myanmar, Khmer, the CJK
# radicals, the iteration marks, Hiragana and Katakana, the Han ideographs.
_UNSPACED = (
    (0x0E00, 0x0EFF),
    (0x0F00, 0x0FFF),
    (0x1000, 0x109F),
    (0x1780, 0x17FF),
    (0x2E80, 0x2FDF),
    (0x3005, 0x3007),
    (0x3040, 0x30FF),
    (0x31F0, 0x31FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x323AF),
)
_NUMBER_SEPARATORS = " .,'"


def _unspaced(char: str) -> bool:
    if char < "\u0e00":
        return False
    code = ord(char)
    return any(low <= code <= high for low, high in _UNSPACED)


def _mark(char: str) -> bool:
    return char > "\u02ff" and unicodedata.category(char)[0] == "M"


def _digit(char: str) -> bool:
    return "0" <= char <= "9"


def _inside(norm: str, at: int) -> bool:
    """Whether a cut between `norm[at - 1]` and `norm[at]` falls inside a token. A token is a
    maximal run of letters with their marks, or a number run: digits that may hold, between two
    digits, one single space, full stop, comma or apostrophe. In a script written without spaces
    every character is a token of its own: nothing there says where a word ends."""
    before, after = norm[at - 1], norm[at]
    if _digit(after):
        if _digit(before):
            return True
        return before in _NUMBER_SEPARATORS and at >= 2 and _digit(norm[at - 2])
    if _digit(before):
        return after in _NUMBER_SEPARATORS and at + 1 < len(norm) and _digit(norm[at + 1])
    if _mark(after):
        return before.isalpha() or _mark(before)
    if after.isalpha() and (before.isalpha() or _mark(before)):
        return not (_unspaced(before) or _unspaced(after))
    return False


def is_token_boundary(norm: str, at: int) -> bool:
    """Whether a span of the normalised text `norm` may start or end at `at` without cutting a
    word or a number."""
    return at <= 0 or at >= len(norm) or not _inside(norm, at)


# --- finding an excerpt ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Span:
    """The passage of a document that an excerpt cites: `start` and `end` in N(document), on
    token boundaries. Every value rule reads `text`, never what the agent typed."""

    document: Document
    start: int
    end: int

    @property
    def text(self) -> str:
        return self.document.text[self.start : self.end]

    @property
    def source_start(self) -> int:
        return self.document.origin[self.start]

    @property
    def source_end(self) -> int:
        return _cluster_end(self.document.source, self.document.origin[self.end - 1])

    def source(self, start: int | None = None, end: int | None = None) -> str:
        """The document's own text under the span, or under `text[start:end]` of it."""
        first = self.start if start is None else self.start + start
        last = self.end if end is None else self.start + end
        document = self.document
        return document.source[
            document.origin[first] : _cluster_end(document.source, document.origin[last - 1])
        ]


def _widen(norm: str, start: int, end: int) -> tuple[int, int] | None:
    """`start` and `end` moved outwards to the boundaries of the tokens they fall in, or None
    when that takes the span over SPAN_MAX."""
    budget = SPAN_MAX - (end - start)
    while start > 0 and _inside(norm, start):
        start -= 1
        budget -= 1
        if budget < 0:
            return None
    while end < len(norm) and _inside(norm, end):
        end += 1
        budget -= 1
        if budget < 0:
            return None
    return start, end


def find(document: str | Document, excerpt: str) -> Span | Refusal:
    """The cited span of `excerpt` in `document`, or the rule it breaks.

    The excerpt matches where its skeleton is a substring of the document's, which forgives
    hyphenation and line breaks on both sides. Among its first OCCURRENCES_MAX occurrences the
    first that starts and ends on token boundaries is taken; when none does, the first
    occurrence is taken and widened to the boundaries of the tokens it cuts. O(length of the
    document) for the search, constant work per occurrence."""
    prepared = prepare(document) if isinstance(document, str) else document
    if not isinstance(excerpt, str):
        return _refuse(Rule.EXCERPT_NOT_TEXT)
    if len(excerpt) > EXCERPT_SOURCE_MAX:
        return _refuse(Rule.EXCERPT_TOO_LONG)
    wanted = normalise(excerpt).text
    needle = skeleton(wanted).text
    norm = prepared.text
    if len(norm) < EXCERPT_MIN:
        if not needle or needle != prepared.skeleton:
            return _refuse(Rule.EXCERPT_NOT_WHOLE_DOCUMENT)
        chosen: tuple[int, int] | None = (0, len(norm))
    else:
        if len(wanted) < EXCERPT_MIN:
            return _refuse(Rule.EXCERPT_TOO_SHORT)
        if len(wanted) > EXCERPT_MAX:
            return _refuse(Rule.EXCERPT_TOO_LONG)
        if len(needle) < SKELETON_MIN:
            return _refuse(Rule.EXCERPT_TOO_THIN)
        haystack, index = prepared.skeleton, prepared.skeleton_index
        at = haystack.find(needle)
        if at < 0:
            return _refuse(Rule.EXCERPT_NOT_FOUND)
        first: tuple[int, int] | None = None
        chosen = None
        for _ in range(OCCURRENCES_MAX):
            start, end = index[at], index[at + len(needle) - 1] + 1
            if is_token_boundary(norm, start) and is_token_boundary(norm, end):
                chosen = (start, end)
                break
            if first is None:
                first = (start, end)
            at = haystack.find(needle, at + 1)
            if at < 0:
                break
        if chosen is None and first is not None:
            chosen = _widen(norm, *first)
    if chosen is None or chosen[1] - chosen[0] > SPAN_MAX:
        return _refuse(Rule.SPAN_TOO_LONG)
    span = Span(prepared, *chosen)
    if span.source_end - span.source_start > SPAN_SOURCE_MAX:
        return _refuse(Rule.SPAN_TOO_LONG)
    return span


# --- rule N: numbers ------------------------------------------------------------------------------

_NUMBER_RUN = re.compile(r"[0-9]+(?:[ .,'][0-9]+)*")
_SEPARATOR = re.compile(r"[ .,']")
# "version 1.10", "v1.10", "v. 2", "Version : 3.1": the run that follows is compared as text.
_VERSION_BEFORE = re.compile(r"(?<![^\W\d_])(?:versions?|v) ?[.:]? ?\Z")
_VERSION_WINDOW = 14


def _decimal(whole: str, fraction: str = "") -> str:
    """A number as a canonical decimal string: digits are compared, never floats, so a number
    of any length keeps every digit."""
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


def readings(run: str) -> frozenset[str]:
    """The numbers a number run may stand for, as canonical decimal strings:

    a. no separator: the integer;
    b. every separator is the same character and is followed by exactly three digits, the first
       group having one to three digits and not starting with 0: the grouped integer
       ("10 000", "10,000" and "10.000" read 10000 and nothing else);
    c. exactly one separator, a full stop or a comma, followed by one or two digits, or by any
       number of digits when the integer part is 0: the decimal ("3,5", "0,125");
    d. groups as in b, then one different separator among full stop and comma, then digits:
       the grouped integer with its decimals ("1 234,56");
    e. only when none of b, c and d applies: each piece is a number of its own ("2019 2021").
    """
    if _NUMBER_RUN.fullmatch(run) is None:
        return frozenset()
    parts = _SEPARATOR.split(run)
    separators = _SEPARATOR.findall(run)
    if not separators:
        return frozenset({_decimal(run)})
    head = len(parts[0]) <= 3 and not parts[0].startswith("0")
    if head and len(set(separators)) == 1 and all(len(part) == 3 for part in parts[1:]):
        return frozenset({_decimal("".join(parts))})
    if len(separators) == 1 and separators[0] in ".," and (len(parts[1]) <= 2 or parts[0] == "0"):
        return frozenset({_decimal(parts[0], parts[1])})
    if (
        len(separators) >= 2
        and separators[-1] in ".,"
        and separators[-1] != separators[0]
        and len(set(separators[:-1])) == 1
        and head
        and all(len(part) == 3 for part in parts[1:-1])
    ):
        return frozenset({_decimal("".join(parts[:-1]), parts[-1])})
    return frozenset(_decimal(part) for part in parts)


def _number_runs(norm: str) -> Iterator[tuple[str, bool]]:
    """Each number run of a normalised text, and whether it is written as a version."""
    for run in _NUMBER_RUN.finditer(norm):
        before = norm[max(0, run.start() - _VERSION_WINDOW) : run.start()]
        yield run.group(), _VERSION_BEFORE.search(before) is not None


def numbers_of(norm: str) -> frozenset[str]:
    """Every reading of every number run of a normalised text."""
    out: set[str] = set()
    for run, _ in _number_runs(norm):
        out |= readings(run)
    return frozenset(out)


# --- rule D: dates --------------------------------------------------------------------------------

# Month words are compared without accents: "février" is looked up as "fevrier".
MONTH_NAMES: Mapping[str, int] = {
    "january": 1,
    "janvier": 1,
    "february": 2,
    "fevrier": 2,
    "march": 3,
    "mars": 3,
    "april": 4,
    "avril": 4,
    "may": 5,
    "mai": 5,
    "june": 6,
    "juin": 6,
    "july": 7,
    "juillet": 7,
    "august": 8,
    "aout": 8,
    "september": 9,
    "septembre": 9,
    "october": 10,
    "octobre": 10,
    "november": 11,
    "novembre": 11,
    "december": 12,
    "decembre": 12,
}
# Only an abbreviation may carry a full stop: "janv. 2023" is January, "may. 2023" is not May.
MONTH_ABBREVIATIONS: Mapping[str, int] = {
    "jan": 1,
    "janv": 1,
    "feb": 2,
    "fev": 2,
    "fevr": 2,
    "mar": 3,
    "apr": 4,
    "avr": 4,
    "jun": 6,
    "jul": 7,
    "juil": 7,
    "juill": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_RANGE_WORDS = frozenset({"a", "au", "to", "until", "till", "through", "-", "\u2192"})
_LIST_WORDS = frozenset({"et", "and", "/", ","})
_ORDINALS = frozenset({"er", "st", "nd", "rd", "th"})
_PRESENT_WORDS = frozenset(
    {
        "present",
        "today",
        "now",
        "current",
        "currently",
        "ongoing",
        "maintenant",
        "actuel",
        "actuellement",
    }
)
_PRESENT_PHRASES = (("aujourd", "'", "hui"), ("ce", "jour"), ("en", "cours"))
_DATE_TOKEN = re.compile(r"[0-9]+|\n|[^\W\d_]+|\S")
_DATE_VALUE = re.compile(r"([0-9]{4})(?:-(0[1-9]|1[0-2]))?")


class _Token(NamedTuple):
    kind: str  # "w" a word without its accents, "d" digits, "b" a break, "p" one other character
    text: str
    glued: bool  # no space before it


class _Atom(NamedTuple):
    """A month and a year written as numbers: "01/2023", "2023-01", "9/2021", "09/21"."""

    start: int  # index of its first token
    end: int  # index after its last token
    year: int  # two digits when `kind` is "short"
    month: int
    kind: str  # "firm", "one_digit", "ambiguous" or "short"


@dataclass(frozen=True, slots=True)
class DateFacts:
    """What cited text says about dates. `years` are the runs of exactly four digits, `pairs`
    the (year, month) whose month stands next to its year, `short` the (two digit year, month)
    never read as a year on their own, `implied` the years written by their last two digits
    after a full year ("2019-20"), and `apart` the (year, month) that a break alone separates."""

    years: frozenset[int]
    pairs: frozenset[tuple[int, int]]
    short: frozenset[tuple[int, int]]
    implied: frozenset[int]
    apart: frozenset[tuple[int, int]]


@lru_cache(maxsize=4096)
def _unaccent_char(char: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", char) if not unicodedata.combining(c))


def _unaccent(text: str) -> str:
    """`text` without its accents, one character at a time: a normalisation of the whole string
    would sort a long run of combining marks, which is quadratic."""
    return text if text.isascii() else "".join(_unaccent_char(char) for char in text)


def _date_tokens(norm: str) -> list[_Token]:
    tokens = []
    for found in _DATE_TOKEN.finditer(norm):
        text = found.group()
        glued = found.start() > 0 and norm[found.start() - 1] not in " \n"
        if _digit(text[0]):
            tokens.append(_Token("d", text, glued))
        elif text == "\n":
            tokens.append(_Token("b", text, glued))
        elif text[0].isalpha():
            tokens.append(_Token("w", _unaccent(text), glued))
        else:
            tokens.append(_Token("p", text, glued))
    return tokens


class _DateReader:
    """Reads the dates of one cited span. Linear in its tokens: every look ahead is bounded."""

    def __init__(self, norm: str) -> None:
        self.tokens = _date_tokens(norm)
        self.years: set[int] = set()
        self.pairs: set[tuple[int, int]] = set()
        self.short: set[tuple[int, int]] = set()
        self.implied: set[int] = set()
        self.apart: set[tuple[int, int]] = set()

    def facts(self) -> DateFacts:
        tokens = self.tokens
        self.years = {int(t.text) for t in tokens if t.kind == "d" and len(t.text) == 4}
        for at in range(len(tokens)):
            self._month_word(at)
        self._numeric()
        return DateFacts(
            frozenset(self.years),
            frozenset(self.pairs),
            frozenset(self.short),
            frozenset(self.implied),
            frozenset(self.apart),
        )

    # -- the pieces of a written date

    def _is(self, at: int, text: str) -> bool:
        return at < len(self.tokens) and self.tokens[at].text == text

    def _digits(self, at: int, size: int) -> bool:
        tokens = self.tokens
        return at < len(tokens) and tokens[at].kind == "d" and len(tokens[at].text) == size

    def _month(self, at: int) -> tuple[int, int] | None:
        """The month a word names at `at` and the index after it. Only an abbreviation takes its
        full stop along."""
        tokens = self.tokens
        if at >= len(tokens) or tokens[at].kind != "w":
            return None
        word = tokens[at].text
        if word in MONTH_NAMES:
            return MONTH_NAMES[word], at + 1
        if word in MONTH_ABBREVIATIONS:
            dotted = self._is(at + 1, ".") and tokens[at + 1].glued
            return MONTH_ABBREVIATIONS[word], at + (2 if dotted else 1)
        return None

    def _day(self, at: int) -> int:
        """How many tokens a day of the month takes at `at` ("15", "1er", "3rd"), or 0."""
        tokens = self.tokens
        if at >= len(tokens) or tokens[at].kind != "d" or len(tokens[at].text) > 2:
            return 0
        if not 1 <= int(tokens[at].text) <= 31:
            return 0
        after = at + 1
        ordinal = after < len(tokens) and tokens[after].glued and tokens[after].text in _ORDINALS
        return 2 if ordinal else 1

    def _connector(self, at: int, *, lists: bool) -> int:
        """How many tokens a connector takes at `at`, or 0: the words and signs of a range, with
        `lists` those of a list too."""
        tokens = self.tokens
        if at >= len(tokens):
            return 0
        text = tokens[at].text
        if text in _RANGE_WORDS or (lists and text in _LIST_WORDS):
            return 1
        if text == "jusqu" and self._is(at + 1, "'") and at + 2 < len(tokens):
            return 3 if tokens[at + 2].text in ("a", "au", "en") else 0
        return 0

    def _present(self, at: int) -> bool:
        tokens = self.tokens
        if at < len(tokens) and tokens[at].kind == "w" and tokens[at].text in _PRESENT_WORDS:
            return True
        return any(
            all(self._is(at + offset, part) for offset, part in enumerate(phrase))
            for phrase in _PRESENT_PHRASES
        )

    def _year_after(self, at: int, *, across_breaks: bool = False) -> int:
        """The index of the year that the month word ending before `at` belongs to, or -1:
        an optional day ("15", "the 3rd"), at most one comma, an optional "of", then four
        digits."""
        tokens = self.tokens

        def skip(index: int) -> int:
            while across_breaks and index < len(tokens) and tokens[index].kind == "b":
                index += 1
            return index

        at = skip(at)
        day_at = skip(at + 1) if self._is(at, "the") else at
        day = self._day(day_at)
        if day:
            at = skip(day_at + day)
        if self._is(at, ","):
            at = skip(at + 1)
        if self._is(at, "of"):
            at = skip(at + 1)
        return at if self._digits(at, 4) else -1

    # -- month words

    def _month_word(self, at: int) -> None:
        found = self._month(at)
        if found is None:
            return
        tokens = self.tokens
        month, after = found
        year_at = self._year_after(after)
        if year_at >= 0:
            # "sept 2021 collaborateurs" is seven of them, not a month.
            bare_sept = tokens[at].text == "sept" and year_at == at + 1
            counted = (
                year_at + 1 < len(tokens)
                and tokens[year_at + 1].kind == "w"
                and not self._connector(year_at + 1, lists=True)
                and not self._present(year_at + 1)
            )
            if not (bare_sept and counted):
                self.pairs.add((int(tokens[year_at].text), month))
            return
        if self._is(after, "'") and self._digits(after + 1, 2) and tokens[after + 1].glued:
            self.short.add((int(tokens[after + 1].text), month))  # "Jan '23"
            return
        broken = self._year_after(after, across_breaks=True)
        if broken >= 0:
            self.apart.add((int(tokens[broken].text), month))
        shared = self._shared_year(after, month)
        if shared is not None:
            self.pairs.add((shared, month))

    def _shared_year(self, at: int, month: int) -> int | None:
        """The year a month takes from the months that follow it in a range or a list: "juin à
        août 2019", "du 1er juin au 31 août 2019", "juin, juillet et août 2019". The chain must
        go forward in the calendar: "novembre à février 2023" starts in 2022."""
        previous = month
        at += self._day(at)  # "June 1st to August 31st, 2019"
        for _ in range(CHAIN_LINKS_MAX):
            step = self._connector(at, lists=True)
            if not step:
                return None
            at += step
            day_at = at + 1 if self._is(at, "the") else at
            day = self._day(day_at)
            if day:
                at = day_at + day
                if self._is(at, "of"):
                    at += 1
            found = self._month(at)
            if found is None:
                return None
            following, at = found
            if following < previous:
                return None
            previous = following
            year_at = self._year_after(at)
            if year_at >= 0:
                return int(self.tokens[year_at].text)
            at += self._day(at)
        return None

    # -- dates written as numbers

    def _numeric(self) -> None:
        tokens = self.tokens
        atoms: list[_Atom] = []
        at = 0
        while at < len(tokens):
            token = tokens[at]
            if token.kind != "d" or (token.glued and tokens[at - 1].kind == "w"):
                at += 1
                continue
            parts = [token.text]
            separator = ""
            end = at + 1
            while (
                end + 1 < len(tokens)
                and tokens[end].text in ("/", ".", "-")
                and tokens[end].glued
                and tokens[end + 1].kind == "d"
                and tokens[end + 1].glued
                and separator in ("", tokens[end].text)
            ):
                separator = tokens[end].text
                parts.append(tokens[end + 1].text)
                end += 2
            atom = self._atom(at, end, parts, separator)
            if atom is not None:
                atoms.append(atom)
                if atom.kind == "firm":
                    self.pairs.add((atom.year, atom.month))
            at = end
        self._ranges(atoms)

    def _atom(self, start: int, end: int, parts: list[str], separator: str) -> _Atom | None:
        sizes = [len(part) for part in parts]
        if max(sizes) > 4:
            return None
        numbers = [int(part) for part in parts]
        if sizes == [2, 4] and 1 <= numbers[0] <= 12:  # MM/YYYY, MM.YYYY, MM-YYYY
            return _Atom(start, end, numbers[1], numbers[0], "firm")
        if sizes == [1, 4] and numbers[0] >= 1:  # a month only inside a range: "9/2021 to present"
            return _Atom(start, end, numbers[1], numbers[0], "one_digit")
        if sizes == [4, 2] and separator in ("-", "/"):
            year, second = numbers
            if second > year % 100:  # "2019-20", "2010-11": it can be the year that follows
                self.implied.add(year - year % 100 + second)
            elif year % 100 == 99 and second == 0:
                self.implied.add(year + 1)
            if 1 <= second <= 12:
                kind = "firm" if second <= year % 100 else "ambiguous"
                return _Atom(start, end, year, second, kind)
            return None
        if sizes == [2, 2] and separator == "/" and 1 <= numbers[0] <= 12:
            return _Atom(start, end, numbers[1], numbers[0], "short")
        if sizes == [4, 2, 2] and 1 <= numbers[1] <= 12 and 1 <= numbers[2] <= 31:
            self.pairs.add((numbers[0], numbers[1]))  # YYYY-MM-DD
            return None
        if len(parts) == 3 and sizes[2] == 4 and sizes[0] <= 2 and sizes[1] <= 2:
            first, second, year = numbers
            if 12 < first <= 31 and 1 <= second <= 12:
                self.pairs.add((year, second))  # the day first
            elif 12 < second <= 31 and 1 <= first <= 12 and separator != ".":
                self.pairs.add((year, first))  # the month first
            elif 1 <= first <= 12 and 1 <= second <= 12 and (separator == "." or first == second):
                self.pairs.add((year, second))  # dotted dates are day first
            # else two numbers up to 12 with a slash or a hyphen: no telling, no month
        return None

    def _ranges(self, atoms: list[_Atom]) -> None:
        """The forms that count only inside a range of dates: a month in one digit, a
        `YYYY-MM` whose two digits could be a year, a two digit year. Both ends are a month and
        a year, the second later than the first, or the second end is the present."""
        for first, second in pairwise(atoms):
            step = self._connector(first.end, lists=False)
            if not step or first.end + step != second.start or not _later(first, second):
                continue
            if _school_year(first) and _school_year(second):
                continue  # "2009-10 à 2010-11" are two school years
            self._inside_range(first)
            self._inside_range(second)
        for atom in atoms:
            step = self._connector(atom.end, lists=False)
            if step and self._present(atom.end + step):
                self._inside_range(atom)

    def _inside_range(self, atom: _Atom) -> None:
        if atom.kind == "short":
            self.short.add((atom.year, atom.month))
        else:
            self.pairs.add((atom.year, atom.month))


def _later(first: _Atom, second: _Atom) -> bool:
    if first.kind == "short" or second.kind == "short":
        return (first.year % 100, first.month) < (second.year % 100, second.month)
    return (first.year, first.month) < (second.year, second.month)


def _school_year(atom: _Atom) -> bool:
    return atom.kind == "ambiguous" and atom.month == (atom.year + 1) % 100


def dates_of(norm: str) -> DateFacts:
    """What a normalised text says about dates (rule D). A month word belongs to the year that
    follows it, never to a year written before it, and nothing pairs across a break."""
    return _DateReader(norm).facts()


# --- rule P: phones -------------------------------------------------------------------------------

# Digits with spaces, full stops, hyphens, slashes and parentheses between them, an optional
# plus sign first. A space is the only way into the repeated group, so nothing backtracks.
_PHONE_RUN = re.compile(r"\+?\(?[0-9][0-9().\-/]*(?: [0-9(][0-9().\-/]*)*")
# A French number is ten digits, written in pairs, or 33 and nine digits after the plus sign:
# when more digits follow on the line, they are another number ("06 39 98 12 34 12 rue").
_FRENCH = (
    re.compile(r"0[1-9](?:[ .-]?[0-9]{2}){4}(?= )"),
    re.compile(r"\+33[ .-]?(?:\(0\)[ .-]?|0)?[1-9](?:[ .-]?[0-9]{2}){4}(?= )"),
)
_NOT_DIGIT = re.compile(r"[^0-9]")


def phones_of(norm: str) -> frozenset[str]:
    """The digits of every phone number of a normalised text. A break ends a phone number."""
    out: set[str] = set()
    for run in _PHONE_RUN.finditer(norm):
        text = run.group()
        out.add(_NOT_DIGIT.sub("", text))
        for pattern in _FRENCH:
            head = pattern.match(text)
            if head is not None:
                out.add(_NOT_DIGIT.sub("", head.group()))
    return frozenset(out)


# --- instruction patterns -------------------------------------------------------------------------

LOOKS_LIKE_INSTRUCTION = "looks_like_instruction"
# Compared without accents and case, as whole words. A hint for the user, not a control.
INSTRUCTION_PATTERNS = (
    "ignore previous",
    "ignore all previous",
    "ignore the previous",
    "ignore les instructions",
    "ignorez",
    "system prompt",
    "you are an ai",
    "as an ai",
    "tu es une ia",
    "vous etes une ia",
    "en tant qu'ia",
)
_INSTRUCTION = re.compile(
    r"(?<![^\W\d_])(?:"
    + "|".join(re.escape(pattern) for pattern in INSTRUCTION_PATTERNS)
    + r")(?![^\W\d_])"
)


def looks_like_instruction(norm: str) -> bool:
    """Whether a normalised text reads like an instruction given to a model."""
    return _INSTRUCTION.search(_unaccent(norm).replace("\n", " ")) is not None


# --- the rules, as functions of the value and the cited spans -------------------------------------

_URL_START = re.compile(r"(?<![\w.%+@/-])(?:https?://(?:www\.)?|www\.)")
_ADDRESS_BEFORE = "._%+-@/"
_ADDRESS_AFTER = "_%+-@"
_ADDRESS_GOES_ON = "./?#:=&~"


def _bare(norm: str) -> str:
    """A normalised text without "http://", "https://" and "www.", removed only where an
    address starts."""
    return _URL_START.sub("", norm)


def _address_char(char: str, others: str) -> bool:
    return char.isalnum() or char in others or _mark(char)


def rule_address(value: str, spans: Sequence[Span], *, url: bool = False) -> Rule | None:
    """Rule V for an email or a URL: N(value) is written in a span, hyphens and spaces kept,
    and bounded on both sides, so a part of an address never passes."""
    wanted = normalise(value).text
    if url:
        wanted = _bare(wanted).rstrip("/")
    if not wanted:
        return Rule.VALUE_EMPTY
    for span in spans:
        text = _bare(span.text) if url else span.text
        if len(wanted) > len(text):
            continue
        at = text.find(wanted)
        while at >= 0:
            end = at + len(wanted)
            starts = at == 0 or not _address_char(text[at - 1], _ADDRESS_BEFORE)
            # A full stop, a slash or a sign of a query after it is punctuation only when what
            # follows is not more of the address.
            rest = end
            while rest < len(text) and text[rest] in _ADDRESS_GOES_ON:
                rest += 1
            ends = rest == len(text) or not _address_char(text[rest], _ADDRESS_AFTER)
            if starts and ends:
                return None
            at = text.find(wanted, at + 1)
    return Rule.ADDRESS_NOT_IN_SOURCE


def _bones(text: str, *, casefold: bool = True) -> str:
    return skeleton(normalise(text, casefold=casefold).text).text


def _name(value: str, spans: Sequence[Span]) -> Rule | None:
    wanted = _bones(value)
    if not wanted:
        return Rule.VALUE_EMPTY
    exact = _bones(value, casefold=False)
    for span in spans:
        text = span.text
        bones = skeleton(text)
        if len(wanted) > len(bones.text):
            continue
        at = bones.text.find(wanted)
        while at >= 0:
            start, end = bones.index[at], bones.index[at + len(wanted) - 1] + 1
            if (
                is_token_boundary(text, start)
                and is_token_boundary(text, end)
                and (
                    len(wanted) >= SHORT_NAME
                    or _bones(span.source(start, end), casefold=False) == exact
                )
            ):
                return None
            at = bones.text.find(wanted, at + 1)
    return Rule.NAME_NOT_IN_SOURCE


def _date(value: str, facts: DateFacts) -> Rule | None:
    written = _DATE_VALUE.fullmatch(value)
    if written is None:
        return Rule.DATE_FORMAT
    year = int(written.group(1))
    if year not in facts.years:
        short = year in facts.implied or any(two == year % 100 for two, _ in facts.short)
        return Rule.TWO_DIGIT_YEAR if short else Rule.YEAR_NOT_IN_SOURCE
    if written.group(2) is None:
        return None
    month = int(written.group(2))
    # A month next to a two digit year counts once the four digit year is cited as well.
    if (year, month) in facts.pairs or (year % 100, month) in facts.short:
        return None
    if (year, month) in facts.apart:
        return Rule.MONTH_APART_FROM_YEAR
    return Rule.MONTH_NOT_IN_SOURCE


def _phone(value: str, phones: frozenset[str]) -> Rule | None:
    digits = _NOT_DIGIT.sub("", normalise(value).text)
    if len(digits) < PHONE_DIGITS_MIN:
        return Rule.PHONE_TOO_SHORT
    return None if digits in phones else Rule.PHONE_NOT_IN_SOURCE


def rule_name(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule V for a name: its skeleton is in a span as whole tokens, the match starting and
    ending on token boundaries. A name whose skeleton has fewer than four characters must also
    be written there with the same capitals. Then rule N, since digits in a name are numbers."""
    return _name(value, spans) or rule_numbers(value, spans)


def rule_date(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule D for `YYYY` or `YYYY-MM`: the four digit year is in a span, and the month stands
    next to that year in one of the forms `dates_of` reads."""
    return _date(value, _Evidence(spans).dates())


def rule_phone(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule P: the digits of the value are all the digits of one phone number of a span. No
    conversion between national and international forms."""
    return _phone(value, _Evidence(spans).phones())


def rule_numbers(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule N: each number run of the value is a number run of a span, or has a reading in
    common with one. A run that follows "version" or "v", in the value or in the span, is a
    version and is compared as text: "1.10" is not "1.1"."""
    return _Evidence(spans).numbers(value)


class _Evidence:
    """The cited spans of one item, read once for every part of its value."""

    def __init__(self, spans: Sequence[Span]) -> None:
        self.spans = spans
        self._dates: DateFacts | None = None
        self._phones: frozenset[str] | None = None
        self._runs: frozenset[str] | None = None
        self._readings: frozenset[str] = frozenset()

    def dates(self) -> DateFacts:
        if self._dates is None:
            each = [dates_of(span.text) for span in self.spans]
            self._dates = DateFacts(
                frozenset().union(*(facts.years for facts in each)),
                frozenset().union(*(facts.pairs for facts in each)),
                frozenset().union(*(facts.short for facts in each)),
                frozenset().union(*(facts.implied for facts in each)),
                frozenset().union(*(facts.apart for facts in each)),
            )
        return self._dates

    def phones(self) -> frozenset[str]:
        if self._phones is None:
            self._phones = frozenset().union(*(phones_of(span.text) for span in self.spans))
        return self._phones

    def numbers(self, value: str) -> Rule | None:
        if self._runs is None:
            runs: set[str] = set()
            found: set[str] = set()
            for span in self.spans:
                for run, version in _number_runs(span.text):
                    runs.add(run)
                    if not version:
                        found |= readings(run)
            self._runs, self._readings = frozenset(runs), frozenset(found)
        for run, version in _number_runs(normalise(value).text):
            if run in self._runs:
                continue
            if version or not readings(run) & self._readings:
                return Rule.NUMBER_NOT_IN_SOURCE
        return None

    def check(self, field_type: FieldType, value: object) -> Rule | None:
        """The rule that `value` breaks as a field of type `field_type`, or None."""
        if field_type is FieldType.SETTING:
            return Rule.SETTING
        if value is None or field_type in (FieldType.TERM, FieldType.ENUM):
            return None
        if field_type is FieldType.TEXT:
            if isinstance(value, bool):
                return None
            if isinstance(value, int | float):
                try:
                    value = repr(value)
                except ValueError:  # an integer too long to print
                    return Rule.NUMBER_NOT_IN_SOURCE
        if not isinstance(value, str):
            return Rule.DATE_FORMAT if field_type is FieldType.DATE else Rule.VALUE_NOT_TEXT
        if field_type is FieldType.DATE:
            return _date(value, self.dates())
        if field_type is FieldType.PHONE:
            return _phone(value, self.phones())
        if field_type is FieldType.EMAIL:
            return rule_address(value, self.spans)
        if field_type is FieldType.URL:
            return rule_address(value, self.spans, url=True)
        if field_type is FieldType.NAME:
            return _name(value, self.spans) or self.numbers(value)
        return self.numbers(value)


def check_value(
    field_type: FieldType | str, value: object, spans: Sequence[Span]
) -> Refusal | None:
    """One scalar of a value against the cited spans, by its field type: rule V for `email`,
    `url` and `name`, D for `date`, P for `phone`, N for `text` and `name`, nothing for `term`
    and `enum`, and `setting` is refused whatever the spans say. None when it holds."""
    try:
        kind = FieldType(field_type)
    except ValueError:
        return _refuse(Rule.UNKNOWN_FIELD, "value")
    rule = _Evidence(spans).check(kind, value)
    return None if rule is None else _refuse(rule, "value")


# --- the check ------------------------------------------------------------------------------------


class _Leaf(NamedTuple):
    index: str
    field_type: FieldType
    value: object


def _leaves(
    field_types: FieldType | str | Mapping[str, FieldType | str], value: object
) -> list[_Leaf] | Refusal:
    """The scalars of `value` with their field types, or the refusal of its shape.

    `field_types` is one type, for a scalar or a list of scalars, or a mapping from each path of
    the value to its type: keys joined by full stops, `[]` for the items of a list
    ("organisation", "terms[]", "links[].url"). A path that is not in the mapping is refused,
    and so is a setting anywhere in the value, before anything else is looked at."""
    table: Mapping[str, Any] = (
        field_types if isinstance(field_types, Mapping) else {"": field_types, "[]": field_types}
    )
    types: dict[str, FieldType] = {}
    for path, name in table.items():
        try:
            types[path] = FieldType(name)
        except ValueError:
            return _refuse(Rule.UNKNOWN_FIELD, "value")
    # Every path that leads to a known one: "links" and "links[]" for "links[].url".
    containers = {
        path[:cut] for path in types for cut, char in enumerate(path) if char in ".[" and cut
    }
    leaves: list[_Leaf] = []
    setting: str | None = None
    unknown: str | None = None
    too_large = False
    stack: list[tuple[str, str, object]] = [("", "value", value)]
    while stack:
        path, index, node = stack.pop()
        if isinstance(node, Mapping):
            children: list[tuple[str, str, object]] = []
            for key, child in node.items():
                below = f"{path}.{key}" if path else key
                if not isinstance(key, str) or (below not in types and below not in containers):
                    unknown = unknown or index
                    continue
                children.append((below, f"{index}.{key}", child))
            stack.extend(reversed(children))
        elif isinstance(node, list | tuple):
            below = f"{path}[]"
            if below not in types and below not in containers:
                unknown = unknown or index
                continue
            if len(node) > VALUE_LEAVES_MAX:
                too_large = True
                continue
            stack.extend(
                (below, f"{index}[{position}]", child)
                for position, child in reversed(list(enumerate(node)))
            )
        elif path not in types:
            unknown = unknown or index
        elif types[path] is FieldType.SETTING:
            setting = setting or index
        elif len(leaves) >= VALUE_LEAVES_MAX:
            too_large = True
            break
        else:
            leaves.append(_Leaf(index, types[path], node))
    if setting is not None:
        return _refuse(Rule.SETTING, setting)
    if unknown is not None:
        return _refuse(Rule.UNKNOWN_FIELD, unknown)
    if too_large:
        return _refuse(Rule.VALUE_TOO_LARGE, "value")
    return leaves


def check_item(
    field_types: FieldType | str | Mapping[str, FieldType | str],
    value: object,
    document: str | Document,
    excerpts: Sequence[str],
) -> Accepted | Refusal:
    """Whether `document` supports `value` through `excerpts`: the citations to store and the
    flags of the item, or the first refusal as `(index, rule, reason)`.

    `value` is the item's value as the agent proposed it (a scalar, or the nested value of an
    employer, a school, a fact block, a category), and `field_types` gives the type of each of
    its paths (see `_leaves`). `document` is the stored text, or better the `prepare`d document
    of the step. In order: a setting or an unknown field anywhere in the value refuses the item
    whatever it cites; then each excerpt must be found; then each part of the value must hold
    against the cited spans. An item that is accepted carries `looks_like_instruction` when its
    value or a cited span reads like an instruction."""
    leaves = _leaves(field_types, value)
    if isinstance(leaves, Refusal):
        return leaves
    if not isinstance(excerpts, list | tuple) or not excerpts:
        return _refuse(Rule.NO_CITATION, "excerpts")
    if len(excerpts) > EXCERPTS_MAX:
        return _refuse(Rule.TOO_MANY_CITATIONS, "excerpts")
    prepared = prepare(document) if isinstance(document, str) else document
    spans: list[Span] = []
    for position, excerpt in enumerate(excerpts):
        found = find(prepared, excerpt)
        if isinstance(found, Refusal):
            return found._replace(index=f"excerpts[{position}]")
        spans.append(found)
    evidence = _Evidence(spans)
    flagged = any(looks_like_instruction(span.text) for span in spans)
    for leaf in leaves:
        rule = evidence.check(leaf.field_type, leaf.value)
        if rule is not None:
            return _refuse(rule, leaf.index)
        if not flagged and isinstance(leaf.value, str):
            flagged = looks_like_instruction(normalise(leaf.value).text)
    citations = tuple(Citation(span.source_start, span.source_end, span.source()) for span in spans)
    return Accepted(citations, (LOOKS_LIKE_INSTRUCTION,) if flagged else ())
