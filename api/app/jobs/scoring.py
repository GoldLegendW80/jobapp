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
- Bonus (opportunities O4, owner 2026-10-20), only when `search` is passed: see `bonus` below.
  It is added before the clamp, so a posting a gate took below 0 stays at 0, and the fit is
  never above 100. The engine had no bonus, so the golden test passes no `search`.

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
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from app.profile import matching
from app.profile.schemas import Category, SearchSection, SkillsSection

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
class Bonus:
    """The points `bonus` adds and what earned them, in plain words for the match reason."""

    points: int = 0
    seniority: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    company_types: tuple[str, ...] = ()

    @property
    def matched(self) -> tuple[str, ...]:
        return self.seniority + self.industries + self.company_types


NO_BONUS = Bonus()


@dataclass(frozen=True)
class ScoreResult:
    fit: int
    competencies: list[str]
    gaps: list[str]
    bonus: Bonus = field(default=NO_BONUS)


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


def score(
    posting_text: str,
    skills: SkillsSection,
    *,
    title: str = "",
    search: SearchSection | None = None,
) -> ScoreResult:
    """Fit 0..100, the categories the posting names and the gaps, for one posting; with the
    person's `search` section, plus the bonus of its seniority, industries and company types
    (the competencies and gaps never change with it)."""
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
    extra = bonus(posting_text, title, search) if search is not None else NO_BONUS
    fit += extra.points
    return ScoreResult(fit=max(0, min(100, fit)), competencies=named, gaps=gaps, bonus=extra)


# --- the bonus of what the person aims for (opportunities O4) ---------------------------------
#
# Owner, 2026-10-20: seniority, industries and company types feed the score and never filter.
# A posting earns a part for each of the three it matches, each part a third of BONUS_CAP, so
# one part gives 3 points, two give 7 and all three give 10 (round(10 x parts / 3)). A field the
# person left empty earns nothing; nothing here drops a posting, and the sweep's filters and the
# deck's filter never read these fields.
#
# - Seniority is read in the title only: a body names the levels of colleagues and readers
#   ("you will work with senior engineers"), the title names the role's own.
# - Industries are the person's own terms (a TermList, matched as every term list is, through
#   app.profile.matching), in the title and the text.
# - Company types are read in the title and the text through the allow list below.
#
# The allow lists name what is accepted, in English and French, as whole words. A word is a
# hyphen compound: no letter, digit or hyphen just before or after, so "multi-stage", "lead-gen"
# and "non-government" are other words, and a space inside an entry also reads a hyphen
# ("team lead" reads "team-lead", "senior level" reads "senior-level"). Single words that mean
# something else in a posting are left out on purpose (fail closed): "agency" ("high agency"),
# "conseil" ("conseil d'administration"), "charity" ("charity days"), "mid" ("mid-market"),
# "large enterprise" (a customer), "early stage" alone ("at an early stage of the project").
# Three words are read by their place rather than alone:
# - "lead" is a level unless a marketing noun follows it ("lead generation", "lead scoring");
# - "stage" (French for internship) is a level only as a title segment of its own: first in the
#   title or after a separator ("Stage: Data Engineer", "Data Engineer (Stage)", "offre de
#   stage"), or last in it, never after an English adjective ("multi stage", "early stage")
#   and never before a theatre noun ("stage manager");
# - "early stage" and "seed stage" are a startup only before a company noun.
# Text is NFKC normalised, every dash and minus sign read as "-", white space runs read as one
# space, and lowercased, so "Sénior", "SENIOR", "start-up" written with a non breaking hyphen and
# full width letters read as their plain forms. Industries are read with accents folded too, so
# "sante" and "santé" name the same industry.

BONUS_CAP = 10
BONUS_PARTS = 3

# Levels and types as the person reads them in the match reason.
SENIORITY_LABELS = {
    "intern": "intern",
    "junior": "junior",
    "mid": "mid-level",
    "senior": "senior",
    "lead": "lead",
    "principal": "principal",
}
COMPANY_TYPE_LABELS = {
    "startup": "startup",
    "scaleup": "scale-up",
    "large_company": "large company",
    "public_sector": "public sector",
    "nonprofit": "nonprofit",
    "agency": "agency",
    "consulting": "consulting",
}

