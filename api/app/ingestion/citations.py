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
- A cited span holds at most SPAN_MAX characters and is read once per item, whatever the number
  of parts of the value, so every value rule works on bounded text and costs O(length of the
  value).
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
WRITTEN_MAX = 64  # characters looked at around a number for a superscript glued to it

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

    DOCUMENT_NOT_TEXT = "document_not_text"
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
    Rule.DOCUMENT_NOT_TEXT: "this document has no text yet: nothing can be cited from it",
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


# Format characters that a reader sees: the Arabic number signs and the end of ayah, the
# Syriac abbreviation mark, the Arabic pound and piastre marks, the Mongolian vowel separator,
# the Kaithi number signs. Between two numbers they separate them, as a control character does.
_FORMAT_SIGNS = frozenset(
    "\u0600\u0601\u0602\u0603\u0604\u0605\u06dd\u070f\u0890\u0891\u08e2\u180e\U000110bd\U000110cd"
)


def _invisible(char: str, category: str) -> bool:
    """Format characters (soft hyphen, zero width, bidi controls, tags), variation selectors and
    the grapheme joiner: nothing a reader sees."""
    return (
        (category == "Cf" and char not in _FORMAT_SIGNS)
        or "\ufe00" <= char <= "\ufe0f"
        or "\U000e0100" <= char <= "\U000e01ef"
        or char == "\u034f"
    )


def _fold(cluster: str, casefold: bool) -> str:
    """N of one cluster. Whitespace comes out as a space or, for a break, a line feed."""
    kept = []
    for char in cluster.translate(_FOLDS):
        category = unicodedata.category(char)
        if category == "Cc" or char in _FORMAT_SIGNS:
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
    citation into it is refused with `document_unreadable`. So is what is not text at all (the
    null text of a document that waits for its transcription): nothing here raises."""
    if not isinstance(text, str):
        return Document("", "", array("I"), "", array("I"), readable=False)
    norm, whole = _normalise(text, True, UNFOLD_MAX * len(text) + UNFOLD_SLACK)
    if not whole:
        return Document(text, "", array("I"), "", array("I"), readable=False)
    bones = skeleton(norm.text)
    return Document(text, norm.text, norm.origin, bones.text, bones.index)


def _prepared(document: object) -> Document | None:
    """The document of a call, prepared when it comes as its text, or None when it is neither a
    text nor a prepared document."""
    if isinstance(document, str):
        return prepare(document)
    return document if isinstance(document, Document) else None


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
    prepared = _prepared(document)
    if prepared is None:
        return _refuse(Rule.DOCUMENT_NOT_TEXT)
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


def _two_numbers(whole: str, fraction: str, separator: str) -> bool:
    """Whether `whole`, a separator and four digits or more are two numbers and not a decimal:
    a month and its year written with a full stop ("01.2023"), or two years ("2019,2021")."""
    if len(fraction) != 4:
        return False
    return len(whole) == 4 or (separator == "." and len(whole) <= 2 and 1 <= int(whole) <= 12)


def readings(run: str) -> frozenset[str]:
    """The numbers a number run may stand for, as canonical decimal strings:

    a. no separator: the integer;
    b. every separator is the same character and is followed by exactly three digits, the first
       group having one to three digits and not starting with 0: the grouped integer
       ("10 000", "10,000" and "10.000" read 10000 and nothing else);
    c. exactly one separator, a full stop or a comma, followed by one or two digits, or by any
       number of digits when the integer part is 0: the decimal ("3,5", "0,125"). Followed by
       four digits or more it is the decimal too ("99.9999" is not 99), unless it reads as a
       month and its year ("01.2023") or as two years ("2019,2021"), which are pieces;
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
    if len(separators) == 1 and separators[0] in ".,":
        whole, fraction = parts
        short = len(fraction) <= 2 or whole == "0"
        if short or (len(fraction) >= 4 and not _two_numbers(whole, fraction, separators[0])):
            return frozenset({_decimal(whole, fraction)})
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


@lru_cache(maxsize=4096)
def _compatibility_digit(char: str) -> bool:
    """Whether `char` is not a digit and NFKC writes it with digits and no letter: a
    superscript, a subscript, a fraction, a circled number. The single character for the
    square metre, which NFKC writes with a letter and a digit, is a sign and not one of them."""
    unfolded = unicodedata.normalize("NFKC", char)
    return (
        not char.isdecimal()
        and any(c.isdecimal() for c in unfolded)
        and not any(c.isalpha() for c in unfolded)
    )


