"""The plan of the searches "Anywhere on the web" makes before it may give up (jobs J23, #813).

The sweep of 2026-11-18 (the owner's places La Réunion, Guadeloupe and Tahiti) ran five web
searches, all "ingénieur IA" and a place, and reported no posting; Claude Code, the same day
and with the same profile, found the person's best fits with broad queries per place, roles
next to the profile and the national public job boards by `site:` query. So code builds the
plan from the saved titles and places and the profile summary (`plan_of`), shows it to the agent
in its first message (`shown`), counts what was searched from the WebSearch queries the session
recorded (`left`), and `report_no_postings` is refused until nothing is left (finder
`FinderSession.report_none`), unless WebSearch is not offered or keeps failing.

For each wanted place, the families of queries (accept and refuse table:
tests/browser/test_j23_web_breadth.py):
- `titles`: every saved title (the first TITLES_MAX), each in a query that names the place;
- `adjacent`: at least ADJACENT_NEEDED of the roles next to the profile (`adjacent_roles`, the
  ROLES table read against the summary's recent roles, skills and adjacent skills, then the
  broad FALLBACK roles of Claude Code's own query), each in a query that names the place;
- `boards`: each national public job board of the place's country (BOARDS) by a `site:` query
  that names the place; a country the table does not know has the `jobs` family instead: a
  query that names the place and a word for jobs (JOB_WORDS).
With no place saved (or only a place that says no limit), one row per search language instead
(`search_languages`: the profile's languages of LANGUAGES, else French and English): its queries
need not name a place, its roles count only by their forms in that language, and its boards are
those of the language's country (French: France's; English: none).

Matching (`_text` and `_names`): a query's words folded as the finder folds them (case and
accents aside, any other character a space), its `-word` exclusions and `site:` operators set
aside first; a title, a role or a place is named when its words are in the query as a sequence
of whole words. A place is named by itself as written, each comma separated part of it, without
a leading article, LinkedIn's name for it and that name's aliases (linkedin_places.ALIASES:
"Papeete" or "Polynésie française" for "Tahiti"), and the département number of an overseas
département ("974"). A `site:` operator names a board when its host is the board's host, one of
its subdomains, or its parent domain other than a public suffix ("site:francetravail.fr" for
candidat.francetravail.fr). Fails closed: a query read otherwise counts for nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.browser.sources import linkedin_places
from app.browser.words import words
from app.jobs import gazetteer

TITLES_MAX = 12  # saved titles a place's plan needs, the first ones (jobs J20's budget)
PLACES_MAX = 10  # places planned, the first ones
ADJACENT_MAX = 6  # roles next to the profile the plan lists
ADJACENT_NEEDED = 3  # of which a place needs at least this many searched
QUERIES_MAX = 1000  # queries counted, the first ones
LEFT_MAX = 1500  # characters of the refusal's list of what is left


@dataclass(frozen=True)
class Role:
    """A role next to the profile: its name in French and in English, the forms a query may
    name it by in each language (folded; a form of both languages in both), and the words of
    the profile that call for it (folded)."""

    fr: str
    en: str
    forms_fr: tuple[str, ...]
    forms_en: tuple[str, ...]
    triggers: tuple[str, ...]

    @property
    def forms(self) -> tuple[str, ...]:
        """Its forms in either language: a place's queries may be in French or English."""
        return tuple(dict.fromkeys((*self.forms_fr, *self.forms_en)))

    def forms_in(self, language: str | None) -> tuple[str, ...]:
        if language == "fr":
            return self.forms_fr
        return self.forms_en if language == "en" else self.forms


# The roles next to the profile, in the order the plan lists them. A role is listed when the
# summary's recent roles, skills or adjacent skills name one of its triggers as whole words.
AI_WORDS = ("llm", "llms", "genai", "ia", "ai", "intelligence artificielle",
            "artificial intelligence")  # fmt: skip
