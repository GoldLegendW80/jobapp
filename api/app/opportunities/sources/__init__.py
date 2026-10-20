"""Freelance and project feeds of the sweep (opportunities O7, SPEC-opportunities "Freelance and
project platforms as sources"): public APIs and feeds whose terms allow an app to read them, read
over plain HTTP, no browser, no model.

The supported kinds (catalogue.py says why each is allowed, its attribution and the date of the
check): `remoteok` (remoteok.py, the public JSON API), `weworkremotely` (weworkremotely.py, the
RSS feed), `hackernews_freelance` and `hackernews_hiring` (hackernews.py, the official API: the
month's "Freelancer? Seeking freelancer?" or "Who is hiring?" thread of the `whoishiring`
account). Platforms whose terms forbid automated reading are not read here, ever: they are task
O7b's, behind its guard and the person's opt in.

After the boards, `FeedRunner` (the `feeds` hook of app/jobs/sweep.py's `run_sweep`) takes each
enabled feed of the person's `search.feeds` in turn:
1. Read. One GET per feed (the Hacker News kinds: the account, its newest submissions until the
   thread is found, then at most FEEDS_HN_MAX_COMMENTS top level comments, HN_CONCURRENCY at a
   time) through `FeedClient`: the app's User-Agent, FEEDS_TIMEOUT_S per request, retries of a
   429, a 5xx, a timeout or a connection error with backoff (ats.py's rules and codes), the body
   read up to MAX_BODY, redirects only to the source's own hosts over https.
2. Parse. The source module turns the answer into (company, posting) pairs, pure: title,
   company or client, location, the posting's own address on the source (an address off the
   source's host is refused and counted), plain text (HTML made plain, cut at TEXT_MAX), the
   published date, and `contract_type` Freelance when the source says it is a contract or a
   freelance mission. More than ITEMS_MAX items is `too_many`: nothing of that feed is kept.
3. Keep. The feed's own `terms`, when set, must match the title too. A title or company that
   the person's never apply rules match, folded as apply reads them (app/apply/guardrails.py:
   "Cybersecurity Engineer", "Ingénieur Cybersécurité", a soft hyphen inside "Security"), is
   never stored, so it is never `found`.
4. Store. The sweep's own store (sweep.store_postings, in one transaction per feed): the
   search's include, exclude, location and contract terms, the days window, duplicates (same
   address, same company, title and location, or for a feed same company and title: one row
   naming every source that found it), the score, the never apply rules, status `found`. The
   source is recorded as `remoteok`, `weworkremotely`, `hackernews:freelance` or
   `hackernews:hiring`; the card says "via Remote OK" and links to the posting's address there,
   as the sources ask.

What a feed reports in the sweep's `stats.sources` (one entry each, `kind` the feed's kind,
`error` None when it was read): the codes of ats.py (`not_found`, `blocked`, `rate_limited`,
`upstream_error`, `timeout`, `network`, `too_large`, `invalid_response`), `too_many`,
`store_failed` and `time_budget` (the sweep's time limit is near: not read). A failed feed never
stops the sweep or the next feed.

FEEDS_STUB_BASE_URL (tests only, refused in production) sends every feed to the fake feeds
server (tests/feeds_stub.py) at {base}/{source}/{the source's own path}. Nothing here logs a
posting's text, a profile value or an answer body.
"""

import asyncio
import html
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.apply import guardrails
from app.jobs import ats
from app.jobs import sweep as sweeps
from app.profile.schemas import Feed

logger = logging.getLogger("app.opportunities.sources")

USER_AGENT = "jobapp-sweep/1.0 (personal job search app; public posting APIs and feeds only)"
TIMEOUT_S = 20.0
ATTEMPTS = 3
BACKOFF_S = 1.0
RETRY_AFTER_MAX_S = 10.0
MAX_REDIRECTS = 2
MAX_BODY = 10 * 1024 * 1024
ITEMS_MAX = 1_000  # postings of one feed; more is `too_many`
TEXT_MAX = 20_000  # characters of one posting's text kept from a feed
TITLE_MAX = 300
NAME_MAX = 200
HN_CONCURRENCY = 8
HN_MAX_COMMENTS = 500
TIME_BUDGET_S = 30.0  # a feed is not started this close to the sweep's time limit
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

