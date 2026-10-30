"""Job feeds: official job APIs a sweep reads beside the ticked job websites (owner, 2026-10-30:
Arbeitnow, Himalayas and Adzuna, in place of Indeed, whose Cloudflare check blocks any automated
access and which has no official search API). SPEC-jobs.md, "Job feeds".

A feed is a `FeedSource` (arbeitnow.py, himalayas.py, adzuna.py): a name, the host it reads,
whether it needs a key, and `fetch(ctx, criteria)`, which reads a few pages of the API through
`Http` and returns normalised `Finding`s. Everything else is shared here:

- What a sweep reads. The person ticks feeds in Find jobs (`search.job_feeds`); the sweep freezes
  them with the websites (`sweep.freeze_scope`, one `stats.sources` entry each, mode "api") and
  `FeedRunner`, the sweep's feed hook, reads them one after the other before the websites.
- Politeness. One request at a time per feed, PAUSE_S apart, at most the feed's own request
  budget per sweep (a few), a User-Agent naming the app, FEED_TIME_S per feed and FEEDS_TIME_S
  for all the ticked feeds together, so the websites read after them keep most of the sweep's
  time. A 429 stops that feed for this sweep (`rate_limited`), whatever Retry-After says: the
  next sweep reads it again. Requests go straight to the transport, never through httpx's
  Client, whose INFO log line would carry the address, and Adzuna's key is in its query; they
  ask for an uncompressed answer (`Accept-Encoding: identity`) and a compressed one is still
  decoded.
- Time. A feed stopped by its time (`time_budget`) keeps what it read before: the sources add
  to the `Fetched` of their context as they read, so the cancellation loses nothing read.
- Filtering (`Criteria`), the same as the websites' after reading: a wanted title must match
  (the feeds' own keyword search is loose), no unwanted title or place may match, a wanted place
  must match when the person saved places and the feed is not already scoped to a place
  (`place_filter`), a place such as "everywhere", "worldwide" or "partout" meaning no place
  filter; a contract type outside the saved ones is left out (the feed's stated type, else the
  title's, as for the websites); a posting older than `search.days` is left out (an unknown date
  is kept). Never apply rules, the app's own against cybersecurity included, mark a stored row
  `skipped` with the rule as its note, as for every source (sweep.Commits).
- Storing. Kept findings go through the run's commits (sweep.Commits) under the feed's source
  entry: deduplicated by address, then by employer, title and location, against the person's
  rows and this sweep's; scored; the external id kept in the sweep event's evidence.
- Cursor (Arbeitnow). The entry records the feed's new cursor only when the run ended with no
  error at all: every page read, every batch stored and the commits settled. A refused or
  failed store, a time budget, a 429 or the page cap leave it unrecorded, so the next sweep
  reads back to the previous cursor again.
- Missing key. Adzuna without a stored key is skipped with the reason `no_key` and the outcome
  `not_attempted`, never an error of the sweep.

Nothing here logs a key, a posting text or an address with a query.
"""

import asyncio
import contextlib
import json
import logging
import re
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Protocol

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import sweep
from app.jobs.importer import kind
from app.jobs.models import ContractType, Sweep
from app.profile import matching
from app.profile.plain_terms import typed_words
from app.profile.schemas import SearchSection

logger = logging.getLogger("app.jobs.feeds")

USER_AGENT = "jobapp/1.0 (self-hosted personal job search; https://github.com/GoldLegendW80/jobapp)"
PAUSE_S = 3.0  # between two requests of one feed
TIMEOUT_S = 20.0  # one request
FEED_TIME_S = 120.0  # one feed in one sweep
FEEDS_TIME_S = 180.0  # all the ticked feeds of one sweep together (of SWEEP_TIMEOUT_S, 900)
BODY_MAX = 8_000_000  # bytes of one answer (an Arbeitnow page of 300 postings is about 2 MB)
TEXT_MAX = 50_000  # characters of one posting's text
QUERIES_MAX = 3  # saved titles searched by a feed with a keyword search, in saved order
BATCH = 20  # findings per commit
DEADLINE_MARGIN_S = 60.0  # what a feed leaves before the sweep's time limit

