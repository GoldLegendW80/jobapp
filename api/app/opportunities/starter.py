"""The starter list of boards and sources (opportunities O5b, issue #482): a brand new account
has no boards, so its first sweep would find nothing. From what the person gave in the wizard
(roles, industries, places, company types, contract types), this module proposes public company
boards (Ashby, Greenhouse, Lever) and browser sources (browser B8: Google Careers and Business
France VIE, searched for the person's own role words). Nothing is added until the person chooses.

The catalog is `starter_catalog.json` next to this module: versioned with the code, public
company ATS identifiers only, each board asked once through the profile's board check before it
ships (`starter_check.py`). When every ATS call of the stack goes to a test stub (ATS_BASE_URL
set with no ATS_BASE_URL_ORG_PREFIX, refused in production), the catalog is `starter_stub.json`
instead: invented companies on the fake ATS server's orgs, so a journey never proposes a real
board.

The rules, each tested in tests/opportunities/test_o5b_starter.py:
- a board or source whose company or industries match one of the person's never apply rules,
  or the app's own rule against cybersecurity (CLAUDE.md rule 2), is never proposed, and the
  catalog refuses to load one that names cybersecurity at all;
- LinkedIn and careers page sites are never proposed (B10's opt in is the person's own act; a
  careers page is the person's own address);
- each target the person gave filters: the role words name role families (an allow list per
  family), the industries name catalog industries, the places name countries ("Anywhere" and
  "Remote" do not filter). A role or industry list that names nothing the catalog knows proposes
  nothing, and says which words were not recognised;
- no target at all proposes the small general list (`general` in the catalog);
- a board or source already in the search section is shown as added; one the person removed
  from the list, or added and later deleted, is never proposed again (`starter_handled` in the
  profile's onboarding part, written only by `choose`);
- the list is ranked: industries matched, then role families, then countries, then company
  type, then the company's name.
"""

import functools
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy.orm import Session

from app.apply.guardrails import fold, matching_rule
from app.identity.models import User
from app.jobs.sweep import NeverApplyRule, never_apply_rules
from app.profile import service as profile_service
from app.profile.models import Profile
from app.profile.schemas import ORG_PATTERN, STARTER_KEY_PATTERN, SearchSection

CATALOG_PATH = Path(__file__).with_name("starter_catalog.json")
STUB_CATALOG_PATH = Path(__file__).with_name("starter_stub.json")
ENTRIES_MAX = 15  # proposed at once, the added ones included
GENERAL_MAX = 8  # the list for a person who gave no target

# The app's own rule, whatever the person's list says (CLAUDE.md rule 2).
CYBER_RULE = NeverApplyRule.parse(
    "Never cybersecurity: cyber, cybersecurity, security, pentest, pentesting, threat"
)

RoleFamily = Literal[
    "engineering", "ai", "data", "product", "design", "sales", "marketing", "operations",
    "people", "finance",
]  # fmt: skip
Industry = Literal[
    "ai", "software", "developer_tools", "data", "fintech", "health", "legal", "consumer",
    "marketplace", "media", "mobility", "education",
]  # fmt: skip
Country = Literal[
    "FR", "GB", "US", "DE", "NL", "CH", "ES", "IE", "CA", "SG", "BE", "LU", "PT", "IT", "SE",
    "PL", "JP", "IN", "AU",
]  # fmt: skip
ROLE_FAMILIES: tuple[str, ...] = RoleFamily.__args__  # type: ignore[attr-defined]

# --- the words a person types, read as the catalog's vocabulary ---------------------------------
# Folded as apply's never apply matcher folds (accents, case, hyphens: "Développeur" is
# "developpeur", "e-commerce" is "e commerce"), matched as whole words with an optional plural.

ROLE_WORDS: dict[str, tuple[str, ...]] = {
    "engineering": (
        "engineer", "engineering", "developer", "developpeur", "developpeuse", "dev",
        "ingenieur", "ingenieure", "software", "backend", "back end", "frontend", "front end",
        "full stack", "fullstack", "devops", "sre", "programmer", "architect", "cto",
        "tech lead", "mobile", "ios", "android",
    ),
    "ai": (
        "ai", "ml", "machine learning", "llm", "genai", "generative ai", "applied ai",
        "forward deployed", "deep learning", "nlp", "ia", "prompt engineer", "mlops",
        "research engineer", "research scientist", "computer vision",
    ),
    "data": (
        "data", "analytics", "analyst", "data scientist", "data engineer", "bi",
        "business intelligence",
    ),
    "product": ("product manager", "product owner", "product", "pm", "chef de produit"),
    "design": ("designer", "design", "ux", "ui", "product designer"),
    "sales": (
        "sales", "account executive", "solutions engineer", "solutions architect",
        "sales engineer", "presales", "business development", "customer success",
        "account manager", "commercial", "solutions",
    ),
    "marketing": ("marketing", "growth", "content", "brand", "seo", "community manager"),
    "operations": (
        "operations", "ops", "project manager", "program manager", "chef de projet",
        "consultant", "strategy",
    ),
    "people": (
        "recruiter", "talent", "people", "hr", "human resources", "rh", "recrutement",
        "recruteur",
    ),
    "finance": ("finance", "accountant", "comptable", "controller", "financial analyst"),
}  # fmt: skip

