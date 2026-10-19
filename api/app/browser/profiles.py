"""The one browser profile of each person (browser B3): made on the first lease, then the same
row, so the same directory and uid, for every lease of that person whatever its purpose.

`ensure` reads the row first and inserts only when it is missing, so a uid of the sequence is
consumed only when a row is made, never by the leases that follow (the range has no cycle). The
insert is `INSERT ... SELECT ... WHERE NOT EXISTS`, where the column default `nextval` runs only
for a row the select produces, with ON CONFLICT on `user_id`: two first leases of one person that
race past the read both draw a number and one row wins, so a race of k first calls of one person
uses at most k numbers for one row; both read the same row; two people get two uids of the
sequence. A unique violation on `uid` or `profile_id` (a sequence set back by hand, a random id met
twice) is retried with a new number, up to ATTEMPTS times. The functions are synchronous (the
app's sessions are); app/browser/leases.py runs them in a thread.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.browser.models import BrowserProfile

ATTEMPTS = 5


@dataclass(frozen=True)
class ProfileRef:
    profile_id: str
    uid: int


@dataclass(frozen=True)
class ProfileView:
    size_bytes: int
    created_at: datetime
    last_used_at: datetime | None


def ensure(sessions: sessionmaker[Session], user_id: uuid.UUID) -> ProfileRef:
    for attempt in range(ATTEMPTS):
        with sessions() as db:
            found = _read(db, user_id)
            if found is not None:
                return found
            missing = ~sa.exists().where(BrowserProfile.user_id == user_id)
            try:
                db.execute(
                    pg.insert(BrowserProfile)
                    .from_select(
                        ["user_id", "profile_id"],
                        sa.select(
                            sa.literal(user_id, sa.Uuid), sa.literal(uuid.uuid4(), sa.Uuid)
                        ).where(missing),
                    )
                    .on_conflict_do_nothing(index_elements=["user_id"])
                )
                db.commit()
            except IntegrityError:
                db.rollback()
                if attempt == ATTEMPTS - 1:
                    raise
                continue
            found = _read(db, user_id)
            if found is not None:
                return found
    raise AssertionError("unreachable")  # pragma: no cover


def _read(db: Session, user_id: uuid.UUID) -> ProfileRef | None:
    row = db.execute(
        sa.select(BrowserProfile.profile_id, BrowserProfile.uid).where(
            BrowserProfile.user_id == user_id
        )
    ).one_or_none()
    return None if row is None else ProfileRef(profile_id=str(row.profile_id), uid=row.uid)


def mark_used(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    at: datetime,
    size_bytes: int | None = None,
    profile_id: str | None = None,
) -> None:
    """The last use; at a lease end (B7) also the size browserd measured, written to that
    profile only: a row made again after "Forget all logins" never gets the old one's size."""
    values: dict[str, object] = {"last_used_at": at}
    if size_bytes is not None:
        values["size_bytes"] = size_bytes
    where = [BrowserProfile.user_id == user_id]
    if profile_id is not None:
        where.append(BrowserProfile.profile_id == uuid.UUID(profile_id))
    with sessions() as db:
        db.execute(sa.update(BrowserProfile).where(*where).values(**values))
        db.commit()


def remove(sessions: sessionmaker[Session], user_id: uuid.UUID) -> ProfileRef | None:
    """Deletes the person's profile row (B7, "Forget all logins"): B3's trigger queues the
    directory's removal, and the next lease makes a new row, so a new empty directory under a
    new uid. The row deleted, or None when there was none."""
    with sessions() as db:
        row = db.execute(
            sa.delete(BrowserProfile)
            .where(BrowserProfile.user_id == user_id)
            .returning(BrowserProfile.profile_id, BrowserProfile.uid)
        ).one_or_none()
        db.commit()
    return None if row is None else ProfileRef(profile_id=str(row.profile_id), uid=row.uid)


def view(sessions: sessionmaker[Session], user_id: uuid.UUID) -> ProfileView | None:
    with sessions() as db:
        row = db.execute(
            sa.select(
                BrowserProfile.size_bytes, BrowserProfile.created_at, BrowserProfile.last_used_at
            ).where(BrowserProfile.user_id == user_id)
        ).one_or_none()
    if row is None:
        return None
    return ProfileView(
        size_bytes=row.size_bytes, created_at=row.created_at, last_used_at=row.last_used_at
    )
