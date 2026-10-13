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
    SIZED,
    LimitRequestBodyMiddleware,
    SessionGate,
    class_for,
    limit_for,
)

Message = dict[str, Any]
CHUNKED = [(b"transfer-encoding", b"chunked")]


def scope(method: str, path: str, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "method": method, "path": path, "headers": headers or []}


def length(size: int) -> list[tuple[bytes, bytes]]:
    return [(b"content-length", str(size).encode())]


class Echo:
    """An app that reads the whole body, then answers 200 with what it saw: how many bytes, in
    how many messages. `started` tells whether it was run at all."""

    def __init__(self) -> None:
        self.started = False

    async def __call__(
        self,
        scope: dict,
        receive: Callable[[], Awaitable[Message]],
        send: Callable[[Message], Awaitable[None]],
    ) -> None:
        self.started = True
        read = messages = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            messages += 1
            read += len(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = json.dumps({"read": read, "messages": messages}).encode()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


class Run:
    """One request through the middleware: what was sent back, whether the app ran, how often
    the body was asked for."""

    def __init__(self, gate: SessionGate | None = None) -> None:
        self.app = Echo()
        self.middleware = LimitRequestBodyMiddleware(self.app, gate=gate)
        self.sent: list[Message] = []
        self.reads = 0

    def go(
        self, request: dict, chunks: list[Message], pause_s: float = 0.0, timeout_s: float = 10
    ) -> "Run":
        queue = list(chunks)

        async def receive() -> Message:
            self.reads += 1
            if pause_s:
                await asyncio.sleep(pause_s)
            return queue.pop(0) if queue else {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            self.sent.append(message)

        asyncio.run(asyncio.wait_for(self.middleware(request, receive, send), timeout_s))
        return self

    @property
    def status(self) -> int:
        starts = [m for m in self.sent if m["type"] == "http.response.start"]
        assert len(starts) == 1, starts  # one answer, never a second one on top
        return int(starts[0]["status"])

    @property
    def headers(self) -> dict[bytes, bytes]:
        return dict(next(m for m in self.sent if m["type"] == "http.response.start")["headers"])

    @property
    def body(self) -> dict:
        raw = b"".join(m.get("body", b"") for m in self.sent if m["type"] == "http.response.body")
        return json.loads(raw) if raw else {}


def pieces(total: int, size: int) -> list[Message]:
    """A body of `total` bytes in messages of `size`, the last one ending it."""
    count = total // size
    return [
        {"type": "http.request", "body": b"x" * size, "more_body": index < count - 1}
        for index in range(count)
    ]


WHOLE = [{"type": "http.request", "body": b"{}", "more_body": False}]

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
        ("POST", "/api/jobs", SIZED[("POST", "/api/jobs")]),
        ("PUT", "/api/jobs", DEFAULT_MAX_BYTES),  # the size belongs to the method too
        ("POST", "/api/jobs/bulk", DEFAULT_MAX_BYTES),
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


def test_the_routes_under_auth_get_the_short_times() -> None:
    public = class_for("POST", "/api/auth/login")
    other = class_for("POST", "/api/agents/runs")
    assert public is not None
    assert other is not None
    assert (public.idle_s, public.total_s) == (5.0, 10.0)
    assert (other.idle_s, other.total_s) == (20.0, 60.0)


# --- size limit --------------------------------------------------------------------------------


def test_declared_length_over_limit_is_413_before_the_body_is_read() -> None:
    run = Run().go(scope("POST", "/api/auth/login", length(100_000)), pieces(1000, 1000))
    assert run.status == 413
    assert run.body == {"detail": bl.TOO_LARGE}
    assert run.reads == 0
    assert not run.app.started


def test_streamed_body_over_limit_is_413_and_the_app_never_runs_on_it() -> None:
    # No Content-Length (chunked): the body is counted as it is gathered. What came before the
    # limit could be a whole JSON document: the app must not act on it while the client reads
    # 413.
    chunks = pieces(PUBLIC_MAX_BYTES + 64 * 1024, 16 * 1024)
    run = Run().go(scope("POST", "/api/auth/login", CHUNKED), chunks)
    assert run.status == 413
    assert not run.app.started
    assert run.reads == PUBLIC_MAX_BYTES // (16 * 1024) + 1  # it stopped reading at the limit


def test_default_limit_applies_to_an_authenticated_route_body() -> None:
    chunks = pieces(DEFAULT_MAX_BYTES + 256 * 1024, 128 * 1024)
    run = Run().go(scope("POST", "/api/agents/runs", CHUNKED), chunks)
    assert run.status == 413
    assert not run.app.started


def test_body_at_the_limit_reaches_the_app() -> None:
    chunks = pieces(PUBLIC_MAX_BYTES, PUBLIC_MAX_BYTES)
    run = Run().go(scope("POST", "/api/auth/login", length(PUBLIC_MAX_BYTES)), chunks)
    assert run.status == 200
    assert run.body == {"read": PUBLIC_MAX_BYTES, "messages": 1}


def test_a_body_in_tiny_pieces_reaches_the_app_as_one_message() -> None:
    # Starlette keeps one object per message it is given: 64 KiB in two byte messages held
    # megabytes. Gathered here, it is one buffer of at most the limit and one message.
    run = Run().go(scope("POST", "/api/auth/login", CHUNKED), pieces(PUBLIC_MAX_BYTES, 2))
    assert run.status == 200
    assert run.body == {"read": PUBLIC_MAX_BYTES, "messages": 1}


def test_self_limited_route_is_not_capped_and_not_read_by_the_middleware() -> None:
    # A body well over the default reaches the app as it came: the route reads it itself.
    run = Run().go(scope("PUT", "/api/profile/photo", CHUNKED), pieces(8 * 256 * 1024, 256 * 1024))
    assert run.status == 200
    assert run.body == {"read": 8 * 256 * 1024, "messages": 8}


def test_non_api_path_is_not_capped() -> None:
    run = Run().go(scope("POST", "/assets/app.js", CHUNKED), pieces(8 * 256 * 1024, 256 * 1024))
    assert run.status == 200


def test_a_request_that_announces_no_body_is_passed_on_untouched() -> None:
    # A GET, an event stream: the app gets the server's own receive, with no timer on it.
    run = Run().go(scope("GET", "/api/agents/runs/x/stream"), WHOLE)
    assert run.status == 200
    assert run.body == {"read": 2, "messages": 1}


def test_a_client_that_leaves_while_its_body_is_gathered_is_passed_on_as_such() -> None:
    chunks = [{"type": "http.request", "body": b"ab", "more_body": True}]  # then a disconnect
    run = Run().go(scope("POST", "/api/auth/login", CHUNKED), chunks)
    assert run.app.started
    assert run.body == {"read": 0, "messages": 0}  # the app saw the disconnect, not half a body


# --- time limit --------------------------------------------------------------------------------


def test_a_body_that_stalls_is_408_and_the_connection_goes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bl, "PUBLIC_READ_IDLE_S", 0.05)
    run = Run().go(scope("POST", "/api/auth/login", length(2000)), WHOLE, pause_s=10)
    assert run.status == 408
    assert run.body == {"detail": bl.TOO_SLOW}
    assert run.headers[b"connection"] == b"close"
    assert not run.app.started


def test_a_slow_but_progressing_body_is_not_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each piece comes 10 ms after the last, the idle window is 5 seconds: no load on the
    # machine turns that into a timeout.
    monkeypatch.setattr(bl, "PUBLIC_READ_IDLE_S", 5.0)
    run = Run().go(scope("POST", "/api/auth/login", CHUNKED), pieces(6, 2), pause_s=0.01)
    assert run.status == 200
    assert run.body == {"read": 6, "messages": 1}


def test_a_body_sent_a_byte_now_and_then_is_408_after_the_total_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every byte comes well inside the idle window, so only the total time can end it.
    monkeypatch.setattr(bl, "BODY_READ_IDLE_S", 5.0)
    monkeypatch.setattr(bl, "BODY_READ_TOTAL_S", 0.2)
    endless = [{"type": "http.request", "body": b"a", "more_body": True}] * 5000
    run = Run().go(scope("POST", "/api/agents/runs", CHUNKED), endless, pause_s=0.02)
    assert run.status == 408
    assert 1 <= run.reads < 100  # it was being read, and was ended long before the idle time
    assert not run.app.started


def test_a_declared_body_on_a_stream_that_never_comes_is_one_408_not_a_second_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A GET that declares a body and sends none: the refusal comes before the app has begun to
    # answer, so there is never a second response start on top of a started stream.
    monkeypatch.setattr(bl, "BODY_READ_IDLE_S", 0.05)
    run = Run().go(scope("GET", "/api/agents/runs/x/stream", length(10)), WHOLE, pause_s=10)
    assert run.status == 408  # `status` also checks that there is exactly one response start
    assert not run.app.started


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


def test_without_a_session_the_body_is_never_read() -> None:
    gate = Gate(signed_in=False)
    run = Run(gate.gate()).go(scope("POST", "/api/agents/runs", length(2)), WHOLE)
    assert run.status == 401
    assert run.body == {"detail": "sign in first"}
    assert run.reads == 0
    assert not run.app.started
    assert gate.asked == ["/api/agents/runs"]


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"9000000")],
        [(b"transfer-encoding", b"chunked")],
        [(b"content-length", b"abc")],  # not a number still announces a body
        [(b"content-length", b"00")],  # only the one spelling "0" announces none
    ],
)
def test_every_way_of_announcing_a_body_is_refused_without_a_session(
    headers: list[tuple[bytes, bytes]],
) -> None:
    run = Run(Gate(signed_in=False).gate()).go(scope("PUT", "/api/profile/photo", headers), WHOLE)
    assert run.status == 401  # a route that reads its own body is behind the gate too
    assert run.reads == 0


def test_with_a_session_the_request_goes_on_to_the_limits() -> None:
    gate = Gate(signed_in=True)
    run = Run(gate.gate()).go(scope("POST", "/api/agents/runs", length(2)), WHOLE)
    assert run.status == 200
    assert run.body == {"read": 2, "messages": 1}
    over = Run(gate.gate()).go(
        scope("POST", "/api/agents/runs", length(DEFAULT_MAX_BYTES + 1)), WHOLE
    )
    assert over.status == 413
    assert over.reads == 0


def test_a_public_route_is_not_asked_for_a_session() -> None:
    gate = Gate(signed_in=False, public=frozenset({("POST", "/api/auth/login")}))
    run = Run(gate.gate()).go(scope("POST", "/api/auth/login", length(2)), WHOLE)
    assert run.status == 200
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
    run = Run(gate.gate()).go(scope(method, path, headers), WHOLE)
    assert run.status == 200
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