INDUSTRY_WORDS: dict[str, tuple[str, ...]] = {
    "ai": (
        "ai", "artificial intelligence", "machine learning", "ml", "genai", "generative ai",
        "llm", "deep learning", "intelligence artificielle", "ia", "nlp", "computer vision",
    ),
    "software": (
        "software", "saas", "tech", "technology", "logiciel", "internet", "b2b",
        "enterprise software", "informatique", "numerique", "digital",
    ),
    "developer_tools": (
        "developer tools", "devtools", "dev tools", "developer platform", "cloud",
        "infrastructure", "observability", "monitoring", "open source", "devops",
    ),
    "data": ("data", "analytics", "big data", "database", "donnees"),
    "fintech": (
        "fintech", "finance", "financial services", "banking", "bank", "banque", "payment",
        "paiement", "insurance", "insurtech", "assurance", "accounting", "comptabilite",
    ),
    "health": (
        "health", "healthcare", "healthtech", "medtech", "sante", "medical", "biotech",
    ),
    "legal": ("legal", "legaltech", "law", "juridique", "droit"),
    "consumer": ("consumer", "consumer apps", "b2c", "productivity", "social", "communication"),
    "marketplace": (
        "marketplace", "e commerce", "ecommerce", "retail", "commerce", "travel", "hospitality",
    ),
    "media": (
        "media", "music", "audio", "voice", "video", "entertainment", "creative", "gaming",
        "games", "jeux video", "musique",
    ),
    "mobility": ("mobility", "mobilite", "transport", "transportation", "automotive"),
    "education": ("education", "edtech", "learning"),
}  # fmt: skip

COUNTRY_WORDS: dict[str, tuple[str, ...]] = {
    "FR": (
        "france", "paris", "lyon", "bordeaux", "toulouse", "lille", "nantes", "marseille",
        "nice", "sophia antipolis", "montpellier", "rennes", "grenoble", "strasbourg",
        "ile de france",
    ),
    "GB": (
        "united kingdom", "uk", "great britain", "england", "scotland", "london", "londres",
        "manchester", "edinburgh", "cambridge", "oxford", "royaume uni",
    ),
    "US": (
        "united states", "usa", "us", "etats unis", "new york", "nyc", "san francisco",
        "bay area", "seattle", "boston", "austin", "los angeles", "chicago", "palo alto",
        "mountain view",
    ),
    "DE": ("germany", "deutschland", "allemagne", "berlin", "munich", "munchen", "hamburg",
           "frankfurt"),
    "NL": ("netherlands", "holland", "pays bas", "amsterdam", "rotterdam"),
    "CH": ("switzerland", "suisse", "schweiz", "zurich", "geneva", "geneve", "lausanne", "bern"),
    "ES": ("spain", "espagne", "madrid", "barcelona", "barcelone"),
    "IE": ("ireland", "irlande", "dublin"),
    "CA": ("canada", "toronto", "montreal", "vancouver"),
    "SG": ("singapore", "singapour"),
    "BE": ("belgium", "belgique", "brussels", "bruxelles"),
    "LU": ("luxembourg",),
    "PT": ("portugal", "lisbon", "lisbonne", "porto"),
    "IT": ("italy", "italie", "milan", "rome"),
    "SE": ("sweden", "suede", "stockholm"),
    "PL": ("poland", "pologne", "warsaw", "varsovie"),
    "JP": ("japan", "japon", "tokyo"),
    "IN": ("india", "inde", "bangalore", "bengaluru"),
    "AU": ("australia", "australie", "sydney", "melbourne"),
}  # fmt: skip
EUROPE = ("FR", "GB", "DE", "NL", "CH", "ES", "IE", "BE", "LU", "PT", "IT", "SE", "PL")
EUROPE_WORDS = ("europe", "emea", "eu", "european union", "union europeenne")
ANYWHERE_WORDS = (
    "anywhere", "remote", "worldwide", "partout", "teletravail", "remote first", "full remote",
)  # fmt: skip

