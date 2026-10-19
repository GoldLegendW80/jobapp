"""The live view's api (app/browser/live_router.py, browser B4) on a real Postgres at head, a real
Redis and browserd's own lease and live logic behind an httpx transport (b3_support.FakeBrowserd).

Two people, A and B: B never gets a ticket, a grant or the input of A's lease, and a ticket is
used once, by the person and the session that asked for it, within its 30 seconds, while the
lease it names is still theirs. Every time comes from the shared injected clock.
"""

import asyncio
import json
import logging
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.browser import live_router
from app.browser.leases import DbProfiles, Leases
from app.main import create_app
from tests.browser.b3_support import IDLE_S, SESSION_MAX_S, Clock, FakeBrowserd, flush, redis_url
from tests.browser.test_b3_api import SECRET_KEY, USER_A, USER_B, no_removals, sign_in
from tests.conftest import make_settings
from tests.identity import conftest as identity
from tests.identity.api_helpers import Api

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd import live

migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory

__all__ = ["engine", "factory", "migrated_url", "no_removals", "redis_url"]

HOST = "app.example.org"
ORIGIN = f"https://{HOST}"
VIEWER = str(uuid.uuid4())


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def browserd(clock: Clock) -> FakeBrowserd:
    return FakeBrowserd(clock, "e2e-browserd-" + uuid.uuid4().hex)


@pytest.fixture
def app(migrated_url: str, redis_url: str, browserd: FakeBrowserd, clock: Clock) -> FastAPI:
    flush(redis_url)
    made = create_app(
        make_settings(
            migrated_url,
            secret_key=SECRET_KEY,
            redis_url=redis_url,
            browser_control_secret=browserd.secret,
        )
    )
    made.state.browser_leases = Leases(
        made.state.queue.pool,
        browserd.client(),
        DbProfiles(made.state.sessionmaker),
        idle_s=IDLE_S,
        session_max_s=SESSION_MAX_S,
        clock=clock,
    )
    return made


@pytest.fixture
def api(app: FastAPI) -> Iterator[Api]:
    with TestClient(app, follow_redirects=False) as client:
        yield Api(client)


@pytest.fixture
def user_a(factory: sessionmaker[Session], api: Api) -> tuple[uuid.UUID, str]:
    return sign_in(factory, api, USER_A)


@pytest.fixture
def user_b(factory: sessionmaker[Session], api: Api) -> tuple[uuid.UUID, str]:
    return sign_in(factory, api, USER_B)


def start(api: Api, token: str) -> str:
    started = api.post("/api/browser/sessions", token=token, json={"purpose": "look"})
    assert started.status_code == 201, started.text
    return str(started.json()["lease"]["id"])


def ticket(api: Api, token: str, viewer: str = VIEWER) -> str:
    answer = api.post("/api/browser/live/ticket", token=token, json={"viewer": viewer})
    assert answer.status_code == 201, answer.text
    body = answer.json()
    assert body["expires_in_s"] == 30
    assert body["url"] == f"/vnc/websockify?ticket={body['ticket']}"
    assert answer.headers["cache-control"] == "no-store"
    return str(body["ticket"])


def auth(api: Api, token: str | None, value: str, **headers: str) -> object:
    sent = {
        "Host": HOST,
        "Origin": ORIGIN,
        "X-Forwarded-Uri": f"/vnc/websockify?ticket={value}",
        "X-Forwarded-Method": "GET",
        **headers,
    }
    return api.get("/api/browser/live/auth", token=token, csrf=False, headers=sent)


def admitted(browserd: FakeBrowserd, answer: object, lease_id: str, viewer: str = VIEWER) -> None:
    """The answer carries a grant browserd admits, once, for that lease and that viewer."""
    assert answer.status_code == 204, answer.text  # type: ignore[attr-defined]
    raw = answer.headers["x-live-grant"]  # type: ignore[attr-defined]
    found = live.read_grant(browserd.secret.encode(), raw, browserd.manager.clock())
    assert found is not None
    assert (found.lease_id, found.viewer) == (lease_id, viewer)
    view = live.live_of(browserd.service)
    view.wall = browserd.manager.clock
    assert view.admit({live.GRANT_HEADER: raw}) is not None
    assert view.admit({live.GRANT_HEADER: raw}) is None