def _digit_like(char: str) -> bool:
    return char.isdecimal() or (char >= "\u00b2" and _compatibility_digit(char))


def _written(source: str, first: int, last: int) -> str:
    """How `source` writes the number at `source[first:last + 1]` when it is not a plain
    number, or "". A superscript, a subscript or a fraction glued to a number makes one written
    form with it (ten with a superscript three, three and the sign of a half): ten to the
    power of three is neither 10 nor 3. And a digit that comes out of a sign (the superscript
    two of a square metre) is that sign, never a number."""
    if not source[first].isdecimal() and not _compatibility_digit(source[first]):
        return source[first]
    low, high = first, last + 1
    for _ in range(WRITTEN_MAX):
        if low > 0 and _digit_like(source[low - 1]):
            low -= 1
        elif high < len(source) and _digit_like(source[high]):
            high += 1
        else:
            break
    glued = source[low:first] + source[last + 1 : high]
    if not source[first].isdecimal() or any(not char.isdecimal() for char in glued):
        return source[low:high]
    return ""


class _Run(NamedTuple):
    text: str  # the number run, in N
    version: bool  # it follows "version" or "v"
    form: str  # how the source writes it with the superscript or fraction glued to it, or ""


def _number_runs(
    text: str,
    start: int = 0,
    end: int | None = None,
    source: str = "",
    origin: Sequence[int] | None = None,
) -> Iterator[_Run]:
    """Each number run of `text[start:end]`. What stands before the run counts even when it is
    before `start`. With `source` and `origin`, the text that `text` is N of and its map, a run
    also tells how the source writes it (see `_written`)."""
    for run in _NUMBER_RUN.finditer(text, start, len(text) if end is None else end):
        before = text[max(0, run.start() - _VERSION_WINDOW) : run.start()]
        form = ""
        if origin is not None:
            form = _written(source, origin[run.start()], origin[run.end() - 1])
        yield _Run(run.group(), _VERSION_BEFORE.search(before) is not None, form)


