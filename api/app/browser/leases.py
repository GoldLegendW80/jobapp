"""Who holds the one browser of the Jetson, and who waits for it (browser B3).

One Chromium runs at a time (SPEC-browser, "Memory on the Jetson"), so a lease is a turn: the api
keeps the turns in Redis and browserd (app/browser/client.py) mirrors the holder and refuses a
second one. The keys, all under `jobapp:browser:`:

    holder          the current lease as JSON, written and ended by compare and set only
    queue:<tier>    one sorted set per priority tier, members `<user id>|<purpose>`, in order of
                    arrival; tier 0 first: a person's own session (`takeover`, `signin`, and
                    Apply myself's `apply_manual`, apply A9), then
                    the apply purposes a person waits on, then `full_auto`, then `sweep`
    ticket:<member> present while that waiter still waits: it lives TICKET_TTL_S and is renewed
                    each time the waiter asks again; a member whose ticket is gone leaves the queue
    durations       the lengths of the last ten leases, for the estimated wait
    synced          set once this Redis has read browserd's current lease (below)
    seen:<lease id> when browserd last answered that it still holds that lease (`held`), and
                    since when it has not answered (`unanswered`, cleared by the next answer)
    ending          the leases ended here whose end browserd has not confirmed yet
    forgets:<user id>:<day>  how many "Forget all logins" the person spent that UTC day (B7)

`acquire(user_id, purpose, automation)` is idempotent per person and purpose, also for two calls
at once (two tabs): the one that loses the turn to the other answers that same lease, and leaves no
place in the queue behind it. The first call queues, each call renews the ticket and takes the
turn when the browser is free and this member is the head of the first non empty tier (one Lua
script, so two callers never both win), then starts the lease in browserd on the person's one
profile (app/browser/profiles.py). There is no preemption but one: a `sweep` lease ends at
once when a person asks for their own browser (`takeover`, `signin`; B8): the person is queued
first, then the sweep ends, then the person claims, so no other waiter takes the turn in between.
Any other lease ends first. A waiter
keeps its place by asking again within TICKET_TTL_S; the caller that took the turn gets browserd's
lease token once, in memory: it is never written to Redis nor logged.

Time (SPEC-browser, "Leases"): the purpose's maximum counts active time only, and the idle timer
(BROWSER_IDLE_S without a tool call or input, `touch`) ends a lease; while the lease is
`waiting_person` (B12 sets it, with a reason, a host and a deadline of BROWSER_SESSION_MAX_S)
neither runs, and `resume` gives the lease back the active time it had left. The sign in hand off
(app/browser/handoff.py) sets and clears this copy's wait in step with browserd's, with the
seconds browserd has left; a lease already waiting keeps its deadline, and after a Redis restart
the wait is read back from browserd with what it has left, never a fresh deadline. A wait for a
sign in past its deadline ends `signin_timeout`. browserd applies the
same rules to its own copy and sees every tool call and every input, so while it answers and still
holds the lease, it decides idle and maximum (nobody has to `touch` this copy in step). Two
timers of this copy still apply:
- whatever browserd answers, a lease ends MAX_GRACE_S after its maximum of active time, or after
  the deadline of its wait for the person (a browserd that answers "held" forever never keeps the
  browser forever);
- when browserd cannot be asked, the idle timer counts from the latest of the last `touch`, the
  last answer of browserd that it still held the lease, and the first call it did not answer: so
  one failed or slow call never ends a busy lease, and BROWSER_IDLE_S without an answer from
  browserd does (a holder whose process died expires here).
Every call first settles the holder: one that browserd no longer holds (its own idle or maximum, a
crash) is dropped, and an expired one is ended. An end browserd could not be told is kept in
`ending` and told on every later call until browserd confirms it, and a lease of that list is
never taken back from browserd, so the api and browserd agree again as soon as it answers. When
Redis has lost its keys (a restart), the first call reads browserd's `GET /leases/current` and
takes its lease back as the holder.

At every end browserd measures the profile once Chromium closed (B7); the size it answers (to
the end this store asked for, or in `last_end` for an end browserd decided) is written to that
profile's row with its last use. A person's own session may open at an address the person typed
(B7, checked by app/browser/address.py): it goes to browserd with the start, never to Redis.

`current(user_id)` answers the person's own lease, whether someone else holds the browser (never
who, nor why), and the person's places in the queue. The estimated wait is rounded up to whole
minutes, so a waiter learns how long another person's lease has run to the minute at best, never
to the second (test_the_estimated_wait_hides_the_holders_running_time). Every time comes from the
injected clock.
"""

