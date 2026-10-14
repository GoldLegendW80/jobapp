"""The checks of a document (SPEC-documents.md, "Being careful", steps 1 to 3): pure.

`run(kind, content, sources, settings) -> Checks` is the one entry point: the submit tools, the
api (the notes on a hand edit) and the chat all call it. It finds, in this order of levels:

1. `invalid`: the schema and the hard limits, and, given `employer_keys`, an experience entry
   whose key is no fixed employer. Content that fails the schema is checked no further;
2. `claim`: M9's `factcheck_rules.check` per printed field, mapped back to `{field, start, end,
   code}`, and the person's banned phrases (`banned_phrase`);
3. `separator`: with the person's dash rule on, each dash used as a separator;
4. `note`: the template's soft lengths, given `soft_limits`, and an empty required field.

Nothing here refuses a render, a download or a send: `invalid` is answered to a writer at
submit, everything else is corrected (`corrections`) or shown.

Pure and deterministic: the same content, sources and settings give the same issues in the
same order: by level, then by the field's place in the document, then by position and code.

For speed, M9 runs once over the printed fields joined by a neutral break (a pilcrow between
blank lines) rather than once per field, which would fold every source again for every field;
each issue is then mapped back to its field. `test_checks` proves both give the same issues.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel

from app.documents import content as c
from app.documents import rules
from app.documents.rules import Issue
from app.documents.settings import DocumentSettings
from app.mail import factcheck_rules
from app.mail.factcheck_rules import Sources

FIELD_BREAK = "\n\n\u00b6\n\n"


@dataclass(frozen=True, slots=True)
class Checks:
    kind: c.Kind
    content: c.CvContent | c.LetterContent | None  # validated; None when the schema failed
    issues: tuple[Issue, ...]

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
    """M9 over the fields at once, each issue mapped back onto its field (clipped to it)."""
    starts: list[int] = []
    parts: list[str] = []
    at = 0
    for _, text in fields:
        starts.append(at)
        parts.append(text)
        at += len(text) + len(FIELD_BREAK)
    body = FIELD_BREAK.join(parts)
    out: list[Issue] = []
    for found in factcheck_rules.check(body, sources):
        index = bisect_right(starts, found.start) - 1
        if index < 0:
            continue
        path, text = fields[index]
        start = found.start - starts[index]
        end = min(found.end - starts[index], len(text))
        if start < len(text) and end > start:
            out.append(Issue(path, start, end, found.code, "claim"))
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
    return Checks(kind, model, tuple(_sorted(issues, order)))


def excerpt(content: BaseModel, issue: Issue) -> str:
    """The text an issue points at (what a writer is shown with the rule it breaks)."""
    for path, text in c.text_fields(content):
        if path == issue.field:
            return text[issue.start : issue.end] if issue.end > issue.start else text
    return ""
