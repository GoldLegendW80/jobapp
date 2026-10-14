"""The two locks of the mail module (mail M4; tasks/mail/todo.md, "Locks").

Two transaction advisory locks per user, taken the way `ingestion.documents.lock_user` is, and
released at the commit. There is no session lock in this module.

- `lock_sync(db, user_id)`, key `mail-sync:<user_id>`: the sync for each batch it stores,
  Connect, Disconnect, the deletion of one thread, the account deletion.
- `lock_send(db, user_id)`, key `mail-send:<user_id>`: the send gate for its decision, and
  Disconnect.

Order, for every path of the module: `lock_sync`, then `lock_send`, then the `mail_accounts`
row, then the job row, the threads in id order, the draft, the run lock, the run row, and the
proposal rows last. A path takes a subset, in this order. `lock_send` after `lock_sync` in one
transaction is checked here: taking them the other way round raises `LockOrderError` before
any wait, so a path that would deadlock fails in its first test.

Waits are bounded: `bounded(db)` sets `lock_timeout` to 5 s for the transaction, well under the
15 s `statement_timeout` of app/db.py. A wait that runs out is SQLSTATE 55P03, which app.main
answers as 409 in a route (`jobs.service.is_lock_timeout`); a job ends its batch. No lock is
held across a call to Gmail, to the parser or to a model.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

LOCK_TIMEOUT_MS = 5_000
BUSY = "Busy, try again."

_HELD = "mail.locks.held"


class LockOrderError(RuntimeError):
    """A path asked for `lock_sync` while holding `lock_send` of the same user."""


def sync_key(user_id: uuid.UUID) -> str:
    return f"mail-sync:{user_id}"


def send_key(user_id: uuid.UUID) -> str:
    return f"mail-send:{user_id}"


def bounded(db: Session, timeout_ms: int | None = None) -> None:
    """No lock wait of this transaction lasts longer than `timeout_ms` (5 s when not given)."""
    wait = LOCK_TIMEOUT_MS if timeout_ms is None else timeout_ms
    db.execute(sa.text(f"SET LOCAL lock_timeout = {int(wait)}"))


def _held(db: Session) -> set[str]:
    """The keys this transaction holds: kept with the transaction they were taken in, so the
    next transaction of the same session starts with none."""
    transaction = db.get_transaction()
    found = db.info.get(_HELD)
    if transaction is None or found is None or found[0] is not transaction:
        found = (transaction, set())
        db.info[_HELD] = found
    held: set[str] = found[1]
    return held


def _take(db: Session, key: str) -> None:
    bounded(db)
    db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(key, 0))))
    _held(db).add(key)


def lock_sync(db: Session, user_id: uuid.UUID) -> None:
    """Serialises everything that stores or deletes one user's mail. First lock of any path."""
    if send_key(user_id) in _held(db) and sync_key(user_id) not in _held(db):
        raise LockOrderError("lock_sync must be taken before lock_send")
    _take(db, sync_key(user_id))


def lock_send(db: Session, user_id: uuid.UUID) -> None:
    """Serialises the decision of a send with Disconnect. After `lock_sync` when both."""
    _take(db, send_key(user_id))


def lock_both(db: Session, user_id: uuid.UUID) -> None:
    """Disconnect's two locks, in the order of the module."""
    lock_sync(db, user_id)
    lock_send(db, user_id)
