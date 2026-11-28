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
  the posting's text, and that counts RUN_WORDS (5) words or more, of which at least
  CONTENT_WORDS_MIN (2) are not small words: "in order to be able to" is no run.
- **Small words** (`SMALL_WORDS`, `SMALL_PHRASES`): a closed list, the closed classes of
  English, French and German as `rules.fold` gives them: articles and determiners, pronouns,
  prepositions, conjunctions, the forms of the auxiliary and modal verbs (be, have, do, can,
  will, ...; être, avoir, pouvoir, devoir; sein, haben, werden, können, müssen, ...), negations,
  the pieces of an elision or a contraction ("l'", "qu'", "n't"), and a few degree adverbs
  ("very", "also", "très", "sehr"); and the words of a fixed phrase that works as one of them
  ("in order to", "able to", "as well as", "afin de", "au sein de", "in der Lage"), small only
  inside that phrase ("order" alone counts). Every other word counts as content: a word the
  list does not name is never taken for a small word.
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
CONTENT_WORDS_MIN = 2
QUOTED_WORDS_MAX = 8
POSTING_CHARS_MAX = 60_000

_SMALL_ENGLISH = """
a an the this that these those some any each every no all both either neither another other
such what which whose who whom i me my mine myself you your yours yourself yourselves he him his
himself she her hers herself it its itself we us our ours ourselves they them their theirs
themselves one something anything nothing everything someone anyone everyone about above across
after against along among around as at before behind below beside besides between beyond by
despite down during except for from in inside into like near of off on onto out over past per
since through throughout till to toward towards under until up upon via with within without and
but or nor so yet because if unless whether while whereas although though than then when where
once how why there here be am is are was were been being have has had having do does did doing
done can could may might must shall should will would not never very too also just only even
more most much many few less s t re ll d ve m
"""
_SMALL_FRENCH = """
le la les l un une des du de d au aux ce cet cette ces mon ma mes ton ta tes son sa ses notre
nos votre vos leur leurs quel quelle quels quelles chaque tout toute tous toutes aucun aucune
plusieurs je j me m moi tu te t toi il elle on nous vous ils elles se s soi lui y en qui que qu
quoi dont ou celui celle ceux celles cela ca ceci a dans par pour sur sous avec sans chez entre
vers depuis pendant avant apres selon contre parmi et mais donc or ni car si quand comme lorsque
puisque suis es est sommes etes sont etait etaient sera serez seront serait ete etre ai as avons
avez ont avait avaient aura aurez auront aurait eu avoir peut pouvez peuvent pourrez pourrait
pouvoir doit devez doivent devrez devrait devoir ne n pas plus jamais rien tres aussi trop
"""
_SMALL_GERMAN = """
der die das den dem des ein eine einen einem einer eines kein keine keinen keinem keiner keines
dieser diese dieses diesen diesem jeder jede jedes jeden jedem alle allen aller mein meine
meinen meinem meiner sein seine seinen seinem seiner ihr ihre ihren ihrem ihrer unser unsere
unseren unserem unserer welche welcher welches ich mich mir du dich dir er ihn ihm sie es wir
uns euch ihnen man sich was wer wen wem an auf aus bei bis durch fur gegen hinter in im ins am
ans beim vom zum zur mit nach neben ohne seit uber um unter von vor wahrend wegen zu zwischen und
oder aber denn sondern dass ob weil wenn als wie damit sowie bin bist ist sind seid war waren sei
gewesen habe hast hat haben habt hatte hatten gehabt werde wirst wird werden werdet wurde wurden
kann konnen konnte muss mussen musste soll sollen sollte will wollen darf durfen mochte mochten
nicht nie sehr auch noch nur schon mehr so
"""
SMALL_WORDS = frozenset(
    rules.fold(word).text for word in (_SMALL_ENGLISH + _SMALL_FRENCH + _SMALL_GERMAN).split()
)
SMALL_PHRASES = (
    "in order to",
    "in order for",
    "able to",
    "unable to",
    "as well as",
    "so that",
    "such as",
    "rather than",
    "according to",
    "due to",
    "in terms of",
    "in addition to",
    "afin de",
    "afin que",
    "au sein de",
    "au sein du",
    "au sein des",
    "en mesure de",
    "grace a",
    "ainsi que",
    "en tant que",
    "par rapport a",
    "in der lage",
    "im rahmen",
    "so dass",
)

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


@lru_cache(maxsize=2)
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


Grouped = dict[str, tuple[tuple[str, ...], ...]]


def _grouped(phrases: Iterable[str]) -> Grouped:
    """Each phrase as a tuple of folded words, grouped by its first word, the longer first."""
    grouped: dict[str, set[tuple[str, ...]]] = {}
    for phrase in phrases:
        key = tuple(word.folded for word in words(phrase))
        if key:
            grouped.setdefault(key[0], set()).add(key)
    return {
        first: tuple(sorted(keys, key=lambda k: (-len(k), k))) for first, keys in grouped.items()
    }


_SMALL_GROUPED = _grouped(SMALL_PHRASES)


def posting(text: str, own_terms: Iterable[str]) -> Posting | None:
    """The posting's text (capped) and the terms left aside; None when it has no words."""
    found = tuple(word.folded for word in words(text[:POSTING_CHARS_MAX]))
    if not found:
        return None
    return Posting(_automaton(found), _grouped(own_terms))


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


def _mark(found: Sequence[_Word], phrases: Grouped, marks: list[bool]) -> None:
    """Set the mark of each word inside one of `phrases`, the longer first, left to right."""
    i = 0
    while i < len(found):
        for key in phrases.get(found[i].folded, ()):
            if tuple(word.folded for word in found[i : i + len(key)]) == key:
                for j in range(i, i + len(key)):
                    marks[j] = True
                i += len(key) - 1
                break
        i += 1


def _aside(text: str, found: Sequence[_Word], terms: Grouped) -> list[bool]:
    """For each word of the field: is it a word of one of the posting's own terms?"""
    aside = [False] * len(found)
    for index in _quoted(text, found):
        aside[index] = True
    _mark(found, terms, aside)
    return aside


def _small(found: Sequence[_Word]) -> list[bool]:
    """For each word of the field: is it a small word, alone or inside a small phrase?"""
    small = [word.folded in SMALL_WORDS for word in found]
    _mark(found, _SMALL_GROUPED, small)
    return small


def runs(text: str, against: Posting | None) -> list[tuple[int, int]]:
    """The spans of `text` covered by a copied run, in order, overlapping runs merged."""
    if against is None or not text:
        return []
    found = words(text)
    aside = _aside(text, found, against.terms)
    counted, content = [0], [0]
    for flag, small in zip(aside, _small(found), strict=True):
        counted.append(counted[-1] + (0 if flag else 1))
        content.append(content[-1] + (0 if flag or small else 1))
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
        if (
            length
            and counted[i + 1] - counted[first] >= RUN_WORDS
            and content[i + 1] - content[first] >= CONTENT_WORDS_MIN
        ):
            start, end = found[first].start, found[i].end
            if spans and start <= spans[-1][1]:
                spans[-1] = (spans[-1][0], end)
            else:
                spans.append((start, end))
    return spans
