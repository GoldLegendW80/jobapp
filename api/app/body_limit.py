"""A second line, independent of the proxy, against a caller making the api buffer a large body
(issue #100).

FastAPI reads and buffers a request body before the route's dependencies run, so a caller with
no session can make the api hold a body on any JSON route (the public ones, and the ones that
answer 401, whose body is read before the 401). Caddy caps bodies at the edge, but the api must
not depend on the proxy being in front of it.

`LimitRequestBodyMiddleware` is a pure ASGI middleware that runs before the body is parsed. It
does two things, in this order.

1. No session, no body (`SessionGate`, given by app.main): a request under /api that announces
   a body, on a route that is not public, is answered 401 before a byte of its body is read
   when it carries no valid session. The session is looked up the way the routes look it up
   (once per request, kept on the request's state), and a request without the cookie costs no
   database call. So the only bodies a caller with no session can make the api read are those
   of the public routes, which are small (below).
2. A size limit by route class, and a time limit on the read. It never makes a second copy, and
   it leaves alone the routes that read their own raw body with their own limit:
   - routes under /api/auth/ (sign in, sign out, link check and accept, and the account
     routes): PUBLIC_MAX_BYTES, a few hundred bytes of sign in or acceptance with wide margin;
   - every other JSON route under /api: DEFAULT_MAX_BYTES, well under Caddy's 10 MiB;
   - the routes that take a larger body and read it themselves, each with its own limit, are
     in SELF_LIMITED and are not capped here: the profile photo (PUT, 2 MiB) and profile import
     (POST, 2 MiB), the jobs import (POST, 10 MiB), and the ingestion document upload and paste
     of pull request #98 (POST, its own limits and slots).

How it caps: a declared Content-Length over the limit is refused with 413 before a byte of body
is read; otherwise the body is counted as it streams and, at the first byte past the limit, the
app is told the body ended, so it reads and buffers no more, and whatever it then answers is
replaced by the 413. A body that brings nothing for BODY_READ_IDLE_S, or is not whole after
BODY_READ_TOTAL_S, is ended the same way and answered 408.

The refusal is sent from here, not raised: of the exceptions that reach FastAPI
while it reads a body, only its own HTTPException keeps its status, any other becomes a 400
(routing.py, "There was an error parsing the body"). What the server then does with the
connection and with the rest of the body is uvicorn's business and was not examined.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
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

# The time a capped body (JSON, at most DEFAULT_MAX_BYTES) has to arrive: it must bring a chunk
# at least every BODY_READ_IDLE_S, and be whole within BODY_READ_TOTAL_S, else the read is
# abandoned with 408. The second bound is for a body sent a byte now and then, which no idle
# rule ends. Both are far above an honest body, which arrives at once. Caddy in front ends a
# request after a minute without a byte; these come first. The routes of SELF_LIMITED keep
# their own rules. Read at call time so a test can lower them.
BODY_READ_IDLE_S = 20.0
BODY_READ_TOTAL_S = 60.0


def limit_for(method: str, path: str) -> int | None:
    """The body limit in bytes for this request, or None when the middleware must not cap it
    (a route that reads its own body, or a path the api does not serve)."""
    if not is_api_path(path):
        return None
    if (method, path) in SELF_LIMITED:
        return None
    if path.startswith(PUBLIC_PREFIX):
        return PUBLIC_MAX_BYTES
    return DEFAULT_MAX_BYTES


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


@dataclass(frozen=True)
class SessionGate:
    """Who may send a body at all (module docstring, 1). `needs_session(method, path)` says
    whether the route is one that answers only with a session; `has_session(scope)` looks the
    session of this request up; `detail` is the sentence of the 401."""

    needs_session: Callable[[str, str], bool]
    has_session: Callable[[Scope], Awaitable[bool]]
    detail: str


def _declared_length(scope: Scope) -> int | None:
    """The Content-Length the client declared, or None when it declared none or not a number."""
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            text = value.decode("latin-1").strip()
            return int(text) if text.isdigit() else None
    return None


def announces_body(scope: Scope) -> bool:
    """Does this request say it carries a body: a Content-Length that is not 0 (a value that
    is not a number counts), or a Transfer-Encoding. Without either there is no body to read."""
    for name, value in scope.get("headers", ()):
        if name == b"transfer-encoding":
            return True
        if name == b"content-length" and value.strip() != b"0":
            return True
    return False


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
    """No session, no body; then a size and a time limit by route class (module docstring).
    Without a `gate` only the limits apply."""

    def __init__(self, app: ASGIApp, gate: SessionGate | None = None) -> None:
        self.app = app
        self.gate = gate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method, path = scope.get("method", ""), scope.get("path", "")
        gate = self.gate
        if (
            gate is not None
            and is_api_path(path)
            and announces_body(scope)
            and gate.needs_session(method, path)
            and not await gate.has_session(scope)
        ):
            # Before anything is read: the body of this request never enters the process.
            await _send_error(send, 401, gate.detail)
            return
        limit = limit_for(method, path)
        if limit is None:
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await _send_error(send, 413, TOO_LARGE)  # refuse before reading a byte of body
            return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + BODY_READ_TOTAL_S
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
            left = min(BODY_READ_IDLE_S, deadline - loop.time())
            try:
                if left <= 0:
                    raise TimeoutError
                message = await asyncio.wait_for(receive(), left)
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