# Each entry is literal words (a space stands for a space or a hyphen, as in "team lead" and
# "team-lead") unless it starts with "re:", then it is a pattern of this file over the text as
# bonus_text gives it (one space between words; bounded, no nested quantifier).
_ROLE_NOUN = r"(?:engineer|scientist|developer|designer|architect|researcher|analyst)"
# "lead" before one of these is marketing ("lead generation", "lead scoring"), not a level.
_LEAD_NOT = r"(?:gen|nurtur|scor|qualif|magnet|routing|management|capture|list)"
# "stage" right after one of these is a step of something ("multi stage", "early stage").
_STAGE_AFTER_NOT = "".join(
    f"(?<!{word} )"
    for word in (
        "early", "late", "later", "seed", "growth", "final", "next", "first", "second", "third",
        "last", "single", "multi", "two", "three", "each", "every", "any", "this", "that", "back",
        "main", "center", "centre", "big", "world", "global", "same", "one", "dev", "test",
        "prod", "sound", "mid",
    )
)  # fmt: skip
# The places where "stage" is a title segment of its own (see the comment above).
_STAGE = (
    _STAGE_AFTER_NOT
    + r"(?:(?:(?<![^|(\[,:/;])|(?<=[|(\[,:/;] )|(?<= - )|(?<=\bde )|(?<=\bd')|(?<=\ben ))stage"
    + r"|stage(?=$|[|)\],:;/]| [|(\[/]| - | \d| de | d'| en | h/f| f/h| \())"
    + r"(?! (?:manager|management|managers|technician|technicians|hand|hands|crew|director"
    + r"|door|lighting|sound|design|designer|left|right|combat|presence|fright))"
)
_COMPANY_NOUN = r"(?:start-?ups?|compan(?:y|ies)|ventures?|business(?:es)?|firms?)"
SENIORITY_WORDS: dict[str, tuple[str, ...]] = {
    "intern": (
        "intern", "interns", "internship", "internships", "trainee", "trainees", "re:" + _STAGE,
        "stagiaire", "stagiaires", "alternance", "alternant", "alternante", "apprenti",
        "apprentie", "apprentice", "apprenticeship", "work-study", "work study",
    ),
    "junior": (
        "junior", "juniors", "junior level", "jr", "entry level", "graduate", "new grad",
        "débutant", "débutante", "jeune diplômé", "jeune diplômée",
    ),
    "mid": (
        "mid level", "midlevel", "mid senior", "intermediate", "confirmé", "confirmée",
    ),
    "senior": ("senior", "sénior", "seniors", "séniors", "sr", "senior level", "mid senior"),
    "lead": (
        "re:lead(?! " + _LEAD_NOT + ")", "leader", "team lead", "tech lead", "co lead",
        "lead level",
    ),
    "principal": (
        "principal", "distinguished", "re:staff(?: [\\w-]+){0,2}? " + _ROLE_NOUN,
    ),
}  # fmt: skip
COMPANY_TYPE_WORDS: dict[str, tuple[str, ...]] = {
    "startup": (
        "startup", "startups", "start-up", "start-ups", "pre-seed", "series a", "série a",
        "series a funded", "series a backed", "jeune pousse", "jeunes pousses",
        "re:(?:early|seed)[ -]stage " + _COMPANY_NOUN,
    ),
    "scaleup": (
        "scaleup", "scaleups", "scale-up", "scale-ups", "hypergrowth", "hyper-growth",
        "hypercroissance", "forte croissance", "high-growth", "series b", "series c", "série b",
        "série c",
    ),
    "large_company": (
        "multinational", "multinationale", "fortune 500", "fortune 100", "global 500", "cac 40",
        "sbf 120", "ftse 100", "grand groupe", "groupe international",
    ),
    "public_sector": (
        "public sector", "secteur public", "fonction publique", "service public",
        "civil service", "government", "gouvernement", "ministère", "ministry",
        "administration publique", "public administration", "collectivité territoriale",
        "collectivités territoriales", "établissement public",
    ),
    "nonprofit": (
        "nonprofit", "non-profit", "not-for-profit", "not for profit", "ngo", "ngos", "ong",
        "but non lucratif", "sans but lucratif", "registered charity", "charitable organization",
        "charitable organisation", "association loi 1901",
    ),
    "agency": (
        "digital agency", "creative agency", "marketing agency", "advertising agency",
        "design agency", "web agency", "media agency", "agence digitale", "agence web",
        "agence créative", "agence de communication", "agence de publicité", "agence marketing",
        "agence de design",
    ),
    "consulting": (
        "consulting firm", "consulting company", "consulting group", "consultancy",
        "management consulting", "cabinet de conseil", "société de conseil",
        "cabinet de consulting", "esn", "ssii",
    ),
}  # fmt: skip

