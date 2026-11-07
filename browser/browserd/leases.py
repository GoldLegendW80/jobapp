"""The one lease browserd holds at a time, and its clocks. No Playwright, no process here: the
manager is given a `Browser` factory, so this module runs in the api's unit tests with a fake
clock and a fake browser (api/tests/browser/test_browserd_leases.py).

Time rules (SPEC-browser, "Leases"): the purpose's maximum counts active time only; the idle
timer (no tool call and no input for BROWSER_IDLE_S) ends a lease; while the lease is
`waiting_person` neither runs, the wait has its own deadline (BROWSER_SESSION_MAX_S), and the
lease then resumes with the active time it had left. B12 sets and clears the wait
(browserd/handoff.py): a wait for a sign in that reaches its deadline ends `signin_timeout`, and
`swap` changes the lease's Chromium while it waits, one closed wholly before the other starts.

A person's own session (`takeover`, which the page calls `look`, and `signin`; B7) never runs
with automation, whatever the caller asks: its Chromium has no debugging port
(browserd/signin.py), the person holds the input from its start to its end, and every tool call
on it is refused 409 `person_session`. Its address, when the person typed one, is checked as
signin.check_address checks it.

Apply myself (`apply_manual`, apply A9) keeps its debugging port, so the app's tools can upload
the documents and fill identity fields on request, but the person holds the input from its start
to its end like a person's own session, except during the app's turn (`app_turn`, which the api
sets and clears through the live view's input route, browserd/live.py). A watch of the page
(`touch=False`) is not activity: the idle timer still ends a session nobody uses.

While a lease starts or ends (its Chromium starting or closing, the profile measured) browserd is
still busy, also when the caller went away: a new lease is refused 409 and the profile is not
removed until that work is done, and a start or swap revoked meanwhile never installs its
Chromium. An end that fails is done all the same: Chromium's end reaps every process of the
profile's uid even then, and every start reaps again first (chromium.py, B7).

Two browsers at once (owner, 2026-11-06; BROWSER_SLOTS, default 1 here, 2 in compose.yml): while
one person's agent leases (SLOT_PURPOSES) hold the browser, another of that person's agent leases
joins the same Chromium in a free slot, its own lease with its own id, token, clocks, tools and
live view, and the grant names its slot (`Request.slot`). Nobody else joins; a person's own
session never shares the browser; no lease joins while the browser starts, ends, is handed to
the person (B12) or holds a lease that is not an agent's. A slot's end closes its window
(Browser.end_slot) and the Chromium ends with the last slot.
"""

import asyncio
import contextlib
import hashlib
import hmac
import logging
import math
import re
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, TypeVar

from browserd.config import PURPOSES, SLOTS_MAX, UID_MAX, UID_MIN, Config
from browserd.tools import policy

T = TypeVar("T")
log = logging.getLogger("browserd")
Clock = Callable[[], float]
APPLY_WAITS = frozenset({"captcha", "email_code", "forbidden"})
APPLY_WAIT_S = 1800

LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")
TIMEZONE = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}$")
USER_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
PERSON_PURPOSES = ("takeover", "signin")
MANUAL_PURPOSE = "apply_manual"
# The purposes whose leases may share the browser in slots: an agent's, never a person's own.
SLOT_PURPOSES = tuple(p for p in PURPOSES if p not in (*PERSON_PURPOSES, MANUAL_PURPOSE))


