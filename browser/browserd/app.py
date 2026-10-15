"""browserd's HTTP server on :9300 (network `browsing`, reached by the api and the worker) and the
control API of SPEC-browser:

  POST   /leases          {user_id, profile_id, uid, purpose, automation, languages, timezone}
                          201 {lease_id, lease_token, ...} once Chromium answers on CDP;
                          409 {error: busy, holder_purpose} while another lease runs
  DELETE /leases/{id}     204, Chromium closed cleanly; 404 when it is not the current lease
  GET    /leases/current  {lease: {...} | null, last_end: {lease_id, reason} | null}
  GET    /healthz         {ok, display, lease}
  POST   /leases/{id}/tools/{name}, PUT /leases/{id}/files/{name}   the tool API (B5,
                          browserd/tools/routes.py); POST /leases also answers the gate's
                          capability (`gate_capability`) for an apply_submit or profile_edit lease

Every request carries `Authorization: Bearer <BROWSER_CONTROL_SECRET>`, compared in constant
time; without it (or with no secret configured) the answer is 401 and nothing starts. The lease
token is for the tool API of B5 (`Manager.call`). A small HTTP/1.1 server of
the standard library: one request per connection, JSON in and out, bodies capped per route.
"""

import asyncio
import contextlib
import hmac
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from browserd import live
from browserd.leases import LeaseError, Manager, Request
from browserd.tools import routes as tools

log = logging.getLogger("browserd")

Handler = Callable[["Service", "HttpRequest", re.Match[str]], Awaitable[tuple[int, Any]]]


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern[str]
    handler: Handler
    body_max: int = 16 * 1024


@dataclass
class HttpRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body or b"{}")
        except ValueError:
            raise LeaseError(400, "bad_json") from None


class Service:
    """What the handlers reach: the lease manager and the display (set by __main__)."""

    def __init__(self, manager: Manager, secret: bytes) -> None:
        self.manager, self.secret = manager, secret
        self.display_ok: Callable[[], bool] = lambda: True


async def create_lease(svc: Service, req: HttpRequest, _: re.Match[str]) -> tuple[int, Any]:
    lease, token = await svc.manager.acquire(Request.parse(req.json()))
    now = svc.manager.clock()
    answer = {**lease.public(now), "lease_token": token}
    # B5: the gate's capability, for the purposes whose submit a gate's code clicks only.
    capability = tools.issue_capability(lease)
    if capability:
        answer["gate_capability"] = capability
    return 201, answer


async def delete_lease(svc: Service, _: HttpRequest, match: re.Match[str]) -> tuple[int, Any]:
    if not await svc.manager.release(match["lease_id"], "released"):
        raise LeaseError(404, "no_such_lease")
    return 204, None


async def current_lease(svc: Service, _: HttpRequest, __: re.Match[str]) -> tuple[int, Any]:
    lease = svc.manager.current()
    now = svc.manager.clock()
    return 200, {
        "lease": lease.public(now) if lease else None,
        "last_end": svc.manager.last_end,
    }


async def healthz(svc: Service, _: HttpRequest, __: re.Match[str]) -> tuple[int, Any]:
    ok = svc.display_ok()
    return (200 if ok else 503), {
        "ok": ok,
        "display": ok,
        "lease": svc.manager.current() is not None,
    }


LEASE_ID = r"(?P<lease_id>[0-9a-f-]{36})"
CONTROL = [
    Route("POST", re.compile(r"/leases"), create_lease),
    Route("GET", re.compile(r"/leases/current"), current_lease),
    Route("DELETE", re.compile(rf"/leases/{LEASE_ID}"), delete_lease),
    Route("GET", re.compile(r"/healthz"), healthz),
]

# The router list. B4 and B5 each add one line here, nothing else of this file.
ROUTES: list[Route] = [
    *CONTROL,
    *live.ROUTES,  # B4: browserd/live.py, who holds the live view's keyboard and mouse
    *(Route(*spec) for spec in tools.ROUTES),  # B5: browserd/tools/routes.py
]

REASONS = {200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request", 401: "Unauthorized",
           403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
           410: "Gone", 413: "Payload Too Large", 500: "Internal Server Error", 503: "Service Unavailable"}  # fmt: skip  # noqa: E501


def authorized(secret: bytes, headers: dict[str, str]) -> bool:
    given = headers.get("authorization", "")
    scheme, _, value = given.partition(" ")
    if not secret or scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(value.strip().encode(), secret)


async def read_request(
    reader: asyncio.StreamReader,
) -> tuple[HttpRequest, Route | None, Any]:
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    lines = head.decode("latin-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    path = path.split("?", 1)[0]
    route, match, allowed = None, None, False
    for candidate in ROUTES:
        found = candidate.pattern.fullmatch(path)
        if found:
            allowed = True
            if candidate.method == method:
                route, match = candidate, found
                break
    length = int(headers.get("content-length", "0") or 0)
    request = HttpRequest(method, path, headers, b"")
    if route is None:
        return request, None, 405 if allowed else 404
    if length > route.body_max:
        return request, None, 413
    request.body = await asyncio.wait_for(reader.readexactly(length), 30) if length else b""
    return request, route, match


def respond(writer: asyncio.StreamWriter, status: int, payload: Any) -> None:
    body = b"" if payload is None else json.dumps(payload).encode()
    head = (
        f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
    )
    writer.write(head.encode() + body)


def handler_for(
    svc: Service,
) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                request, route, match = await read_request(reader)
            except (
                ValueError,
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError,
                TimeoutError,
            ):
                respond(writer, 400, {"error": "bad_request"})
                return
            # The secret first: an unauthenticated caller learns nothing, not even a route.
            if not authorized(svc.secret, request.headers):
                respond(writer, 401, {"error": "unauthorized"})
                return
            if route is None:
                respond(writer, match, {"error": "not_found" if match == 404 else "refused"})
                return
            try:
                status, payload = await route.handler(svc, request, match)
            except LeaseError as exc:
                status, payload = exc.status, {"error": exc.code, **exc.extra}
            except Exception:
                log.exception("%s %s failed", request.method, route.pattern.pattern)
                status, payload = 500, {"error": "internal"}
            respond(writer, status, payload)
        finally:
            with contextlib.suppress(OSError):
                await writer.drain()
            writer.close()

    return handle
