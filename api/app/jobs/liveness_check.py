"""The check of a posting's own address (SPEC-jobs, "Liveness"): one GET, paced and bounded, read
by app/jobs/liveness.py, stored on the posting (`jobs.liveness`), and the refusal it gives a click
that would spend on a closed posting.

- When: the person's clicks that write documents or apply (Write, Apply, a Like, Apply to the
  selected postings) and the apply run before it starts (apply/posting_check.py), each reusing a
  check younger than FRESH; "Check now" (POST /api/jobs/{id}/check) always asks again. Never on
  a timer, never in a sweep.
- What: a plain GET of the posting's own address (`jobs.url`): no cookie, no proxy from the
  environment, a User-Agent naming the app, at most REDIRECTS_MAX redirects, the first BODY_MAX
  bytes of the page (gzip and deflate decoded within that bound, another coding `unreadable`),
  BUDGET_S in all, the pacing included. A LinkedIn posting is never asked (`linkedin`): LinkedIn
  answers a plain GET with a sign in wall, and its requests belong to the LinkedIn checkbox, its
  pause and its cooldown (browser B10).
- Where to: http or https on a port of PORTS and a host as `browser.address.check_address` reads
  it, whose every address is public unicast (`address_allowed`, the rule of browserd's egress
  proxy, browser/browserd/proxy.py); the connection goes to the address checked, the Host header
  and the TLS name staying the host's, so a DNS answer that changes in between cannot send it to
  the app's own services. Every redirect is checked the same way.
- Pace: the browser's shared per host pacer (`sources.Pacer`, in Redis across the api and the
  workers), keyed by `platform_policy.pace_key`: one request or page of a host at a time, PAUSE_S
  after the previous one ended.
- Stored: {verdict, reason, sentence, checked_at} in `jobs.liveness`, without moving `updated_at`
  (a check is not an edit, so it never makes the person's open edit stale). A `closed` finding
  moves a posting that is found, shortlisted, ready or staged to closed (`set_status`, `via`
  "liveness"), unless the app moved it to closed once before and it is open again: the person
  reopened it, and that stays (later findings only propose). Nothing is ever deleted.
- Failures: a refused address, the network, the time, the pacer's store, a lock held past its
  timeout are an `unconfirmed` finding, or no finding at all: a click is never refused because
  a check could not be made.

Logs carry ids and codes, never an address or a page's text.
"""

import asyncio
import contextlib
import ipaddress
import logging
import socket
import uuid
import zlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import anyio.from_thread
import httpx
import sqlalchemy as sa
from fastapi import Request
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.browser.address import check_address
from app.browser.leases import StoreUnavailableError
from app.browser.sources import PACE_POLL_S, MemoryPacer, Pacer, RedisPacer
from app.jobs import feeds, liveness
from app.jobs import service as jobs_service
from app.jobs.liveness import CLOSED, UNCONFIRMED, Finding
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus

logger = logging.getLogger("app.jobs.liveness")

USER_AGENT = "Mozilla/5.0 (compatible; jobapp/1.0; checks that a saved job posting is open)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8,*/*;q=0.1",
    "Accept-Encoding": "identity",
}
BUDGET_S = 8.0
BULK_BUDGET_S = 20.0
BODY_MAX = 2 * 1024 * 1024
TEXT_MAX = 200_000
REDIRECTS_MAX = 4
PAUSE_S = 1.0
PORTS = frozenset({80, 443, 8080, 8443})
FRESH = timedelta(hours=1)
PAGE_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
REDIRECTS = frozenset({301, 302, 303, 307, 308})
AUTO_CLOSE_FROM = frozenset(
    {JobStatus.found, JobStatus.shortlisted, JobStatus.cv_ready, JobStatus.staged}
)
CLOSED_SENTENCE = "This posting is closed."


# --- the addresses a check may reach: browserd's egress rule, kept in step by a shared test -----


def _networks(*cidrs: str) -> tuple[Any, ...]:
    return tuple(ipaddress.ip_network(n) for n in cidrs)


REFUSED_V4 = _networks(
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.88.99.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/3",
)
GLOBAL_V6 = ipaddress.ip_network("2000::/3")
REFUSED_V6 = _networks("2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20")


