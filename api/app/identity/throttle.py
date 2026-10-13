"""Sign in throttling over the `login_attempts` table (SPEC-identity.md, Rules).

Limits, over a sliding window of WINDOW (15 minutes):
- EMAIL_LIMIT (5) failed attempts per email address. A success for that address resets its
  counter: the failures before the most recent success no longer count. The person who just
  proved they hold the password is not the attacker, and a typo streak followed by a good
  password should not leave them one typo away from a lockout.
- IP_LIMIT (20) failed attempts per client address. A success does NOT reset it: one attacker
  who owns a single account could otherwise sign into it every few guesses and spray other
  addresses forever.

How an attempt goes (`reserve`, then the caller verifies the password, then `mark_success`):
1. Under two transaction advisory locks (the email, then the address, always in that order so
   two requests never wait on each other in a cycle), count the failures in the window. Over a
   limit: ThrottledError, nothing recorded, no argon2 run.
2. Else insert the attempt as a failure, commit (which releases the locks), and only then run
   argon2. The count and the insert are one atomic step per email and per address, so N
   parallel guesses cannot all pass the check before any of them is recorded: exactly the
   limit reaches argon2, no overshoot. A request that dies between the insert and the verdict
   leaves a failure behind, the conservative side.
3. A right password flips the row to success.

A refused attempt (429) is not recorded: it tested no password, and counting it would let the
lockout extend itself for as long as someone keeps retrying. The 429 answers fast, without the
argon2 cost: that timing difference tells nothing about the account, since the same refusal
comes for any address once the limit is reached, existing or not.

Attempts on unknown, disabled or malformed addresses are recorded like any other (the key of a
malformed one is its trimmed lowercase text). Rows older than RETENTION (30 days) are deleted
opportunistically on write, at most once per PURGE_EVERY (1 hour) per process.

The same counters guard POST /api/auth/password: a wrong current password is a password guess
like a failed sign in, recorded under the account's email and the caller's address. Invite and
reset links need no throttle: the 43 character token is the credential, not the password.
"""

import math
import threading
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.identity import service
from app.identity.models import LoginAttempt

WINDOW = timedelta(minutes=15)
EMAIL_LIMIT = 5
IP_LIMIT = 20
RETENTION = timedelta(days=30)
PURGE_EVERY = timedelta(hours=1)
EMAIL_KEY_MAX = 320

# Namespaces of the transaction advisory locks (identity uses 20_002 and 20_005 already).
_EMAIL_LOCK_NAMESPACE = 20_007
_IP_LOCK_NAMESPACE = 20_008


class ThrottledError(Exception):
    """Too many failures in the window. `retry_after` is in whole seconds, at least 1."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"retry after {retry_after}s")
        self.retry_after = retry_after


def wait_phrase(retry_after: int) -> str:
    """ "Try again in N minutes." with N rounded up, never 0."""
    minutes = max(1, math.ceil(retry_after / 60))
    return f"Try again in {minutes} minute{'' if minutes == 1 else 's'}."


def attempt_email(raw: str) -> str:
    """The key an attempt is recorded and counted under: the normalised address, or for text
    that is not an address, its trimmed lowercase form (without NUL, which Postgres text cannot
    hold), cut to EMAIL_KEY_MAX characters."""
    try:
        return service.normalize_email(raw)
    except service.InvalidEmailError:
        key = raw.replace("\x00", "").strip().lower()[:EMAIL_KEY_MAX]
        return key.encode("utf-8", "replace").decode("utf-8")


class AttemptPurger:
    """Deletes attempts older than RETENTION, at most once per PURGE_EVERY. One per app (so one
    per process); thread safe, the sign in handlers run on the threadpool."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: datetime | None = None

    def _due(self, now: datetime) -> bool:
        with self._lock:
            # A clock that went backwards (a test, a corrected host clock) makes it due too.
            if self._last is None or now - self._last >= PURGE_EVERY or now < self._last:
                self._last = now
                return True
            return False

    def maybe_purge(self, session: Session, now: datetime) -> int | None:
        """How many rows were deleted, or None when no purge was due."""
        if not self._due(now):
            return None
        result = session.execute(sa.delete(LoginAttempt).where(LoginAttempt.at < now - RETENTION))
        return int(result.rowcount or 0)


