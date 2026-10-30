"""LinkedIn searched through its public job search, the guest API (owner, 2026-10-30: "use api to
search but keep browser to apply", then "Both, switchable in setup"). SPEC-jobs, "LinkedIn's
public job search".

When LinkedIn is ticked in Find jobs and the person's `search.linkedin_search` is "api" (the
default), a sweep reads LinkedIn with plain HTTP requests to the endpoints LinkedIn's own signed
out job pages call, instead of the finder agent in the person's browser (mode `api`, no lease, no
model). "browser" keeps the finder agent (mode `llm_browser`, app/browser/sources/interactive.py)
unchanged. Easy Apply always uses the browser, whatever the mode: the LinkedIn checkbox still
gives its one permission (linkedin_guard), which this source checks too.

- Search: `GET <base>/jobs-guest/jobs/api/seeMoreJobPostings/search` returns an HTML fragment
  of up to 10 job cards (id, title, company, location, posted date). One search per saved title
  and place: no saved place, or a place that says no limit (finder.worldwide), searches
  `location=Worldwide&geoId=92000000`; any other place is sent as written, without a geoId.
  `f_TPR=r<days * 86400>` limits the age to the saved days. Contract types: `f_JT` with CDI as F
  (full time), CDD as T (temporary), Freelance as C (contract), Stage as I (internship), all the
  saved ones in one filter; Alternance has no code, so with it saved no `f_JT` is sent and the
  postings read are checked against the saved types (`contract_of`). VIE has no code either:
  with VIE saved each title is also searched as "<title> VIE", without `f_JT` and checked the
  same way; with VIE alone saved, only those searches run. Pages: `start=0, 10, 20`, at most
  PAGES_PER_SEARCH a search, in rounds (the first page of every search, then the second ...), a
  search ending at a short or a stale page.
- Filter before any detail request: the saved titles to include (a card whose title matches
  none is left out: LinkedIn's relevance is loose), the unwanted titles and places, the never
  apply rules with the app's own rule against cybersecurity, and the person's postings (same
  LinkedIn id or address: counted found and duplicate, never fetched again).
- Detail: `GET <base>/jobs-guest/jobs/api/jobPosting/<id>`, only for a new card that passed the
  filter, at most DETAILS_PER_PAGE after each page and the rest at the end while the budget
  holds: the description and the criteria (seniority, employment type, job function,
  industries) become the posting's text, so the normal scoring applies; the employment type
  gives the contract type when the title says none (`contract_of`). The posting is stored as
  `https://www.linkedin.com/jobs/view/<id>/` through the run's commits (sweep.Commits), source
  `www.linkedin.com`, as the browser's LinkedIn postings are.
- Pace: one request of LinkedIn's host at a time across people and workers, at least
  PAUSE_S[0] seconds after the previous one of that host ended, browser pages included (the
  shared per host pacer, RedisPacer, key `www.linkedin.com`). A proof of concept of 2026-10-30
  got HTTP 429 for a request 4 s after another, and none at 8 s between pages and 6 s between
  postings. At most REQUESTS_MAX requests a sweep, and the source's share of the sweep's time.
- Stop: HTTP 429 or 999, or a redirect to LinkedIn's sign in or security pages, ends the source
  at once `rate_limited`, with what was stored kept; no retry. It records no LinkedIn
  checkpoint: the calls carry no account, so the 7 day pause of the person's account (and of
  Easy Apply) is not started by an anonymous refusal. Any other unexpected answer ends it
  `page_failed`; a posting gone (404) is skipped.
- Permission, kept conservative: the LinkedIn switch, its accepted sentence and the 7 day pause
  (platform_policy, linkedin_guard) are checked before every request, as before every browser
  page, although the anonymous calls cannot harm the account.

LinkedIn's User Agreement forbids automated access, these endpoints included; the owner accepted
that risk in the LinkedIn checkbox. These requests send no cookie and no account: the worst case
is a block of the Jetson's address, not a restriction of the person's profile.

BROWSER_SOURCES_BASE_URL (tests only) sends every request to `<base>/linkedin/...` and stores
postings there, never on the real host; without an HTTP transport (Runner.guest_http) the source
makes no request and ends `page_failed`.
"""

