"""Posting titles as job boards write them, cleaned before the store and its duplicate check, and
the key that makes one job found on several boards one row (jobs J25, #817).

The owner's sweep of 2026-11-19 (81ab9533, La Réunion) stored the same LHH job twice: Hellowork's
"Data Scientist H/F LHH Recruitment Solutions" (no employer stated, the agency in the title) and
Jobijoba's "Data scientist h/f". Accept and refuse tables: tests/jobs/test_j25_titles_dedup.py.

`clean(title, company, location)`:
- a gender marker is taken out wherever it stands: two or three letters joined by "/", in
  parentheses or brackets or not, of one of the allowed sets (H/F, F/H, M/F, M/W/D, F/H/X, ...):
  an allow list, so "C/C++", "I/O" or "B/C" stay;
- a leading offer phrase ("Offre d'emploi :") and a trailing board name after a separator
  (" - Hellowork", " | Indeed.com", BOARDS) are board boilerplate, taken out;
- what follows the last gender marker, or the last separator (" - ", " | ", a dash), is taken
  out when it is the posting's company (`same_company`) or a board; when no company is stated it
  becomes the company, after a marker when it reads as an organisation (`_org_like`), after a
  separator only when it is a recruiter (`is_recruiter`); anything else stays in the title
  (fail closed: "Chef de projet Data & IA" keeps "Data & IA", "Data Scientist - Saint-Denis"
  keeps its place);
- a title that would be left with no letter is kept as it was.

`cross_key(company, title, location)`: the company without its generic words (`company_core`:
"LHH Recruitment Solutions" and "LHH" are one), the cleaned title's words and the place
(`place_key`: the town and the département or overseas region it names, "Saint-Denis (974)" and
"Saint-Denis, La Réunion" are one place, "Saint-Denis (93)" another); None when the employer is
not stated or the place is unknown (fail closed: only the address makes such a posting a
duplicate). The store compares it within WINDOW_DAYS of the earlier row's discovery.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.jobs import gazetteer
from app.jobs.slug import text_key

TITLE_MAX = 400  # characters of a title read
WINDOW_DAYS = 60  # a job seen again later than this is another posting of it
UNKNOWN_EMPLOYER = "Employer not stated"  # sweep.UNKNOWN_EMPLOYER

# Gender markers: the letters of one marker, as a set, must fit one of these and hold a pair.
MARKER = re.compile(
    r"(?<![\w/+])[(\[]?\s*([a-z])\s*/\s*([a-z])(?:\s*/\s*([a-z]))?\s*[)\]]?(?![\w/+])", re.I
)
MARKER_SETS = (
    frozenset("hfx"), frozenset("hfd"), frozenset("hfn"), frozenset("mfx"), frozenset("mfd"),
    frozenset("mwd"), frozenset("mwx"),
)  # fmt: skip
MARKER_PAIRS = (frozenset("hf"), frozenset("mf"), frozenset("mw"))
SEPARATOR = re.compile(r"\s+[-\u2013\u2014|\u00b7\u2022]\s+|\s*\|\s*")
LEADING = re.compile(
    r"^\s*(?:offres?\s+d\s*['\u2019]?\s*emploi|offres?\s+emploi|job\s+offers?)\s*[:|\u2013\u2014-]?\s+",
    re.I,
)
EDGES = " \t-\u2013\u2014|\u00b7\u2022,:;/"
# Job boards whose name a title may carry at its end (text_key, a ".com" or ".fr" aside).
BOARDS = frozenset(
    {"hellowork", "indeed", "jobijoba", "linkedin", "monster", "glassdoor", "welcome to the jungle",
     "france travail", "pole emploi", "apec", "cadremploi", "meteojob", "regionsjob", "jobteaser",
     "optioncarriere", "jooble", "talent", "free work", "lesjeudis", "domtomjob", "emploi re"}
)  # fmt: skip
# Recruitment agencies (company_core) and the words that name one.
RECRUITERS = frozenset(
    {"lhh", "michael page", "page personnel", "page executive", "hays", "robert half",
     "robert walters", "walters people", "randstad", "adecco", "manpower", "manpowergroup",
     "expectra", "actual", "kaptiv", "synergie", "crit", "proman", "start people", "partnaire",
     "spring", "harvey nash", "morgan philips", "approach people", "emploi domtom", "fed"}
)  # fmt: skip
AGENCY_WORDS = frozenset(
    {"recruitment", "recrutement", "recruiting", "interim", "staffing", "headhunting",
     "headhunters"}
)  # fmt: skip
# Words a company name carries that do not tell the company (company_core).
GENERIC = frozenset(
    {"recruitment", "solutions", "solution", "group", "groupe", "sas", "sasu", "sarl", "sa",
     "inc", "ltd", "gmbh", "france", "the", "et", "and", "cie"}
)  # fmt: skip
# Words of a job title or a contract: a tail holding one is part of the title, not an employer.
_TITLE = """data ia ai ml llm developpeur developpeuse developer dev engineer ingenieur ingenieure
    manager chef projet project consultant consultante lead senior junior confirme scientist
    analyst analyste architect architecte stagiaire intern technicien technicienne product
    devops mlops full stack fullstack backend frontend python java cloud cdi cdd stage alternance
    freelance poste responsable directeur directrice head officer specialist expert
    assistant assistante charge chargee business sales marketing it si digital software logiciel
    web mobile systeme systemes reseau reseaux securite recherche research temps plein partiel
    remote teletravail hybride urgent h f m"""
TITLE_WORDS = frozenset(_TITLE.split())
# Overseas regions and their codes (folded), for `place_key`.
REGIONS = {
    "reunion": "974", "la reunion": "974", "ile de la reunion": "974", "guadeloupe": "971",
    "martinique": "972", "guyane": "973", "guyane francaise": "973", "french guiana": "973",
    "mayotte": "976", "polynesie francaise": "987", "french polynesia": "987", "tahiti": "987",
    "nouvelle caledonie": "988", "new caledonia": "988",
}  # fmt: skip
PLACE_SPLIT = re.compile(r"[,()\[\]/|\u2013\u2014]|\s-\s")
NUMBER = re.compile(r"\b(\d{5}|\d{3}|\d{2}|2[ab])\b", re.I)


@dataclass(frozen=True)
class Cleaned:
    title: str
    company: str | None  # the company the title named when none was stated, else None
    changed: bool


def _markers(text: str) -> list[re.Match[str]]:
    found = []
    for match in MARKER.finditer(text):
        letters = [g.casefold() for g in match.groups() if g]
        kind = frozenset(letters)
        if len(kind) != len(letters):
            continue
        if any(kind <= allowed for allowed in MARKER_SETS) and any(
            pair <= kind for pair in MARKER_PAIRS
        ):
            found.append(match)
    return found


def company_core(name: str | None) -> str:
    """A company's words without its generic ones (GENERIC), all its words when none is left."""
    tokens = text_key(name or "").split()
    kept = [t for t in tokens if t not in GENERIC]
    return " ".join(kept or tokens)


