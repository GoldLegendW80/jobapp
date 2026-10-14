"""Who holds the one browser of the Jetson, and who waits for it (browser B3).

One Chromium runs at a time (SPEC-browser, "Memory on the Jetson"), so a lease is a turn: the api
keeps the turns in Redis and browserd (app/browser/client.py) mirrors the holder and refuses a
second one. The keys, all under `jobapp:browser:`:

    holder          the current lease as JSON, written and ended by compare and set only
    queue:<tier>    one sorted set per priority tier, members `<user id>|<purpose>`, in order of
                    arrival; tier 0 first: a person's own session (`takeover`, `signin`), then
                    the apply purposes a person waits on, then `full_auto`, then `sweep`
    ticket:<member> present while that waiter still waits: it lives TICKET_TTL_S and is renewed
                    each time the waiter asks again; a member whose ticket is gone leaves the queue
    durations       the lengths of the last ten leases, for the estimated wait
    synced          set once this Redis has read browserd's current lease (below)

`acquire(user_id, purpose, automation)` is idempotent per person and purpose, also for two calls
at once (two tabs): the one that loses the turn to the other answers that same lease, and leaves no
place in the queue behind it. The first call queues, each call renews the ticket and takes the
turn when the browser is free and this member is the head of the first non empty tier (one Lua
script, so two callers never both win), then starts the lease in browserd on the person's one
profile (app/browser/profiles.py). There is no preemption: a running lease ends first. A waiter
keeps its place by asking again within TICKET_TTL_S; the caller that took the turn gets browserd's
lease token once, in memory: it is never written to Redis nor logged.

Time (SPEC-browser, "Leases"): the purpose's maximum counts active time only, and the idle timer
(BROWSER_IDLE_S without a tool call or input, `touch`) ends a lease; while the lease is
`waiting_person` (B12 sets it, with a reason, a site and a deadline of BROWSER_SESSION_MAX_S)
neither runs, and `resume` gives the lease back the active time it had left. browserd applies the
same rules to its own copy and sees every tool call and every input, so while it answers and still
holds the lease, it alone decides idle and maximum (nobody has to `touch` this copy in step); the
timers of this copy end the turn only when browserd cannot be asked (a holder whose process died
expires here after BROWSER_IDLE_S without a `touch`). Every call first settles the holder: one that
browserd no longer holds (its own idle or maximum, a crash) is dropped, and, while browserd cannot
be asked, an expired one is ended (browserd is told when it can be). When Redis has lost its
keys (a restart), the first call reads browserd's `GET /leases/current` and takes its lease back
as the holder.

`current(user_id)` answers the person's own lease, whether someone else holds the browser (never
who, nor why), and the person's places in the queue. The estimated wait is rounded up to whole
minutes, so a waiter learns how long another person's lease has run to the minute at best, never
to the second (test_the_estimated_wait_hides_the_holders_running_time). Every time comes from the
injected clock.
"""

import asyncio
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Callable, Collection
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from redis.exceptions import RedisError
from sqlalchemy.orm import Session, sessionmaker

from app.browser import profiles
from app.browser.client import BrowserdClient, BrowserError
from app.browser.profiles import ProfileRef

logger = logging.getLogger("app.browser.leases")

PREFIX = "jobapp:browser:"
HOLDER = PREFIX + "holder"
SEQ = PREFIX + "seq"
DURATIONS = PREFIX + "durations"
SYNCED = PREFIX + "synced"
TICKET = PREFIX + "ticket:"