# The codes a feed's entry ends with (`error`, and `reason` once finished). The web words them
# (features/jobs/job-feeds.ts); the first group is the old feeds' (FEED_PROBLEMS).
ERRORS = (
    "rate_limited",
    "blocked",
    "not_found",
    "upstream_error",
    "timeout",
    "network",
    "too_large",
    "invalid_response",
    "store_failed",
    "time_budget",
    "no_key",
    "key_unreadable",
    "key_refused",
    "unsupported_criteria",
    "feed_failed",
    "page_cap",
)
# The reasons a refused store replaces: a store failure is worth more to the person.
STORE_OVERRIDES = (None, "page_cap")


class FeedError(Exception):
    def __init__(self, code: str) -> None:
        if code not in ERRORS:
            raise ValueError(code)
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Finding:
    """One posting a feed returned, as the sweep's store takes it (sweep._store_finding reads
    title, location, url, text, company, published_on, evidence, detail_read_at, and contract
    and work_mode when given)."""

    source: str
    external_id: str
    title: str
    company: str | None
    location: str
    url: str
    text: str
    posted_at: datetime | None = None
    remote: bool | None = None
    contract: ContractType | None = None
    read_at: datetime | None = None

    @property
    def published_on(self) -> str | None:
        return self.posted_at.date().isoformat() if self.posted_at else None

    @property
    def work_mode(self) -> str | None:
        return "remote" if self.remote else None

    @property
    def detail_read_at(self) -> str:
        return (self.read_at or datetime.now(UTC)).isoformat()

    @property
    def evidence(self) -> dict[str, str]:
        # The API's own id: what dedupes a feed against itself across runs is the address
        # derived from it; it is kept with the sweep event for the record.
        return {"api": self.source, "external_id": self.external_id[:200]}


@dataclass
class Batch:
    findings: tuple[Finding, ...]


@dataclass
class Fetched:
    """What one feed read: its findings, the searches it ran, its requests, why it stopped early
    (None when it read what it meant to) and its new cursor (Arbeitnow)."""

    findings: list[Finding] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)
    requests: int = 0
    error: str | None = None
    cursor: int | None = None
    queries_left_out: int = 0


# --- HTTP -----------------------------------------------------------------------------------------

Sleep = Callable[[float], Awaitable[None]]


class Http:
    """One feed's polite GETs: a pause before every request but the first, a budget, a size cap,
    JSON answers only. FeedError for anything else."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        *,
        budget: int,
        sleep: Sleep = asyncio.sleep,
        pause_s: float = PAUSE_S,
    ) -> None:
        self.transport, self.budget, self.sleep, self.pause_s = transport, budget, sleep, pause_s
        self.requests = 0

    @property
    def left(self) -> int:
        return self.budget - self.requests

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if self.requests >= self.budget:
            raise RuntimeError("request budget spent")  # a source bug: it checks `left` first
        if self.requests:
            await self.sleep(self.pause_s)
        self.requests += 1
        request = httpx.Request(
            "GET",
            url,
            params={k: v for k, v in (params or {}).items() if v is not None},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                # Read with the transport's raw bytes, so ask for no compression (a compressed
                # answer anyway is decoded below).
                "Accept-Encoding": "identity",
            },
            extensions={"timeout": httpx.Timeout(TIMEOUT_S).as_dict()},
        )
        try:
            response = await self.transport.handle_async_request(request)
        except httpx.TimeoutException:
            raise FeedError("timeout") from None
        except (httpx.HTTPError, OSError):
            raise FeedError("network") from None
        try:
            status = response.status_code
            if status == 429:
                raise FeedError("rate_limited")
            if status in (401, 403):
                raise FeedError("blocked")
            if status == 404:
                raise FeedError("not_found")
            if status != 200:
                raise FeedError("upstream_error")
            body = bytearray()
            try:
                encoded = response.headers.get("Content-Encoding", "identity").strip().lower()
                if response.is_stream_consumed:  # an answer already in memory
                    body += response.content
                else:
                    # Decoded when the server compressed it anyway; the cap counts decoded bytes.
                    chunks = (
                        response.aiter_raw() if encoded == "identity" else response.aiter_bytes()
                    )
                    async for chunk in chunks:
                        body += chunk
                        if len(body) > BODY_MAX:
                            break
                if len(body) > BODY_MAX:
                    raise FeedError("too_large")
            except httpx.TimeoutException:
                raise FeedError("timeout") from None
            except httpx.DecodingError:
                raise FeedError("invalid_response") from None
            except httpx.HTTPError:
                raise FeedError("network") from None
        finally:
            with contextlib.suppress(Exception):
                await response.aclose()
        try:
            return json.loads(bytes(body))
        except ValueError:
            raise FeedError("invalid_response") from None


# --- reading the APIs' values ---------------------------------------------------------------------


class _Text(HTMLParser):
    BLOCKS = frozenset(
        {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def plain_text(value: object, limit: int = TEXT_MAX) -> str:
    """The text of an API's HTML (or plain) value, lines trimmed, at most `limit` characters."""
    if not isinstance(value, str) or not value:
        return ""
    parser = _Text()
    parser.feed(value[: limit * 4])
    parser.close()
    lines = (
        re.sub(r"[ \t\r\f\v\xa0]+", " ", line).strip() for line in "".join(parser.parts).split("\n")
    )
    text = "\n".join(line for line in lines if line)
    return text[:limit]


