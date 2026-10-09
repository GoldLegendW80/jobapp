"""POST /api/profile/boards/check: does this company board exist on its ATS, and how many postings
does it list? The Search tab calls it before a board is added, so a typo in the org slug is caught
at once instead of failing silently in the next sweep.

The call goes from the backend, never from the browser (the ATS APIs send no CORS headers, and
the browser would leak the user's address to them). It reads the same public posting APIs the
engine sweeps (engine/jobs.py `fetch`), one GET per check:
- ashby: https://api.ashbyhq.com/posting-api/job-board/{org} -> {"jobs": [...]}, counting the
  postings not marked `isListed: false`;
- greenhouse: https://boards-api.greenhouse.io/v1/boards/{org}/jobs -> {"jobs": [...]}, each job
  carrying `company_name`;
- lever: https://api.lever.co/v0/postings/{org}?mode=json -> [...].

No SSRF: the host is fixed per ATS, the org must match ORG_PATTERN (letters, digits, dot, dash,
underscore, starting with a letter or digit, no ".."), checked by the request model before any
request, and is percent-encoded into one path segment anyway. Redirects are never followed
automatically; a redirect to the same fixed host over https is followed (at most MAX_REDIRECTS),
one to any other host or scheme is refused as an upstream error without being requested.

Answer: `{exists, postings, company_name, error}`. `error` is null when the board was read,
"not_found" (404, or a 200 Lever answers with an error object), "timeout" (no complete answer
within TIMEOUT_S, the whole exchange included), "blocked" (401, 403, 429: the ATS refused this
server, often a bot check), or "upstream_error" (5xx, a refused redirect, a body that is not the
expected JSON, larger than MAX_BODY, or a connection that failed twice). A connection error is
retried once; nothing else is.

Each user may run RATE_LIMIT checks per RATE_WINDOW_S (429 with Retry-After above; refused checks
do not count). Definitive answers (found, not found) are cached in this process for CACHE_TTL_S
per (ats, org, case-insensitive), shared between users since the data is public; failures are
not cached so a retry really retries. Behind the app's default deny and CSRF check like every
other /api route.
"""

import asyncio
import json
import math
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from urllib.parse import SplitResult, quote, urljoin, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.profile.schemas import ORG_PATTERN, Ats

TIMEOUT_S = 5.0
CACHE_TTL_S = 600.0
CACHE_MAX = 1_000
RATE_LIMIT = 10
RATE_WINDOW_S = 60.0
MAX_REDIRECTS = 2
MAX_BODY = 20 * 1024 * 1024  # an Ashby board lists every description: a few MB at most
USER_AGENT = "jobapp-board-check/1.0"

HOSTS: dict[str, str] = {
    "ashby": "api.ashbyhq.com",
    "greenhouse": "boards-api.greenhouse.io",
    "lever": "api.lever.co",
}

TOO_MANY = "Too many board checks. Wait a minute and try again."

CheckError = Literal["not_found", "timeout", "upstream_error", "blocked"]


def board_url(ats: str, org: str, base_url: str | None = None) -> str:
    """The posting API address of a board; `org` becomes one percent-encoded path segment.
    With `base_url` (settings.ats_base_url, tests only) the same path goes to
    `<base_url>/<ats>` instead of the ATS's own host."""
    segment = quote(org, safe="")
    paths = {
        "ashby": f"/posting-api/job-board/{segment}",
        "greenhouse": f"/v1/boards/{segment}/jobs",
        "lever": f"/v0/postings/{segment}?mode=json",
    }
    if ats not in paths:
        raise ValueError(f"unknown ATS {ats!r}")
    if base_url is not None:
        return f"{base_url.rstrip('/')}/{ats}{paths[ats]}"
    return f"https://{HOSTS[ats]}{paths[ats]}"


def _org(value: str) -> str:
    if ".." in value:
        raise ValueError("must not contain two dots in a row")
    return value


class BoardCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ats: Ats
    org: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=ORG_PATTERN),
        AfterValidator(_org),
    ]


class BoardCheckOut(BaseModel):
    exists: bool
    postings: int | None = None
    company_name: str | None = None
    error: CheckError | None = None


class _UpstreamError(Exception):
    def __init__(self, error: CheckError) -> None:
        super().__init__(error)
        self.error = error


def _count(ats: str, payload: Any) -> BoardCheckOut:
    """The answer for a decoded body; _UpstreamError when it is not the shape the ATS documents."""
    if ats == "lever":
        if isinstance(payload, dict) and payload.get("ok") is False:
            return BoardCheckOut(exists=False, error="not_found")
        if not isinstance(payload, list):
            raise _UpstreamError("upstream_error")
        return BoardCheckOut(exists=True, postings=len(payload))
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise _UpstreamError("upstream_error")
    listed = [job for job in jobs if not (isinstance(job, dict) and job.get("isListed") is False)]
    name = None
    if ats == "greenhouse":
        for job in jobs:
            candidate = job.get("company_name") if isinstance(job, dict) else None
            if isinstance(candidate, str) and candidate.strip():
                name = candidate.strip()[:200]
                break
    return BoardCheckOut(exists=True, postings=len(listed), company_name=name)


