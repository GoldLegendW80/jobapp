"""The checks of a document (SPEC-documents.md, "Being careful", steps 1 to 3): pure.

`run(kind, content, sources, settings) -> Checks` is the one entry point: the submit tools, the
api (the notes on a hand edit) and the chat all call it. It finds, in this order of levels:

1. `invalid`: the schema and the hard limits, and, given `employer_keys`, an experience entry
   whose key is no fixed employer. Content that fails the schema is checked no further;
2. `claim`: M9's `factcheck_rules.check` per printed field, mapped back to `{field, start, end,
   code}`, with what `DocumentSources` adds (below); the credentials, change words and decades
   the person's texts do not state (`rules.credentials`, `rules.changes`, `rules.decades`); and
   the person's banned phrases (`banned_phrase`);
3. `separator`: with the person's dash rule on, each dash used as a separator;
4. `note`: the template's soft lengths, given `soft_limits`, and an empty required field.

Nothing here refuses a render, a download or a send: `invalid` is answered to a writer at
submit, everything else is corrected (`corrections`) or shown. Each claim carries `rewrite`
from `rules.POLICY` (SPEC-documents.md step 5): set, it is ambiguous by construction and waits
for the model rewrite (`Checks.needs_rewrite`).

Pure and deterministic: the same content, sources and settings give the same issues in the
same order: by level, then by the field's place in the document, then by position and code.

What the documents add to M9, each matched as the kind of thing it is, so that it sources
nothing else:
- a number that only an answer states sources a sentence about the same thing only: one that
  shares a word with the answer or its question ("My notice period is three months" passes,
  "Managed three squads" does not because the notice period answer says "Three months");
- the person's phone, e-mail, website and links source the same phone, address or link;
- a numeric date ("12/2022", "2022-12") equal to a stored period of an employer or a school is
  sourced, a bare "12" never;
- a duration is anchored to the organisations named in its sentence ("three years as X at
  Y", "at Y for three years", "N years at Y"), else to the experience entry that holds it, and
  is sourced when one of their periods holds N whole years (or months) counted from the stored
  months, an open period up to the generation date `today`; a duration of nothing named is the
  person's total, sourced by any whole number of years at or under `years_experience`
  ("8 years" for 8.5; rounded up stays a claim);
- a date said of one of the person's organisations ("since 2021", "depuis janvier 2022",
  "(2017 to 2021)", "(2021 to present)", "2021 à aujourd'hui") is sourced by that
  organisation's stored start and end only (an open end by an open period), never by the same
  year elsewhere in the profile (`date_not_in_profile`); a year of an experience entry's bullet
  ("in 2018") by that entry's own period only;
- a bare number the person's texts state only as a percent is a claim ("Managed 20 engineers"
  with "by 20%");
- a title that M9 reads with a connector before it ("then Software Engineer") is read without
  it; a descriptive title ("Backend engineer") is sourced by one of the person's titles with
  the same head noun and seniority; a seniority word the person's titles lack is the claim
  alone (`seniority_not_in_profile`); a letter's posting title held at one of the person's
  employers is a claim;
- a degree after "with" ("with an MSc") is a credential, never an employer.

For speed, M9 runs once over the printed fields joined by a neutral break (a pilcrow between
blank lines) rather than once per field, which would fold every source again for every field;
each issue is then mapped back to its field. `test_checks` proves both give the same issues.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date

from pydantic import BaseModel

from app.documents import content as c
from app.documents import rules
from app.documents.rules import Issue
from app.documents.settings import DocumentSettings
from app.documents.sources import DocumentSources
from app.mail import factcheck_rules
from app.mail.factcheck_rules import Sources

FIELD_BREAK = "\n\n\u00b6\n\n"


@dataclass(frozen=True, slots=True)
class Checks:
    kind: c.Kind
    content: c.CvContent | c.LetterContent | None  # validated; None when the schema failed
    issues: tuple[Issue, ...]
    known_terms: tuple[str, ...] = ()  # the skills terms of the sources: what a version follows

    def _level(self, level: rules.Level) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.level == level)

    @property
    def invalid(self) -> tuple[Issue, ...]:
        return self._level("invalid")

    @property
    def claims(self) -> tuple[Issue, ...]:
        return self._level("claim")

    @property
    def separators(self) -> tuple[Issue, ...]:
        return self._level("separator")

    @property
    def notes(self) -> tuple[Issue, ...]:
        return self._level("note")

    @property
    def needs_rewrite(self) -> tuple[Issue, ...]:
        """The claims ambiguous by construction (`rules.POLICY`), for the model rewrite."""
        return tuple(issue for issue in self.claims if issue.rewrite)


def fact_issues(
    fields: list[tuple[str, str]],
    sources: Sources,
    *,
    owners: dict[str, str] | None = None,
    today: date | None = None,
) -> list[Issue]:
    """M9 over the fields at once, each issue mapped back onto its field (clipped to it), less
    those that `DocumentSources` sources (the module docstring), and with the dates anchored to
    their employer (`_anchored_dates`). Linear in the fields: the sentences, the anchored dates
    and the words of each answer are computed once.

    `owners` maps an experience entry's path ("experience.1") to its employer key: a year in
    that entry is that employer's. `today` is the generation date, given as data so the checks
    stay pure: an open period then covers its whole years up to it. Without it an open period
    sources no duration."""
    starts: list[int] = []
    parts: list[str] = []
    at = 0
    for _, text in fields:
        starts.append(at)
        parts.append(text)
        at += len(text) + len(FIELD_BREAK)
    body = FIELD_BREAK.join(parts)
    found = factcheck_rules.check(body, replace(sources, answers=()))
    if sources.answers:
        with_answers = {(i.start, i.end, i.code) for i in factcheck_rules.check(body, sources)}
    else:
        with_answers = {(i.start, i.end, i.code) for i in found}
    topics = sources.answer_topics if isinstance(sources, DocumentSources) else ()
    if not topics:
        topics = tuple(("", text) for text in sources.answers)
    scope = _AnswerScope(topics)
    apart = _Apart(sources, owners or {}, today) if isinstance(sources, DocumentSources) else None
    out: list[Issue] = []
    anchored: dict[int, list[tuple[int, int, bool]]] = {}
    in_entry: dict[int, list[tuple[int, int, bool]]] = {}
    if apart is not None:
        for index, (path, text) in enumerate(fields):
            anchored[index] = _anchored_dates(text, apart.names)
            out += [
                Issue(path, s, e, rules.DATE_AT_EMPLOYER, "claim")
                for s, e, ok in anchored[index]
                if not ok
            ]
            in_entry[index] = apart.entry_years(path, text, anchored[index])
            out += [
                Issue(path, s, e, rules.DATE_AT_EMPLOYER, "claim")
                for s, e, ok in in_entry[index]
                if not ok
            ]
            out += [
                Issue(path, a, b, code, "claim")
                for s, e in apart.posting_titles_at_employers(text)
                for a, b, code in apart.seniority_apart(text, s, e, "title_not_in_profile")
            ]
    for issue in found:
        index = bisect_right(starts, issue.start) - 1
        if index < 0:
            continue
        path, text = fields[index]
        start = issue.start - starts[index]
        end = min(issue.end - starts[index], len(text))
        if start >= len(text) or end <= start:
            continue
        if issue.code in _DATE_CODES and any(
            s <= start and end <= e for s, e, _ in anchored.get(index, ())
        ):
            continue  # the date at an employer is that employer's date, checked above
        if issue.code in _DATE_CODES and any(
            s <= start and end <= e for s, e, _ in in_entry.get(index, ())
        ):
            continue  # a year of an experience entry is that entry's, checked above
        answered = (issue.start, issue.end, issue.code) not in with_answers
        if answered and scope.about(index, text, start, end):
            continue
        if apart is None:
            out.append(Issue(path, start, end, issue.code, "claim"))
            continue
        out += [
            Issue(path, s, e, code, "claim")
            for s, e, code in apart.adjust(path, text, start, end, issue.code)
        ]
    return out


class _AnswerScope:
    """Is a claim that only an answer sources in a sentence about what that answer answers? The
    sentence must share a word, outside the claim, with the answer or its question. The words
    of each answer and the sentences of each field are computed once, and whether an answer
    states a claim once per claim text: one pass over a field, whatever its number of claims."""

    def __init__(self, topics: tuple[tuple[str, str], ...]) -> None:
        self.answers = [
            (answer, _content_words(answer) | set(topic.split())) for topic, answer in topics
        ]
        self.sentences: dict[int, tuple[list[int], list[tuple[int, int]]]] = {}
        self.words: dict[tuple[int, int], set[str]] = {}
        self.states: dict[str, list[bool]] = {}

    def about(self, field: int, text: str, start: int, end: int) -> bool:
        if field not in self.sentences:
            spans = rules.sentences(text)
            self.sentences[field] = ([s for s, _ in spans], spans)
        firsts, spans = self.sentences[field]
        at = max(bisect_right(firsts, start) - 1, 0)
        key = (field, at)
        if key not in self.words:
            s, e = spans[at] if spans else (0, len(text))
            self.words[key] = _content_words(text[s:e])
        claim = text[start:end]
        around = self.words[key] - _content_words(claim)
        if not around:
            return False
        if claim not in self.states:
            self.states[claim] = [
                not factcheck_rules.check(claim, Sources(answers=(answer,)))
                for answer, _ in self.answers
            ]
        return any(
            states and around & words
            for states, (_, words) in zip(self.states[claim], self.answers, strict=True)
        )


_IDENTITY_CODES = frozenset(("phone_not_in_profile", "email_not_in_profile", "link_not_in_profile"))
_DATE_CODES = frozenset(("number_not_in_sources", "date_not_in_sources", "year_not_in_sources"))
_DURATION_UNITS = {
    "year": 12,
    "years": 12,
    "yr": 12,
    "yrs": 12,
    "an": 12,
    "ans": 12,
    "annee": 12,
    "annees": 12,
    "month": 1,
    "months": 1,
    "mois": 1,
}
_SMALL_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "un": 1,
    "une": 1,
    "deux": 2,
    "trois": 3,
    "quatre": 4,
    "cinq": 5,
    "sept": 7,
    "huit": 8,
    "neuf": 9,
    "dix": 10,
}
_AT = frozenset(("at", "chez", "with", "for", "pour", "a", "au"))
# Words that say nothing of what a sentence is about.
_PLAIN = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "have",
        "has",
        "had",
        "are",
        "was",
        "were",
        "will",
        "would",
        "can",
        "could",
        "not",
        "but",
        "you",
        "your",
        "our",
        "their",
        "his",
        "her",
        "its",
        "into",
        "over",
        "under",
        "about",
        "after",
        "before",
        "since",
        "per",
        "les",
        "des",
        "une",
        "pour",
        "avec",
        "dans",
        "sur",
        "par",
        "pas",
        "qui",
        "que",
        "est",
        "sont",
        "aux",
        "ces",
        "son",
        "ses",
        "mon",
        "mes",
        "my",
        "is",
        "am",
        "be",
        "of",
        "to",
        "in",
        "on",
        "at",
        "an",
        "a",
        "i",
        "je",
    ]
)


def _content_words(text: str) -> set[str]:
    """The folded words of `text` that say what it is about."""
    words = (word.strip('.,;:!?()"') for word in rules.fold(text).text.replace("'", " ").split())
    return {word for word in words if word and word not in _PLAIN}


# The head nouns of a title, English and French read as one ("Développeur" is a "Developer"),
# and the seniority words a title claim may carry only when one of the person's titles does.
_TITLE_HEADS = {
    "engineer": "engineer",
    "ingenieur": "engineer",
    "ingenieure": "engineer",
    "developer": "developer",
    "developpeur": "developer",
    "developpeuse": "developer",
    "programmer": "developer",
    "architect": "architect",
    "architecte": "architect",
    "analyst": "analyst",
    "analyste": "analyst",
    "consultant": "consultant",
    "consultante": "consultant",
    "scientist": "scientist",
    "researcher": "researcher",
    "chercheur": "researcher",
    "chercheuse": "researcher",
    "designer": "designer",
    "manager": "manager",
}
_SENIORITY = frozenset(
    ["senior", "sr", "lead", "principal", "staff", "head", "chief", "confirme", "confirmee"]
)
_SPAN_CODES = frozenset(
    ("title_not_in_profile", "employer_not_in_profile", "school_not_in_profile")
)


class _Apart:
    """What `DocumentSources` sources apart from M9, and how an issue of M9 is narrowed to the
    claim it makes (the module docstring). Built once per check; each lookup is a bounded scan
    of the claim's own words."""

    def __init__(
        self, sources: DocumentSources, owners: dict[str, str], today: date | None
    ) -> None:
        self.sources = sources
        self.today = today
        self.names = _period_names(sources.periods)
        keyed = {key: (name, start, end) for key, name, start, end in sources.entries}
        self.owners = {path: keyed[key] for path, key in owners.items() if key in keyed}
        self._own: tuple[str, ...] | None = None
        self._titles: tuple[str, ...] | None = None
        self.own_titles = [
            {w.folded for w in rules.words_of(title)} for title in sources.own_titles
        ]
        self.posting = [
            tuple(w.folded for w in rules.words_of(title)) for title in sources.posting_titles
        ]
        self.employers = [
            tuple(w.folded for w in rules.words_of(name)) for name in sources.employers
        ]

    @property
    def own(self) -> tuple[str, ...]:
        if self._own is None:
            self._own = rules.corpus(self.sources.profile)
        return self._own

    @property
    def titles(self) -> tuple[str, ...]:
        if self._titles is None:
            s = self.sources
            self._titles = rules.corpus([*s.profile, *s.answers, *s.titles])
        return self._titles

    def entry(self, path: str) -> tuple[str, str, str] | None:
        """The (organisation, start, end) of the experience entry that holds the field `path`."""
        if not path.startswith("experience."):
            return None
        return self.owners.get(".".join(path.split(".", 2)[:2]))

    def entry_years(
        self, path: str, text: str, anchored: list[tuple[int, int, bool]]
    ) -> list[tuple[int, int, bool]]:
        """The years of an experience entry's field, with whether its own period holds them
        (`_entry_years`); none outside an entry."""
        entry = self.entry(path)
        if entry is None:
            return []
        return _entry_years(text, entry[1], entry[2], self.today, anchored)

    def adjust(
        self, path: str, text: str, start: int, end: int, code: str
    ) -> list[tuple[int, int, str]]:
        """The issue of M9 at `text[start:end]` as the claims it makes: none when sourced."""
        claim = text[start:end]
        s = self.sources
        if code in _IDENTITY_CODES:
            if s.identity and not factcheck_rules.check(claim, Sources(profile=s.identity)):
                return []
        elif code in _DATE_CODES:
            month = _numeric_month(claim)
            if month is not None and any(
                month in (_month_of(a), _month_of(b)) for _, a, b in s.periods
            ):
                return []
        elif code == "duration_not_in_profile":
            if _duration_sourced(text, start, end, s, self.names, self.entry(path), self.today):
                return []
        elif code in _SPAN_CODES:
            return self._span(text, start, end, code)
        return [(start, end, code)]

    def _span(self, text: str, start: int, end: int, code: str) -> list[tuple[int, int, str]]:
        # M9 takes the lowercase words before a name into its span ("then Software Engineer",
        # "puis Software Engineer"): they are connectors, never part of the claim. A seniority
        # word stays ("senior Software Engineer").
        words = rules.words_of(text[start:end])
        k = 0
        while (
            k < len(words) - 1
            and not words[k].capital
            and words[k].folded not in _SENIORITY
            and any(w.capital for w in words[k + 1 :])
        ):
            k += 1
        if k:
            start += words[k].start
            words = rules.words_of(text[start:end])
        claim = text[start:end]
        if code != "title_not_in_profile":
            # A degree or a certification after "with" ("with an MSc") is a credential, which
            # `rules.credentials` checks; trimmed, a name of the person's own is sourced.
            if rules.names_credential(claim):
                return []
            if k and rules.corpus_states(claim, self.titles):
                return []
            return [(start, end, code)]
        if self._title_sourced(claim, words):
            return []
        first = words[0]
        if first.folded in _SENIORITY and len(words) > 1:
            rest = claim[words[1].start :]
            if self._title_sourced(rest, rules.words_of(rest)):
                # Only the seniority word is unsourced: it alone is the claim.
                return [(start + first.start, start + first.end, rules.SENIORITY)]
        return self.seniority_apart(text, start, end, code)

    def seniority_apart(
        self, text: str, start: int, end: int, code: str
    ) -> list[tuple[int, int, str]]:
        """An unsourced title that opens with a seniority word none of the person's titles holds
        ("Staff Platform Engineer"): two claims, the word (by code, a high confidence claim) and
        the rest, which may paraphrase one of the person's titles (`rules.POLICY`)."""
        words = rules.words_of(text[start:end])
        if (
            len(words) > 1
            and words[0].folded in _SENIORITY
            and words[1].capital
            and not any(words[0].folded in title for title in self.own_titles)
        ):
            first, second = words[0], words[1]
            return [
                (start + first.start, start + first.end, rules.SENIORITY),
                (start + second.start, end, code),
            ]
        return [(start, end, code)]

    def _title_sourced(self, claim: str, words: list) -> bool:
        """The title as the person's texts state it, or a descriptive title ("Backend engineer",
        lowercase after its first word) whose head noun is the head of one of the person's
        titles, whose seniority words that title holds, and whose other words the person's
        texts state."""
        if rules.corpus_states(claim, self.titles):
            return True
        if not words or any(w.capital for w in words[1:]):
            return False
        heads = {_TITLE_HEADS[w.folded] for w in words if w.folded in _TITLE_HEADS}
        if not heads:
            return False
        seniority = {w.folded for w in words if w.folded in _SENIORITY}
        if not any(
            heads & {_TITLE_HEADS[x] for x in title if x in _TITLE_HEADS} and seniority <= title
            for title in self.own_titles
        ):
            return False
        others = [
            w.folded for w in words if w.folded not in _TITLE_HEADS and w.folded not in _SENIORITY
        ]
        return all(any(f" {w} " in entry for entry in self.own) for w in others)

    def posting_titles_at_employers(self, text: str) -> list[tuple[int, int]]:
        """A letter's posting title held at one of the person's employers ("As a Staff Platform
        Engineer at Orbite Données"): the posting sources the role applied for, never a title
        held elsewhere. A claim unless the person's own texts state that title."""
        if not self.posting:
            return []
        words = rules.words_of(text)
        out: list[tuple[int, int]] = []
        for title in self.posting:
            size = len(title)
            for i in range(len(words) - size - 1):
                if words[i].folded != title[0]:
                    continue
                if tuple(w.folded for w in words[i : i + size]) != title:
                    continue
                at = i + size
                if words[at].folded not in _AT or not self._employer_at(words, at + 1):
                    continue
                span = (words[i].start, words[at - 1].end)
                if not rules.corpus_states(text[span[0] : span[1]], self.own):
                    out.append(span)
        return out

    def _employer_at(self, words: list, i: int) -> bool:
        return any(
            name and tuple(w.folded for w in words[i : i + len(name)]) == name
            for name in self.employers
        )


