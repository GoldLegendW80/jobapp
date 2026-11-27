"""freehire (https://freehire.me, its backend the MIT project strelov1/freehire): an open
aggregator of the postings of about 50 application systems, read through its public JSON
interface `GET /api/v1/agent/jobs/search`, no key, `{data, meta}` with each posting's full
description. Its documents state no rate limit: a sweep keeps to a few requests (BUDGET, PAUSE_S
apart, TIME_S at most). SPEC-jobs.md, "Job feeds".

What a sweep asks for (`params_of`, `groups_of`, `places_of`):
- the saved titles' plain words as `q`, at most ALTERNATIVES a request joined by an uppercase
  `OR`, each `title:` scoped; the groups after the first take the requests left in turn;
- the saved places: a country of the app's own table (`app.apply.answers.COUNTRIES`) as
  `countries` (ISO 3166 alpha 2, lower case), a region of that table or a word for remote work
  left out of the request, "everywhere" and its kin meaning no place, any other place as
  `cities` (as typed, and the gazetteer's name when it differs: "Genève" and "Geneva"). The
  places sent are one group the server ORs, so what it answers is in a wanted place (`placed`);
  with no place sent, the saved places' words filter the answer as for every feed;
- the age window as `posted_within_days`, the remote stance as `work_mode` when it excludes
  something (remote, or hybrid: "on site" holds hybrid too and one value cannot say both).

What it keeps of a posting (`finding_of`): its own address (the application system's page,
canonical, http or https only), else freehire's page of it; a closed one (`closed_at`) and one
with neither address are left out. Its upstream system and freehire's page are kept in the
sweep event's evidence. Nothing in a description is followed.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.apply.answers import COUNTRIES, EU
from app.browser.words import words
from app.jobs import gazetteer
from app.jobs.feeds import (
    ANYWHERE,
    Criteria,
    FeedContext,
    FeedError,
    Fetched,
    Finding,
    _fold,
    iso,
    line,
    plain_text,
)
from app.jobs.models import ContractType, WorkMode

DEFAULT_BASE = "https://freehire.me"
SEARCH = "/api/v1/agent/jobs/search"
BUDGET = 8  # requests a sweep
PAGE_SIZE = 25
PAUSE_S = 2.0
TIME_S = 60.0
ALTERNATIVES = 5  # the interface refuses more `OR` alternatives in one `q` (a 400)
ALTERNATIVE_MAX = 100  # characters of one alternative
OFFSET_MAX = 10_000  # the interface's bound on offset + limit
COUNTRIES_MAX = 40
CITIES_MAX = 20
CITY_MAX = 80  # characters of one city sent
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")
UPSTREAM = re.compile(r"[a-z0-9][a-z0-9_.-]{0,39}")

# The ISO 3166 alpha 2 code of each country of the app's own table; its regions have none.
ISO: dict[str, str] = {
    "United States": "us", "United Kingdom": "gb", "Austria": "at", "Belgium": "be",
    "Bulgaria": "bg", "Croatia": "hr", "Cyprus": "cy", "Czechia": "cz", "Denmark": "dk",
    "Estonia": "ee", "Finland": "fi", "France": "fr", "Germany": "de", "Greece": "gr",
    "Hungary": "hu", "Ireland": "ie", "Italy": "it", "Latvia": "lv", "Lithuania": "lt",
    "Luxembourg": "lu", "Malta": "mt", "Netherlands": "nl", "Poland": "pl", "Portugal": "pt",
    "Romania": "ro", "Slovakia": "sk", "Slovenia": "si", "Spain": "es", "Sweden": "se",
    "Switzerland": "ch", "Norway": "no", "Canada": "ca", "Singapore": "sg", "India": "in",
    "Japan": "jp", "Australia": "au", "South Korea": "kr", "Israel": "il",
    "United Arab Emirates": "ae", "China": "cn",
}  # fmt: skip
REGIONS = frozenset(
    {EU, "Europe", "European Economic Area", "Schengen Area", "EFTA", "North America",
     "Latin America", "South America", "Central America"}
)  # fmt: skip
_COUNTRY_OF = {phrase: name for name, phrases in COUNTRIES.items() for phrase in phrases}
# A saved place that asks for remote work rather than a place: whole words, case and accents
# aside. The remote stance is how a search asks for it (`work_mode`).
REMOTE = frozenset(
    {"remote", "full remote", "fully remote", "remote only", "100% remote", "remote first",
     "teletravail", "full teletravail", "teletravail complet", "a distance", "distanciel",
     "work from home", "wfh", "home office"}
)  # fmt: skip
WORK_MODES = {"remote": WorkMode.remote, "hybrid": WorkMode.hybrid, "onsite": WorkMode.office}
STANCES = {"remote": "remote", "hybrid": "hybrid"}
CONTRACTS = {
    "internship": ContractType.Stage,
    "intern": ContractType.Stage,
    "contract": ContractType.Freelance,
    "contractor": ContractType.Freelance,
    "freelance": ContractType.Freelance,
    "temporary": ContractType.CDD,
}


@dataclass(frozen=True)
class Posting(Finding):
    """A freehire finding: its work mode as stated, and what the record keeps of its origin."""

    mode: WorkMode | None = None
    upstream: str | None = None  # the application system freehire read it from
    page: str | None = None  # freehire's page of it: the link back

    @property
    def work_mode(self) -> str | None:
        return self.mode.value if self.mode is not None else None

    @property
    def evidence(self) -> dict[str, str]:
        kept = {"upstream": self.upstream, "page": self.page}
        return {**super().evidence, **{k: v for k, v in kept.items() if v}}


@dataclass(frozen=True)
class Places:
    countries: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()

    @property
    def sent(self) -> bool:
        return bool(self.countries or self.cities)


def _country(term: str) -> str | None:
    """The table's name of the country or region `term` is, whole words only, else None."""
    said = re.sub(r"^(?:the|l|le|la|les) ", "", words(term))
    return _COUNTRY_OF.get(said)


