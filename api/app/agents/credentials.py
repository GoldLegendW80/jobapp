"""The users' model credentials: format check, save, read back for a run, verify, remove.

Every function takes the user id from the caller's session (the router) or the run (A2), never
from a request body, and touches only that user's row. The plaintext secret exists only in
memory: it is encrypted before it reaches the database (`crypto.Keyring`), never returned by
the API (only kind, last4, verified_at, updated_at), never logged, and never quoted in an
error message.

Formats (checked before anything is stored, messages name the field like the account routes):
- Anthropic API key, from console.anthropic.com: starts with `sk-ant-api` (today
  `sk-ant-api03-...`).
- Claude subscription token, printed by `claude setup-token`: today `sk-ant-oat01-...`. The
  Claude Code docs do not state the prefix, so any `sk-ant-` value that is not an API key is
  accepted for this kind; an API key pasted under the subscription choice (or the reverse) is
  refused with a message saying which choice it belongs to.
- Admin API keys (`sk-ant-admin...`) are refused for both kinds: they manage an organization,
  run no model, and are worse to hold than a user key.
- Surrounding whitespace is trimmed (a pasted line break), inner whitespace is refused, and only
  letters, digits, `-` and `_` are allowed, MIN_LENGTH to MAX_LENGTH characters.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, undefer

from app.agents.crypto import Keyring
from app.agents.models import CredentialKind, ModelCredential

MIN_LENGTH = 40
MAX_LENGTH = 400
PREFIX = "sk-ant-"
API_KEY_PREFIX = "sk-ant-api"
OAUTH_PREFIX = "sk-ant-oat"
ADMIN_KEY_PREFIX = "sk-ant-admin"
_ALLOWED = re.compile(r"[A-Za-z0-9_-]+")

logger = logging.getLogger(__name__)


class FormatError(ValueError):
    """The pasted value is not a credential of the chosen kind. The message never quotes it."""


def clean_secret(kind: CredentialKind, raw: str) -> str:
    """The secret to store, or FormatError with a message for the person."""
    secret = raw.strip()
    if not secret:
        raise FormatError("Paste the token or key.")
    if any(char.isspace() for char in secret):
        raise FormatError("Remove the spaces or line breaks inside the value.")
    if not secret.startswith(PREFIX):
        raise FormatError(f"This is not an Anthropic credential: those start with {PREFIX}")
    if secret.startswith(ADMIN_KEY_PREFIX):
        raise FormatError(
            "This is an Anthropic Admin API key: it manages an organization and must not be "
            f"stored here. Paste a regular API key, which starts with {API_KEY_PREFIX}"
        )
    if kind is CredentialKind.anthropic_api_key and not secret.startswith(API_KEY_PREFIX):
        if secret.startswith(OAUTH_PREFIX):
            raise FormatError(
                "This is a Claude subscription token. Choose Claude subscription token, "
                f"or paste an API key, which starts with {API_KEY_PREFIX}"
            )
        raise FormatError(f"This is not an Anthropic API key: those start with {API_KEY_PREFIX}")
    if kind is CredentialKind.claude_subscription and secret.startswith(API_KEY_PREFIX):
        raise FormatError(
            "This is an Anthropic API key. Choose Anthropic API key, or paste the token "
            f"printed by claude setup-token, which starts with {OAUTH_PREFIX}"
        )
    if not _ALLOWED.fullmatch(secret):
        raise FormatError("Only letters, digits, hyphens and underscores belong in the value.")
    if len(secret) < MIN_LENGTH:
        raise FormatError("This is too short: paste the whole value.")
    if len(secret) > MAX_LENGTH:
        raise FormatError(f"This is too long: a credential has at most {MAX_LENGTH} characters.")
    return secret


def get(db: Session, user_id: uuid.UUID) -> ModelCredential | None:
    """The caller's credential row (without the ciphertext) as stored now, or None."""
    return db.get(ModelCredential, user_id, populate_existing=True)