INDUSTRY_NAMES = {
    "ai": "Artificial intelligence",
    "software": "Software",
    "developer_tools": "Developer tools",
    "data": "Data",
    "fintech": "Finance",
    "health": "Health",
    "legal": "Legal",
    "consumer": "Consumer apps",
    "marketplace": "Marketplaces",
    "media": "Media and audio",
    "mobility": "Mobility",
    "education": "Education",
}
COUNTRY_NAMES = {
    "FR": "France", "GB": "United Kingdom", "US": "United States", "DE": "Germany",
    "NL": "Netherlands", "CH": "Switzerland", "ES": "Spain", "IE": "Ireland", "CA": "Canada",
    "SG": "Singapore", "BE": "Belgium", "LU": "Luxembourg", "PT": "Portugal", "IT": "Italy",
    "SE": "Sweden", "PL": "Poland", "JP": "Japan", "IN": "India", "AU": "Australia",
}  # fmt: skip


def _pattern(words: Iterable[str]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternation})(?:s|es)?(?!\w)")


_ROLE_PATTERNS = {name: _pattern(words) for name, words in ROLE_WORDS.items()}
_INDUSTRY_PATTERNS = {name: _pattern(words) for name, words in INDUSTRY_WORDS.items()}
_COUNTRY_PATTERNS = {name: _pattern(words) for name, words in COUNTRY_WORDS.items()}
_EUROPE = _pattern(EUROPE_WORDS)
_ANYWHERE = _pattern(ANYWHERE_WORDS)


def _named(
    terms: Sequence[str], patterns: dict[str, re.Pattern[str]]
) -> tuple[set[str], list[str]]:
    """The vocabulary names the terms match, and the terms that match none."""
    found: set[str] = set()
    unmatched: list[str] = []
    for term in terms:
        text = fold(term)
        hits = {name for name, pattern in patterns.items() if pattern.search(text)}
        found |= hits
        if not hits:
            unmatched.append(term)
    return found, unmatched


def role_families(terms: Sequence[str]) -> tuple[set[str], list[str]]:
    return _named(terms, _ROLE_PATTERNS)


def industries_of(terms: Sequence[str]) -> tuple[set[str], list[str]]:
    return _named(terms, _INDUSTRY_PATTERNS)


def countries_of(terms: Sequence[str]) -> tuple[set[str] | None, list[str]]:
    """The countries the places name; None when one of them is "Anywhere" or "Remote" (no
    filter). A place the table does not know is returned as unmatched and filters nothing."""
    found: set[str] = set()
    unmatched: list[str] = []
    anywhere = False
    for term in terms:
        text = fold(term)
        hits = {name for name, pattern in _COUNTRY_PATTERNS.items() if pattern.search(text)}
        if _EUROPE.search(text):
            hits |= set(EUROPE)
        if _ANYWHERE.search(text):
            anywhere = True
        elif not hits:
            unmatched.append(term)
        found |= hits
    return (None if anywhere else found), unmatched


# --- the catalog --------------------------------------------------------------------------------

_Org = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=ORG_PATTERN)]
_Name = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    company: _Name
    industries: Annotated[tuple[Industry, ...], Field(min_length=1, max_length=12)]
    roles: tuple[RoleFamily, ...] = tuple(ROLE_FAMILIES)
    countries: tuple[Country, ...] = ()
    remote: bool = False  # hires remotely: shown whatever country the person named
    company_type: Literal["startup", "scaleup", "large_company"] | None = None
    general: bool = False  # in the small list for a person who gave no target


class Board(_Entry):
    ats: Literal["ashby", "greenhouse", "lever"]
    org: _Org

    @property
    def key(self) -> str:
        return f"{self.ats}:{self.org.lower()}"


class Source(_Entry):
    """A browser source of B8; `contract` limits it to a person who chose that contract type
    (VIE offers are VIE contracts only)."""

    kind: Literal["google_careers", "vie"]
    contract: Literal["VIE"] | None = None

    @property
    def key(self) -> str:
        return f"source:{self.kind}"


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Annotated[int, Field(ge=1)]
    checked_on: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    boards: tuple[Board, ...]
    sources: tuple[Source, ...] = ()

    def entry(self, key: str) -> Board | Source | None:
        for item in (*self.boards, *self.sources):
            if item.key == key:
                return item
        return None