class LeaseError(Exception):
    """An answer of the control API: an HTTP status and a code, never a person's data."""

    def __init__(self, status: int, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.status, self.code, self.extra = status, code, extra


class LeaseEnded(Exception):  # noqa: N818 - an outcome, not an error
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
    # B5: the hosts the tools may navigate to, and the apply adapter's own submit elements.
    hosts: tuple[str, ...] = ()
    submit_selectors: tuple[str, ...] = ()
    # B7: where a person's own session opens, http or https only (signin.check_address).
    address: str | None = None
    # The slot browserd granted (never read from the caller's body): its window and its view.
    slot: int = 0
    # The person's own browsers at once (the admin's setting, 2026-11-07): no lease of theirs
    # joins past it, BROWSER_SLOTS the ceiling; SLOTS_MAX when the caller does not say.
    browsers: int = SLOTS_MAX

    @classmethod
    def parse(cls, body: Any) -> "Request":
        if not isinstance(body, dict):
            raise LeaseError(400, "bad_request")
        try:
            profile_id = str(uuid.UUID(str(body.get("profile_id", ""))))
        except ValueError:
            raise LeaseError(400, "bad_profile_id") from None
        uid = body.get("uid")
        if not isinstance(uid, int) or isinstance(uid, bool) or not UID_MIN <= uid <= UID_MAX:
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
            or not all(isinstance(lang, str) and LANGUAGE.match(lang) for lang in languages)
        ):
            raise LeaseError(400, "bad_languages")
        timezone = body.get("timezone") or "Europe/Paris"
        if not isinstance(timezone, str) or not TIMEZONE.match(timezone) or ".." in timezone:
            raise LeaseError(400, "bad_timezone")
        try:
            hosts = policy.parse_hosts(body.get("hosts"))
            selectors = policy.parse_selectors(body.get("submit_selectors"))
        except ValueError as exc:
            raise LeaseError(400, f"bad_{exc}") from None
        browsers = body.get("browsers", SLOTS_MAX)
        if type(browsers) is not int or not 1 <= browsers <= SLOTS_MAX:
            raise LeaseError(400, "bad_browsers")
        automation = bool(body.get("automation", True)) and purpose not in PERSON_PURPOSES
        address = body.get("address")
        if address is not None:
            from browserd.signin import check_address  # signin imports this module

            address = check_address(address)
            if address is None or automation:
                raise LeaseError(400, "bad_address")
        return cls(
            user_id=user_id,
            profile_id=profile_id,
            uid=uid,
            purpose=purpose,
            automation=automation,
            languages=tuple(languages),
            timezone=timezone,
            hosts=hosts,
            submit_selectors=selectors,
            address=address,
            browsers=browsers,
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
    # B12b: the host of the noted address, shown to the person; never the model's site argument.
    wait_host: str | None = None
    wait_deadline: float | None = None
    active_before: float = 0.0  # active seconds banked before the current active stretch
    active_since: float = 0.0
    last_input: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)
    # B5: the hash of the gate's capability (empty: no call may click a submit element), and
    # what the tool layer keeps for this lease (its documents, its tab), dropped with it.
    gate_hash: bytes = b""
    scratch: dict[str, Any] = field(default_factory=dict)
    # B12: the person holds the live view's input of this agent's lease (the sign in hand off).
    person_input: bool = False
    # A9: during Apply myself, the app's tools hold the input (a "Fill my details" under way).
    app_turn: bool = False

    def active_s(self, now: float) -> float:
        running = now - self.active_since if self.state == "active" else 0.0
        return self.active_before + running

    def touch(self, now: float) -> None:
        self.last_input = now

    def wait_person(self, now: float, reason: str, host: str, deadline_s: int) -> None:
        if self.state == "waiting_person" and self.wait_reason in APPLY_WAITS:
            return
        if self.state == "active":
            self.active_before = self.active_s(now)
        self.state, self.wait_reason, self.wait_host = "waiting_person", reason, host
        self.wait_deadline = now + deadline_s

    def resume(self, now: float) -> None:
        if self.state != "waiting_person":
            return
        self.state, self.wait_reason, self.wait_host, self.wait_deadline = (
            "active",
            None,
            None,
            None,
        )
        self.active_since = self.last_input = now

    def wait_left_s(self, now: float) -> int | None:
        if self.state != "waiting_person" or self.wait_deadline is None:
            return None
        return max(0, math.ceil(self.wait_deadline - now))

    def expired(self, now: float) -> str | None:
        """Why the lease must end now, or None."""
        if self.state == "waiting_person":
            if self.wait_reason in ("takeover", "ask_person"):
                # Live.tick gives input back when the takeover allowance ends, and resumes an
                # ask nobody took over at its deadline (B20, tools/ask_person.py).
                return None
            if self.wait_deadline is None or now < self.wait_deadline:
                return None
            return "signin_timeout" if self.wait_reason == "signin" else "person_timeout"
        if self.active_s(now) >= self.max_s:
            return "lease_max"
        if now - self.last_input >= self.idle_s:
            return "idle"
        return None

    def public(self, now: float) -> dict[str, Any]:
        """The lease as GET /leases/current answers it to the api (never the token)."""
        return {
            "lease_id": self.lease_id,
            "slot": self.request.slot,
            "user_id": self.request.user_id,
            "profile_id": self.request.profile_id,
            "purpose": self.request.purpose,
            "automation": self.request.automation,
            "state": self.state,
            "wait_reason": self.wait_reason,
            # B12: the api reads the wait back after a Redis restart, never a fresh deadline.
            "wait_host": self.wait_host,
            "wait_left_s": self.wait_left_s(now),
            "active_s": round(self.active_s(now), 1),
            "max_s": self.max_s,
            "idle_s": self.idle_s,
            # A9: Apply myself warns the person a minute before the idle timer ends the session.
            "idle_left_s": max(0, math.ceil(self.idle_s - (now - self.last_input))),
            "events": list(self.events[-20:]),
        }


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass
class _Record:
    request: Request
    lease_id: str
    revoked: bool = False
    task: asyncio.Task[Any] | None = None
    browser: Browser | None = None

    @property
    def terminal(self) -> bool:
        return self.revoked and self.task is None and self.browser is None


