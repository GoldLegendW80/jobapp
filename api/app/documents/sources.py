"""The sources of the fact check of a document (SPEC-documents.md, "Being careful", step 3).

`sources_for(view, base, upload_text, posting, kind=...)` builds M9's `factcheck_rules.Sources`, as
a `DocumentSources` that carries what the documents' own rules read besides:

- `profile`: the text of the `facts` blocks, the employers' and the schools' fields (their
  dates as stored and in words), the languages, the skills terms, the years of experience, and,
  for every document, the base CV's content (its printed fields and its `fixed` block) and the
  text of the CV it was converted from;
- `answers`: the answers section (`checks` lets a number of an answer source only a sentence
  about the same thing: `answer_topics` holds each answer with the words of its question);
- `employers`, `schools`, `titles`: from the fixed facts;
- for a letter and an outreach proposal only, the posting's title in `titles` and its company
  in `participants`, so that they may name the role and the company they write to. Never for a
  CV: there they would make "Senior Data Engineer at Example SA" pass as experience;
- `known_terms`: the skills terms, so that a technology is not taken for an organisation;
- `terms`: the skills terms with the base CV's skills items and competencies, after which a
  version number is cut alone (`corrections`);
- `identity`: the person's phone, e-mail, website and links, which source only the same phone,
  address or link (the header and contact lines), never a number of the text;
- `own_titles`: the titles of the person's employers (the profile's and the base CV's), which
  source a descriptive title of the same head noun and seniority ("Backend engineer" for a
  "Software Engineer"); `posting_titles`, a letter's only, which never source a title held at
  one of the person's employers ("As a Staff Platform Engineer at Orbite Données");
- `periods`: the stored periods of the employers and schools, `(organisation, start, end)` as
  stored, which source a numeric date equal to one of them ("12/2022") and a number of years at
  one employer that its closed period holds ("2 years at Sample Labs"), matched as dates and
  durations so that a bare "12" is never sourced;
- `entries`: each experience entry's employer key with its organisation and period, which alone
  source a year in that entry's bullets ("Built the platform in 2018" under an employer joined
  in 2021 is a claim, whatever other period holds 2018);
- `years_experience`: the person's total, which sources any whole number of years at or below
  it ("8 years" for 8.5: rounded down is true, rounded up a claim).

`instructions` and `thread` stay empty: neither the posting nor a chat message makes a claim
true ("5 years of Python required" in a posting does not make "5 years of Python" pass).

Pure: no I/O. Every tuple keeps its first occurrence of an entry and drops the empty ones.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.documents import content as c
from app.documents.profile_view import ProfileView
from app.mail.factcheck_rules import Sources
from app.profile.schemas import format_period


@dataclass(frozen=True, slots=True)
class PostingView:
    """What the checks read of a posting: its company and its title, never its text."""

    company: str = ""
    title: str = ""


# The words of each question of the answers section: a number of an answer sources a sentence
# only when that sentence shares a word with its question or its answer (`checks`).
_TOPICS = {
    "work_authorization": "work authorization authorisation permit visa sponsorship sponsor "
    "citizen citizenship autorisation travail permis nationalite",
    "notice_period": "notice period preavis available availability disponible disponibilite "
    "start leave",
    "earliest_start": "start starting available availability date disponible disponibilite "
    "commencer demarrer",
    "salary_policy": "salary salaire compensation pay remuneration package expectations",
    "relocation": "relocation relocate relocating move moving demenager mobilite",
    "travel": "travel travelling traveling deplacements deplacement voyager trips",
    "motivation": "",
    "heard_about": "heard found discovered posting annonce offre",
}


@dataclass(frozen=True, slots=True)
class DocumentSources(Sources):
    """M9's sources with what the documents' rules read besides (see the module docstring)."""

    answer_topics: tuple[tuple[str, str], ...] = ()  # (the words of the question, the answer)
    identity: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()  # the skills terms with the base CV's skills and competencies
    periods: tuple[tuple[str, str, str], ...] = ()  # (organisation, start, end or "")
    own_titles: tuple[str, ...] = ()  # the person's titles, never the posting's
    posting_titles: tuple[str, ...] = ()  # a letter's: the role it applies for
    # (employer key, organisation, start, end or ""): the period of an experience entry, which
    # alone sources a year of its bullets.
    entries: tuple[tuple[str, str, str, str], ...] = ()
    # The person's total ("8.5"), the one entry: a whole number of years at or under it is true.
    years_experience: tuple[str, ...] = ()


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


def _identity(view: ProfileView) -> list[str]:
    who = view.identity
    return [who.phone, who.email, who.website, who.linkedin_url, *(url for _, url in who.links)]


def _terms(view: ProfileView, base: c.BaseCvContent | None) -> list[str]:
    out = list(view.skills_terms)
    if base is not None:
        out += base.competencies
        out += [item for group in base.skills for item in group.items]
    return out


def _periods(view: ProfileView, base: c.BaseCvContent | None) -> list[tuple[str, str, str]]:
    rows = [(e.organisation, e.start, e.end) for e in view.employers]
    rows += [(s.school, s.start, s.end) for s in view.schools]
    if base is not None:
        rows += [(e.organisation, e.start, e.end) for e in base.fixed.employers]
        rows += [(s.school, s.start, s.end) for s in base.fixed.schools]
    out: list[tuple[str, str, str]] = []
    for name, start, end in rows:
        row = (name.strip(), start or "", end or "")
        if (row[1] or row[2]) and row not in out:
            out.append(row)
    return out


def _entries(view: ProfileView, base: c.BaseCvContent | None) -> list[tuple[str, str, str, str]]:
    rows = [(e.key, e.organisation, e.start, e.end) for e in view.employers]
    if base is not None:
        rows += [(e.key, e.organisation, e.start, e.end) for e in base.fixed.employers]
    out: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for key, name, start, end in rows:
        if key not in seen:
            seen.add(key)
            out.append((key, name.strip(), start or "", end or ""))
    return out


def sources_for(
    view: ProfileView,
    base: c.BaseCvContent | None,
    upload_text: str | None,
    posting: PostingView | None,
    *,
    kind: Literal["cv", "base", "letter", "proposal"] = "cv",
) -> DocumentSources:
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
    own_titles = [*titles, *(e.title for e in base.fixed.employers)] if base else list(titles)
    participants: list[str] = []
    posting_titles: list[str] = []
    if posting is not None and kind in ("letter", "proposal"):
        titles.append(posting.title)
        posting_titles.append(posting.title)
        participants.append(posting.company)
    return DocumentSources(
        profile=_unique(profile),
        answers=_unique(view.answers),
        employers=_unique(e.organisation for e in view.employers),
        schools=_unique(s.school for s in view.schools),
        titles=_unique(titles),
        participants=_unique(participants),
        known_terms=_unique(view.skills_terms),
        answer_topics=tuple(
            (_TOPICS.get(key, ""), text) for key, text in view.answer_topics if text.strip()
        ),
        identity=_unique(_identity(view)),
        terms=_unique(_terms(view, base)),
        periods=tuple(_periods(view, base)),
        own_titles=_unique(own_titles),
        posting_titles=_unique(posting_titles),
        entries=tuple(_entries(view, base)),
        years_experience=(
            (f"{view.years_experience:g}",) if view.years_experience is not None else ()
        ),
    )
