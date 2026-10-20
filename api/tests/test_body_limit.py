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


DRAFT = SIZED[("PUT", "/api/documents/{document_id}/draft")]
DOC = "3f2b8c1e-0d4a-4c6e-9a7b-5e1f2d3c4b5a"


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("PUT", f"/api/documents/{DOC}/draft", DRAFT),
        ("PUT", "/api/documents/x/draft", DRAFT),
        ("PUT", "/api/documents/not-a-uuid.v2/draft", DRAFT),
        ("GET", f"/api/documents/{DOC}/draft", DEFAULT_MAX_BYTES),
        ("PATCH", f"/api/documents/{DOC}/draft", DEFAULT_MAX_BYTES),
        ("POST", f"/api/documents/{DOC}/draft", DEFAULT_MAX_BYTES),
        ("DELETE", f"/api/documents/{DOC}/draft", DEFAULT_MAX_BYTES),
        ("put", f"/api/documents/{DOC}/draft", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/documents/{DOC}/draft/", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/documents/{DOC}/draft/x", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/documents/{DOC}/x/draft", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/documents/{DOC}/drafts", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/documents/{DOC}/Draft", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/documents/{DOC}/draf", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/documents/{DOC}/xdraft", DEFAULT_MAX_BYTES),
        ("PUT", "/api/documents//draft", DEFAULT_MAX_BYTES),
        ("PUT", "/api/documents/draft", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/documents/{DOC}", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/documents/{DOC}/", DEFAULT_MAX_BYTES),
        ("PUT", f"/api//documents/{DOC}/draft", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/document/{DOC}/draft", DEFAULT_MAX_BYTES),
        ("PUT", "/api/documents/a/b/draft", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/x/api/documents/{DOC}/draft", DEFAULT_MAX_BYTES),
        ("PUT", f"/api/documents/{DOC}/draft\n", DEFAULT_MAX_BYTES),
        ("PUT", f"/documents/{DOC}/draft", None),
        ("PUT", f"/api/auth/documents/{DOC}/draft", PUBLIC_MAX_BYTES),
        ("POST", "/api/jobs/", DEFAULT_MAX_BYTES),
        ("POST", "/api/jobs/x", DEFAULT_MAX_BYTES),
        ("POST", "/api//jobs", DEFAULT_MAX_BYTES),
        ("post", "/api/jobs", DEFAULT_MAX_BYTES),
        ("PATCH", "/api/jobs", DEFAULT_MAX_BYTES),
    ],
)
def test_near_misses_of_a_sized_route_get_the_default(
    method: str, path: str, expected: int | None
) -> None:
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


# --- bodies in flight per user (issue #108) ----------------------------------------------------


class Desk:
    """An app that reads its body, then waits to be let go before it answers. With `early`, it
    starts its answer first and waits in the middle of it, like a long event stream."""

    def __init__(self, early: bool = False, fail: bool = False) -> None:
        self.early = early
        self.fail = fail
        self.inside = 0
        self.let_go = asyncio.Event()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        self.inside += 1
        try:
            if self.fail:
                raise RuntimeError("the route broke")
            if self.early:
                await send({"type": "http.response.start", "status": 200, "headers": []})
            await self.let_go.wait()
            if not self.early:
                await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})
        finally:
            self.inside -= 1


def owned_gate(owners: dict[str, str]) -> SessionGate:
    """Everyone has a session; the user is named by the x-user header of the test request."""

    async def has_session(scope: Any) -> bool:
        return True

    async def owner(scope: Any) -> str | None:
        owners["asked"] = scope["path"]
        return dict(scope["headers"]).get(b"x-user", b"").decode() or None

    return SessionGate(lambda method, path: path != "/api/auth/login", has_session, "no", owner)


class Flight:
    """Several requests through one middleware, at once."""

    def __init__(self, app: Any, per_user: int) -> None:
        self.bodies = bl.BodiesInFlight(per_user)
        self.asked: dict[str, str] = {}
        self.middleware = LimitRequestBodyMiddleware(
            app, gate=owned_gate(self.asked), bodies=self.bodies
        )

    def start(
        self, user: str, path: str = "/api/jobs", size: int = 10, stall: bool = False
    ) -> tuple["asyncio.Task[None]", list[Message]]:
        sent: list[Message] = []
        request = scope("POST", path, [*length(size), (b"x-user", user.encode())])
        queue: list[Message] = [{"type": "http.request", "body": b"x" * size, "more_body": False}]

        async def receive() -> Message:
            if stall or not queue:
                await asyncio.sleep(3600)
            return queue.pop(0)

        async def send(message: Message) -> None:
            sent.append(message)

        return asyncio.ensure_future(self.middleware(request, receive, send)), sent