def save(
    db: Session,
    keyring: Keyring,
    user_id: uuid.UUID,
    kind: CredentialKind,
    secret: str,
    now: datetime,
) -> ModelCredential:
    """Store (or replace) the caller's credential, encrypted, unverified. `secret` has passed
    `clean_secret`. One statement (insert or update on the primary key), so two saves at once
    leave one row: the last one."""
    sealed = keyring.encrypt(secret, user_id, kind.value)
    values = {
        "kind": kind,
        "secret_encrypted": sealed.ciphertext,
        "key_version": sealed.key_version,
        "last4": secret[-4:],
        "verified_at": None,
        "updated_at": now,
    }
    statement = (
        insert(ModelCredential)
        .values(user_id=user_id, created_at=now, **values)
        .on_conflict_do_update(index_elements=[ModelCredential.user_id], set_=values)
    )
    db.execute(statement)
    db.expire_all()
    row = db.get(ModelCredential, user_id)
    if row is None:  # pragma: no cover, the statement above just wrote it
        raise RuntimeError("credential not stored")
    logger.info("model credential saved", extra={"user_id": str(user_id), "kind": kind.value})
    return row


def delete(db: Session, user_id: uuid.UUID) -> bool:
    """Remove the caller's credential; False when there was none."""
    result = db.execute(sa.delete(ModelCredential).where(ModelCredential.user_id == user_id))
    removed = bool(result.rowcount)
    if removed:
        logger.info("model credential removed", extra={"user_id": str(user_id)})
    return removed


@dataclass(frozen=True)
class Revealed:
    row: ModelCredential
    secret: str

    def __repr__(self) -> str:  # never print the secret
        return f"Revealed(user_id={self.row.user_id}, kind={self.row.kind.value})"


def reveal(db: Session, keyring: Keyring, user_id: uuid.UUID) -> Revealed | None:
    """The caller's credential decrypted, for verify now and for an agent run (A2), or None.

    A row written by CREDENTIALS_KEY_PREVIOUS is re-encrypted with the current key on the way
    (the caller commits). Raises crypto.DecryptError when the row cannot be read: written with
    a key this server does not hold, or tampered with."""
    row = db.scalars(
        sa.select(ModelCredential)
        .where(ModelCredential.user_id == user_id)
        .options(undefer(ModelCredential.secret_encrypted))
        .with_for_update()
    ).one_or_none()
    if row is None:
        return None
    opened = keyring.decrypt(row.secret_encrypted, row.key_version, user_id, row.kind.value)
    if opened.stale:
        sealed = keyring.encrypt(opened.plaintext, user_id, row.kind.value)
        row.secret_encrypted = sealed.ciphertext
        row.key_version = sealed.key_version
        db.flush()
        logger.info("model credential re-encrypted", extra={"user_id": str(user_id)})
    return Revealed(row, opened.plaintext)


def set_verified_if_unchanged(
    db: Session,
    user_id: uuid.UUID,
    kind: CredentialKind,
    checked_at: datetime,
    verified_at: datetime | None,
) -> bool:
    """Record a check's outcome only on the row that was checked: one UPDATE matching the user,
    the kind and the `updated_at` read before the outbound call. False when no row matches (the
    credential was replaced or removed meanwhile): nothing is written. The caller commits."""
    result = db.execute(
        sa.update(ModelCredential)
        .where(
            ModelCredential.user_id == user_id,
            ModelCredential.kind == kind,
            ModelCredential.updated_at == checked_at,
        )
        .values(verified_at=verified_at)
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)


def rotate_all(db: Session, keyring: Keyring) -> int:
    """Re-encrypt with the current key every row written by another key; how many changed.
    Rows this server cannot decrypt are left as they are and raise crypto.DecryptError."""
    stale = db.scalars(
        sa.select(ModelCredential.user_id).where(ModelCredential.key_version != keyring.version)
    ).all()
    for user_id in stale:
        reveal(db, keyring, user_id)
    return len(stale)