@dataclass
class BoardChecker:
    """The checker of one app: HTTP client settings, the answer cache and the per-user counters.

    `transport` replaces the network in tests (httpx.MockTransport); `clock` is monotonic
    seconds."""

    transport: httpx.AsyncBaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    timeout_s: float = TIMEOUT_S
    # settings.ats_base_url: a test stub in place of the real hosts (never set in production)
    base_url: str | None = None
    _cache: OrderedDict[tuple[str, str], tuple[float, BoardCheckOut]] = field(
        default_factory=OrderedDict
    )
    _calls: dict[uuid.UUID, deque[float]] = field(default_factory=dict)

    # --- rate limit ---------------------------------------------------------------------------

    def take(self, user_id: uuid.UUID) -> int | None:
        """Count one check for this user; the seconds to wait instead when over the limit."""
        now = self.clock()
        calls = self._calls.setdefault(user_id, deque())
        while calls and calls[0] <= now - RATE_WINDOW_S:
            calls.popleft()
        if len(calls) >= RATE_LIMIT:
            return max(1, math.ceil(calls[0] + RATE_WINDOW_S - now))
        calls.append(now)
        if len(self._calls) > CACHE_MAX:  # forget users idle for a whole window
            for key in [k for k, v in self._calls.items() if not v or v[-1] <= now - RATE_WINDOW_S]:
                del self._calls[key]
        return None

    # --- cache --------------------------------------------------------------------------------

    def cached(self, ats: str, org: str) -> BoardCheckOut | None:
        key = (ats, org.lower())
        hit = self._cache.get(key)
        if hit is None:
            return None
        if hit[0] <= self.clock():
            del self._cache[key]
            return None
        return hit[1]

    def remember(self, ats: str, org: str, answer: BoardCheckOut) -> None:
        if answer.error not in (None, "not_found"):
            return
        key = (ats, org.lower())
        self._cache[key] = (self.clock() + CACHE_TTL_S, answer)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)

    # --- the request --------------------------------------------------------------------------

    async def check(self, ats: str, org: str) -> BoardCheckOut:
        hit = self.cached(ats, org)
        if hit is not None:
            return hit
        try:
            async with asyncio.timeout(self.timeout_s):
                answer = await self._fetch(ats, org)
        except (TimeoutError, httpx.TimeoutException):
            answer = BoardCheckOut(exists=False, error="timeout")
        except _UpstreamError as exc:
            answer = BoardCheckOut(exists=False, error=exc.error)
        self.remember(ats, org, answer)
        return answer

    async def _fetch(self, ats: str, org: str) -> BoardCheckOut:
        url = board_url(ats, org, self.base_url)
        origin = urlsplit(url)
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            trust_env=False,
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                status_code, location, body = await self._get(client, url)
                if status_code in (301, 302, 303, 307, 308):
                    url = _same_host(url, location, origin)
                    continue
                return _answer(ats, status_code, body)
        raise _UpstreamError("upstream_error")  # too many redirects

    async def _get(self, client: httpx.AsyncClient, url: str) -> tuple[int, str | None, bytes]:
        """One GET, retried once on a connection error; the body read up to MAX_BODY."""
        for attempt in (1, 2):
            try:
                async with client.stream("GET", url) as response:
                    if response.is_redirect or response.status_code != 200:
                        return response.status_code, response.headers.get("location"), b""
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > MAX_BODY:
                        raise _UpstreamError("upstream_error")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BODY:
                            raise _UpstreamError("upstream_error")
                        chunks.append(chunk)
                    return 200, None, b"".join(chunks)
            except httpx.ConnectError:
                if attempt == 2:
                    raise _UpstreamError("upstream_error") from None
            except httpx.TimeoutException:
                raise
            except httpx.HTTPError:
                raise _UpstreamError("upstream_error") from None
        raise _UpstreamError("upstream_error")  # pragma: no cover, unreachable


def _same_host(url: str, location: str | None, origin: SplitResult) -> str:
    """The redirect target when it stays on the first request's scheme, host and port (the
    ATS's own host over https), else refused."""
    if not location:
        raise _UpstreamError("upstream_error")
    target = urljoin(url, location)
    parts = urlsplit(target)
    default = {"https": 443, "http": 80}
    if (
        parts.scheme != origin.scheme
        or parts.hostname != origin.hostname
        or (parts.port or default.get(parts.scheme)) != (origin.port or default[origin.scheme])
    ):
        raise _UpstreamError("upstream_error")
    if parts.username or parts.password:
        raise _UpstreamError("upstream_error")
    return target


def _answer(ats: str, status_code: int, body: bytes) -> BoardCheckOut:
    if status_code == 404:
        return BoardCheckOut(exists=False, error="not_found")
    if status_code in (401, 403, 429):
        return BoardCheckOut(exists=False, error="blocked")
    if status_code != 200:
        raise _UpstreamError("upstream_error")
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        raise _UpstreamError("upstream_error") from None
    return _count(ats, payload)


def checker(request: Request) -> BoardChecker:
    """The app's checker, made on first use (tests install their own on app.state)."""
    found = getattr(request.app.state, "board_checker", None)
    if found is None:
        settings = getattr(request.app.state, "settings", None)
        found = BoardChecker(base_url=getattr(settings, "ats_base_url", None))
        request.app.state.board_checker = found
    return found


router = APIRouter(prefix="/boards", tags=["profile"])


@router.post(
    "/check",
    response_model=BoardCheckOut,
    responses={
        401: {"model": ErrorOut, "description": "Not signed in"},
        422: {"model": ErrorOut, "description": "Unknown ATS or an org that is not a slug"},
        429: {"model": ErrorOut, "description": "Too many checks; see Retry-After"},
    },
)
async def check_board(
    body: BoardCheckIn,
    user: Annotated[User, Depends(deps.current_user)],
    boards: Annotated[BoardChecker, Depends(checker)],
) -> BoardCheckOut:
    """Whether the board exists on its ATS and how many postings it lists right now."""
    wait = boards.take(user.id)
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=TOO_MANY,
            headers={"Retry-After": str(wait)},
        )
    return await boards.check(body.ats, body.org)