# --- dates anchored to their employer -----------------------------------------------------------

_MONTH_WORDS = {
    **dict.fromkeys(("jan", "january", "janv", "janvier"), 1),
    **dict.fromkeys(("feb", "february", "fev", "fevr", "fevrier"), 2),
    **dict.fromkeys(("mar", "march", "mars"), 3),
    **dict.fromkeys(("apr", "april", "avr", "avril"), 4),
    **dict.fromkeys(("may", "mai"), 5),
    **dict.fromkeys(("jun", "june", "juin"), 6),
    **dict.fromkeys(("jul", "july", "juil", "juillet"), 7),
    **dict.fromkeys(("aug", "august", "aout"), 8),
    **dict.fromkeys(("sep", "sept", "september", "septembre"), 9),
    **dict.fromkeys(("oct", "october", "octobre"), 10),
    **dict.fromkeys(("nov", "november", "novembre"), 11),
    **dict.fromkeys(("dec", "december", "decembre"), 12),
}
_SINCE = frozenset(("since", "from", "depuis", "de", "starting"))
# The open end of a period ("2018 to present", "2018 à aujourd'hui", "2018 à ce jour"): the
# date before it is a start, and the period must be open.
_OPEN_ENDS = frozenset(("present", "now", "today", "date", "aujourd", "current", "currently"))
_OPEN: _Date = (0, None)
_UNTIL = frozenset(("to", "until", "till", "through", "jusqu"))
_RANGE_DASHES = frozenset("-\u2010\u2011\u2012\u2013\u2014\u2212")

