"""The one browser profile of each person (browser B3): made on the first lease, then the same
row, so the same directory and uid, for every lease of that person whatever its purpose.

`ensure` is safe under concurrency: two first leases of one person insert once (ON CONFLICT on
the primary key) and both read the same row; two people get two uids of the sequence. A unique
violation on `uid` or `profile_id` (a sequence set back by hand, a random id met twice) is retried
with a new number, up to ATTEMPTS times. The functions are synchronous (the app's sessions are);
app/browser/leases.py runs them in a thread.
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
            try:
                db.execute(
                    pg.insert(BrowserProfile)
                    .values(user_id=user_id, profile_id=uuid.uuid4())
                    .on_conflict_do_nothing(index_elements=["user_id"])
                )
                db.commit()
            except IntegrityError:
                db.rollback()
                if attempt == ATTEMPTS - 1:
                    raise
                continue
            row = db.execute(
                sa.select(BrowserProfile.profile_id, BrowserProfile.uid).where(
                    BrowserProfile.user_id == user_id
                )
            ).one()
            return ProfileRef(profile_id=str(row.profile_id), uid=row.uid)
    raise AssertionError("unreachable")  # pragma: no cover


def mark_used(sessions: sessionmaker[Session], user_id: uuid.UUID, at: datetime) -> None:
    with sessions() as db:
        db.execute(
            sa.update(BrowserProfile)
            .where(BrowserProfile.user_id == user_id)
            .values(last_used_at=at)
        )
        db.commit()


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