def test_the_holder_gets_a_ticket_and_caddy_a_grant(
    api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str], redis_url: str
) -> None:
    _, a = user_a
    assert api.post("/api/browser/live/ticket", token=a, json={"viewer": VIEWER}).status_code == 403
    lease_id = start(api, a)
    value = ticket(api, a)

    async def keys() -> list[tuple[bytes, bytes]]:
        client = aioredis.from_url(redis_url)
        try:
            found = [key async for key in client.scan_iter(live_router.TICKET_KEY + "*")]
            return [(key, await client.get(key)) for key in found]
        finally:
            await client.aclose()

    stored = asyncio.run(keys())
    assert len(stored) == 1
    assert value.encode() not in stored[0][0]
    assert value.encode() not in stored[0][1]
    assert json.loads(stored[0][1])["lease"] == lease_id

    admitted(browserd, auth(api, a, value), lease_id)
    assert auth(api, a, value).status_code == 403  # one use
    assert asyncio.run(keys()) == []


def test_b_never_gets_into_as_view(
    api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str], user_b: tuple[uuid.UUID, str]
) -> None:
    _, a = user_a
    _, b = user_b
    lease_id = start(api, a)
    # B holds no lease: no ticket, no input.
    assert api.post("/api/browser/live/ticket", token=b, json={"viewer": VIEWER}).status_code == 403
    assert api.post("/api/browser/takeover", token=b, json={"viewer": VIEWER}).status_code == 403
    assert api.post("/api/browser/giveback", token=b, json={"viewer": VIEWER}).status_code == 403
    # A's ticket in B's session is refused (and spent).
    stolen = auth(api, b, ticket(api, a))
    assert stolen.status_code == 403  # type: ignore[attr-defined]
    assert "x-live-grant" not in stolen.headers  # type: ignore[attr-defined]
    assert lease_id not in stolen.text  # type: ignore[attr-defined]
    # Without a session at all: 401.
    assert auth(api, None, ticket(api, a)).status_code == 401  # type: ignore[attr-defined]
    assert live.live_of(browserd.service).holder(lease_id) is None


def test_a_ticket_is_bound_to_its_session_and_its_time(
    api: Api, browserd: FakeBrowserd, clock: Clock, user_a: tuple[uuid.UUID, str]
) -> None:
    _, a = user_a
    lease_id = start(api, a)
    other_session = api.login_token(USER_A)
    assert auth(api, other_session, ticket(api, a)).status_code == 403  # type: ignore[attr-defined]

    late = ticket(api, a)
    clock.advance(30)
    assert auth(api, a, late).status_code == 403  # type: ignore[attr-defined]

    on_time = ticket(api, a)
    clock.advance(29)
    admitted(browserd, auth(api, a, on_time), lease_id)


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example.net"},
        {"Origin": f"https://{HOST}.evil.example.net"},
        {"Origin": "null"},
        {"Origin": ""},
        {"Origin": f"ftp://{HOST}"},
        {"X-Forwarded-Uri": "/vnc/other?ticket={t}"},
        {"X-Forwarded-Uri": "/vnc/websockify?ticket={t}&ticket={t}"},
        {"X-Forwarded-Uri": "/vnc/websockify"},
        {"X-Forwarded-Uri": ""},
    ],
)
def test_a_wrong_origin_or_address_is_refused(
    api: Api, user_a: tuple[uuid.UUID, str], headers: dict[str, str]
) -> None:
    _, a = user_a
    start(api, a)
    value = ticket(api, a)
    sent = {key: item.replace("{t}", value) for key, item in headers.items()}
    assert auth(api, a, value, **sent).status_code == 403  # type: ignore[attr-defined]