_Date = tuple[int, int | None]


def _period_names(
    periods: tuple[tuple[str, str, str], ...],
) -> dict[str, list[tuple[tuple[str, ...], list[tuple[str, str]]]]]:
    """The stored organisations by their first folded word: (their words, their periods)."""
    grouped: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    for name, start, end in periods:
        words = tuple(w.folded for w in rules.words_of(name))
        if words:
            grouped.setdefault(words, []).append((start, end))
    out: dict[str, list[tuple[tuple[str, ...], list[tuple[str, str]]]]] = {}
    for words, rows in grouped.items():
        out.setdefault(words[0], []).append((words, rows))
    return out


def _role(text: str, word: rules.Word) -> str:
    """ "since" for a word that opens a start date, "until" for one that opens an end date.
    The French "à" and "dès" count with their accent only: "a" and "des" are other words."""
    original = text[word.start : word.end].lower()
    if word.folded in _SINCE or original == "d\u00e8s":
        return "since"
    if word.folded in _UNTIL or original == "\u00e0":
        return "until"
    return ""


def _year(word: str) -> int | None:
    return int(word) if len(word) == 4 and word.isdigit() and 1950 <= int(word) <= 2100 else None


def _date_at(text: str, words: list, i: int) -> tuple[_Date, int] | None:
    """The date that starts at word `i` and the index after it: "January 2022", "Jan 2022",
    "01/2022", "2022-01" (a year and a month), or "2022" (a year)."""
    n = len(words)
    if i >= n:
        return None
    word = words[i].folded
    gap = text[words[i].end : words[i + 1].start] if i + 1 < n else ""
    nxt = words[i + 1].folded if i + 1 < n else ""
    if word in _MONTH_WORDS and (year := _year(nxt)) and gap.strip(" .,") == "":
        return (year, _MONTH_WORDS[word]), i + 2
    if (year := _year(word)) is not None:
        if gap in ("-", "/") and 1 <= len(nxt) <= 2 and nxt.isdigit() and 1 <= int(nxt) <= 12:
            return (year, int(nxt)), i + 2
        return (year, None), i + 1
    if 1 <= len(word) <= 2 and word.isdigit() and gap in ("/", ".", "-") and (year := _year(nxt)):
        month = int(word)
        return ((year, month), i + 2) if 1 <= month <= 12 else None
    return None