def same_company(a: str | None, b: str | None) -> bool:
    core = company_core(a)
    return bool(core) and core == company_core(b)


def is_board(text: str) -> bool:
    key = re.sub(r"\s+(?:com|fr|re|net)$", "", text_key(text))
    return key in BOARDS


def is_recruiter(name: str | None) -> bool:
    """Whether a company name is a recruitment agency's: RECRUITERS, or a word of AGENCY_WORDS."""
    if not name or not text_key(name) or stated(name) is None:
        return False
    return company_core(name) in RECRUITERS or bool(set(text_key(name).split()) & AGENCY_WORDS)


def stated(company: str | None) -> str | None:
    """The company when one is stated, None for an empty one or UNKNOWN_EMPLOYER."""
    if not company or not company.strip() or text_key(company) == text_key(UNKNOWN_EMPLOYER):
        return None
    return company


def _org_like(tail: str, location: str | None) -> bool:
    """Whether a title's tail reads as an organisation's name: one to six words, the first with
    a capital or a digit, no word of a title or a contract, not the posting's place nor a place
    the gazetteer knows."""
    words = text_key(tail).split()
    if not 0 < len(words) <= 6 or len(tail) > 80:
        return False
    if not (tail[0].isupper() or tail[0].isdigit()):
        return False
    if set(words) & TITLE_WORDS:
        return False
    if set(words) <= set(text_key(location or "").split()):
        return False
    return text_key(tail) not in REGIONS and not gazetteer.places_of(tail)


