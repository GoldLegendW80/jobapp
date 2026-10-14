"""What the tests of browser B3 share: a real Redis, browserd's own lease logic behind an httpx
transport (browser/browserd/app.py's control routes and leases.Manager, with a fake Chromium), a
clock the test moves, and profiles kept in memory for the tests that need no database.

No test sleeps or reads the wall clock: browserd and the api share the injected clock. Every
secret and token is made up here; every account is `e2e-*@example.org`.
"""

import asyncio
import json
import os
import secrets
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import redis.asyncio as aioredis

from app.browser.client import BrowserdClient
from app.browser.leases import Leases
from app.browser.profiles import ProfileRef
from tests.pg import docker_available
from tests.redis_server import start_redis

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd import app as browserd_app  # noqa: E402
from browserd.config import Config  # noqa: E402
from browserd.leases import LeaseError, Manager, Request  # noqa: E402

BASE = "http://browser:9300"
IDLE_S = 300
SESSION_MAX_S = 1800


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeChromium:
    def __init__(self) -> None:
        self.stopped = self.killed = False

    async def stop(self) -> None:
        self.stopped = True

    async def kill(self) -> None:
        self.killed = True


class FakeBrowserd:
    """browserd's real control routes and lease manager; `down` makes every call fail to
    connect, as a stopped container does. The secret is checked as browserd checks it."""

    def __init__(self, clock: Clock, secret: str) -> None:
        self.secret = secret
        self.down = False
        self.starts: list[Request] = []
        self.chromiums: list[FakeChromium] = []
        self.calls: list[str] = []

        async def start(request: Request, _: str) -> FakeChromium:
            self.starts.append(request)
            self.chromiums.append(FakeChromium())
            return self.chromiums[-1]

        self.manager = Manager(Config(secret=secret.encode(), idle_s=IDLE_S), start, clock)
        self.service = browserd_app.Service(self.manager, secret.encode())
        self.tokens: list[str] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        if self.down:
            raise httpx.ConnectError("browserd is down", request=request)
        headers = {key.lower(): value for key, value in request.headers.items()}
        if not browserd_app.authorized(self.secret.encode(), headers):
            return httpx.Response(401, json={"error": "unauthorized"})
        path = request.url.path
        for route in browserd_app.ROUTES:
            match = route.pattern.fullmatch(path)
            if match is None or route.method != request.method:
                continue
            req = browserd_app.HttpRequest(request.method, path, headers, request.content)
            try:
                status, payload = await route.handler(self.service, req, match)
            except LeaseError as exc:
                status, payload = exc.status, {"error": exc.code, **exc.extra}
            if isinstance(payload, dict) and "lease_token" in payload:
                self.tokens.append(payload["lease_token"])
            if payload is None:
                return httpx.Response(status)
            return httpx.Response(status, content=json.dumps(payload).encode())
        return httpx.Response(404, json={"error": "not_found"})

    def client(self, secret: str | None = None) -> BrowserdClient:
        given = self.secret if secret is None else secret
        return BrowserdClient(BASE, given, transport=httpx.MockTransport(self.handle))


class MemoryProfiles:
    """One profile per person, as app/browser/profiles.py makes them, without a database."""

    def __init__(self) -> None:
        self.rows: dict[str, ProfileRef] = {}
        self.used: dict[str, float] = {}

    async def ensure(self, user_id: str) -> ProfileRef:
        if user_id not in self.rows:
            self.rows[user_id] = ProfileRef(str(uuid.uuid4()), 20000 + len(self.rows))
        return self.rows[user_id]

    async def mark_used(self, user_id: str, at: float) -> None:
        self.used[user_id] = at


def worker_redis_url(url: str) -> str:
    """A database of its own per pytest-xdist worker (as tests/jobs/test_worker.py does)."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return url
    return urlsplit(url)._replace(path=f"/{int(worker.removeprefix('gw')) + 1}").geturl()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    external = os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")
    if external:
        yield worker_redis_url(external)
        return
    if not docker_available():
        pytest.skip("Docker is not available and TEST_REDIS_URL is not set")
    server = start_redis()
    yield worker_redis_url(server.url)
    server.remove()


def flush(url: str) -> None:
    """Empty this worker's database, from a client of its own closed on every path."""

    async def go() -> None:
        client = aioredis.from_url(url)
        try:
            await client.flushdb()
        finally:
            await client.aclose()

    asyncio.run(go())


def pool_of(url: str) -> tuple[Callable[[], Awaitable[Any]], Callable[[], Awaitable[None]]]:
    """A lazily made client (bound to the loop that first asks) and its closing."""
    held: list[Any] = []

    async def pool() -> Any:
        if not held:
            held.append(aioredis.from_url(url))
        return held[0]

    async def close() -> None:
        for client in held:
            await client.aclose()

    return pool, close


class World:
    """A store on a real Redis, a fake browserd and a moving clock, flushed when made."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.clock = Clock()
        self.secret = secrets.token_hex(16)
        self.browserd = FakeBrowserd(self.clock, self.secret)
        self.profiles = MemoryProfiles()
        self.pool, self.close = pool_of(url)
        self.store = self.make_store()

    def make_store(self, **overrides: Any) -> Leases:
        return Leases(
            self.pool,
            overrides.pop("client", None) or self.browserd.client(),
            overrides.pop("profiles", None) or self.profiles,
            idle_s=IDLE_S,
            session_max_s=SESSION_MAX_S,
            clock=self.clock,
            **overrides,
        )

    async def redis(self) -> Any:
        return await self.pool()

    async def flush(self) -> None:
        await (await self.pool()).flushdb()

    async def dump(self) -> str:
        """Every key and value of this database, as text, for the canary."""
        r = await self.pool()
        out = []
        async for key in r.scan_iter():
            kind = (await r.type(key)).decode()
            if kind == "string":
                value: Any = await r.get(key)
            elif kind == "zset":
                value = await r.zrange(key, 0, -1, withscores=True)
            elif kind == "list":
                value = await r.lrange(key, 0, -1)
            else:
                value = kind
            out.append(f"{key!r}={value!r}")
        return "\n".join(out)