def _open_end(words: list, i: int, stop: int) -> int | None:
    """The index after an open end that starts at word `i` ("present", "aujourd'hui", "ce
    jour", "date"), else None."""
    if i >= stop:
        return None
    word = words[i].folded
    if word == "ce" and i + 1 < stop and words[i + 1].folded == "jour":
        return i + 2
    if word in _OPEN_ENDS:
        return (
            i + 2 if word == "aujourd" and i + 1 < stop and words[i + 1].folded == "hui" else i + 1
        )
    return None


def _same(claim: _Date | None, stored: str) -> bool:
    if claim is None:
        return True
    if claim is _OPEN:
        return stored == ""
    month = _month_of(stored)
    year = month[0] if month else (int(stored) if stored.isdigit() else None)
    if year is None or claim[0] != year:
        return False
    return claim[1] is None or month is None or claim[1] == month[1]


def _anchored_dates(
    text: str, names: dict[str, list[tuple[tuple[str, ...], list[tuple[str, str]]]]]
) -> list[tuple[int, int, bool]]:
    """The dates of `text` said of one of the person's organisations, with whether they are
    its stored dates: `(start, end, true)`. A date after "since", "from", "depuis" or "de" is a
    start, after "to", "until", "à" or "jusqu'en" an end, and "<date> to <date>" or
    "<date>-<date>" a period; each belongs to the organisation named last before it in its
    sentence (else the first after it), and is true only when that organisation's stored start
    (and end) has the same year (and month, when both give one). A date of no organisation is
    left to M9. One pass over the words, each date and name read once."""
    if not names:
        return []
    words = rules.words_of(text)
    out: list[tuple[int, int, bool]] = []
    stop = 0
    for _, s_end in rules.sentences(text):
        first = stop
        while stop < len(words) and words[stop].start < s_end:
            stop += 1
        if first == stop:
            continue
        mentions: list[tuple[int, int, list[tuple[str, str]]]] = []
        k = first
        while k < stop:
            hit = None
            for name, rows in names.get(words[k].folded, ()):
                if tuple(w.folded for w in words[k : k + len(name)]) == name:
                    hit = (k, k + len(name), rows)
                    break
            if hit:
                mentions.append(hit)
                k = hit[1]
            else:
                k += 1
        if not mentions:
            continue
        i = first
        m = 0
        while i < stop:
            word = words[i].folded
            role = _role(text, words[i])
            j = i + 1 if role else i
            if word == "jusqu" and j < stop and words[j].folded in ("en", "a", "au"):
                j += 1
            found = _date_at(text, words, j) if j < stop else None
            if found is None:
                i += 1
                continue
            date, k = found
            begin: _Date | None = date if role != "until" else None
            finish: _Date | None = date if role == "until" else None
            if role != "until" and k < stop:
                gap = text[words[k - 1].end : words[k].start].strip()
                later = None
                if _role(text, words[k]) == "until" and gap == "":
                    later = _date_at(text, words, k + 1)
                    if later is None and (after := _open_end(words, k + 1, stop)) is not None:
                        later = (_OPEN, after)
                elif gap and all(ch in _RANGE_DASHES for ch in gap):
                    later = _date_at(text, words, k)
                    if later is None and (after := _open_end(words, k, stop)) is not None:
                        later = (_OPEN, after)
                if later is not None and later[1] <= stop:
                    finish, k = later
            if not role and finish is None:
                i += 1  # a year alone ("in 2017") belongs to no organisation here
                continue
            while m < len(mentions) and mentions[m][1] <= i:
                m += 1
            after = m
            while after < len(mentions) and mentions[after][0] < k:
                after += 1
            mention = mentions[m - 1] if m else (mentions[after] if after < len(mentions) else None)
            if mention is not None:
                ok = any(_same(begin, a) and _same(finish, b) for a, b in mention[2])
                out.append((words[i].start, words[k - 1].end, ok))
            i = k
    return out


