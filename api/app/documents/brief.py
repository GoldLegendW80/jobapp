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
drawn from the text (`requirement_lines`), the parts of the text the brief names
(`parts_of`: what the posting says about the company, the role, the profile, the benefits, the
pay and the contract, under their headings), and the fit analysis of the jobs module (the score,
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
import unicodedata
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
SECTION_CHARS = 3_000
HEADING_CHARS = 60
# The parts of a posting the brief names, by the words of their heading (English and French,
# case and accents folded by `_fold`). A heading is a short line that names one of them, alone
# or before a colon ("About us", "Benefits:", "Salary: 50,000 EUR a year").
PARTS: dict[str, tuple[str, ...]] = {
    "about_company": (
        "about us",
        "about the company",
        "who we are",
        "our company",
        "the company",
        "qui sommes nous",
        "a propos",
        "l entreprise",
        "notre entreprise",
    ),
    "responsibilities": (
        "responsibilities",
        "what you will do",
        "what you ll do",
        "your role",
        "the role",
        "your mission",
        "missions",
        "vos missions",
        "le poste",
        "about the role",
        "about the job",
        "about the position",
    ),
    "requirements_text": (
        "requirements",
        "qualifications",
        "what you bring",
        "who you are",
        "your profile",
        "you have",
        "about you",
        "profil recherche",
        "votre profil",
        "competences",
    ),
    "benefits": (
        "benefits",
        "perks",
        "what we offer",
        "why join us",
        "why join",
        "avantages",
        "nous offrons",
        "ce que nous offrons",
    ),
    "salary_text": ("salary", "compensation", "pay", "remuneration", "salaire"),
    "contract_text": (
        "contract",
        "type of contract",
        "employment type",
        "contrat",
        "type de contrat",
    ),
}


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


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.casefold()).split())


def _heading(line: str) -> tuple[str, str] | None:
    """The part a heading line opens and what follows its colon on the same line, else None."""
    stripped = line.strip().strip("#*_ ").strip()
    if not stripped:
        return None
    head, _, rest = stripped.partition(":")
    if len(head) > HEADING_CHARS:
        return None
    folded = _fold(head)
    for part, words in PARTS.items():
        if folded in words:
            return part, rest.strip()
    # "About Example Robotics": the company's own name after "About".
    if folded.startswith("about ") and len(folded) <= 40:
        return "about_company", rest.strip()
    return None


def parts_of(text: str) -> dict[str, str]:
    """The named parts of a posting's own text (`PARTS`): the lines under each heading up to the
    next heading, each part at most SECTION_CHARS, the first of each name kept. A heuristic that
    only adds: the whole text is in the brief too, so a part it misses is still read."""
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        opened = _heading(line)
        if opened is not None:
            part, rest = opened
            current = part if part not in found else None
            if current is not None:
                found[current] = [rest] if rest else []
            continue
        if current is not None and line.strip():
            found[current].append(line.strip())
    out = {}
    for part, lines in found.items():
        joined = "\n".join(lines).strip()[:SECTION_CHARS]
        if joined:
            out[part] = joined
    return out


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
            "known": (
                "The app stores nothing about this company beyond its name: what the posting "
                "text says of it is in from_the_text.about_company when the text has that part."
            ),
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
        # What the posting itself says of the company, the role, the profile, the benefits,
        # the pay and the contract, when its text has such a part (`parts_of`).
        "from_the_text": parts_of(text),
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