def numbers_of(norm: str, start: int = 0, end: int | None = None) -> frozenset[str]:
    """Every reading of every number run of a normalised text, or of `norm[start:end]` read in
    its place when `start` and `end` are the bounds of a span."""
    out: set[str] = set()
    for run in _number_runs(norm, start, end):
        out |= readings(run.text)
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
    after a full year ("2019-20"), `apart` the (year, month) that a break alone separates, and
    `unread` the (two digit year, month) of a range that may as well be days and months or
    scores ("05/10 au 08/10"): never read, even beside the four digit year."""

    years: frozenset[int] = frozenset()
    pairs: frozenset[tuple[int, int]] = frozenset()
    short: frozenset[tuple[int, int]] = frozenset()
    implied: frozenset[int] = frozenset()
    apart: frozenset[tuple[int, int]] = frozenset()
    unread: frozenset[tuple[int, int]] = frozenset()

    def __or__(self, other: DateFacts) -> DateFacts:
        return DateFacts(
            self.years | other.years,
            self.pairs | other.pairs,
            self.short | other.short,
            self.implied | other.implied,
            self.apart | other.apart,
            self.unread | other.unread,
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
        self.unread: set[tuple[int, int]] = set()
        # A whole date read with certainty, by the index of its first token: year, month, day,
        # whether the day is written first, and its separator.
        self.whole: dict[int, tuple[int, int, int, bool, str]] = {}
        self.cut = self._cut_out()

    def facts(self) -> DateFacts:
        tokens = self.tokens
        years = {int(tokens[at].text) for at in range(self.first, self.limit) if self._year(at)}
        for at in range(self.first, self.limit):
            self._month_word(at)
        self._numeric()
        return DateFacts(
            frozenset(years),
            frozenset(self.pairs),
            frozenset(self.short),
            frozenset(self.implied),
            frozenset(self.apart),
            frozenset(self.unread),
        )

    # -- the pieces of a written date

    def _is(self, at: int, text: str) -> bool:
        return at < len(self.tokens) and self.tokens[at].text == text

    def _digits(self, at: int, size: int) -> bool:
        tokens = self.tokens
        return at < len(tokens) and tokens[at].kind == "d" and len(tokens[at].text) == size

    def _year(self, at: int) -> bool:
        """Whether the token at `at` is a year: four digits, not cut out of a longer number."""
        return self._digits(at, 4) and at not in self.cut

    def _cut_out(self) -> set[int]:
        """The numbers that are a part of a longer one, where four digits are not a year.

        Glued by full stops or commas ("2021,50", "0.2021"), unless the whole is a dotted date
        of the table or every number has four digits ("2019,2021"). In a run of three numbers
        or more with one space between them ("+33 6 3998 2019", an IBAN), next to a number of
        three digits or more, unless every number of the run is a year from 1900 to 2100
        ("2019 2020 2021"). One pass over the tokens each."""
        tokens = self.tokens
        cut: set[int] = set()
        at = 0
        while at < len(tokens):
            end = at + 1
            if tokens[at].kind == "d":
                while (
                    end + 1 < len(tokens)
                    and tokens[end].text in (".", ",")
                    and tokens[end].glued
                    and tokens[end + 1].kind == "d"
                    and tokens[end + 1].glued
                ):
                    end += 2
                if end > at + 1 and not self._dotted_date(at, end):
                    cut.update(range(at, end, 2))
            at = end
        at = 0
        while at < len(tokens):
            end = at + 1
            if tokens[at].kind == "d":
                while end < len(tokens) and tokens[end].kind == "d":
                    end += 1
                sizes = [len(tokens[index].text) for index in range(at, end)]
                if len(sizes) >= 3 and not all(
                    "1900" <= tokens[index].text <= "2100" and len(tokens[index].text) == 4
                    for index in range(at, end)
                ):
                    for place, index in enumerate(range(at, end)):
                        beside = sizes[max(0, place - 1) : place] + sizes[place + 1 : place + 2]
                        if max(beside) >= 3:
                            cut.add(index)
            at = end
        return cut

    def _dotted_date(self, at: int, end: int) -> bool:
        """Whether the numbers glued by full stops or commas from `at` to `end` can hold a year:
        all of four digits, or `MM.YYYY`, `DD.MM.YYYY`, `YYYY.MM` or `YYYY.MM.DD` with full
        stops, the month from 1 to 12 and the day from 1 to 31."""
        tokens = self.tokens
        texts = [tokens[index].text for index in range(at, end, 2)]
        sizes = [len(text) for text in texts]
        if all(size == 4 for size in sizes):
            return True
        if len(sizes) > 3 or any(tokens[index].text != "." for index in range(at + 1, end, 2)):
            return False
        if sizes in ([4, 2], [4, 2, 2]):  # "2012.09" gives its year, as many CVs write a date
            return 1 <= int(texts[1]) <= 12 and 1 <= int(texts[-1]) <= 31
        if sizes[-1] != 4 or max(sizes[:-1]) > 2:
            return False
        if len(sizes) == 3 and not 1 <= int(texts[0]) <= 31:
            return False
        return 1 <= int(texts[-2]) <= 12

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
        if self._is(at, "-") and tokens[at].glued and self._year(at + 1) and tokens[at + 1].glued:
            return at + 1  # "Jan-2019", as a spreadsheet writes it
        day_at = skip(at + 1) if self._is(at, "the") else at
        day = self._day(day_at)
        if day:
            at = skip(day_at + day)
        if self._is(at, ","):
            at = skip(at + 1)
        if self._is(at, "of"):
            at = skip(at + 1)
        return at if self._year(at) else -1

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
        halves: list[tuple[int, int, str]] = []  # two numbers of two digits at most
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
            if end == at + 1 and self._spaced_slash(at):
                separators.append("/")  # "09 / 2019"
                parts.append(at + 2)
                end = at + 3
            after_word = tokens[at].glued and at > 0 and tokens[at - 1].kind == "w"
            if at >= self.first and end <= self.limit and not after_word:
                atoms.extend(self._chain(parts, separators))
                if len(parts) == 2 and max(len(tokens[part].text) for part in parts) <= 2:
                    halves.append((at, end, separators[0]))
            at = end
        self._ranges(atoms)
        for start, end, separator in halves:
            self._shares_the_year(start, end, separator)

    def _spaced_slash(self, at: int) -> bool:
        """Whether a month and its year stand at `at` around a slash with a space on each side,
        "09 / 2019", and nothing says that they are the end or the start of a longer date."""
        tokens = self.tokens
        # "03 / 04 / 2023" is a day, a month and a year, also when the span starts at "04".
        goes_on = at > 0 and (
            tokens[at - 1].text == "/"
            or (tokens[at].glued and tokens[at - 1].text in _DATE_SEPARATORS)
        )
        return (
            at + 2 < len(tokens)
            and tokens[at + 1].text == "/"
            and not tokens[at + 1].glued
            and not tokens[at + 2].glued
            and len(tokens[at].text) <= 2
            and self._year(at + 2)
            and not goes_on
            and not self._is(at + 3, "/")
        )

    def _shares_the_year(self, start: int, end: int, separator: str) -> None:
        """A day and its month before a whole date, in a range that goes forward: "du 15/06 au
        20/08/2019" starts in June 2019. The whole date says which of the two numbers is the
        day, so it must be one that is read with certainty."""
        step = self._connector(end, lists=False)
        whole = self.whole.get(end + step) if step else None
        if whole is None or whole[4] != separator:
            return
        year, month, day, day_first, _ = whole
        numbers = (int(self.tokens[start].text), int(self.tokens[end - 1].text))
        its_day, its_month = numbers if day_first else numbers[::-1]
        if 1 <= its_day <= 31 and 1 <= its_month <= 12 and (its_month, its_day) < (month, day):
            self.pairs.add((year, its_month))

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
                self.whole[start] = (year, second, first, True, separator)
            elif 12 < second <= 31 and 1 <= first <= 12 and separator != ".":
                self.pairs.add((year, first))  # the month first
                self.whole[start] = (year, first, second, False, separator)
            elif 1 <= first <= 12 and 1 <= second <= 12 and (separator == "." or first == second):
                self.pairs.add((year, second))  # dotted dates are day first
                self.whole[start] = (year, second, first, True, separator)
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
            years = _years_only(first) and _years_only(second)
            self._inside_range(first, years)
            self._inside_range(second, years)
        for atom in atoms:
            step = self._connector(atom.end, lists=False)
            present = self._present(atom.end + step) if step else 0
            if present and atom.end + step + present <= self.limit:
                self._inside_range(atom, _years_only(atom))

    def _inside_range(self, atom: _Atom, years: bool) -> None:
        if atom.kind != "short":
            self.pairs.add((atom.year, atom.month))
        elif years:
            self.short.add((atom.year, atom.month))
        else:
            self.unread.add((atom.year, atom.month))


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


def _years_only(atom: _Atom) -> bool:
    """Whether the second number of `MM/YY` can only be a year: over 12 it is not a month, so
    the two numbers are not a day and its month ("du 05/10 au 08/10") nor a score out of ten or
    twelve. Decision of the orchestrator after the first review of pull request #97."""
    return atom.kind != "short" or atom.year > 12


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

