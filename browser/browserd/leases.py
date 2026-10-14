"""The one lease browserd holds at a time, and its clocks. No Playwright, no process here: the
manager is given a `Browser` factory, so this module runs in the api's unit tests with a fake
clock and a fake browser (api/tests/browser/test_browserd_leases.py).

Time rules (SPEC-browser, "Leases"): the purpose's maximum counts active time only; the idle
timer (no tool call and no input for BROWSER_IDLE_S) ends a lease; while the lease is
`waiting_person` neither runs, the wait has its own deadline (BROWSER_SESSION_MAX_S), and the
lease then resumes with the active time it had left. B12 sets and clears the wait.
"""

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from browserd.config import PURPOSES, UID_MAX, UID_MIN, Config

T = TypeVar("T")
log = logging.getLogger("browserd")
Clock = Callable[[], float]

LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")
TIMEZONE = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}$")
USER_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class LeaseError(Exception):
    """An answer of the control API: an HTTP status and a code, never a person's data."""

    def __init__(self, status: int, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.status, self.code, self.extra = status, code, extra


class LeaseEnded(Exception):
    """A tool call whose lease ended under it (maximum, idle, crash, release)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Browser(Protocol):
    async def stop(self) -> None: ...

    async def kill(self) -> None: ...


@dataclass(frozen=True)
class Request:
    user_id: str
    profile_id: str
    uid: int
    purpose: str
    automation: bool
    languages: tuple[str, ...]
    timezone: str

    @classmethod
    def parse(cls, body: Any) -> "Request":
        if not isinstance(body, dict):
            raise LeaseError(400, "bad_request")
        try:
            profile_id = str(uuid.UUID(str(body.get("profile_id", ""))))
        except ValueError:
            raise LeaseError(400, "bad_profile_id") from None
        uid = body.get("uid")
        if (
            not isinstance(uid, int)
            or isinstance(uid, bool)
            or not UID_MIN <= uid <= UID_MAX
        ):
            raise LeaseError(400, "bad_uid")
        purpose = body.get("purpose")
        if purpose not in PURPOSES:
            raise LeaseError(400, "bad_purpose")
        user_id = str(body.get("user_id", ""))
        if not USER_ID.match(user_id):
            raise LeaseError(400, "bad_user_id")
        languages = body.get("languages") or ["fr-FR", "fr", "en-US", "en"]
        if (
            not isinstance(languages, list)
            or not 1 <= len(languages) <= 6
            or not all(
                isinstance(lang, str) and LANGUAGE.match(lang) for lang in languages
            )
        ):
            raise LeaseError(400, "bad_languages")
        timezone = body.get("timezone") or "Europe/Paris"
        if (
            not isinstance(timezone, str)
            or not TIMEZONE.match(timezone)
            or ".." in timezone
        ):
            raise LeaseError(400, "bad_timezone")
        return cls(
            user_id=user_id,
            profile_id=profile_id,
            uid=uid,
            purpose=purpose,
            automation=bool(body.get("automation", True)),
            languages=tuple(languages),
            timezone=timezone,
        )


@dataclass
class Lease:
    request: Request
    max_s: int
    idle_s: int
    started: float
    lease_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    token_hash: bytes = b""
    state: str = "active"  # or "waiting_person"
    wait_reason: str | None = None
    wait_site: str | None = None
    wait_deadline: float | None = None
    active_before: float = (
        0.0  # active seconds banked before the current active stretch
    )
    active_since: float = 0.0
    last_input: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)

    def active_s(self, now: float) -> float:
        running = now - self.active_since if self.state == "active" else 0.0
        return self.active_before + running

    def touch(self, now: float) -> None:
        self.last_input = now

    def wait_person(self, now: float, reason: str, site: str, deadline_s: int) -> None:
        if self.state == "active":
            self.active_before = self.active_s(now)
        self.state, self.wait_reason, self.wait_site = "waiting_person", reason, site
        self.wait_deadline = now + deadline_s

    def resume(self, now: float) -> None:
        if self.state != "waiting_person":
            return
        self.state, self.wait_reason, self.wait_site, self.wait_deadline = (
            "active",
            None,
            None,
            None,
        )
        self.active_since = self.last_input = now

    def expired(self, now: float) -> str | None:
        """Why the lease must end now, or None."""
        if self.state == "waiting_person":
            return (
                "person_timeout"
                if self.wait_deadline and now >= self.wait_deadline
                else None
            )
        if self.active_s(now) >= self.max_s:
            return "lease_max"
        if now - self.last_input >= self.idle_s:
            return "idle"
        return None

    def public(self, now: float) -> dict[str, Any]:
        """The lease as GET /leases/current answers it to the api (never the token)."""
        return {
            "lease_id": self.lease_id,
            "user_id": self.request.user_id,
            "profile_id": self.request.profile_id,
            "purpose": self.request.purpose,
            "automation": self.request.automation,
            "state": self.state,
            "wait_reason": self.wait_reason,
            "active_s": round(self.active_s(now), 1),
            "max_s": self.max_s,
            "idle_s": self.idle_s,
            "events": list(self.events[-20:]),
        }


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class Manager:
    """Holds at most one lease. `start` brings a Browser up for a request (chromium.start in the
    service, a fake in the tests); `tick` is called every second by the service's loop."""

    def __init__(
        self,
        config: Config,
        start: Callable[[Request, str], Awaitable[Browser]],
        clock: Clock = time.monotonic,
    ) -> None:
        self.config, self._start, self.clock = config, start, clock
        self.lease: Lease | None = None
        self.browser: Browser | None = None
        self.starting: Request | None = None
        self.last_end: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._calls: set[asyncio.Task[Any]] = set()

    def holder_purpose(self) -> str | None:
        if self.lease:
            return self.lease.request.purpose
        return self.starting.purpose if self.starting else None

    async def acquire(self, request: Request) -> tuple[Lease, str]:
        # A second lease is refused with the holder's purpose, never its id or its person.
        holder = self.holder_purpose()
        if holder:
            raise LeaseError(409, "busy", holder_purpose=holder)
        async with self._lock:
            holder = self.holder_purpose()
            if holder:
                raise LeaseError(409, "busy", holder_purpose=holder)
            self.starting = request
            try:
                lease_id = str(uuid.uuid4())
                browser = await self._start(request, lease_id)
            except LeaseError:
                raise
            except Exception as exc:
                log.warning(
                    "lease start failed: %s: %s", type(exc).__name__, str(exc)[:300]
                )
                raise LeaseError(503, "browser_failed") from exc
            finally:
                self.starting = None
            now = self.clock()
            token = secrets.token_urlsafe(32)
            lease = Lease(
                request=request,
                max_s=self.config.lease_max_s[request.purpose],
                idle_s=self.config.idle_s,
                started=now,
                lease_id=lease_id,
                token_hash=_hash(token),
                active_since=now,
                last_input=now,
            )
            self.lease, self.browser = lease, browser
            return lease, token

    def current(self) -> Lease | None:
        return self.lease

    def check_token(self, lease_id: str, token: str) -> Lease:
        lease = self.lease
        if lease is None or lease.lease_id != lease_id:
            raise LeaseError(404, "no_such_lease")
        if not hmac.compare_digest(lease.token_hash, _hash(token)):
            raise LeaseError(403, "bad_lease_token")
        return lease

    async def call(
        self, lease_id: str, token: str, work: Callable[[], Awaitable[T]]
    ) -> T:
        """Runs one tool call on the lease (B5's tools go through here): it counts as input, and
        a lease that ends while it runs cancels it, so the call answers LeaseEnded."""
        lease = self.check_token(lease_id, token)
        if lease.state != "active":
            raise LeaseError(409, "waiting_person")
        lease.touch(self.clock())
        task = asyncio.ensure_future(work())
        self._calls.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            if task.cancelled() and self.lease is not lease:
                raise LeaseEnded((self.last_end or {}).get("reason", "ended")) from None
            raise
        finally:
            self._calls.discard(task)
            if self.lease is lease:
                lease.touch(self.clock())

    async def release(self, lease_id: str | None, reason: str) -> bool:
        """Ends the lease (all of them when lease_id is None): calls cancelled, Chromium closed
        cleanly (profile flushed), or killed when it no longer answers."""
        lease, browser = self.lease, self.browser
        if lease is None or (lease_id is not None and lease.lease_id != lease_id):
            return False
        self.lease, self.browser = None, None
        self.last_end = {"lease_id": lease.lease_id, "reason": reason}
        for task in list(self._calls):
            task.cancel()
        if browser is not None:
            if reason == "browser_crashed":
                await browser.kill()
            else:
                await browser.stop()
        return True

    async def tick(self) -> str | None:
        lease = self.lease
        if lease is None:
            return None
        reason = lease.expired(self.clock())
        if reason:
            await self.release(lease.lease_id, reason)
        return reason