def _month_of(stored: str) -> tuple[int, int] | None:
    """ "2022-12" as stored -> (2022, 12); a year alone has no month."""
    year, _, month = stored.partition("-")
    if len(year) == 4 and year.isdigit() and 1 <= len(month) <= 2 and month.isdigit():
        return int(year), int(month)
    return None


def _numeric_month(claim: str) -> tuple[int, int] | None:
    """ "12/2022", "12.2022", "2022-12", "2022/12" -> (2022, 12). Nothing else: a bare "12" is
    no date."""
    for sep in "/-.":
        left, found, right = claim.partition(sep)
        if not found or sep in right:
            continue
        if not (left.isdigit() and right.isdigit()):
            return None
        if len(left) == 4 and 1 <= len(right) <= 2:
            year, month = int(left), int(right)
        elif len(right) == 4 and 1 <= len(left) <= 2:
            year, month = int(right), int(left)
        else:
            return None
        return (year, month) if 1 <= month <= 12 else None
    return None


def _months_held(start: str, end: str, today: date | None = None) -> int | None:
    """The fewest whole months a period surely covers ("2020-09" to "2022-11": 27; a year alone
    counts from its December to the other's January). An open period counts to the month
    before `today`, the generation date given as data; None without it, and for no start."""
    if not start:
        return None
    first = _month_of(start) or ((int(start), 12) if start.isdigit() else None)
    if end:
        last = _month_of(end) or ((int(end), 1) if end.isdigit() else None)
    elif today is not None:
        last = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    else:
        return None
    if first is None or last is None:
        return None
    return max((last[0] - first[0]) * 12 + last[1] - first[1] + 1, 0)