def _retry_after(
    session: Session, where: list[sa.ColumnElement[bool]], limit: int, now: datetime
) -> int | None:
    """Seconds until the failures matching `where` drop below `limit`, or None when they are
    below it already. The window slides: a failure at `at` stops counting at `at + WINDOW`."""
    failures = sa.and_(*where, LoginAttempt.success.is_(False), LoginAttempt.at > now - WINDOW)
    count = int(session.scalar(sa.select(sa.func.count()).where(failures)) or 0)
    if count < limit:
        return None
    # The (count - limit + 1)-th oldest failure is the one whose expiry brings the count under
    # the limit. Normally count == limit and this is the oldest failure in the window.
    releasing = session.scalar(
        sa.select(LoginAttempt.at)
        .where(failures)
        .order_by(LoginAttempt.at, LoginAttempt.id)
        .offset(count - limit)
        .limit(1)
    )
    if releasing is None:  # pragma: no cover, count >= limit guarantees a row
        return None
    return max(1, math.ceil((releasing + WINDOW - now).total_seconds()))


def _email_conditions(email: str, now: datetime) -> list[sa.ColumnElement[bool]]:
    # Only failures after the most recent success of this address count. Ordered by id, not
    # time, so a success in the same instant as a failure (a frozen test clock) still resets.
    last_success = (
        sa.select(sa.func.coalesce(sa.func.max(LoginAttempt.id), 0))
        .where(
            LoginAttempt.email == email,
            LoginAttempt.success.is_(True),
            LoginAttempt.at > now - WINDOW,
        )
        .scalar_subquery()
    )
    return [LoginAttempt.email == email, LoginAttempt.id > last_success]


def reserve(
    session: Session,
    email: str,
    ip: str | None,
    now: datetime,
    purger: AttemptPurger | None = None,
) -> int:
    """Check the limits for this email key and client address and record the attempt as a
    failure. Returns the attempt id, for `mark_success`. Raises ThrottledError without writing.
    The caller commits right away, before verifying any password, so the locks are released.

    `ip` is the client address as the api sees it; anything that is not an address (a test
    client, a unix socket) is recorded as NULL and escapes the per address limit, never the
    per email one.
    """
    clean_ip = service.clean_ip(ip)
    session.execute(
        sa.select(sa.func.pg_advisory_xact_lock(_EMAIL_LOCK_NAMESPACE, sa.func.hashtext(email)))
    )
    if clean_ip is not None:
        session.execute(
            sa.select(sa.func.pg_advisory_xact_lock(_IP_LOCK_NAMESPACE, sa.func.hashtext(clean_ip)))
        )
    waits = [_retry_after(session, _email_conditions(email, now), EMAIL_LIMIT, now)]
    if clean_ip is not None:
        waits.append(
            _retry_after(
                session, [LoginAttempt.ip == sa.cast(clean_ip, LoginAttempt.ip.type)], IP_LIMIT, now
            )
        )
    found = [wait for wait in waits if wait is not None]
    if found:
        raise ThrottledError(max(found))
    attempt = LoginAttempt(email=email, ip=clean_ip, at=now, success=False)
    session.add(attempt)
    session.flush()
    if purger is not None:
        purger.maybe_purge(session, now)
    return attempt.id


def mark_success(session: Session, attempt_id: int) -> None:
    """The password of this attempt was right. The caller commits."""
    session.execute(
        sa.update(LoginAttempt).where(LoginAttempt.id == attempt_id).values(success=True)
    )


def forget(session: Session, attempt_id: int) -> None:
    """Remove a reserved attempt whose password was never tested, because the api had no slot
    free to verify it (503, issue #108). Like a refused attempt (429) it must not count: the
    caller did nothing wrong, and a busy moment must not bring anyone closer to a lockout. The
    caller commits."""
    session.execute(sa.delete(LoginAttempt).where(LoginAttempt.id == attempt_id))


def failures_in_window(
    session: Session, now: datetime, *, email: str | None = None, ip: str | None = None
) -> int:
    """Failures counted now against an email key (since its last success) or an address."""
    if email is not None:
        where = _email_conditions(email, now)
    elif ip is not None:
        where = [LoginAttempt.ip == sa.cast(ip, LoginAttempt.ip.type)]
    else:
        raise ValueError("give an email or an ip")
    return int(
        session.scalar(
            sa.select(sa.func.count()).where(
                *where, LoginAttempt.success.is_(False), LoginAttempt.at > now - WINDOW
            )
        )
        or 0
    )