def address_allowed(address: str) -> bool:
    """True for a public unicast address only: unparsable, scoped or special purpose is False."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id is not None:
            return False
        if ip.ipv4_mapped is None:
            return (
                ip in GLOBAL_V6
                and not any(ip in network for network in REFUSED_V6)
                and ip.is_global
            )
        ip = ip.ipv4_mapped
    return not any(ip in network for network in REFUSED_V4) and ip.is_global


Resolve = Callable[[str, int], Awaitable[list[str]]]


async def resolve_host(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


# The resolver of every Checker that names none; api/tests/conftest.py makes it refuse.
RESOLVE: Resolve = resolve_host


# --- one request -------------------------------------------------------------------------------


class _UnconfirmedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Hop:
    url: str
    parts: SplitResult
    host: str
    address: str


@dataclass(frozen=True)
class Answer:
    status: int
    url: str
    body: bytes = b""
    content_type: str = ""
    location: str | None = None


def _decoder(header: str) -> Any:
    codings = [c.strip().casefold() for c in header.split(",")]
    codings = [c for c in codings if c not in ("", "identity")]
    if not codings:
        return None
    if codings in (["gzip"], ["x-gzip"]):
        return zlib.decompressobj(16 + zlib.MAX_WBITS)
    if codings == ["deflate"]:
        return zlib.decompressobj()
    raise _UnconfirmedError("unreadable")


async def _body(response: httpx.Response) -> bytes:
    """The first BODY_MAX bytes of the page, decoded within that bound."""
    decoder = _decoder(response.headers.get("content-encoding", ""))
    body = bytearray()
    try:
        async for raw in response.aiter_raw():
            if decoder is None:
                body += raw[: BODY_MAX - len(body)]
            else:
                body += decoder.decompress(raw, BODY_MAX - len(body))
            if len(body) >= BODY_MAX:
                break
    except zlib.error:
        raise _UnconfirmedError("unreadable") from None
    return bytes(body)


@dataclass
class Checker:
    """`transport` and `resolve` replace the network in tests; `sleep` the clock's waits."""

    pacer: Pacer
    transport: httpx.AsyncBaseTransport | None = None
    resolve: Resolve | None = None
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)

    async def check(self, url: str | None) -> Finding:
        """What the posting's address says now; never raises for the network."""
        if not url:
            return Finding(UNCONFIRMED, "no_address")
        # Imported here: importing it registers the browser finder's day-long agent at import.
        from app.browser import platform_policy

        if platform_policy.platform_of(url) == "linkedin":
            return Finding(UNCONFIRMED, "linkedin")
        try:
            async with asyncio.timeout(BUDGET_S):
                answer = await self._get(url)
        except TimeoutError:
            return Finding(UNCONFIRMED, "timeout")
        except _UnconfirmedError as exc:
            return Finding(UNCONFIRMED, exc.reason)
        return await asyncio.to_thread(read, url, answer)

    async def _get(self, url: str) -> Answer:
        from app.browser import platform_policy  # as in check()

        hop = await self._checked(url)
        key = platform_policy.pace_key(platform_policy.platform_of(url), hop.host)
        holder = uuid.uuid4().hex
        try:
            await self._take(key, holder)
            return await self._follow(hop)
        finally:
            # A store that refuses leaves the hold to end after the pacer's PACE_HOLD_S.
            with contextlib.suppress(StoreUnavailableError):
                await self.pacer.release(key, holder)

    async def _take(self, key: str, holder: str) -> None:
        try:
            while (wait := await self.pacer.reserve(key, PAUSE_S, holder)) is None:
                await self.sleep(PACE_POLL_S)
        except StoreUnavailableError:
            raise _UnconfirmedError("busy") from None
        if wait > 0:
            await self.sleep(wait)

    async def _checked(self, url: str) -> Hop:
        if check_address(url) is None:
            raise _UnconfirmedError("address_refused")
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        port = parts.port or (443 if scheme == "https" else 80)
        host = parts.hostname or ""
        if port not in PORTS or not host:
            raise _UnconfirmedError("address_refused")
        try:
            found = await (self.resolve or RESOLVE)(host, port)
        except (OSError, UnicodeError):
            raise _UnconfirmedError("network") from None
        if not found or not all(address_allowed(address) for address in found):
            raise _UnconfirmedError("address_refused")
        return Hop(urlunsplit(parts._replace(scheme=scheme, fragment="")), parts, host, found[0])

    async def _follow(self, hop: Hop) -> Answer:
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(BUDGET_S),
            follow_redirects=False,
            trust_env=False,
            headers=HEADERS,
        ) as client:
            for _ in range(REDIRECTS_MAX + 1):
                answer = await self._fetch(client, hop)
                if answer.status not in REDIRECTS:
                    return answer
                if not answer.location:
                    raise _UnconfirmedError("unexpected_answer")
                try:
                    target = urljoin(hop.url, answer.location)
                except ValueError:
                    raise _UnconfirmedError("address_refused") from None
                hop = await self._checked(target)
        raise _UnconfirmedError("too_many_redirects")

    async def _fetch(self, client: httpx.AsyncClient, hop: Hop) -> Answer:
        """One GET sent to the address checked, as the host's own name."""
        address = f"[{hop.address}]" if ":" in hop.address else hop.address
        netloc = f"{address}:{hop.parts.port}" if hop.parts.port else address
        pinned = urlunsplit(
            (hop.parts.scheme.lower(), netloc, hop.parts.path or "/", hop.parts.query, "")
        )
        https = hop.parts.scheme.lower() == "https"
        request = client.build_request(
            "GET",
            pinned,
            headers={"Host": hop.parts.netloc},
            extensions={"sni_hostname": hop.host} if https else {},
        )
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError:
            raise _UnconfirmedError("network") from None
        try:
            status = response.status_code
            if status in REDIRECTS:
                return Answer(status, hop.url, location=response.headers.get("location"))
            ctype = response.headers.get("content-type", "")
            if not 200 <= status < 300 or _media(ctype) not in PAGE_TYPES:
                return Answer(status, hop.url, content_type=ctype)
            return Answer(status, hop.url, await _body(response), ctype)
        except httpx.HTTPError:
            raise _UnconfirmedError("network") from None
        finally:
            await response.aclose()


