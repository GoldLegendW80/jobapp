"""The one client of Google (mail M1): every HTTP call the app makes to Google is built here.

OAuth (the consent address, the code exchange, the refresh, the revocation), `userinfo`, and the
Gmail read calls: `getProfile`, `messages.list`, `messages.get`, `history.list`, `threads.get`
and `attachments.get`. **There is no function that sends a mail, and none may be added**: the
only code that builds that request is one private function of `app/mail/gate.py` (M12), which
takes from this module what it needs and nothing more (`access_token`, `gmail_url`,
`TIMEOUT`, `raise_for_answer`). A test reads this file and fails if it names that call.

Where the calls go. Unset, the three settings mean Google: `GOOGLE_AUTH_URL` (where the browser
is sent), `GOOGLE_TOKEN_BASE_URL` (`/token`, `/revoke`) and `GOOGLE_API_BASE_URL` (Gmail under
`/gmail/v1/users/me`, userinfo under `/v1/userinfo`). Tests point them at the fake of
`api/tests/mail/fake_gmail.py`. `endpoints()` refuses a Google address when APP_ENV is test, and
anything but Google when it is production (the settings refuse the three variables there too).

Rules of every call, with the figures of "Google at the HTTP boundary" (tasks/mail/todo.md):

- **Timeouts:** 10 s to connect and 10 s to read (`TIMEOUT`).
- **Bounded answers:** `messages.get` 8 MB, every other JSON answer 2 MB, `attachments.get`
  36 MB read as a stream and decoded in pieces of at most 64 KB, never held whole. Over the cap:
  `TooLargeError`.
- **Retries inside one call:** 429, 403 with a rate limit reason and 5xx only: up to three more
  tries after 1 s, 2 s and 4 s (plus up to a quarter of jitter). A `Retry-After` of at most
  `RETRY_AFTER_INLINE_S` is waited instead; a longer one raises at once and carries the figure.
  A timeout, a broken connection and an unreadable answer are not retried here. After that the
  caller owns the account's backoff: `next_backoff()` gives the delay (5 minutes, doubling, at
  most 6 hours).
- **Tokens:** access tokens live in this object's memory only, until they expire. A 401
  refreshes once; a second 401, or `invalid_grant`, is `RevokedError`.
- **Quota:** each call spends its quota units in a counter per user and per minute
  (`Budget`), which refuses to pass half of Google's documented per user rate. The counter is
  per process: the worker syncs, the api only downloads attachments.
- **Downloads:** two at once for the process, one per user (`DOWNLOADS_PROCESS`,
  `DOWNLOADS_USER`); the next one waits.
- **Privacy:** tokens are registered with the log scrubber while in use, errors carry a code
  and never Google's text (`app/mail/errors.py`), and the module logs through `app/mail/log.py`
  only.

One `GoogleClient` per process. `transport`, `clock`, `sleep` and `jitter` are for tests.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import random
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar
from urllib.parse import quote, urlencode, urlsplit

import httpx

from app.config import Settings
from app.mail import log
from app.mail.errors import (
    GoogleDownError,
    GoogleError,
    GoogleTimeoutError,
    HistoryExpiredError,
    MalformedAnswerError,
    NotFoundError,
    OAuthError,
    QuotaError,
    RealGoogleInTestError,
    RevokedError,
    ScopeMissingError,
    TooLargeError,
)

# --- where Google is ------------------------------------------------------------------------

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_BASE_URL = "https://oauth2.googleapis.com"  # noqa: S105, an address
GOOGLE_API_BASE_URL = "https://gmail.googleapis.com"
# The `userinfo_endpoint` of Google's OpenID discovery document.
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_HOST_SUFFIXES = ("googleapis.com", "google.com")

SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPE_EMAIL = "https://www.googleapis.com/auth/userinfo.email"
SCOPE_OPENID = "openid"
SCOPES = (SCOPE_OPENID, SCOPE_EMAIL, SCOPE_READONLY, SCOPE_SEND)

# --- the figures ----------------------------------------------------------------------------

CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 10.0
TIMEOUT = httpx.Timeout(
    connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S, write=READ_TIMEOUT_S, pool=CONNECT_TIMEOUT_S
)
MB = 1024 * 1024
MESSAGE_MAX_BYTES = 8 * MB
JSON_MAX_BYTES = 2 * MB
ATTACHMENT_MAX_BYTES = 36 * MB
CHUNK_BYTES = 64 * 1024
DOWNLOADS_PROCESS = 2
DOWNLOADS_USER = 1
RETRY_DELAYS_S = (1.0, 2.0, 4.0)
RETRY_JITTER = 0.25
RETRY_AFTER_INLINE_S = 10
RETRY_AFTER_MAX_S = 24 * 3600
BACKOFF_FIRST_S = 5 * 60
BACKOFF_MAX_S = 6 * 3600
# An access token is dropped this long before Google says it ends.
TOKEN_MARGIN_S = 60
# `list_all_messages` stops after this many pages whatever Google answers: the first sync reads
# at most 5,000 ids, 100 per page.
PAGE_CAP = 60
PAGE_SIZE = 100

# Quota units per call and the per user rate, as Google's "Usage limits" page of the Gmail API
# gave them on 2026-10-14 (the page said: last updated 2026-09-10). Copied under "Measurements",
# M1, of tasks/mail/todo.md (assumption A2). The client stays under half of the per user rate.
UNITS: Mapping[str, int] = {
    "getProfile": 1,
    "messages.list": 5,
    "messages.get": 20,
    "history.list": 2,
    "threads.get": 40,
    "attachments.get": 20,
}
GOOGLE_USER_UNITS_PER_MINUTE = 6_000
USER_UNITS_PER_MINUTE = GOOGLE_USER_UNITS_PER_MINUTE // 2

_ID = re.compile(r"\A[A-Za-z0-9_-]{1,4096}\Z")
_DATA_KEY = re.compile(rb'"data"\s*:\s*"')
_BASE64URL = re.compile(rb"\A[A-Za-z0-9_-]*\Z")
_HEAD_MAX = 64 * 1024
_RATE_REASONS = {"rateLimitExceeded": "rate_limit", "userRateLimitExceeded": "user_rate_limit"}
_DAILY_REASONS = frozenset({"dailyLimitExceeded", "quotaExceeded"})
_SCOPE_REASONS = frozenset({"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"})

Checked = TypeVar("Checked")


def is_google_host(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in GOOGLE_HOST_SUFFIXES)


@dataclass(frozen=True)
class Endpoints:
    auth_url: str
    token_url: str
    revoke_url: str
    userinfo_url: str
    gmail_url: str  # .../gmail/v1/users/me


def endpoints(settings: Settings) -> Endpoints:
    """The addresses of this environment, refused when they do not belong in it."""
    token_base = settings.google_token_base_url or GOOGLE_TOKEN_BASE_URL
    api_base = settings.google_api_base_url
    found = Endpoints(
        auth_url=settings.google_auth_url or GOOGLE_AUTH_URL,
        token_url=f"{token_base}/token",
        revoke_url=f"{token_base}/revoke",
        userinfo_url=f"{api_base}/v1/userinfo" if api_base else GOOGLE_USERINFO_URL,
        gmail_url=f"{api_base or GOOGLE_API_BASE_URL}/gmail/v1/users/me",
    )
    for name in ("auth_url", "token_url", "revoke_url", "userinfo_url", "gmail_url"):
        real = is_google_host(urlsplit(getattr(found, name)).hostname)
        if settings.app_env == "test" and real:
            raise RealGoogleInTestError(
                f"{name} is a real Google address and APP_ENV is test: set GOOGLE_AUTH_URL, "
                "GOOGLE_TOKEN_BASE_URL and GOOGLE_API_BASE_URL to the fake"
            )
        if settings.app_env == "production" and not real:
            raise RealGoogleInTestError(f"{name} is not a Google address and APP_ENV is production")
    return found


def next_backoff(previous_s: int | None) -> int:
    """The account's next backoff in seconds: 5 minutes, then double, at most 6 hours. The
    caller stores it and resets it (None) on the first success."""
    if not previous_s or previous_s < BACKOFF_FIRST_S:
        return BACKOFF_FIRST_S
    return min(BACKOFF_MAX_S, previous_s * 2)


# --- what the calls take and return ---------------------------------------------------------


@dataclass(frozen=True)
class Grant:
    """One user's grant, as the caller holds it: the app's user id (the key of the token cache
    and of the quota counter) and the decrypted refresh token. Its text never shows the token."""

    user_id: str
    refresh_token: str = field(repr=False)


@dataclass(frozen=True)
class Tokens:
    """The answer of a code exchange. `refresh_token` is None when Google gave none."""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_in: int
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class UserInfo:
    sub: str
    email: str = field(repr=False)
    email_verified: bool


@dataclass(frozen=True)
class Profile:
    email: str = field(repr=False)
    messages_total: int
    threads_total: int
    history_id: str


@dataclass(frozen=True)
class MessageRef:
    id: str
    thread_id: str


@dataclass(frozen=True)
class MessagePage:
    refs: tuple[MessageRef, ...]
    next_page_token: str | None
    estimate: int


@dataclass(frozen=True)
class Listing:
    """Every page of a search, up to a limit. `stopped` says why it ended: `end` (no more
    pages), `limit`, `page_cap`, or `page_token_repeated` (Google handed back a token already
    used: the loop is cut instead of turning for ever)."""

    refs: tuple[MessageRef, ...]
    pages: int
    stopped: Literal["end", "limit", "page_cap", "page_token_repeated"]


@dataclass(frozen=True)
class HistoryPage:
    """`records` are Google's history records as they came (ids and label names only)."""

    records: tuple[Mapping[str, Any], ...]
    next_page_token: str | None
    history_id: str