class CatalogError(ValueError):
    """The catalog file breaks a rule: never shipped (a test loads the real file)."""


def _cyber(entry: _Entry) -> bool:
    return matching_rule([CYBER_RULE], entry.company, *entry.industries) is not None


def load(path: Path) -> Catalog:
    """The catalog at `path`, checked: every key once, no cybersecurity company."""
    try:
        catalog = Catalog.model_validate_json(path.read_text("utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        raise CatalogError(f"{path.name}: {exc}") from None
    keys = [item.key for item in (*catalog.boards, *catalog.sources)]
    twice = sorted({key for key in keys if keys.count(key) > 1})
    if twice:
        raise CatalogError(f"{path.name}: listed twice: {', '.join(twice)}")
    cyber = [item.company for item in (*catalog.boards, *catalog.sources) if _cyber(item)]
    if cyber:
        raise CatalogError(f"{path.name}: never a cybersecurity company: {', '.join(cyber)}")
    if any(re.fullmatch(STARTER_KEY_PATTERN, key) is None for key in keys):
        raise CatalogError(f"{path.name}: a key the profile cannot store")
    return catalog


@functools.cache
def _cached(path: Path) -> Catalog:
    return load(path)


def uses_stub(settings: Any) -> bool:
    """Every ATS call goes to a test stub: real company boards would only answer its 404."""
    return (
        getattr(settings, "ats_base_url", None) is not None
        and getattr(settings, "ats_base_url_org_prefix", None) is None
    )


def catalog_for(settings: Any) -> Catalog:
    return _cached(STUB_CATALOG_PATH if uses_stub(settings) else CATALOG_PATH)


# --- the proposal -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    key: str
    kind: Literal["board", "source"]
    company: str
    ats: str | None
    org: str | None
    source: str | None
    query: str | None
    location: str | None
    why: str
    added: bool


@dataclass(frozen=True)
class Proposal:
    targets: bool  # the person gave roles, industries or places
    entries: list[Entry]
    unmatched: list[str]  # role and industry words the catalog knows nothing of
    version: int
    checked_on: str
    blocked: int = 0  # left out by a never apply rule (a count, never which)


def _first_plain(terms: Any) -> str | None:
    """The person's first role or place, as typed; None for a list kept as patterns."""
    if terms.advanced_regex or not terms.terms:
        return None
    return str(terms.terms[0])[:200]


def _why(item: _Entry, industries: set[str], countries: set[str] | None) -> str:
    shown = [name for name in item.industries if name in industries] or list(item.industries[:1])
    parts = [INDUSTRY_NAMES[name] for name in shown[:2]]
    if countries:
        parts += [COUNTRY_NAMES[c] for c in item.countries if c in countries][:2]
    if item.remote and (countries is None or not countries & set(item.countries)):
        parts.append("Remote")
    return ", ".join(parts)


def propose(catalog: Catalog, search: SearchSection, handled: Iterable[str]) -> Proposal:
    """The person's starter list (pure: the catalog, the search section, the keys handled)."""
    done = set(handled)
    rules = [*never_apply_rules(search), CYBER_RULE]
    present = {f"{b.ats}:{b.org.lower()}" for b in search.boards}
    present |= {f"source:{s.kind}" for s in search.browser_sources}
    roles, places, kinds = search.title_include.terms, search.location.terms, search.industries
    given = bool(roles or kinds.terms or places)
    families, unknown_roles = role_families(roles)
    industries, unknown_industries = industries_of(kinds.terms)
    countries, _ = countries_of(places)
    nothing_known = bool(roles and not families) or bool(kinds.terms and not industries)
    query = _first_plain(search.title_include)
    place = _first_plain(search.location)
    types = set(search.company_types)

    ranked: list[tuple[tuple[int, ...], Entry]] = []
    blocked = 0
    for item in (*catalog.boards, *catalog.sources):
        if matching_rule(rules, item.company, *item.industries) is not None:
            blocked += 1
            continue
        if item.key in done and item.key not in present:
            continue  # removed from the list, or added and later deleted: never again
        if isinstance(item, Source):
            if query is None:
                continue  # a browser source searches the person's own role words
            if item.contract and item.contract not in search.contract_types:
                continue
        if not given:
            if not item.general:
                continue
            score: tuple[int, ...] = (0, 0, 0, 0)
        else:
            if nothing_known:
                continue
            if families and not families & set(item.roles):
                continue
            if industries and not industries & set(item.industries):
                continue
            local = countries is None or bool(countries & set(item.countries))
            if countries and not local and not item.remote and item.countries:
                continue
            score = (
                len(industries & set(item.industries)),
                len(families & set(item.roles)),
                int(bool(countries and countries & set(item.countries))),
                int(item.company_type in types),
            )
        board = item if isinstance(item, Board) else None
        source = item if isinstance(item, Source) else None
        entry = Entry(
            key=item.key,
            kind="board" if board else "source",
            company=item.company,
            ats=board.ats if board else None,
            org=board.org if board else None,
            source=source.kind if source else None,
            query=query if source else None,
            location=(place if source and source.kind == "google_careers" else None),
            why=_why(item, industries, countries),
            added=item.key in present,
        )
        ranked.append((score, entry))
    ranked.sort(key=lambda pair: (tuple(-n for n in pair[0]), pair[1].company.casefold()))
    limit = ENTRIES_MAX if given else GENERAL_MAX
    return Proposal(
        targets=given,
        entries=[entry for _, entry in ranked[:limit]],
        unmatched=(unknown_roles if roles and not families else [])
        + (unknown_industries if kinds.terms and not industries else []),
        version=catalog.version,
        checked_on=catalog.checked_on,
        blocked=blocked,
    )


# --- the person's choice ------------------------------------------------------------------------


class UnknownKeyError(ValueError):
    """A key that is in no catalog entry."""


class SearchUnreadableError(Exception):
    """The stored search section no longer passes the checks: open Search options first."""


class TooManyError(Exception):
    """The search section would hold more boards or sources than it allows."""


def _locked(db: Session, user: User, now: datetime) -> Profile:
    profile_service.get_profile(db, user.id, now)  # the row exists from here on
    return db.execute(
        sa.select(Profile)
        .where(Profile.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def _search(profile: Profile) -> SearchSection:
    try:
        return SearchSection.model_validate((profile.sections or {}).get("search") or {})
    except ValidationError:
        raise SearchUnreadableError from None


def handled_of(profile: Profile) -> list[str]:
    onboarding = ((profile.sections or {}).get("preferences") or {}).get("onboarding") or {}
    found = onboarding.get("starter_handled")
    return [key for key in found if isinstance(key, str)] if isinstance(found, list) else []


def current(db: Session, user: User, catalog: Catalog, now: datetime) -> Proposal:
    profile = profile_service.get_profile(db, user.id, now)
    return propose(catalog, _search(profile), handled_of(profile))


def _board(entry: Entry) -> dict[str, Any]:
    return {
        "company": entry.company,
        "ats": entry.ats,
        "org": entry.org,
        "enabled": True,
        "extra_exclude": {"advanced_regex": False, "terms": []},
    }


def _source(entry: Entry) -> dict[str, Any]:
    return {"kind": entry.source, "query": entry.query, "location": entry.location, "enabled": True}


def choose(
    db: Session,
    user: User,
    catalog: Catalog,
    add: Sequence[str],
    remove: Sequence[str],
    now: datetime,
) -> tuple[Proposal, list[str]]:
    """Add the chosen entries to the search section (enabled: the person chose them) and keep
    every key added or removed among the ones handled, under the profile row lock; the caller
    commits. Only entries proposed right now are taken: one already added, handled or left out
    by a never apply rule is passed over. Returns the new proposal and the keys added."""
    for key in (*add, *remove):
        if catalog.entry(key) is None:
            raise UnknownKeyError(key)
    profile = _locked(db, user, now)
    search = _search(profile)
    offered = {e.key: e for e in propose(catalog, search, handled_of(profile)).entries}
    offered = {key: entry for key, entry in offered.items() if not entry.added}
    adding = [offered[key] for key in dict.fromkeys(add) if key in offered]
    taken = {entry.key for entry in adding}
    removing = [key for key in dict.fromkeys(remove) if key in offered and key not in taken]
    if adding:
        stored = search.model_dump(mode="json")
        stored["boards"] = [*stored["boards"], *(_board(e) for e in adding if e.kind == "board")]
        stored["browser_sources"] = [
            *stored["browser_sources"],
            *(_source(e) for e in adding if e.kind == "source"),
        ]
        try:
            data = SearchSection.model_validate(stored).model_dump(mode="json")
        except ValidationError:
            raise TooManyError from None
        profile = profile_service.save_section(db, user.id, "search", data, profile.revision, now)
    if adding or removing:
        profile = profile_service.record_starter(db, user.id, [*taken, *removing], now)
    return propose(catalog, _search(profile), handled_of(profile)), sorted(taken)
