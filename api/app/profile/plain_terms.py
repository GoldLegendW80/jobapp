"""The plain words a saved regular expression stands for (owner, 2026-10-28: the search setup asks
for words, never for regular expressions, and the search agent reads every saved word).

A search list saved before that decision may hold regular expressions (`advanced_regex` true, as
the importer wrote the engine's boards.json). The stored list is never rewritten: the filters keep
reading it as the expression it is (jobs J16). Only what a website search types is plain words,
so `plain_words(term)` gives the words of one expression when that is safe, else None:

- accepted: letters and digits, a space, an apostrophe, a hyphen, and the escaped forms of
  these; `\\b`, `^` and `$` at either end (a plain word is matched whole anyway); a
  separator between two words: `\\s` or `\\s+` is a space, `.`, `[ -]` or `[\\s-]` a space or
  a hyphen ("forward.deployed" is "forward deployed" and "forward-deployed"), each optional
  with `?` or `*`; `x?` for one optional letter or group ("solutions? architect"); groups
  `(...)` or `(?:...)` with alternatives `a|b`, and the same at the top; a class of a few
  letters `[sz]`.
- refused (None): anything else, such as `.*`, `\\w`, `\\d`, `+` or `{n}` after a letter, a
  negated class, a look around, a backreference, `\\b` inside, or an expression that gives more
  than VARIANTS_MAX words or a word that is not a plain one (`PLAIN`, the shape a website search
  types).

An expression searched anywhere in a text also matched inside a longer word ("engineer" found
"engineering"); its words match whole words with an optional plural, as every plain term does.
That narrowing is the plain rule the owner chose, and the website's own search widens it again.
"""

import re

from app.profile.schemas import TermList

# The shape of a word a website search types (app/browser/sources/interactive.py).
PLAIN = re.compile(r"[^\W_]+(?:[ '-][^\W_]+)*")
VARIANTS_MAX = 8
DEPTH_MAX = 8  # groups inside groups
SEPARATORS_MAX = VARIANTS_MAX.bit_length() - 1  # 2 ** 3 words is VARIANTS_MAX
_EDGE_START = re.compile(r"^(?:\^|\\b)+")
_EDGE_END = re.compile(r"(?:\$|\\b)+$")
_SEP = "\x00"  # a separator between two words that may be a space or a hyphen
_SEPARATOR_CLASSES = frozenset({"[ -]", "[- ]", "[\\s-]", "[-\\s]", "[\\s\\-]", "[\\- ]", "[ \\-]"})
_LITERAL_ESCAPES = frozenset(" -'")
PLURALS = ("s", "es")


class _UnsafeError(Exception):
    """The expression holds something that is not a plain word."""


def _alternatives(pattern: str, at: int, depth: int) -> tuple[list[str], int]:
    """The strings of `a|b|...` from `at` to the closing parenthesis (or the end at depth 0)."""
    found: list[str] = []
    while True:
        branch, at = _sequence(pattern, at, depth)
        found.extend(branch)
        if at < len(pattern) and pattern[at] == "|":
            at += 1
            continue
        return found, at


def _atom(pattern: str, at: int, depth: int) -> tuple[list[str], int, bool]:
    """One item and where it ends; True when it is a separator."""
    char = pattern[at]
    if char == "(":
        if depth >= DEPTH_MAX:
            raise _UnsafeError
        at += 1
        if pattern.startswith("?:", at):
            at += 2
        elif at < len(pattern) and pattern[at] == "?":
            raise _UnsafeError  # a look around, a named group, a flag
        inner, at = _alternatives(pattern, at, depth + 1)
        if at >= len(pattern) or pattern[at] != ")":
            raise _UnsafeError
        return inner, at + 1, False
    if char == "[":
        end = pattern.find("]", at + 1)
        if end < 0:
            raise _UnsafeError
        text = pattern[at : end + 1]
        if text in _SEPARATOR_CLASSES:
            return [_SEP], end + 1, True
        members = text[1:-1]
        if not members or len(members) > 4 or not all(m.isalnum() for m in members):
            raise _UnsafeError  # a negated class, a range, an escape, or too many letters
        return list(dict.fromkeys(members)), end + 1, False
    if char == "\\":
        escaped = pattern[at : at + 2]
        if escaped == "\\s":
            return [" "], at + 2, True
        if len(escaped) == 2 and escaped[1] in _LITERAL_ESCAPES:
            return [escaped[1]], at + 2, False
        raise _UnsafeError
    if char == ".":
        return [_SEP], at + 1, True
    if char.isalnum() or char in " '-":
        return [char], at + 1, False
    raise _UnsafeError


