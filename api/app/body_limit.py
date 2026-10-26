"""A second line, independent of the proxy, against a caller making the api buffer a large body
(issue #100).

FastAPI reads and buffers a request body before the route's dependencies run, so a caller with
no session can make the api hold a body on any JSON route (the public ones, and the ones that
answer 401, whose body is read before the 401). Caddy caps bodies at the edge, but the api must
not depend on the proxy being in front of it.

`LimitRequestBodyMiddleware` is a pure ASGI middleware that runs before the body is parsed. It
does three things, in this order.

1. No session, no body (`SessionGate`, given by app.main): a request under /api that announces
   a body, on a route that is not public, is answered 401 before a byte of its body is read
   when it carries no valid session. The session is looked up the way the routes look it up
   (once per request, kept on the request's state), and a request without the cookie costs no
   database call. So the only bodies a caller with no session can make the api read are those
   of the public routes, which are small (below).
2. A bound on the bodies one user has in flight (`BodiesInFlight`, issue #108): a signed in
   user's request that announces a body, when USER_BODIES_IN_FLIGHT of that user's are already
   being read or worked on, is answered 429 with Retry-After before a byte of it is read. It
   covers the routes whose body is gathered here and the ones that read their own. So what one
   user can make the api hold in bodies is that many times the largest body, not as many as
   uvicorn has connections.
3. A size limit by route class, and a time limit on the read, for the routes whose body the
   framework would read (the routes that read their own raw body keep their own rules and are
   neither capped nor read here: SELF_LIMITED):
   - routes under /api/auth/ (sign in, sign out, link check and accept, and the account
     routes): PUBLIC_MAX_BYTES, a few hundred bytes of sign in or acceptance with wide margin,
     and the short times;
   - a route whose schema allows more, or whose content is capped at far less, than the
     default: its own size, from the schema (SIZED, by method and path template);
   - every other route under /api: DEFAULT_MAX_BYTES, well under Caddy's 10 MiB.

How it caps. A request that announces no body is passed on untouched. A declared Content-Length
over the limit is refused with 413 before a byte of body is read. Otherwise the body is gathered
here, into one buffer, before the app is started: at the first byte past the limit the answer is
413, when nothing came for the idle time or the body is not whole after the total time it is
408, and the app never runs on a body that was refused. The app then receives the body as one
message, so the buffer of the limit's size is all a body can occupy, in whatever small pieces it
was sent (Starlette keeps one object per message it is given).

The refusals are sent from here, not raised: of the exceptions that reach FastAPI while it reads
a body, only its own HTTPException keeps its status, any other becomes a 400 (routing.py).

What a refusal does to the connection (measured in the review of pull request #106, on uvicorn
alone with raw sockets): after the 401, both 413 and the 408 as first written, uvicorn kept the
connection, dropped the rest of the body as body, did not answer a request line hidden in it,
and answered a real request sent after it. The 401 and the 413 keep the connection on purpose:
closing one on which a body is still arriving makes Caddy answer 502 in place of the refusal
(api/app/ingestion/documents_router.py). The 408, whose body will never be whole, now carries
`Connection: close`, as the upload route's does. A refused body that never completes leaves a
direct connection to uvicorn open with no timer; behind Caddy it holds nothing, Caddy closes the
upstream connection of a request it has not finished writing.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from app.apply.answer_limits import LETTER_MAX
from app.documents.content import RAW_MAX_BYTES
from app.jobs.schemas import POSTING_MAX

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

PUBLIC_PREFIX = "/api/auth/"
PUBLIC_MAX_BYTES = 64 * 1024  # a login or an invite acceptance is a few hundred bytes
DEFAULT_MAX_BYTES = 1024 * 1024  # other JSON routes; well under Caddy's 10 MiB edge limit

# Routes whose schema allows more than DEFAULT_MAX_BYTES, each sized from that schema with four
# bytes per character (the longest UTF-8 encoding) plus room for the other fields, and routes
# whose content is capped far below it. Keyed by method and path template (`{name}` is one path
# segment). A session is required before any of it is read. tests/identity/test_body_limit_api.py
# walks every route with a JSON body and fails when a schema can describe more than its class
# takes.
SIZED: dict[tuple[str, str], int] = {
    # JSON can escape one astral character as two six-byte surrogate escapes.
    ("PUT", "/api/applications/{application_id}/answers/{field_key:path}"): 12 * LETTER_MAX + 1024,
    # A posting added by hand: posting_text up to POSTING_MAX characters (jobs/schemas.py).
    ("POST", "/api/jobs"): 4 * POSTING_MAX + 64 * 1024,
    # A draft saved by hand: the content's raw cap (documents/content.py), and room for escapes.
    ("PUT", "/api/documents/{document_id}/draft"): RAW_MAX_BYTES + 48 * 1024,
}

# Routes that read their own raw body with their own limit (module docstring). They are not
# capped and not read here. Matched by method and exact path.
SELF_LIMITED: frozenset[tuple[str, str]] = frozenset(
    {
        ("PUT", "/api/profile/photo"),
        ("POST", "/api/profile/import"),
        ("POST", "/api/jobs/import"),
        ("POST", "/api/ingestion/documents"),  # 20 MiB, upload slots
        ("POST", "/api/ingestion/documents/text"),  # the paste, its own limit
    }
)

TOO_LARGE = "The request body is too large."
TOO_MANY_BODIES = "Too many requests at once. Try again in a moment."
TOO_SLOW = "The request body stopped arriving."

# The time a capped body has to arrive: it must bring something at least every *_IDLE_S and be
# whole within *_TOTAL_S, else 408. The total is for a body sent a byte now and then, which no
# idle rule ends. Both are far above an honest body, which arrives at once. The routes under
# /api/auth/ get the short pair: their body is a few hundred bytes, one packet, and they are the
# only bodies a caller with no session can make the api wait for, so this is how long such a
# caller holds one of uvicorn's connections (--limit-concurrency). The routes of SELF_LIMITED
# keep their own rules. Read at call time so a test can lower them.
PUBLIC_READ_IDLE_S = 5.0
PUBLIC_READ_TOTAL_S = 10.0
BODY_READ_IDLE_S = 20.0
BODY_READ_TOTAL_S = 60.0


# How many requests with a body one user may have in flight at once (issue #108). The limits
# above bound one body; a signed in user could still send many at once, and each holds its
# class's size while it is gathered: measured in the review of pull request #106, 4.45 MiB per
# stalled `POST /api/jobs`, so 85 to 90 of them from one session passed the api's 512 MiB.
# With 6: 6 x 4.45 MiB = 27 MiB per user on the largest class gathered here, 6 x 10 MiB = 60 MiB
# on the largest body a route reads for itself (`POST /api/jobs/import`, Caddy's own limit;
# document uploads keep their own slots). The password hashes leave about 210 MiB of the
# container for bodies (identity/security.py), which is three users each at that worst. The
# page sends its requests one at a time, so 6 at once is far above ordinary use. The bound is
# per user, not for the whole api: more than three invited users all at their worst at the
# same moment are not covered.
USER_BODIES_IN_FLIGHT = 6
BODIES_RETRY_AFTER_S = 1


@dataclass(frozen=True)
class BodyClass:
    max_bytes: int
    idle_s: float
    total_s: float


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _template(path: str) -> re.Pattern[str]:
    return re.compile(
        "".join(
            ".+"
            if part.endswith(":path}")
            else "[^/]+"
            if part.startswith("{")
            else re.escape(part)
            for part in re.split(r"(\{[^}]*\})", path)
        )
    )


_SIZED = tuple((method, _template(path), size) for (method, path), size in SIZED.items())


def _sized(method: str, path: str) -> int:
    for wanted, template, size in _SIZED:
        if wanted == method and template.fullmatch(path):
            return size
    return DEFAULT_MAX_BYTES


def class_for(method: str, path: str) -> BodyClass | None:
    """The size and the times of this request's body, or None when it is not capped here (a
    route that reads its own body, or a path the api does not serve)."""
    if not is_api_path(path) or (method, path) in SELF_LIMITED:
        return None
    if path.startswith(PUBLIC_PREFIX):
        return BodyClass(PUBLIC_MAX_BYTES, PUBLIC_READ_IDLE_S, PUBLIC_READ_TOTAL_S)
    return BodyClass(_sized(method, path), BODY_READ_IDLE_S, BODY_READ_TOTAL_S)


def limit_for(method: str, path: str) -> int | None:
    """The body limit in bytes for this request, or None when it is not capped here."""
    found = class_for(method, path)
    return found.max_bytes if found else None


@dataclass(frozen=True)
class SessionGate:
    """Who may send a body at all (module docstring, 1). `needs_session(method, path)` says
    whether the route is one that answers only with a session; `has_session(scope)` looks the
    session of this request up; `detail` is the sentence of the 401. `owner(scope)`, when
    given, names the user of that session: the key of the bound on bodies in flight."""

    needs_session: Callable[[str, str], bool]
    has_session: Callable[[Scope], Awaitable[bool]]
    detail: str
    owner: Callable[[Scope], Awaitable[str | None]] | None = None


class BodiesInFlight:
    """Requests with a body in flight, counted per user (issue #108). A request counts from the
    moment its session is known until its response starts (or it ends without one): the time
    its body is being gathered here, or read by a route that reads its own, and worked on.
    Touched from the event loop only, so a plain dict is enough."""

    def __init__(self, per_user: int = USER_BODIES_IN_FLIGHT) -> None:
        self.per_user = per_user
        self.refused = 0
        self._counts: dict[str, int] = {}

    def enter(self, owner: str) -> bool:
        count = self._counts.get(owner, 0)
        if count >= self.per_user:
            self.refused += 1
            return False
        self._counts[owner] = count + 1
        return True

    def leave(self, owner: str) -> None:
        count = self._counts.get(owner, 0) - 1
        if count > 0:
            self._counts[owner] = count
        else:
            self._counts.pop(owner, None)  # no entry is kept for a user with nothing in flight

    def count(self, owner: str) -> int:
        return self._counts.get(owner, 0)


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


async def _send_error(
    send: Send, status: int, detail: str, close: bool = False, retry_after: int | None = None
) -> None:
    body = json.dumps({"detail": detail}).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
    ]
    if close:
        headers.append((b"connection", b"close"))
    if retry_after is not None:
        headers.append((b"retry-after", str(retry_after).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


class LimitRequestBodyMiddleware:
    """No session, no body; then a bound on the bodies one user has in flight; then a size and
    a time limit by route class (module docstring). Without a `gate` only the limits apply."""

    def __init__(
        self, app: ASGIApp, gate: SessionGate | None = None, bodies: BodiesInFlight | None = None
    ) -> None:
        self.app = app
        self.gate = gate
        self.bodies = bodies or BodiesInFlight()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not announces_body(scope):
            # Nothing announced, nothing to bound: an event stream, any GET, goes straight on.
            await self.app(scope, receive, send)
            return
        method, path = scope.get("method", ""), scope.get("path", "")
        gate = self.gate
        owner: str | None = None
        if gate is not None and is_api_path(path) and gate.needs_session(method, path):
            if not await gate.has_session(scope):
                # Before anything is read: the body of this request never enters the process.
                await _send_error(send, 401, gate.detail)
                return
            if gate.owner is not None:
                owner = await gate.owner(scope)
        if owner is None:
            await self._limited(scope, receive, send, method, path)
            return
        if not self.bodies.enter(owner):
            # Also before anything is read, and the connection is kept, like the 413 below.
            await _send_error(send, 429, TOO_MANY_BODIES, retry_after=BODIES_RETRY_AFTER_S)
            return
        counted = True

        def leave() -> None:
            nonlocal counted
            if counted:
                counted = False
                self.bodies.leave(owner)

        async def counting_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                leave()  # the body was read and worked on: a long answer holds no place
            await send(message)

        try:
            await self._limited(scope, receive, counting_send, method, path)
        finally:
            leave()  # whatever ended it: a refusal, an error, a client that went away

    async def _limited(
        self, scope: Scope, receive: Receive, send: Send, method: str, path: str
    ) -> None:
        limits = class_for(method, path)
        if limits is None:
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > limits.max_bytes:
            await _send_error(send, 413, TOO_LARGE)  # refuse before reading a byte of body
            return

        # The body is gathered here, whole, before the app runs: into one buffer that never
        # passes the limit, however small the pieces it arrives in, and the app is not started
        # on a body that is then refused.
        buffer = bytearray()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limits.total_s
        while True:
            left = min(limits.idle_s, deadline - loop.time())
            try:
                if left <= 0:
                    raise TimeoutError
                message = await asyncio.wait_for(receive(), left)
            except TimeoutError:
                # A body that will never be whole: the connection goes with the answer.
                await _send_error(send, 408, TOO_SLOW, close=True)
                return
            if message.get("type") != "http.request":
                first: Message | None = message  # the client went away: the app is told
                break
            chunk = message.get("body", b"")
            if len(buffer) + len(chunk) > limits.max_bytes:
                await _send_error(send, 413, TOO_LARGE)
                return
            buffer += chunk
            if not message.get("more_body", False):
                first = {"type": "http.request", "body": bytes(buffer), "more_body": False}
                break
        # Nothing of the body stays referenced from this frame while the app runs: the app
        # holds the one message it is handed, for as long as it needs it and no longer.
        del buffer, message
        chunk = b""

        async def gathered() -> Message:
            nonlocal first
            if first is not None:
                handed, first = first, None
                return handed
            return await receive()  # after the body: the app waiting for a disconnect

        await self.app(scope, gathered, send)
