"""Fit of a posting against a user's skills section: a port of engine/jobs.py `score`.

`score(posting_text, skills, title=...)` is pure: no database, no clock, no I/O. It reproduces
the engine decision for decision (tests/jobs/test_scoring_golden.py runs the engine itself on
every posting of ../data and compares), reading the user's `skills` section instead of
profile/skills.json and matching terms through `app.profile.matching`, the single source of
matching rules.

How the score is made, exactly as the engine does it:
- The text is lowercased. The requirements part is the text from the first requirements marker
  (REQ_START, "requirements", "qualifications", "about you", ...) to the next end marker
  (REQ_END, "benefits", "about us", ...) after it, or nothing when no marker is found. The
  markers are English only, as in the engine: a French posting has no requirements part.
- Each category of have, adjacent and none, in that order, counts its occurrences in the whole
  text and in the requirements part, each capped at 3 (OCCURRENCE_CAP): n = whole + 2 x part,
  so 0 to 9. A category with n > 0 is named in `competencies`, adds n to the weight and
  n x credit to the credit, with credit 1 for have, 0.5 for adjacent and -0.5 for none.
  Adjacent and none categories are also named in `gaps`.
- fit = round(100 x credit / weight), 0 when nothing matched. Python's round: halves go to
  the even number (62.5 gives 62).
- Years: every "<1 or 2 digits> [+] years|yrs" with "experience" within 80 characters before
  or after counts; the largest is needed. A title containing senior, sr or lead needs at least
  5. When the need is over the section's years_experience, fit loses
  min(35, round((need - years_experience) x 8)) and `gaps` gets "<need>+ years". Only digits
  count: "five years", "5 ans" or "expérience" (accented) are not read. "3-5 years" needs 5.
  A section with no years_experience skips this step.
- Each gate that matches the lowercased text takes its penalty (25 for the converted engine
  gates) and is named in `gaps`.
- The result is clamped to 0..100.

Two deliberate differences, neither reachable from profile/skills.json: a gate's penalty is the
gate's own (the engine fixed 25), so a user can weigh gates; and a category or gate with no terms
matches nothing (the engine's pattern for an empty list matched the empty string everywhere).
Matching is case-insensitive in both: the engine searches its gate regexes on lowercased text,
the port with `re.IGNORECASE` on the same lowercased text.

Bounded text: only the first SCORED_CHARS_MAX (64,000) characters of a posting are scored.
Python's `re` costs about 1 ms per category per 64 KB with case-insensitive whole-word patterns,
so a 1 MB posting would take a second; capped, it scores in well under 200 ms
(tests/jobs/test_scoring.py measures it). The longest posting of ../data is under 13,000
characters, five times below the cap, so the golden test covers the engine's whole range; a
longer text is a pasted page, not a job description, and its tail would only add noise.

A term list that does not compile (stored before the profile refused such lists, for instance
two terms naming the same group) is skipped with one logged warning per list and process, never
an error for every posting: the rest of the profile still scores.
"""

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from app.profile import matching
from app.profile.schemas import Category, SkillsSection

OCCURRENCE_CAP = 3
REQUIREMENTS_WEIGHT = 2
CREDIT = (("have", 1.0), ("adjacent", 0.5), ("none", -0.5))
YEARS_STEP = 8
YEARS_MAX_PENALTY = 35
SENIOR_YEARS = 5
YEARS_WINDOW = 80
SCORED_CHARS_MAX = 64_000

logger = logging.getLogger(__name__)


@lru_cache(maxsize=512)
def _compiled(terms: tuple[str, ...], advanced_regex: bool, where: str) -> re.Pattern[str] | None:
    """The pattern of a term list, None for an empty list or one that does not compile (logged
    once, by its place in the section, never its contents)."""
    try:
        return matching.compile_terms(terms, advanced_regex)
    except re.error as exc:
        logger.warning(
            "skills term list does not compile, skipped in scoring",
            extra={"list": where, "position": exc.pos},
        )
        return None


