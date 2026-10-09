"""Company boards on Ashby, Greenhouse and Lever: fetch one board and parse its postings exactly as
engine/jobs.py `fetch` does.

Parsing (pure functions, `parse(ats, payload, tz)`), field for field the engine's tuple
(title, location, url, published, text), plus a remote flag the engine did not keep:
- ashby: GET https://api.ashbyhq.com/posting-api/job-board/{org} -> {"jobs": [...]}. Postings
  with `isListed: false` are left out. location = the primary location, then every secondary
  location, then "Remote" when `isRemote`, empty ones dropped, duplicates removed in order,
  joined with ", ". url = `jobUrl`; published = the first 10 characters of `publishedAt`;
  text = `descriptionPlain`. remote = `isRemote` is true.
- greenhouse: GET https://boards-api.greenhouse.io/v1/boards/{org}/jobs?content=true ->
  {"jobs": [...]}. location = `location.name`; url = `absolute_url`; published = the first 10
  characters of `first_published`, else of `updated_at`; text = `content` (HTML escaped twice)
  unescaped, its tags replaced by spaces, unescaped again, whitespace collapsed to single spaces.
  remote = the location names "remote".
- lever: GET https://api.lever.co/v0/postings/{org}?mode=json -> [...]. title = `text`;
  location = `categories.location`; url = `hostedUrl`; published = the date of `createdAt`
  (milliseconds) in the sweep's time zone (the engine used the Mac's, Europe/Paris); text =
  `descriptionPlain`. remote = `workplaceType` is "remote" or the location names "remote".

Where the engine would stop the whole board on one odd posting (a KeyError), this module skips
that posting and counts it (`BoardResult.invalid`): a posting without a title or a URL, or that
is not an object. Text is made storable: NUL characters and lone surrogates (which Postgres and
psycopg refuse) are removed; nothing else changes.

Fetching (`AtsClient.fetch_board`): one GET to the fixed host of the ATS, the org percent-encoded
into one path segment (SSRF: the same rules as app/profile/boards.py, whose HOSTS this reuses),
a User-Agent naming the app, redirects followed only to the same host over https, the body read
up to MAX_BODY. Retries: a 429 or a 5xx answer, a connection error or a timeout is retried up to
ATTEMPTS times in all, waiting BACKOFF_S x 2^n (or the Retry-After the ATS sends, at most
RETRY_AFTER_MAX_S) between attempts. Failures raise BoardError with a short code: not_found,
blocked (401, 403), rate_limited (429 after the retries), upstream_error (5xx after the retries,
another status, a refused redirect), timeout, network, too_large, invalid_response (not JSON, or
not the shape the ATS documents).

Test-only override, shared with the board check (app/profile/boards.py): `stub_base_url`
(Settings.ats_base_url, refused in production by the config) sends the boards to a stub at
{stub_base_url}/{ats}/{the ATS's own path}; with `stub_org_prefix`
(Settings.ats_base_url_org_prefix) only the orgs starting with it go there and every other org
still goes to its real ATS, so one stack can sweep a fake board and real boards side by side.
"""

import asyncio
import contextlib
import email.utils
import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app.profile.boards import HOSTS

USER_AGENT = "jobapp-sweep/1.0 (personal job search app; public posting APIs only)"
TIMEOUT_S = 20.0
ATTEMPTS = 3
BACKOFF_S = 1.0
RETRY_AFTER_MAX_S = 10.0
MAX_REDIRECTS = 2
MAX_BODY = 25 * 1024 * 1024  # OpenAI's Ashby board, the largest known, is about 14 MB
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_CONTROL = re.compile(r"[\x00\ud800-\udfff]")
_TAG = re.compile(r"<[^>]+>")