def line(value: object, limit: int = 300) -> str:
    """A one line value: text only, whitespace folded, no markup, at most `limit` characters."""
    return " ".join(plain_text(value, limit * 2).split())[:limit]


def epoch(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def https_url(value: object, hosts: Sequence[str]) -> str | None:
    """`value` when it is an https address on one of `hosts` (or a subdomain), else None."""
    if not isinstance(value, str) or len(value) > 2000:
        return None
    if not value.startswith("https://"):
        return None
    try:
        host = httpx.URL(value).host.lower()
    except httpx.InvalidURL:
        return None
    if not any(host == h or host.endswith("." + h) for h in hosts):
        return None
    return value


# --- the person's criteria ------------------------------------------------------------------------


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


# A saved place that means no place (owner, 2026-10-28: read every criterion as the person meant
# it): exact words only, case and accents aside.
ANYWHERE = frozenset(
    {"everywhere", "anywhere", "worldwide", "world", "partout", "monde entier", "n'importe ou",
     "n importe ou", "nimporte ou", "any location", "international"}
)  # fmt: skip


def _pattern(terms: Any) -> re.Pattern[str] | None:
    words = getattr(terms, "terms", None)
    if not isinstance(words, list) or not words:
        return None
    return matching.compile_terms(words, getattr(terms, "advanced_regex", False) is True)


@dataclass(frozen=True)
class Criteria:
    """The frozen search of the sweep, as the feeds read it (module docstring, Filtering)."""

    queries: tuple[str, ...]
    queries_left_out: int
    wanted_titles: re.Pattern[str] | None
    unwanted_titles: re.Pattern[str] | None
    wanted_places: re.Pattern[str] | None  # None: no place filter
    unwanted_places: re.Pattern[str] | None
    contracts: frozenset[str]
    days: int

    @classmethod
    def of(cls, search: SearchSection) -> "Criteria":
        """FeedError("unsupported_criteria") when no wanted title gives a word to search: a feed
        never widens a search to everything."""
        words, _ = typed_words(search.title_include)
        wanted = _pattern(search.title_include)
        if not words or wanted is None:
            raise FeedError("unsupported_criteria")
        places = list(search.location.terms)
        anywhere = not places or any(_fold(p) in ANYWHERE for p in places)
        return cls(
            queries=tuple(words[:QUERIES_MAX]),
            queries_left_out=max(0, len(words) - QUERIES_MAX),
            wanted_titles=wanted,
            unwanted_titles=_pattern(search.title_exclude),
            wanted_places=None if anywhere else _pattern(search.location),
            unwanted_places=_pattern(search.location_exclude),
            contracts=frozenset(search.contract_types),
            days=search.days,
        )

    def left_out(self, finding: Finding, now: datetime, *, place_filter: bool) -> str | None:
        """Why `finding` is left out ("title", "unwanted", "place", "contract", "old"), None
        when it is kept."""
        if self.wanted_titles is None or not self.wanted_titles.search(finding.title):
            return "title"
        if self.unwanted_titles is not None and self.unwanted_titles.search(finding.title):
            return "unwanted"
        if self.unwanted_places is not None and self.unwanted_places.search(finding.location):
            return "unwanted"
        if (
            place_filter
            and self.wanted_places is not None
            and not self.wanted_places.search(finding.location)
        ):
            return "place"
        if self.contracts:
            contract = finding.contract or kind(finding.title, finding.location, finding.url)
            if contract.value not in self.contracts:
                return "contract"
        if finding.posted_at is not None and finding.posted_at < now - timedelta(days=self.days):
            return "old"
        return None


# --- a feed ---------------------------------------------------------------------------------------


@dataclass
class FeedContext:
    http: Http
    now: datetime
    key: dict[str, str] | None = None  # Adzuna's app_id and app_key
    cursor: int | None = None  # the feed's last cursor (Arbeitnow: newest created_at read)
    # What the feed has read so far: a source adds to it as it reads and returns it, so a feed
    # cancelled by its time budget keeps its findings (FeedRunner._read).
    fetched: Fetched = field(default_factory=Fetched)


class FeedSource(Protocol):
    name: str  # the job's `source` and the entry's `kind`
    host: str
    budget: int  # requests per sweep
    needs_key: bool
    place_filter: bool  # False when the feed is already scoped to a place (Himalayas: France)

    async def fetch(self, ctx: FeedContext, criteria: Criteria) -> Fetched: ...


def feeds() -> dict[str, FeedSource]:
    """The feeds in the order a sweep reads them."""
    from app.jobs.feeds.adzuna import Adzuna
    from app.jobs.feeds.arbeitnow import Arbeitnow
    from app.jobs.feeds.himalayas import Himalayas

    return {feed.name: feed for feed in (Arbeitnow(), Himalayas(), Adzuna())}


FEED_NAMES = ("arbeitnow", "himalayas", "adzuna")


def scope_entry(name: str) -> dict[str, Any]:
    """The frozen `stats.sources` entry of a ticked feed (sweep.freeze_scope)."""
    feed = feeds()[name]
    return {
        "kind": name,
        "mode": "api",
        "host": feed.host,
        "source_id": sweep._source_id("api", name),
    }


# --- the sweep's hook -----------------------------------------------------------------------------

KeyReader = Callable[[uuid.UUID, str], dict[str, str] | None]


class FeedRunner:
    """The sweep's feed hook (sweep.FeedHook): every ticked feed, in `feeds()` order."""

    def __init__(
        self,
        sessions: sessionmaker[Session] | None,
        *,
        keys: KeyReader | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        sources: dict[str, FeedSource] | None = None,
        feed_time_s: float = FEED_TIME_S,
        feeds_time_s: float = FEEDS_TIME_S,
    ) -> None:
        self.sessions, self.keys = sessions, keys
        self.transport, self.sleep, self.clock, self.monotonic = transport, sleep, clock, monotonic
        self.sources = sources
        self.feed_time_s, self.feeds_time_s = feed_time_s, feeds_time_s

    async def __call__(
        self,
        plan: Any,
        stats: Any,
        progress: Callable[[], Awaitable[None]],
        deadline: float,
    ) -> None:
        search: SearchSection | None = getattr(plan, "search", None)
        ticked = set(getattr(search, "job_feeds", None) or [])
        if search is None or not ticked:
            return
        sources = self.sources or feeds()
        transport = self.transport or httpx.AsyncHTTPTransport(retries=0)
        # All the feeds together stop at FEEDS_TIME_S (feeds_time_s) from now, well before the
        # sweep's own deadline: `_read` leaves DEADLINE_MARGIN_S before the deadline it is given.
        deadline = min(deadline, self.monotonic() + self.feeds_time_s + DEADLINE_MARGIN_S)
        try:
            for name, source in sources.items():
                if name in ticked:
                    await self._one(plan, stats, progress, deadline, search, source, transport)
        finally:
            if self.transport is None:
                await transport.aclose()

    async def _one(
        self,
        plan: Any,
        stats: Any,
        progress: Callable[[], Awaitable[None]],
        deadline: float,
        search: SearchSection,
        source: FeedSource,
        transport: httpx.AsyncBaseTransport,
    ) -> None:
        entry = sweep.bind_source(
            stats,
            "api",
            (source.name,),
            {
                **scope_entry(source.name),
                "query": "",
                "location": None,
                "result_pages": 0,
                "found": 0,
                "postings": 0,
                "searches": 0,
                "searched": [],
                "read": 0,
                "left_out": {},
                "error": None,
            },
        )
        stats.current_board = entry["source_id"]
        await progress()
        commits = getattr(plan, "commits", None)
        cursor: int | None = None
        try:
            entry["error"], cursor = await self._read(
                plan, entry, search, source, transport, deadline
            )
        except Exception:
            logger.exception("job feed failed", extra={"feed": source.name})
            entry["error"] = "feed_failed"
        # Always settled first; a store failure then replaces only a lesser reason.
        unsettled = commits is not None and not await commits.settle()
        if unsettled and entry["error"] in STORE_OVERRIDES:
            entry["error"] = "store_failed"
        # The cursor moves only once everything read was stored (module docstring, Cursor).
        if entry["error"] is None and cursor is not None:
            entry["cursor"] = cursor
        sweep.finish_source(entry)
        if entry["error"] == "no_key":
            entry["outcome"] = "not_attempted"  # skipped, never a failure (owner brief)
        elif entry["error"] == "page_cap":
            entry["outcome"] = "partial"  # pages were read, the window's end was not reached
        await progress()

    async def _read(
        self,
        plan: Any,
        entry: dict[str, Any],
        search: SearchSection,
        source: FeedSource,
        transport: httpx.AsyncBaseTransport,
        deadline: float,
    ) -> tuple[str | None, int | None]:
        """Read one feed and store what it keeps: the entry's error (None when it read what it
        meant to) and the cursor to record once the commits settled."""
        try:
            criteria = Criteria.of(search)
        except FeedError as error:
            return error.code, None
        key = None
        if source.needs_key:
            try:
                key = await asyncio.to_thread(self._key, plan.user_id, source.name)
            except Exception:
                logger.warning("job feed key unreadable", extra={"feed": source.name})
                return "key_unreadable", None
            if key is None:
                return "no_key", None
        cursor = await asyncio.to_thread(self._cursor, plan, source.name)
        now = self.clock()
        ctx = FeedContext(Http(transport, budget=source.budget, sleep=self.sleep), now, key, cursor)
        left = min(self.feed_time_s, deadline - self.monotonic() - DEADLINE_MARGIN_S)
        if left <= 0:
            return "time_budget", None
        try:
            async with asyncio.timeout(left):
                fetched = await source.fetch(ctx, criteria)
        except TimeoutError:
            # What was read before the time ran out is kept, never the cursor: the pages after
            # it were not read.
            fetched = ctx.fetched
            fetched.error, fetched.cursor = "time_budget", None
            fetched.requests = ctx.http.requests
        entry["result_pages"] = fetched.requests
        entry["searches"] = len(fetched.searched)
        entry["searched"] = [{"title": q, "place": None} for q in fetched.searched]
        entry["titles_left_out"] = fetched.queries_left_out
        cursor = fetched.cursor if fetched.error is None else None
        kept: list[Finding] = []
        seen: set[str] = set()
        left_out: dict[str, int] = {}
        for finding in fetched.findings:
            if finding.external_id in seen:
                continue  # one posting on two pages or under two searches
            seen.add(finding.external_id)
            reason = criteria.left_out(finding, now, place_filter=source.place_filter)
            if reason is None:
                kept.append(finding)
            else:
                left_out[reason] = left_out.get(reason, 0) + 1
        entry["read"] = len(seen)
        entry["left_out"] = left_out
        commits = getattr(plan, "commits", None)
        if commits is not None and kept:
            commits.admit(entry["source_id"])
            for first in range(0, len(kept), BATCH):
                try:
                    await commits.save(
                        entry["source_id"], source.name, Batch(tuple(kept[first : first + BATCH]))
                    )
                except sweep.StoreRefusedError:
                    return "store_failed", None
        return fetched.error, cursor

    def _key(self, user_id: uuid.UUID, name: str) -> dict[str, str] | None:
        return self.keys(user_id, name) if self.keys is not None else None

    def _cursor(self, plan: Any, name: str) -> int | None:
        """The feed's cursor of the person's latest sweep that recorded one (not this one)."""
        if self.sessions is None:
            return None
        with self.sessions() as db:
            rows = db.execute(
                sa.select(Sweep.stats)
                .where(Sweep.user_id == plan.user_id, Sweep.id != plan.sweep_id)
                .order_by(Sweep.started_at.desc())
                .limit(20)
            ).scalars()
            for stats in rows:
                for item in (stats or {}).get("sources") or []:
                    if not isinstance(item, dict) or item.get("kind") != name:
                        continue
                    value = item.get("cursor")
                    if type(value) is int:
                        return value
        return None
