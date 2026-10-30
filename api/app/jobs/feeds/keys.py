"""The keys of the job feeds that need one (Adzuna's app id and app key), one row per person and
feed, encrypted at rest exactly as the model credentials are (`crypto.Keyring`, AES-256-GCM,
bound to the person's id and the kind `job_feed/<feed>`).

The person pastes them in Account (web features/jobs/adzuna-key-card.tsx, PUT
/api/jobs/feeds/adzuna/key). The plaintext exists only in memory: it is never returned by the
API (only whether one is stored, the last four characters of the app key and when it was saved),
never logged and never quoted in an error. A sweep reads it through `reveal` (feeds.FeedRunner).
"""

import json
import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker, undefer

from app.agents.crypto import NONCE_BYTES, TAG_BYTES, Keyring
from app.models_base import Base

logger = logging.getLogger("app.jobs.feeds")

KEYED_FEEDS = ("adzuna",)
# Adzuna shows an app id of 8 hexadecimal characters and an app key of 32: the shapes accepted
# are wider (letters and digits), and nothing else, so no pasted line or label is stored.
APP_ID = re.compile(r"[A-Za-z0-9]{4,64}")
APP_KEY = re.compile(r"[A-Za-z0-9]{16,128}")


class FormatError(ValueError):
    """What the person pasted is not an Adzuna key. The message never quotes it."""


class FeedKey(Base):
    __tablename__ = "job_feed_keys"
    __table_args__ = (
        sa.CheckConstraint("feed IN ('adzuna')", name="feed_known"),
        sa.CheckConstraint(
            f"octet_length(secret_encrypted) > {NONCE_BYTES + TAG_BYTES}",
            name="secret_encrypted_length",
        ),
        sa.CheckConstraint("char_length(last4) = 4", name="last4_length"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    feed: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    secret_encrypted: Mapped[bytes] = mapped_column(pg.BYTEA, nullable=False, deferred=True)
    key_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    last4: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


def _kind(feed: str) -> str:
    return f"job_feed/{feed}"


def clean(app_id: str, app_key: str) -> dict[str, str]:
    """The key to store, or FormatError with a message for the person."""
    app_id, app_key = app_id.strip(), app_key.strip()
    if not app_id or not app_key:
        raise FormatError("Paste both the application ID and the application key.")
    if not APP_ID.fullmatch(app_id):
        raise FormatError("The application ID holds only letters and digits, as Adzuna shows it.")
    if not APP_KEY.fullmatch(app_key):
        raise FormatError(
            "The application key holds only letters and digits, at least 16, as Adzuna shows it."
        )
    return {"app_id": app_id, "app_key": app_key}


def get(db: Session, user_id: uuid.UUID, feed: str) -> FeedKey | None:
    return db.get(FeedKey, (user_id, feed), populate_existing=True)


def save(
    db: Session, keyring: Keyring, user_id: uuid.UUID, feed: str, key: dict[str, str],
    now: datetime,
) -> FeedKey:  # fmt: skip
    """Store or replace the person's key of `feed` (one statement: two saves leave the last)."""
    sealed = keyring.encrypt(json.dumps(key, sort_keys=True), user_id, _kind(feed))
    values = {
        "secret_encrypted": sealed.ciphertext,
        "key_version": sealed.key_version,
        "last4": key["app_key"][-4:],
        "updated_at": now,
    }
    db.execute(
        insert(FeedKey)
        .values(user_id=user_id, feed=feed, created_at=now, **values)
        .on_conflict_do_update(index_elements=[FeedKey.user_id, FeedKey.feed], set_=values)
    )
    db.expire_all()
    row = get(db, user_id, feed)
    if row is None:  # pragma: no cover, the statement above just wrote it
        raise RuntimeError("feed key not stored")
    logger.info("job feed key saved", extra={"user_id": str(user_id), "feed": feed})
    return row


def delete(db: Session, user_id: uuid.UUID, feed: str) -> bool:
    result = db.execute(sa.delete(FeedKey).where(FeedKey.user_id == user_id, FeedKey.feed == feed))
    removed = bool(result.rowcount)
    if removed:
        logger.info("job feed key removed", extra={"user_id": str(user_id), "feed": feed})
    return removed


def reveal(db: Session, keyring: Keyring, user_id: uuid.UUID, feed: str) -> dict[str, str] | None:
    """The person's key of `feed` decrypted, None when none is stored. Raises
    crypto.DecryptError when the row cannot be read (a key this server does not hold)."""
    row = db.scalars(
        sa.select(FeedKey)
        .where(FeedKey.user_id == user_id, FeedKey.feed == feed)
        .options(undefer(FeedKey.secret_encrypted))
    ).one_or_none()
    if row is None:
        return None
    opened = keyring.decrypt(row.secret_encrypted, row.key_version, user_id, _kind(feed))
    data = json.loads(opened.plaintext)
    if not isinstance(data, dict):
        raise ValueError("not a feed key")
    return {"app_id": str(data.get("app_id", "")), "app_key": str(data.get("app_key", ""))}


def reader(
    sessions: sessionmaker[Session], keyring: Keyring | None
) -> Callable[[uuid.UUID, str], dict[str, str] | None]:
    """The sweep's key reader (feeds.KeyReader): None without a keyring (no key can be stored)."""

    def read(user_id: uuid.UUID, feed: str) -> dict[str, str] | None:
        if keyring is None:
            return None
        with sessions() as db:
            return reveal(db, keyring, user_id, feed)

    return read