# What an address may hold comes from its grammar, not from a list of the characters met so far.
#
# An email is a local part, an @ and a domain. The local part (RFC 5322, atext, and the full
# stops between its atoms; RFC 6531 adds the letters of every script) holds letters, digits and
# the signs below. A domain (RFC 1035, RFC 5890) holds letters, digits, hyphens and the full
# stops between its labels, nothing else: a question mark or a slash after it is not it. The
# underscore is read as one more character of a domain, since host names carry it in practice:
# "camille@my" is not an address of a text that writes "camille@my_host.example".
_LOCAL_SIGNS = "!#$%&'*+/=?^_`{|}~-."
# Of these signs, a text also writes some around an address: a quote, the asterisks or the
# backticks of Markdown, the equals sign of "email=". Such a sign is part of the address only
# when something of the address stands before it ("o'brien", "r&d"). The others never open an
# address in a text: found right before a value, they say that the address started earlier.
_LOCAL_JOINS = "._%+-/"
# A URL (RFC 3986) holds letters, digits, the unreserved marks, the percent of an escape and
# the delimiters that join its parts: path, query, fragment, user. One of them right before a
# value says that the URL started earlier ("?u=example.org/cv", "/~camille/cv.html").
_URL_JOINS = "-._~%/?#=&@+"
# The other delimiters of RFC 3986 (: , ; ! $ ' ( ) * [ ]) are what a text writes around an
# address ("(example.org)", "a,b" in a CSV line, "[PERSONAL:http://...]"): they bound it.
# After a URL, a run of these goes on with it only when more of the address follows the run:
_URL_GOES_ON = "./?#:=&~"
_URL_AFTER = "_%+-@"
_URL_HEAD = re.compile(r"(?:https?://(?:www\.)?|www\.)")
_URL_PREFIXES = ("https://www.", "http://www.", "https://", "http://", "www.")
_SIGNS_MAX = 64  # signs in a row looked at around an address: past that, no telling


