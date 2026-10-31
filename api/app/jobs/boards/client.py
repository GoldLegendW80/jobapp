"""Reading company boards politely (app/jobs/boards/ats.py says where and what).

- One request per board and sweep, two when the ATS pages (SmartRecruiters, ats.PAGES_MAX). No
  retry: a board that failed is read again at the next sweep.
- One request at a time per ATS (`ats.pace_key`), PAUSE_S after the previous one ended; other
  ATSs run beside it.
- A 429 stops that ATS for the rest of the sweep: the board that met it ends `rate_limited`, the
  next boards on it are not asked (`host_rate_limited`).
- TIMEOUT_S per request, the whole answer read within it; bodies above MAX_BODY are refused.
- Redirects are followed only to the same host over https, with no port and no credentials,
  at most MAX_REDIRECTS; any other target (http, another host, a port, a malformed address) ends
  the board `upstream_error`.
- Memory: an answer is read into one growing buffer (no list of chunks joined into a copy), and
  one answer is parsed at a time across the ATSs (`parsing`, the sweep's when it gives one, so
  also across the sweep's other sources, sweep.parse_one), its buffer let go right after.
- No cookie, no proxy from the environment, a User-Agent naming the app.

Error codes (`ats.BoardError.code`, then the board's `reason` in the sweep's stats): not_found
(404, or Lever's error object), blocked (401, 403), rate_limited, host_rate_limited,
upstream_error (5xx, another status, a refused redirect, an answer that breaks HTTP), timeout,
network, too_large, invalid_response.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from app.jobs import sweep
from app.jobs.boards import ats

USER_AGENT = "jobapp/1.0 (personal job search app; public job board APIs only)"
TIMEOUT_S = 20.0
PAUSE_S = 1.5
MAX_REDIRECTS = 2
MAX_BODY = 25 * 1024 * 1024  # OpenAI's Ashby board, the largest known, is about 14 MB


@dataclass
class Read:
    """One board as read: its postings, the requests it took."""

    parsed: ats.Parsed
    requests: int


@dataclass
class BoardClient:
    """Use as an async context manager. `transport` replaces the network in tests; `sleep` and
    `monotonic` are injectable so a test never waits."""

    transport: httpx.AsyncBaseTransport | None = None
    pause_s: float = PAUSE_S
    timeout_s: float = TIMEOUT_S
    max_body: int = MAX_BODY
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    monotonic: Callable[[], float] = time.monotonic
    _client: httpx.AsyncClient | None = None
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _last: dict[str, float] = field(default_factory=dict)
    stopped: set[str] = field(default_factory=set)  # ATSs that answered 429 in this sweep
    # One parse at a time for the whole sweep: a parsed answer can be several times its size.
    # The sweep's own (Plan.parsing) when given, shared with its other sources.
    parsing: asyncio.Semaphore | None = None
    # True once the sweep let go of its row (sweep.abandoned): no request is sent after, the
    # board ends `store_failed`.
    abandoned: Callable[[], bool] | None = None

    async def __aenter__(self) -> "BoardClient":
        self._client = httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            trust_env=False,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def read(self, kind: str, board: str) -> Read:
        """Every posting of one board (two pages at most). Raises ats.BoardError."""
        parsed = ats.Parsed()
        requests = 0
        for page in range(ats.PAGES_MAX):
            body = await self._paced(kind, ats.api_url(kind, board, page))
            requests += 1
            if self.parsing is None:
                self.parsing = asyncio.Semaphore(1)
            one = await sweep.parse_one(self.parsing, ats.parse, kind, board, body)
            del body
            parsed.postings += one.postings
            parsed.invalid += one.invalid
            listed = len(parsed.postings) + parsed.invalid
            if kind != "smartrecruiters" or one.total is None or listed >= one.total:
                break
            if len(one.postings) + one.invalid < ats.PAGE_SIZE:
                break
        return Read(parsed, requests)

    async def _paced(self, kind: str, url: str) -> bytearray:
        key = ats.pace_key(kind)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self.stopped:
                raise ats.BoardError("host_rate_limited")
            self._check()
            last = self._last.get(key)
            if last is not None:
                wait = last + self.pause_s - self.monotonic()
                if wait > 0:
                    await self.sleep(wait)
                    self._check()
            try:
                return await self._follow(url)
            except ats.BoardError as exc:
                if exc.code == "rate_limited":
                    self.stopped.add(key)
                raise
            finally:
                self._last[key] = self.monotonic()

    def _check(self) -> None:
        if self.abandoned is not None and self.abandoned():
            raise ats.BoardError("store_failed")

    async def _follow(self, url: str) -> bytearray:
        host = urlsplit(url).hostname
        for _ in range(MAX_REDIRECTS + 1):
            status, location, body = await self._get(url)
            if status not in (301, 302, 303, 307, 308):
                return body
            target = _same_host_target(url, location, host) if location else None
            if target is None:
                raise ats.BoardError("upstream_error")
            url = target
        raise ats.BoardError("upstream_error")

    async def _get(self, url: str) -> tuple[int, str | None, bytearray]:
        if self._client is None:
            raise RuntimeError("use BoardClient as an async context manager")
        try:
            async with asyncio.timeout(self.timeout_s), self._client.stream("GET", url) as answer:
                status = answer.status_code
                if status in (301, 302, 303, 307, 308):
                    return status, answer.headers.get("location"), bytearray()
                if status == 404:
                    raise ats.BoardError("not_found")
                if status in (401, 403):
                    raise ats.BoardError("blocked")
                if status == 429:
                    raise ats.BoardError("rate_limited")
                if status != 200:
                    raise ats.BoardError("upstream_error")
                declared = answer.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > self.max_body:
                    raise ats.BoardError("too_large")
                body = bytearray()
                async for chunk in answer.aiter_bytes():
                    if len(body) + len(chunk) > self.max_body:
                        raise ats.BoardError("too_large")
                    body += chunk
                return status, None, body
        except (TimeoutError, httpx.TimeoutException):
            raise ats.BoardError("timeout") from None
        except httpx.RemoteProtocolError:  # an answer that breaks HTTP, a malformed redirect
            raise ats.BoardError("upstream_error") from None
        except httpx.TransportError:
            raise ats.BoardError("network") from None


def _same_host_target(url: str, location: str, host: str | None) -> str | None:
    """The address a redirect from `url` leads to when the client follows it: https, the same
    host, no port, no credentials; else None. A malformed Location (an unclosed IPv6 bracket that
    urljoin refuses, a port that is not a number) is not one."""
    try:
        target = urljoin(url, location)
        parts = urlsplit(target)
        port = parts.port
    except ValueError:
        return None
    same = (
        parts.scheme == "https"
        and parts.hostname == host
        and port is None
        and not parts.username
        and not parts.password
    )
    return target if same else None