# fmt: off
ROLES: tuple[Role, ...] = (
    Role("data scientist", "data scientist",
         ("data scientist", "data science"), ("data scientist", "data science"),
         ("machine learning", "ml", "deep learning", "data science", "data scientist",
          "statistics", "statistiques", "scikit learn", "pandas", "nlp", *AI_WORDS)),
    Role("ingénieur machine learning", "machine learning engineer",
         ("ingenieur machine learning", "ingenieur ml"),
         ("machine learning engineer", "ml engineer"),
         ("machine learning", "ml", "deep learning", "pytorch", "tensorflow", "llm", "llms",
          "genai", "mlops")),
    Role("data engineer", "data engineer",
         ("data engineer", "ingenieur data", "ingenieur donnees", "ingenieur de donnees"),
         ("data engineer",),
         ("sql", "etl", "spark", "airflow", "dbt", "data engineering", "data engineer",
          "bigquery", "snowflake", "postgresql", "postgres", "data pipelines", "pipelines")),
    Role("MLOps", "MLOps", ("mlops", "ml ops"), ("mlops", "ml ops"),
         ("mlops", "docker", "kubernetes", "aws", "gcp", "azure", "terraform", "ci cd",
          "deployment", "deploiement")),
    Role("développeur Python", "Python developer",
         ("developpeur python", "developpeuse python", "ingenieur python"),
         ("python developer", "python engineer"),
         ("python", "fastapi", "django", "flask")),
    Role("développeur full stack", "full stack developer",
         ("full stack", "fullstack"), ("full stack", "fullstack"),
         ("react", "typescript", "javascript", "node", "nodejs", "vue", "angular",
          "full stack", "fullstack", "frontend", "front end")),
    Role("consultant IA", "AI consultant",
         ("consultant ia", "consultante ia", "consultant intelligence artificielle",
          "consultant data"),
         ("ai consultant", "consultant ai", "data consultant"),
         ("consulting", "consultant", "conseil", "freelance", "client facing")),
    Role("tech lead", "tech lead",
         ("tech lead", "lead developpeur", "lead developer", "lead dev"),
         ("tech lead", "technical lead", "lead developer", "lead dev"),
         ("lead", "tech lead", "architect", "architecte", "head of", "manager")),
    Role("ingénieur IA", "AI engineer",
         ("ingenieur ia", "ingenieure ia", "ingenieur intelligence artificielle"),
         ("ai engineer", "genai engineer", "llm engineer"),
         ("agents", "rag", "langchain", *AI_WORDS)),
    Role("data analyst", "data analyst",
         ("data analyst", "analyste data", "analyste de donnees"), ("data analyst",),
         ("power bi", "tableau", "analytics", "data analysis", "analyse de donnees")),
    Role("ingénieur logiciel", "software engineer",
         ("ingenieur logiciel", "developpeur logiciel"),
         ("software engineer", "software developer"),
         ("java", "c", "go", "rust", "software engineering", "backend", "back end")),
)
# The broad roles of Claude Code's own query of 2026-11-18 ("développeur OR data OR
# intelligence artificielle"), listed after the profile's own, so the broad query it found the
# person's best fits with counts as three roles.
FALLBACK: tuple[Role, ...] = (
    Role("développeur", "developer", ("developpeur", "developpeuse"), ("developer",), ()),
    Role("data", "data", ("data",), ("data",), ()),
    Role("intelligence artificielle", "artificial intelligence",
         ("intelligence artificielle",), ("artificial intelligence",), ()),
)
# fmt: on


@dataclass(frozen=True)
class Board:
    """A national public job board: its name and the hosts a `site:` query may name."""

    name: str
    hosts: tuple[str, ...]


