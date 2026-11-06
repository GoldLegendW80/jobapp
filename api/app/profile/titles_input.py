"""What the job titles agent reads of the person (owner, 2026-11-05: "uploaded cv, the profile ...
or just a chat with the agent to tell what we want to do is enough. ... he can read everything to
help the user find best jobs"). SPEC-profile, "Job title suggestions"; titles.py puts the parts
into one request with the person's messages and bounds the whole again (REQUEST_MAX).

- `profile(sections)`: every section the person fills, at most PROFILE_MAX characters as JSON:
  the identity's headline, city, country and languages; the employers (organisation, title,
  dates, place, contract) and schools; the fact blocks; the skills the person has, has adjacent
  to and lacks, the gates and the years; what the search aims for (unwanted places, contracts,
  remote stance, seniority, industries, company types, pay sought); the answers about work
  (work authorisation, notice, earliest start, salary policy and ranges, relocation, travel,
  on site or hybrid, the stated years and degree, the motivation). The wanted and unwanted
  titles, the places and the never apply rules go beside it (titles.Request), as the checks
  read them. A list is cut from its end (CUT_ORDER) while the whole is too long.
- `documents(db, user_id)`: the text the app already keeps, never a file parsed again: the base
  CVs of the CV panel (their printed fields, `documents.content.text_fields`, and their target)
  and the uploads of Fill my profile whose text is read (`ingestion_documents.text`, done, and
  not held by a secret warning the person has not confirmed, as every run of ingestion skips
  it). The default base CV first, then the uploads, newest first, then the other base CVs; an
  upload a read base CV was converted from is read as that base CV. At most DOC_MAX characters
  each, DOCS_MAX together, DOCS_COUNT_MAX documents. A document goes only under the consent that
  already sends it to Anthropic (decision of 2026-10-10, "Explicit consent the first time a run
  sends documents"): an upload under Fill my profile's (`ingestion.estimate_router`), a base CV
  under the documents module's (`documents.consent`).

**Left out, because it never helps choose a job and must never steer one:** the EEO self
identification answers and their policy, every consent flag and consent row (the background and
reference check consents, the job websites' consents), the settings of where and when to search
(job websites, feeds, schedules, document settings, which hold addresses and consent rows), and
anything secret (no section holds a credential; a document under an unconfirmed secret warning is
not read). Also left out: the person's name, e-mail, phone, links and photo, the preferred name
and pronouns, how they heard of a job and what to leave blank: the app's consent texts promise
the profile goes "without your contact details", and none of these says what work fits.

Read only; nothing here logs a text.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.profile.schemas import (
    AnswersSection,
    ExperienceSection,
    FactsSection,
    IdentitySection,
    SearchSection,
    SkillsSection,
    TermList,
)

LINE_MAX = 200  # characters of one value
PARAGRAPH_MAX = 1_000  # one fact block's body, the motivation, one answer
ENTRIES_MAX = 20  # entries of one list (employers, schools, blocks, categories, ...)
TERMS_MAX = 15  # terms of one skill category or of the industries
PROFILE_MAX = 10_000  # characters of the whole profile as JSON
DOC_MAX = 6_000  # characters of one document's text
DOCS_MAX = 12_000  # characters of all the documents' text
DOCS_COUNT_MAX = 4
NAME_MAX = 100  # characters of a document's name

# The sections the agent reads: a revision of one is a trigger of the automatic refresh
# (titles.due). `preferences` (the wizard's dates, the documents' language and tone) is not one.
SECTIONS = ("identity", "experience", "facts", "skills", "search", "answers")

# What is cut, last entry first, while the profile is longer than PROFILE_MAX: the least telling
# first, the skills and the employers last.
CUT_ORDER = (
    ("facts",),
    ("experience", "education"),
    ("answers", "work_authorization"),
    ("answers", "salary_ranges"),
    ("skills", "gates"),
    ("skills", "lacks"),
    ("search", "industries"),
    ("search", "places_to_avoid"),
    ("identity", "languages"),
    ("skills", "adjacent"),
    ("experience", "employers"),
    ("skills", "has"),
)

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_SPACES = re.compile("[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def _section(model: Any, data: Any) -> Any:
    """A stored section as its model; an empty one when absent or no longer valid."""
    try:
        return model.model_validate(dict(data) if isinstance(data, Mapping) else {})
    except ValueError:
        return model()


def _line(text: object, limit: int = LINE_MAX) -> str:
    return " ".join(_CONTROL.sub(" ", text).split())[:limit] if isinstance(text, str) else ""


def _paragraph(text: object) -> str:
    return _line(text, PARAGRAPH_MAX)


def _words(terms: TermList) -> list[str]:
    """A list's plain terms; a list of regular expressions holds patterns, not words: none."""
    if terms.advanced_regex:
        return []
    return [w for w in (_line(t) for t in terms.terms[:TERMS_MAX]) if w]