def _address_char(char: str, signs: str) -> bool:
    """Whether `char` can be part of an address: a letter of a script written with spaces, a
    digit, a mark, or one of `signs`."""
    return (char.isalnum() and not _unspaced(char)) or char in signs or _mark(char)


def _email_starts(text: str, at: int) -> bool:
    """Whether the local part of an email of the document starts at `at`: nothing of a local
    part stands before it, or only signs that a text writes around an address."""
    low = at
    floor = max(0, at - _SIGNS_MAX)
    while low > floor and text[low - 1] in _LOCAL_SIGNS and text[low - 1] not in _LOCAL_JOINS:
        low -= 1
    if low == 0:
        return True
    before = text[low - 1]
    if before == "@" or before in _LOCAL_SIGNS:
        return False  # a sign that joins, or more signs than can be told from an address
    if before == ":":
        return not _user_info(text, low - 1)
    return not _address_char(before, "")


def _user_info(text: str, colon: int) -> bool:
    """Whether the colon at `colon` is the one of "https://user:password@host": the word before
    it starts right after two slashes. The colon of "mailto:" and of a label bounds an email."""
    low = colon
    floor = max(0, colon - _SIGNS_MAX)
    while low > floor and _address_char(text[low - 1], "-._~%"):
        low -= 1
    return low >= 2 and text.startswith("//", low - 2)


def _email_ends(text: str, end: int) -> bool:
    """Whether the domain of an email of the document ends at `end`: a full stop there is the
    end of a sentence unless a letter, a digit or a hyphen follows it."""
    rest = end
    stop = min(len(text), end + _SIGNS_MAX)
    while rest < stop and text[rest] == ".":
        rest += 1
    if rest == len(text):
        return True
    return rest < stop and text[rest] != "@" and not _address_char(text[rest], "-_")


def _email_shape(wanted: str) -> bool:
    """Whether a normalised value has the shape of an email: a local part, an @, a domain."""
    local, sign, domain = wanted.rpartition("@")
    return (
        bool(sign and local and domain)
        and all(_address_char(char, _LOCAL_SIGNS) for char in local)
        and all(_address_char(char, "-._") for char in domain)
    )


def _url_starts(text: str, at: int) -> bool:
    """Whether a URL of the document starts at `at`: nothing that joins the parts of a URL
    stands before it. "http://", "https://" and "www." may, where a URL starts."""
    if at == 0:
        return True
    if text[at - 1] == ":":
        # A colon bounds a URL ("Site:example.org") unless it is the colon of the URL: the one
        # of a scheme, before two slashes, or the one of a port, before its digits.
        return not (text.startswith("//", at) or _port(text, at))
    if not _address_char(text[at - 1], _URL_JOINS):
        return True
    for prefix in _URL_PREFIXES:
        head = at - len(prefix)
        if head >= 0 and text.startswith(prefix, head):
            return head == 0 or not _address_char(text[head - 1], _URL_JOINS)
    return False


def _port(text: str, at: int) -> bool:
    """Whether the digits at `at` are a port, not the first label of a host ("3dprint.example",
    "123.example"): at most five of them, then nothing that goes on with a label."""
    end = at
    while end < len(text) and end - at < 6 and text[end].isdigit():
        end += 1
    if not 0 < end - at < 6:
        return False
    rest = text[end : end + 2]
    return not (rest[:1].isalpha() or rest[:1] == "-" or (rest[:1] == "." and rest[1:].isalnum()))


