"""What the documents module reads of a profile and of a base CV, as one frozen value.

`build(sections, revision, base)` turns the profile's stored sections (the JSON of each
section, as `profile.service` keeps them) into a `ProfileView`:

- the fixed facts (employers, schools, languages) from the profile, else from the base CV's
  `fixed`, field by field: an employer of the profile keeps its organisation, title and dates
  (Decision 7: the profile's title, always) and takes a location the profile leaves empty from
  the base CV's entry of the same key; an employer, a school or a language that only the base
  CV states is kept after the profile's own. The languages are the identity section's (the
  profile keeps them there), they are fixed facts all the same;
- `identity` (name, contact, links) for the template only: no writer and no source reads it;
- `facts_blocks`, `skills_terms` (the categories the person has or has adjacent to, with their
  plain terms; never what the profile says the person lacks), `years_experience`, `answers`
  and `motivation`;
- `revision`, the profile's revision the view was read at.

Pure: no I/O. The caller reads the rows.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.documents.content import BaseCvContent
from app.profile.schemas import (
    AnswersSection,
    ExperienceSection,
    FactsSection,
    IdentitySection,
    SkillsSection,
)


@dataclass(frozen=True, slots=True)
class Employer:
    key: str
    organisation: str
    title: str
    start: str | None  # YYYY-MM or YYYY
    end: str | None  # None: present, or not stated
    location: str


@dataclass(frozen=True, slots=True)
class School:
    school: str
    degree: str
    start: str | None
    end: str | None
    location: str


@dataclass(frozen=True, slots=True)
class Language:
    name: str
    level: str


@dataclass(frozen=True, slots=True)
class Identity:
    """For the template only (name, contact lines, links): never given to a writer."""

    full_name: str = ""
    headline: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    country: str = ""
    linkedin_url: str = ""
    website: str = ""
    links: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ProfileView:
    revision: int
    employers: tuple[Employer, ...] = ()
    schools: tuple[School, ...] = ()
    languages: tuple[Language, ...] = ()
    identity: Identity = field(default_factory=Identity)
    facts_blocks: tuple[tuple[str, str], ...] = ()  # (title, body)
    skills_terms: tuple[str, ...] = ()
    years_experience: float | None = None
    answers: tuple[str, ...] = ()
    answer_topics: tuple[tuple[str, str], ...] = ()  # (the answer's question key, the answer)
    motivation: str = ""

    @property
    def employer_keys(self) -> tuple[str, ...]:
        return tuple(employer.key for employer in self.employers)


def _section(model: Any, data: Mapping[str, Any] | None) -> Any:
    return model.model_validate(dict(data) if data else {})


def _fold_key(text: str) -> str:
    return " ".join(text.casefold().split())


def _employers(experience: ExperienceSection, base: BaseCvContent | None) -> tuple[Employer, ...]:
    from_base = {e.key: e for e in base.fixed.employers} if base else {}
    out: list[Employer] = []
    for e in experience.employers:
        other = from_base.get(e.key)
        out.append(
            Employer(
                key=e.key,
                organisation=e.organisation,
                title=e.title,
                start=e.start,
                end=e.end,
                location=e.location or (other.location if other else ""),
            )
        )
    own = {e.key for e in experience.employers}
    out += [
        Employer(e.key, e.organisation, e.title, e.start, e.end, e.location)
        for e in (base.fixed.employers if base else [])
        if e.key not in own
    ]
    return tuple(out)


def _schools(experience: ExperienceSection, base: BaseCvContent | None) -> tuple[School, ...]:
    from_base = {_fold_key(s.school): s for s in base.fixed.schools} if base else {}
    out: list[School] = []
    for s in experience.education:
        other = from_base.get(_fold_key(s.school))
        out.append(
            School(
                school=s.school,
                degree=s.degree,
                start=s.start if s.start or not other else other.start,
                end=s.end if s.end or not other else other.end,
                location=s.location or (other.location if other else ""),
            )
        )
    own = {_fold_key(s.school) for s in experience.education}
    out += [
        School(s.school, s.degree, s.start, s.end, s.location)
        for s in (base.fixed.schools if base else [])
        if _fold_key(s.school) not in own
    ]
    return tuple(out)


def _languages(identity: IdentitySection, base: BaseCvContent | None) -> tuple[Language, ...]:
    from_base = {_fold_key(lang.name): lang for lang in base.fixed.languages} if base else {}
    out = [
        Language(
            lang.name,
            lang.level
            or (from_base[key].level if (key := _fold_key(lang.name)) in from_base else ""),
        )
        for lang in identity.languages
    ]
    own = {_fold_key(lang.name) for lang in identity.languages}
    out += [
        Language(lang.name, lang.level)
        for lang in (base.fixed.languages if base else [])
        if _fold_key(lang.name) not in own
    ]
    return tuple(out)


def _skills_terms(skills: SkillsSection) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for category in [*skills.have, *skills.adjacent]:
        # An advanced category holds patterns, not words: only its name is a term.
        terms = [] if category.advanced_regex else category.terms
        for term in [category.name, *terms]:
            key = _fold_key(term)
            if key and key not in seen:
                seen.add(key)
                out.append(term)
    return tuple(out)


def _answer_topics(answers: AnswersSection) -> tuple[tuple[str, str], ...]:
    pairs = [("work_authorization", item.answer) for item in answers.work_authorization]
    pairs += [
        (name, getattr(answers, name))
        for name in (
            "notice_period",
            "earliest_start",
            "salary_policy",
            "relocation",
            "travel",
            "motivation",
            "heard_about",
        )
    ]
    return tuple((name, text) for name, text in pairs if text)


def _answers(answers: AnswersSection) -> tuple[str, ...]:
    return tuple(text for _, text in _answer_topics(answers))


def build(
    sections: Mapping[str, Mapping[str, Any] | None],
    revision: int,
    base: BaseCvContent | None = None,
) -> ProfileView:
    """The view of a profile (its stored sections by name, None or missing for an empty one)
    and of the base CV a document is written from, if any."""
    identity = _section(IdentitySection, sections.get("identity"))
    experience = _section(ExperienceSection, sections.get("experience"))
    facts = _section(FactsSection, sections.get("facts"))
    skills = _section(SkillsSection, sections.get("skills"))
    answers = _section(AnswersSection, sections.get("answers"))
    return ProfileView(
        revision=revision,
        employers=_employers(experience, base),
        schools=_schools(experience, base),
        languages=_languages(identity, base),
        identity=Identity(
            full_name=identity.full_name or "",
            headline=identity.headline or "",
            email=identity.email or "",
            phone=identity.phone or "",
            city=identity.city or "",
            country=identity.country or "",
            linkedin_url=identity.linkedin_url or "",
            website=identity.website or "",
            links=tuple((link.label, link.url) for link in identity.links),
        ),
        facts_blocks=tuple((block.title, block.body) for block in facts.blocks),
        skills_terms=_skills_terms(skills),
        years_experience=skills.years_experience,
        answers=_answers(answers),
        answer_topics=_answer_topics(answers),
        motivation=answers.motivation or "",
    )
