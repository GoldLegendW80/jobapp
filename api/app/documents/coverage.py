"""The keyword coverage of a posting's CV (SPEC-documents.md, "Keyword coverage"): no model.

Adapted from `keyword-match.mjs` of career-ops (MIT, NOTICE): which terms of the posting's
requirements the CV's printed text names. A diagnostic, never a block (Decision 4): the document
page shows the missing ones with "add one only if it is true of you", and nothing else reads it.

- **The requirement text** (`requirement_text`): the posting's bulleted lines but those under a
  heading of the company, the benefits, the pay or the contract, and the parts under a
  requirements or a responsibilities heading (`brief.requirement_lines`, `brief.parts_of`),
  never its whole text, so the company's pitch and the benefits stay out. A posting with none of
  them has no coverage: nothing is said, rather than "every term is there".
- **Its terms** (`vocabulary`): the person's own words only, so the check is about the document,
  not the person (the job page's Gap line names what the profile lacks): the plain terms of the
  skills categories the person has or is close to (`have`, `adjacent`; never `none`, which the
  person said they lack, nor a gate), then the skills items and competencies of the base CV the
  CV is written from. A category written as regular expressions holds patterns, not words, and
  is left out, as the fact check leaves it (`profile_view`).
- **The match**: a term stands as whole words, a plural "s" or "es" allowed, case and accents
  folded (`rules.fold`, the fold of the banned phrases). The longer term wins where two overlap
  ("machine learning" over "learning"). Each term is named once, as the posting writes it.
- **The printed text** (`printed`): the CV's printed fields and the fixed facts the template
  prints beside them (titles, organisations, schools, degrees, languages), so a term the
  template prints is never called missing.

Pure but `for_document`, which reads the posting, the profile and the base CV of one document.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session, undefer

from app.documents import brief, profile_view, rules
from app.documents import content as c
from app.documents.models import Document, Kind, Scope
from app.jobs.models import Job
from app.profile import matching
from app.profile.models import Profile
from app.profile.schemas import SkillsSection

VOCABULARY_MAX = 500
# Between two printed fields, so that no term is read across them.
FIELD_BREAK = "\n\u00b6\n"
TERM_CHARS_MAX = 80
# The parts of a posting whose words are asked of the person (`brief.PARTS`).
REQUIREMENT_PARTS = ("requirements_text", "responsibilities")
# The parts whose bullets are the company's, not asked of the person.
ASIDE_PARTS = ("about_company", "benefits", "salary_text", "contract_text")


@dataclass(frozen=True, slots=True)
class Coverage:
    terms: tuple[str, ...]  # the posting's requirement terms of the person's words, as written
    missing: tuple[str, ...]  # those the CV's printed text does not name, in the same order


NONE = Coverage((), ())


def requirement_text(posting_text: str) -> str:
    parts = brief.parts_of(posting_text)
    aside = {line for part in ASIDE_PARTS for line in brief.requirement_lines(parts.get(part, ""))}
    lines = [line for line in brief.requirement_lines(posting_text) if line not in aside]
    lines += [parts.get(part, "") for part in REQUIREMENT_PARTS]
    return "\n".join(line for line in lines if line.strip())


def vocabulary(skills: Mapping[str, Any] | None, base: c.BaseCvContent | None) -> list[str]:
    """The person's words a requirement is matched against, the first spelling of each kept."""
    try:
        section = SkillsSection.model_validate(dict(skills or {}))
    except ValidationError:
        section = SkillsSection()
    words: list[str] = []
    for category in [*section.have, *section.adjacent]:
        if not category.advanced_regex:
            words += category.terms
    if base is not None:
        words += base.competencies
        words += [item for group in base.skills for item in group.items]
    seen: set[str] = set()
    out: list[str] = []
    for word in words:
        key = rules.fold(word).text.strip()
        if key and len(key) <= TERM_CHARS_MAX and key not in seen:
            seen.add(key)
            out.append(word)
    return out[:VOCABULARY_MAX]


def _pattern(words: Iterable[str]) -> re.Pattern[str] | None:
    """One pattern for every word, folded, the longest tried first."""
    keys = sorted({rules.fold(word).text.strip() for word in words} - {""}, key=len, reverse=True)
    if not keys:
        return None
    alternation = "|".join(matching.escape(key) for key in keys)
    return re.compile(rf"(?<!\w)({alternation}){matching.PLURAL}(?!\w)")


def _found(pattern: re.Pattern[str], text: str) -> dict[str, str]:
    """Each word the text names (its folded key) with the text's own spelling of it."""
    folded = rules.fold(text)
    out: dict[str, str] = {}
    for match in pattern.finditer(folded.text):
        key = match.group(1)
        if key not in out:
            start, end = folded.span(match.start(), match.end())
            out[key] = " ".join(text[start:end].split())
    return out


def check(requirements: str, printed_texts: Iterable[str], words: Iterable[str]) -> Coverage:
    """The coverage of a CV's printed text against a posting's requirement text."""
    pattern = _pattern(words)
    if pattern is None or not requirements.strip():
        return NONE
    asked = _found(pattern, requirements)
    named = _found(pattern, FIELD_BREAK.join(printed_texts))
    return Coverage(
        tuple(asked.values()), tuple(shown for key, shown in asked.items() if key not in named)
    )


def printed(content: c.CvContent, view: profile_view.ProfileView) -> list[str]:
    texts = [text for _, text in c.text_fields(content)]
    texts += [part for e in view.employers for part in (e.title, e.organisation)]
    texts += [part for s in view.schools for part in (s.school, s.degree)]
    texts += [lang.name for lang in view.languages]
    return [text for text in texts if text]


def _parsed(kind: c.Kind, data: object) -> Any:
    """The content, None for none or one that no longer fits its model (never an error here)."""
    if data is None:
        return None
    try:
        return c.parse(kind, data)
    except c.ContentInvalidError:
        return None


def for_document(db: Session, document: Document) -> Coverage:
    """The coverage of a posting's CV draft; NONE for any other document."""
    if document.scope != Scope.posting or document.kind != Kind.cv or document.posting_id is None:
        return NONE
    user_id: uuid.UUID = document.user_id
    job = db.execute(
        sa.select(Job)
        .where(Job.id == document.posting_id, Job.user_id == user_id)
        .options(undefer(Job.posting_text))
    ).scalar_one_or_none()
    if job is None:
        return NONE
    requirements = requirement_text(job.posting_text or job.description or "")
    if not requirements:
        return NONE
    base = None
    if document.base_document_id is not None:
        row = db.scalar(
            sa.select(Document.draft).where(
                Document.id == document.base_document_id, Document.user_id == user_id
            )
        )
        base = _parsed("base", row)
    sections: Any = db.scalar(sa.select(Profile.sections).where(Profile.user_id == user_id)) or {}
    view = profile_view.build(sections, 0, base)
    content = _parsed("cv", document.draft)
    if not isinstance(content, c.CvContent):
        return NONE
    return check(requirements, printed(content, view), vocabulary(sections.get("skills"), base))
