"""The citation checker (SPEC-ingestion.md, "Citation check"; tasks/ingestion/todo.md, "Citations").

An extraction agent proposes a profile item: a value, and excerpts of the document it read.
`check_item` says whether the document supports the value. It finds each excerpt in the document,
takes the passage it quotes (the cited span, never cutting a word or a number) and checks every
part of the value against those passages by its field type: an address must be written there
exactly, a name as whole words, a date with its month next to its year, a phone number whole, and
every other number must be a number of the passage. A span is read in its place in the document:
an address, a phone number or a date that the excerpt cuts is not read.

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
- Memory: a prepared document keeps N, S and two maps of four bytes per character. A text that
  unfolds past UNFOLD_MAX times its length is not read, and a value holds at most
  VALUE_CHARS_MAX characters.

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
from itertools import pairwise, repeat
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
    "VALUE_CHARS_MAX",
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
VALUE_CHARS_MAX = 250_000  # characters of text in one value, all its parts together
UNFOLD_MAX = 4  # N of a text is at most that many times its length, plus UNFOLD_SLACK
UNFOLD_SLACK = 1_000
CLUSTER_MAX = 32  # characters of one cluster: a longer run of combining marks is cut there
CLUSTERS_REMEMBERED = 65_536  # distinct clusters whose N is kept while one text is normalised
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

    DOCUMENT_UNREADABLE = "document_unreadable"
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
    Rule.DOCUMENT_UNREADABLE: "this document cannot be read as text: nothing can be cited from it",
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
    Rule.VALUE_TOO_LARGE: "the value is too large to check",
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
_PARTS = re.compile(r"[ \n]|[^ \n]+")


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
    """N(text), cluster by cluster (a character with the marks that follow it):

    1. apostrophes, quotes and dashes fold to their ASCII form, before NFKC can consume them;
    2. invisible characters go: format characters and variation selectors;
    3. NFKC, and every decimal digit of any script becomes its ASCII digit. A digit that comes
       from another character (a superscript, a fraction) is fenced by breaks;
    4. case fold (skipped with `casefold=False`, for names of fewer than four characters);
    5. whitespace: a run that holds a control character or a line separator, or that is three
       characters long or more, becomes one line feed, a break. Any other run becomes one space.
       None at the ends.

    One pass, O(length). N is as long as the text or shorter, but for the few characters that
    NFKC unfolds (a ligature into its letters, U+FDFA into eighteen).
    """
    return _normalise(text, casefold, None)[0]


def _normalise(text: str, casefold: bool, limit: int | None) -> tuple[Normalised, bool]:
    """`normalise`, and whether it went to the end: it stops as soon as N passes `limit`
    characters."""
    pieces: list[str] = []
    origin = array("I")
    # N of each cluster met, cut into its whitespace characters and what stands between them:
    # the same strings are appended again and again, never one new string per character.
    cache: dict[str, list[str]] = {}
    pending = 0  # whitespace characters since the last character kept
    hard = False  # that run holds a break
    pending_at = 0
    position, length = 0, len(text)
    while position < length:
        if limit is not None and len(origin) > limit:
            return Normalised("", array("I")), False
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
            folded = _PARTS.findall(_fold(cluster, casefold))
            if len(cache) < CLUSTERS_REMEMBERED:
                cache[cluster] = folded
        for part in folded:
            if part in (" ", "\n"):
                if not pending:
                    pending_at = position
                pending += 1
                hard = hard or part == "\n"
                continue
            if pending:
                if pieces:
                    pieces.append("\n" if hard or pending >= 3 else " ")
                    origin.append(pending_at)
                pending, hard = 0, False
            pieces.append(part)
            if len(part) == 1:
                origin.append(position)
            else:
                origin.extend(repeat(position, len(part)))
        position = end
    return Normalised("".join(pieces), origin), True


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
    readable: bool = True


def prepare(text: str) -> Document:
    """The document ready to be searched. One pass, O(length); keep it for the step.

    Its memory is bounded too: a text whose N passes UNFOLD_MAX times its length is not prose
    (500,000 times U+FDFA would unfold to nine million characters) and is left unread, so every
    citation into it is refused with `document_unreadable`."""
    norm, whole = _normalise(text, True, UNFOLD_MAX * len(text) + UNFOLD_SLACK)
    if not whole:
        return Document(text, "", array("I"), "", array("I"), readable=False)
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
    token boundaries. Every value rule reads the span in its document, never what the agent
    typed: what stands right before and right after it tells whether an address, a phone
    number or a date is whole."""

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
    if not prepared.readable:
        return _refuse(Rule.DOCUMENT_UNREADABLE)
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