REQ_START = re.compile(
    r"requirements|qualifications|what you.ll bring|what we.re looking for|what you bring|"
    r"about you|who you are|must have|you have|you are|your profile|we.re looking for|"
    r"ideal candidate|skills and experience",
    re.I,
)
REQ_END = re.compile(
    r"benefits|what we offer|perks|compensation|salary|about us|our values|"
    r"equal opportunity|why join|interview process|how we work",
    re.I,
)
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|yrs)")
SENIOR_TITLE = re.compile(r"\bsenior\b|\bsr\.?\b|\blead\b", re.I)


def engine_text(posting_text: str, header: object = None) -> str:
    """The text the engine scored for a posting: its posting.txt as written, header included.

    The importer stores the header lines of posting.txt (`KEY: value`, up to the first blank
    line) in legacy["posting_header"] and the rest as posting_text. The engine scored the whole
    file, so the header's words (the title, the company, the location) counted too. This puts
    the header back in front, `KEY: value` per line and a blank line, which gives the same
    score as the original file on every posting of ../data (tests/jobs/test_rescore_real.py).
    Without a header (a posting added by hand or found by a sweep) the text is scored as is.
    """
    if not isinstance(header, dict) or not header:
        return posting_text
    lines = "".join(f"{key}: {value}\n" for key, value in header.items() if isinstance(key, str))
    return f"{lines}\n{posting_text}"


@dataclass(frozen=True)
class ScoreResult:
    fit: int
    competencies: list[str]
    gaps: list[str]


def requirements_part(low: str) -> str:
    """The requirements section of a lowercased posting, or "" when it has no start marker."""
    start = REQ_START.search(low)
    if start is None:
        return ""
    part = low[start.start() :]
    end = REQ_END.search(part)
    return part[: end.start()] if end else part


def _count(pattern: re.Pattern[str], text: str) -> int:
    """Occurrences as `len(pattern.findall(text))` counts them, capped at OCCURRENCE_CAP."""
    found = 0
    for _ in pattern.finditer(text):
        found += 1
        if found == OCCURRENCE_CAP:
            break
    return found


def years_needed(low: str, title: str) -> int:
    """The years of experience the posting asks for, 0 when it names none."""
    years = [
        int(m.group(1))
        for m in YEARS.finditer(low)
        if "experience" in low[max(0, m.start() - YEARS_WINDOW) : m.end() + YEARS_WINDOW]
    ]
    need = max(years, default=0)
    if SENIOR_TITLE.search(title):
        need = max(need, SENIOR_YEARS)
    return need


def score(posting_text: str, skills: SkillsSection, *, title: str = "") -> ScoreResult:
    """Fit 0..100, the categories the posting names and the gaps, for one posting."""
    low = posting_text[:SCORED_CHARS_MAX].lower()
    req = requirements_part(low)
    credit = weight = 0.0
    named: list[str] = []
    gaps: list[str] = []
    for level, mult in CREDIT:
        categories: list[Category] = getattr(skills, level)
        for index, category in enumerate(categories):
            pattern = _compiled(
                tuple(category.terms), category.advanced_regex, f"{level} {index + 1}"
            )
            if pattern is None:
                continue
            n = _count(pattern, low) + REQUIREMENTS_WEIGHT * _count(pattern, req)
            if n:
                weight += n
                credit += n * mult
                named.append(category.name)
                if mult < 1:
                    gaps.append(category.name)
    fit = round(100 * credit / weight) if weight else 0
    if skills.years_experience is not None:
        need = years_needed(low, title)
        if need > skills.years_experience:
            fit -= min(YEARS_MAX_PENALTY, round((need - skills.years_experience) * YEARS_STEP))
            gaps.append(f"{need}+ years")
    for index, gate in enumerate(skills.gates):
        pattern = _compiled(tuple(gate.terms), gate.advanced_regex, f"gate {index + 1}")
        if pattern is not None and pattern.search(low) is not None:
            fit -= gate.penalty
            gaps.append(gate.name)
    return ScoreResult(fit=max(0, min(100, fit)), competencies=named, gaps=gaps)
