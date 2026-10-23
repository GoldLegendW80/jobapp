"""API-only capability for one exact provider send. Never handed to an agent."""

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime
from typing import Any

LIFETIME_S = 60


class InvalidKeyError(ValueError):
    def __init__(self) -> None:
        super().__init__("not_authorised")


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def mint(
    secret: str | None,
    send_id: uuid.UUID,
    user_id: uuid.UUID,
    address: str,
    draft_id: uuid.UUID,
    thread_id: str | None,
    content_hash: str,
    raw: bytes,
    now: datetime,
) -> str:
    if not secret or len(secret) < 32:
        raise InvalidKeyError()
    payload = {
        "send_id": str(send_id),
        "user_id": str(user_id),
        "address": address,
        "draft_id": str(draft_id),
        "thread_id": thread_id,
        "content_hash": content_hash,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "expires_at": int(now.timestamp()) + LIFETIME_S,
    }
    body = _encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_encode(signature)}"


def verify(
    secret: str | None,
    key: str,
    user_id: str,
    address: str,
    thread_id: str | None,
    raw: bytes,
    now: datetime,
) -> dict[str, Any]:
    try:
        if not secret or len(secret) < 32 or len(key) > 8192:
            raise InvalidKeyError()
        body, signature = key.split(".")
        expected = _encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise InvalidKeyError()
        found = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if (
            not isinstance(found, dict)
            or found.get("user_id") != user_id
            or found.get("address") != address
            or found.get("thread_id") != thread_id
            or found.get("raw_sha256") != hashlib.sha256(raw).hexdigest()
            or not isinstance(found.get("expires_at"), int)
            or not now.timestamp() < found["expires_at"] <= now.timestamp() + LIFETIME_S
        ):
            raise InvalidKeyError()
        uuid.UUID(found["send_id"])
        uuid.UUID(found["draft_id"])
        return found
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise InvalidKeyError() from None