def _categories(categories: list[Any]) -> list[dict[str, Any]]:
    return [{"name": _line(one.name), "terms": _words(one)} for one in categories[:ENTRIES_MAX]]


def _pruned(value: Any) -> Any:
    """The value without its empty parts (None, "", [], {}); False and 0 stay."""
    if isinstance(value, dict):
        kept = {key: _pruned(one) for key, one in value.items()}
        return {key: one for key, one in kept.items() if one not in (None, "", [], {})}
    if isinstance(value, list):
        kept_list = [_pruned(one) for one in value]
        return [one for one in kept_list if one not in (None, "", [], {})]
    return value


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def profile(sections: Mapping[str, Any]) -> dict[str, Any]:
    """What the agent reads of the stored sections (module docstring), its empty parts left
    out: an empty profile is `{}`."""
    identity = _section(IdentitySection, sections.get("identity"))
    experience = _section(ExperienceSection, sections.get("experience"))
    facts = _section(FactsSection, sections.get("facts"))
    skills = _section(SkillsSection, sections.get("skills"))
    search = _section(SearchSection, sections.get("search"))
    answers = _section(AnswersSection, sections.get("answers"))
    out = {
        "identity": {
            "headline": _line(identity.headline),
            "city": _line(identity.city),
            "country": _line(identity.country),
            "languages": [
                {"name": _line(one.name), "level": _line(one.level)}
                for one in identity.languages[:ENTRIES_MAX]
            ],
        },
        "experience": {
            "employers": [
                {
                    "organisation": _line(one.organisation),
                    "title": _line(one.title),
                    "start": one.start,
                    "end": one.end or "present",
                    "location": _line(one.location),
                    "contract": one.contract,
                }
                for one in experience.employers[:ENTRIES_MAX]
            ],
            "education": [
                {
                    "school": _line(one.school),
                    "degree": _line(one.degree),
                    "start": one.start,
                    "end": one.end,
                    "location": _line(one.location),
                    "details": _paragraph(one.details),
                }
                for one in experience.education[:ENTRIES_MAX]
            ],
        },
        "facts": [
            {"title": _line(one.title), "body": _paragraph(one.body)}
            for one in facts.blocks[:ENTRIES_MAX]
        ],
        "skills": {
            "years_experience": skills.years_experience,
            "has": _categories(skills.have),
            "adjacent": _categories(skills.adjacent),
            "lacks": _categories(skills.none),
            "gates": [{"name": _line(one.name), "terms": _words(one)} for one in skills.gates],
        },
        "search": {
            "places_to_avoid": _words(search.location_exclude),
            "contract_types": list(search.contract_types),
            "remote_stance": search.remote_stance,
            "seniority": list(search.seniority),
            "industries": _words(search.industries),
            "company_types": list(search.company_types),
            "pay_sought": search.compensation.model_dump(mode="json"),
        },
        "answers": {
            "work_authorization": [
                {
                    "country": _line(one.country),
                    "answer": _line(one.answer),
                    "needs_sponsorship": one.needs_sponsorship,
                }
                for one in answers.work_authorization[:ENTRIES_MAX]
            ],
            "notice_period": _paragraph(answers.notice_period),
            "earliest_start": _line(answers.earliest_start),
            "salary_policy": _paragraph(answers.salary_policy),
            "salary_stance": answers.salary_stance,
            "salary_ranges": [one.model_dump(mode="json") for one in answers.salary_ranges],
            "relocation": _paragraph(answers.relocation),
            "travel": _paragraph(answers.travel),
            "willing_onsite": answers.willing_onsite,
            "willing_hybrid": answers.willing_hybrid,
            "years_experience_stated": _line(answers.years_experience_stated),
            "highest_degree": _line(answers.highest_degree),
            "highest_degree_year": answers.highest_degree_year,
            "motivation": _paragraph(answers.motivation),
        },
    }
    found: dict[str, Any] = _pruned(out)
    for path in CUT_ORDER:
        while _size(found) > PROFILE_MAX:
            parent = found
            for key in path[:-1]:
                parent = parent.get(key, {})
            entries = parent.get(path[-1])
            if not entries:
                break
            entries.pop()
        found = _pruned(found)
    return found