def _tidy(text: str) -> str:
    text = re.sub(r"[(\[]\s*[)\]]", " ", text)
    return re.sub(r"\s+", " ", text).strip(EDGES).strip()


def clean(title: str, company: str | None, location: str | None = None) -> Cleaned:
    """The title without its gender markers and board boilerplate, and the company it names when
    none was stated (module docstring)."""
    original = title
    text = re.sub(r"\s+", " ", title[:TITLE_MAX]).strip()
    known = stated(company)
    named: str | None = None
    text = LEADING.sub("", text)
    # Trailing boards or the company after a separator, as often as they repeat.
    while True:
        parts = SEPARATOR.split(text)
        if len(parts) < 2:
            break
        head, tail = text[: text.rfind(parts[-1])], parts[-1].strip()
        if is_board(tail) or (known and same_company(tail, known)):
            text = _tidy(head)
        elif known is None and named is None and is_recruiter(tail) and _org_like(tail, location):
            named, text = tail, _tidy(head)
        else:
            break
    marks = _markers(text)
    if marks:
        last = marks[-1]
        head, tail = text[: last.start()], text[last.end() :].strip(EDGES).strip()
        if tail and (is_board(tail) or (known and same_company(tail, known))):
            tail = ""
        elif tail and known is None and named is None and _org_like(tail, location):
            named, tail = tail, ""
        for mark in reversed(marks[:-1]):
            head = head[: mark.start()] + " " + head[mark.end() :]
        text = _tidy(f"{head} {tail}")
    text = _tidy(text)
    if not re.search(r"[^\W\d_]", text):
        return Cleaned(original, None, False)
    return Cleaned(text, named, text != original)


def _code(number: str) -> str:
    """The département of a number: a postcode's first two digits, three overseas ("97400")."""
    if len(number) == 5:
        return number[:3] if number[:2] in ("97", "98") else number[:2]
    return number


def place_key(location: str | None) -> str | None:
    """The town and the département or overseas region a location names, "town|code"; None when
    it names neither."""
    parts = [p for p in PLACE_SPLIT.split(location or "") if p.strip()]
    town = ""
    code = ""
    for part in parts:
        folded = text_key(part)
        for number in NUMBER.findall(folded):
            code = code or _code(number)
        words = text_key(NUMBER.sub(" ", folded))
        if words in REGIONS:
            code = code or REGIONS[words]
        elif words and not town and words != "france" and words != "cedex":
            town = words
    if not town and not code:
        return None
    return f"{town}|{code}"


def title_key(title: str) -> str:
    """A title's words in any script, case and accents aside ("AI Engineer 東京" is not
    "مهندس AI Engineer"; text_key would keep "ai engineer" of both)."""
    folded = unicodedata.normalize("NFKD", title.casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", folded))


def cross_key(company: str | None, title: str, location: str | None) -> tuple[str, str, str] | None:
    """The key of one job across boards (module docstring), None when it cannot be told."""
    core = company_core(stated(company))
    if not core:
        return None
    key = title_key(clean(title, company, location).title)
    place = place_key(location)
    if not key or place is None:
        return None
    return core, key, place