def test_the_origin_may_be_the_forwarded_host(
    api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    _, a = user_a
    lease_id = start(api, a)
    answer = auth(
        api, a, ticket(api, a), Host="caddy:8080", Origin="https://jetson.example.ts.net",
        **{"X-Forwarded-Host": "jetson.example.ts.net"},
    )  # fmt: skip
    admitted(browserd, answer, lease_id)


def test_a_ticket_for_a_lease_that_ended(
    api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    _, a = user_a
    first = start(api, a)
    value = ticket(api, a)
    assert api.request("DELETE", "/api/browser/sessions/current", token=a).status_code == 204
    assert auth(api, a, value).status_code == 403  # type: ignore[attr-defined]
    # A new lease of the same person does not revive a ticket of the old one.
    second = start(api, a)
    assert second != first
    stale = ticket(api, a)
    asyncio.run(browserd.manager.release(second, "lease_max"))
    assert auth(api, a, stale).status_code == 403  # type: ignore[attr-defined]


def agent_lease(app: FastAPI, api: Api, user_id: uuid.UUID) -> str:
    """An agent's lease of the person (take over is for those only since B7)."""
    store: Leases = app.state.browser_leases

    async def take() -> str:
        got = await store.acquire(str(user_id), "apply_fill", True)
        assert got.lease is not None
        return got.lease.lease_id

    return str(api.client.portal.call(take))  # type: ignore[union-attr]


def test_take_over_and_give_back(
    app: FastAPI, api: Api, browserd: FakeBrowserd, user_a: tuple[uuid.UUID, str]
) -> None:
    a_id, a = user_a
    lease_id = agent_lease(app, api, a_id)
    taken = api.post("/api/browser/takeover", token=a, json={"viewer": VIEWER})
    assert (taken.status_code, taken.json()) == (200, {"input": "you"})
    assert taken.headers["cache-control"] == "no-store"
    assert live.live_of(browserd.service).holder(lease_id) == VIEWER
    # Another tab's Give back (it unmounted after this tab took over) takes nothing away.
    other = str(uuid.uuid4())
    stale = api.post("/api/browser/giveback", token=a, json={"viewer": other})
    assert (stale.status_code, stale.json()) == (200, {"input": "assistant"})
    assert live.live_of(browserd.service).holder(lease_id) == VIEWER
    back = api.post("/api/browser/giveback", token=a, json={"viewer": VIEWER})
    assert (back.status_code, back.json()) == (200, {"input": "assistant"})
    assert live.live_of(browserd.service).holder(lease_id) is None
    assert api.post("/api/browser/giveback", token=a).status_code == 422  # a viewer is required
    # CSRF and the body's shape are checked like every write.
    assert (
        api.post("/api/browser/takeover", token=a, csrf=False, json={"viewer": VIEWER}).status_code
        == 403
    )
    assert api.post("/api/browser/takeover", token=a, json={"viewer": "x"}).status_code == 422
    browserd.down = True
    assert api.post("/api/browser/takeover", token=a, json={"viewer": VIEWER}).status_code in (
        403,
        503,
    )


def test_no_ticket_or_grant_in_the_logs(
    api: Api,
    browserd: FakeBrowserd,
    user_a: tuple[uuid.UUID, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, a = user_a
    caplog.set_level(logging.DEBUG)
    lease_id = start(api, a)
    value = ticket(api, a)
    answer = auth(api, a, value)
    grant = answer.headers["x-live-grant"]  # type: ignore[attr-defined]
    api.post("/api/browser/takeover", token=a, json={"viewer": VIEWER})
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert lease_id in grant  # an id, not a secret: httpx logs it in browserd's address
    for secret in (value, grant, grant.rsplit(".", 1)[1], grant.split(".")[4]):
        assert secret not in text
