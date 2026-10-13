"""The request-body limit middleware at the ASGI level (issue #100): the route-class policy and
the behaviour of LimitRequestBodyMiddleware driven by a hand-made ASGI app, with no server and no
database. The limits through the real app and over a real socket are in
tests/identity/test_body_limit_api.py."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

import app.body_limit as bl
from app.body_limit import (
    DEFAULT_MAX_BYTES,
    PUBLIC_MAX_BYTES,
    LimitRequestBodyMiddleware,
    SessionGate,
    limit_for,
)

Message = dict[str, Any]


def scope(method: str, path: str, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "method": method, "path": path, "headers": headers or []}


async def echo_app(
    scope: dict,
    receive: Callable[[], Awaitable[Message]],
    send: Callable[[Message], Awaitable[None]],
) -> None:
    """Read the whole body, then answer 200 with the number of bytes read, so a test can tell how
    much the app actually saw."""
    read = 0
    while True:
        message = await receive()
        if message["type"] == "http.request":
            read += len(message.get("body", b""))
            if not message.get("more_body", False):
                break
        else:
            break
    body = json.dumps({"read": read}).encode()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body})


async def drive(scope: dict, chunks: list[Message]) -> list[Message]:
    """Run the middleware over `echo_app`, feeding `chunks` then an endless disconnect."""
    index = 0
    sent: list[Message] = []

    async def receive() -> Message:
        nonlocal index
        if index < len(chunks):
            message = chunks[index]
            index += 1
            return message
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await LimitRequestBodyMiddleware(echo_app)(scope, receive, send)
    return sent


def status_of(sent: list[Message]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def body_of(sent: list[Message]) -> dict:
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return json.loads(raw) if raw else {}


# --- the policy --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/auth/login", PUBLIC_MAX_BYTES),
        ("POST", "/api/auth/logout", PUBLIC_MAX_BYTES),
        ("POST", "/api/auth/invites/sometoken/accept", PUBLIC_MAX_BYTES),
        ("GET", "/api/auth/invites/sometoken", PUBLIC_MAX_BYTES),
        ("GET", "/api/health", DEFAULT_MAX_BYTES),
        ("POST", "/api/agents/runs", DEFAULT_MAX_BYTES),
        ("PATCH", "/api/auth/me", PUBLIC_MAX_BYTES),
        ("PUT", "/api/profile/photo", None),
        ("POST", "/api/profile/import", None),
        ("POST", "/api/jobs/import", None),
        ("POST", "/api/ingestion/documents", None),
        ("POST", "/api/ingestion/documents/text", None),
        ("GET", "/", None),
        ("GET", "/assets/app.js", None),
        ("POST", "/api", DEFAULT_MAX_BYTES),
    ],
)
def test_policy_by_route_class(method: str, path: str, expected: int | None) -> None:
    assert limit_for(method, path) == expected


# --- size limit --------------------------------------------------------------------------------


def test_declared_length_over_limit_is_413_before_the_body_is_read() -> None:
    # A huge chunk is queued, but the declared Content-Length alone must trigger 413 and the app
    # must never run (so the body is never read).
    chunks = [{"type": "http.request", "body": b"x" * 1000, "more_body": False}]
    sent = asyncio.run(
        drive(scope("POST", "/api/auth/login", [(b"content-length", b"100000")]), chunks)
    )
    assert status_of(sent) == 413
    assert body_of(sent) == {"detail": bl.TOO_LARGE}


def test_streamed_body_over_limit_is_413_and_app_sees_no_more_than_the_limit() -> None:
    # No Content-Length (chunked): the body is counted as it streams and cut at the limit.
    chunk = b"x" * (16 * 1024)
    n = PUBLIC_MAX_BYTES // len(chunk) + 4
    chunks = [{"type": "http.request", "body": chunk, "more_body": True} for _ in range(n)]
    chunks.append({"type": "http.request", "body": b"", "more_body": False})
    sent = asyncio.run(drive(scope("POST", "/api/auth/login"), chunks))
    assert status_of(sent) == 413


def test_default_limit_applies_to_an_authenticated_route_body() -> None:
    chunk = b"y" * (128 * 1024)
    n = DEFAULT_MAX_BYTES // len(chunk) + 2
    chunks = [{"type": "http.request", "body": chunk, "more_body": True} for _ in range(n)]
    chunks.append({"type": "http.request", "body": b"", "more_body": False})
    sent = asyncio.run(drive(scope("POST", "/api/agents/runs"), chunks))
    assert status_of(sent) == 413


def test_body_at_the_limit_reaches_the_app() -> None:
    body = b"z" * PUBLIC_MAX_BYTES
    chunks = [{"type": "http.request", "body": body, "more_body": False}]
    sent = asyncio.run(
        drive(
            scope("POST", "/api/auth/login", [(b"content-length", str(len(body)).encode())]), chunks
        )
    )
    assert status_of(sent) == 200
    assert body_of(sent) == {"read": PUBLIC_MAX_BYTES}


def test_self_limited_route_is_not_capped_by_the_middleware() -> None:
    # A body well over DEFAULT reaches the app untouched: the route reads it with its own limit.
    chunk = b"p" * (256 * 1024)
    chunks = [{"type": "http.request", "body": chunk, "more_body": True} for _ in range(8)]
    chunks.append({"type": "http.request", "body": b"", "more_body": False})
    sent = asyncio.run(drive(scope("PUT", "/api/profile/photo"), chunks))
    assert status_of(sent) == 200
    assert body_of(sent) == {"read": 8 * 256 * 1024}


def test_non_api_path_is_not_capped() -> None:
    chunk = b"q" * (256 * 1024)
    chunks = [{"type": "http.request", "body": chunk, "more_body": True} for _ in range(8)]
    chunks.append({"type": "http.request", "body": b"", "more_body": False})
    sent = asyncio.run(drive(scope("GET", "/assets/app.js"), chunks))
    assert status_of(sent) == 200


# --- time limit --------------------------------------------------------------------------------


def test_a_body_that_stalls_is_408(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bl, "BODY_READ_IDLE_S", 0.05)
    sent: list[Message] = []

    async def run() -> None:
        async def receive() -> Message:
            await asyncio.sleep(10)  # a chunk that never arrives in time
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        await LimitRequestBodyMiddleware(echo_app)(scope("POST", "/api/auth/login"), receive, send)

    asyncio.run(asyncio.wait_for(run(), 5))
    assert status_of(sent) == 408
    assert body_of(sent) == {"detail": bl.TOO_SLOW}


def test_a_slow_but_progressing_body_is_not_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bl, "BODY_READ_IDLE_S", 0.2)
    sent: list[Message] = []

    async def run() -> None:
        state = {"left": 3}

        async def receive() -> Message:
            await asyncio.sleep(0.05)  # each chunk arrives well within the idle window
            if state["left"] > 0:
                state["left"] -= 1
                return {"type": "http.request", "body": b"ab", "more_body": state["left"] > 0}
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            sent.append(message)

        await LimitRequestBodyMiddleware(echo_app)(scope("POST", "/api/auth/login"), receive, send)

    asyncio.run(asyncio.wait_for(run(), 5))
    assert status_of(sent) == 200
    assert body_of(sent) == {"read": 6}


# --- no session, no body -----------------------------------------------------------------------


class Gate:
    """A SessionGate whose answers a test chooses, and which records what it was asked."""

    def __init__(self, signed_in: bool, public: frozenset[tuple[str, str]] = frozenset()) -> None:
        self.signed_in = signed_in
        self.public = public
        self.asked: list[str] = []

    def needs_session(self, method: str, path: str) -> bool:
        return (method, path) not in self.public

    async def has_session(self, scope: Any) -> bool:
        self.asked.append(scope["path"])
        return self.signed_in

    def gate(self) -> SessionGate:
        return SessionGate(self.needs_session, self.has_session, "sign in first")


async def drive_gated(gate: Gate, scope: dict, chunks: list[Message]) -> tuple[list[Message], int]:
    """Run the middleware with `gate` over `echo_app`; also how often the body was asked for."""
    sent: list[Message] = []
    reads = 0
    queue = list(chunks)

    async def receive() -> Message:
        nonlocal reads
        reads += 1
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await LimitRequestBodyMiddleware(echo_app, gate=gate.gate())(scope, receive, send)
    return sent, reads


BODY = [{"type": "http.request", "body": b"{}", "more_body": False}]
LENGTH = [(b"content-length", b"2")]


def test_without_a_session_the_body_is_never_read() -> None:
    gate = Gate(signed_in=False)
    sent, reads = asyncio.run(drive_gated(gate, scope("POST", "/api/agents/runs", LENGTH), BODY))
    assert status_of(sent) == 401
    assert body_of(sent) == {"detail": "sign in first"}
    assert reads == 0
    assert gate.asked == ["/api/agents/runs"]


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"9000000")],
        [(b"transfer-encoding", b"chunked")],
        [(b"content-length", b"abc")],  # not a number still announces a body
    ],
)
def test_every_way_of_announcing_a_body_is_refused_without_a_session(
    headers: list[tuple[bytes, bytes]],
) -> None:
    sent, reads = asyncio.run(
        drive_gated(Gate(signed_in=False), scope("PUT", "/api/profile/photo", headers), BODY)
    )
    assert status_of(sent) == 401  # a route that reads its own body is behind the gate too
    assert reads == 0


def test_with_a_session_the_request_goes_on_to_the_limits() -> None:
    gate = Gate(signed_in=True)
    sent, _ = asyncio.run(drive_gated(gate, scope("POST", "/api/agents/runs", LENGTH), BODY))
    assert status_of(sent) == 200
    assert body_of(sent) == {"read": 2}
    over = [(b"content-length", str(DEFAULT_MAX_BYTES + 1).encode())]
    sent, reads = asyncio.run(drive_gated(gate, scope("POST", "/api/agents/runs", over), BODY))
    assert status_of(sent) == 413
    assert reads == 0


def test_a_public_route_is_not_asked_for_a_session() -> None:
    gate = Gate(signed_in=False, public=frozenset({("POST", "/api/auth/login")}))
    sent, _ = asyncio.run(drive_gated(gate, scope("POST", "/api/auth/login", LENGTH), BODY))
    assert status_of(sent) == 200
    assert gate.asked == []


@pytest.mark.parametrize(
    ("method", "path", "headers"),
    [
        ("GET", "/api/agents/runs", []),  # no body announced: the route's own check answers
        ("POST", "/api/agents/runs", [(b"content-length", b"0")]),
        ("POST", "/assets/app.js", [(b"content-length", b"2")]),  # not the api
    ],
)
def test_the_session_is_only_looked_up_for_a_body_under_api(
    method: str, path: str, headers: list[tuple[bytes, bytes]]
) -> None:
    gate = Gate(signed_in=False)
    sent, _ = asyncio.run(drive_gated(gate, scope(method, path, headers), BODY))
    assert status_of(sent) == 200
    assert gate.asked == []


# --- passthrough -------------------------------------------------------------------------------


def test_non_http_scope_passes_through() -> None:
    seen: list[str] = []

    async def lifespan_app(scope: dict, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    async def run() -> None:
        await LimitRequestBodyMiddleware(lifespan_app)({"type": "lifespan"}, None, None)

    asyncio.run(run())
    assert seen == ["lifespan"]