ERRORS = (
    "not_found",
    "blocked",
    "rate_limited",
    "upstream_error",
    "timeout",
    "network",
    "too_large",
    "invalid_response",
    "too_many",
    "store_failed",
    "time_budget",
)

# The `source` a feed's postings are recorded with, per kind.
SOURCES = {
    "remoteok": "remoteok",
    "weworkremotely": "weworkremotely",
    "hackernews_freelance": "hackernews:freelance",
    "hackernews_hiring": "hackernews:hiring",
}
NAMES = {
    "remoteok": "Remote OK",
    "weworkremotely": "We Work Remotely",
    "hackernews_freelance": "Hacker News, Freelancer? Seeking freelancer?",
    "hackernews_hiring": "Hacker News, Who is hiring?",
}
FREELANCE = "Freelance"


class FeedError(Exception):
    """A feed could not be read. `code` is one of ERRORS; `retryable` says whether another attempt
    may succeed, `retry_after` is the wait the source asked for (seconds), if any."""

    def __init__(
        self, code: str, *, retryable: bool = False, retry_after: float | None = None
    ) -> None:
        if code not in ERRORS:
            raise ValueError(code)
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


# --- text ---------------------------------------------------------------------------------------

_BREAKS = re.compile(r"<\s*(?:br|/p|p|li|/ul|/ol|/div|div|/h\d|h\d|/tr|hr)\b[^>]*>", re.IGNORECASE)
_DROPPED = re.compile(r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]*>")
_SPACES = re.compile("[ \t\r\f\v\u00a0]+")
_BLANKS = re.compile(r"\n{3,}")


def plain_text(value: object, limit: int = TEXT_MAX) -> str:
    """HTML or text made plain: scripts and styles dropped, paragraphs and line breaks kept as new
    lines, other tags removed, entities decoded, spaces collapsed, made storable, cut at
    `limit` characters."""
    if not isinstance(value, str):
        return ""
    text = _DROPPED.sub(" ", value[: limit * 4])
    text = _BREAKS.sub("\n", text)
    text = html.unescape(_TAG.sub(" ", text))
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    text = _BLANKS.sub("\n\n", "\n".join(lines)).strip()
    return ats.storable(text)[:limit]


def line(value: object, limit: int) -> str:
    """One line of plain text, at most `limit` characters."""
    return " ".join(plain_text(value, limit * 2).split())[:limit].strip()


def day_of(when: datetime | None) -> str:
    """The date in the sweep's time zone (YYYY-MM-DD), "" when unknown."""
    if when is None:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(sweeps.SWEEP_TZ).date().isoformat()