import asyncio
import contextlib
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from redis.exceptions import RedisError
from sqlalchemy.orm import Session, sessionmaker

from app.browser import profiles
from app.browser.client import BrowserdClient, BrowserError, ended_of
from app.browser.profiles import ProfileRef

logger = logging.getLogger("app.browser.leases")

PREFIX = "jobapp:browser:"
HOLDER = PREFIX + "holder"
SEQ = PREFIX + "seq"
DURATIONS = PREFIX + "durations"
SYNCED = PREFIX + "synced"
TICKET = PREFIX + "ticket:"
SEEN = PREFIX + "seen:"
ENDING = PREFIX + "ending"
FORGETS = PREFIX + "forgets:"

# Active seconds per purpose: browserd's table (browser/browserd/config.py, the test compares).
LEASE_MAX_S = {
    "takeover": 1800,
    "signin": 1800,
    "apply_manual": 1800,
    "apply_fill": 900,
    "apply_submit": 900,
    "profile_edit": 900,
    "full_auto": 900,
    "sweep": 900,
}
PURPOSES = tuple(LEASE_MAX_S)
TIERS = {
    "takeover": 0,
    "signin": 0,
    "apply_manual": 0,
    "apply_fill": 1,
    "apply_submit": 1,
    "profile_edit": 1,
    "full_auto": 2,
    "sweep": 3,
}
PREEMPTS_SWEEP = ("takeover", "signin")  # a person's own browser ends a sweep lease (B8)
QUEUES = tuple(f"{PREFIX}queue:{tier}" for tier in range(4))
TICKET_TTL_S = 60  # a waiter that has not asked again for this long leaves the queue
START_GRACE_S = 60  # a turn still starting after this is dropped (the caller died mid start)
MAX_GRACE_S = 60  # past the maximum or the wait deadline, the api ends a lease browserd still holds
SEEN_TTL_S = 86400  # a sighting nobody refreshed for a day is forgotten (renewed on every call)
HISTORY = 10
DEFAULT_LEASE_S = 300  # the estimate's lease length while no lease has ended yet
UPDATE_ATTEMPTS = 5
HOST_MAX = 253  # a DNS name's longest form: the host a sign in waits on, as browserd noted it

# Drops the members whose ticket is gone, then, when ARGV[1] names a member, gives it the turn
# if nobody holds one and it is the head of the first non empty tier.
# KEYS: the holder, then the queues by tier. ARGV: member ('' prunes only), holder JSON, prefix.
CLAIM = """
for i = 2, #KEYS do
  for _, m in ipairs(redis.call('ZRANGE', KEYS[i], 0, -1)) do
    if redis.call('EXISTS', ARGV[3] .. m) == 0 then redis.call('ZREM', KEYS[i], m) end
  end
end
if ARGV[1] == '' or redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
for i = 2, #KEYS do
  local head = redis.call('ZRANGE', KEYS[i], 0, 0)[1]
  if head then
    if head ~= ARGV[1] then return 0 end
    redis.call('ZREM', KEYS[i], head)
    redis.call('DEL', ARGV[3] .. head)
    redis.call('SET', KEYS[1], ARGV[2])
    return 1
  end
end
return 0
"""

# Compare and set: KEYS[1] becomes ARGV[2] ('' deletes it) only if it still holds ARGV[1].
CAS = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
if ARGV[2] == '' then redis.call('DEL', KEYS[1]) else redis.call('SET', KEYS[1], ARGV[2]) end
return 1
"""

# What browserd answered about one lease. ARGV: 'held' (it holds it) or 'unanswered', the time,
# the TTL. Answers the hash's `held` and `unanswered` (nil when unset).
SIGHT = """
if ARGV[1] == 'held' then
  redis.call('HSET', KEYS[1], 'held', ARGV[2])
  redis.call('HDEL', KEYS[1], 'unanswered')
