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
submit, everything else is corrected (`corrections`) or shown.

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
- "N years at <employer>" (or months, "ans chez") is sourced when that employer's closed period
  holds N whole years (or months), counted from the stored months.

For speed, M9 runs once over the printed fields joined by a neutral break (a pilcrow between
blank lines) rather than once per field, which would fold every source again for every field;
each issue is then mapped back to its field. `test_checks` proves both give the same issues.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, replace

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


def fact_issues(fields: list[tuple[str, str]], sources: Sources) -> list[Issue]:
    """M9 over the fields at once, each issue mapped back onto its field (clipped to it), less
    those that `DocumentSources` sources (the module docstring)."""
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
    out: list[Issue] = []
    for issue in found:
        index = bisect_right(starts, issue.start) - 1
        if index < 0:
            continue
        path, text = fields[index]
        start = issue.start - starts[index]
        end = min(issue.end - starts[index], len(text))
        if start >= len(text) or end <= start:
            continue
        answered = (issue.start, issue.end, issue.code) not in with_answers
        if answered and _about_an_answer(text, start, end, topics):
            continue
        if isinstance(sources, DocumentSources) and _sourced_apart(
            text, start, end, issue.code, sources
        ):
            continue
        out.append(Issue(path, start, end, issue.code, "claim"))
    return out


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


def _about_an_answer(text: str, start: int, end: int, topics: tuple[tuple[str, str], ...]) -> bool:
    """Is the claim at `text[start:end]`, which only an answer sources, in a sentence about what
    that answer answers? The sentence must share a word, outside the claim, with the answer or
    its question."""
    claim = text[start:end]
    sentence = next((text[s:e] for s, e in rules.sentences(text) if s <= start < e), text)
    around = _content_words(sentence) - _content_words(claim)
    if not around:
        return False
    for topic, answer in topics:
        if factcheck_rules.check(claim, Sources(answers=(answer,))):
            continue  # this answer does not state the claim
        if around & (_content_words(answer) | set(topic.split())):
            return True
    return False


def _sourced_apart(text: str, start: int, end: int, code: str, sources: DocumentSources) -> bool:
    claim = text[start:end]
    if code in _IDENTITY_CODES:
        return bool(sources.identity) and not factcheck_rules.check(
            claim, Sources(profile=sources.identity)
        )
    if code in _DATE_CODES:
        month = _numeric_month(claim)
        return month is not None and any(
            month in (_month_of(s), _month_of(e)) for _, s, e in sources.periods
        )
    if code == "duration_not_in_profile":
        return _duration_at_employer(text, start, end, sources.periods)
    return False


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


def _months_held(start: str, end: str) -> int | None:
    """The fewest whole months a closed period surely covers ("2020-09" to "2022-11": 27; a
    year alone counts from its December to the other's January). None for an open period: the
    checks have no clock."""
    if not start or not end:
        return None
    first = _month_of(start) or ((int(start), 12) if start.isdigit() else None)
    last = _month_of(end) or ((int(end), 1) if end.isdigit() else None)
    if first is None or last is None:
        return None
    return (last[0] - first[0]) * 12 + last[1] - first[1] + 1


def _duration_at_employer(
    text: str, start: int, end: int, periods: tuple[tuple[str, str, str], ...]
) -> bool:
    words = rules.fold(text[start:end]).text.split()
    if len(words) != 2 or words[1] not in _DURATION_UNITS:
        return False
    value = int(words[0]) if words[0].isdigit() else _SMALL_NUMBERS.get(words[0])
    if not value:
        return False
    rest = rules.fold(text[end : end + 200]).text.split(" ", 2)
    if len(rest) < 3 or rest[0] != "" or rest[1].strip("'") not in _AT:
        return False
    after = rest[2]
    unit = _DURATION_UNITS[words[1]]
    for name, first, last in periods:
        months = _months_held(first, last)
        folded = rules.fold(name).text
        if months is None or not after.startswith(folded):
            continue
        if after[len(folded) : len(folded) + 1].isalnum():
            continue
        if months // unit == value:
            return True
    return False


def own_issues(fields: list[tuple[str, str]], sources: Sources) -> list[Issue]:
    """The claims M9 does not see (`rules.credentials`, `rules.changes`, `rules.decades`),
    against the person's own texts only (the profile sources, never the answers)."""
    entries = rules.corpus(sources.profile)
    years: dict[int, bool] = {}
    out: list[Issue] = []
    for path, text in fields:
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
) -> Checks:
    """The checks of `content` (a model, or JSON data from a writer or the editor)."""
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
    issues += fact_issues(fields, sources)
    issues += own_issues(fields, sources)
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
    terms = sources.terms if isinstance(sources, DocumentSources) else ()
    return Checks(kind, model, tuple(_sorted(issues, order)), terms or sources.known_terms)


def excerpt(content: BaseModel, issue: Issue) -> str:
    """The text an issue points at (what a writer is shown with the rule it breaks)."""
    for path, text in c.text_fields(content):
        if path == issue.field:
            return text[issue.start : issue.end] if issue.end > issue.start else text
    return ""