def from_epoch(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    try:
        return day_of(datetime.fromtimestamp(value, UTC))
    except (OverflowError, OSError, ValueError):
        return ""


def on_host(url: object, hosts: frozenset[str]) -> str | None:
    """The address when it is an https (or http) address on one of the source's hosts."""
    if not isinstance(url, str) or len(url) > 2_000:
        return None
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or parts.username or parts.password:
        return None
    if (parts.hostname or "").lower() not in hosts:
        return None
    return url


def remote_location(value: str) -> str:
    """The location of a posting on a remote only board: "Remote" first, then what it says."""
    value = value.strip()
    if not value:
        return "Remote"
    return value if re.search(r"\bremote\b", value, re.IGNORECASE) else f"Remote, {value}"


# --- fetching -----------------------------------------------------------------------------------


def _retry_after(value: str | None) -> float | None:
    if value and value.strip().isdigit():
        return float(value.strip())
    return None


@dataclass
class FeedClient:
    """GETs of the feeds over one httpx client. Use as an async context manager. `transport`
    replaces the network in tests; `stub_base_url` is FEEDS_STUB_BASE_URL."""

    stub_base_url: str | None = None
    transport: httpx.AsyncBaseTransport | None = None
    timeout_s: float = TIMEOUT_S
    attempts: int = ATTEMPTS
    backoff_s: float = BACKOFF_S
    max_body: int = MAX_BODY
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    async def __aenter__(self) -> "FeedClient":
        self._client = httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
            trust_env=False,
            limits=httpx.Limits(max_connections=HN_CONCURRENCY, max_keepalive_connections=4),
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def address(self, real: str, stub_prefix: str) -> tuple[str, frozenset[str]]:
        """The address to read for a source's real address, and the hosts a redirect may go to:
        the real one, or the stub's under {base}/{stub_prefix} in tests."""
        parts = urlsplit(real)
        if self.stub_base_url:
            url = f"{self.stub_base_url.rstrip('/')}/{stub_prefix}{parts.path}"
            if parts.query:
                url = f"{url}?{parts.query}"
            return url, frozenset({(urlsplit(url).hostname or "").lower()})
        host = (parts.hostname or "").lower()
        return real, frozenset({host, host.removeprefix("www."), f"www.{host}"})

    async def get(self, url: str, hosts: frozenset[str]) -> bytes:
        """The body of one GET, with the retries of the module docstring. Raises FeedError."""
        last: FeedError | None = None
        for attempt in range(self.attempts):
            if last is not None:
                wait = self.backoff_s * 2 ** (attempt - 1)
                if last.retry_after is not None:
                    wait = min(RETRY_AFTER_MAX_S, last.retry_after)
                await self.sleep(wait)
            try:
                return await self._follow(url, hosts)
            except FeedError as exc:
                if not exc.retryable:
                    raise
                last = exc
        raise FeedError(last.code if last is not None else "network")

    async def _follow(self, url: str, hosts: frozenset[str]) -> bytes:
        for _ in range(MAX_REDIRECTS + 1):
            status, location, body = await self._get(url)
            if status in (301, 302, 303, 307, 308):
                url = self._allowed(url, location, hosts)
                continue
            return body
        raise FeedError("upstream_error")

    @staticmethod
    def _allowed(url: str, location: str | None, hosts: frozenset[str]) -> str:
        if not location:
            raise FeedError("upstream_error")
        target = urljoin(url, location)
        parts, origin = urlsplit(target), urlsplit(url)
        if (
            (parts.hostname or "").lower() not in hosts
            or parts.scheme != origin.scheme
            or parts.username
            or parts.password
        ):
            raise FeedError("upstream_error")
        return target

    async def _get(self, url: str) -> tuple[int, str | None, bytes]:
        if self._client is None:
            raise RuntimeError("use FeedClient as an async context manager")
        try:
            async with self._client.stream("GET", url) as response:
                status = response.status_code
                if status in (301, 302, 303, 307, 308):
                    return status, response.headers.get("location"), b""
                if status == 404:
                    raise FeedError("not_found")
                if status in (401, 403):
                    raise FeedError("blocked")
                if status in RETRY_STATUSES:
                    raise FeedError(
                        "rate_limited" if status == 429 else "upstream_error",
                        retryable=True,
                        retry_after=_retry_after(response.headers.get("retry-after")),
                    )
                if status != 200:
                    raise FeedError("upstream_error")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > self.max_body:
                    raise FeedError("too_large")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_body:
                        raise FeedError("too_large")
                    chunks.append(chunk)
                return status, None, b"".join(chunks)
        except httpx.TimeoutException:
            raise FeedError("timeout", retryable=True) from None
        except httpx.TransportError:
            raise FeedError("network", retryable=True) from None


# --- the run ------------------------------------------------------------------------------------

Found = list[tuple[str, ats.Posting]]
Reader = Callable[[FeedClient, Feed], Awaitable[Found]]
StoreFound = Callable[[str, Sequence[tuple[str, ats.Posting]]], Awaitable[None]]


def readers(hn_max_comments: int = HN_MAX_COMMENTS) -> dict[str, Reader]:
    """The reader of each supported kind."""
    from app.opportunities.sources import hackernews, remoteok, weworkremotely

    async def hn_freelance(client: FeedClient, feed: Feed) -> Found:
        return await hackernews.read(client, "freelance", hn_max_comments)

    async def hn_hiring(client: FeedClient, feed: Feed) -> Found:
        return await hackernews.read(client, "hiring", hn_max_comments)

    return {
        "remoteok": remoteok.read,
        "weworkremotely": weworkremotely.read,
        "hackernews_freelance": hn_freelance,
        "hackernews_hiring": hn_hiring,
    }


def keep(plan: Any, feed: Feed, found: Found) -> tuple[Found, int]:
    """The postings of a feed that its own terms keep and the person's never apply rules, folded,
    do not exclude; and how many the rules excluded (module docstring, step 3)."""
    terms = sweeps.term_matcher(feed.terms.terms, feed.terms.advanced_regex)
    kept: Found = []
    excluded = 0
    for company, posting in found:
        if terms is not None and not terms.search(posting.title):
            continue
        if guardrails.matching_rule(plan.rules, company, posting.title) is not None:
            excluded += 1
            continue
        kept.append((company, posting))
    return kept, excluded


class FeedRunner:
    """The `feeds` hook of sweep.run_sweep (module docstring)."""

    def __init__(
        self,
        client_factory: Callable[[], FeedClient],
        read: dict[str, Reader] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client_factory = client_factory
        self.read = read if read is not None else readers()
        self.monotonic = monotonic

    async def __call__(
        self,
        plan: Any,
        stats: Any,
        store: StoreFound,
        progress: Callable[[], Awaitable[None]],
        deadline: float,
    ) -> None:
        async with self.client_factory() as client:
            for feed in plan.feeds:
                entry: dict[str, Any] = {
                    "kind": feed.kind,
                    "query": "",
                    "location": None,
                    "result_pages": 0,
                    "found": 0,
                    "postings": 0,
                    "excluded": 0,
                    "error": None,
                }
                stats.sources.append(entry)
                if self.monotonic() > deadline - TIME_BUDGET_S:
                    entry["error"] = "time_budget"
                    continue
                stats.current_board = NAMES.get(feed.kind, feed.kind)
                await progress()
                entry["error"] = await self._one(client, plan, stats, feed, entry, store)
                await progress()

    async def _one(
        self,
        client: FeedClient,
        plan: Any,
        stats: Any,
        feed: Feed,
        entry: dict[str, Any],
        store: StoreFound,
    ) -> str | None:
        reader = self.read.get(feed.kind)
        if reader is None:
            return "not_found"
        try:
            found = await reader(client, feed)
        except FeedError as exc:
            logger.warning("feed not read: kind=%s error=%s", feed.kind, exc.code)
            return exc.code
        except Exception as exc:  # a parser's bug or an answer of an unexpected shape
            logger.warning("feed not read: kind=%s error=%s", feed.kind, type(exc).__name__)
            return "invalid_response"
        if len(found) > ITEMS_MAX:
            logger.warning("feed not read: kind=%s error=too_many", feed.kind)
            return "too_many"
        kept, entry["excluded"] = keep(plan, feed, found)
        entry["found"] = len(kept)
        before = stats.new
        try:
            await store(SOURCES[feed.kind], kept)
        except Exception as exc:
            # The type only: a database error's message can hold the posting's values.
            logger.warning("feed not stored: kind=%s error=%s", feed.kind, type(exc).__name__)
            return "store_failed"
        entry["postings"] = stats.new - before
        return None


def runner_for(settings: Any) -> FeedRunner:
    """The worker's hook: FEEDS_TIMEOUT_S, FEEDS_HN_MAX_COMMENTS, FEEDS_STUB_BASE_URL, and the
    tests' fast timers (one attempt, short backoff) when TEST_FAST_TIMERS is on."""
    fast = bool(getattr(settings, "test_fast_timers", False))

    def client() -> FeedClient:
        return FeedClient(
            stub_base_url=settings.feeds_stub_base_url,
            timeout_s=2.0 if fast else settings.feeds_timeout_s,
            attempts=1 if fast else ATTEMPTS,
            backoff_s=0.1 if fast else BACKOFF_S,
        )

    return FeedRunner(client, readers(settings.feeds_hn_max_comments))


def parse_json(body: bytes) -> Any:
    """The JSON of an answer; FeedError `invalid_response` when it is not JSON."""
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise FeedError("invalid_response") from None
