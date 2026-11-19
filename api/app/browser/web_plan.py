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
  query that names the place and a word for jobs (JOB_WORDS);
- `town` (jobs J25, #817): a query naming the place's main town (the first town of TOWNS:
  Saint-Denis for La Réunion, Pointe-à-Pitre, Papeete); a place without a town table has none;
- `ai_boards` (J25, jobs J26 #819): a `site:` query of at least one niche AI and tech job board
  (NICHE; several joined by OR in one query count too) that names the place or its main town
  (the main town by the rules of a place's names below: "Saint-Denis La Réunion", "Pointe-à-
  Pitre"); a niche board that is also a national board of the row (Hellowork) only in a query
  naming the main town (the place itself for a place without towns); two `site:` operators
  with no OR are ANDed and count for nothing.
The owner's sweep of 2026-11-19 (81ab9533) ran six broad OR queries, which covered the first
three families, and missed the AI and data CDIs of Saint-Denis Claude Code had found the day
before on a niche board. So each of `town` and `ai_boards` needs a query of its own per place:
one that names no other row's place (an OR inside it still counts), and the two families are
not served by the same single query. J25 also counted an offer phrase ("offre emploi") with a
role and the town as `ai_boards`; the sweep f769661a (2026-11-19) met the family so with
"offre emploi genai engineer Saint-Denis La Réunion", and the search engine returned none of
the niche board's pages, where four Saint-Denis AI and data CDIs were online (J26). So only a
`site:` query of a niche board counts now.
With no place saved (or only a place that says no limit), one row per search language instead
(`search_languages`: the profile's languages of LANGUAGES, else French and English): its queries
need not name a place, its roles count only by their forms in that language, and its boards are
those of the language's country (French: France's; English: none).

Matching (`_text` and `_named_by`): a query's words folded as the finder folds them (case and
accents aside, any other character a space), its `-word` exclusions and `site:` operators set
aside first; a title, a role or a place is named when its words are in the query as a sequence
of whole words. Titles and roles are matched longest form first, and the words a form matched
are used up (`_consume`): "data scientist" is one role, never also "data"; "data OR data
scientist" is two. A place is named by itself as written, each comma separated part of it,
without a leading article, LinkedIn's name for it and that name's aliases
(linkedin_places.ALIASES: "Papeete" or "Polynésie française" for "Tahiti"), the département
number of an overseas département ("974"), and its main towns (TOWNS: "Pointe-à-Pitre" for
Guadeloupe). A town another place has too (Saint-Denis, also in Seine-Saint-Denis, the larger
city a search engine reads) names nothing alone (round 2 of #814, an allow list): only right
next to a postcode of its place (POSTCODES: "Saint-Denis 97400", "97410 Saint-Pierre"); else
the query names the place by one of its own names ("Saint-Denis La Réunion", "Saint-Denis 974")
or not at all ("ingénieur IA Saint-Denis" is no search of La Réunion). A `site:` operator
names a board when its host is the board's host, one of its subdomains, or its parent domain
other than a public suffix ("site:francetravail.fr" for candidat.francetravail.fr).

How a search engine reads a query (round 1 of #814): words side by side are ANDed, so a query
with no OR operator (` OR ` in capitals, or `|`) searches one thing of each family at most. It
counts for its longest title and its longest role only, for no board when it holds two `site:`
operators or more, and for no place when it names the places of two rows of the plan or more (a
place named by a name another row has too is no other place: "Tahiti" and "Papeete" saved).
With an OR, every title, role, board and place it names counts. Planned are only the titles
whose words fold to something and the places a query can name (`plan_of`): "++" or "!!!" is
left out, never a plan nobody can search; titles that fold to the same words ("ingénieur IA"
and "Ingenieur IA", "full-stack" and "full stack") are one title, the first (round 2). Fails
closed: a query read otherwise counts for nothing. `covering_query` is the one query with OR
that covers the families a query may search at once, and `covering_queries` adds each place's
town and AI boards queries (the fake backend's web stream searches them all).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from app.browser.sources import linkedin_places
from app.browser.words import words
from app.jobs import gazetteer

TITLES_MAX = 12  # saved titles a place's plan needs, the first ones (jobs J20's budget)
PLACES_MAX = 10  # places planned, the first ones
ADJACENT_MAX = 6  # roles next to the profile the plan lists
ADJACENT_NEEDED = 3  # of which a place needs at least this many searched
QUERIES_MAX = 1000  # queries counted, the first ones
LEFT_MAX = 2500  # characters of the refusal's list of what is left


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
OR = re.compile(r"(?<!\S)OR(?!\S)|\|")  # a search engine's OR operator, in capitals
# The main towns of an overseas place, by LinkedIn's name (accept and refuse table:
# tests/browser/test_j23_web_breadth.py): (the town, whether another place has a town of that
# name: Saint-Denis of Seine-Saint-Denis, Saint-Pierre of Martinique or of Saint-Pierre-et-
# Miquelon, Saint-Paul and Le Port of metropolitan France). A shared town names the place only
# right next to a postcode of the place (`_shared_named`).
# The first town of a place is its main town, the one its `town` family must name (J25).
TOWNS: dict[str, tuple[tuple[str, bool], ...]] = {
    "Reunion": (("Saint-Denis", True), ("Saint-Pierre", True), ("Le Port", True),
                ("Saint-Paul", True)),
    "Guadeloupe": (("Pointe-à-Pitre", False), ("Les Abymes", False), ("Baie-Mahault", False),
                   ("Basse-Terre", False)),
    "French Polynesia": (("Papeete", False), ("Faa'a", False), ("Punaauia", False)),
}  # fmt: skip
# The postcode prefix of an overseas place: a five digit number of a query that starts with it
# is one of its postcodes ("97400").
POSTCODES = {**DEPARTMENTS, "French Polynesia": "987"}
POSTCODE = re.compile(r"\d{5}")
# Jobs J25 and J26: the AI and tech job boards an `ai_boards` query names by `site:` (the only
# form that counts, J26). Public job boards only. The Intelligence Academy's /job-board pages
# listed the owner's best Réunion matches on 2026-11-18. A board that is also a national board
# of the row (Hellowork) counts here only in a query naming the main town (the place itself for
# a place without towns): its `boards` query of the place alone asks nothing new of the AI
# boards.
NICHE: tuple[Board, ...] = (
    Board("The Intelligence Academy", ("the-intelligence-academy.com",)),
    Board("Hellowork", ("hellowork.com",)),
    Board("Welcome to the Jungle", ("welcometothejungle.com",)),
    Board("Free-Work", ("free-work.com",)),
    Board("LesJeudis", ("lesjeudis.com",)),
    Board("Choose Your Boss", ("chooseyourboss.com",)),
    Board("AI Jobs", ("aijobs.net", "ai-jobs.net")),
)


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
    shared: tuple[str, ...] = ()  # folded towns that name the place next to its postcode
    linkedin: str = ""  # LinkedIn's name for the place (linkedin_places.linkedin_name)
    town: str = ""  # its main town as TOWNS writes it (J25), "" for a place without towns


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
    names |= {words(town) for town, shared in TOWNS.get(name, ()) if not shared}
    return tuple(sorted(n for n in names if n))


def main_town(place: str) -> str:
    """The main town of a saved place (J25: the first of TOWNS; a town of TOWNS the place
    names itself, "Saint-Pierre, La Réunion"), "" when TOWNS has none for it."""
    name = linkedin_places.linkedin_name(place)
    if name not in TOWNS:
        name = linkedin_places.linkedin_name(place.split(",")[-1].strip())
    towns = TOWNS.get(name, ())
    folded = words(place)
    own = [town for town, _ in towns if _has(folded, words(town))]
    return own[0] if own else towns[0][0] if towns else ""


def shared_towns(place: str) -> tuple[str, ...]:
    """The folded towns of TOWNS another place has too, for a saved place."""
    name = linkedin_places.linkedin_name(place)
    return tuple(words(town) for town, shared in TOWNS.get(name, ()) if shared)


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
    # Only what a query can name (round 1 of #814): a title of no words, a place of no name;
    # one title of those that fold to the same words, the first (round 2: a query uses its
    # words up for one title only, `_consume`).
    kept: dict[str, str] = {}
    for title in titles:
        if words(title):
            kept.setdefault(words(title), title)
    titles = tuple(kept.values())[:TITLES_MAX]
    wanted = [p for p in places if p.strip() and not _worldwide(p) and place_names(p)]
    wanted = wanted[:PLACES_MAX]
    if not wanted:
        return tuple(
            Row(
                None,
                (),
                titles,
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
                titles,
                roles,
                BOARDS.get(country or "", ()),
                country,
                country in (None, "France", "Belgium", "Switzerland", "Luxembourg", "Canada"),
                shared=shared_towns(place),
                linkedin=linkedin_places.linkedin_name(place),
                town=main_town(place),
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


def _shared_named(row: Row, text: str) -> set[str]:
    """The row's shared towns a query's text names right next to a postcode of its place
    (module docstring): "saint denis 97400", "97410 saint pierre"."""
    own = POSTCODES.get(row.linkedin)
    if own is None:
        return set()
    tokens = text.split()
    found: set[str] = set()
    for town in row.shared:
        parts = town.split()
        size = len(parts)
        for i in range(len(tokens) - size + 1):
            if tokens[i : i + size] != parts:
                continue
            near = (*tokens[max(i - 1, 0) : i], *tokens[i + size : i + size + 1])
            if any(POSTCODE.fullmatch(n) and n.startswith(own) for n in near):
                found.add(town)
    return found


def _named_by(row: Row, text: str) -> set[str]:
    """The names of the row's place a query's text names (module docstring)."""
    return {name for name in row.names if _has(text, name)} | _shared_named(row, text)


def _consume(text: str, forms: list[tuple[str, str]]) -> list[str]:
    """The items `forms` (folded form, item) names in `text`, longest form first, each word of
    the text used by one form at most (every occurrence of a form found is used up): the
    items in that order, each once."""
    tokens = text.split()
    used = [False] * len(tokens)
    found: list[str] = []
    for form, item in sorted(forms, key=lambda f: -len(f[0].split())):
        parts = form.split()
        size = len(parts)
        if not size:
            continue
        for i in range(len(tokens) - size + 1):
            if tokens[i : i + size] == parts and not any(used[i : i + size]):
                used[i : i + size] = [True] * size
                if item not in found:
                    found.append(item)
    return found


def coverage(row: Row, queries: list[str], rows: tuple[Row, ...] = ()) -> dict[str, Any]:
    """What the queries searched of one row: the titles, roles and boards named in a query
    that names the place, and whether the `jobs` family was; a query without OR counts for one
    of each at most, and for none when it names the place of another of `rows` too (module
    docstring)."""
    titles: set[str] = set()
    roles: set[str] = set()
    boards: set[str] = set()
    jobs = False
    town_queries: set[str] = set()
    ai_queries: set[str] = set()
    for query in queries[:QUERIES_MAX]:
        text = _text(query)
        ored = bool(OR.search(query))
        alone = True  # names no other row's place (J25: a query of its own)
        if row.place is not None:
            named = _named_by(row, text)
            if not named:
                continue
            alone = not any(
                other.place is not None and other is not row and _named_by(other, text) - named
                for other in rows
            )
            if not ored and not alone:
                continue  # ANDed places: narrower, not wider
        found_titles = _consume(text, [(words(t), t) for t in row.titles])
        # Every role of the tables uses up its words, planned or not: "data engineer" is one
        # role, never the planned "data".
        planned = {r.en for r in row.roles}
        found_roles = [
            r
            for r in _consume(
                text, [(f, r.en) for r in (*ROLES, *FALLBACK) for f in r.forms_in(row.language)]
            )
            if r in planned
        ]
        sites = _sites(query)
        found_boards = [b.name for b in row.boards if _board_named(b, sites)]
        if row.place is not None and alone:
            town = _has(text, words(row.town)) if row.town else False
            if town:
                town_queries.add(query.strip())
            # J26: a niche board by `site:` only; ANDed sites name no page.
            national = {host for b in row.boards for host in b.hosts}
            anded = not ored and len(SITE.findall(EXCLUDED.sub(" ", query))) > 1
            niche = not anded and any(
                _board_named(b, sites) and (town or not row.town or not national & set(b.hosts))
                for b in NICHE
            )
            if niche:
                ai_queries.add(query.strip())
        if not ored:
            found_titles, found_roles = found_titles[:1], found_roles[:1]
            if len(SITE.findall(EXCLUDED.sub(" ", query))) > 1:
                found_boards = []  # ANDed sites: no page is on two hosts
        titles |= set(found_titles)
        roles |= set(found_roles)
        boards |= set(found_boards)
        jobs = jobs or any(word in JOB_WORDS for word in text.split())
    return {
        "titles": titles,
        "roles": roles,
        "boards": boards,
        "jobs": jobs,
        "town_queries": town_queries,
        "ai_queries": ai_queries,
    }


def own_families(row: Row, done: dict[str, Any]) -> tuple[bool, bool]:
    """Whether the `town` and the `ai_boards` families of a row are searched (J25): each by a
    query of its own, never both by one single query; a row with no place needs neither, a
    place without towns no `town` query."""
    if row.place is None:
        return True, True
    town_q, ai_q = done["town_queries"], done["ai_queries"]
    if not row.town:
        return True, bool(ai_q)
    both = bool(town_q) and bool(ai_q)
    if both and not any(a != t for t in town_q for a in ai_q):
        return True, False  # one query for both: the AI boards one is still to make
    return bool(town_q), bool(ai_q)


def _first_term(row: Row) -> str:
    if row.titles:
        return row.titles[0]
    return _role_name(row.roles[0], row.french) if row.roles else "emploi"


def _town_place(row: Row) -> str:
    """The main town and the place, as a query names them ("Saint-Denis La Réunion"), the place
    alone when it names its town already ("Saint-Denis, La Réunion")."""
    place = row.place or ""
    if not row.town or _has(words(place), words(row.town)):
        return place
    return f"{row.town} {place}"


def town_query(row: Row) -> str:
    """A `town` query of the row (J25), "" when it needs none."""
    return f"{_first_term(row)} {_town_place(row)}" if row.place and row.town else ""


# The niche boards of the example `ai_boards` query: the board that listed the owner's
# Saint-Denis matches, and Hellowork (J26).
AI_EXAMPLE = NICHE[:2]


def ai_boards_query(row: Row) -> str:
    """An `ai_boards` query of the row (J26: "site:the-intelligence-academy.com OR
    site:hellowork.com data Saint-Denis La Réunion"), "" for a row with no place. A niche board
    lists only its field's jobs, so the broad role "data" when planned, else the first term."""
    if not row.place:
        return ""
    broad = next((r.en for r in row.roles if r.en == "data"), "")
    sites = " OR ".join(f"site:{b.hosts[0]}" for b in AI_EXAMPLE)
    return f"{sites} {broad or _first_term(row)} {_town_place(row)}"


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
        done = coverage(row, queries, rows)
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
                "the national job boards, one site: query each or joined by OR ("
                + " OR ".join(f"site:{b.hosts[0]}" for b in boards)
                + ")"
            )
        if row.place is not None and not row.boards and not done["jobs"]:
            parts.append(f'a jobs query of the place ("jobs {row.place}")')
        town, ai = own_families(row, done)
        if not town:
            parts.append(
                f'a query of its own naming the main town {row.town} ("{town_query(row)}")'
            )
        if not ai:
            parts.append(
                "a query of its own with site: of one or more AI and tech job boards ("
                + " OR ".join(f"site:{b.hosts[0]}" for b in NICHE)
                + "), naming the place"
                + (" or its main town" if row.town else "")
                + f' ("{ai_boards_query(row)}")'
            )
        if parts:
            out.append(f"{_where(row)}: " + "; ".join(parts))
    return out


def left_text(rows: tuple[Row, ...], queries: list[str]) -> str:
    """The refusal's list of what is left, bounded (LEFT_MAX)."""
    text = ". ".join(left(rows, queries))
    return text if len(text) <= LEFT_MAX else text[: LEFT_MAX - 5].rsplit(" ", 1)[0] + " ..."


def covering_queries(rows: tuple[Row, ...]) -> list[str]:
    """The queries that search the whole plan `rows`: `covering_query`, then for each place its
    `town` query and its `ai_boards` query (J25: each family needs a query of its own per
    place). The fake backend's web stream searches them all (`{web_plan_query}`, then each of
    `{web_plan_queries}`)."""
    first = covering_query(rows)
    rest = [q for row in rows for q in (town_query(row), ai_boards_query(row)) if q]
    return [first, *rest] if first else []


def covering_query(rows: tuple[Row, ...]) -> str:
    """One query with OR that searches the whole plan `rows` (module docstring): every title,
    the first ADJACENT_NEEDED roles of each row by a form of the row's language, each board's
    `site:`, each place as written and a word for jobs; "" for no plan. The fake backend's web
    stream searches it (`{web_plan_query}`), so a fake sweep searches its plan whatever the
    journey's criteria (round 2 of #814)."""
    terms: list[str] = []
    for row in rows:
        terms.extend(row.titles)
        terms.extend(r.forms_in(row.language)[0] for r in row.roles[:ADJACENT_NEEDED])
        terms.extend(f"site:{b.hosts[0]}" for b in row.boards)
        if row.place is not None:
            terms.append(row.place)
    if terms:
        terms.append("jobs")
    return " OR ".join(dict.fromkeys(terms))


def shown(rows: tuple[Row, ...]) -> dict[str, Any]:
    """The plan as the agent's first message shows it."""
    return {
        "rule": (
            "report_no_postings is refused until, for each place, your WebSearch queries "
            "naming the place searched every title, at least "
            f"{ADJACENT_NEEDED} of the adjacent roles, and every board by site: query; with "
            "no place saved, the same for each search language, the roles named in it. Words "
            "side by side are ANDed: a query without OR counts for one title, one role, one "
            "board and one place at most. For each place, also a query of its own (naming "
            "no other place) naming its main_town, and another with site: of one or more of "
            "its ai_boards (joined by OR) naming the place or its main town, as its "
            'ai_boards_query; a search without site: ("offre emploi ...") is not one'
        ),
        # Jobs J24 (#815): what a row's searches report, beside what they must search.
        "report": (
            "every open posting of a place whose role is one of its titles or adjacent roles, "
            "or fits the profile's skills, whatever its title wording, its seniority or the "
            "years of experience it asks: the app scores its fit and gaps"
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
                **({"main_town": row.town} if row.town else {}),
                **(
                    {
                        "ai_boards": [f"site:{b.hosts[0]}" for b in NICHE],
                        "ai_boards_query": ai_boards_query(row),
                    }
                    if row.place
                    else {}
                ),
            }
            for row in rows
        ],
    }


# Jobs J28 (#823): the niche board results of the web source's searches. The sweep b9ca8a4b
# (2026-11-19) searched its whole plan, `site:the-intelligence-academy.com` queries naming La
# Réunion included, and never opened one of that board's pages, where three Saint-Denis CDIs
# were online: the agent judged them from the results list. So code collects from each WebSearch
# answer the results whose host is a NICHE board and whose title or address names a wanted place
# (`niche_link`, accept and refuse table: tests/browser/test_j28_open_niche_results.py), at most
# NICHE_LINKS_MAX a run in answer order, and the run may not finish before the agent opened,
# reported or saw refused each of them (finder `FinderSession.niche_left`). Code fetches none.
# Round 1 of #824: the real results of a La Réunion query named no place ("Programme IA sur
# mesureC'est gratuit →" at /job-board/offre/data-engineer-senior-ia-agentique-cdi-<co>-10508),
# so a result whose address has its board's job posting shape (POSTING) counts too when the
# query that returned it named a wanted place. A board this table does not name, or another
# page of a named one (a listing, a course), counts only by its own title or address.
NICHE_LINKS_MAX = 15  # results a run must open, the first ones
LINKS_READ_MAX = 100  # results read of one WebSearch answer, the first ones
LINK_TITLE_MAX = 300  # characters of a result's title read and shown
LINK_URL_MAX = 2000  # a longer address is no result (finder's report bound)
# The path of one job posting per NICHE board, as the board's pages wrote it on 2026-11-19:
# The Intelligence Academy `/job-board/offre/<slug>`, Hellowork `/fr-fr/emplois/<id>.html`.
POSTING = {
    "The Intelligence Academy": re.compile(r"/job-board/offre/[^/]+/?"),
    "Hellowork": re.compile(r"/[a-z]{2}-[a-z]{2}/emplois/\d+\.html"),
}


def niche_board(url: str) -> Board | None:
    """The NICHE board an address is on: its host is the board's host or one of its
    subdomains (never a parent domain, nor a host that merely ends with the same letters), on
    http or https; None otherwise."""
    if len(url) > LINK_URL_MAX:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").rstrip(".")
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    for board in NICHE:
        if any(host == h or host.endswith("." + h) for h in board.hosts):
            return board
    return None


def _link_text(title: str, url: str) -> str:
    """A result's words as a place is looked for in them: its title, then its address's path
    and query decoded (`/offres/data-scientist-la-reunion`), the host aside (a board's own name
    is no place)."""
    parts = urlsplit(url.strip())
    address = unquote(f"{parts.path} {parts.query}")
    return words(f"{title[:LINK_TITLE_MAX]} {address}")


def posting(url: str) -> bool:
    """Whether a NICHE board address has its board's job posting shape (POSTING), the path
    whole; False for a board POSTING does not name."""
    board = niche_board(url)
    shape = POSTING.get(board.name) if board is not None else None
    return shape is not None and shape.fullmatch(urlsplit(url.strip()).path) is not None


def niche_link(rows: tuple[Row, ...], title: str, url: str, query: str | None = None) -> bool:
    """Whether a WebSearch result must be opened (J28): it is on a NICHE board and its title or
    address names the place of a row of the plan, by the rules a query names a place by
    (`_named_by`: the place, its other names and département number, its towns no other place
    has, a shared town only right next to a postcode of the place); or (round 1 of #824) its
    address is a job posting of its board (`posting`) and `query`, the WebSearch query that
    returned it, names such a place by the same rules. A plan with no place wants no place, so
    no result is needed."""
    if not isinstance(title, str) or not isinstance(url, str) or niche_board(url) is None:
        return False
    texts = [_link_text(title, url)]
    if isinstance(query, str) and posting(url):
        texts.append(_text(query))
    return any(row.place is not None and _named_by(row, t) for row in rows for t in texts)