from __future__ import annotations

import copy
import logging
import re
import unicodedata
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.browser import finder
from app.browser.leases import StoreUnavailableError
from app.browser.sources import PACE_POLL_S, linkedin
from app.jobs import sweep
from app.jobs.importer import kind
from app.jobs.models import ContractType
from app.jobs.never_apply import CYBER_RULE
from app.jobs.schemas import SEARCHES_LISTED
from app.profile import matching
from app.profile.plain_terms import typed_words

if TYPE_CHECKING:
    from app.browser.sources import Runner
    from app.profile.schemas import InteractiveSite, SearchSection

logger = logging.getLogger("app.browser.sources.linkedin_guest")

MODE = "api"
KIND = "linkedin"
BASE_URL = "https://www.linkedin.com"
API_PATH = "/jobs-guest/jobs/api"
WORLDWIDE_LOCATION, WORLDWIDE_GEO_ID = "Worldwide", "92000000"
PAUSE_S = (8.0, 12.0)  # between two requests of LinkedIn's host (module docstring, Pace)
PAGES_PER_SEARCH = 3
PAGE_SIZE = 10  # cards LinkedIn gives a page
DETAILS_PER_PAGE = 5
REQUESTS_MAX = 30
SEARCHES_MAX = 30  # searches built from the saved titles and places, in saved order
CARDS_MAX = 25  # cards read from one page, whatever it holds
BODY_MAX = 2_000_000  # bytes read of one answer
TIMEOUT_S = 20.0
REQUEST_S = 30.0  # a request is not started this close to the source's time limit
TEXT_MAX = finder.TEXT_MAX
FIELD_MAX = finder.TITLE_MAX
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# LinkedIn's f_JT codes of the saved contract types (module docstring); Alternance and VIE have
# none.
JOB_TYPES = {"CDI": "F", "CDD": "T", "Freelance": "C", "Stage": "I"}
# The contract type LinkedIn's "Employment type" criterion states, when the title states none.
EMPLOYMENT = {
    "internship": ContractType.Stage,
    "temporary": ContractType.CDD,
    "contract": ContractType.Freelance,
}
STOPPED = frozenset({429, 999})
WALL_SEGMENTS = frozenset({"authwall", "login", "uas", "checkpoint", "signup"})
_JOB_ID = re.compile(r"urn:li:jobPosting:([0-9]{1,24})$")
_VIEW = re.compile(r"/jobs/view/(?:[^/?#]*-)?([0-9]{1,24})/?$")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class StopError(Exception):
    """The source ends with this code (sweep's `reason`)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- the searches --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Query:
    """One search: the keywords, the saved place it stands for (None with no place saved), what
    LinkedIn's `location` and `geoId` get, the `f_JT` filter, and whether the postings read are
    checked against the saved contract types."""

    keywords: str
    place: str | None
    location: str
    geo_id: str | None = None
    job_types: str | None = None
    check_contract: bool = False
    title: str = ""  # the saved title it searches (the summary names it)


def queries_for(search: SearchSection) -> list[Query]:
    """The searches of `search` (module docstring), at most SEARCHES_MAX; ValueError
    (`unsupported_criteria`) when no saved title gives a word to type."""
    titles, _ = typed_words(search.title_include)
    if not titles:
        raise ValueError("unsupported_criteria")
    places, _ = typed_words(search.location)
    spots: list[tuple[str | None, str, str | None]] = []
    if not places or any(finder.worldwide(place) for place in places):
        worldwide = next((p for p in places if finder.worldwide(p)), None)
        spots.append((worldwide, WORLDWIDE_LOCATION, WORLDWIDE_GEO_ID))
    spots.extend((place, place, None) for place in places if not finder.worldwide(place))
    saved = set(search.contract_types)
    others = saved - {"VIE"}
    native = bool(others) and others <= set(JOB_TYPES)
    job_types = ",".join(sorted({JOB_TYPES[c] for c in others})) if native else None
    found: list[Query] = []
    for title in titles:
        for place, location, geo_id in spots:
            if others or not saved:
                found.append(
                    Query(
                        title,
                        place,
                        location,
                        geo_id,
                        job_types,
                        bool(others) and not native,
                        title,
                    )
                )
            if "VIE" in saved:
                found.append(Query(f"{title} VIE", place, location, geo_id, None, True, title))
    return found[:SEARCHES_MAX]


def search_url(base: str, query: Query, days: int, start: int) -> str:
    params: dict[str, str] = {"keywords": query.keywords, "location": query.location}
    if query.geo_id:
        params["geoId"] = query.geo_id
    params["f_TPR"] = f"r{days * 86400}"
    if query.job_types:
        params["f_JT"] = query.job_types
    params["start"] = str(start)
    return f"{base}{API_PATH}/seeMoreJobPostings/search?{urlencode(params)}"


def posting_url(base: str, job_id: str) -> str:
    return f"{base}{API_PATH}/jobPosting/{job_id}"


def view_url(base: str, job_id: str) -> str:
    """The posting's address as the app stores it (and Easy Apply opens)."""
    return f"{base}/jobs/view/{job_id}/"