def places_of(criteria: Criteria) -> Places:
    """The saved places as the request says them (module docstring)."""
    if criteria.wanted_places is None:
        return Places()  # none saved, or one that means everywhere
    countries: list[str] = []
    cities: list[str] = []
    for term in criteria.places:
        folded = _fold(term)
        if not folded or folded in ANYWHERE:
            continue
        name = _country(term)
        if name is not None or folded in REMOTE:
            # A region, a country with no code here or remote work: left out of the request.
            code = ISO.get(name or "")
            if code is not None and code not in countries:
                countries.append(code)
        else:
            shown = " ".join(term.split())[:CITY_MAX]
            named = [p.name for p in gazetteer.places_of(term) if p.kind == "city"]
            for city in (shown, *named[:1]):
                if city and city.casefold() not in {c.casefold() for c in cities}:
                    cities.append(city)
    return Places(tuple(countries[:COUNTRIES_MAX]), tuple(cities[:CITIES_MAX]))


def _alternative(title: str) -> str:
    # Lower case, so an "OR" inside a saved title never splits it; no quote, colon or bracket,
    # which the query syntax may read.
    plain = re.sub(r"[\"():]", " ", title).casefold()
    return " ".join(plain.split())[:ALTERNATIVE_MAX].strip()


def groups_of(titles: tuple[str, ...]) -> list[tuple[str, ...]]:
    """The saved titles, cleaned and once each, ALTERNATIVES a request."""
    cleaned: list[str] = []
    for title in titles:
        alternative = _alternative(title)
        if alternative and alternative not in cleaned:
            cleaned.append(alternative)
    return [tuple(cleaned[i : i + ALTERNATIVES]) for i in range(0, len(cleaned), ALTERNATIVES)]


def query_of(group: tuple[str, ...]) -> str:
    return " OR ".join(f"title:{title}" for title in group)


def params_of(criteria: Criteria, places: Places) -> dict[str, Any]:
    """The parameters every request of a sweep shares."""
    params: dict[str, Any] = {
        "posted_within_days": criteria.days,
        "limit": PAGE_SIZE,
        "description_format": "html",
    }
    if places.countries:
        params["countries"] = list(places.countries)
    if places.cities:
        params["cities"] = list(places.cities)
    mode = STANCES.get(criteria.remote_stance or "")
    if mode is not None:
        params["work_mode"] = mode
    return params