@dataclass(frozen=True)
class Fetched:
    """Several messages. `gone` counts the ids that answered 404 (listed, then deleted: skipped).
    `truncated` names the ids whose full answer was over the cap and that came back as
    `format=metadata` instead: stored with their snippet, never dropped."""

    messages: tuple[Mapping[str, Any], ...]
    gone: int
    truncated: frozenset[str]


@dataclass(frozen=True)
class Answer:
    status: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)


class Budget:
    """Quota units spent per user in the current minute, refused over `limit`."""

    def __init__(self, clock: Callable[[], float], limit: int = USER_UNITS_PER_MINUTE) -> None:
        self._clock = clock
        self.limit = limit
        self._spent: dict[str, tuple[int, int]] = {}

    def used(self, user_id: str) -> int:
        minute, spent = self._spent.get(user_id, (-1, 0))
        return spent if minute == int(self._clock() // 60) else 0

    def spend(self, user_id: str, units: int) -> None:
        now = self._clock()
        minute = int(now // 60)
        spent = self.used(user_id)
        if spent + units > self.limit:
            raise QuotaError(reason="local_budget", retry_after=60 - int(now % 60))
        self._spent[user_id] = (minute, spent + units)


class DownloadSlots:
    """Two downloads at once for the process, one per user. `waiting` counts the callers that
    are queued, so a test can see the third one wait without measuring time."""

    def __init__(self, process: int = DOWNLOADS_PROCESS) -> None:
        self._process = asyncio.Semaphore(process)
        self._users: dict[str, asyncio.Lock] = {}
        self.active = 0
        self.waiting = 0

    async def acquire(self, user_id: str) -> None:
        lock = self._users.setdefault(user_id, asyncio.Lock())
        self.waiting += 1
        try:
            await lock.acquire()
            try:
                await self._process.acquire()
            except BaseException:
                lock.release()
                raise
        finally:
            self.waiting -= 1
        self.active += 1

    def release(self, user_id: str) -> None:
        self.active -= 1
        self._process.release()
        self._users[user_id].release()


# --- reading an answer ----------------------------------------------------------------------


def _retry_after(headers: Mapping[str, str]) -> int | None:
    raw = (headers.get("retry-after") or "").strip()
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return max(1, min(RETRY_AFTER_MAX_S, math.ceil(seconds)))


def _loads(body: bytes) -> dict[str, Any]:
    """The JSON object of an answer. An answer cut short is `truncated`, anything else that
    does not parse is `not_json`, and a list or a number is `shape`."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise MalformedAnswerError(reason="not_json") from None
    try:
        found = json.loads(text)
    except ValueError:
        stripped = text.strip()
        cut = stripped.startswith(("{", "[")) and not stripped.endswith(("}", "]"))
        raise MalformedAnswerError(reason="truncated" if cut else "not_json") from None
    except RecursionError:
        raise MalformedAnswerError(reason="shape") from None
    if not isinstance(found, dict):
        raise MalformedAnswerError(reason="shape")
    return found


def _google_reason(body: bytes) -> str:
    """Google's own reason word of an error body, to compare with the ones this module knows.
    It is never stored, logged or raised."""
    try:
        error = json.loads(body.decode("utf-8", "replace")).get("error")
    except (ValueError, AttributeError, RecursionError):
        return ""
    if isinstance(error, str):  # the token endpoint: {"error": "invalid_grant"}
        return error
    if not isinstance(error, dict):
        return ""
    for item in error.get("errors") or ():
        if isinstance(item, dict) and isinstance(item.get("reason"), str):
            return item["reason"]
    for item in error.get("details") or ():
        if isinstance(item, dict) and isinstance(item.get("reason"), str):
            return item["reason"]
    status = error.get("status")
    return status if isinstance(status, str) else ""


def raise_for_answer(answer: Answer, call: str) -> None:
    """Raise the typed error of a Gmail answer that is not a success. Public for the one
    function of the gate (M12) that builds its own request."""
    status = answer.status
    if 200 <= status < 300:
        return
    reason = _google_reason(answer.body)
    if status == 429:
        raise QuotaError(
            status=status,
            reason="rate_limit",
            retry_after=_retry_after(answer.headers),
            retryable=True,
        )
    if status == 403:
        if reason in _RATE_REASONS:
            raise QuotaError(
                status=status,
                reason=_RATE_REASONS[reason],
                retry_after=_retry_after(answer.headers),
                retryable=True,
            )
        if reason in _DAILY_REASONS:
            raise QuotaError(
                status=status, reason="daily_limit", retry_after=_retry_after(answer.headers)
            )
        if reason in _SCOPE_REASONS:
            raise ScopeMissingError(status=status)
        raise GoogleDownError(status=status, reason="refused")
    if status == 401:
        raise RevokedError(status=status)
    if status == 404:
        if call == "history.list":
            raise HistoryExpiredError(status=status)
        raise NotFoundError(status=status)
    if status >= 500:
        raise GoogleDownError(
            status=status,
            reason="server_error",
            retry_after=_retry_after(answer.headers),
            retryable=True,
        )
    raise GoogleDownError(status=status, reason="refused")


def _text(found: Mapping[str, Any], key: str) -> str:
    value = found.get(key)
    if not isinstance(value, str) or not value:
        raise MalformedAnswerError(reason="shape")
    return value


def _count(found: Mapping[str, Any], key: str) -> int:
    value = found.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MalformedAnswerError(reason="shape")
    return value


def _items(found: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = found.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise MalformedAnswerError(reason="shape")
    return value


def _safe_id(value: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise GoogleDownError(reason="bad_id")
    return quote(value, safe="")


def _scopes(found: Mapping[str, Any]) -> tuple[str, ...]:
    raw = found.get("scope", "")
    return tuple(sorted(raw.split())) if isinstance(raw, str) else ()


def _decode(piece: bytes) -> bytes:
    if not _BASE64URL.match(piece):
        raise MalformedAnswerError(reason="shape")
    try:
        return base64.urlsafe_b64decode(piece + b"=" * (-len(piece) % 4))
    except (binascii.Error, ValueError):
        raise MalformedAnswerError(reason="shape") from None


async def decode_attachment(
    chunks: AsyncIterator[bytes], cap: int = ATTACHMENT_MAX_BYTES
) -> AsyncIterator[bytes]:
    """The bytes of an attachment out of the stream of Google's answer
    (`{"size": n, "data": "<base64url>"}`), piece by piece: nothing but the current piece and
    at most five characters carried over (three of data, two of padding) is held. Padding, which
    Google may add, is accepted only at the very end of the data; anywhere else it is refused
    as a malformed answer. Over `cap` bytes of answer: `TooLargeError`
    after what was already decoded (the download is cut, the caller drops what it wrote)."""
    total = 0
    state = "head"
    head = b""
    carry = b""
    async for chunk in chunks:
        total += len(chunk)
        if total > cap:
            raise TooLargeError(limit=cap)
        if state == "head":
            head += chunk
            match = _DATA_KEY.search(head)
            if match is None:
                if len(head) > _HEAD_MAX:
                    raise MalformedAnswerError(reason="shape")
                continue
            chunk = head[match.end() :]
            head = b""
            state = "data"
        if state == "data":
            end = chunk.find(b'"')
            carry += chunk if end < 0 else chunk[:end]
            if end >= 0:
                state = "tail"
                piece, carry = carry.rstrip(b"="), b""
            else:
                bare = len(carry.rstrip(b"="))
                if len(carry) - bare > 2:
                    raise MalformedAnswerError(reason="shape")
                usable = bare - bare % 4
                piece, carry = carry[:usable], carry[usable:]
            if piece:
                yield _decode(piece)
    if state == "head" and not head.strip():
        raise MalformedAnswerError(reason="truncated")
    if state != "tail":
        raise MalformedAnswerError(reason="truncated" if state == "data" else "shape")


# --- the client -----------------------------------------------------------------------------


class GoogleClient:
    """See the module's text. `settings` gives the environment, the three addresses and the
    OAuth client; a missing client id or secret raises when a token call needs it."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.endpoints = endpoints(settings)
        self._client_id = settings.google_oauth_client_id
        self._client_secret = (
            settings.google_oauth_client_secret.get_secret_value()
            if settings.google_oauth_client_secret
            else None
        )
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self.budget = Budget(clock)
        self.slots = DownloadSlots()
        # user id -> (fingerprint of the refresh token, access token, the clock when it ends)
        self._tokens: dict[str, tuple[str, str, float]] = {}
        self._refreshing: dict[str, asyncio.Lock] = {}

    @property
    def gmail_url(self) -> str:
        return self.endpoints.gmail_url

    # -- the transport --

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=TIMEOUT,
            follow_redirects=False,
            trust_env=self._transport is None,
        )

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: Sequence[tuple[str, str]] | None = None,
        form: Mapping[str, str] | None = None,
        bearer: str | None = None,
        cap: int = JSON_MAX_BYTES,
    ) -> Answer:
        """One exchange, read up to `cap` bytes. Network failures become typed errors here."""
        headers = {"Accept": "application/json"}
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            with log.quiet():
                async with (
                    self._http() as http,
                    http.stream(method, url, params=params, data=form, headers=headers) as response,
                ):
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > cap:
                        raise TooLargeError(limit=cap, status=response.status_code)
                    body = bytearray()
                    async for chunk in response.aiter_bytes(CHUNK_BYTES):
                        if len(body) + len(chunk) > cap:
                            raise TooLargeError(limit=cap, status=response.status_code)
                        body += chunk
                    return Answer(
                        response.status_code,
                        {name.lower(): value for name, value in response.headers.items()},
                        bytes(body),
                    )
        except httpx.TimeoutException:
            raise GoogleTimeoutError(reason="timeout") from None
        except (httpx.HTTPError, OSError):
            raise GoogleDownError(reason="unreachable") from None

    async def _retrying(self, call: str, once: Callable[[], Awaitable[Checked]]) -> Checked:
        for attempt, delay in enumerate((*RETRY_DELAYS_S, None), start=1):
            try:
                return await once()
            except GoogleError as error:
                if delay is None or not error.retryable:
                    raise
                if error.retry_after is not None:
                    if error.retry_after > RETRY_AFTER_INLINE_S:
                        raise
                    wait = float(error.retry_after)
                else:
                    wait = delay * (1 + RETRY_JITTER * min(1.0, max(0.0, self._jitter())))
                log.event(
                    "mail.google.retry",
                    call=call,
                    code=error.code,
                    status=error.status,
                    attempt=attempt,
                )
                await self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    # -- OAuth --

    def consent_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scopes: Sequence[str] = SCOPES,
        login_hint: str | None = None,
    ) -> str:
        """Where the browser is sent: offline access, consent asked again (so a refresh token
        comes back), PKCE with S256. Only scopes of `SCOPES` are ever asked for, and an address
        without a `state` or a challenge is never built (issue #175)."""
        if not scopes or any(scope not in SCOPES for scope in scopes):
            raise ValueError("consent_url: a scope outside SCOPES")
        if not state or not code_challenge:
            raise ValueError("consent_url: state and code_challenge are required")
        query = {
            "client_id": self._client(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
        }
        if login_hint:
            query["login_hint"] = login_hint
        return f"{self.endpoints.auth_url}?{urlencode(query)}"

    def _client(self) -> str:
        if not self._client_id or not self._client_secret:
            raise OAuthError(reason="invalid_client")
        return self._client_id

    async def _token_call(self, form: dict[str, str], *, refresh: bool) -> dict[str, Any]:
        form = {**form, "client_id": self._client(), "client_secret": self._client_secret or ""}
        answer = await self._send("POST", self.endpoints.token_url, form=form)
        if 200 <= answer.status < 300:
            return _loads(answer.body)
        reason = _google_reason(answer.body)
        if answer.status in (400, 401):
            if reason == "invalid_grant":
                if refresh:
                    raise RevokedError(status=answer.status, reason="invalid_grant")
                raise OAuthError(status=answer.status, reason="invalid_grant")
            raise OAuthError(
                status=answer.status,
                reason="invalid_client" if reason == "invalid_client" else "refused",
            )
        raise_for_answer(answer, "token")
        raise AssertionError("unreachable")  # pragma: no cover

    async def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> Tokens:
        """The code of the callback for tokens. A refused code is `OAuthError`."""

        async def once() -> Tokens:
            found = await self._token_call(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "redirect_uri": redirect_uri,
                },
                refresh=False,
            )
            refresh_token = found.get("refresh_token")
            return Tokens(
                access_token=_text(found, "access_token"),
                refresh_token=refresh_token if isinstance(refresh_token, str) else None,
                expires_in=_count(found, "expires_in"),
                scopes=_scopes(found),
            )

        with log.hide(code, code_verifier, self._client_secret):
            tokens = await self._retrying("token", once)
        return tokens

    async def _refresh(self, grant: Grant) -> str:
        found = await self._token_call(
            {"grant_type": "refresh_token", "refresh_token": grant.refresh_token}, refresh=True
        )
        token = _text(found, "access_token")
        lifetime = max(0, _count(found, "expires_in") - TOKEN_MARGIN_S)
        self._tokens[grant.user_id] = (_fingerprint(grant), token, self._clock() + lifetime)
        log.event("mail.google.refreshed", user_id=grant.user_id)
        return token

    async def _token(self, grant: Grant) -> str:
        lock = self._refreshing.setdefault(grant.user_id, asyncio.Lock())
        async with lock:
            held = self._tokens.get(grant.user_id)
            if held is not None and held[0] == _fingerprint(grant) and self._clock() < held[2]:
                return held[1]
            return await self._refresh(grant)

    async def access_token(self, grant: Grant) -> str:
        """A live access token of this grant, refreshed when needed. For the gate (M12) only:
        every call of this module finds its own."""
        with log.hide(grant.refresh_token, self._client_secret):
            return await self._retrying("token", lambda: self._token(grant))

    def forget(self, user_id: str) -> None:
        """Drop the user's access token from memory (disconnect, or a 401)."""
        self._tokens.pop(user_id, None)

    async def revoke(self, token: str) -> None:
        """Revoke a refresh token at Google. A token Google no longer knows (`invalid_token`) is
        a success: the aim, a dead grant, is reached. Any other 400 is a refusal (`OAuthError`):
        the grant may still live, and the caller must not record it as revoked (issue #175)."""

        async def once() -> None:
            answer = await self._send("POST", self.endpoints.revoke_url, form={"token": token})
            if answer.status == 400:
                if _google_reason(answer.body) == "invalid_token":
                    return
                raise OAuthError(status=400, reason="refused")
            raise_for_answer(answer, "revoke")

        with log.hide(token):
            await self._retrying("revoke", once)

    async def userinfo(self, access_token: str) -> UserInfo:
        """Which address was connected, read with the access token of the exchange."""

        async def once() -> UserInfo:
            answer = await self._send("GET", self.endpoints.userinfo_url, bearer=access_token)
            raise_for_answer(answer, "userinfo")
            found = _loads(answer.body)
            return UserInfo(
                sub=_text(found, "sub"),
                email=_text(found, "email"),
                email_verified=found.get("email_verified") is True,
            )

        with log.hide(access_token):
            return await self._retrying("userinfo", once)

    # -- Gmail --

    async def _gmail(
        self,
        grant: Grant,
        call: str,
        path: str,
        params: Sequence[tuple[str, str]] = (),
        *,
        cap: int = JSON_MAX_BYTES,
    ) -> dict[str, Any]:
        url = f"{self.endpoints.gmail_url}{path}"

        async def exchange() -> Answer:
            self.budget.spend(grant.user_id, UNITS[call])
            token = await self._token(grant)
            with log.hide(token):
                return await self._send("GET", url, params=params, bearer=token, cap=cap)

        async def once() -> dict[str, Any]:
            answer = await exchange()
            if answer.status == 401:
                # The token died early (revoked, or Google's clock is ahead): one refresh.
                self.forget(grant.user_id)
                answer = await exchange()
            raise_for_answer(answer, call)
            return _loads(answer.body)

        with log.hide(grant.refresh_token, self._client_secret):
            try:
                return await self._retrying(call, once)
            except GoogleError as error:
                log.event(
                    "mail.google.failed",
                    user_id=grant.user_id,
                    call=call,
                    code=error.code,
                    status=error.status,
                    reason=error.reason,
                )
                raise

    async def get_profile(self, grant: Grant) -> Profile:
        found = await self._gmail(grant, "getProfile", "/profile")
        return Profile(
            email=_text(found, "emailAddress"),
            messages_total=_count(found, "messagesTotal"),
            threads_total=_count(found, "threadsTotal"),
            history_id=_text(found, "historyId"),
        )

    async def list_messages(
        self,
        grant: Grant,
        *,
        q: str | None = None,
        page_token: str | None = None,
        max_results: int = PAGE_SIZE,
        include_spam_trash: bool = False,
    ) -> MessagePage:
        """One page of `messages.list`: ids only."""
        params = [("maxResults", str(max(1, min(500, max_results))))]
        if q:
            params.append(("q", q))
        if page_token:
            params.append(("pageToken", page_token))
        if include_spam_trash:
            params.append(("includeSpamTrash", "true"))
        found = await self._gmail(grant, "messages.list", "/messages", params)
        token = found.get("nextPageToken")
        return MessagePage(
            refs=tuple(
                MessageRef(_text(item, "id"), _text(item, "threadId"))
                for item in _items(found, "messages")
            ),
            next_page_token=token if isinstance(token, str) and token else None,
            estimate=_count(found, "resultSizeEstimate"),
        )

    async def list_all_messages(
        self, grant: Grant, *, q: str | None = None, limit: int = 5000, page_cap: int = PAGE_CAP
    ) -> Listing:
        """Every page of a search, cut at `limit` ids, at `page_cap` pages, and as soon as a
        page token comes back a second time."""
        refs: list[MessageRef] = []
        seen: set[str] = set()
        token: str | None = None
        pages = 0
        stopped: Literal["end", "limit", "page_cap", "page_token_repeated"] = "end"
        while True:
            page = await self.list_messages(grant, q=q, page_token=token)
            pages += 1
            refs.extend(page.refs)
            token = page.next_page_token
            if len(refs) >= limit:
                stopped = "limit"
            elif token is None:
                stopped = "end"
            elif token in seen:
                stopped = "page_token_repeated"
            elif pages >= page_cap:
                stopped = "page_cap"
            else:
                seen.add(token)
                continue
            break
        if stopped in ("page_cap", "page_token_repeated"):
            log.event("mail.google.listing_cut", user_id=grant.user_id, reason=stopped, pages=pages)
        return Listing(tuple(refs[:limit]), pages, stopped)

    async def get_message(
        self,
        grant: Grant,
        message_id: str,
        *,
        format: Literal["full", "metadata"] = "full",
        metadata_headers: Sequence[str] = (),
    ) -> dict[str, Any]:
        """One message as Google splits it (never `format=raw`). Over 8 MB: `TooLargeError`."""
        params = [("format", format)]
        if format == "metadata":
            params += [("metadataHeaders", name) for name in metadata_headers]
        found = await self._gmail(
            grant,
            "messages.get",
            f"/messages/{_safe_id(message_id)}",
            params,
            cap=MESSAGE_MAX_BYTES,
        )
        _text(found, "id")
        return found

    async def get_messages(self, grant: Grant, message_ids: Sequence[str]) -> Fetched:
        """Each message in full, one after the other. An id that is gone (404) is skipped and
        counted; a message over the cap comes back as metadata and is named in `truncated`."""
        messages: list[Mapping[str, Any]] = []
        truncated: set[str] = set()
        gone = 0
        for message_id in message_ids:
            try:
                try:
                    messages.append(await self.get_message(grant, message_id))
                except TooLargeError:
                    messages.append(await self.get_message(grant, message_id, format="metadata"))
                    truncated.add(message_id)
            except NotFoundError:
                gone += 1
        if gone or truncated:
            log.event(
                "mail.google.fetched",
                user_id=grant.user_id,
                count=len(messages),
                gone=gone,
                truncated=len(truncated),
            )
        return Fetched(tuple(messages), gone, frozenset(truncated))

    async def get_thread(
        self,
        grant: Grant,
        thread_id: str,
        *,
        format: Literal["full", "metadata", "minimal"] = "metadata",
    ) -> dict[str, Any]:
        """One conversation with its messages. Not in the plan's list of calls: kept to
        `metadata` by default, so its answer stays under the 2 MB cap of a JSON answer."""
        found = await self._gmail(
            grant, "threads.get", f"/threads/{_safe_id(thread_id)}", [("format", format)]
        )
        _text(found, "id")
        _items(found, "messages")
        return found

    async def list_history(
        self,
        grant: Grant,
        start_history_id: str,
        *,
        history_types: Sequence[str] = (
            "messageAdded",
            "messageDeleted",
            "labelAdded",
            "labelRemoved",
        ),
        page_token: str | None = None,
        max_results: int = PAGE_SIZE,
    ) -> HistoryPage:
        """One page of what changed since `start_history_id`. Too old: `HistoryExpiredError`."""
        params = [
            ("startHistoryId", start_history_id),
            ("maxResults", str(max(1, min(500, max_results)))),
        ]
        params += [("historyTypes", name) for name in history_types]
        if page_token:
            params.append(("pageToken", page_token))
        found = await self._gmail(grant, "history.list", "/history", params)
        token = found.get("nextPageToken")
        return HistoryPage(
            records=tuple(_items(found, "history")),
            next_page_token=token if isinstance(token, str) and token else None,
            history_id=_text(found, "historyId"),
        )

    async def download_attachment(
        self, grant: Grant, message_id: str, attachment_id: str
    ) -> AsyncIterator[bytes]:
        """The bytes of one attachment, in pieces of at most 64 KB, read as they are sent. Two
        downloads run at once in the process and one per user: the next one waits here. Close
        the iterator (`contextlib.aclosing`) when stopping early, so the slot is freed."""
        url = (
            f"{self.endpoints.gmail_url}/messages/{_safe_id(message_id)}"
            f"/attachments/{_safe_id(attachment_id)}"
        )
        await self.slots.acquire(grant.user_id)
        try:
            with log.hide(grant.refresh_token, self._client_secret), log.quiet():
                async for piece in self._download(grant, url):
                    yield piece
        finally:
            self.slots.release(grant.user_id)

    async def _download(self, grant: Grant, url: str) -> AsyncIterator[bytes]:
        refreshed = False
        retries = iter(RETRY_DELAYS_S)
        while True:
            self.budget.spend(grant.user_id, UNITS["attachments.get"])
            token = await self._token(grant)
            try:
                with log.hide(token):
                    async with (
                        self._http() as http,
                        http.stream(
                            "GET",
                            url,
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Accept": "application/json",
                            },
                        ) as response,
                    ):
                        if response.status_code == 200:
                            chunks = response.aiter_bytes(CHUNK_BYTES)
                            async for piece in decode_attachment(chunks):
                                yield piece
                            return
                        body = bytearray()
                        async for chunk in response.aiter_bytes(CHUNK_BYTES):
                            body += chunk[: JSON_MAX_BYTES - len(body)]
                        answer = Answer(
                            response.status_code,
                            {name.lower(): value for name, value in response.headers.items()},
                            bytes(body),
                        )
            except httpx.TimeoutException:
                raise GoogleTimeoutError(reason="timeout") from None
            except (httpx.HTTPError, OSError):
                raise GoogleDownError(reason="unreachable") from None
            if answer.status == 401 and not refreshed:
                refreshed = True
                self.forget(grant.user_id)
                continue
            try:
                raise_for_answer(answer, "attachments.get")
            except GoogleError as error:
                delay = next(retries, None)
                short = error.retry_after is None or error.retry_after <= RETRY_AFTER_INLINE_S
                if delay is None or not error.retryable or not short:
                    log.event(
                        "mail.google.failed",
                        user_id=grant.user_id,
                        call="attachments.get",
                        code=error.code,
                        status=error.status,
                    )
                    raise
                await self._sleep(
                    float(error.retry_after)
                    if error.retry_after is not None
                    else delay * (1 + RETRY_JITTER * min(1.0, max(0.0, self._jitter())))
                )


def _fingerprint(grant: Grant) -> str:
    return hashlib.sha256(grant.refresh_token.encode("utf-8")).hexdigest()[:16]