else
  redis.call('HSETNX', KEYS[1], 'unanswered', ARGV[2])
end
redis.call('EXPIRE', KEYS[1], ARGV[3])
return redis.call('HMGET', KEYS[1], 'held', 'unanswered')
"""

Clock = Callable[[], float]
_CANNOT_ASK = "\0cannot_ask"  # _gone_in_browserd: browserd did not answer


class StoreUnavailableError(Exception):
    """Redis could not be reached: nobody can take or end a turn now."""


def lease_max_table(raw: str) -> dict[str, int]:
    """BROWSER_LEASE_MAX_S, "purpose=seconds,..." over LEASE_MAX_S, read as browserd reads it."""
    table = dict(LEASE_MAX_S)
    for item in filter(None, (part.strip() for part in raw.split(","))):
        purpose, _, seconds = item.partition("=")
        if purpose.strip() not in table or not re.fullmatch(r"\d{2,5}", seconds.strip()):
            raise ValueError(f"BROWSER_LEASE_MAX_S: {item!r} is not purpose=seconds")
        table[purpose.strip()] = int(seconds)
    return table


@dataclass
class Lease:
    """The holder's turn. `lease_id` is browserd's lease id once it started (`starting:<uuid>`
    before). Times are seconds of the store's clock."""

    lease_id: str
    user_id: str
    purpose: str
    automation: bool
    profile_id: str
    max_s: int
    idle_s: int
    started_at: float
    state: str = "starting"  # then "active", or "waiting_person"
    wait_reason: str | None = None
    wait_host: str | None = None  # browserd's host of the noted address, never the model's words
    wait_deadline: float | None = None
    active_before: float = 0.0  # active seconds banked before the current active stretch
    active_since: float = 0.0
    last_input: float = 0.0

    def active_s(self, now: float) -> float:
        running = now - self.active_since if self.state == "active" else 0.0
        return self.active_before + running

    def touch(self, now: float) -> None:
        self.last_input = now

    def wait_person(self, now: float, reason: str, host: str, deadline_s: int) -> None:
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

    def expired(self, now: float, alive_at: float = 0.0) -> str | None:
        """Why the turn must end now, or None. `alive_at`: the last time the lease was known to
        be in use other than by `touch` (the idle timer counts from the later of the two)."""
        if self.state == "starting":
            return "start_failed" if now - self.started_at >= START_GRACE_S else None
        if self.state == "waiting_person":
            if self.wait_deadline is not None and now >= self.wait_deadline:
                return self._wait_timeout()
            return None
        if self.active_s(now) >= self.max_s:
            return "lease_max"
        if now - max(self.last_input, alive_at) >= self.idle_s:
            return "idle"
        return None

    def overdue(self, now: float) -> str | None:
        """Why the turn must end now whatever browserd answers: MAX_GRACE_S past the maximum of
        active time, or past the deadline of the wait for the person."""
        if self.state == "waiting_person":
            if self.wait_deadline is not None and now >= self.wait_deadline + MAX_GRACE_S:
                return self._wait_timeout()
            return None
        if self.state == "active" and self.active_s(now) >= self.max_s + MAX_GRACE_S:
            return "lease_max"
        return None

    def _wait_timeout(self) -> str:
        return "signin_timeout" if self.wait_reason == "signin" else "person_timeout"

    def dumps(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def loads(cls, raw: str | bytes) -> "Lease":
        known = {f.name for f in fields(cls)}
        # A lease stored by an older api (its `wait_site`) still loads.
        return cls(**{k: v for k, v in json.loads(raw).items() if k in known})


@dataclass(frozen=True)
class Waiting:
    purpose: str
    position: int  # 1: the next turn
    estimated_wait_s: int


@dataclass(frozen=True)
class Acquired:
    """What `acquire` gives: the caller's lease, or its place in the queue. `token` is set on the
    call that started the lease only (browserd's lease token, for the tool API of B5)."""

    lease: Lease | None = None
    waiting: Waiting | None = None
    token: str | None = field(default=None, repr=False)
    # B5: the gate's capability, on the starting call of an apply_submit or profile_edit lease.
    capability: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class View:
    lease: Lease | None  # the person's own, never another's
    held_by_other: bool
    waiting: tuple[Waiting, ...]


class Profiles(Protocol):
    async def ensure(self, user_id: str) -> ProfileRef: ...

    async def mark_used(
        self, user_id: str, at: float, size_bytes: int | None = None, profile_id: str | None = None
    ) -> None: ...


class DbProfiles:
    """app/browser/profiles.py in a thread: the sessions are synchronous."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    async def ensure(self, user_id: str) -> ProfileRef:
        return await asyncio.to_thread(profiles.ensure, self.sessions, uuid.UUID(user_id))

    async def mark_used(
        self, user_id: str, at: float, size_bytes: int | None = None, profile_id: str | None = None
    ) -> None:
        when = datetime.fromtimestamp(at, UTC)
        await asyncio.to_thread(
            profiles.mark_used, self.sessions, uuid.UUID(user_id), when, size_bytes, profile_id
        )


def _member(user_id: str, purpose: str) -> str:
    return f"{user_id}|{purpose}"


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


class Leases:
    def __init__(
        self,
        pool: Callable[[], Any],
        client: BrowserdClient,
        profile_store: Profiles,
        *,
        idle_s: int,
        session_max_s: int,
        max_table: dict[str, int] | None = None,
        clock: Clock = time.time,
    ) -> None:
        self._pool, self.client, self.profiles = pool, client, profile_store
        self.idle_s, self.session_max_s = idle_s, session_max_s
        self.max_table = dict(max_table or LEASE_MAX_S)
        self.clock = clock

    # --- the public calls ----------------------------------------------------------------------

    async def acquire(
        self,
        user_id: str,
        purpose: str,
        automation: bool,
        *,
        hosts: Sequence[str] = (),
        submit_selectors: Sequence[str] = (),
        address: str | None = None,
    ) -> Acquired:
        """The caller's lease, started now or already held, or its place in the queue. Raises
        BrowserError when browserd cannot start it, StoreUnavailableError without Redis.
        `hosts` and `submit_selectors` (B5) are given to browserd by the call that starts the
        lease: the hosts its tools may open, the adapter's own submit elements; `address` (B7)
        is where a person's own session opens."""
        if purpose not in TIERS:
            raise ValueError(purpose)
        try:
            r = await self._pool()
            now = self.clock()
            held = await self._settle(r, now)
            if held is not None and held.user_id == user_id and held.purpose == purpose:
                return Acquired(lease=held)
            member = _member(user_id, purpose)
            if held is not None and held.purpose == "sweep" and purpose in PREEMPTS_SWEEP:
                # B8: the one preemption. A sweep ends at once when a person asks for their own
                # browser, and resumes at the next sweep (app/browser/sources). The person's
                # place is queued (tier 0) before the sweep ends: while browserd closes the
                # sweep's Chromium, any other waiter's claim finds this member at the head and
                # never takes the turn in between.
                await r.set(TICKET + member, "1", ex=TICKET_TTL_S)
                await r.zadd(QUEUES[TIERS[purpose]], {member: await r.incr(SEQ)}, nx=True)
                raw_held = _text(await r.get(HOLDER))
                if raw_held is not None and Lease.loads(raw_held).lease_id == held.lease_id:
                    await self._end(r, raw_held, held, "preempted", now)
            turn = Lease(
                lease_id=f"starting:{uuid.uuid4()}",
                user_id=user_id,
                purpose=purpose,
                automation=automation,
                profile_id="",
                max_s=self.max_table[purpose],
                idle_s=self.idle_s,
                started_at=now,
                active_since=now,
                last_input=now,
            )
            raw = turn.dumps()
            for _ in range(2):
                # The ticket first: a member without one is pruned by the next claim.
                await r.set(TICKET + member, "1", ex=TICKET_TTL_S)
                await r.zadd(QUEUES[TIERS[purpose]], {member: await r.incr(SEQ)}, nx=True)
                if await r.eval(CLAIM, 1 + len(QUEUES), HOLDER, *QUEUES, member, raw, TICKET):
                    return await self._start(r, turn, raw, hosts, submit_selectors, address)
                mine = await self._own(r, user_id, purpose)
                if mine is not None:
                    return Acquired(lease=mine)
                place = await self._place(r, user_id, purpose, now)
                if place is not None:
                    return Acquired(waiting=place)
                # The other call of this member claimed and then lost its turn before this one
                # read the holder: ask once more, as that call's caller will.
            return Acquired(waiting=await self._place(r, user_id, purpose, now))
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None

    async def release(
        self,
        user_id: str,
        lease_id: str | None = None,
        reason: str = "released",
        purposes: Collection[str] | None = None,
    ) -> bool:
        """Ends the person's lease (that one only, when `lease_id` is given; of those purposes
        only, when `purposes` is). False when the person holds no such lease."""
        try:
            r = await self._pool()
            raw = _text(await r.get(HOLDER))
            if raw is None:
                return False
            lease = Lease.loads(raw)
            if lease.user_id != user_id or (lease_id is not None and lease.lease_id != lease_id):
                return False
            if purposes is not None and lease.purpose not in purposes:
                return False
            return await self._end(r, raw, lease, reason, self.clock())
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None

    async def take_forget(self, user_id: str, per_day: int) -> str | None:
        """Spends one of the person's `per_day` Forgets of the UTC day of this store's clock and
        answers the day's key, for give_back_forget; None, and nothing spent, when none is left.
        Two calls at once never both spend the last one."""
        day = datetime.fromtimestamp(self.clock(), UTC).date().isoformat()
        key = f"{FORGETS}{user_id}:{day}"
        try:
            r = await self._pool()
            spent = int(await r.incr(key))
            await r.expire(key, 2 * 86400)
            if spent > per_day:
                await r.decr(key)
                return None
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None
        return key

    async def give_back_forget(self, key: str) -> None:
        """A Forget that found nothing to forget gives its place back (best effort)."""
        with contextlib.suppress(RedisError, OSError, TimeoutError):
            r = await self._pool()
            await r.decr(key)

    async def cancel(self, user_id: str, purposes: Collection[str] | None = None) -> int:
        """Takes the person out of the queue (for those purposes); how many places were left."""
        left = 0
        try:
            r = await self._pool()
            for purpose in purposes or PURPOSES:
                member = _member(user_id, purpose)
                left += int(await r.zrem(QUEUES[TIERS[purpose]], member))
                await r.delete(TICKET + member)
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None
        return left

    async def current(self, user_id: str) -> View:
        try:
            r = await self._pool()
            now = self.clock()
            held = await self._settle(r, now)
            await r.eval(CLAIM, 1 + len(QUEUES), HOLDER, *QUEUES, "", "", TICKET)
            places = []
            for purpose in PURPOSES:
                place = await self._place(r, user_id, purpose, now)
                if place is not None:
                    places.append(place)
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None
        mine = held if held is not None and held.user_id == user_id else None
        return View(
            lease=mine,
            held_by_other=held is not None and mine is None,
            waiting=tuple(places),
        )

    async def holds(self, user_id: str, lease_id: str) -> bool:
        """Whether the person holds that lease now, as this store last knew it: one read, no
        call to browserd (B5's tools ask it before each call; browserd then checks the token)."""
        try:
            r = await self._pool()
            raw = _text(await r.get(HOLDER))
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None
        if raw is None:
            return False
        lease = Lease.loads(raw)
        return lease.user_id == user_id and lease.lease_id == lease_id and lease.state != "starting"

    async def touch(self, user_id: str, lease_id: str) -> Lease | None:
        """A tool call or an input on the lease: the idle timer starts again."""
        return await self._update(user_id, lease_id, lambda lease, now: lease.touch(now))

    async def wait_person(
        self, user_id: str, lease_id: str, reason: str, host: str, left_s: int | None = None
    ) -> Lease | None:
        """B12: the lease waits for the person, both timers suspended, until the deadline:
        `left_s` from now (browserd's own, at most BROWSER_SESSION_MAX_S). A lease that already
        waits keeps its deadline."""
        deadline_s = (
            self.session_max_s if left_s is None else max(0, min(left_s, self.session_max_s))
        )

        def wait(lease: Lease, now: float) -> None:
            if lease.state != "waiting_person":
                lease.wait_person(now, reason, host, deadline_s)

        return await self._update(user_id, lease_id, wait)

    async def resume(self, user_id: str, lease_id: str) -> Lease | None:
        return await self._update(user_id, lease_id, lambda lease, now: lease.resume(now))

    async def apply_wait(self, user_id: str, lease_id: str, found: dict[str, Any]) -> Lease | None:
        """Mirror the private browser-owned Apply wait, with no refresh on readback."""

        def sync(lease: Lease, now: float) -> None:
            if lease.purpose != "apply_fill" or found.get("lease_id") != lease_id:
                raise BrowserError("refused")
            if found.get("state") == "active":
                lease.resume(now)
            elif found.get("wait_reason") in {"captcha", "email_code", "forbidden"}:
                left = max(0, min(float(found.get("wait_left_s") or 0), 1800))
                deadline = now + left
                if lease.state == "waiting_person" and lease.wait_deadline is not None:
                    deadline = min(deadline, lease.wait_deadline)
                lease.active_before = float(found.get("active_s") or 0)
                lease.state, lease.wait_reason, lease.wait_host = (
                    "waiting_person",
                    found["wait_reason"],
                    "",
                )
                lease.wait_deadline = deadline
            else:
                raise BrowserError("refused")

        return await self._update(user_id, lease_id, sync)

    # --- inside ----------------------------------------------------------------------------------

    async def _start(
        self,
        r: Any,
        turn: Lease,
        raw: str,
        hosts: Sequence[str] = (),
        submit_selectors: Sequence[str] = (),
        address: str | None = None,
    ) -> Acquired:
        try:
            profile = await self.profiles.ensure(turn.user_id)
            started = await self.client.start(
                user_id=turn.user_id,
                profile_id=profile.profile_id,
                uid=profile.uid,
                purpose=turn.purpose,
                automation=turn.automation,
                hosts=hosts,
                submit_selectors=submit_selectors,
                address=address,
            )
        except BrowserError as exc:
            await r.eval(CAS, 1, HOLDER, raw, "")
            if exc.code != "busy":
                raise
            # browserd holds a lease this Redis does not know: read it back on the next call,
            # and keep this waiter at the head of its tier.
            await r.delete(SYNCED)
            member = _member(turn.user_id, turn.purpose)
            await r.set(TICKET + member, "1", ex=TICKET_TTL_S)
            await r.zadd(QUEUES[TIERS[turn.purpose]], {member: 0}, nx=True)
            return Acquired(waiting=await self._place(r, turn.user_id, turn.purpose, self.clock()))
        except BaseException:
            await r.eval(CAS, 1, HOLDER, raw, "")
            raise
        now = self.clock()
        lease = replace(
            turn,
            lease_id=started.lease_id,
            profile_id=profile.profile_id,
            state="active",
            started_at=now,
            active_since=now,
            last_input=now,
        )
        if not await r.eval(CAS, 1, HOLDER, raw, lease.dumps()):
            # The turn was dropped while Chromium started (START_GRACE_S): nobody wants it now.
            await self._tell_end(r, started.lease_id)
            raise BrowserError("unavailable")
        await self.profiles.mark_used(turn.user_id, now)
        logger.info("browser lease started: purpose=%s", turn.purpose)
        return Acquired(lease=lease, token=started.token, capability=started.capability)

    async def _settle(self, r: Any, now: float) -> Lease | None:
        """The holder after ending an expired one or one browserd no longer holds."""
        await self._tell_ending(r)
        raw = _text(await r.get(HOLDER))
        if raw is None:
            if await r.exists(SYNCED):
                return None
            raw = await self._sync(r, now)
            if raw is None:
                return None
        lease = Lease.loads(raw)
        size: int | None = None
        if lease.state == "starting":
            reason = lease.expired(now)
        else:
            # browserd decides while it answers, within this copy's bound; this copy's idle
            # timer only when it cannot be asked, counted from browserd's last sign of the lease.
            reason = lease.overdue(now)
            if reason is None:
                reason, size = await self._gone_in_browserd(lease)
            if reason is None or reason == _CANNOT_ASK:
                answer = "held" if reason is None else "unanswered"
                seen = await r.eval(SIGHT, 1, SEEN + lease.lease_id, answer, now, SEEN_TTL_S)
                if reason == _CANNOT_ASK:
                    alive_at = max((float(_text(x) or 0.0) for x in seen), default=0.0)
                    reason = lease.expired(now, alive_at)
        if reason is None:
            return lease
        await self._end(r, raw, lease, reason, now, size)
        return None

    async def _sync(self, r: Any, now: float) -> str | None:
        """After Redis lost its keys: browserd's current lease becomes the holder again."""
        try:
            found = (await self.client.current())["lease"]
        except BrowserError:
            return None  # asked again on the next call
        if found is not None:
            try:
                active_s = float(found.get("active_s") or 0.0)
                lease = Lease(
                    lease_id=str(found["lease_id"]),
                    user_id=str(found["user_id"]),
                    purpose=str(found["purpose"]),
                    automation=bool(found.get("automation", True)),
                    profile_id=str(found.get("profile_id") or ""),
                    max_s=int(found.get("max_s") or self.max_table[str(found["purpose"])]),
                    idle_s=int(found.get("idle_s") or self.idle_s),
                    started_at=now - active_s,
                    state="active",
                    active_before=active_s,
                    active_since=now,
                    last_input=now,
                )
            except (KeyError, TypeError, ValueError):
                lease = None
            if lease is not None and await r.sismember(ENDING, lease.lease_id):
                # The api ended it while browserd could not be told: end it, never take it back.
                await self._tell_end(r, lease.lease_id)
                lease = None
            if lease is not None and lease.purpose in TIERS:
                if found.get("state") == "waiting_person":
                    # browserd's deadline, not a fresh one (#291): what it has left, else none.
                    left = found.get("wait_left_s")
                    left = left if isinstance(left, int) and not isinstance(left, bool) else 0
                    host = found.get("wait_host")
                    lease.wait_person(
                        now,
                        str(found.get("wait_reason") or "signin"),
                        host[:HOST_MAX] if isinstance(host, str) else "",
                        max(
                            0,
                            min(
                                left,
                                1800
                                if lease.purpose == "apply_fill"
                                and found.get("wait_reason")
                                in {"captcha", "email_code", "forbidden"}
                                else self.session_max_s,
                            ),
                        ),
                    )
                if await r.set(HOLDER, lease.dumps(), nx=True):
                    logger.info("browser lease taken back from browserd: purpose=%s", lease.purpose)
        await r.set(SYNCED, "1")
        return _text(await r.get(HOLDER))

    async def _own(self, r: Any, user_id: str, purpose: str) -> Lease | None:
        """The person's lease of this purpose when another call of theirs (a second tab) took the
        turn while this one queued; the place this call may have put back after that call's claim
        is taken out, so the person never waits behind their own lease."""
        raw = _text(await r.get(HOLDER))
        if raw is None:
            return None
        lease = Lease.loads(raw)
        if lease.user_id != user_id or lease.purpose != purpose:
            return None
        member = _member(user_id, purpose)
        await r.zrem(QUEUES[TIERS[purpose]], member)
        await r.delete(TICKET + member)
        return lease

    async def _gone_in_browserd(self, lease: Lease) -> tuple[str | None, int | None]:
        """Why browserd no longer holds this lease (None when it does, _CANNOT_ASK when browserd
        does not answer), and the profile's size it measured at that end, when it says."""
        try:
            found = await self.client.current()
        except BrowserError:
            return _CANNOT_ASK, None
        current = found["lease"]
        if current is not None and current.get("lease_id") == lease.lease_id:
            return None, None
        last = found["last_end"]
        if last is not None and last.get("lease_id") == lease.lease_id:
            ended = ended_of(last)
            return ended.reason, ended.size_bytes
        return "ended", None

    async def _end(
        self, r: Any, raw: str, lease: Lease, reason: str, now: float, size: int | None = None
    ) -> bool:
        if not await r.eval(CAS, 1, HOLDER, raw, ""):
            return False
        if lease.state != "starting":
            await r.lpush(DURATIONS, max(0, int(now - lease.started_at)))
            await r.ltrim(DURATIONS, 0, HISTORY - 1)
            await r.delete(SEEN + lease.lease_id)
            told = await self._tell_end(r, lease.lease_id)
            size = told if told is not None else size
        logger.info("browser lease ended: purpose=%s reason=%s", lease.purpose, reason)
        if size is not None and lease.profile_id:
            await self._mark_size(lease, size)
        return True

    async def _mark_size(self, lease: Lease, size: int) -> None:
        """The profile's size and last use (B7); a failure to write them never stops an end."""
        try:
            await self.profiles.mark_used(
                lease.user_id, self.clock(), size_bytes=size, profile_id=lease.profile_id
            )
        except Exception as exc:
            logger.warning("browser profile size not written: %s", type(exc).__name__)

    async def _tell_end(self, r: Any, lease_id: str) -> int | None:
        """Ends the lease in browserd, and answers the profile's size it measured; when browserd
        does not confirm, the end waits in ENDING."""
        try:
            ended = await self.client.end(lease_id)
        except BrowserError as exc:
            logger.warning("browserd did not confirm the end of a lease: %s", exc.code)
            await r.sadd(ENDING, lease_id)
            await r.expire(ENDING, SEEN_TTL_S)
            return None
        return ended.size_bytes if ended is not None else None

    async def _tell_ending(self, r: Any) -> None:
        """Tells browserd the ends it did not confirm yet, until it does (or no longer knows)."""
        for member in await r.smembers(ENDING):
            lease_id = _text(member) or ""
            try:
                await self.client.end(lease_id)
            except BrowserError:
                return  # still not answering: told on a later call
            await r.srem(ENDING, lease_id)
            logger.info("browserd confirmed the end of a lease it held while it did not answer")

    async def _place(self, r: Any, user_id: str, purpose: str, now: float) -> Waiting | None:
        tier = TIERS[purpose]
        rank = await r.zrank(QUEUES[tier], _member(user_id, purpose))
        if rank is None:
            return None
        ahead = 0
        for queue in QUEUES[:tier]:
            ahead += int(await r.zcard(queue))
        position = ahead + int(rank) + 1
        recent = [int(x) for x in await r.lrange(DURATIONS, 0, HISTORY - 1)]
        typical = sum(recent) / len(recent) if recent else DEFAULT_LEASE_S
        holder = _text(await r.get(HOLDER))
        left = 0.0
        if holder is not None:
            left = max(0.0, typical - (now - Lease.loads(holder).started_at))
        # Whole minutes, rounded up: the seconds would tell how long the holder has run.
        minutes = math.ceil((left + (position - 1) * typical) / 60)
        return Waiting(purpose=purpose, position=position, estimated_wait_s=60 * minutes)

    async def _update(
        self, user_id: str, lease_id: str, change: Callable[[Lease, float], None]
    ) -> Lease | None:
        try:
            r = await self._pool()
            for _ in range(UPDATE_ATTEMPTS):
                raw = _text(await r.get(HOLDER))
                if raw is None:
                    return None
                lease = Lease.loads(raw)
                if lease.user_id != user_id or lease.lease_id != lease_id:
                    return None
                change(lease, self.clock())
                if await r.eval(CAS, 1, HOLDER, raw, lease.dumps()):
                    return lease
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None
        raise StoreUnavailableError