def _sequence(pattern: str, at: int, depth: int) -> tuple[list[str], int]:
    """The strings of the items from `at` to the next `|` or `)` of this depth."""
    found = [""]
    while at < len(pattern) and pattern[at] not in "|)":
        dot = pattern[at] == "."
        options, at, separator = _atom(pattern, at, depth)
        quantifier = pattern[at] if at < len(pattern) else ""
        if quantifier and quantifier in "?*+":
            if dot and quantifier != "?":
                raise _UnsafeError  # `.*` and `.+` stand for any text, not for a separator
            at += 1
            if quantifier == "?" or (separator and quantifier == "*"):
                options = [*options, ""]
            elif not separator:
                raise _UnsafeError  # one or more of a letter or a group: not a word
            if at < len(pattern) and pattern[at] in "?+*{":
                raise _UnsafeError  # a lazy or possessive quantifier
        elif quantifier == "{":
            raise _UnsafeError
        found = [left + right for left in found for right in options]
        if len(found) > VARIANTS_MAX * 4:
            raise _UnsafeError
    if depth == 0 and at < len(pattern) and pattern[at] == ")":
        raise _UnsafeError
    return found, at


def _words(variant: str) -> list[str]:
    """A variant with its separators read as a space and as a hyphen."""
    words = [""]
    for part in variant.split(_SEP)[:-1]:
        words = [word + part + glue for word in words for glue in (" ", "-")]
    tail = variant.split(_SEP)[-1]
    return [word + tail for word in words]


def plain_words(pattern: str) -> list[str] | None:
    """The plain words `pattern` stands for, in order, or None when converting it is not safe."""
    core = _EDGE_END.sub("", _EDGE_START.sub("", pattern.strip()))
    if not core:
        return None
    try:
        variants, at = _alternatives(core, 0, 0)
    except (_UnsafeError, IndexError):
        return None
    if at != len(core):
        return None
    # Each separator doubles a variant's words (a space and a hyphen): past SEPARATORS_MAX the
    # words are more than VARIANTS_MAX whatever follows, so refuse before building them.
    if any(variant.count(_SEP) > SEPARATORS_MAX for variant in variants):
        return None
    words: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        for word in _words(variant):
            if not PLAIN.fullmatch(word):
                return None  # an empty choice, a doubled space, a word cut at its edge
            if word.casefold() not in seen:
                seen.add(word.casefold())
                words.append(word)
    # "engineers?" is "engineer": a plain word already matches its plural ending s or es.
    words = [
        word
        for word in words
        if not any(
            word.casefold() == f"{other.casefold()}{end}" for other in words for end in PLURALS
        )
    ]
    if len(words) > VARIANTS_MAX:
        return None
    return words


def typed_words(terms: TermList) -> tuple[list[str], int]:
    """The words a website search may type for a saved list, and how many saved terms gave none.

    A plain list is its terms. A list of regular expressions gives the words of each expression
    that converts (`plain_words`) and counts the others, which stay in the criteria the search
    agent reads and in the filters, as the expressions they are."""
    if not terms.advanced_regex:
        return list(terms.terms), 0
    words: list[str] = []
    seen: set[str] = set()
    dropped = 0
    for term in terms.terms:
        converted = plain_words(term)
        if converted is None:
            dropped += 1
            continue
        for word in converted:
            if word.casefold() not in seen:
                seen.add(word.casefold())
                words.append(word)
    return words, dropped
