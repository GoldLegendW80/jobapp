"""The request-body limit through the real app (issue #100): with a TestClient over the whole
middleware stack, and over a real socket served by uvicorn, on a public route and on an
authenticated one, plus the size and time limits and that the routes with their own larger limit
still work. The unit-level behaviour of the middleware is in tests/test_body_limit.py."""

import json
import re
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import TypeAdapter
from sqlalchemy.orm import Session, sessionmaker

import app.body_limit as bl
from app.body_limit import DEFAULT_MAX_BYTES, PUBLIC_MAX_BYTES, SELF_LIMITED, SIZED, limit_for
from app.identity.deps import PUBLIC_ROUTES, UNAUTHORIZED, is_public_request
from app.jobs.schemas import POSTING_MAX
from tests.conftest import free_port
from tests.identity.api_helpers import Api, make_user, session_cookie


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


def sample_path(template: str) -> str:
    """A request path for a route's template: every parameter becomes one plain segment."""
    return re.sub(r"\{[^}]*\}", "x", template)


def served_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, path template) the app serves, included routers flattened."""
    found = set()
    for context in iter_route_contexts(app.routes):
        if isinstance(context.original_route, APIRoute):
            found.update((method, context.path or "") for method in context.methods or ())
    return sorted(found)


def test_public_requests_are_told_from_method_and_path_as_the_router_tells_them(
    app: FastAPI,
) -> None:
    # The body limits run before routing and decide from the method and the path alone which
    # requests need a session. That must agree with PUBLIC_ROUTES for every route there is.
    routes = served_routes(app)
    assert len(routes) > 20, routes
    for method, template in routes:
        expected = (method, template) in PUBLIC_ROUTES
        assert is_public_request(method, sample_path(template)) is expected, (method, template)
    for method, path in (
        ("GET", "/api/auth/login"),  # another method
        ("POST", "/api/auth/login/"),  # another spelling
        ("POST", "/api/auth/invites/a/b/accept"),  # a parameter is one segment
        ("POST", "/api/auth/invites//accept"),
        ("POST", "/API/AUTH/LOGIN"),
        ("POST", "/api/nope"),
    ):
        assert not is_public_request(method, path), (method, path)


def test_no_session_no_body_on_every_route_that_is_not_public(app: FastAPI, api: Api) -> None:
    # FastAPI reads a JSON body before the 401. So the 401 is given first, for any request that
    # announces a body, on every route but the public ones, the ones that read their own body
    # included. The body is declared as JSON and is not JSON: FastAPI decodes such a body and
    # answers 422 before it runs the session check (routing.py), so on a route with a JSON
    # body a 401 means the body was not looked at. With the gate taken out, this test fails.
    refused = 0
    for method, template in served_routes(app):
        if (method, template) in PUBLIC_ROUTES or not template.startswith("/api/"):
            continue
        response = api.request(
            method,
            sample_path(template),
            content=b"{",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 401, (method, template, response.text)
        assert response.json() == {"detail": UNAUTHORIZED}, (method, template)
        refused += 1
    assert refused > 20, refused


def test_a_cookie_that_names_no_session_is_refused_the_same_way_and_cleared(api: Api) -> None:
    response = api.post("/api/agents/runs", token="A" * 43, json={"anything": True})
    assert response.status_code == 401
    morsel = session_cookie(response)
    assert morsel is not None
    assert morsel.value == ""  # the stale cookie is dropped


def test_with_a_session_a_json_body_over_the_default_limit_is_413(
    api: Api, factory: sessionmaker[Session]
) -> None:
    make_user(factory, email="harald@example.org")
    token = api.login_token("harald@example.org")
    body = b"y" * (DEFAULT_MAX_BYTES + 1)
    response = api.post(
        "/api/agents/runs",
        token=token,
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413, response.text
    assert response.json() == {"detail": bl.TOO_LARGE}
    small = api.post("/api/agents/runs", token=token, json={"agent": "nope", "input": {}})
    assert small.status_code == 422, small.text  # the route itself answered


def test_self_limited_photo_route_keeps_its_own_larger_limit(
    api: Api, factory: sessionmaker[Session]
) -> None:
    # A signed-in user uploads 1.5 MiB (over the 1 MiB default) of non-image bytes to the photo
    # route. The middleware must not cap it: the route reads it with its own 2 MiB limit and
    # answers 415 (not a PNG or JPEG), proving the body reached the handler.
    make_user(factory, email="harald@example.org")
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


def test_a_chunked_body_cut_at_the_limit_is_not_acted_on(
    api: Api, factory: sessionmaker[Session]
) -> None:
    # The first chunk is a whole JSON document, the rest pads it past the limit. The client
    # reads 413, so nothing may have been done with what came first.
    make_user(factory, email="harald@example.org", name="Harald")
    token = api.login_token("harald@example.org")

    def body() -> Iterator[bytes]:
        yield b'{"name": "Changed"}'
        yield from (b" " * 16384 for _ in range(PUBLIC_MAX_BYTES // 16384 + 1))

    response = api.request(
        "PATCH",
        "/api/auth/me",
        token=token,
        content=body(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413, response.text
    assert api.me(token).json()["name"] == "Harald"


# --- every class fits what its route's schema allows -------------------------------------------


def schema_bound(schema: dict[str, Any], defs: dict[str, Any], depth: int = 0) -> tuple[int, bool]:
    """The most bytes a JSON value of this schema can take with four bytes per character (the
    longest UTF-8 encoding), and whether the schema bounds it at all. A string without a
    maxLength, a list without maxItems or an open object is not bounded: its part counts 0."""
    if depth > 12:
        return 0, False
    if "$ref" in schema:
        return schema_bound(defs[schema["$ref"].split("/")[-1]], defs, depth + 1)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            parts = [schema_bound(sub, defs, depth + 1) for sub in schema[key]]
            return max(part[0] for part in parts), all(part[1] for part in parts)
    kind = schema.get("type")
    if "enum" in schema or "const" in schema or kind in ("boolean", "integer", "number", "null"):
        return 32, True
    if kind == "string":
        if "maxLength" in schema:
            return 4 * schema["maxLength"] + 2, True
        return (40, True) if schema.get("format") in ("uuid", "date", "date-time") else (0, False)
    if kind == "array":
        item, bounded = schema_bound(schema.get("items", {}), defs, depth + 1)
        if "maxItems" in schema:
            return schema["maxItems"] * (item + 1) + 2, bounded
        return item, False
    if kind == "object" or "properties" in schema:
        total, bounded = 2, schema.get("additionalProperties") is False
        for name, sub in schema.get("properties", {}).items():
            size, sub_bounded = schema_bound(sub, defs, depth + 1)
            total += size + len(name) + 4
            bounded = bounded and sub_bounded
        return total, bounded
    return 0, False


# Routes whose schema does not bound its own size, so the class is the bound: each with what
# does limit it. A new one must be looked at and added here.
UNBOUNDED_SCHEMAS = {
    ("POST", "/api/agents/runs"): "input is free JSON, at most 16 KiB (runs_router)",
    ("POST", "/api/proposals/{proposal_id}/accept"): "the edited fields are free JSON",
    ("PUT", "/api/profile/{section}"): "data is validated per section; facts at most 200,000 bytes",
}


def test_no_schema_allows_more_than_the_class_of_its_route_takes(app: FastAPI) -> None:
    # The body limits are set by route class, the schemas by their modules. A schema that grows
    # past its class would turn valid input into 413: this walks every route with a JSON body.
    unbounded = set()
    walked = 0
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if not isinstance(route, APIRoute) or route.body_field is None:
            continue
        schema = TypeAdapter(route.body_field.field_info.annotation).json_schema()
        size, bounded = schema_bound(schema, schema.get("$defs", {}))
        for method in context.methods or ():
            walked += 1
            limit = limit_for(method, sample_path(context.path or ""))
            assert limit is not None, (method, context.path, "a JSON body on a route not capped")
            assert size <= limit, (method, context.path, size, limit)
            if not bounded:
                unbounded.add((method, context.path))
    assert walked >= 15, walked
    assert unbounded == set(UNBOUNDED_SCHEMAS), unbounded ^ set(UNBOUNDED_SCHEMAS)


def test_the_hand_kept_lists_name_routes_that_exist(app: FastAPI) -> None:
    served = set(served_routes(app))
    assert set(SIZED) <= served, set(SIZED) - served
    assert served >= SELF_LIMITED, SELF_LIMITED - served


def test_a_posting_at_the_schema_maximum_in_four_byte_characters_is_saved(
    api: Api, factory: sessionmaker[Session]
) -> None:
    # POSTING_MAX characters is what the schema and the form accept: 4 MB of body when each
    # character takes four bytes, far over the default class, inside the class of this route.
    make_user(factory, email="harald@example.org")
    token = api.login_token("harald@example.org")
    posting = "\U0001f4bc" * POSTING_MAX
    body = json.dumps(
        {"company": "A", "title": "B", "posting_text": posting}, ensure_ascii=False
    ).encode()
    assert DEFAULT_MAX_BYTES < len(body) <= SIZED[("POST", "/api/jobs")]
    response = api.post(
        "/api/jobs", token=token, content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 201, response.text[:300]
    over = api.post(
        "/api/jobs",
        token=token,
        content=body + b" " * (SIZED[("POST", "/api/jobs")] - len(body) + 1),
        headers={"Content-Type": "application/json"},
    )
    assert over.status_code == 413


# --- the deploy's smoke check ------------------------------------------------------------------


def test_the_smoke_check_of_deploy_sh_expects_what_the_api_answers(api: Api) -> None:
    # ./deploy.sh smoke posts 5 MB to /api/health with the CSRF header and no session, and
    # fails the deploy's check when the answer is not the one it names. No CI step runs the
    # smoke, so its expectation is read here and the same request is sent to the app.
    script = (Path(__file__).resolve().parents[3] / "deploy.sh").read_text(encoding="utf-8")
    found = re.search(
        r"head -c (\d+) /dev/zero \| pcurl \"\$url/api/health\" -X POST --data-binary @- "
        r"-H 'Content-Type: application/octet-stream' -H 'X-Requested-With: jobapp'\)\"\n"
        r"\s+if \[\[ \"\$code\" == \"(\d+)\" \]\] && grep -q '([^']+)'",
        script,
    )
    assert found, "the 5 MB check of deploy.sh smoke is not where this test reads it"
    size, status, sentence = int(found.group(1)), int(found.group(2)), found.group(3)
    response = api.post(
        "/api/health",
        content=b"\0" * size,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == status, response.text
    assert sentence in response.text


# --- over a real socket ------------------------------------------------------------------------


class Server:
    """The app on a real uvicorn on a loopback port, for raw-socket tests that the ASGI
    TestClient cannot express (a declared length with no body, a body that stalls)."""

    def __init__(self, app: FastAPI, limit_concurrency: int | None = None) -> None:
        self.port = free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self.port,
                log_config=None,
                ws="none",
                limit_concurrency=limit_concurrency,
            )
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


def test_socket_no_session_is_401_before_any_body_arrives(server: Server) -> None:
    # A route that needs a session, a large body announced, and not one byte of it sent: the
    # answer comes all the same, so it did not wait for the body, and there was none to keep.
    for announced in ("Content-Length: 9000000\r\n", "Transfer-Encoding: chunked\r\n"):
        sock = socket.create_connection(("127.0.0.1", server.port), timeout=10)
        try:
            send_head(sock, "POST", "/api/agents/runs", server.port, announced)
            assert read_status(sock) == 401, announced
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
    monkeypatch.setattr(bl, "PUBLIC_READ_IDLE_S", 0.3)
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


def health_status(port: int) -> int:
    with httpx.Client(timeout=10) as client:
        return client.get(f"http://127.0.0.1:{port}/api/health").status_code


def test_socket_limit_concurrency_counts_open_connections_and_answers_503(app: FastAPI) -> None:
    # What --limit-concurrency does (api/Dockerfile), shown with a small number: the request
    # that finds as many connections open as the limit is answered 503, and a connection counts
    # as soon as it is open, before it has sent anything. So the limit is both the bound on
    # what the api holds and the number of slow connections at which it stops answering.
    server = Server(app, limit_concurrency=3)
    held: list[socket.socket] = []
    try:
        assert health_status(server.port) == 200  # one connection: under the limit
        held = [socket.create_connection(("127.0.0.1", server.port), timeout=10) for _ in range(2)]
        assert health_status(server.port) == 503  # the third connection reaches the limit
        for sock in held:
            sock.close()
        held = []
        deadline = time.monotonic() + 10
        while (status := health_status(server.port)) != 200:  # once the server saw them close
            assert time.monotonic() < deadline, f"still {status} after the connections closed"
    finally:
        for sock in held:
            sock.close()
        server.close()