# The words before a duration that make it a bound: more than N, at least N.
_BOUNDS = {
    ("over",): "gt",
    ("more", "than"): "gt",
    ("plus", "de"): "gt",
    ("at", "least"): "ge",
    ("au", "moins"): "ge",
}
# What a duration of the person's total is of: "8 years of experience", "8 years in data".
_EXPERIENCE = frozenset(
    ("experience", "professional", "work", "industry", "hands", "engineering", "career")
)


def _duration_claim(claim: str) -> tuple[int, int, str] | None:
    """ "three years", "more than 8 years", "5 ans" -> (value, months per unit, bound)."""
    words = rules.fold(claim).text.replace("'", " ").split()
    if len(words) < 2 or words[-1] not in _DURATION_UNITS:
        return None
    number = words[-2].rstrip("+")
    value = int(number) if number.isdigit() else _SMALL_NUMBERS.get(number)
    if not value:
        return None
    bound = _BOUNDS.get(tuple(words[:-2]), "") if len(words) > 2 else ""
    if len(words) > 2 and not bound:
        return None
    if number != words[-2]:
        bound = bound or "ge"  # "5+ years"
    return value, _DURATION_UNITS[words[-1]], bound


def _held(value: int, unit: int, bound: str, months: int) -> bool:
    """Does a period of `months` whole months make the duration true? A plain number is the
    whole units it holds (3 years and 5 months is "three years"), a bound is under it."""
    if bound == "gt":
        return value * unit < months
    if bound == "ge":
        return value * unit <= months
    return months // unit == value


def _named_in(sentence: str, names: dict[str, list[tuple[tuple[str, ...], list]]]) -> list:
    """The stored periods of the organisations `sentence` names."""
    words = [w.folded for w in rules.words_of(sentence)]
    out: list = []
    for k, word in enumerate(words):
        for name, rows in names.get(word, ()):
            if tuple(words[k : k + len(name)]) == name:
                out += rows
    return out


def _duration_sourced(
    text: str,
    start: int,
    end: int,
    sources: DocumentSources,
    names: dict[str, list[tuple[tuple[str, ...], list[tuple[str, str]]]]],
    entry: tuple[str, str, str] | None,
    today: date | None,
) -> bool:
    """A duration is anchored to the organisations its sentence names ("three years as a
    Software Developer at Kestrel", "at Kestrel for three years", "N years at Y"), else to the
    experience entry that holds it, and is then true when one of their periods holds it (an
    open period up to `today`). A duration of nothing named is the person's total: any whole
    number of years at or under `years_experience` (rounded down is true, rounded up a claim),
    unless it says what it is of ("5 years of Python")."""
    claim = _duration_claim(text[start:end])
    if claim is None:
        return False
    value, unit, bound = claim
    sentence = next(((s, e) for s, e in rules.sentences(text) if s <= start < e), (0, len(text)))
    periods = _named_in(text[sentence[0] : sentence[1]], names)
    if not periods and entry is not None:
        periods = [(entry[1], entry[2])]
    if periods:
        for first, last in periods:
            months = _months_held(first, last, today)
            if months is not None and _held(value, unit, bound, months):
                return True
        return False
    if not sources.years_experience or unit != 12:
        return False
    total = float(sources.years_experience[0])
    after = [w.folded for w in rules.words_of(text[end : end + 40])]
    if after[:1] in (["of"], ["d"], ["de"]) and (after[1:2] or [""])[0] not in _EXPERIENCE:
        return False
    if after[:1] and after[0] in _AT:
        return False  # "2 years at Nimbus Example": a period at an organisation not stored
    if bound == "gt":
        return value < total
    return value <= int(total)