def _cut_by_a_line(text: str, at: int, end: int) -> bool:
    """Whether the address at `at` is a piece of one that a line cut at a hyphen: it stops on a
    hyphen right before a break, or it starts right after a break that follows a hyphen glued to
    a word. Such an address is not repaired, and no piece of it is the address."""
    if text[end - 1] == "-" and text.startswith("\n", end):
        return True
    return at >= 3 and text[at - 2 : at] == "-\n" and _address_char(text[at - 3], "")


def _url_ends(text: str, end: int) -> bool:
    """Whether a URL of the document ends at `end`. A full stop, a slash or a sign of a query
    there is punctuation only when what follows is not more of the address."""
    rest = end
    stop = min(len(text), end + _SIGNS_MAX)
    while rest < stop and text[rest] in _URL_GOES_ON:
        rest += 1
    if rest == len(text):
        return True
    return rest < stop and not _address_char(text[rest], _URL_AFTER)


def _same_address(written: str, proposed: str, url: bool) -> bool:
    """Whether the address the document writes is the one proposed, both normalised without the
    case fold: an email in lower case (as `normalize_email` of the identity module compares
    them, since a case fold would merge "straße" and "strasse"), the host of a URL in lower
    case and its path and query as written."""
    if not url:
        return written.lower() == proposed.lower()
    cut = min((at for at in map(written.find, "/?#") if at >= 0), default=len(written))
    return written[:cut].lower() == proposed[:cut].lower() and written[cut:] == proposed[cut:]


def _address(value: str, norm: str, spans: Sequence[Span], url: bool) -> Rule | None:
    """Rule V for an email or a URL. The value is looked for in the case folded text, then the
    place found must be a whole address of the document by the grammar of an address, read in
    the document and not in the span alone, and the same address once the fold is taken back."""
    wanted = norm
    head = _URL_HEAD.match(wanted) if url else None
    if head is not None:
        wanted = wanted[head.end() :]
    if url:
        wanted = wanted.rstrip("/")
    if not wanted:
        return Rule.VALUE_EMPTY
    if " " in wanted or "\n" in wanted or not (url or _email_shape(wanted)):
        return Rule.ADDRESS_NOT_IN_SOURCE
    proposed = normalise(value, casefold=False).text
    proposed = proposed[head.end() if head is not None else 0 :]
    if url:
        proposed = proposed.rstrip("/")
    starts, ends = (_url_starts, _url_ends) if url else (_email_starts, _email_ends)
    for span in spans:
        text = span.document.text
        at = text.find(wanted, span.start, span.end)
        for _ in range(OCCURRENCES_MAX):
            if at < 0:
                break
            end = at + len(wanted)
            if not _cut_by_a_line(text, at, end) and starts(text, at) and ends(text, end):
                written = normalise(span.source(at - span.start, end - span.start), casefold=False)
                if _same_address(written.text, proposed, url):
                    return None
            at = text.find(wanted, at + 1, span.end)
    return Rule.ADDRESS_NOT_IN_SOURCE


def _bones(text: str, *, casefold: bool = True) -> str:
    return skeleton(normalise(text, casefold=casefold).text).text


_LINE_BREAK = re.compile(r"\r\n|[\n\r\x85\u2028]")
_FREE, _SOFT, _HARD = 0, 1, 2


class _Read(NamedTuple):
    """A span as rule V reads it for a name, made once per item: its text, the skeleton of that
    text, and what each break of it is in the stored text."""

    span: Span
    text: str
    bones: Skeleton
    hard: frozenset[int]  # more than one line break: no name goes across
    soft: frozenset[int]  # one line break: two words of a name, never one word


def _break(raw: str) -> int:
    """What a break of N is in the stored text, for a name. One line break, indented or not,
    may stand between two words of a name. A blank line, a page break, a tab, a gap of three
    spaces or more or a sign that N reads as a break may not. A break that the stored text
    does not write (around a superscript) or that follows a soft hyphen asks for nothing."""
    rest, lines = _LINE_BREAK.subn("", raw)
    blanks = 0
    for char in rest:
        category = unicodedata.category(char)
        if char == "\t" or category == "Zs":
            blanks += 1
        elif not _invisible(char, category):
            return _HARD
    if lines > 1 or (not lines and (blanks >= 3 or "\t" in rest)):
        return _HARD
    return _SOFT if lines and "\u00ad" not in rest else _FREE


