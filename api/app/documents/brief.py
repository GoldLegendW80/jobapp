"""The job brief: everything the app holds about one posting, in one data block (task D29, Part B;
SPEC-documents.md, "The writers", "The hiring panel").

The owner, 2026-11-06: "hiring-panel-skill must know everything about the jobs to be perfectly
specialise on the job, the company, the offer ect.." Code assembles the brief before a CV or a
letter is written; the writers read it with `get_job_brief` (writer_tools.py) and every reviewer
of the hiring panel gets it in its data block (panel.py).

`for_posting` reads the person's own posting only: its title, company, location, contract, pay,
work mode, language, source and address, the whole posting text (the sweep stores up to
`sweep.POSTING_TEXT_MAX` characters; a text at that size is marked `text_cut`: the app has no
fetch of a posting page outside the sweep, so nothing is fetched again), the requirement lines
drawn from the text (`requirement_lines`), and the fit analysis of the jobs module (the score,
the matched terms `competencies` and the missing ones `gaps`). The app stores nothing about a
company beyond its name: `company.known` says so, and no reviewer may assume more.

`for_targets` stands in for a posting when the panel reviews a converted upload (D29, item 9):
the person's target roles, the search titles and the profile's headline, as the skill asks for
the role when there is no posting.

The brief is a stranger's text (the posting) beside the person's own (their headline): it goes
in escaped data blocks, held in process, never logged.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, undefer

from app.documents import upload
from app.jobs import sweep
from app.jobs.models import Job
from app.profile.models import Profile

TEXT_MAX = sweep.POSTING_TEXT_MAX
REQUIREMENTS_MAX = 30
REQUIREMENT_CHARS = 300
# A requirement line: a bullet, a dash, a star or a number that opens a line of the posting.
_BULLET = re.compile(r"^\s*(?:[-*•●▪‣⁃]|\d{1,2}[.)])\s+(\S.*)$")  # noqa: RUF001 (bullet characters)
TARGETS_MAX = 12


def requirement_lines(text: str) -> list[str]:
    """The posting's bulleted lines, in order, at most REQUIREMENTS_MAX, each clipped. A
    heuristic that only adds: the whole text is in the brief too, so a line it misses is still
    read by the writer and every reviewer."""
    found: list[str] = []
    for line in text.splitlines():
        match = _BULLET.match(line)
        if match is None:
            continue
        item = " ".join(match.group(1).split())[:REQUIREMENT_CHARS]
        if item and item not in found:
            found.append(item)
        if len(found) >= REQUIREMENTS_MAX:
            break
    return found


def _pay(job: Job) -> dict[str, Any] | None:
    if job.pay_min is None and job.pay_max is None:
        return None
    return {
        "min": job.pay_min,
        "max": job.pay_max,
        "currency": job.pay_currency,
        "period": job.pay_period.value if job.pay_period is not None else None,
    }


def for_posting(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> dict[str, Any] | None:
    """The brief of one of the person's postings; None for any other id."""
    job = db.execute(
        sa.select(Job)
        .where(Job.id == posting_id, Job.user_id == user_id)
        .options(undefer(Job.posting_text))
    ).scalar_one_or_none()
    if job is None:
        return None
    text = job.posting_text or job.description or ""
    return {
        "title": job.title,
        "company": {
            "name": job.company,
            "known": "Only the name: the app stores nothing else about this company.",
        },
        "location": job.location or None,
        "contract": job.contract_type.value,
        "pay": _pay(job),
        "work_mode": job.work_mode.value if job.work_mode is not None else None,
        "language": upload.language_of(text).value if text else None,
        "source": job.source,
        "url": job.url,
        "published_on": job.published_on.isoformat() if job.published_on else None,
        "text": text,
        "text_cut": len(text) >= TEXT_MAX,
        "requirements": requirement_lines(text),
        "fit": {
            "score": job.fit,
            "matched": list(job.competencies or ()),
            "missing": list(job.gaps or ()),
        },
    }


def _terms(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = value.get("terms") or value.get("items") or []
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        term = item.get("term") if isinstance(item, dict) else item
        if isinstance(term, str) and term.strip():
            out.append(term.strip())
    return out[:TARGETS_MAX]


def for_targets(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    """The roles the person aims at, in place of a posting (module docstring)."""
    sections: Any = db.scalar(sa.select(Profile.sections).where(Profile.user_id == user_id)) or {}
    search = sections.get("search") or {}
    identity = sections.get("identity") or {}
    headline = identity.get("headline") if isinstance(identity, dict) else None
    return {
        "title": None,
        "target_roles": _terms(search.get("title_include")),
        "headline": headline if isinstance(headline, str) else None,
        "company": {"name": None, "known": "No posting: judge for the target roles."},
    }


def role_of(brief: dict[str, Any]) -> tuple[str, str]:
    """The role and the company a reviewer plays at: "the hiring manager of <title> at
    <company>". For an upload, the target roles and "a company hiring for them"."""
    title = brief.get("title")
    company = (brief.get("company") or {}).get("name")
    if title:
        return str(title), str(company or "the company")
    roles = brief.get("target_roles") or []
    wanted = ", ".join(roles) if roles else (brief.get("headline") or "the role the person seeks")
    return str(wanted), "a company hiring for it"