FRANCE = (
    Board("France Travail", ("candidat.francetravail.fr",)),
    Board("APEC", ("apec.fr",)),
    Board("Hellowork", ("hellowork.com",)),
)
# The boards by country (linkedin_places' names); France's overseas places use France's.
BOARDS: dict[str, tuple[Board, ...]] = {
    "France": FRANCE,
    "Belgium": (
        Board("Le Forem", ("leforem.be",)),
        Board("VDAB", ("vdab.be",)),
        Board("Actiris", ("actiris.brussels",)),
    ),
    "Switzerland": (Board("Job-Room", ("job-room.ch",)), Board("jobs.ch", ("jobs.ch",))),
    "Luxembourg": (Board("ADEM", ("adem.public.lu",)), Board("jobs.lu", ("jobs.lu",))),
    "United Kingdom": (
        Board("Find a job", ("findajob.dwp.gov.uk",)),
        Board("Totaljobs", ("totaljobs.com",)),
    ),
    "Germany": (
        Board("Bundesagentur für Arbeit", ("arbeitsagentur.de",)),
        Board("StepStone", ("stepstone.de",)),
    ),
    "Canada": (Board("Job Bank", ("jobbank.gc.ca", "guichetemplois.gc.ca")),),
    "Spain": (Board("InfoJobs", ("infojobs.net",)),),
    "United States": (Board("USAJOBS", ("usajobs.gov",)), Board("Indeed", ("indeed.com",))),
}
# The département number of an overseas place, a name its people search with ("974").
DEPARTMENTS = {
    "Guadeloupe": "971",
    "Martinique": "972",
    "French Guiana": "973",
    "Reunion": "974",
    "Mayotte": "976",
}
# Words for jobs in a query of the `jobs` family (a country without a board of the table).
JOB_WORDS = frozenset(
    {"job", "jobs", "emploi", "emplois", "offre", "offres", "career", "careers", "carriere",
     "carrieres", "vacancy", "vacancies", "recrutement", "hiring", "stellen", "stellenangebote",
     "empleo", "empleos", "lavoro", "vagas", "vacatures", "cdi", "cdd"}
)  # fmt: skip
ARTICLES = ("la ", "le ", "les ", "l ", "the ")
# Second level labels under which a registrable name sits (a `site:` of one alone names
# nothing): "gov.uk", "gc.ca", "public.lu".
SUFFIXES = frozenset(
    {"gov.uk", "co.uk", "gc.ca", "public.lu", "com", "fr", "de", "be", "ch", "lu", "uk", "ca",
     "es", "net", "org", "gov", "brussels", "pf", "nc"}
)  # fmt: skip
SITE = re.compile(r"(?<![\w-])site:\s*(\S+)", re.IGNORECASE)
EXCLUDED = re.compile(r"(?:(?<=\s)|^)-\S+")


# With no place wanted, the search languages: the profile's languages this table names (folded),
# in its order, else French and English; each with the country whose boards it searches.
LANGUAGES = {
    "french": "fr", "francais": "fr", "fr": "fr",
    "english": "en", "anglais": "en", "en": "en",
}  # fmt: skip
LANGUAGE_COUNTRY = {"fr": "France", "en": None}
LANGUAGE_NAMES = {"fr": "French", "en": "English"}


@dataclass(frozen=True)
class Row:
    """The plan of one place, or with no place wanted of one search language."""

    place: str | None
    names: tuple[str, ...]  # folded forms a query names the place by
    titles: tuple[str, ...]
    roles: tuple[Role, ...]
    boards: tuple[Board, ...]
    country: str | None
    french: bool
    language: str | None = None  # "fr" or "en" for a row with no place: its roles' forms


def _forms(text: str) -> set[str]:
    folded = words(text)
    out = {folded} if folded else set()
    for article in ARTICLES:
        if folded.startswith(article) and len(folded) > len(article):
            out.add(folded[len(article) :])
    return out


def country_of(place: str) -> str | None:
    """The country of a saved place, France for an overseas one: LinkedIn's name for it and the
    last part read by linkedin_places, else the gazetteer's city or country it names; None when
    neither knows it (never guessed)."""
    name = linkedin_places.linkedin_name(place)
    found = linkedin_places.country_of(name) or linkedin_places.country_of(place)
    if found is None:
        for known in gazetteer.places_of(place[: linkedin_places.PLACE_MAX]):
            found = linkedin_places.CITY_HOME.get(known.name) or linkedin_places.country_of(
                known.name
            )
            if found:
                break
    if found in linkedin_places.OVERSEAS:
        return "France"
    return found


def place_names(place: str) -> tuple[str, ...]:
    """The folded forms a query names a saved place by (module docstring)."""
    names = _forms(place)
    for part in place.split(","):
        names |= _forms(part)
    name = linkedin_places.linkedin_name(place)
    for alias in (name, *linkedin_places.ALIASES.get(name, ())):
        names |= _forms(alias)
    if name in DEPARTMENTS:
        names.add(DEPARTMENTS[name])
    return tuple(sorted(n for n in names if n))


