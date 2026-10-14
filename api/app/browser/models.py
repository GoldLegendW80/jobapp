"""Tables of the browser module (browser B3): one browser profile per person, and the queue of
profile directories to remove.

`browser_profiles` is the row every lease of a person uses, whatever its purpose, so the person's
logins (cookies and local storage in the directory `/profiles/<profile_id>` of the browser
container) persist across runs. `uid` is the Linux user that owns that directory, 20000 plus a
counter: it comes from the sequence `browser_profile_uid_seq`, which never gives a number twice,
so a uid once given to a person is never given to anyone else, even after that person's row is
deleted and while the old directory still waits for its removal (browserd refuses a profile whose
directory belongs to another uid, but cannot tell an old directory of the same uid).

Deleting a row (the account's cascade, or B7's "Forget all logins") queues the directory's removal
in `browser_profile_removals`, by a trigger, so no deletion path can forget it. B7 removes the
directories and then the queue rows. The api never reads a profile file: these rows hold names,
numbers and dates only.

Migration: alembic/versions/0016_browser.py, which must stay in step with this file (`alembic
check` in the tests compares them).
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models_base import Base

UID_MIN, UID_MAX = 20000, 59999  # browserd's range (browser/browserd/config.py)
UID_SEQUENCE = sa.Sequence(
    "browser_profile_uid_seq",
    start=UID_MIN,
    minvalue=UID_MIN,
    maxvalue=UID_MAX,
    cycle=False,
    metadata=Base.metadata,
)


class BrowserProfile(Base):
    __tablename__ = "browser_profiles"
    __table_args__ = (
        sa.CheckConstraint(f"uid BETWEEN {UID_MIN} AND {UID_MAX}", name="uid_in_range"),
        sa.CheckConstraint("size_bytes >= 0", name="size_not_negative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4
    )
    uid: Mapped[int] = mapped_column(sa.Integer, UID_SEQUENCE, nullable=False, unique=True)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class BrowserProfileRemoval(Base):
    """A profile directory to delete from the volume (B7 does it). No foreign key: the profile row
    is gone when this one is written."""

    __tablename__ = "browser_profile_removals"

    profile_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True)
    uid: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    queued_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
