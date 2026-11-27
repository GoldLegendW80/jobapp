"""Runs of words copied from the posting (SPEC-documents.md, "Being careful", copied runs): pure.

After the "echo traps" of ai-job-search (MIT, NOTICE): a document answers the posting in the
person's own voice. The posting's hard terms are reused as they are, its sentences never: five
or more consecutive words of the posting read as the posting pasted back.

The rule:

- **Words.** Both texts are folded by `rules.fold` (the fold of the banned phrases: case,
  accents, compatibility forms and spacing), then read as words, a word being a run of letters
  and digits. Punctuation, line breaks and spacing never start or end a run: "build, data
  pipelines" is "build data pipelines".
- **A copied run** is a run of consecutive words of one field that stands, word for word, in
  the posting's text, and that counts RUN_WORDS (5) words or more.
- **The posting's own terms aside.** The words of these, found in the field as whole words (the
  longer first), must still match the posting but are not counted: the posting's title; the
  company's name; each term of the person's own skills (`DocumentSources.terms`: the profile's
  skills terms, the base CV's skills items and competencies); and a quoted name, 1 to
  QUOTED_WORDS_MAX words between double quotation marks ("...", “...”, „...“, «...»), a
  product or a certification. They neither make a run nor break one.
- **What stays found.** The posting's sentence around those terms: "build data pipelines with
  Python and Airflow", copied, counts "build data pipelines with and" and is a run. A quote of
  more than QUOTED_WORDS_MAX words is read as text, never as a name. A term of the posting that
  is not one of the person's skills counts like any word. A run in the posting's other
  language matches only where the words are the same.

Which fields: those of `checks.copied_fields` (a letter's printed fields but its recipient, a
CV's free sentences). The posting's text is a stranger's: it is only read as words here, never
passed on, and capped at POSTING_CHARS_MAX characters.

Linear: the posting's words make a suffix automaton (at most twice as many states as words),
and each word of a field is read once against it, which gives, at each word, the longest run
ending there that the posting holds.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from app.documents import rules

RUN_WORDS = 5
QUOTED_WORDS_MAX = 8
POSTING_CHARS_MAX = 60_000

_WORD = re.compile(r"[^\W_]+")  # one class under one quantifier: linear
# Opening mark: its closing marks. A single quote is left out: it is an apostrophe as often.
_QUOTES = {'"': '"', "\u201c": "\u201d", "\u201e": "\u201c\u201d", "\xab": "\xbb"}


@dataclass(frozen=True, slots=True)
class _Word:
    start: int  # into the original text
    end: int
    folded: str


def words(text: str) -> list[_Word]:
    """The words of `text` after `rules.fold`, with their spans in `text`."""
    folded = rules.fold(text)
    out = []
    for match in _WORD.finditer(folded.text):
        start, end = folded.span(match.start(), match.end())
        out.append(_Word(start, end, match.group()))
    return out


@dataclass(frozen=True, slots=True)
class _Automaton:
    moves: tuple[dict[str, int], ...]
    links: tuple[int, ...]
    lengths: tuple[int, ...]


@lru_cache(maxsize=8)
def _automaton(posting: tuple[str, ...]) -> _Automaton:
    """The suffix automaton of the posting's words: every run of the posting is a path."""
    moves: list[dict[str, int]] = [{}]
    links = [-1]
    lengths = [0]
    last = 0
    for word in posting:
        current = len(moves)
        moves.append({})
        lengths.append(lengths[last] + 1)
        links.append(0)
        at = last
        while at != -1 and word not in moves[at]:
            moves[at][word] = current
            at = links[at]
        if at != -1:
            target = moves[at][word]
            if lengths[at] + 1 == lengths[target]:
                links[current] = target
            else:
                clone = len(moves)
                moves.append(dict(moves[target]))
                lengths.append(lengths[at] + 1)
                links.append(links[target])
                while at != -1 and moves[at].get(word) == target:
                    moves[at][word] = clone
                    at = links[at]
                links[target] = clone
                links[current] = clone
        last = current
    return _Automaton(tuple(moves), tuple(links), tuple(lengths))


@dataclass(frozen=True, slots=True)
class Posting:
    """What a field is checked against: the posting's words, and its own terms as tuples of
    folded words, grouped by their first word, the longer first."""

    automaton: _Automaton
    terms: dict[str, tuple[tuple[str, ...], ...]]


def posting(text: str, own_terms: Iterable[str]) -> Posting | None:
    """The posting's text (capped) and the terms left aside; None when it has no words."""
    found = tuple(word.folded for word in words(text[:POSTING_CHARS_MAX]))
    if not found:
        return None
    grouped: dict[str, set[tuple[str, ...]]] = {}
    for term in own_terms:
        key = tuple(word.folded for word in words(term))
        if key:
            grouped.setdefault(key[0], set()).add(key)
    terms = {
        first: tuple(sorted(keys, key=lambda k: (-len(k), k))) for first, keys in grouped.items()
    }
    return Posting(_automaton(found), terms)


def _quoted(text: str, found: Sequence[_Word]) -> set[int]:
    """The indices of the words inside a quotation of 1 to QUOTED_WORDS_MAX words, on one line."""
    out: set[int] = set()
    starts = [word.start for word in found]
    at = 0
    while at < len(text):
        closers = _QUOTES.get(text[at])
        if closers is None:
            at += 1
            continue
        end = at + 1
        while end < len(text) and text[end] not in closers and text[end] != "\n":
            end += 1
        if end >= len(text) or text[end] == "\n":
            at = end
            continue
        first = last = bisect_right(starts, at)
        while last < len(found) and found[last].end <= end:
            last += 1
        if 1 <= last - first <= QUOTED_WORDS_MAX:
            out.update(range(first, last))
        at = end + 1
    return out


def _aside(
    text: str, found: Sequence[_Word], terms: dict[str, tuple[tuple[str, ...], ...]]
) -> list[bool]:
    """For each word of the field: is it a word of one of the posting's own terms?"""
    aside = [False] * len(found)
    for index in _quoted(text, found):
        aside[index] = True
    i = 0
    while i < len(found):
        for key in terms.get(found[i].folded, ()):
            if tuple(word.folded for word in found[i : i + len(key)]) == key:
                for j in range(i, i + len(key)):
                    aside[j] = True
                i += len(key) - 1
                break
        i += 1
    return aside


def runs(text: str, against: Posting | None) -> list[tuple[int, int]]:
    """The spans of `text` covered by a copied run, in order, overlapping runs merged."""
    if against is None or not text:
        return []
    found = words(text)
    aside = _aside(text, found, against.terms)
    counted = [0]
    for flag in aside:
        counted.append(counted[-1] + (0 if flag else 1))
    machine = against.automaton
    spans: list[tuple[int, int]] = []
    state = length = 0
    for i, word in enumerate(found):
        while state and word.folded not in machine.moves[state]:
            state = machine.links[state]
            length = machine.lengths[state]
        if word.folded in machine.moves[state]:
            state = machine.moves[state][word.folded]
            length += 1
        else:
            state = length = 0
        first = i + 1 - length
        if length and counted[i + 1] - counted[first] >= RUN_WORDS:
            start, end = found[first].start, found[i].end
            if spans and start <= spans[-1][1]:
                spans[-1] = (spans[-1][0], end)
            else:
                spans.append((start, end))
    return spans