def _number_runs(text: str, start: int = 0, end: int | None = None) -> Iterator[tuple[str, bool]]:
    """Each number run of `text[start:end]`, and whether it is written as a version: what
    stands before the run counts even when it is before `start`."""
    for run in _NUMBER_RUN.finditer(text, start, len(text) if end is None else end):
        before = text[max(0, run.start() - _VERSION_WINDOW) : run.start()]
        yield run.group(), _VERSION_BEFORE.search(before) is not None


def numbers_of(norm: str, start: int = 0, end: int | None = None) -> frozenset[str]:
    """Every reading of every number run of a normalised text, or of `norm[start:end]` read in
    its place when `start` and `end` are the bounds of a span."""
    out: set[str] = set()
    for run, _ in _number_runs(norm, start, end):
        out |= readings(run)
    return frozenset(out)


# --- rule D: dates --------------------------------------------------------------------------------

# Month words are compared without accents: "février" is looked up as "fevrier".
MONTH_NAMES: Mapping[str, int] = {
    name: month
    for month, names in enumerate(
        (
            "january janvier",
            "february fevrier",
            "march mars",
            "april avril",
            "may mai",
            "june juin",
            "july juillet",
            "august aout",
            "september septembre",
            "october octobre",
            "november novembre",
            "december decembre",
        ),
        start=1,
    )
    for name in names.split()
}
# Only an abbreviation may carry a full stop: "janv. 2023" is January, "may. 2023" is not May.
MONTH_ABBREVIATIONS: Mapping[str, int] = {
    name: month
    for month, names in enumerate(
        (
            "jan janv",
            "feb fev fevr",
            "mar",
            "apr avr",
            "",
            "jun",
            "jul juil juill",
            "aug",
            "sep sept",
            "oct",
            "nov",
            "dec",
        ),
        start=1,
    )
    for name in names.split()
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
_DATE_SEPARATORS = ("/", ".", "-")
_DATE_TOKEN = re.compile(r"[0-9]+|\n|[^\W\d_]+|\S")
_DATE_VALUE = re.compile(r"([0-9]{4})(?:-(0[1-9]|1[0-2]))?")
# What a span needs of the document around it: the two characters before it tell whether its
# first number goes on a date that started earlier, the next word tells what follows its last.
_DATE_BEFORE = 2
_DATE_AFTER = 24


class _Token(NamedTuple):
    kind: str  # "w" a word without its accents, "d" digits, "b" a break, "p" one other character
    text: str
    glued: bool  # no space before it
    start: int
    end: int


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

    years: frozenset[int] = frozenset()
    pairs: frozenset[tuple[int, int]] = frozenset()
    short: frozenset[tuple[int, int]] = frozenset()
    implied: frozenset[int] = frozenset()
    apart: frozenset[tuple[int, int]] = frozenset()

    def __or__(self, other: DateFacts) -> DateFacts:
        return DateFacts(
            self.years | other.years,
            self.pairs | other.pairs,
            self.short | other.short,
            self.implied | other.implied,
            self.apart | other.apart,
        )


@lru_cache(maxsize=4096)
def _unaccent_char(char: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", char) if not unicodedata.combining(c))


def _unaccent(text: str) -> str:
    """`text` without its accents, one character at a time: a normalisation of the whole string
    would sort a long run of combining marks, which is quadratic."""
    return text if text.isascii() else "".join(_unaccent_char(char) for char in text)


def _date_tokens(text: str, start: int, end: int) -> list[_Token]:
    tokens = []
    for found in _DATE_TOKEN.finditer(text, start, end):
        piece = found.group()
        glued = found.start() > 0 and text[found.start() - 1] not in " \n"
        if _digit(piece[0]):
            kind = "d"
        elif piece == "\n":
            kind = "b"
        elif piece[0].isalpha():
            kind, piece = "w", _unaccent(piece)
        else:
            kind = "p"
        tokens.append(_Token(kind, piece, glued, found.start(), found.end()))
    return tokens


class _DateReader:
    """Reads the dates of `text[start:end]`, a span in its document. A fact counts when every
    token it is made of lies inside the span; the tokens around the span are read only to know
    that a date goes on beyond it. Linear in the tokens: every look ahead is bounded."""

    def __init__(self, text: str, start: int, end: int) -> None:
        self.tokens = tokens = _date_tokens(
            text, max(0, start - _DATE_BEFORE), min(len(text), end + _DATE_AFTER)
        )
        first = 0
        while first < len(tokens) and tokens[first].start < start:
            first += 1
        limit = first
        while limit < len(tokens) and tokens[limit].end <= end:
            limit += 1
        self.first, self.limit = first, limit
        self.pairs: set[tuple[int, int]] = set()
        self.short: set[tuple[int, int]] = set()
        self.implied: set[int] = set()
        self.apart: set[tuple[int, int]] = set()

    def facts(self) -> DateFacts:
        tokens = self.tokens
        cited = tokens[self.first : self.limit]
        years = {int(token.text) for token in cited if token.kind == "d" and len(token.text) == 4}
        for at in range(self.first, self.limit):
            self._month_word(at)
        self._numeric()
        return DateFacts(
            frozenset(years),
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

    def _present(self, at: int) -> int:
        """How many tokens the present takes at `at` ("present", "aujourd'hui"), or 0."""
        tokens = self.tokens
        if at < len(tokens) and tokens[at].kind == "w" and tokens[at].text in _PRESENT_WORDS:
            return 1
        for phrase in _PRESENT_PHRASES:
            if all(self._is(at + offset, part) for offset, part in enumerate(phrase)):
                return len(phrase)
        return 0

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
            # "sept 2021 collaborateurs" is seven of them, not a month. What follows the year
            # counts even when the span stops at the year.
            bare_sept = tokens[at].text == "sept" and year_at == at + 1
            counted = (
                year_at + 1 < len(tokens)
                and tokens[year_at + 1].kind == "w"
                and not self._connector(year_at + 1, lists=True)
                and not self._present(year_at + 1)
            )
            if year_at < self.limit and not (bare_sept and counted):
                self.pairs.add((int(tokens[year_at].text), month))
            return
        if self._is(after, "'") and self._digits(after + 1, 2) and tokens[after + 1].glued:
            if after + 1 < self.limit:
                self.short.add((int(tokens[after + 1].text), month))  # "Jan '23"
            return
        broken = self._year_after(after, across_breaks=True)
        if 0 <= broken < self.limit:
            self.apart.add((int(tokens[broken].text), month))
        shared = self._shared_year(after, month)
        if 0 <= shared < self.limit:
            self.pairs.add((int(tokens[shared].text), month))

    def _shared_year(self, at: int, month: int) -> int:
        """The index of the year a month takes from the months that follow it in a range or a
        list, or -1: "juin à août 2019", "du 1er juin au 31 août 2019", "juin, juillet et août
        2019". The chain must go forward in the calendar: "novembre à février 2023" starts in
        2022."""
        previous = month
        at += self._day(at)  # "June 1st to August 31st, 2019"
        for _ in range(CHAIN_LINKS_MAX):
            step = self._connector(at, lists=True)
            if not step:
                return -1
            at += step
            day_at = at + 1 if self._is(at, "the") else at
            day = self._day(day_at)
            if day:
                at = day_at + day
                if self._is(at, "of"):
                    at += 1
            found = self._month(at)
            if found is None:
                return -1
            following, at = found
            if following < previous:
                return -1
            previous = following
            year_at = self._year_after(at)
            if year_at >= 0:
                return year_at
            at += self._day(at)
        return -1

    # -- dates written as numbers

    def _numeric(self) -> None:
        """Every chain of numbers glued by slashes, full stops or hyphens that lies whole inside
        the span. A chain that the span cuts, or that is glued to a word, is not read."""
        tokens = self.tokens
        atoms: list[_Atom] = []
        at = 0
        while at < len(tokens):
            if tokens[at].kind != "d":
                at += 1
                continue
            parts = [at]
            separators: list[str] = []
            end = at + 1
            while (
                end + 1 < len(tokens)
                and tokens[end].text in _DATE_SEPARATORS
                and tokens[end].glued
                and tokens[end + 1].kind == "d"
                and tokens[end + 1].glued
            ):
                separators.append(tokens[end].text)
                parts.append(end + 1)
                end += 2
            after_word = tokens[at].glued and at > 0 and tokens[at - 1].kind == "w"
            if at >= self.first and end <= self.limit and not after_word:
                atoms.extend(self._chain(parts, separators))
            at = end
        self._ranges(atoms)

    def _chain(self, parts: list[int], separators: list[str]) -> list[_Atom]:
        """The dates of one chain. One kind of separator: one date. Two kinds, one of them a
        hyphen: two dates and the sign between them, "01/2019-06/2020" as "2019-01/2020-06".
        A slash and a hyphen can each be that sign: the reading that leaves fewer numbers on
        their own is taken, and none when they leave as many. Anything else is not a date."""
        kinds = set(separators)
        if len(kinds) <= 1:
            groups = [(parts, separators[0] if separators else "")]
        elif kinds == {"-", "."}:
            groups = _split(parts, separators, "-")
        elif kinds == {"-", "/"}:
            both = (_split(parts, separators, "-"), _split(parts, separators, "/"))
            alone = [sum(len(group) == 1 for group, _ in groups) for groups in both]
            if alone[0] == alone[1]:
                return []
            groups = both[0] if alone[0] < alone[1] else both[1]
        else:
            return []
        atoms = []
        for group, separator in groups:
            atom = self._atom(group, separator)
            if atom is not None:
                atoms.append(atom)
                if atom.kind == "firm":
                    self.pairs.add((atom.year, atom.month))
        return atoms

    def _atom(self, parts: list[int], separator: str) -> _Atom | None:
        texts = [self.tokens[part].text for part in parts]
        sizes = [len(text) for text in texts]
        if len(parts) < 2 or max(sizes) > 4:
            return None
        start, end = parts[0], parts[-1] + 1
        numbers = [int(text) for text in texts]
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
            present = self._present(atom.end + step) if step else 0
            if present and atom.end + step + present <= self.limit:
                self._inside_range(atom)

    def _inside_range(self, atom: _Atom) -> None:
        if atom.kind == "short":
            self.short.add((atom.year, atom.month))
        else:
            self.pairs.add((atom.year, atom.month))


def _split(parts: list[int], separators: list[str], at: str) -> list[tuple[list[int], str]]:
    """The numbers of a chain in groups, cut at each separator `at`, each with the separator
    that holds it together (the other one of the chain)."""
    inside = next(separator for separator in separators if separator != at)
    groups = [[parts[0]]]
    for part, separator in zip(parts[1:], separators, strict=True):
        if separator == at:
            groups.append([part])
        else:
            groups[-1].append(part)
    return [(group, inside) for group in groups]


def _later(first: _Atom, second: _Atom) -> bool:
    if first.kind == "short" or second.kind == "short":
        return (first.year % 100, first.month) < (second.year % 100, second.month)
    return (first.year, first.month) < (second.year, second.month)


def _school_year(atom: _Atom) -> bool:
    return atom.kind == "ambiguous" and atom.month == (atom.year + 1) % 100


def dates_of(norm: str, start: int = 0, end: int | None = None) -> DateFacts:
    """What a normalised text says about dates (rule D), or what `norm[start:end]` says, read in
    its place, when `start` and `end` are the bounds of a span: a date that the span cuts is not
    read. A month word belongs to the year that follows it, never to a year written before it,
    and nothing pairs across a break."""
    return _DateReader(norm, start, len(norm) if end is None else end).facts()


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
_PHONE_CHARS = frozenset("0123456789().-/+ ")
_PHONE_CONTEXT = 48


def _phones(text: str, start: int, end: int) -> set[str]:
    """The digits of every phone number that lies whole in `text[start:end]`. A number that
    starts before the span or goes on after it is not one of them."""
    low = start
    floor = max(0, start - _PHONE_CONTEXT)
    while low > floor and text[low - 1] in _PHONE_CHARS:
        low -= 1
    high = end
    ceiling = min(len(text), end + _PHONE_CONTEXT)
    while high < ceiling and text[high] in _PHONE_CHARS:
        high += 1
    clear = start
    if low > 0 and text[low - 1] in _PHONE_CHARS:
        # More of a number may stand before what was read: the one at the edge is not known.
        while clear < end and text[clear] in _PHONE_CHARS:
            clear += 1
    out: set[str] = set()
    for run in _PHONE_RUN.finditer(text, low, high):
        if run.start() < clear or run.end() > end:
            continue
        out.add(_NOT_DIGIT.sub("", run.group()))
        for pattern in _FRENCH:
            head = pattern.match(text, run.start(), run.end())
            if head is not None:
                out.add(_NOT_DIGIT.sub("", head.group()))
    return out


def phones_of(norm: str, start: int = 0, end: int | None = None) -> frozenset[str]:
    """The digits of every phone number of a normalised text, or of those that lie whole in
    `norm[start:end]` when `start` and `end` are the bounds of a span. A break ends a phone
    number."""
    return frozenset(_phones(norm, start, len(norm) if end is None else end))


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


# --- rule V: addresses and names ------------------------------------------------------------------

_URL_HEAD = re.compile(r"(?:https?://(?:www\.)?|www\.)")
_URL_PREFIXES = ("https://www.", "http://www.", "https://", "http://", "www.")
_ADDRESS_BEFORE = "._%+-@/"
_ADDRESS_AFTER = "_%+-@"
_ADDRESS_GOES_ON = "./?#:=&~"
_GOES_ON_MAX = 64


def _address_char(char: str, others: str) -> bool:
    """Whether `char` can be part of an address: a letter of a script written with spaces, a
    digit, a mark, or one of `others`."""
    return (char.isalnum() and not _unspaced(char)) or char in others or _mark(char)


def _address_starts(text: str, at: int, url: bool) -> bool:
    """Whether an address can start at `at`: nothing of an address stands before it. For a
    URL, "http://", "https://" and "www." may, where an address starts."""
    if at == 0 or not _address_char(text[at - 1], _ADDRESS_BEFORE):
        return True
    if url:
        for prefix in _URL_PREFIXES:
            head = at - len(prefix)
            if head >= 0 and text.startswith(prefix, head):
                return head == 0 or not _address_char(text[head - 1], _ADDRESS_BEFORE)
    return False


def _address_ends(text: str, end: int) -> bool:
    """Whether an address can end at `end`. A full stop, a slash or a sign of a query there is
    punctuation only when what follows is not more of the address."""
    rest = end
    stop = min(len(text), end + _GOES_ON_MAX)
    while rest < stop and text[rest] in _ADDRESS_GOES_ON:
        rest += 1
    if rest == len(text):
        return True
    return rest < stop and not _address_char(text[rest], _ADDRESS_AFTER)


def _address(norm: str, spans: Sequence[Span], url: bool) -> Rule | None:
    wanted = norm
    if url:
        head = _URL_HEAD.match(wanted)
        wanted = (wanted[head.end() :] if head else wanted).rstrip("/")
    if not wanted:
        return Rule.VALUE_EMPTY
    for span in spans:
        text = span.document.text
        at = text.find(wanted, span.start, span.end)
        for _ in range(OCCURRENCES_MAX):
            if at < 0:
                break
            if _address_starts(text, at, url) and _address_ends(text, at + len(wanted)):
                return None
            at = text.find(wanted, at + 1, span.end)
    return Rule.ADDRESS_NOT_IN_SOURCE


def _bones(text: str, *, casefold: bool = True) -> str:
    return skeleton(normalise(text, casefold=casefold).text).text


def _words_kept(text: str, bones: Skeleton, at: int, value: Skeleton) -> bool:
    """Whether the value splits its words where the document does. Where the document writes one
    space between two characters of the match, the value has a space or a hyphen, and the value
    has none inside a token of the document. A hyphen or a break in the document asks for
    nothing, since it may be a word cut at the end of a line: "Therapist" is not written in
    "the rapist" nor "Dev Tech" in "DevTech", and "University" is in "Uni-" then "versity"."""
    for offset in range(len(value.text) - 1):
        before, after = bones.index[at + offset], bones.index[at + offset + 1]
        glued = value.index[offset + 1] - value.index[offset] == 1
        if glued and after - before == 2 and text[before + 1] == " ":
            return False
        if not glued and after - before == 1 and _inside(text, after):
            return False
    return True


def _name(value: str, norm: str, spans: Sequence[Span]) -> Rule | None:
    wanted = skeleton(norm)
    size = len(wanted.text)
    if not size:
        return Rule.VALUE_EMPTY
    if size > SPAN_MAX:
        return Rule.NAME_NOT_IN_SOURCE  # longer than any span
    # A short name is compared with its capitals, as the document writes them at that place.
    exact = _bones(value, casefold=False) if size < SHORT_NAME else ""
    for span in spans:
        text = span.text
        bones = skeleton(text)
        at = bones.text.find(wanted.text)
        for _ in range(OCCURRENCES_MAX):
            if at < 0:
                break
            start, end = bones.index[at], bones.index[at + size - 1] + 1
            if (
                is_token_boundary(text, start)
                and is_token_boundary(text, end)
                and (size >= SHORT_NAME or _bones(span.source(start, end), casefold=False) == exact)
                and _words_kept(text, bones, at, wanted)
            ):
                return None
            at = bones.text.find(wanted.text, at + 1)
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


def _phone(norm: str, phones: frozenset[str]) -> Rule | None:
    digits = _NOT_DIGIT.sub("", norm)
    if len(digits) < PHONE_DIGITS_MIN:
        return Rule.PHONE_TOO_SHORT
    return None if digits in phones else Rule.PHONE_NOT_IN_SOURCE


def rule_address(value: str, spans: Sequence[Span], *, url: bool = False) -> Rule | None:
    """Rule V for an email or a URL: N(value) is written in a span, hyphens and spaces kept,
    and bounded on both sides in the document, so a part of an address never passes, even when
    the excerpt starts or stops inside it. For a URL the scheme, "www." and the final slash of
    the value are left out."""
    return _address(normalise(value).text, spans, url)


def rule_name(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule V for a name: its skeleton is in a span as whole tokens, the match starting and
    ending on token boundaries, its words split where the document splits them. A name whose
    skeleton has fewer than four characters must also be written there with the same capitals.
    Then rule N, since digits in a name are numbers."""
    norm = normalise(value).text
    return _name(value, norm, spans) or _Evidence(spans).numbers(norm)


def rule_date(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule D for `YYYY` or `YYYY-MM`: the four digit year is in a span, and the month stands
    next to that year in one of the forms `dates_of` reads."""
    return _date(value, _Evidence(spans).dates())


def rule_phone(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule P: the digits of the value are all the digits of one phone number of a span. No
    conversion between national and international forms."""
    return _phone(normalise(value).text, _Evidence(spans).phones())


def rule_numbers(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule N: each number run of the value is a number run of a span, or has a reading in
    common with one. A run that follows "version" or "v", in the value or in the document, is
    a version and is compared as text: "1.10" is not "1.1"."""
    return _Evidence(spans).numbers(normalise(value).text)


class _Evidence:
    """The cited spans of one item, each read once, in its place in the document, for every
    part of the value."""

    def __init__(self, spans: Sequence[Span]) -> None:
        self.spans = spans
        self._dates: DateFacts | None = None
        self._phones: frozenset[str] | None = None
        self._runs: frozenset[str] | None = None
        self._readings: frozenset[str] = frozenset()

    def dates(self) -> DateFacts:
        if self._dates is None:
            facts = DateFacts()
            for span in self.spans:
                facts |= dates_of(span.document.text, span.start, span.end)
            self._dates = facts
        return self._dates

    def phones(self) -> frozenset[str]:
        if self._phones is None:
            found: set[str] = set()
            for span in self.spans:
                found |= _phones(span.document.text, span.start, span.end)
            self._phones = frozenset(found)
        return self._phones

    def numbers(self, norm: str) -> Rule | None:
        """Rule N for the normalised text of a value."""
        if self._runs is None:
            runs: set[str] = set()
            found: set[str] = set()
            for span in self.spans:
                for run, version in _number_runs(span.document.text, span.start, span.end):
                    runs.add(run)
                    if not version:
                        found |= readings(run)
            self._runs, self._readings = frozenset(runs), frozenset(found)
        for run, version in _number_runs(norm):
            if run in self._runs:
                continue
            if version or not readings(run) & self._readings:
                return Rule.NUMBER_NOT_IN_SOURCE
        return None

    def check(self, field_type: FieldType, value: object) -> tuple[Rule | None, str]:
        """The rule that `value` breaks as a field of type `field_type`, or None, and the
        normalised text of the value when it was read."""
        if field_type is FieldType.SETTING:
            return Rule.SETTING, ""
        if value is None:
            return None, ""
        if field_type in (FieldType.TERM, FieldType.ENUM) and not isinstance(value, str):
            return None, ""
        if field_type is FieldType.TEXT:
            if isinstance(value, bool):
                return None, ""
            if isinstance(value, int | float):
                try:
                    value = repr(value)
                except ValueError:  # an integer too long to print
                    return Rule.NUMBER_NOT_IN_SOURCE, ""
        if not isinstance(value, str):
            other = Rule.DATE_FORMAT if field_type is FieldType.DATE else Rule.VALUE_NOT_TEXT
            return other, ""
        normalised, whole = _normalise(value, True, UNFOLD_MAX * len(value) + UNFOLD_SLACK)
        if not whole:
            return Rule.VALUE_TOO_LARGE, ""
        norm = normalised.text
        if field_type in (FieldType.TERM, FieldType.ENUM):
            return None, norm
        if field_type is FieldType.DATE:
            return _date(value, self.dates()), norm
        if field_type is FieldType.PHONE:
            return _phone(norm, self.phones()), norm
        if field_type is FieldType.EMAIL:
            return _address(norm, self.spans, url=False), norm
        if field_type is FieldType.URL:
            return _address(norm, self.spans, url=True), norm
        if field_type is FieldType.NAME:
            return _name(value, norm, self.spans) or self.numbers(norm), norm
        return self.numbers(norm), norm


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
    rule, _ = _Evidence(spans).check(kind, value)
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
    characters = 0
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
            elif len(node) > VALUE_LEAVES_MAX:
                too_large = True
            else:
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
            if isinstance(node, str):
                characters += len(node)
    if setting is not None:
        return _refuse(Rule.SETTING, setting)
    if unknown is not None:
        return _refuse(Rule.UNKNOWN_FIELD, unknown)
    if too_large or characters > VALUE_CHARS_MAX:
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
        rule, norm = evidence.check(leaf.field_type, leaf.value)
        if rule is not None:
            return _refuse(rule, leaf.index)
        flagged = flagged or looks_like_instruction(norm)
    citations = tuple(Citation(span.source_start, span.source_end, span.source()) for span in spans)
    return Accepted(citations, (LOOKS_LIKE_INSTRUCTION,) if flagged else ())