# --- the documents ------------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentText:
    """The text of one document as the agent reads it: `source` is `cv` (a base CV of the CV
    panel) or `upload` (a document of Fill my profile)."""

    source: str
    name: str
    text: str

    def data(self) -> dict[str, str]:
        return {"source": self.source, "name": self.name, "text": self.text}


def _tidy(text: str) -> str:
    """The text with its control characters and runs of spaces and blank lines made short."""
    lines = (_SPACES.sub(" ", _CONTROL.sub(" ", line)).strip() for line in text.splitlines())
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _base_cv_text(draft: object) -> str:
    """A base CV's target and printed fields, one per line (`content.text_fields`); empty when
    its draft no longer validates."""
    from app.documents import content as c

    try:
        parsed = c.parse("base", draft)  # type: ignore[arg-type]
    except c.ContentInvalidError:
        return ""
    lines = [parsed.target] if isinstance(parsed, c.CvContent) else []
    lines += [text for _, text in c.text_fields(parsed)]
    return "\n".join(one for one in lines if one.strip())


def _base_cvs(db: Session, user_id: uuid.UUID) -> list[tuple[bool, uuid.UUID | None, DocumentText]]:
    """The person's base CVs with text, the default first, then the newest: (default, the
    upload it was converted from, the text)."""
    from app.documents.models import Document, Scope

    rows = db.execute(
        sa.select(Document.is_default, Document.source_document_id, Document.name, Document.draft)
        .where(Document.user_id == user_id, Document.scope == Scope.base)
        .order_by(Document.is_default.desc(), Document.updated_at.desc(), Document.id)
        .limit(DOCS_COUNT_MAX)
    ).all()
    out = []
    for default, source, name, draft in rows:
        text = _tidy(_base_cv_text(draft))
        if text:
            out.append((default, source, DocumentText("cv", _line(name, NAME_MAX), text)))
    return out


def _uploads(db: Session, user_id: uuid.UUID, skip: set[uuid.UUID]) -> list[DocumentText]:
    """The uploads whose text is read and that any run of ingestion may read, newest first, the
    head of each only (the text is cut in the database)."""
    from app.ingestion.models import IngestionDocument, TextStatus

    doc = IngestionDocument
    rows = db.execute(
        sa.select(doc.id, doc.name, sa.func.left(doc.text, DOC_MAX))
        .where(
            doc.user_id == user_id,
            doc.text_status == TextStatus.done,
            doc.text.is_not(None),
            sa.not_(sa.and_(doc.secret_warning.is_(True), doc.secret_confirmed_at.is_(None))),
        )
        .order_by(doc.created_at.desc(), doc.id)
        .limit(DOCS_COUNT_MAX + len(skip))
    ).all()
    out = []
    for document_id, name, text in rows:
        tidy = _tidy(text or "")
        if document_id not in skip and tidy:
            out.append(DocumentText("upload", _line(name, NAME_MAX), tidy))
    return out


def documents(
    db: Session,
    user_id: uuid.UUID,
    consents: Callable[[Session, uuid.UUID], tuple[bool, bool]] | None = None,
) -> tuple[DocumentText, ...]:
    """The documents' text the agent reads, in order and within the caps (module docstring)."""
    uploads_allowed, base_allowed = (consents or _consents)(db, user_id)
    bases = _base_cvs(db, user_id) if base_allowed else []
    skip = {source for _, source, _ in bases if source is not None}
    uploads = _uploads(db, user_id, skip) if uploads_allowed else []
    ordered = [one for default, _, one in bases if default]
    ordered += uploads + [one for default, _, one in bases if not default]
    out: list[DocumentText] = []
    left = DOCS_MAX
    for one in ordered[:DOCS_COUNT_MAX]:
        text = one.text[: min(DOC_MAX, left)].rstrip()
        if not text:
            break
        out.append(DocumentText(one.source, one.name, text))
        left -= len(text)
    return tuple(out)


def _consents(db: Session, user_id: uuid.UUID) -> tuple[bool, bool]:
    """(Fill my profile's consent, the documents module's consent), each to its current text."""
    from app.documents import consent as documents_consent
    from app.ingestion import estimate_router

    return estimate_router.has_consented(db, user_id), documents_consent.has_consented(db, user_id)