def _profile_text(profile: dict[str, Any]) -> str:
    parts: list[str] = []
    for role in profile.get("recent_roles") or []:
        if isinstance(role, str):
            parts.append(role)
    for key in ("skills", "adjacent_skills"):
        for category in profile.get(key) or []:
            if not isinstance(category, dict):
                continue
            parts.append(str(category.get("name") or ""))
            parts.extend(str(t) for t in category.get("terms") or [] if isinstance(t, str))
    return " | ".join(words(part) for part in parts if part)


def _has(text: str, phrase: str) -> bool:
    """Whether `phrase` (folded) is in `text` (folded words, ` | ` between parts) as whole
    words."""
    return bool(phrase) and f" {phrase} " in f" {text.replace(' | ', '   ')} "


def adjacent_roles(
    profile: dict[str, Any], titles: tuple[str, ...], excluded: tuple[str, ...]
) -> tuple[Role, ...]:
    """The roles next to the profile: those of ROLES it calls for, in order, at most
    ADJACENT_MAX, then FALLBACK; none a saved title holds or is held by, none holding an
    unwanted title term."""
    text = _profile_text(profile)
    saved = [words(t) for t in titles]
    unwanted = [words(t) for t in excluded if words(t)]

    def fits(role: Role) -> bool:
        if any(_has(title, form) or _has(form, title) for form in role.forms for title in saved):
            return False
        return not any(_has(form, term) for form in role.forms for term in unwanted)

    chosen = [r for r in ROLES if any(_has(text, t) for t in r.triggers) and fits(r)]
    return (*chosen[:ADJACENT_MAX], *(r for r in FALLBACK if fits(r)))


def search_languages(profile: dict[str, Any]) -> tuple[str, ...]:
    """The search languages of a plan with no place: the profile's languages LANGUAGES names, in
    the profile's order, else French and English (never guessed from a name it does not know)."""
    found: list[str] = []
    for language in profile.get("languages") or []:
        name = language.get("name") if isinstance(language, dict) else language
        code = LANGUAGES.get(words(name)) if isinstance(name, str) else None
        if code and code not in found:
            found.append(code)
    return tuple(found) or ("fr", "en")


def _excluded(criteria: str) -> tuple[str, ...]:
    try:
        data = json.loads(criteria)
    except ValueError:
        return ()
    terms = (data.get("title_exclude") or {}).get("terms") if isinstance(data, dict) else None
    return tuple(t for t in terms or [] if isinstance(t, str))


def plan_of(
    titles: tuple[str, ...], places: tuple[str, ...], criteria: str, profile: str
) -> tuple[Row, ...]:
    """The plan of a web search scope (module docstring)."""
    try:
        summary = json.loads(profile)
    except ValueError:
        summary = {}
    if not isinstance(summary, dict):
        summary = {}
    roles = adjacent_roles(summary, titles, _excluded(criteria))
    wanted = [p for p in places if p.strip() and not _worldwide(p)][:PLACES_MAX]
    if not wanted:
        return tuple(
            Row(
                None,
                (),
                titles[:TITLES_MAX],
                roles,
                BOARDS.get(LANGUAGE_COUNTRY[code] or "", ()),
                LANGUAGE_COUNTRY[code],
                code == "fr",
                code,
            )
            for code in search_languages(summary)
        )
    rows: list[Row] = []
    for place in wanted:
        country = country_of(place)
        rows.append(
            Row(
                place,
                place_names(place),
                titles[:TITLES_MAX],
                roles,
                BOARDS.get(country or "", ()),
                country,
                country in (None, "France", "Belgium", "Switzerland", "Luxembourg", "Canada"),
            )
        )
    return tuple(rows)


def _worldwide(place: str) -> bool:
    from app.browser.finder import worldwide

    return worldwide(place)


def _text(query: str) -> str:
    """A query's words, folded, its exclusions and `site:` operators aside."""
    return words(SITE.sub(" ", EXCLUDED.sub(" ", query)))


def _sites(query: str) -> set[str]:
    hosts = set()
    for raw in SITE.findall(EXCLUDED.sub(" ", query)):
        host = re.sub(r"^[a-z]+://", "", raw.strip("\"'()").casefold()).split("/")[0]
        host = host.removeprefix("www.").rstrip(".")
        if "." in host and host not in SUFFIXES:
            hosts.add(host)
    return hosts