# A word before a year that makes it a date of the sentence ("in 2018", "In 2018, ..."), not a
# version ("Office 2019", "Windows Server 2012").
_YEAR_BEFORE = frozenset(
    (
        "in",
        "since",
        "from",
        "by",
        "until",
        "till",
        "to",
        "through",
        "during",
        "after",
        "before",
        "early",
        "late",
        "mid",
        "en",
        "depuis",
        "de",
        "des",
        "jusqu",
        "a",
        "apres",
        "avant",
        "fin",
        "debut",
        "and",
        "et",
    )
)


def _entry_years(
    text: str,
    start: str,
    end: str,
    today: date | None,
    anchored: list[tuple[int, int, bool]],
) -> list[tuple[int, int, bool]]:
    """Each year of an experience entry's field said as a date ("in 2018", "In 2023, ...",
    "Jan 2022"), outside the dates `anchored` to a named organisation, with whether the entry's
    own period holds it: `(start, end, true)`. The span is the date ("Jan 2022", "2018");
    `corrections` cuts its preposition with it. Another period of the profile never sources
    it."""
    low = _month_of(start) or ((int(start), 1) if start.isdigit() else None)
    if low is None:
        return []
    high_month = _month_of(end) or ((int(end), 12) if end.isdigit() else None)
    high = high_month[0] if high_month else (today.year if today is not None else None)
    words = rules.words_of(text)
    out: list[tuple[int, int, bool]] = []
    for k, word in enumerate(words):
        year = _year(word.folded)
        if year is None or any(s <= word.start < e for s, e, _ in anchored):
            continue
        if word.end < len(text) and text[word.end] in "%+":
            continue
        before = words[k - 1] if k else None
        # A year after a word with only a space between is a date after a preposition or a
        # month; after anything else ("Office 2019") it is a version or part of a name. A year
        # that opens its sentence or follows a bracket or a comma is a date.
        at = word.start
        if before is not None and text[before.end : word.start].strip(" ") == "":
            if before.folded in _MONTH_WORDS:
                at = before.start
            elif before.folded not in _YEAR_BEFORE:
                continue
        ok = low[0] <= year and (high is None or year <= high)
        out.append((at, word.end, ok))
    return out


_PERCENT = "percent_not_in_sources"
_NUMBER = "number_not_in_sources"
_SCHOOL = "school_not_in_profile"


def _numbers(text: str) -> list[tuple[int, int, str]]:
    """The bare numbers of `text` written in digits ("20", "2.5"), outside a percent and outside
    a word ("x86", "v2"): `(start, end, number)`. One pass."""
    skip = [(s, e) for s, e, _ in rules.percents(text)]
    out: list[tuple[int, int, str]] = []
    n = len(text)
    i = 0
    while i < n:
        if not text[i].isdigit():
            i += 1
            continue
        j = i
        while j < n and (
            text[j].isdigit() or (text[j] in ".," and j + 1 < n and text[j + 1].isdigit())
        ):
            j += 1
        glued = (i > 0 and (text[i - 1].isalnum() or text[i - 1] in ".,")) or (
            j < n and text[j].isalnum()
        )
        if not glued and not any(s <= i < e for s, e in skip):
            out.append((i, j, text[i:j].replace(",", ".")))
        i = j
    return out


def _percent_only(sources: Sources) -> frozenset[str]:
    """The numbers the person's texts state only as a percent: "by 20%" sources "20%", never a
    bare "20" ("Managed 20 engineers", "Forbes 30 Under 30")."""
    texts = [*sources.profile, *sources.answers]
    percents = {key for text in texts for *_, key in rules.percents(text)}
    plain = {number for text in texts for *_, number in _numbers(text)}
    return frozenset(key for key in percents if not key.startswith("top ") and key not in plain)


def own_issues(fields: list[tuple[str, str]], sources: Sources) -> list[Issue]:
    """The claims M9 does not see (`rules.credentials`, `rules.changes`, `rules.decades`, a
    percent the person's texts do not state as a percent, `rules.percents`, a bare number they
    state only as a percent, and a school named outside a preposition, `rules.schools`), against
    the person's own texts only (the profile sources, never the answers; a letter's addressee
    may be a school)."""
    entries = rules.corpus(sources.profile)
    named = rules.corpus([*sources.profile, *sources.participants])
    stated = {key for text in sources.profile for *_, key in rules.percents(text)}
    only_percent = _percent_only(sources)
    years: dict[int, bool] = {}
    out: list[Issue] = []
    for path, text in fields:
        out += [
            Issue(path, s, e, _PERCENT, "claim")
            for s, e, key in rules.percents(text)
            if key not in stated
        ]
        if only_percent:
            out += [
                Issue(path, s, e, _NUMBER, "claim")
                for s, e, number in _numbers(text)
                if number in only_percent
            ]
        out += [Issue(path, s, e, _SCHOOL, "claim") for s, e in rules.schools(text, named)]
        out += [
            Issue(path, s, e, rules.CREDENTIAL, "claim")
            for s, e in rules.credentials(text, entries)
        ]
        out += [Issue(path, s, e, rules.CHANGE, "claim") for s, e in rules.changes(text, entries)]
        for s, e, least in rules.decades(text):
            if least not in years:
                # "A decade" is "10+ years": the person's texts must hold that many.
                years[least] = not factcheck_rules.check(
                    f"{least}+ years", Sources(profile=sources.profile)
                )
            if not years[least] and not rules.corpus_states(text[s:e], entries):
                out.append(Issue(path, s, e, rules.DECADE, "claim"))
    return out


