"""Signed undo tokens of a bulk status change (POST /api/jobs/bulk, POST /api/jobs/bulk/undo).

A token carries what the undo needs and nothing the client may change: the user it belongs to,
when it expires, and per job its previous status, its previous applied_on and the updated_at
the bulk change left it with. The server keeps no state: the token is the record.

Format: `<payload>.<signature>`, both base64url without padding. The payload is the JSON of
{"u": user id, "x": expiry (Unix seconds), "j": [[job id, status, applied_on | null,
updated_at], ...]} compressed with zlib (500 jobs make about 25 kB). The signature is
HMAC-SHA256 over the payload with a key derived from SECRET_KEY, compared in constant time.

Single use without state: the undo restores a job only while its updated_at is still the one
recorded, and restoring it moves updated_at on. A second undo of the same token, or an undo
after the job was edited again, finds nothing to restore (service.undo_bulk).
"""

import base64
import binascii
import hashlib
import hmac
import json
import uuid
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.jobs.models import JobStatus

UNDO_WINDOW = timedelta(minutes=10)
TOKEN_MAX_CHARS = 200_000
_PURPOSE = b"jobapp jobs bulk undo v1"


class InvalidTokenError(ValueError):
    """The token was not made by this server for this user, or is damaged."""


class ExpiredTokenError(ValueError):
    """The token is genuine but its undo window has passed."""


@dataclass(frozen=True)
class Previous:
    """One job as the bulk change found it, and the updated_at it left."""

    job_id: uuid.UUID
    status: JobStatus
    applied_on: date | None
    updated_at: datetime


def derive_key(secret: bytes) -> bytes:
    """The signing key of undo tokens: never the secret itself, so a token signed here cannot
    be replayed as any other kind of signed value."""
    return hmac.new(secret, _PURPOSE, hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(key: bytes, user_id: uuid.UUID, jobs: list[Previous], now: datetime) -> str:
    body = {
        "u": user_id.hex,
        "x": int((now + UNDO_WINDOW).timestamp()),
        "j": [
            [
                p.job_id.hex,
                p.status.value,
                p.applied_on.isoformat() if p.applied_on else None,
                p.updated_at.isoformat(),
            ]
            for p in jobs
        ],
    }
    payload = zlib.compress(json.dumps(body, separators=(",", ":")).encode(), 9)
    signature = hmac.new(key, payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def expires_at(now: datetime) -> datetime:
    return datetime.fromtimestamp(int((now + UNDO_WINDOW).timestamp()), tz=now.tzinfo)


def verify(key: bytes, token: str, user_id: uuid.UUID, now: datetime) -> list[Previous]:
    """The jobs a token restores. Raises InvalidTokenError for a token this server did not sign
    for this user (tampered, truncated, another user's) and ExpiredTokenError after the window."""
    if len(token) > TOKEN_MAX_CHARS or token.count(".") != 1:
        raise InvalidTokenError("malformed")
    encoded, signature = token.split(".")
    try:
        payload = _unb64(encoded)
        given = _unb64(signature)
    except (binascii.Error, ValueError):
        raise InvalidTokenError("not base64") from None
    expected = hmac.new(key, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(given, expected):
        raise InvalidTokenError("bad signature")
    try:
        # A signed payload is ours, but bound the decompression anyway.
        body = json.loads(zlib.decompressobj().decompress(payload, 4 * TOKEN_MAX_CHARS))
        if body["u"] != user_id.hex:
            raise InvalidTokenError("another user's token")
        expiry = int(body["x"])
        jobs = [
            Previous(
                job_id=uuid.UUID(hex=job_id),
                status=JobStatus(status),
                applied_on=date.fromisoformat(applied) if applied else None,
                updated_at=datetime.fromisoformat(updated),
            )
            for job_id, status, applied, updated in body["j"]
        ]
    except InvalidTokenError:
        raise
    except (KeyError, TypeError, ValueError, zlib.error):
        raise InvalidTokenError("unreadable") from None
    if now.timestamp() > expiry:
        raise ExpiredTokenError("expired")
    return jobs