def _board_named(board: Board, sites: set[str]) -> bool:
    return any(
        site == host or site.endswith("." + host) or host.endswith("." + site)
        for site in sites
        for host in board.hosts
    )


def _names(row: Row, text: str) -> bool:
    return row.place is None or any(_has(text, name) for name in row.names)


def coverage(row: Row, queries: list[str]) -> dict[str, Any]:
    """What the queries searched of one row: the titles, roles and boards named in a query
    that names the place, and whether the `jobs` family was."""
    titles: set[str] = set()
    roles: set[str] = set()
    boards: set[str] = set()
    jobs = False
    for query in queries[:QUERIES_MAX]:
        text = _text(query)
        if not _names(row, text):
            continue
        titles |= {t for t in row.titles if _has(text, words(t))}
        roles |= {r.en for r in row.roles if any(_has(text, f) for f in r.forms_in(row.language))}
        sites = _sites(query)
        boards |= {b.name for b in row.boards if _board_named(b, sites)}
        jobs = jobs or any(word in JOB_WORDS for word in text.split())
    return {"titles": titles, "roles": roles, "boards": boards, "jobs": jobs}


def _role_name(role: Role, french: bool) -> str:
    return role.fr if french else role.en


def _where(row: Row) -> str:
    if row.place is not None:
        return f"for {row.place}"
    return f"with no place, in {LANGUAGE_NAMES.get(row.language or '', 'any language')}"


def left(rows: tuple[Row, ...], queries: list[str]) -> list[str]:
    """What is left to search, one sentence a row, empty once every family is covered."""
    out: list[str] = []
    for row in rows:
        done = coverage(row, queries)
        parts: list[str] = []
        titles = [t for t in row.titles if t not in done["titles"]]
        if titles:
            parts.append("the saved titles " + ", ".join(f'"{t}"' for t in titles))
        needed = min(ADJACENT_NEEDED, len(row.roles))
        missing = needed - len(done["roles"])
        if missing > 0:
            others = [_role_name(r, row.french) for r in row.roles if r.en not in done["roles"]]
            parts.append(
                f"{missing} more role{'s' if missing > 1 else ''} next to the profile (of: "
                + ", ".join(others)
                + ")"
            )
        boards = [b for b in row.boards if b.name not in done["boards"]]
        if boards:
            parts.append(
                "the national job boards by site: query ("
                + ", ".join(f"site:{b.hosts[0]}" for b in boards)
                + ")"
            )
        if row.place is not None and not row.boards and not done["jobs"]:
            parts.append(f'a jobs query of the place ("jobs {row.place}")')
        if parts:
            out.append(f"{_where(row)}: " + "; ".join(parts))
    return out


def left_text(rows: tuple[Row, ...], queries: list[str]) -> str:
    """The refusal's list of what is left, bounded (LEFT_MAX)."""
    text = ". ".join(left(rows, queries))
    return text if len(text) <= LEFT_MAX else text[: LEFT_MAX - 5].rsplit(" ", 1)[0] + " ..."


def shown(rows: tuple[Row, ...]) -> dict[str, Any]:
    """The plan as the agent's first message shows it."""
    return {
        "rule": (
            "report_no_postings is refused until, for each place, your WebSearch queries "
            "naming the place searched every title, at least "
            f"{ADJACENT_NEEDED} of the adjacent roles, and every board by site: query; with "
            "no place saved, the same for each search language, the roles named in it"
        ),
        "places": [
            {
                "place": row.place or "no place saved: any",
                **(
                    {"language": LANGUAGE_NAMES.get(row.language, row.language)}
                    if row.language
                    else {}
                ),
                **({"also_named": list(row.names)} if row.names else {}),
                "titles": list(row.titles),
                "adjacent_roles": [_role_name(r, row.french) for r in row.roles],
                **(
                    {"boards": [f"site:{b.hosts[0]}" for b in row.boards]}
                    if row.boards
                    else {"jobs_query": f"jobs {row.place}"}
                    if row.place
                    else {}
                ),
            }
            for row in rows
        ],
    }