def statuses(sent: list[Message]) -> list[int]:
    return [m["status"] for m in sent if m["type"] == "http.response.start"]


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def test_one_user_has_at_most_six_bodies_in_flight_and_the_rest_are_429() -> None:
    assert bl.USER_BODIES_IN_FLIGHT == 6

    async def scenario() -> None:
        desk = Desk()
        flight = Flight(desk, bl.USER_BODIES_IN_FLIGHT)
        started = [flight.start("ada") for _ in range(10)]
        await settle()
        assert desk.inside == 6
        assert flight.bodies.count("ada") == 6
        refused = [sent for task, sent in started if task.done()]
        assert len(refused) == 4
        for sent in refused:
            assert statuses(sent) == [429]
            headers = dict(sent[0]["headers"])
            assert headers[b"retry-after"] == b"1"
            assert json.loads(sent[1]["body"]) == {"detail": bl.TOO_MANY_BODIES}
        assert flight.bodies.refused == 4

        # Another user is not held back by this one.
        other, other_sent = flight.start("grace")
        await settle()
        assert (desk.inside, flight.bodies.count("grace")) == (7, 1)

        desk.let_go.set()
        await asyncio.gather(other, *(task for task, _ in started))
        assert statuses(other_sent) == [200]
        assert sorted(statuses(sent)[0] for _, sent in started) == [200] * 6 + [429] * 4
        assert (flight.bodies.count("ada"), flight.bodies.count("grace")) == (0, 0)
        assert flight.bodies._counts == {}

        again, again_sent = flight.start("ada")
        await again
        assert statuses(again_sent) == [200]

    asyncio.run(scenario())


def test_a_refused_body_is_never_read() -> None:
    async def scenario() -> None:
        desk = Desk()
        flight = Flight(desk, 1)
        first, _ = flight.start("ada")
        await settle()
        # Its receive would stall for an hour: the 429 comes without asking for a byte.
        second, sent = flight.start("ada", stall=True)
        await asyncio.wait_for(second, 1)
        assert statuses(sent) == [429]
        desk.let_go.set()
        await first

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["too large", "route error", "cancelled", "gathering"])
def test_the_place_is_given_back_however_the_request_ends(ending: str) -> None:
    async def scenario() -> None:
        desk = Desk(fail=ending == "route error")
        flight = Flight(desk, 1)
        if ending == "too large":
            task, sent = flight.start("ada", size=DEFAULT_MAX_BYTES + 1, path="/api/agents")
            await task
            assert statuses(sent) == [413]
        elif ending == "route error":
            task, _ = flight.start("ada")
            with pytest.raises(RuntimeError, match="the route broke"):
                await task
        else:
            # A client that goes away: while the app works, or while its body is gathered.
            task, _ = flight.start("ada", stall=ending == "gathering")
            await settle()
            assert flight.bodies.count("ada") == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert flight.bodies.count("ada") == 0
        assert desk.inside == 0

        desk.fail = False
        desk.let_go.set()
        after, sent = flight.start("ada")
        await after
        assert statuses(sent) == [200]

    asyncio.run(scenario())


def test_a_long_answer_holds_no_place() -> None:
    async def scenario() -> None:
        desk = Desk(early=True)
        flight = Flight(desk, 1)
        streaming, _ = flight.start("ada")
        await settle()
        assert desk.inside == 1  # still answering
        assert flight.bodies.count("ada") == 0  # but its body is done with
        second, _ = flight.start("ada")
        await settle()
        assert desk.inside == 2
        desk.let_go.set()
        await asyncio.gather(streaming, second)

    asyncio.run(scenario())


def test_a_route_that_reads_its_own_body_counts_too() -> None:
    async def scenario() -> None:
        desk = Desk()
        flight = Flight(desk, 1)
        assert ("POST", "/api/jobs/import") in bl.SELF_LIMITED
        upload, _ = flight.start("ada", path="/api/jobs/import")
        await settle()
        assert flight.bodies.count("ada") == 1
        second, sent = flight.start("ada")
        await second
        assert statuses(sent) == [429]
        desk.let_go.set()
        await upload

    asyncio.run(scenario())


def test_public_routes_are_not_counted() -> None:
    async def scenario() -> None:
        desk = Desk()
        flight = Flight(desk, 1)
        tasks = [flight.start("ada", path="/api/auth/login")[0] for _ in range(3)]
        await settle()
        assert desk.inside == 3
        assert flight.bodies._counts == {}
        assert "asked" not in flight.asked  # the user is not even looked up
        desk.let_go.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