def _read(span: Span) -> _Read:
    document = span.document
    source, origin = document.source, document.origin
    text = span.text
    kinds: tuple[set[int], set[int], set[int]] = (set(), set(), set())
    at = text.find("\n")
    while at >= 0:
        place = span.start + at  # N has no break at its ends: both neighbours exist
        raw = source[_cluster_end(source, origin[place - 1]) : origin[place + 1]]
        kinds[_break(raw)].add(at)
        at = text.find("\n", at + 1)
    return _Read(span, text, skeleton(text), frozenset(kinds[_HARD]), frozenset(kinds[_SOFT]))


def _words_kept(read: _Read, at: int, value: Skeleton, norm: str) -> bool:
    """Whether the value splits its words where the document does. Where the document writes a
    space between two characters of the match, the value has a space or a hyphen, and the value
    has none inside a token of the document: "Therapist" is not written in "the rapist" nor
    "Dev Tech" in "DevTech". A hyphen in the document asks for nothing, since it may be a word
    cut at the end of a line: "University" is in "Uni-" then "versity". A dash with a space
    beside it separates two things, and the value must write a hyphen there too. One line
    break may stand between two words of a name, and inside one word only after a hyphen.
    Nothing goes across a blank line, a page break or the gap between two columns."""
    text, bones = read.text, read.bones
    for offset in range(len(value.text) - 1):
        before, after = bones.index[at + offset], bones.index[at + offset + 1]
        glued = value.index[offset + 1] - value.index[offset] == 1
        if after - before == 1:
            if not glued and _inside(text, after):
                return False
            continue
        gap = text[before + 1 : after]  # spaces, breaks and hyphens
        if " " in gap:
            typed = norm[value.index[offset] + 1 : value.index[offset + 1]]
            if glued or ("-" in gap and "-" not in typed):
                return False
        place = gap.find("\n")
        while place >= 0:
            where = before + 1 + place
            cut_word = gap[place - 1 : place] == "-"
            if where in read.hard or (glued and where in read.soft and not cut_word):
                return False
            place = gap.find("\n", place + 1)
    return True


