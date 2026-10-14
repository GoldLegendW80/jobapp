"""The sources of the fact check of a document (SPEC-documents.md, "Being careful", step 3).

`sources_for(view, base, upload_text, posting)` builds M9's `factcheck_rules.Sources`:

- `profile`: the text of the `facts` blocks, the employers' and the schools' fields (their
  dates as stored and in words), the languages, the skills terms, the years of experience, and,
  for every document, the base CV's content (its printed fields and its `fixed` block) and the
  text of the CV it was converted from;
- `answers`: the answers section;
- `employers`, `schools`, `titles`: from the fixed facts; `titles` also holds the posting's title;
- `participants`: the posting's company, so that a letter may name the company it writes to;
- `known_terms`: the skills terms, so that a technology is not taken for an organisation.

`instructions` and `thread` stay empty: neither the posting nor a chat message makes a claim
true ("5 years of Python required" in a posting does not make "5 years of Python" pass).

Pure: no I/O. Every tuple keeps its first occurrence of an entry and drops the empty ones.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.documents import content as c
from app.documents.profile_view import ProfileView
from app.mail.factcheck_rules import Sources
from app.profile.schemas import format_period


@dataclass(frozen=True, slots=True)
class PostingView:
    """What the checks read of a posting: its company and its title, never its text."""

    company: str = ""
    title: str = ""


def _unique(texts: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for text in texts:
        text = text.strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return tuple(out)


def _period(value: str | None) -> str:
    if not value:
        return ""
    try:
        # In words only: "2022-12" as stored would make any "12" of a draft a sourced number.
        return format_period(value)
    except ValueError:
        return value


def _dated(*parts: str | None) -> str:
    return " ".join(part for part in parts if part)


def _fixed_texts(fixed: c.Fixed) -> list[str]:
    out = [
        _dated(e.organisation, e.title, e.location, _period(e.start), _period(e.end))
        for e in fixed.employers
    ]
    out += [
        _dated(s.school, s.degree, s.location, _period(s.start), _period(s.end))
        for s in fixed.schools
    ]
    out += [_dated(lang.name, lang.level) for lang in fixed.languages]
    return out


def sources_for(
    view: ProfileView,
    base: c.BaseCvContent | None,
    upload_text: str | None,
    posting: PostingView | None,
) -> Sources:
    profile = [f"{title}\n{body}" for title, body in view.facts_blocks]
    profile += [
        _dated(e.organisation, e.title, e.location, _period(e.start), _period(e.end))
        for e in view.employers
    ]
    profile += [
        _dated(s.school, s.degree, s.location, _period(s.start), _period(s.end))
        for s in view.schools
    ]
    profile += [_dated(lang.name, lang.level) for lang in view.languages]
    profile += view.skills_terms
    if view.years_experience is not None:
        profile.append(f"{view.years_experience:g} years of professional experience")
    if base is not None:
        profile += [text for _, text in c.text_fields(base)]
        profile += _fixed_texts(base.fixed)
    if upload_text:
        profile.append(upload_text)
    titles = [e.title for e in view.employers]
    participants: list[str] = []
    if posting is not None:
        titles.append(posting.title)
        participants.append(posting.company)
    return Sources(
        profile=_unique(profile),
        answers=_unique(view.answers),
        employers=_unique(e.organisation for e in view.employers),
        schools=_unique(s.school for s in view.schools),
        titles=_unique(titles),
        participants=_unique(participants),
        known_terms=_unique(view.skills_terms),
    )
