"""What binds one OAuth round trip to one signed in browser (mail M4).

Three values travel, and none of them is ever logged, raised or answered:

- `state`: `<payload>.<signature>`, both base64url. The payload holds the user id, a hash of
  the session id, a nonce and an expiry 10 minutes ahead. The signature is HMAC-SHA256 with a
  key derived from SECRET_KEY for this purpose alone (as `jobs/undo.py` derives its own), so a
  state cannot be replayed as any other signed value of the app, nor the other way round.
  `read_state` refuses a state that is malformed, forged, expired, signed for another user or
  made in another session, with a code of `REFUSALS` and nothing of the value.
- the PKCE verifier: 64 random characters. Its S256 challenge goes to Google with the consent
  address; the verifier itself waits in Redis under the nonce and is read once (`GETDEL`), so
  a state works a single time: a replayed callback finds no verifier.
- the nonce: 32 random characters, the key of the verifier.

The redirect address is no part of this: it is a setting (GOOGLE_OAUTH_REDIRECT_URI).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from redis.exceptions import RedisError

STATE_TTL = timedelta(minutes=10)
STATE_MAX_CHARS = 600
VERIFIER_PREFIX = "jobapp:mail:oauth:"

_STATE_PURPOSE = b"jobapp mail oauth state v1"
_SESSION_PURPOSE = b"jobapp mail oauth session v1"

# Why a state was refused: the only thing a log line says about it.
REFUSALS = frozenset({"malformed", "signature", "expired", "user", "session"})


class StateError(Exception):
    """A state that does not open. `reason` is one of `REFUSALS`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class StoreUnavailableError(Exception):
    """Redis could not keep or give back a verifier."""


@dataclass(frozen=True)
class State:
    user_id: uuid.UUID
    session: str = field(repr=False)
    nonce: str = field(repr=False)
    expires_at: datetime


def derive_key(secret: bytes) -> bytes:
    """The signing key of states: never the secret itself."""
    return hmac.new(secret, _STATE_PURPOSE, hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def session_hash(session_id: uuid.UUID) -> str:
    """What a state holds of the session: enough to tell it from another, never its id."""
    return hashlib.sha256(_SESSION_PURPOSE + session_id.bytes).hexdigest()[:32]


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def new_verifier() -> str:
    return secrets.token_urlsafe(48)


def challenge(verifier: str) -> str:
    """The S256 challenge of a verifier (RFC 7636)."""
    return _b64(hashlib.sha256(verifier.encode("ascii")).digest())


def sign_state(
    key: bytes, *, user_id: uuid.UUID, session_id: uuid.UUID, nonce: str, now: datetime
) -> str:
    payload = json.dumps(
        {
            "u": str(user_id),
            "s": session_hash(session_id),
            "n": nonce,
            "e": int((now + STATE_TTL).timestamp()),
        },
        separators=(",", ":"),
    ).encode("ascii")
    return f"{_b64(payload)}.{_b64(hmac.new(key, payload, hashlib.sha256).digest())}"


def read_state(
    key: bytes, token: str, *, user_id: uuid.UUID, session_id: uuid.UUID, now: datetime
) -> State:
    """The state of a callback, checked against the session it came back in. Raises
    `StateError`; the signature is compared before anything of the payload is read."""
    if not token or len(token) > STATE_MAX_CHARS or token.count(".") != 1:
        raise StateError("malformed")
    encoded, signature = token.split(".")
    try:
        payload = _unb64(encoded)
        given = _unb64(signature)
    except (ValueError, binascii.Error):
        raise StateError("malformed") from None
    if not hmac.compare_digest(given, hmac.new(key, payload, hashlib.sha256).digest()):
        raise StateError("signature")
    try:
        found = json.loads(payload)
        state = State(
            user_id=uuid.UUID(found["u"]),
            session=str(found["s"]),
            nonce=str(found["n"]),
            expires_at=datetime.fromtimestamp(int(found["e"]), UTC),
        )
    except (ValueError, KeyError, TypeError, OverflowError, OSError):
        raise StateError("malformed") from None
    if now >= state.expires_at:
        raise StateError("expired")
    if state.user_id != user_id:
        raise StateError("user")
    if not hmac.compare_digest(state.session, session_hash(session_id)):
        raise StateError("session")
    return state


class Verifiers(Protocol):
    """Where a PKCE verifier waits between the start and the callback."""

    async def put(self, nonce: str, verifier: str) -> None: ...

    async def take(self, nonce: str) -> str | None: ...


class RedisVerifiers:
    """The verifier under `jobapp:mail:oauth:<nonce>`, for the life of a state, read once."""

    def __init__(self, pool: Callable[[], Awaitable[Any]]) -> None:
        self._pool = pool

    async def put(self, nonce: str, verifier: str) -> None:
        try:
            redis = await self._pool()
            await redis.set(
                VERIFIER_PREFIX + nonce, verifier, ex=int(STATE_TTL.total_seconds()), nx=True
            )
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None

    async def take(self, nonce: str) -> str | None:
        try:
            redis = await self._pool()
            found = await redis.getdel(VERIFIER_PREFIX + nonce)
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None
        if found is None:
            return None
        return found.decode("ascii") if isinstance(found, bytes) else str(found)