def _name(value: str, norm: str, reads: Sequence[_Read]) -> Rule | None:
    wanted = skeleton(norm)
    size = len(wanted.text)
    if not any(char.isalnum() for char in wanted.text):
        return Rule.VALUE_EMPTY  # nothing, or punctuation alone
    if size > SPAN_MAX:
        return Rule.NAME_NOT_IN_SOURCE  # longer than any span
    # A short name is compared with its capitals, as the document writes them at that place.
    exact = _bones(value, casefold=False) if size < SHORT_NAME else ""
    for read in reads:
        span, text, bones = read.span, read.text, read.bones
        at = bones.text.find(wanted.text)
        for _ in range(OCCURRENCES_MAX):
            if at < 0:
                break
            start, end = bones.index[at], bones.index[at + size - 1] + 1
            if (
                is_token_boundary(text, start)
                and is_token_boundary(text, end)
                and (size >= SHORT_NAME or _bones(span.source(start, end), casefold=False) == exact)
                and _words_kept(read, at, wanted, norm)
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
        short = year in facts.implied or any(
            two == year % 100 for two, _ in facts.short | facts.unread
        )
        return Rule.TWO_DIGIT_YEAR if short else Rule.YEAR_NOT_IN_SOURCE
    if written.group(2) is None:
        return None
    month = int(written.group(2))
    # A month next to a two digit year counts once the four digit year is cited as well.
    if (year, month) in facts.pairs or (year % 100, month) in facts.short:
        return None
    if (year, month) in facts.apart:
        return Rule.MONTH_APART_FROM_YEAR
    if (year % 100, month) in facts.unread:
        return Rule.TWO_DIGIT_YEAR  # "09/11 à 03/12" may be days and months: the user types it
    return Rule.MONTH_NOT_IN_SOURCE


def _phone(norm: str, phones: frozenset[str]) -> Rule | None:
    digits = _NOT_DIGIT.sub("", norm)
    if len(digits) < PHONE_DIGITS_MIN:
        return Rule.PHONE_TOO_SHORT
    return None if digits in phones else Rule.PHONE_NOT_IN_SOURCE


def rule_address(value: str, spans: Sequence[Span], *, url: bool = False) -> Rule | None:
    """Rule V for an email or a URL: the value is a whole address of a span, hyphens kept. What
    an address holds is its grammar's: the local part and the domain of an email, the parts of
    a URL and the signs that join them, read in the document and not in the span alone, so a
    part of an address never passes, even when the excerpt starts or stops inside it. An email
    is compared in lower case, never case folded; the host of a URL in lower case, its path
    and query as written. For a URL the scheme, "www." and the final slash are left out."""
    return _address(value, normalise(value).text, spans, url)


def rule_name(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule V for a name: its skeleton is in a span as whole tokens, the match starting and
    ending on token boundaries, its words split where the document splits them. A name whose
    skeleton has fewer than four characters must also be written there with the same capitals.
    Then rule N, since digits in a name are numbers."""
    norm = normalise(value)
    evidence = _Evidence(spans)
    return _name(value, norm.text, evidence.reads()) or evidence.numbers(norm, value)


def rule_date(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule D for `YYYY` or `YYYY-MM`: the four digit year is in a span, and the month stands
    next to that year in one of the forms `dates_of` reads."""
    return _date(value, _Evidence(spans).dates())


def rule_phone(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule P: the digits of the value are all the digits of one phone number of a span. No
    conversion between national and international forms."""
    return _phone(normalise(value).text, _Evidence(spans).phones())


def rule_numbers(value: str, spans: Sequence[Span]) -> Rule | None:
    """Rule N: each number run of the value is a number run of a span, or every number it reads
    as is a number of a span (every piece of "2019 2023", not one of them). A run that follows
    "version" or "v", in the value or in the document, is a version and is compared as text:
    "1.10" is not "1.1". A number with a superscript, a subscript or a fraction glued to it is
    one written form: ten with a superscript three in a document supports neither 10 nor 3,
    and a value may hold it only as a span writes it."""
    return _Evidence(spans).numbers(normalise(value), value)


class _Evidence:
    """The cited spans of one item, each read once, in its place in the document, for every
    part of the value."""

    def __init__(self, spans: Sequence[Span]) -> None:
        self.spans = spans
        self._dates: DateFacts | None = None
        self._phones: frozenset[str] | None = None
        self._runs: frozenset[str] | None = None
        self._forms: frozenset[str] = frozenset()
        self._readings: set[str] = set()
        self._reads: list[_Read] | None = None

    def reads(self) -> list[_Read]:
        """The spans as rule V reads them for a name: a value of two thousand names costs each
        span one skeleton, not two thousand."""
        if self._reads is None:
            self._reads = [_read(span) for span in self.spans]
        return self._reads

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

    def numbers(self, norm: Normalised, value: str) -> Rule | None:
        """Rule N for a value and its normalised text."""
        if self._runs is None:
            runs: set[str] = set()
            forms: set[str] = set()
            found: set[str] = set()
            for span in self.spans:
                document = span.document
                for run in _number_runs(
                    document.text, span.start, span.end, document.source, document.origin
                ):
                    if run.form:
                        forms.add(run.form)  # a number with a superscript is not that number
                        continue
                    runs.add(run.text)
                    if not run.version:
                        found |= readings(run.text)
            self._runs, self._forms, self._readings = frozenset(runs), frozenset(forms), found
        for run in _number_runs(norm.text, source=value, origin=norm.origin):
            if run.form:
                # Written with a superscript or a fraction: only as a span writes it.
                if run.form not in self._forms:
                    return Rule.NUMBER_NOT_IN_SOURCE
            elif run.text not in self._runs and (
                # Every piece of the run, not one of them: "2019 2023" is two numbers.
                run.version or not readings(run.text) <= self._readings
            ):
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
            return _address(value, norm, self.spans, url=False), norm
        if field_type is FieldType.URL:
            return _address(value, norm, self.spans, url=True), norm
        if field_type is FieldType.NAME:
            return _name(value, norm, self.reads()) or self.numbers(normalised, value), norm
        return self.numbers(normalised, value), norm


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
        if not isinstance(path, str):
            return _refuse(Rule.UNKNOWN_FIELD, "value")
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
                # Its type first: an integer of five thousand digits cannot even be printed.
                below = (f"{path}.{key}" if path else key) if isinstance(key, str) else None
                if below is None or (below not in types and below not in containers):
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
    prepared = _prepared(document)
    if prepared is None:
        return _refuse(Rule.DOCUMENT_NOT_TEXT)
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