# Every dash and minus sign Unicode has, read as a hyphen.
_DASHES = str.maketrans(
    dict.fromkeys(
        map(chr, (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE63, 0xFF0D)), "-"
    )
)


def _word_pattern(entries: tuple[str, ...]) -> re.Pattern[str]:
    parts = []
    for entry in entries:
        if entry.startswith("re:"):
            parts.append(entry[3:])
        else:
            parts.append("[ -]".join(matching.escape(word) for word in entry.split()))
    return re.compile(rf"(?<![\w-])(?:{'|'.join(parts)})(?![\w-])", re.IGNORECASE)


_SENIORITY = {level: _word_pattern(words) for level, words in SENIORITY_WORDS.items()}
_COMPANY_TYPES = {kind: _word_pattern(words) for kind, words in COMPANY_TYPE_WORDS.items()}
SHOWN_MAX = 3  # industries named in the match reason


_SPACES = re.compile(r"\s+")


def bonus_text(text: str) -> str:
    """`text` as the bonus reads it: NFKC, dashes as "-", a curly apostrophe as "'", one space
    for any white space run, lowercase."""
    plain = unicodedata.normalize("NFKC", text).translate(_DASHES).replace("\u2019", "'")
    return _SPACES.sub(" ", plain).strip().lower()


def fold_accents(text: str) -> str:
    """`text` without its accents ("santé" reads "sante"), for the industry terms."""
    return "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )


def _industries(search: SearchSection, text: str) -> tuple[str, ...]:
    """The person's industry terms the text names, at most SHOWN_MAX, in the person's words (a
    plain term as written, an advanced one as the text has it). Plain terms and the text are
    read with accents folded; an advanced term is the person's own pattern, read as written."""
    terms = search.industries
    if terms.advanced_regex:
        pattern = _compiled(tuple(terms.terms), True, "industries")
    else:
        text = fold_accents(text)
        folded = tuple(fold_accents(term) for term in terms.terms)
        pattern = _compiled(folded, False, "industries")
    if pattern is None:
        return ()
    by_fold = {fold_accents(term).casefold(): term for term in terms.terms}
    found: dict[str, None] = {}
    for match in pattern.finditer(text):
        seen = match.group(0).casefold()
        name = None
        if not terms.advanced_regex:
            for ending in ("", "s", "es"):
                if ending and not seen.endswith(ending):
                    continue
                name = by_fold.get(seen[: len(seen) - len(ending)] if ending else seen)
                if name is not None:
                    break
        found[name or match.group(0)[:LABEL_MAX]] = None
        if len(found) == SHOWN_MAX:
            break
    return tuple(found)


LABEL_MAX = 60


def bonus(posting_text: str, title: str, search: SearchSection) -> Bonus:
    """The points the person's seniority, industries and company types add to a posting, and
    which of them it matched. Pure, linear in the text (one pass per level and type)."""
    if not (search.seniority or search.industries.terms or search.company_types):
        return NO_BONUS
    head = bonus_text(title)
    body = f"{head}\n{bonus_text(posting_text[:SCORED_CHARS_MAX])}"
    levels = tuple(
        SENIORITY_LABELS[level] for level in search.seniority if _SENIORITY[level].search(head)
    )
    industries = _industries(search, body) if search.industries.terms else ()
    kinds = tuple(
        COMPANY_TYPE_LABELS[kind]
        for kind in search.company_types
        if _COMPANY_TYPES[kind].search(body)
    )
    parts = sum(1 for matched in (levels, industries, kinds) if matched)
    points = round(BONUS_CAP * parts / BONUS_PARTS)
    return Bonus(points=points, seniority=levels, industries=industries, company_types=kinds)