def _media(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().casefold()


def _text(answer: Answer) -> str:
    charset = "utf-8"
    for param in answer.content_type.split(";")[1:]:
        name, _, value = param.partition("=")
        if name.strip().casefold() == "charset" and value.strip(" \"'"):
            charset = value.strip(" \"'")
    try:
        decoded = answer.body.decode(charset, "replace")
    except LookupError:
        decoded = answer.body.decode("utf-8", "replace")
    if _media(answer.content_type) == "text/plain":
        return decoded[:TEXT_MAX]
    return feeds.plain_text(decoded, TEXT_MAX)


def read(requested: str, answer: Answer) -> Finding:
    """What the answer says (CPU bound: run it off the event loop)."""
    if 200 <= answer.status < 300 and _media(answer.content_type) not in PAGE_TYPES:
        return Finding(UNCONFIRMED, "not_a_page")
    return liveness.judge(answer.status, _text(answer), requested, answer.url)


# --- stored on the posting --------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    status: JobStatus
    liveness: dict[str, Any] | None


def _target(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> Any:
    return db.execute(
        sa.select(Job.url, Job.status, Job.liveness).where(Job.id == job_id, Job.user_id == user_id)
    ).one_or_none()


def fresh(stored: dict[str, Any] | None, now: datetime) -> bool:
    try:
        return now - datetime.fromisoformat(str((stored or {})["checked_at"])) < FRESH
    except (KeyError, TypeError, ValueError):
        return False


def _closed_by_the_app_before(db: Session, job: Job) -> bool:
    return bool(
        db.scalar(
            sa.select(
                sa.exists().where(
                    JobEvent.job_id == job.id,
                    JobEvent.user_id == job.user_id,
                    JobEvent.kind == JobEventKind.status,
                    JobEvent.data["via"].astext == "liveness",
                )
            )
        )
    )


def record(
    db: Session, user_id: uuid.UUID, job_id: uuid.UUID, finding: Finding, now: datetime
) -> Outcome | None:
    """Store the finding on the posting under its row lock, and move it to closed when the
    finding says so (module docstring). Commits. None when the posting is gone."""
    job = jobs_service.lock_job(db, user_id, job_id)
    if job is None:
        db.rollback()
        return None
    stored = {
        "verdict": finding.verdict,
        "reason": finding.reason,
        "sentence": finding.sentence,
        "checked_at": now.isoformat(),
    }
    moved = (
        finding.verdict == CLOSED
        and job.status in AUTO_CLOSE_FROM
        and not _closed_by_the_app_before(db, job)
    )
    if moved:
        jobs_service.set_status(
            db, job, JobStatus.closed, now, via="liveness", extra={"check": finding.reason}
        )
        job.liveness = stored
    else:
        db.execute(
            sa.update(Job)
            .where(Job.id == job.id, Job.user_id == user_id)
            .values(liveness=stored, updated_at=Job.updated_at)
            .execution_options(synchronize_session=False)
        )
    status = job.status
    db.commit()
    logger.info(
        "jobs.liveness",
        extra={
            "job_id": str(job_id),
            "verdict": finding.verdict,
            "reason": finding.reason,
            "closed": moved,
        },
    )
    return Outcome(status, stored)


def _in_db[T](sessions: sessionmaker[Session], step: Callable[[Session], T]) -> T:
    with sessions() as db:
        return step(db)


async def ensure(
    checker: Checker,
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    now: datetime,
    *,
    force: bool = False,
) -> Outcome | None:
    """The posting's status and what its last check found, checking again first unless the
    posting is closed or its last check is younger than FRESH (`force`: always). None when the
    posting is not the person's."""
    found = await asyncio.to_thread(_in_db, sessions, lambda db: _target(db, user_id, job_id))
    if found is None:
        return None
    if not force and (found.status is JobStatus.closed or fresh(found.liveness, now)):
        return Outcome(found.status, found.liveness)
    finding = await checker.check(found.url)
    return await asyncio.to_thread(
        _in_db, sessions, lambda db: record(db, user_id, job_id, finding, now)
    )


def closed_sentence(stored: dict[str, Any] | None) -> str:
    """What a refused click answers for a closed posting: the page's own words when its check
    closed it."""
    stored = stored or {}
    if stored.get("verdict") == CLOSED:
        if stored.get("sentence"):
            return f"{CLOSED_SENTENCE} Its page says: \u201c{stored['sentence']}\u201d"
        if stored.get("reason") in ("http_404", "http_410"):
            return "This posting is closed: its page no longer exists."
    return CLOSED_SENTENCE


# --- the api's and the worker's checker ----------------------------------------------------------


def for_app(state: Any) -> Checker:
    """The api's checker, kept on its state (tests put theirs there), paced in Redis."""
    checker: Checker | None = getattr(state, "liveness", None)
    if checker is None:
        pool = getattr(getattr(state, "queue", None), "pool", None)
        checker = Checker(RedisPacer(pool) if pool is not None else MemoryPacer())
        state.liveness = checker
    return checker


def for_worker(ctx: dict[str, Any]) -> Checker:
    """A worker's checker, kept in its job context (tests put theirs there)."""
    checker: Checker | None = ctx.get("liveness")
    if checker is None:
        redis = ctx.get("redis")

        async def pool() -> Any:
            return redis

        checker = Checker(RedisPacer(pool) if redis is not None else MemoryPacer())
        ctx["liveness"] = checker
    return checker


def click_refusal(request: Request, user_id: uuid.UUID, job_id: uuid.UUID) -> str | None:
    """Before a click that writes documents or applies, from a sync route: the sentence of the
    409 when the posting is closed, now or by this check; None to go on, also when the check
    could not be made."""
    state = request.app.state
    try:
        outcome = anyio.from_thread.run(
            ensure, for_app(state), state.sessionmaker, user_id, job_id, state.clock()
        )
    except (jobs_service.JobBusyError, DBAPIError):
        logger.warning("jobs.liveness.not_checked", extra={"job_id": str(job_id)})
        return None
    if outcome is None or outcome.status is not JobStatus.closed:
        return None
    return closed_sentence(outcome.liveness)


def check_all(request: Request, user_id: uuid.UUID, job_ids: Sequence[uuid.UUID]) -> None:
    """Before Apply to the selected postings: their checks side by side, within BULK_BUDGET_S
    (a posting not checked by then goes on as it is); one that fails fails alone."""
    state = request.app.state

    async def run() -> None:
        checker, now = for_app(state), state.clock()
        tasks = [
            asyncio.ensure_future(ensure(checker, state.sessionmaker, user_id, job_id, now))
            for job_id in dict.fromkeys(job_ids)
        ]
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=BULK_BUDGET_S)
        for task in pending:
            task.cancel()
        for task in done:
            if task.exception() is not None:
                logger.warning("jobs.liveness.not_checked", extra={"jobs": 1})

    anyio.from_thread.run(run)


def check_now(request: Request, user_id: uuid.UUID, job_id: uuid.UUID) -> Outcome | None:
    """The person's "Check now": a check whatever the last one found. Raises JobBusyError."""
    state = request.app.state
    return anyio.from_thread.run(
        lambda: ensure(
            for_app(state), state.sessionmaker, user_id, job_id, state.clock(), force=True
        )
    )