class BoardError(Exception):
    """A board could not be read. `code` is one of the codes of the module docstring;
    `retryable` says whether another attempt may succeed, `retry_after` is the wait the ATS
    asked for (seconds), if any."""

    def __init__(
        self, code: str, *, retryable: bool = False, retry_after: float | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass(frozen=True)
class Posting:
    title: str
    location: str
    url: str
    published: str  # YYYY-MM-DD as the engine kept it, "" when unknown
    text: str
    remote: bool

    def engine_tuple(self) -> tuple[str, str, str, str, str]:
        return (self.title, self.location, self.url, self.published, self.text)


@dataclass
class BoardResult:
    postings: list[Posting] = field(default_factory=list)
    invalid: int = 0  # postings skipped because they lack a title or a URL


def storable(text: str) -> str:
    """`text` without NUL characters and lone surrogates."""
    return _CONTROL.sub("", text)


def plain(text: str) -> str:
    """engine/jobs.py `plain`: tags become spaces, entities are decoded, whitespace collapses."""
    return " ".join(html.unescape(_TAG.sub(" ", text)).split())


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _required(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _jobs(payload: Any) -> list[Any]:
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise BoardError("invalid_response")
    return jobs


def _ashby(payload: Any, _: tzinfo) -> BoardResult:
    result = BoardResult()
    for job in _jobs(payload):
        if isinstance(job, dict) and job.get("isListed") is False:
            continue
        title = _required(job.get("title")) if isinstance(job, dict) else None
        url = _required(job.get("jobUrl")) if isinstance(job, dict) else None
        if title is None or url is None:
            result.invalid += 1
            continue
        secondary = job.get("secondaryLocations")
        locations = [job.get("location", "")]
        if isinstance(secondary, list):
            locations += [s.get("location", "") for s in secondary if isinstance(s, dict)]
        remote = job.get("isRemote") is True
        locations += ["Remote"] if job.get("isRemote") else []
        names = [loc for loc in locations if loc and isinstance(loc, str)]
        result.postings.append(
            Posting(
                title=storable(title),
                location=storable(", ".join(dict.fromkeys(names))),
                url=storable(url),
                published=_text(job.get("publishedAt"))[:10],
                text=storable(_text(job.get("descriptionPlain"))),
                remote=remote,
            )
        )
    return result


def _greenhouse(payload: Any, _: tzinfo) -> BoardResult:
    result = BoardResult()
    for job in _jobs(payload):
        title = _required(job.get("title")) if isinstance(job, dict) else None
        url = _required(job.get("absolute_url")) if isinstance(job, dict) else None
        if title is None or url is None:
            result.invalid += 1
            continue
        where = job.get("location")
        location = _text(where.get("name")) if isinstance(where, dict) else ""
        published = _text(job.get("first_published")) or _text(job.get("updated_at"))
        result.postings.append(
            Posting(
                title=storable(title),
                location=storable(location),
                url=storable(url),
                published=published[:10],
                text=storable(plain(html.unescape(_text(job.get("content"))))),
                remote="remote" in location.lower(),
            )
        )
    return result


def _lever_date(created: Any, tz: tzinfo) -> str:
    millis = created if isinstance(created, int | float) and not isinstance(created, bool) else 0
    try:
        return datetime.fromtimestamp(millis / 1000, tz=UTC).astimezone(tz).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _lever(payload: Any, tz: tzinfo) -> BoardResult:
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise BoardError("not_found")
    if not isinstance(payload, list):
        raise BoardError("invalid_response")
    result = BoardResult()
    for job in payload:
        title = _required(job.get("text")) if isinstance(job, dict) else None
        url = _required(job.get("hostedUrl")) if isinstance(job, dict) else None
        if title is None or url is None:
            result.invalid += 1
            continue
        categories = job.get("categories")
        location = _text(categories.get("location")) if isinstance(categories, dict) else ""
        workplace = _text(job.get("workplaceType")).lower()
        result.postings.append(
            Posting(
                title=storable(title),
                location=storable(location),
                url=storable(url),
                published=_lever_date(job.get("createdAt", 0), tz),
                text=storable(_text(job.get("descriptionPlain"))),
                remote=workplace == "remote" or "remote" in location.lower(),
            )
        )
    return result


PARSERS: dict[str, Callable[[Any, tzinfo], BoardResult]] = {
    "ashby": _ashby,
    "greenhouse": _greenhouse,
    "lever": _lever,
}


def parse(ats: str, payload: Any, tz: tzinfo) -> BoardResult:
    """The postings of one decoded board body. Raises BoardError for a body of the wrong shape."""
    return PARSERS[ats](payload, tz)


def decode(ats: str, body: bytes, tz: tzinfo) -> BoardResult:
    """The postings of a raw board body. Raises BoardError when it is not the expected JSON."""
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        raise BoardError("invalid_response") from None
    return parse(ats, payload, tz)


def api_path(ats: str, org: str) -> str:
    """The path (and query) of a board's posting API on its ATS host."""
    segment = quote(org, safe="")
    if ats == "ashby":
        return f"/posting-api/job-board/{segment}"
    if ats == "greenhouse":
        return f"/v1/boards/{segment}/jobs?content=true"
    if ats == "lever":
        return f"/v0/postings/{segment}?mode=json"
    raise ValueError(f"unknown ATS {ats!r}")


def published_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


@dataclass
class AtsClient:
    """Fetches boards over one shared httpx client. Use as an async context manager.

    `transport` replaces the network in tests; `sleep` is asyncio.sleep (tests pass a fast one);
    `stub_base_url` and `stub_org_prefix` are the test-only override of the module docstring."""

    tz: tzinfo
    transport: httpx.AsyncBaseTransport | None = None
    stub_base_url: str | None = None
    stub_org_prefix: str | None = None
    body_gate: asyncio.Semaphore | None = None
    timeout_s: float = TIMEOUT_S
    backoff_s: float = BACKOFF_S
    attempts: int = ATTEMPTS
    max_body: int = MAX_BODY
    sleep: Callable[[float], Any] = asyncio.sleep
    _client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AtsClient":
        self._client = httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            trust_env=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def board_url(self, ats: str, org: str) -> tuple[str, str]:
        """The URL of a board and the only host a redirect may go to."""
        path = api_path(ats, org)
        if self.stub_base_url and (
            self.stub_org_prefix is None or org.startswith(self.stub_org_prefix)
        ):
            base = self.stub_base_url.rstrip("/")
            url = f"{base}/{ats}{path}"
            return url, urlsplit(url).hostname or ""
        return f"https://{HOSTS[ats]}{path}", HOSTS[ats]

    async def fetch_board(self, ats: str, org: str) -> BoardResult:
        """Every posting of one board. Raises BoardError. With a `body_gate` (the worker's,
        shared by every sweep) at most that many raw bodies and their decoded JSON are in
        memory at once, whatever the number of sweeps and boards running."""
        async with self.body_gate if self.body_gate is not None else contextlib.nullcontext():
            body = await self.fetch_body(ats, org)
            # Decoding a board of several MB takes a moment: off the event loop.
            return await asyncio.to_thread(decode, ats, body, self.tz)

    async def fetch_body(self, ats: str, org: str) -> bytes:
        url, host = self.board_url(ats, org)
        last: BoardError | None = None
        for attempt in range(self.attempts):
            if last is not None:
                wait = self.backoff_s * 2 ** (attempt - 1)
                if last.retry_after is not None:
                    wait = min(RETRY_AFTER_MAX_S, last.retry_after)
                await self.sleep(wait)
            try:
                return await self._follow(url, host)
            except BoardError as exc:
                if not exc.retryable:
                    raise
                last = exc
        raise BoardError(last.code if last is not None else "network")

    async def _follow(self, url: str, host: str) -> bytes:
        for _ in range(MAX_REDIRECTS + 1):
            status, location, body = await self._get(url)
            if status in (301, 302, 303, 307, 308):
                url = self._same_host(url, location, host)
                continue
            return body
        raise BoardError("upstream_error")

    def _same_host(self, url: str, location: str | None, host: str) -> str:
        if not location:
            raise BoardError("upstream_error")
        target = urljoin(url, location)
        parts, origin = urlsplit(target), urlsplit(url)
        if (
            parts.hostname != host
            or parts.scheme != origin.scheme
            or parts.port != origin.port
            or parts.username
            or parts.password
        ):
            raise BoardError("upstream_error")
        return target

    async def _get(self, url: str) -> tuple[int, str | None, bytes]:
        if self._client is None:
            raise RuntimeError("use AtsClient as an async context manager")
        try:
            async with self._client.stream("GET", url) as response:
                status = response.status_code
                if status in (301, 302, 303, 307, 308):
                    return status, response.headers.get("location"), b""
                if status == 404:
                    raise BoardError("not_found")
                if status in (401, 403):
                    raise BoardError("blocked")
                if status in RETRY_STATUSES:
                    raise BoardError(
                        "rate_limited" if status == 429 else "upstream_error",
                        retryable=True,
                        retry_after=_retry_after(response.headers.get("retry-after")),
                    )
                if status != 200:
                    raise BoardError("upstream_error")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > self.max_body:
                    raise BoardError("too_large")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_body:
                        raise BoardError("too_large")
                    chunks.append(chunk)
                return status, None, b"".join(chunks)
        except httpx.TimeoutException:
            raise BoardError("timeout", retryable=True) from None
        except httpx.TransportError:
            raise BoardError("network", retryable=True) from None