def _own_address(value: object) -> str | None:
    """The posting's address as code keeps it, when it is an http or https one a browser opens."""
    if not isinstance(value, str) or not value or len(value) > 2000:
        return None
    from app.browser.finder import canonical  # the browser module imports the agents

    address = canonical(value)
    return address if address and len(address) <= 2000 else None


def _location(item: dict[str, Any], mode: WorkMode | None) -> str:
    location = line(item.get("location"))
    if not location:
        cities = [line(c, 80) for c in item.get("cities") or [] if isinstance(c, str)][:3]
        location = ", ".join(c for c in cities if c)
    if mode is WorkMode.remote and "remote" not in _fold(location):
        location = f"{location}, Remote" if location else "Remote"
    return location


def finding_of(item: Any, now: datetime, base: str, *, placed: bool) -> Posting | None:
    """One posting of an answer, None when it is closed or lacks what a row needs."""
    if not isinstance(item, dict) or item.get("closed_at"):
        return None
    title = line(item.get("title"))
    slug = item.get("public_slug")
    slug = slug if isinstance(slug, str) and SLUG.fullmatch(slug) else None
    page = f"{base}/jobs/{slug}" if slug else None
    url = _own_address(item.get("url")) or page
    if not title or url is None:
        return None
    mode_said = item.get("work_mode")
    mode = WORK_MODES.get(mode_said) if isinstance(mode_said, str) else None
    enrichment = item.get("enrichment") if isinstance(item.get("enrichment"), dict) else {}
    employment = enrichment.get("employment_type")
    upstream = item.get("source")
    return Posting(
        source="freehire",
        external_id=slug or url,
        title=title,
        company=line(item.get("company")) or None,
        location=_location(item, mode),
        url=url,
        text=plain_text(item.get("description")),
        posted_at=iso(item.get("posted_at")),
        remote=mode is WorkMode.remote or None,
        contract=CONTRACTS.get(employment.casefold()) if isinstance(employment, str) else None,
        read_at=now,
        placed=placed,
        mode=mode,
        upstream=upstream if isinstance(upstream, str) and UPSTREAM.fullmatch(upstream) else None,
        page=page,
    )


def _configured() -> str | None:
    from app.config import ConfigError, get_settings

    try:
        return get_settings().freehire_base_url
    except ConfigError:  # no configuration at all (a unit test): the real host
        return None


class Freehire:
    name = "freehire"
    budget = BUDGET
    needs_key = False
    place_filter = True  # unless the request carried the places (`Posting.placed`)
    pause_s = PAUSE_S
    time_s = TIME_S

    def __init__(self, base: str | None = None) -> None:
        self.base = (base or _configured() or DEFAULT_BASE).rstrip("/")
        self.host = httpx.URL(self.base).host

    async def fetch(self, ctx: FeedContext, criteria: Criteria) -> Fetched:
        fetched = ctx.fetched  # added to as it reads: a cancelled run keeps its findings
        places = places_of(criteria)
        shared = params_of(criteria, places)
        groups = groups_of(criteria.titles)
        for index, group in enumerate(groups):
            if ctx.http.left <= 0 or fetched.error:
                fetched.queries_left_out += len(group)
                continue
            fetched.searched.extend(group)
            # The requests left, shared fairly with the groups after this one.
            pages = math.ceil(ctx.http.left / (len(groups) - index))
            for page in range(pages):
                offset = page * PAGE_SIZE
                if offset + PAGE_SIZE > OFFSET_MAX:
                    break
                params = {**shared, "q": query_of(group), "offset": offset}
                try:
                    data = await ctx.http.get_json(self.base + SEARCH, params)
                except FeedError as error:
                    fetched.error = error.code
                    break
                items = data.get("data") if isinstance(data, dict) else None
                if not isinstance(items, list):
                    fetched.error = "invalid_response"
                    break
                for item in items[:PAGE_SIZE]:
                    found = finding_of(item, ctx.now, self.base, placed=places.sent)
                    if found is not None:
                        fetched.findings.append(found)
                meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
                total = meta.get("total")
                known = type(total) is int and total >= 0
                if len(items) < PAGE_SIZE or (known and offset + PAGE_SIZE >= total):
                    break
        fetched.requests = ctx.http.requests
        return fetched
