"""Business France VIE offers searched through the site's own public search API (owner,
2026-11-01: "I want being able to fetch directly buisness France to search for jobs and apply to
jobs. I want to use reverse engineering api skill like I did for linkedin to search for jobs et
real playwright cli to apply."). SPEC-jobs, "Business France VIE search". This is the search
half; applying stays in the person's browser (app/apply/ats/vie.py).

When the person ticks "Business France VIE" in Find jobs (`search.vie_businessfrance`, off by
default), a sweep reads mon-vie-via.businessfrance.fr with plain HTTP requests to the JSON API
its own public pages call (mode `api`, kind `vie`, no lease, no model, no account), beside the
sweep's other sources that make no browser call (Runner.vie).

It lives next to linkedin_guest.py rather than with the job feeds (app/jobs/feeds): it needs what
the Runner gives that source and the feeds do not have, the shared per host pacer (RedisPacer),
the cooldown shared by every sweep (RedisCooldown), the worker's guest HTTP transport, the test
redirect of BROWSER_SOURCES_BASE_URL, and a POST with a header read from a page first.

- Key. The API wants one header, `X-API-KEY`: the same public key for every anonymous visitor,
  shipped inline in the HTML of the public search page (the Nuxt runtime config). One GET of
  `<site>/offres/recherche` per sweep reads it (`public_key`); it is held in memory for that
  sweep only, never stored, logged nor written to the entry, so a rotation by the site is
  followed. No key in the page ends the source `vie_key_missing` (the front end changed), never
  a fallback to a stored key.
- Searches. `POST <api>/api/Offers/search` with the body the page sends (`search_body`): one
  search per saved title (QUERIES_MAX, in saved order), `missionsTypesIds` VIE only, the
  saved places as ISO 3166 alpha 2 `countriesIds` (`countries_of`: the site's own country list,
  vie_countries.json, by its French or English name, a two letter code, or a country alias of
  the app's gazetteer; "London, United Kingdom" reads its last part; a saved place that says no
  limit means no country filter; a place that names no country, a city alone for instance, is
  skipped and listed in the entry's `places_skipped`, since the app's place data gives no city's
  country; when no saved place names a country, no country filter is sent: every VIE mission is
  abroad, and the saved titles still filter). PAGE_SIZE offers a page, at most PAGES_PER_QUERY
  pages a search, in rounds (the first page of every search, then the second of those that go
  on), a search ending at a short page or its count.
- Filters, after reading (the API's `query` is a substring match over the whole text, words
  ANDed): the saved titles to include must match the offer's title, the unwanted titles and
  places must not, an offer older than the saved days (its fresher of creation and broadcast
  start) is left out, and only `missionType` VIE is kept. Never apply rules, the app's own rule
  against cybersecurity included, mark the stored row `skipped` (sweep.Commits), as for every
  source. The saved contract types do not filter it: ticking it asks for VIE.
- Storing, through the run's commits (sweep.Commits): the public offer page
  `<site>/offres/<id>` as the address (so the same offer id is the same posting across runs),
  source `mon-vie-via.businessfrance.fr`, contract type VIE, title, company, "<city>, <country>",
  published date, and the text: the mission's description and profile with its facts (country,
  city, duration, start, monthly allowance, telework). `contactURL`, the employer's own
  application site when the offer has one, is kept as the finding's `apply_url` and in the sweep
  event's evidence. Privacy: the recruiter's civility, names, e-mail and phone fields are never
  read, and e-mail addresses and phone numbers in the text are replaced.
- Pace. One request of the API's host at a time across people and workers, PAUSE_S after the
  previous one ended (RedisPacer, key the API host). At most REQUESTS_MAX requests a sweep, the
  key page included, within VIE_TIME_S from its start and never closer to the sweep's deadline
  than linkedin_guest.WEBSITES_MIN_S when a website read in the browser is ticked.
- Stop. HTTP 401, 403 or 429 ends the source at once `vie_rate_limited`, what was stored kept,
  no retry, and starts a cooldown: for COOLDOWN_S no sweep of any person sends the host a
  request (the Runner's guest cooldown, one Redis key per host). Any other unexpected answer, a
  redirect, an answer that is no JSON or too big, or a request longer than REQUEST_S ends it
  `page_failed`. Once the sweep let go of its row (`sweep.abandoned`), no further request is
  sent (`store_failed`).

The site's legal notice limits users to its free zone and protects its database (Code de la
propriété intellectuelle, L. 341-1): the search API is what the free zone itself calls, with the
key it hands every visitor; volume stays low (a few searches a sweep for one person's own
applications) and the base is never crawled nor republished. The candidate's signed in endpoints
are never called here.

BROWSER_SOURCES_BASE_URL (tests only) sends every request to `<base>/vie/...` and stores postings
there, never on the real host; without an HTTP transport (Runner.guest_http) the source makes no
request and ends `page_failed`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import json
import logging
import re
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from app.browser import finder
from app.browser.leases import StoreUnavailableError
from app.browser.sources import PACE_POLL_S
from app.jobs import feeds, gazetteer, sweep
from app.jobs.models import ContractType
from app.jobs.schemas import SEARCHES_LISTED
from app.profile.plain_terms import typed_words

if TYPE_CHECKING:
    from app.browser.sources import Runner
    from app.profile.schemas import SearchSection

logger = logging.getLogger("app.browser.sources.vie_businessfrance")

MODE = "api"
KIND = "vie"
SITE_URL = "https://mon-vie-via.businessfrance.fr"
API_URL = "https://api-mon-vie-via.businessfrance.fr"
SITE_HOST = "mon-vie-via.businessfrance.fr"  # the job's `source` and the entry's host
API_HOST = "api-mon-vie-via.businessfrance.fr"  # the pace and cooldown key
KEY_PAGE = "/offres/recherche"
SEARCH_PATH = "/api/Offers/search"
OFFER_PATH = "/offres/"
MISSION_TYPES = ("VIE",)
PAUSE_S = (3.0, 5.0)  # between two requests of the API's host (module docstring, Pace)
QUERIES_MAX = 10  # saved titles searched, in saved order
PAGE_SIZE = 50
PAGES_PER_QUERY = 3
REQUESTS_MAX = 30  # the key page included
# Its own time cap beside the sweep's other sources that make no browser call (alerts 120 s,
# feeds 180 s, boards 300 s, LinkedIn 420 s): 30 requests about 4 s apart fit in 180 s.
VIE_TIME_S = 180.0
TIMEOUT_S = 20.0
REQUEST_S = 30.0  # a request is not started this close to the source's time limit
BODY_MAX = 4_000_000  # bytes read of one answer (50 offers with their text are about 250 KB)
TEXT_MAX = finder.TEXT_MAX
FIELD_MAX = finder.TITLE_MAX
BATCH = 20  # findings per commit
STOPPED = frozenset({401, 403, 429})
RATE_LIMITED = "vie_rate_limited"
KEY_MISSING = "vie_key_missing"
COOLDOWN_S = 1800.0
PLACES_LISTED = 10
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
    "Origin": SITE_URL,
    "Referer": SITE_URL + "/",
}
COUNTRIES_PATH = Path(__file__).with_name("vie_countries.json")
_KEY = re.compile(r'API_KEY\s*:\s*"((?:[^"\\]|\\.){1,400})"')
_KEY_SHAPE = re.compile(r"[A-Za-z0-9+/=_\-.]{8,200}")
_OFFER_ID = re.compile(r"[0-9]{1,12}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(
    r"(?:(?<![\w+])(?:\+|00)\d{1,3}[\s.-]?(?:\(?\d{1,4}\)?[\s.-]?){2,5}\d{2,4}(?!\d))"
    r"|(?:(?<!\d)0\d(?:[\s.-]?\d{2}){4}(?!\d))"
)


def now() -> datetime:
    """The time the age filter reads (tests replace it)."""
    return datetime.now(UTC)


class StopError(Exception):
    """The source ends with this code (sweep's `reason`)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- the searches --------------------------------------------------------------------------------


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    words = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", words).split())


@functools.cache
def country_names() -> dict[str, str]:
    """Folded names of the site's countries (French, English, the code), and the app's
    gazetteer aliases of a country the site lists by the same English name, to their code."""
    names: dict[str, str] = {}
    rows = json.loads(COUNTRIES_PATH.read_text(encoding="utf-8"))
    by_english: dict[str, str] = {}
    for code, french, english in rows:
        names[_fold(french)] = code
        names[_fold(english)] = code
        by_english[_fold(english)] = code
    for country in gazetteer.gazetteer().countries:
        code = by_english.get(_fold(country.name))
        if code is None:
            continue
        for alias in country.aliases:
            names.setdefault(_fold(alias), code)
    return names


def _country(place: str) -> str | None:
    names = country_names()
    folded = _fold(place)
    if folded in names:
        return names[folded]
    if re.fullmatch(r"[A-Za-z]{2}", place.strip()):
        code = place.strip().upper()
        return code if code in names.values() else None
    last = place.rsplit(",", 1)
    if len(last) == 2:
        return names.get(_fold(last[1]))
    return None


def countries_of(search: SearchSection) -> tuple[list[str], list[str]]:
    """The `countriesIds` of the saved places, and the places skipped (module docstring,
    Searches). No country at all means no country filter."""
    places = [p for p in search.location.terms if isinstance(p, str) and p.strip()]
    if not places or any(finder.worldwide(p) or feeds._fold(p) in feeds.ANYWHERE for p in places):
        return [], []
    codes: list[str] = []
    skipped: list[str] = []
    for place in places:
        code = _country(place)
        if code is None:
            skipped.append(place)
        elif code not in codes:
            codes.append(code)
    return codes, skipped


@dataclass(frozen=True)
class Query:
    title: str
    countries: tuple[str, ...]


def queries_for(search: SearchSection) -> tuple[list[Query], list[str]]:
    """One search per saved title (at most QUERIES_MAX), and the places skipped; ValueError
    (`unsupported_criteria`) when no saved title gives a word to type."""
    titles, _ = typed_words(search.title_include)
    titles = [t for t in dict.fromkeys(" ".join(t.split()) for t in titles) if t]
    if not titles:
        raise ValueError("unsupported_criteria")
    countries, skipped = countries_of(search)
    return [Query(t, tuple(countries)) for t in titles[:QUERIES_MAX]], skipped


def search_body(query: Query, page: int) -> dict[str, Any]:
    """What the site's own search page sends, VIE only, `limit` PAGE_SIZE."""
    return {
        "limit": PAGE_SIZE,
        "skip": page * PAGE_SIZE,
        "query": query.title.casefold(),
        "activitySectorId": [],
        "missionsTypesIds": list(MISSION_TYPES),
        "missionsDurations": [],
        "geographicZones": [],
        "countriesIds": list(query.countries),
        "studiesLevelId": [],
        "companiesSizes": [],
        "specializationsIds": [],
        "entreprisesIds": [0],
        "missionStartDate": None,
    }


def public_key(html: str) -> str | None:
    """The X-API-KEY of the page's public runtime config, None when the page holds none."""
    found = _KEY.search(html)
    if found is None:
        return None
    try:
        key = json.loads('"' + found.group(1) + '"')  # the JavaScript escapes undone
    except ValueError:
        return None
    return key if isinstance(key, str) and _KEY_SHAPE.fullmatch(key) else None


# --- an offer ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VieFinding(feeds.Finding):
    """An offer as sweep.Commits stores it: a feed's Finding, plus the employer's application
    address when the offer names one (`apply_url`, read by the store once the jobs table has
    the column) and the evidence that says how it was read."""

    apply_url: str | None = None
    # The published date is the offer's creation; `posted_at`, which the age filter reads, is
    # the fresher of creation and broadcast start (offers are reposted).
    created_on: str | None = None

    @property
    def published_on(self) -> str | None:
        return self.created_on or super().published_on

    @property
    def evidence(self) -> dict[str, str]:
        return {
            "api": KIND,
            "external_id": self.external_id[:200],
            "read_through": "vie_businessfrance_api",
            **({"apply_url": self.apply_url} if self.apply_url else {}),
        }


def _line(value: object, limit: int = FIELD_MAX) -> str:
    text = feeds.line(value, limit)
    return text if len(text) <= limit else ""


def _private(text: str) -> str:
    """The text without e-mail addresses and phone numbers (module docstring, Privacy)."""
    return _PHONE.sub("[phone removed]", _EMAIL.sub("[e-mail removed]", text))


def _place(value: object) -> str:
    text = _line(value)
    return text.title() if text.isupper() else text


def _https(value: object) -> str | None:
    """`contactURL` when it is a plain https address, else None."""
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        return None
    value = value.strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or "." not in parts.hostname:
        return None
    if parts.username or parts.password or any(c.isspace() for c in value):
        return None
    return value


def _money(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return ""
    return f"{value:,.0f} EUR a month".replace(",", " ")


def finding_of(offer: object, site: str, now: datetime) -> VieFinding | None:
    """One API offer as a finding, None when it has no usable id or title. The contact fields
    are never read."""
    if not isinstance(offer, dict):
        return None
    offer_id = str(offer.get("id") or "")
    title = _line(offer.get("missionTitle"))
    if not _OFFER_ID.fullmatch(offer_id) or not title:
        return None
    company = _line(offer.get("organizationName")) or None
    city = _place(offer.get("cityNameEn") or offer.get("cityName"))
    country = _place(offer.get("countryNameEn") or offer.get("countryName"))
    location = ", ".join(p for p in (city, country) if p)
    created = feeds.iso(offer.get("creationDate"))
    broadcast = feeds.iso(offer.get("startBroadcastDate"))
    fresher = max((d for d in (created, broadcast) if d is not None), default=None)
    duration = offer.get("missionDuration")
    start = feeds.iso(offer.get("missionStartDate"))
    facts = [
        ("Country", country),
        ("City", city),
        ("Duration", f"{duration} months" if isinstance(duration, int) and duration > 0 else ""),
        ("Start", start.date().isoformat() if start else ""),
        ("Monthly allowance", _money(offer.get("indemnite"))),
        ("Telework", "possible" if offer.get("teleworkingAvailable") is True else ""),
        ("Reference", _line(offer.get("reference"), 40)),
    ]
    parts = [
        "\n".join(f"{name}: {value}" for name, value in facts if value),
        feeds.plain_text(offer.get("missionDescription"), TEXT_MAX),
        feeds.plain_text(offer.get("missionProfile"), TEXT_MAX),
    ]
    text = _private("\n\n".join(p for p in parts if p))[:TEXT_MAX]
    return VieFinding(
        source=KIND,
        external_id=offer_id,
        title=title,
        company=company,
        location=location,
        url=f"{site}{OFFER_PATH}{offer_id}",
        text=text,
        posted_at=fresher,
        contract=ContractType.VIE,
        read_at=now,
        apply_url=_https(offer.get("contactURL")),
        created_on=created.date().isoformat() if created else None,
    )


def parse_page(body: str) -> tuple[list[dict[str, Any]], int | None]:
    """The offers of a search answer and its total, ValueError when it is not that shape."""
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        raise ValueError("not a search answer")
    count = data.get("count")
    offers = [o for o in data["result"] if isinstance(o, dict)]
    return offers, count if isinstance(count, int) and not isinstance(count, bool) else None


# --- the HTTP client -----------------------------------------------------------------------------


class Client:
    """The site's public endpoints, one paced request at a time (module docstring: Pace, Stop)."""

    def __init__(
        self,
        runner: Runner,
        client: httpx.AsyncClient,
        key: str,
        deadline: float,
        check: Callable[[], Awaitable[None]],
    ) -> None:
        self.runner, self.client, self.key = runner, client, key
        self.deadline, self.check = deadline, check
        self.cooldown = runner.guest_cooldown
        self.requests = 0

    async def _ready(self) -> None:
        await self.check()
        if await self.cooldown.active(self.key):
            raise StopError(RATE_LIMITED)

    async def send(self, method: str, url: str, **kw: Any) -> str:
        """The body of a 200 answer. Raises StopError for a refusal, any other answer, the
        request cap or the time limit."""
        if self.requests >= REQUESTS_MAX:
            raise StopError("request_cap")
        runner = self.runner
        pause, holder = runner.rng.uniform(*PAUSE_S), uuid.uuid4().hex
        try:
            await self._ready()
            try:
                while True:
                    wait = await runner.pacer.reserve(self.key, pause, holder)
                    if wait is not None:
                        break
                    if runner.monotonic() + PACE_POLL_S + REQUEST_S >= self.deadline:
                        raise StopError("time_budget")
                    await runner.sleep(PACE_POLL_S)
                if runner.monotonic() + wait + REQUEST_S >= self.deadline:
                    raise StopError("time_budget")
                if wait > 0:
                    await runner.sleep(wait)
                await self._ready()  # the pause may have met a refusal of another sweep
                self.requests += 1
                try:
                    async with asyncio.timeout(REQUEST_S):
                        return await self._fetch(method, url, **kw)
                except TimeoutError:
                    raise StopError("page_failed") from None
                except StopError as stop:
                    if stop.code == RATE_LIMITED:
                        await self._cool()
                    raise
            finally:
                try:
                    await runner.pacer.release(self.key, holder)
                except StoreUnavailableError:
                    logger.warning("vie pace release failed")
        except StoreUnavailableError:
            raise StopError("page_failed") from None

    async def _cool(self) -> None:
        try:
            await self.cooldown.start(self.key, COOLDOWN_S)
        except StoreUnavailableError:
            logger.warning("vie cooldown not recorded")

    async def _fetch(self, method: str, url: str, **kw: Any) -> str:
        self.client.cookies.clear()  # no cookie the site set is ever sent back
        try:
            async with self.client.stream(method, url, **kw) as answer:
                status = answer.status_code
                if status in STOPPED:
                    raise StopError(RATE_LIMITED)
                if status != 200 or answer.headers.get("content-encoding", "identity").strip(
                ).casefold() not in ("", "identity"):  # fmt: skip
                    raise StopError("page_failed")
                body = bytearray()
                async for raw in answer.aiter_raw():
                    body += raw
                    if len(body) > BODY_MAX:
                        raise StopError("page_failed")
        except httpx.HTTPError:
            raise StopError("page_failed") from None
        return body.decode("utf-8", "replace")


# --- the source ----------------------------------------------------------------------------------


def ticked(plan: Any) -> bool:
    """Whether this sweep reads Business France VIE: as its frozen scope says when it has one,
    else as the person's setting."""
    scope = getattr(plan, "search_scope", None)
    if isinstance(scope, dict):
        return any(
            isinstance(s, dict) and s.get("mode") == MODE and s.get("kind") == KIND
            for s in scope.get("sources") or []
        )
    search = getattr(plan, "search", None)
    return bool(getattr(search, "vie_businessfrance", False))


def scope_entry() -> dict[str, Any]:
    """The frozen `stats.sources` entry of the tick (sweep.freeze_scope)."""
    return {
        "kind": KIND,
        "mode": MODE,
        "host": SITE_HOST,
        "source_id": sweep._source_id(MODE, KIND),
    }


def time_limit(now: float, deadline: float, websites: int) -> float:
    """VIE_TIME_S from `now`; with a website read in the browser ticked, never closer to the
    sweep's deadline than linkedin_guest.WEBSITES_MIN_S (or half the time left)."""
    from app.browser.sources.linkedin_guest import WEBSITES_MIN_S

    left = max(0.0, deadline - now)
    keep = min(WEBSITES_MIN_S, left / 2) if websites > 0 else 0.0
    return min(now + VIE_TIME_S, deadline - keep)


@dataclass
class _Run:
    requests: int = 0
    pages: int = 0
    searched: dict[str, None] = field(default_factory=dict)
    planned: int = 0
    left_out: dict[str, int] = field(default_factory=dict)
    found: int = 0  # findings counted without the run's commits
    places_skipped: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)


async def run(
    runner: Runner,
    plan: Any,
    stats: sweep.Stats,
    progress: Callable[[], Awaitable[None]],
    deadline: float,
) -> None:
    """The sweep's hook when the tick is on (Runner.vie): one entry in `stats.sources`."""
    search = getattr(plan, "search", None)
    if search is None or not ticked(plan):
        return
    entry = sweep.bind_source(
        stats,
        MODE,
        (KIND,),
        {
            **scope_entry(),
            "result_pages": 0,
            "found": 0,
            "postings": 0,
            "error": None,
        },
    )
    stats.current_board = entry["source_id"]
    await progress()
    websites = sum(1 for s in search.interactive_sites if s.enabled)
    limit = time_limit(runner.monotonic(), deadline, websites)
    commits = getattr(plan, "commits", None)
    if commits is not None:
        commits.admit(entry["source_id"])
    try:
        entry["error"] = await _search(runner, plan, search, entry, progress, limit)
    except Exception:
        logger.exception("vie search failed")
        entry["error"] = "page_failed"
    if commits is not None and not await commits.settle(entry["source_id"]):
        entry["error"] = entry["error"] or "store_failed"
    sweep.finish_source(entry)
    sweep.release_board(stats, entry["source_id"])
    await progress()


async def _search(
    runner: Runner,
    plan: Any,
    search: SearchSection,
    entry: dict[str, Any],
    progress: Callable[[], Awaitable[None]],
    deadline: float,
) -> str | None:
    if runner.base_url:  # tests only: the fixture server
        site = api = f"{runner.base_url.rstrip('/')}/vie"
        key_host = urlsplit(runner.base_url).hostname or API_HOST
    else:
        site, api, key_host = SITE_URL, API_URL, API_HOST

    async def check() -> None:
        if sweep.abandoned(plan):
            raise StopError("store_failed")

    try:
        await check()
        queries, skipped = queries_for(search)
    except StopError as stop:
        return stop.code
    except ValueError:
        return "unsupported_criteria"
    transport = runner.guest_http() if runner.guest_http is not None else None
    if transport is None:
        return "page_failed"  # no way out configured: no request at all
    done = _Run(planned=len(queries), places_skipped=skipped[:PLACES_LISTED])
    done.countries = list(queries[0].countries)
    criteria = dataclasses.replace(_criteria(search), contracts=frozenset())
    try:
        async with httpx.AsyncClient(
            transport=transport,
            headers=HEADERS,
            timeout=httpx.Timeout(TIMEOUT_S),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            http = Client(runner, client, key_host, deadline, check)
            try:
                html = await http.send("GET", f"{site}{KEY_PAGE}", headers={"Accept": "text/html"})
                key = public_key(html)
                if key is None:
                    return KEY_MISSING
                await _rounds(http, api, site, key, plan, queries, criteria, entry, done, progress)
            except StopError as stop:
                if stop.code != "request_cap":  # the cap ends the reading, not in error
                    return stop.code
            finally:
                done.requests = http.requests
    finally:
        _write(entry, done, plan)
    return "finding_persistence_pending" if done.found else None


def _criteria(search: SearchSection) -> feeds.Criteria:
    try:
        return feeds.Criteria.of(search)
    except feeds.FeedError:
        raise ValueError("unsupported_criteria") from None


async def _rounds(
    http: Client,
    api: str,
    site: str,
    key: str,
    plan: Any,
    queries: list[Query],
    criteria: feeds.Criteria,
    entry: dict[str, Any],
    done: _Run,
    progress: Callable[[], Awaitable[None]],
) -> None:
    """The pages in rounds: the first page of every search, then the next page of those that go
    on; each page's kept offers are stored before the next request."""
    seen: set[str] = set()
    active = list(queries)
    headers = {"X-API-KEY": key, "Content-Type": "application/json"}
    for page in range(PAGES_PER_QUERY):
        still: list[Query] = []
        for query in active:
            body = await http.send(
                "POST", f"{api}{SEARCH_PATH}", headers=headers,
                content=json.dumps(search_body(query, page)).encode(),
            )  # fmt: skip
            done.pages += 1
            entry["result_pages"] = done.pages
            done.searched.setdefault(query.title, None)
            try:
                offers, count = await sweep.parse_one(
                    getattr(plan, "parsing", None), parse_page, body
                )
            except ValueError:
                raise StopError("page_failed") from None
            batch = _kept(offers, site, seen, criteria, done)
            await _store(plan, entry, batch, done)
            read = (page + 1) * PAGE_SIZE
            if len(offers) >= PAGE_SIZE and (count is None or read < count):
                still.append(query)
            await progress()
        active = still
        if not active:
            break


def _kept(
    offers: list[dict[str, Any]],
    site: str,
    seen: set[str],
    criteria: feeds.Criteria,
    done: _Run,
) -> list[VieFinding]:
    at = now()
    kept: list[VieFinding] = []
    for offer in offers:
        found = finding_of(offer, site, at)
        if found is None:
            done.left_out["invalid"] = done.left_out.get("invalid", 0) + 1
            continue
        if found.external_id in seen:
            continue
        seen.add(found.external_id)
        reason = None
        if str(offer.get("missionType") or "VIE").upper() not in MISSION_TYPES:
            reason = "contract"
        else:
            reason = criteria.left_out(found, at, place_filter=False)
        if reason is not None:
            done.left_out[reason] = done.left_out.get(reason, 0) + 1
            continue
        kept.append(found)
    # Newest first: the API's order is only roughly by recency.
    kept.sort(key=lambda f: f.posted_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return kept


async def _store(plan: Any, entry: dict[str, Any], batch: list[VieFinding], done: _Run) -> None:
    if not batch:
        return
    commits = getattr(plan, "commits", None)
    if commits is None:
        done.found += len(batch)
        return
    for start in range(0, len(batch), BATCH):
        try:
            await commits.save(
                entry["source_id"], SITE_HOST, feeds.Batch(tuple(batch[start : start + BATCH]))
            )
        except Exception:
            raise StopError("store_failed") from None


def _write(entry: dict[str, Any], done: _Run, plan: Any) -> None:
    entry["result_pages"] = done.pages
    entry["requests"] = done.requests
    entry["searches"] = len(done.searched)
    entry["planned"] = done.planned
    entry["searched"] = [{"title": t, "place": None} for t in list(done.searched)[:SEARCHES_LISTED]]
    if done.countries:
        entry["countries"] = done.countries
    if done.places_skipped:
        entry["places_skipped"] = done.places_skipped
    if done.left_out:
        entry["left_out"] = dict(done.left_out)
    if getattr(plan, "commits", None) is None:
        entry["found"] = entry.get("found", 0) + done.found