class Manager:
    """Holds the leases of the one browser: one, or up to `config.slots` agent leases of one
    person (the slots). `start` brings a Browser up for the first lease (chromium.start in the
    service, a fake in the tests); `tick` is called every second by the service's loop."""

    def __init__(
        self,
        config: Config,
        start: Callable[[Request, str], Awaitable[Browser]],
        clock: Clock = time.monotonic,
        measure: Callable[[Request], Awaitable[int | None]] | None = None,
    ) -> None:
        self.config, self._start, self.clock = config, start, clock
        self._measure = measure  # B7: the profile's size at a lease end (chromium.measure_profile)
        self._held: dict[str, Lease] = {}
        self.browser: Browser | None = None
        self.starting: Request | None = None
        self.ending: Request | None = None
        self.removing: set[str] = set()  # profiles whose directory is being removed (signin.py)
        self.last_end: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._swap = asyncio.Lock()  # B12: one Chromium closes wholly before another starts
        self._calls: dict[str, set[asyncio.Task[Any]]] = {}
        self._records: dict[str, _Record] = {}

    @property
    def lease(self) -> Lease | None:
        """The lease of the lowest slot held: the only one while the browser has one."""
        held = self.held()
        return held[0] if held else None

    def held(self) -> list[Lease]:
        return sorted(self._held.values(), key=lambda lease: lease.request.slot)

    def get(self, lease_id: str | None) -> Lease | None:
        """That lease while it holds a slot, else None."""
        return self._held.get(lease_id) if lease_id else None

    def lease_of(self, request: Request) -> Lease | None:
        return next((lease for lease in self._held.values() if lease.request is request), None)

    def holder_purpose(self) -> str | None:
        for request in (self.lease.request if self.lease else None, self.starting, self.ending):
            if request is not None:
                return request.purpose
        return next((r.request.purpose for r in self._records.values() if not r.terminal), None)

    def uses(self, profile_id: str) -> bool:
        """A lease of that profile runs, starts or ends."""
        held = (*(lease.request for lease in self._held.values()), self.starting, self.ending)
        return any(r is not None and r.profile_id == profile_id for r in held) or any(
            not r.terminal and r.request.profile_id == profile_id for r in self._records.values()
        )

    def _free_slot(self, request: Request) -> int | None:
        """The slot a new lease of `request` joins, or None: it must start a browser or wait."""
        held = list(self._held.values())
        most = min(self.config.slots, request.browsers, *(lease.request.browsers for lease in held))
        if (
            most < 2
            or not held
            or len(held) >= most
            or self.browser is None
            or self.starting is not None
            or self.ending is not None
            or not request.automation
            or request.purpose not in SLOT_PURPOSES
            or request.profile_id in self.removing
        ):
            return None
        for lease in held:
            mine = lease.request
            record = self._records.get(lease.lease_id)
            if (
                (mine.user_id, mine.profile_id, mine.uid)
                != (request.user_id, request.profile_id, request.uid)
                or not mine.automation
                or mine.purpose not in SLOT_PURPOSES
                or lease.person_input  # B12: its Chromium has no debugging port now
                or record is None
                or record.revoked
                or record.task is not None  # a swap under way
            ):
                return None
        taken = {lease.request.slot for lease in held}
        return next(slot for slot in range(self.config.slots) if slot not in taken)

    def _own(self, record: _Record, work: Awaitable[T]) -> asyncio.Task[T]:
        task = asyncio.ensure_future(work)
        record.task = task

        def done(task: asyncio.Task[T]) -> None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                task.exception()  # Consume the outcome even when the caller has gone away.
            if record.task is not task:
                return
            record.task = None
            if self.starting is record.request:
                self.starting = None
            if record.revoked and record.lease_id in self._held:
                self._own(record, self.release(record.lease_id, "browser_failed"))

        task.add_done_callback(done)
        return task

    def _revoke(self, record: _Record) -> None:
        record.revoked = True  # Before yielding, including when the factory ignores cancel.
        if record.task is not None and not record.task.done():
            record.task.cancel()
        elif record.lease_id in self._held:
            self._own(record, self.release(record.lease_id, "browser_failed"))

    async def _close(self, record: _Record, *, crash: bool = False) -> None:
        browser = record.browser
        if browser is None:
            return
        try:
            await (browser.kill() if crash else browser.stop())
        finally:
            # An end that raises has still reaped the uid, and every start reaps it again (B7).
            record.browser = None

    def _make_lease(self, record: _Record) -> tuple[Lease, str]:
        now, token = self.clock(), secrets.token_urlsafe(32)
        lease = Lease(
            request=record.request,
            max_s=self.config.lease_max_s[record.request.purpose],
            idle_s=self.config.idle_s,
            started=now,
            lease_id=record.lease_id,
            token_hash=_hash(token),
            active_since=now,
            last_input=now,
        )
        self._held[lease.lease_id] = lease
        return lease, token

    def _record(self, request: Request) -> _Record:
        while len(self._records) >= 32:
            oldest = next((key for key, r in self._records.items() if r.terminal), None)
            if oldest is None:
                raise LeaseError(409, "busy", holder_purpose=request.purpose)
            del self._records[oldest]
        record = _Record(request, str(uuid.uuid4()))
        self._records[record.lease_id] = record
        return record

    async def acquire(self, request: Request) -> tuple[Lease, str]:
        # A second lease is refused with the holder's purpose, never its id or its person.
        holder = self.holder_purpose()
        if holder and self._free_slot(request) is None:
            raise LeaseError(409, "busy", holder_purpose=holder)
        async with self._lock:
            holder = self.holder_purpose()
            slot = self._free_slot(request) if holder else None
            if holder and slot is None:
                raise LeaseError(409, "busy", holder_purpose=holder)
            if request.profile_id in self.removing:
                raise LeaseError(409, "profile_removing")
            if slot is not None:
                # Another window of the person's running Chromium: nothing starts here, the
                # tools open the slot's window on its first call (chromium.Chromium.home).
                return self._make_lease(self._record(replace(request, slot=slot)))
            record = self._record(replace(request, slot=0))
            self.starting = record.request
            task = self._own(record, self._initial(record))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._revoke(record)
            raise
        except LeaseError:
            raise
        except Exception as exc:
            log.warning("lease start failed: %s: %s", type(exc).__name__, str(exc)[:300])
            raise LeaseError(503, "browser_failed") from exc

    async def _initial(self, record: _Record) -> tuple[Lease, str]:
        async with self._swap:
            try:
                if record.revoked:
                    raise asyncio.CancelledError
                record.browser = await self._start(record.request, record.lease_id)
                async with self._lock:
                    if record.revoked:
                        raise asyncio.CancelledError
                    self.browser = record.browser
                    return self._make_lease(record)
            except BaseException:
                record.revoked = True
                with contextlib.suppress(Exception):
                    await self._close(record)
                raise

    def current(self) -> Lease | None:
        return self.lease

    def check_token(self, lease_id: str, token: str) -> Lease:
        lease = self._held.get(lease_id)
        if lease is None or self._records[lease_id].revoked:
            raise LeaseError(404, "no_such_lease")
        if not hmac.compare_digest(lease.token_hash, _hash(token)):
            raise LeaseError(403, "bad_lease_token")
        return lease

    async def call(
        self,
        lease_id: str,
        token: str,
        work: Callable[[], Awaitable[T]],
        *,
        touch: bool = True,
        passive_wait: bool = False,
    ) -> T:
        """Runs one tool call on the lease (B5's tools go through here): it counts as input
        unless `touch` is False (Apply myself's watch, A9), and a lease that ends while it runs
        cancels it, so the call answers LeaseEnded."""
        lease = self.check_token(lease_id, token)
        if not lease.request.automation:
            raise LeaseError(409, "person_session")
        waiting_read = (
            passive_wait
            and not touch
            and lease.request.purpose == "apply_fill"
            and lease.wait_reason in APPLY_WAITS
        )
        if lease.state != "active" and lease.wait_reason != "takeover" and not waiting_read:
            raise LeaseError(409, "waiting_person")
        if touch:
            lease.touch(self.clock())
        task = asyncio.ensure_future(work())
        calls = self._calls.setdefault(lease_id, set())
        calls.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            if task.cancelled() and self._held.get(lease_id) is not lease:
                reason = "ended"
                if self.last_end and self.last_end.get("lease_id") == lease_id:
                    reason = self.last_end.get("reason", "ended")
                raise LeaseEnded(reason) from None
            raise
        finally:
            calls.discard(task)
            if not calls and self._calls.get(lease_id) is calls:
                del self._calls[lease_id]
            if touch and self._held.get(lease_id) is lease:
                lease.touch(self.clock())

    def _cancel_calls(self, lease_id: str) -> None:
        for task in list(self._calls.pop(lease_id, ())):
            task.cancel()

    async def release(self, lease_id: str | None, reason: str) -> bool:
        """Ends the lease (all of them when lease_id is None): calls cancelled, its slot's window
        closed while another slot holds the browser, else Chromium closed cleanly (profile
        flushed), or killed when it no longer answers; then, but at a shutdown, the profile
        trimmed and measured: `last_end` carries its size (B7)."""
        if lease_id is None and len(self._held) > 1:
            ended = False
            for lease in self.held()[1:]:
                ended = await self.release(lease.lease_id, reason) or ended
            return await self.release(None, reason) or ended
        lease = self._held.get(lease_id) if lease_id is not None else self.lease
        if lease is None:
            if not self._held:
                for record in self._records.values():
                    if (
                        record.task is not None
                        and not record.revoked
                        and (lease_id is None or record.lease_id == lease_id)
                    ):
                        record.revoked = True
                        record.task.cancel()
            return False
        if len(self._held) > 1 and reason != "browser_crashed":
            return await self._end_slot(lease, reason)
        for other in self.held():  # a crash of the shared Chromium ends every slot with it
            if other is not lease:
                self._drop_slot(other)
        record = self._records[lease.lease_id]
        record.revoked = True
        del self._held[lease.lease_id]
        self.ending = lease.request
        end = self.last_end = {"lease_id": lease.lease_id, "reason": reason}
        try:
            self._cancel_calls(lease.lease_id)
            for kept in lease.scratch.values():  # the tools' state: documents dropped now (B5)
                drop = getattr(kept, "end", None)
                if callable(drop):
                    drop()
            # After a swap under way (B12): it starts nothing more once the lease is gone.
            async with self._swap:
                self.browser = None
                try:
                    await self._close(record, crash=reason == "browser_crashed")
                except Exception as exc:  # the browser finished its end anyway (chromium.py)
                    log.warning("lease %s: end failed: %s", lease.lease_id, type(exc).__name__)
            if self._measure is not None and reason != "shutdown":
                try:
                    end["size_bytes"] = await self._measure(lease.request)
                except Exception as exc:
                    log.warning("profile not measured: %s", type(exc).__name__)
        finally:
            self.ending = None
        return True

    def _drop_slot(self, lease: Lease) -> None:
        """A slot's lease leaves the browser: its calls and its tools' state go; the Chromium,
        if this lease's record holds it, goes to the lowest slot left."""
        record = self._records[lease.lease_id]
        record.revoked = True
        del self._held[lease.lease_id]
        if record.browser is not None and self._held:
            heir = self._records[self.held()[0].lease_id]
            heir.browser, record.browser = record.browser, None
        self._cancel_calls(lease.lease_id)
        for kept in lease.scratch.values():
            drop = getattr(kept, "end", None)
            if callable(drop):
                drop()

    async def _end_slot(self, lease: Lease, reason: str) -> bool:
        """One slot ends while another holds the browser: its window closes (Browser.end_slot,
        its cookies kept in the profile first), the Chromium stays for the others."""
        self._drop_slot(lease)
        self.last_end = {"lease_id": lease.lease_id, "reason": reason}
        end_slot = getattr(self.browser, "end_slot", None)
        if callable(end_slot):
            try:
                await end_slot(lease.lease_id)
            except Exception as exc:
                log.warning("lease %s: slot end failed: %s", lease.lease_id, type(exc).__name__)
        return True

    async def release_browser(self, browser: Browser, reason: str) -> bool:
        """That Chromium crashed or closed by itself: every lease it serves ends. One that no
        longer serves a lease (swapped, or ended) changes nothing."""
        if browser is None or self.browser is not browser:
            return False
        return await self.release(None, reason)

    async def swap(self, lease: Lease, request: Request) -> bool:
        """The sign in hand off (B12, browserd/handoff.py): the lease's Chromium is closed
        wholly (profile written, its process group gone), then `request` starts another for the
        same lease, with or without a debugging port. `release` takes the same lock, so two never
        run at once. False when the lease ended meanwhile (the new one, if any, closed again),
        or shares the browser with another slot (handoff.request never asks then).
        Whatever fails on the way (a stop, a start) ends the lease `browser_failed`: a lease
        never stays with no Chromium, and none starts beside one that may still run."""
        record = self._records.get(lease.lease_id)
        if (
            record is None
            or record.revoked
            or self._held.get(lease.lease_id) is not lease
            or len(self._held) > 1
            or record.task is not None
        ):
            return False
        task = self._own(
            record, self._replace(lease, replace(request, slot=lease.request.slot), record)
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._revoke(record)
            raise

    async def _replace(self, lease: Lease, request: Request, record: _Record) -> bool:
        swapped = False
        try:
            async with self._swap:
                if self._held.get(lease.lease_id) is not lease or record.revoked:
                    return False
                old, self.browser = self.browser, None
                if old is not None:
                    try:
                        await self._close(record)
                    except BaseException:
                        with contextlib.suppress(Exception):
                            await old.kill()
                        raise
                if self._held.get(lease.lease_id) is not lease or record.revoked:
                    return False
                new = await self._start(request, lease.lease_id)
                record.browser = new
                async with self._lock:
                    if self._held.get(lease.lease_id) is lease and not record.revoked:
                        self.browser, swapped = new, True
                        return True
                await self._close(record)
                return False
        except Exception as exc:
            log.warning("lease swap failed: %s", type(exc).__name__)
            return False
        finally:
            if not swapped:
                await self.release(lease.lease_id, "browser_failed")

    async def tick(self) -> str | None:
        """Ends every lease whose time is up; the last reason, or None."""
        ended = None
        for lease in self.held():
            reason = lease.expired(self.clock())
            if reason and self._held.get(lease.lease_id) is lease:
                await self.release(lease.lease_id, reason)
                ended = reason
        return ended