# Words that say what kind of name a name is, never which one: sharing one of them makes no
# paraphrase ("Université Exemple" is no paraphrase of "University of Northvale").
_NAME_GENERIC = frozenset(
    [
        *_PLAIN,
        *_SENIORITY,
        "ltd",
        "limited",
        "inc",
        "sa",
        "sas",
        "sarl",
        "gmbh",
        "llc",
        "plc",
        "group",
        "groupe",
        "company",
        "corp",
        "university",
        "universite",
        "ecole",
        "school",
        "institute",
        "institut",
        "college",
        "academy",
        "polytechnic",
        "du",
        "la",
        "le",
        "et",
    ]
)


def _distinctive(text: str) -> set[str]:
    return {w.folded for w in rules.words_of(text) if len(w.folded) > 2} - _NAME_GENERIC


def _classify(issues: list[Issue], texts: dict[str, str], sources: Sources) -> list[Issue]:
    """Each claim with `rewrite` set by `rules.POLICY`: a name is ambiguous when it may be a
    paraphrase of one of the person's names of its kind (a title sharing a distinctive word,
    an organisation whose distinctive words are all one of the person's), else a high
    confidence claim."""
    kinds = {
        "employer_not_in_profile": [_distinctive(name) for name in sources.employers],
        "school_not_in_profile": [_distinctive(name) for name in sources.schools],
        "title_not_in_profile": [
            _distinctive(name)
            for name in (
                sources.own_titles if isinstance(sources, DocumentSources) else sources.titles
            )
        ],
    }
    out: list[Issue] = []
    for issue in issues:
        rule = rules.policy(issue.code) if issue.level == "claim" else rules.BY_CODE
        if rule == rules.NAME:
            span = texts.get(issue.field, "")[issue.start : issue.end]
            first = rules.words_of(span)[:1]
            if issue.code == "title_not_in_profile" and first and first[0].folded in _SENIORITY:
                # "Head of Data": a seniority word no title of the person's holds, which
                # cannot go alone, makes the title a high confidence claim.
                rewrite = False
            elif issue.code == "title_not_in_profile":
                # A title that shares a word with one of the person's ("Platform Engineer").
                rewrite = any(_distinctive(span) & names for names in kinds[issue.code])
            else:
                # An organisation that says no more than one of the person's ("Kestrel
                # Analytics" for "Kestrel Analytics Ltd", "Northvale University").
                claim = _distinctive(span)
                rewrite = bool(claim) and any(claim <= names for names in kinds[issue.code])
        else:
            rewrite = rule == rules.NEEDS_REWRITE
        out.append(replace(issue, rewrite=True) if rewrite else issue)
    return out


def _sorted(issues: Iterable[Issue], order: dict[str, int]) -> list[Issue]:
    level = {name: i for i, name in enumerate(rules.LEVELS)}
    return sorted(
        set(issues),
        key=lambda i: (level[i.level], order.get(i.field, -1), i.start, i.end, i.code),
    )


def run(
    kind: c.Kind,
    content: object,
    sources: Sources,
    settings: DocumentSettings,
    *,
    employer_keys: Iterable[str] | None = None,
    soft_limits: rules.SoftLimits | None = None,
    today: date | None = None,
) -> Checks:
    """The checks of `content` (a model, or JSON data from a writer or the editor).

    `today` is the generation date, as data: the generation tasks (D7) pass the date their run
    started, the api the date of the hand edit; without it an open period sources no duration.
    Each claim carries `rewrite` from `rules.POLICY` (`_classify`)."""
    if isinstance(content, BaseModel):
        model, invalid = c.parse(kind, c.dump(content)), []
    else:
        model, invalid = rules.structure(kind, content)
    if model is None:
        return Checks(kind, None, tuple(invalid))
    issues: list[Issue] = []
    if employer_keys is not None:
        issues += rules.employer_keys(model, employer_keys)
    fields = [(path, text) for path, text in c.text_fields(model) if text]
    order = {path: i for i, (path, _) in enumerate(c.text_fields(model))}
    owners = {
        f"experience.{i}": role.employer_key
        for i, role in enumerate(getattr(model, "experience", None) or ())
    }
    found = fact_issues(fields, sources, owners=owners, today=today)
    issues += found
    # A percent or a school that M9 flags already is one claim, not two.
    seen = {(i.field, i.code): [] for i in found}
    for i in found:
        seen[(i.field, i.code)].append((i.start, i.end))
    issues += [
        i
        for i in own_issues(fields, sources)
        if not any(s < i.end and i.start < e for s, e in seen.get((i.field, i.code), ()))
    ]
    for path, text in fields:
        issues += [
            Issue(path, start, end, rules.BANNED_PHRASE, "claim")
            for start, end in rules.banned(text, settings.banned_phrases)
        ]
        if settings.no_dash_separators:
            issues += [
                Issue(path, sep.start, sep.end, rules.DASH_SEPARATOR, "separator")
                for sep in rules.separators(text)
            ]
    if soft_limits is not None:
        issues += rules.soft_lengths(model, soft_limits)
    issues += rules.required_empty(model)
    issues = _classify(issues, dict(fields), sources)
    terms = sources.terms if isinstance(sources, DocumentSources) else ()
    return Checks(kind, model, tuple(_sorted(issues, order)), terms or sources.known_terms)


def excerpt(content: BaseModel, issue: Issue) -> str:
    """The text an issue points at (what a writer is shown with the rule it breaks)."""
    for path, text in c.text_fields(content):
        if path == issue.field:
            return text[issue.start : issue.end] if issue.end > issue.start else text
    return ""
