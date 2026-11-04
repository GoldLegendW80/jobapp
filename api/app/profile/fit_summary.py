"""What the agent of a job website reads of the person's profile (browser B21, owner 2026-11-04:
"the agent must find jobs that fit with my profile, i don't want to find exactly the title job
I'v set").

`summary(sections)` is a bounded summary of what the scoring reads (app/jobs/scoring.py: the
skills the person has and has adjacent to, the years of experience; the search's seniority,
industries and company types) and of the fixed facts the documents writers read
(app/documents/profile_view.py: the titles of the recent roles, the languages). It names no
person: never the identity section's name, headline, e-mail, phone, city, country, links or
photo, never an employer's organisation, location or dates, never a school, a fact block, an
answer or anything the identity module holds. The page the agent reads can steer it (B20), so
only what helps judge a posting's fit goes to the model.

Pure but `read`, which reads the profile row without creating it.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.profile.models import Profile
from app.profile.schemas import ExperienceSection, IdentitySection, SearchSection, SkillsSection

ROLES_MAX = 5
CATEGORIES_MAX = 30
TERMS_MAX = 15
LANGUAGES_MAX = 10
VALUE_MAX = 120  # characters of one value
SUMMARY_MAX = 8000  # characters of the summary as JSON (finder.PROFILE_MAX bounds it again)


def _section(model: Any, data: Any) -> Any:
    """A stored section as its model; an empty one when absent or no longer valid."""
    try:
        return model.model_validate(dict(data) if isinstance(data, Mapping) else {})
    except ValueError:
        return model()


def _value(text: object) -> str:
    return " ".join(text.split())[:VALUE_MAX] if isinstance(text, str) else ""


def _categories(categories: list[Any]) -> list[dict[str, Any]]:
    """A skill category's name and its plain terms (an advanced one holds patterns, not words:
    its name alone), as profile_view's `_skills_terms` reads them."""
    out: list[dict[str, Any]] = []
    for category in categories[:CATEGORIES_MAX]:
        terms = [] if category.advanced_regex else category.terms
        words = [w for w in (_value(t) for t in terms[:TERMS_MAX]) if w]
        name = _value(category.name)
        if name or words:
            out.append({"name": name, "terms": words})
    return out


def _recent_roles(experience: ExperienceSection) -> list[str]:
    """The titles of the most recent roles, current ones first, then by end and start, newest
    first; titles only, once each (case and spacing aside)."""
    ordered = sorted(
        experience.employers,
        key=lambda e: (e.end is None, str(e.end or ""), str(e.start or "")),
        reverse=True,
    )
    titles: list[str] = []
    for employer in ordered:
        title = _value(employer.title)
        if title and title.casefold() not in {t.casefold() for t in titles}:
            titles.append(title)
        if len(titles) == ROLES_MAX:
            break
    return titles


def summary(sections: Mapping[str, Any]) -> dict[str, Any]:
    """The summary of the stored sections (by name), at most SUMMARY_MAX characters as JSON:
    the parts in this order, a list cut from its end when the whole would be longer."""
    skills = _section(SkillsSection, sections.get("skills"))
    experience = _section(ExperienceSection, sections.get("experience"))
    search = _section(SearchSection, sections.get("search"))
    # The languages only: every other field of the identity section names or reaches the person.
    languages = _section(IdentitySection, sections.get("identity")).languages
    industries = [] if search.industries.advanced_regex else search.industries.terms
    parts: list[tuple[str, Any]] = [
        ("years_experience", skills.years_experience),
        ("recent_roles", _recent_roles(experience)),
        ("skills", _categories(skills.have)),
        ("adjacent_skills", _categories(skills.adjacent)),
        ("seniority", list(search.seniority)),
        (
            "languages",
            [
                {"name": _value(lang.name), "level": _value(lang.level)}
                for lang in languages[:LANGUAGES_MAX]
                if _value(lang.name)
            ],
        ),
        ("industries", [w for w in (_value(t) for t in industries[:TERMS_MAX]) if w]),
        ("company_types", list(search.company_types)),
    ]
    out: dict[str, Any] = {}
    for name, value in parts:
        if value in (None, [], ""):
            continue
        out[name] = value
        if isinstance(value, list):
            while value and len(json.dumps(out, ensure_ascii=False)) > SUMMARY_MAX:
                value.pop()
            if not value:
                del out[name]
        elif len(json.dumps(out, ensure_ascii=False)) > SUMMARY_MAX:
            del out[name]
    return out


def read(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    """The summary of the person's stored profile; an empty one when they have none."""
    sections = db.execute(
        sa.select(Profile.sections).where(Profile.user_id == user_id)
    ).scalar_one_or_none()
    return summary(sections if isinstance(sections, Mapping) else {})
