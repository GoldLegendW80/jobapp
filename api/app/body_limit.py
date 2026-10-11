"""A second line, independent of the proxy, against a caller making the api buffer a large body
(issue #100).

FastAPI reads and buffers a request body before the route's dependencies run, so a caller with
no session can make the api hold a body on any JSON route (the public ones, and the ones that
answer 401, whose body is read before the 401). Caddy caps bodies at the edge, but the api must
not depend on the proxy being in front of it.

`LimitRequestBodyMiddleware` is a pure ASGI middleware: it runs before the body is parsed, caps
it by route class, and answers 413 without buffering past the limit. It never makes a second
copy and it leaves alone the routes that read their own raw body with their own limit, so it
does not double read them:

- public routes under /api/auth/ (sign in, sign out, link check and accept): PUBLIC_MAX_BYTES,
  a few hundred bytes of login or acceptance with wide margin;
- every other JSON route under /api: DEFAULT_MAX_BYTES, well under Caddy's 10 MiB edge limit;
- the routes that legitimately take a large body and read it themselves, each with its own
  limit, are in SELF_LIMITED and pass through untouched: the profile photo (PUT, 2 MiB) and
  profile import (POST, 2 MiB), the jobs import (POST, 10 MiB), and the ingestion document
  upload and paste of pull request #98 (POST, its own 20 MiB and slots).

How it caps: a declared Content-Length over the limit is refused before a byte of body is read;
otherwise the body is counted as it streams and, at the first byte past the limit, the body is
cut short so the app stops reading and buffers no more, and whatever the app would answer is
replaced by a 413. The answer is cheap (`{"detail": ...}`, no-store) and the server closes the
connection, having not consumed the rest of the body.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

PUBLIC_PREFIX = "/api/auth/"
PUBLIC_MAX_BYTES = 64 * 1024  # a login or an invite acceptance is a few hundred bytes
DEFAULT_MAX_BYTES = 1024 * 1024  # other JSON routes; well under Caddy's 10 MiB edge limit

# Routes that read their own raw body with their own limit (module docstring). The middleware
# returns None for them and does not touch their body. Matched by method and exact path.
SELF_LIMITED: frozenset[tuple[str, str]] = frozenset(
    {
        ("PUT", "/api/profile/photo"),
        ("POST", "/api/profile/import"),
        ("POST", "/api/jobs/import"),
        ("POST", "/api/ingestion/documents"),  # PR #98: 20 MiB, upload slots
        ("POST", "/api/ingestion/documents/text"),  # PR #98: paste, its own limit
    }
)

TOO_LARGE = "The request body is too large."
TOO_SLOW = "The request body stopped arriving."

# A capped body (small JSON) must bring a chunk at least this often, else the read is abandoned
# with 408. Very generous: an honest body arrives at once, so only a stalled or trickled body is
# cut. Caddy's read_body_idle (15s) fires first in front; this is the second line for direct
# access. Read at call time so a test can lower it.
BODY_READ_IDLE_S = 20.0


def limit_for(method: str, path: str) -> int | None:
    """The body limit in bytes for this request, or None when the middleware must not cap it
    (a route that reads its own body, or a path the api does not serve)."""
    if path != "/api" and not path.startswith("/api/"):
        return None
    if (method, path) in SELF_LIMITED:
        return None
    if path.startswith(PUBLIC_PREFIX):
        return PUBLIC_MAX_BYTES
    return DEFAULT_MAX_BYTES


def _declared_length(scope: Scope) -> int | None:
    """The Content-Length the client declared, or None when it declared none or not a number."""
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            text = value.decode("latin-1").strip()
            return int(text) if text.isdigit() else None
    return None


async def _send_error(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


class LimitRequestBodyMiddleware:
    """Cap the request body by route class before it is parsed (module docstring)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = limit_for(scope.get("method", ""), scope.get("path", ""))
        if limit is None:
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await _send_error(send, 413, TOO_LARGE)  # refuse before reading a byte of body
            return

        received = 0
        over_size = False
        over_time = False
        body_done = False
        sent = False

        async def capped_receive() -> Message:
            nonlocal received, over_size, over_time, body_done
            if body_done:
                # The body is read (or already cut): a later receive is the app waiting for a
                # disconnect, never more body, so it must not carry the idle timeout.
                return await receive()
            try:
                message = await asyncio.wait_for(receive(), BODY_READ_IDLE_S)
            except TimeoutError:
                over_time = True
                body_done = True
                return {"type": "http.request", "body": b"", "more_body": False}
            kind = message.get("type")
            if kind == "http.request":
                received += len(message.get("body", b""))
                if not message.get("more_body", False):
                    body_done = True
                if received > limit:
                    # Cut the body short so the app stops reading and buffers no more; its answer
                    # is replaced by the 413 below.
                    over_size = True
                    body_done = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            elif kind == "http.disconnect":
                body_done = True
            return message

        async def capped_send(message: Message) -> None:
            nonlocal sent
            if not (over_size or over_time):
                await send(message)
                return
            # The body broke a rule: swallow whatever the app answers and send the refusal once.
            if message["type"] == "http.response.start" and not sent:
                sent = True
                if over_size:
                    await _send_error(send, 413, TOO_LARGE)
                else:
                    await _send_error(send, 408, TOO_SLOW)

        await self.app(scope, capped_receive, capped_send)
        if (over_size or over_time) and not sent:
            sent = True
            await _send_error(send, 413 if over_size else 408, TOO_LARGE if over_size else TOO_SLOW)