# Active seconds per purpose: browserd's table (browser/browserd/config.py, the test compares).
LEASE_MAX_S = {
    "takeover": 1800,
    "signin": 1800,
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
    "apply_fill": 1,
    "apply_submit": 1,
    "profile_edit": 1,
    "full_auto": 2,
    "sweep": 3,
}
QUEUES = tuple(f"{PREFIX}queue:{tier}" for tier in range(4))
TICKET_TTL_S = 60  # a waiter that has not asked again for this long leaves the queue
START_GRACE_S = 60  # a turn still starting after this is dropped (the caller died mid start)
HISTORY = 10
DEFAULT_LEASE_S = 300  # the estimate's lease length while no lease has ended yet
UPDATE_ATTEMPTS = 5

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
    wait_site: str | None = None
    wait_deadline: float | None = None
    active_before: float = 0.0  # active seconds banked before the current active stretch
    active_since: float = 0.0
    last_input: float = 0.0

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
        """Why the turn must end now, or None."""
        if self.state == "starting":
            return "start_failed" if now - self.started_at >= START_GRACE_S else None
        if self.state == "waiting_person":
            if self.wait_deadline is not None and now >= self.wait_deadline:
                return "person_timeout"
            return None
        if self.active_s(now) >= self.max_s:
            return "lease_max"
        if now - self.last_input >= self.idle_s:
            return "idle"
        return None

    def dumps(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def loads(cls, raw: str | bytes) -> "Lease":
        return cls(**json.loads(raw))


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


@dataclass(frozen=True)
class View:
    lease: Lease | None  # the person's own, never another's
    held_by_other: bool
    waiting: tuple[Waiting, ...]


class Profiles(Protocol):
    async def ensure(self, user_id: str) -> ProfileRef: ...

    async def mark_used(self, user_id: str, at: float) -> None: ...


class DbProfiles:
    """app/browser/profiles.py in a thread: the sessions are synchronous."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    async def ensure(self, user_id: str) -> ProfileRef:
        return await asyncio.to_thread(profiles.ensure, self.sessions, uuid.UUID(user_id))

    async def mark_used(self, user_id: str, at: float) -> None:
        when = datetime.fromtimestamp(at, UTC)
        await asyncio.to_thread(profiles.mark_used, self.sessions, uuid.UUID(user_id), when)


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

    async def acquire(self, user_id: str, purpose: str, automation: bool) -> Acquired:
        """The caller's lease, started now or already held, or its place in the queue. Raises
        BrowserError when browserd cannot start it, StoreUnavailableError without Redis."""
        if purpose not in TIERS:
            raise ValueError(purpose)
        try:
            r = await self._pool()
            now = self.clock()
            held = await self._settle(r, now)
            if held is not None and held.user_id == user_id and held.purpose == purpose:
                return Acquired(lease=held)
            member = _member(user_id, purpose)
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
                    return await self._start(r, turn, raw)
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

    async def touch(self, user_id: str, lease_id: str) -> Lease | None:
        """A tool call or an input on the lease: the idle timer starts again."""
        return await self._update(user_id, lease_id, lambda lease, now: lease.touch(now))

    async def wait_person(
        self, user_id: str, lease_id: str, reason: str, site: str
    ) -> Lease | None:
        """B12: the lease waits for the person, both timers suspended, until the deadline."""
        return await self._update(
            user_id,
            lease_id,
            lambda lease, now: lease.wait_person(now, reason, site, self.session_max_s),
        )

    async def resume(self, user_id: str, lease_id: str) -> Lease | None:
        return await self._update(user_id, lease_id, lambda lease, now: lease.resume(now))

    # --- inside ----------------------------------------------------------------------------------

    async def _start(self, r: Any, turn: Lease, raw: str) -> Acquired:
        try:
            profile = await self.profiles.ensure(turn.user_id)
            started = await self.client.start(
                user_id=turn.user_id,
                profile_id=profile.profile_id,
                uid=profile.uid,
                purpose=turn.purpose,
                automation=turn.automation,
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
            await self._tell_end(started.lease_id)
            raise BrowserError("unavailable")
        await self.profiles.mark_used(turn.user_id, now)
        logger.info("browser lease started: purpose=%s", turn.purpose)
        return Acquired(lease=lease, token=started.token)

    async def _settle(self, r: Any, now: float) -> Lease | None:
        """The holder after ending an expired one or one browserd no longer holds."""
        raw = _text(await r.get(HOLDER))
        if raw is None:
            if await r.exists(SYNCED):
                return None
            raw = await self._sync(r, now)
            if raw is None:
                return None
        lease = Lease.loads(raw)
        if lease.state == "starting":
            reason = lease.expired(now)
        else:
            # browserd decides while it answers; this copy's timers only when it cannot.
            reason = await self._gone_in_browserd(lease)
            if reason == _CANNOT_ASK:
                reason = lease.expired(now)
        if reason is None:
            return lease
        await self._end(r, raw, lease, reason, now)
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
            if lease is not None and lease.purpose in TIERS:
                if found.get("state") == "waiting_person":
                    lease.wait_person(
                        now, str(found.get("wait_reason") or "signin"), "", self.session_max_s
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

    async def _gone_in_browserd(self, lease: Lease) -> str | None:
        """Why browserd no longer holds this lease, None when it does, _CANNOT_ASK when browserd
        does not answer."""
        try:
            found = await self.client.current()
        except BrowserError:
            return _CANNOT_ASK
        current = found["lease"]
        if current is not None and current.get("lease_id") == lease.lease_id:
            return None
        last = found["last_end"]
        if last is not None and last.get("lease_id") == lease.lease_id:
            reason = str(last.get("reason") or "ended")
            return reason if re.fullmatch(r"[a-z_]{1,32}", reason) else "ended"
        return "ended"

    async def _end(self, r: Any, raw: str, lease: Lease, reason: str, now: float) -> bool:
        if not await r.eval(CAS, 1, HOLDER, raw, ""):
            return False
        if lease.state != "starting":
            await r.lpush(DURATIONS, max(0, int(now - lease.started_at)))
            await r.ltrim(DURATIONS, 0, HISTORY - 1)
            await self._tell_end(lease.lease_id)
        logger.info("browser lease ended: purpose=%s reason=%s", lease.purpose, reason)
        return True

    async def _tell_end(self, lease_id: str) -> None:
        try:
            await self.client.end(lease_id)
        except BrowserError as exc:
            logger.warning("browserd did not confirm the end of a lease: %s", exc.code)

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