# --- the HTML ------------------------------------------------------------------------------------

VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
BLOCKS = frozenset(
    {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section", "tr"}
)
NODES_MAX = 50_000


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list[_Node | str] = field(default_factory=list)

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def walk(self) -> list[_Node]:
        found: list[_Node] = []
        stack: list[_Node] = [self]
        while stack:
            node = stack.pop()
            found.append(node)
            stack.extend(reversed([c for c in node.children if isinstance(c, _Node)]))
        return found

    def first(self, cls: str, tag: str | None = None) -> _Node | None:
        return next(
            (n for n in self.walk() if cls in n.classes() and (tag is None or n.tag == tag)),
            None,
        )

    def text(self, blocks: bool = False) -> str:
        """The text below, whitespace collapsed; with `blocks`, one line per block element."""
        parts: list[str] = []
        stack: list[_Node | str] = [self]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                parts.append(item)
                continue
            if blocks and item.tag in BLOCKS:
                parts.append("\n")
            stack.extend(reversed(item.children))
        if not blocks:
            return " ".join("".join(parts).split())
        lines = (" ".join(line.split()) for line in "".join(parts).split("\n"))
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class _Tree(HTMLParser):
    """A forgiving tree of an HTML fragment: an end tag closes up to its open element, a stray
    one is ignored, and parsing stops at NODES_MAX elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.count >= NODES_MAX:
            return
        self.count += 1
        node = _Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack[-1].tag == tag and len(self.stack) > 1:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self.count < NODES_MAX:
            self.stack[-1].children.append(data)


def _tree(html: str) -> _Node:
    parser = _Tree()
    parser.feed(html)
    parser.close()
    return parser.root


def _clean(text: str, limit: int = FIELD_MAX) -> str:
    """One line, no control or format character, at most `limit` characters ("" past it)."""
    text = "".join(
        ch for ch in unicodedata.normalize("NFC", text) if unicodedata.category(ch)[0] != "C"
    )
    text = " ".join(text.split())
    return text if len(text) <= limit else ""


@dataclass(frozen=True)
class Card:
    job_id: str
    title: str
    company: str
    location: str
    published: str  # YYYY-MM-DD, "" when the card states no valid date


def parse_cards(html: str) -> list[Card]:
    """The job cards of a search page, at most CARDS_MAX, each once; a card without its LinkedIn
    id or a title is left out."""
    root = _tree(html)
    cards: list[Card] = []
    seen: set[str] = set()
    for node in root.walk():
        found = _JOB_ID.match(node.attrs.get("data-entity-urn", ""))
        if found is None or found.group(1) in seen:
            continue
        seen.add(found.group(1))
        title_node = node.first("base-search-card__title") or node.first("sr-only")
        title = _clean(title_node.text()) if title_node else ""
        if not title:
            continue
        company = node.first("base-search-card__subtitle")
        place = node.first("job-search-card__location")
        time = next((n for n in node.walk() if n.tag == "time"), None)
        published = time.attrs.get("datetime", "") if time else ""
        cards.append(
            Card(
                found.group(1),
                title,
                _clean(company.text()) if company else "",
                _clean(place.text()) if place else "",
                published if _valid_date(published) else "",
            )
        )
        if len(cards) >= CARDS_MAX:
            break
    return cards


def _valid_date(value: str) -> bool:
    if not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def looks_like_cards(html: str) -> bool:
    """Whether a page that gave no card still holds list items (a changed layout, not an empty
    result)."""
    return "<li" in html


@dataclass(frozen=True)
class Detail:
    description: str
    criteria: dict[str, str]
    title: str = ""
    company: str = ""
    location: str = ""

    def text(self) -> str:
        """The posting's text as the app stores and scores it: the description, then the
        criteria, one per line."""
        lines = [f"{name}: {value}" for name, value in self.criteria.items()]
        return "\n\n".join(part for part in (self.description, "\n".join(lines)) if part)[:TEXT_MAX]


def parse_posting(html: str) -> Detail | None:
    """The posting page's description and criteria, None when it holds no description."""
    root = _tree(html)
    markup = root.first("show-more-less-html__markup")
    description = markup.text(blocks=True) if markup else ""
    description = "".join(
        ch
        for ch in unicodedata.normalize("NFC", description)
        if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
    )[:TEXT_MAX]
    if not description.strip():
        return None
    criteria: dict[str, str] = {}
    for item in root.walk():
        if "description__job-criteria-item" not in item.classes():
            continue
        name = item.first("description__job-criteria-subheader")
        value = item.first("description__job-criteria-text")
        if name and value and _clean(name.text()) and _clean(value.text()):
            criteria.setdefault(_clean(name.text()), _clean(value.text()))
        if len(criteria) >= 10:
            break
    title = root.first("top-card-layout__title")
    company = root.first("topcard__org-name-link")
    location = next(
        (
            n
            for n in root.walk()
            if {"topcard__flavor", "topcard__flavor--bullet"} <= n.classes()
            and "num-applicants__caption" not in n.classes()
        ),
        None,
    )
    return Detail(
        description,
        criteria,
        _clean(title.text()) if title else "",
        _clean(company.text()) if company else "",
        _clean(location.text()) if location else "",
    )


def contract_of(title: str, location: str, url: str, criteria: dict[str, str]) -> ContractType:
    """The contract type as a new job row takes it (sweep._new_job): the title, location and
    address first (importer.kind), else LinkedIn's employment type."""
    read = kind(title, location, url)
    if read is not ContractType.CDI:
        return read
    stated = next((v for k, v in criteria.items() if k.casefold() == "employment type"), "")
    return EMPLOYMENT.get(stated.casefold(), read)


# --- what is stored ------------------------------------------------------------------------------


@dataclass(frozen=True)
class GuestFinding:
    """A posting read through the guest API, as sweep.Commits stores it (the shape of
    finder.VerifiedFinding, plus the contract type the posting states)."""

    title: str
    location: str
    url: str
    text: str
    company: str | None
    published_on: str | None
    evidence: dict[str, str]
    detail_read_at: str
    contract: ContractType | None = None


@dataclass(frozen=True)
class _Batch:
    findings: tuple[GuestFinding, ...]


# --- the HTTP client -----------------------------------------------------------------------------


class Guest:
    """LinkedIn's guest endpoints, one paced request at a time (module docstring: Pace, Stop)."""

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
        self.requests = 0

    async def get(self, url: str) -> tuple[int, str]:
        """The answer's status and body (200), or its status with no body (400, 404). Raises
        StopError for a refusal, a wall, any other answer, the request cap or the time limit."""
        if self.requests >= REQUESTS_MAX:
            raise StopError("request_cap")
        await self.check()
        runner = self.runner
        pause, holder = runner.rng.uniform(*PAUSE_S), uuid.uuid4().hex
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
            await self.check()  # the pause may have outlasted the permission
            self.requests += 1
            return await self._fetch(url)
        except StoreUnavailableError:
            raise StopError("page_failed") from None
        finally:
            # A failed or cancelled request gives the host back too; a store that refuses
            # leaves the hold to end after PACE_HOLD_S.
            try:
                await runner.pacer.release(self.key, holder)
            except StoreUnavailableError:
                logger.warning("linkedin guest pace release failed")

    async def _fetch(self, url: str) -> tuple[int, str]:
        self.client.cookies.clear()  # no cookie LinkedIn set is ever sent back
        try:
            async with self.client.stream("GET", url) as answer:
                status = answer.status_code
                if status in STOPPED:
                    raise StopError("rate_limited")
                if 300 <= status < 400:
                    target = urlsplit(answer.headers.get("location", ""))
                    segments = {s.casefold() for s in target.path.split("/") if s}
                    raise StopError("rate_limited" if segments & WALL_SEGMENTS else "page_failed")
                if status in (400, 404):
                    return status, ""
                if status != 200:
                    raise StopError("page_failed")
                body = bytearray()
                async for chunk in answer.aiter_bytes():
                    body += chunk
                    if len(body) > BODY_MAX:
                        raise StopError("page_failed")
        except httpx.HTTPError:
            raise StopError("page_failed") from None
        return 200, body.decode("utf-8", "replace")


# --- the source ----------------------------------------------------------------------------------


def by_api(plan: Any, site: InteractiveSite) -> bool:
    """Whether this LinkedIn address is searched through the guest API: as the sweep's frozen
    scope says (sweep.freeze_scope wrote the mode), else as the person's setting."""
    if site.effective_platform != KIND:
        return False
    scope = getattr(plan, "search_scope", None)
    if isinstance(scope, dict):
        for frozen in scope.get("sources") or []:
            if isinstance(frozen, dict) and frozen.get("url") == site.url:
                return frozen.get("mode") == MODE
    search = getattr(plan, "search", None)
    return sweep.linkedin_mode(search) == MODE


def _known_ids(plan: Any, host: str) -> set[str]:
    """The LinkedIn ids of the person's postings (sweep.Known's addresses)."""
    known = getattr(plan, "known", None)
    ids: set[str] = set()
    for url in getattr(known, "urls", ()) or ():
        parts = urlsplit(url)
        name = parts.hostname or ""
        if not (name == host or name == "linkedin.com" or name.endswith(".linkedin.com")):
            continue
        found = _VIEW.search(parts.path)
        if found:
            ids.add(found.group(1))
    return ids


@dataclass
class _Run:
    """What the source read: its counters for the summary."""

    pages: int = 0
    details: int = 0
    searches: dict[tuple[str, str | None], None] = field(default_factory=dict)
    unwanted: int = 0
    other_contract: int = 0
    duplicates: int = 0
    found: int = 0  # findings counted without the run's commits


async def run(
    runner: Runner,
    plan: Any,
    stats: sweep.Stats,
    progress: Callable[[], Awaitable[None]],
    deadline: float,
) -> tuple[Any, str | None]:
    """Search every LinkedIn address in API mode (the first one runs the search, any other one
    ends `linkedin_searched_once`), then the plan the browser's websites run with: the same,
    without those addresses. The second value is the code that stops the later websites (a
    commit that did not settle), else None."""
    search = getattr(plan, "search", None)
    if search is None:
        return plan, None
    sites = [s for s in search.interactive_sites if s.enabled]
    api = [s for s in sites if by_api(plan, s)]
    if not api:
        return plan, None
    stop: str | None = None
    for index, site in enumerate(api):
        entry = sweep.bind_source(
            stats,
            MODE,
            (site.url,),
            {
                "source_id": sweep._source_id(MODE, site.url),
                "mode": MODE,
                "kind": KIND,
                "host": linkedin.host(runner.base_url),
                "result_pages": 0,
                "found": 0,
                "postings": 0,
                "error": None,
            },
        )
        if index:
            entry["error"] = "linkedin_searched_once"
        else:
            stats.current_board = entry["source_id"]
            await progress()
            now = runner.monotonic()
            share = now + max(0.0, deadline - now) / (len(sites) - len(api) + 1)
            commits = getattr(plan, "commits", None)
            if commits is not None:
                commits.admit(entry["source_id"])
            try:
                entry["error"] = await _search(
                    runner, plan, search, site, entry, stats, progress, min(deadline, share)
                )
            except Exception:
                logger.exception("linkedin guest search failed")
                entry["error"] = "page_failed"
            if commits is not None and not await commits.settle():
                entry["error"] = entry["error"] or "store_failed"
                stop = "store_failed"
        sweep.finish_source(entry)
        await progress()
    rest = copy.copy(plan)
    rest.search = search.model_copy(
        update={"interactive_sites": [s for s in search.interactive_sites if s not in api]}
    )
    return rest, stop


async def _search(
    runner: Runner,
    plan: Any,
    search: SearchSection,
    site: InteractiveSite,
    entry: dict[str, Any],
    stats: sweep.Stats,
    progress: Callable[[], Awaitable[None]],
    deadline: float,
) -> str | None:
    from app.browser import platform_policy
    from app.browser.sources.interactive import SourceContext

    base = (
        f"{runner.base_url.rstrip('/')}/linkedin" if runner.base_url else BASE_URL
    )  # tests only: the fixture server
    host = linkedin.host(runner.base_url)
    context = SourceContext(
        str(plan.user_id),
        str(plan.sweep_id) if getattr(plan, "sweep_id", None) else None,
        entry["source_id"],
        KIND,
        f"{base}/",
    )
    policy = runner.interactive_policy

    async def check() -> None:
        if policy is None or context.sweep_id is None:
            raise StopError("permission_unavailable")
        code = await policy.check(context)
        if code is not None:
            raise StopError(code)

    try:
        await check()  # the permission first, before anything else is decided
    except StopError as stop:
        return stop.code
    try:
        queries = queries_for(search)
    except ValueError:
        return "unsupported_criteria"
    transport = runner.guest_http() if runner.guest_http is not None else None
    if transport is None:
        return "page_failed"  # no way out configured: no request at all
    done = _Run()
    try:
        async with httpx.AsyncClient(
            transport=transport,
            headers=HEADERS,
            timeout=httpx.Timeout(TIMEOUT_S),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            guest = Guest(
                runner,
                client,
                platform_policy.pace_key(KIND, host, runner.base_url),
                deadline,
                check,
            )
            try:
                await _rounds(guest, base, plan, search, queries, entry, stats, progress, done)
            except StopError as stop:
                return None if stop.code == "request_cap" else stop.code
            finally:
                entry["requests"] = guest.requests
    finally:
        _write(entry, done, plan)
    return "finding_persistence_pending" if done.found else None


async def _rounds(
    guest: Guest,
    base: str,
    plan: Any,
    search: SearchSection,
    queries: list[Query],
    entry: dict[str, Any],
    stats: sweep.Stats,
    progress: Callable[[], Awaitable[None]],
    done: _Run,
) -> None:
    """The pages in rounds, the details of new cards after each page, the rest at the end."""
    host = urlsplit(base).hostname or linkedin.HOST
    known = _known_ids(plan, host)
    seen: set[str] = set()
    wanted = _Wanted(search, getattr(plan, "rules", None))
    pending: deque[tuple[Card, Query]] = deque()
    active = list(queries)
    for page in range(PAGES_PER_SEARCH):
        still: list[Query] = []
        for query in active:
            status, body = await guest.get(search_url(base, query, search.days, page * PAGE_SIZE))
            done.pages += 1
            entry["result_pages"] = done.pages
            done.searches.setdefault((query.title, query.place), None)
            cards = parse_cards(body) if status == 200 else []
            if status == 200 and not cards and page == 0 and looks_like_cards(body):
                raise StopError("layout_changed")
            new = 0
            for card in cards:
                if card.job_id in seen:
                    continue
                seen.add(card.job_id)
                new += 1
                if not wanted.keep(card):
                    done.unwanted += 1
                elif card.job_id in known:
                    _duplicate(stats, entry, done)
                else:
                    pending.append((card, query))
            if len(cards) >= PAGE_SIZE and new:
                still.append(query)
            await _details(guest, base, plan, search, pending, entry, done, DETAILS_PER_PAGE)
            await progress()
        active = still
        if not active:
            break
    await _details(guest, base, plan, search, pending, entry, done, len(pending))


def _duplicate(stats: sweep.Stats, entry: dict[str, Any], done: _Run) -> None:
    """A card of a posting the person has: found, a duplicate, never fetched again."""
    done.duplicates += 1
    entry["found"] = entry.get("found", 0) + 1
    entry["duplicates"] = entry.get("duplicates", 0) + 1
    stats.seen += 1
    stats.matched += 1
    stats.duplicates += 1


class _Wanted:
    """The saved titles to include, the unwanted titles and places, and the never apply rules
    (the app's own rule against cybersecurity added), read on a card before any detail."""

    def __init__(self, search: SearchSection, rules: Any) -> None:
        def pattern(terms: Any) -> re.Pattern[str] | None:
            return matching.compile_terms(list(terms.terms), terms.advanced_regex)

        self.include = pattern(search.title_include)
        self.titles = pattern(search.title_exclude)
        self.places = pattern(search.location_exclude)
        rules = list(rules) if rules is not None else sweep.never_apply_rules(search)
        self.rules = [*rules, CYBER_RULE]

    def keep(self, card: Card) -> bool:
        if self.include is not None and not self.include.search(card.title):
            return False
        if self.titles is not None and self.titles.search(card.title):
            return False
        if self.places is not None and self.places.search(card.location):
            return False
        return not any(rule.matches(card.company, card.title) for rule in self.rules)


async def _details(
    guest: Guest,
    base: str,
    plan: Any,
    search: SearchSection,
    pending: deque[tuple[Card, Query]],
    entry: dict[str, Any],
    done: _Run,
    count: int,
) -> None:
    """Fetch up to `count` pending postings and store them, one batch of at most
    finder.VERIFIED_BATCH_MAX at a time."""
    batch: list[GuestFinding] = []
    saved = set(search.contract_types)
    try:
        for _ in range(min(count, len(pending))):
            card, query = pending.popleft()
            status, body = await guest.get(posting_url(base, card.job_id))
            if status != 200:
                continue  # the posting is gone
            detail = parse_posting(body)
            if detail is None:
                continue
            done.details += 1
            url = view_url(base, card.job_id)
            location = card.location or detail.location
            contract = contract_of(card.title, location, url, detail.criteria)
            if query.check_contract and saved and contract.value not in saved:
                done.other_contract += 1
                continue
            batch.append(_finding(card, detail, url, location, contract))
            if len(batch) >= finder.VERIFIED_BATCH_MAX:
                await _store(plan, entry, batch, done)
                batch = []
    finally:
        if batch:
            await _store(plan, entry, batch, done)


def _finding(card: Card, detail: Detail, url: str, location: str, contract: ContractType) -> Any:
    company = card.company or detail.company
    evidence = {
        "title": card.title,
        **({"company": company} if company else {}),
        **({"published_on": card.published} if card.published else {}),
        **({"location": location} if location else {}),
        "read_through": "linkedin_guest_api",
    }
    return GuestFinding(
        card.title,
        location,
        url,
        detail.text(),
        company or None,
        card.published or None,
        evidence,
        datetime.now(UTC).isoformat(),
        contract,
    )


async def _store(plan: Any, entry: dict[str, Any], batch: list[GuestFinding], done: _Run) -> None:
    commits = getattr(plan, "commits", None)
    if commits is None:
        done.found += len(batch)
        return
    try:
        await commits.save(entry["source_id"], entry.get("host") or "", _Batch(tuple(batch)))
    except Exception:
        raise StopError("store_failed") from None


def _write(entry: dict[str, Any], done: _Run, plan: Any) -> None:
    entry["result_pages"] = done.pages
    entry["details"] = done.details
    entry["searches"] = len(done.searches)
    entry["searched"] = [
        {"title": title, "place": place} for title, place in list(done.searches)[:SEARCHES_LISTED]
    ]
    if done.unwanted:
        entry["unwanted"] = done.unwanted
    if done.other_contract:
        entry["other_contract"] = done.other_contract
    if getattr(plan, "commits", None) is None:
        entry["found"] = entry.get("found", 0) + done.found
