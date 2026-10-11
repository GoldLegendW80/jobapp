"""The request-body limit through the real app (issue #100): with a TestClient over the whole
middleware stack, and over a real socket served by uvicorn, on a public route and on an
authenticated one, plus the size and time limits and that the routes with their own larger limit
still work. The unit-level behaviour of the middleware is in tests/test_body_limit.py."""

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

import app.body_limit as bl
from app.body_limit import DEFAULT_MAX_BYTES, PUBLIC_MAX_BYTES
from tests.conftest import free_port
from tests.identity.api_helpers import Api, make_user


def _chunks(total: int, size: int = 16 * 1024) -> Iterator[bytes]:
    sent = 0
    while sent < total:
        step = min(size, total - sent)
        sent += step
        yield b"x" * step


# --- TestClient over the whole middleware stack ------------------------------------------------


def test_public_login_body_over_the_limit_is_413(api: Api) -> None:
    body = b"x" * (PUBLIC_MAX_BYTES + 1)
    response = api.post(
        "/api/auth/login", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413, response.text
    assert response.json() == {"detail": bl.TOO_LARGE}
    assert response.headers["cache-control"] == "no-store"


def test_public_login_chunked_body_over_the_limit_is_413(api: Api) -> None:
    # A generator body makes httpx send it chunked, with no Content-Length: the cap is hit while
    # the body streams, not from the header.
    response = api.post(
        "/api/auth/login",
        content=_chunks(PUBLIC_MAX_BYTES + 64 * 1024),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413, response.text


def test_public_login_small_body_reaches_the_handler(api: Api) -> None:
    response = api.login("nobody@example.org", "whatever-password")
    assert response.status_code == 401  # the handler ran: unknown account, not a 413
    assert response.json()["detail"] != bl.TOO_LARGE


def test_authenticated_route_body_is_capped_even_without_a_session(api: Api) -> None:
    # FastAPI reads the body before the 401, so a caller with no session could make the api buffer
    # it. The middleware caps it: a body over the default limit answers 413, not 401.
    body = b"y" * (DEFAULT_MAX_BYTES + 1)
    response = api.post(
        "/api/agents/runs", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413, response.text


def test_authenticated_route_small_body_without_a_session_is_401(api: Api) -> None:
    response = api.post("/api/agents/runs", json={"anything": True})
    assert response.status_code == 401  # default deny, body was small enough to pass the cap


def test_self_limited_photo_route_keeps_its_own_larger_limit(api: Api, factory: object) -> None:
    # A signed-in user uploads 1.5 MiB (over the 1 MiB default) of non-image bytes to the photo
    # route. The middleware must not cap it: the route reads it with its own 2 MiB limit and
    # answers 415 (not a PNG or JPEG), proving the body reached the handler.
    make_user(factory, email="harald@example.org")  # type: ignore[arg-type]
    token = api.login_token("harald@example.org")
    body = b"\x00" * (DEFAULT_MAX_BYTES + 512 * 1024)
    response = api.request(
        "PUT",
        "/api/profile/photo",
        token=token,
        content=body,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 415, response.text


# --- over a real socket ------------------------------------------------------------------------


class Server:
    """The app on a real uvicorn on a loopback port, for raw-socket tests that the ASGI
    TestClient cannot express (a declared length with no body, a body that stalls)."""

    def __init__(self, app: FastAPI) -> None:
        self.port = free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_config=None, ws="none")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            assert time.monotonic() < deadline, "the server did not start"
            time.sleep(0.01)

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture
def server(app: FastAPI) -> Iterator[Server]:
    made = Server(app)
    yield made
    made.close()


def read_status(sock: socket.socket) -> int:
    sock.settimeout(10)
    data = b""
    while b"\r\n" not in data:
        more = sock.recv(256)
        if not more:
            raise AssertionError(f"connection closed with no status line: {data!r}")
        data += more
    return int(data.split(b" ", 2)[1])


def send_head(sock: socket.socket, method: str, path: str, port: int, extra: str) -> None:
    head = (
        f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"X-Requested-With: jobapp\r\nContent-Type: application/json\r\n{extra}\r\n"
    )
    sock.sendall(head.encode())


def test_socket_declared_length_over_public_limit_is_413(server: Server) -> None:
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=10)
    try:
        send_head(sock, "POST", "/api/auth/login", server.port, "Content-Length: 5000000\r\n")
        sock.sendall(b'{"email":"a@b.co","password":"' + b"x" * 1000)  # a fraction of the body
        assert read_status(sock) == 413
    finally:
        sock.close()


def test_socket_declared_length_over_default_limit_on_authenticated_route_is_413(
    server: Server,
) -> None:
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=10)
    try:
        send_head(sock, "POST", "/api/agents/runs", server.port, "Content-Length: 9000000\r\n")
        sock.sendall(b"y" * 1000)
        assert read_status(sock) == 413
    finally:
        sock.close()


def test_socket_small_login_body_reaches_the_handler(server: Server) -> None:
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=10)
    try:
        body = b'{"email":"nobody@example.org","password":"whatever-password"}'
        send_head(sock, "POST", "/api/auth/login", server.port, f"Content-Length: {len(body)}\r\n")
        sock.sendall(body)
        assert read_status(sock) == 401  # the whole stack ran, not a limit
    finally:
        sock.close()


def test_socket_stalled_body_is_408(server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bl, "BODY_READ_IDLE_S", 0.3)
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=10)
    try:
        # A body under the size limit that then stops arriving: the api aborts it with 408 on its
        # own, without the proxy, within the idle window.
        send_head(sock, "POST", "/api/auth/login", server.port, "Content-Length: 2000\r\n")
        sock.sendall(b'{"email":"a@b.co","password":"')  # a start, then silence
        started = time.monotonic()
        assert read_status(sock) == 408
        assert time.monotonic() - started < 5
    finally:
        sock.close()


def test_socket_health_still_works(server: Server) -> None:
    with httpx.Client(timeout=10) as client:
        response = client.get(f"http://127.0.0.1:{server.port}/api/health")
    assert response.status_code == 200
