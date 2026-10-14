"""GET /api/health against a real Postgres, and every way the database can be unavailable."""

import gc
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app import health
from app.main import create_app
from tests.conftest import make_settings
from tests.pg import Postgres

DOWN = {"status": "error", "detail": "database unavailable"}
# Acceptance bar: an unavailable database is reported quickly, never by hanging.
FAST = 3.0


def timed_get(client: TestClient, path: str = "/api/health") -> tuple[int, dict, float]:
    start = time.monotonic()
    response = client.get(path)
    return response.status_code, response.json(), time.monotonic() - start


@pytest.fixture
def client_for() -> Iterator:
    clients: list[TestClient] = []

    def build(url: str, **overrides: object) -> TestClient:
        client = TestClient(create_app(make_settings(url, **overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"


def test_health_reuses_pooled_connections(client: TestClient) -> None:
    for _ in range(25):
        assert client.get("/api/health").status_code == 200
    pool = client.app.state.engine.pool  # type: ignore[attr-defined]
    assert pool.checkedout() == 0


def get_concurrently(client: TestClient, requests: int = 60, threads: int = 20) -> list[int]:
    with ThreadPoolExecutor(max_workers=threads) as pool:
        return list(pool.map(lambda _: client.get("/api/health").status_code, range(requests)))


def test_health_concurrent_requests(client: TestClient) -> None:
    # Checks queue behind two health threads and, by design, time out with 503 when the machine
    # is slow, so the count of 200s depends on load. What must hold on any machine: nothing
    # crashes, nothing but 200 or 503 is answered, and no pooled connection stays checked out.
    codes = get_concurrently(client)
    assert set(codes) <= {200, 503}
    assert 200 in codes
    # Pings already running when a request timed out finish before the pool is inspected.
    client.app.state.health.executor.shutdown(wait=True)  # type: ignore[attr-defined]
    assert client.app.state.engine.pool.checkedout() == 0  # type: ignore[attr-defined]


def test_health_concurrent_requests_all_ok_when_the_database_answers(
    client_for, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Generous deadlines (probe wait and statement timeout), so no request can be queued out.
    monkeypatch.setattr(health, "HEALTH_STATEMENT_TIMEOUT_MS", 30_000)
    client = client_for(database_url, health_timeout_s=30)
    assert get_concurrently(client) == [200] * 60


def test_health_queued_behind_busy_workers_is_503(client_for, database_url: str) -> None:
    client = client_for(database_url, health_timeout_s=0.05)
    checker = client.app.state.health  # type: ignore[attr-defined]
    release = threading.Event()
    # The queue is first in, first out: with both of the two workers held, a check can only wait.
    blockers = [checker.executor.submit(release.wait) for _ in range(2)]
    try:
        status, body, _ = timed_get(client)
        assert (status, body) == (503, DOWN)
    finally:
        release.set()
        for blocker in blockers:
            blocker.result()
    # Once the workers are free again, health is ok again. Only the queueing above is under
    # test, so this check gets a timeout no loaded machine reaches.
    checker.timeout_s = 30
    assert client.get("/api/health").status_code == 200


def test_health_query_string_ignored(client: TestClient) -> None:
    assert client.get("/api/health?verbose=1&x=%F0%9F%9A%80").json() == {"status": "ok"}


def test_other_methods_are_405(client: TestClient) -> None:
    # With the CSRF header: without it the CSRF middleware answers 403 before routing.
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        response = client.request(method, "/api/health", headers={"X-Requested-With": "jobapp"})
        assert response.status_code == 405, method
        assert response.json() == {"detail": "Method Not Allowed"}
        assert response.headers["allow"] == "GET"


def test_trailing_slash_redirects_to_health(client: TestClient) -> None:
    response = client.get("/api/health/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/api/health")


def test_connection_refused_is_503_fast(client_for) -> None:
    client = client_for("postgresql+psycopg://jobapp:x@127.0.0.1:1/jobapp")
    status, body, elapsed = timed_get(client)
    assert (status, body) == (503, DOWN)
    assert elapsed < FAST


def test_unknown_host_is_503_fast(client_for) -> None:
    client = client_for("postgresql+psycopg://jobapp:x@no-such-host.invalid:5432/jobapp")
    status, body, elapsed = timed_get(client)
    assert (status, body) == (503, DOWN)
    assert elapsed < FAST


def test_blackholed_host_is_503_within_deadline(client_for) -> None:
    # TEST-NET-1 address: packets are dropped, so only the timeouts can end the attempt.
    client = client_for("postgresql+psycopg://jobapp:x@192.0.2.1:5432/jobapp")
    status, body, elapsed = timed_get(client)
    assert (status, body) == (503, DOWN)
    assert elapsed < FAST


def test_wrong_password_is_503(client_for, database_url: str) -> None:
    url = make_url(database_url).set(password="wrong")
    client = client_for(url.render_as_string(hide_password=False))
    status, body, elapsed = timed_get(client)
    assert (status, body) == (503, DOWN)
    assert elapsed < FAST


def test_unknown_user_is_503(client_for, database_url: str) -> None:
    url = make_url(database_url).set(username="nobody")
    client = client_for(url.render_as_string(hide_password=False))
    assert timed_get(client)[:2] == (503, DOWN)


def test_unknown_database_is_503(client_for, database_url: str) -> None:
    url = make_url(database_url).set(database="does_not_exist")
    client = client_for(url.render_as_string(hide_password=False))
    assert timed_get(client)[:2] == (503, DOWN)


def test_failure_never_leaks_credentials(
    client_for, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    url = make_url(database_url).set(password="leak-canary-7f3a")
    client = client_for(url.render_as_string(hide_password=False))
    response = client.get("/api/health")
    assert response.status_code == 503
    assert "leak-canary-7f3a" not in response.text
    assert "leak-canary-7f3a" not in capsys.readouterr().out


def test_health_timeout_caps_a_slow_check(client_for, database_url: str) -> None:
    # A deadline shorter than any database round trip proves the cap, not the database, decides.
    client = client_for(database_url, health_timeout_s=0.001)
    status, body, _ = timed_get(client)
    assert (status, body) == (503, DOWN)


@pytest.mark.docker
def test_database_stopped_then_back(own_postgres: Postgres) -> None:
    pg = own_postgres
    with TestClient(create_app(make_settings(pg.url()))) as client:
        assert client.get("/api/health").status_code == 200
        pg.stop()
        status, body, elapsed = timed_get(client)
        assert (status, body) == (503, DOWN)
        assert elapsed < FAST
    pg.start()
    with TestClient(create_app(make_settings(pg.url()))) as client:
        assert client.get("/api/health").status_code == 200


@pytest.mark.docker
def test_database_recovers_on_same_app_after_restart(own_postgres: Postgres) -> None:
    pg = own_postgres
    with TestClient(create_app(make_settings(pg.url()))) as client:
        assert client.get("/api/health").status_code == 200
        pg.stop()
        assert client.get("/api/health").status_code == 503
        pg.start()  # same host port: the fixture pins it
        # pool_pre_ping discards the dead pooled connection: no manual reset needed.
        assert client.get("/api/health").status_code == 200


@pytest.mark.docker
# By design, a ping stuck on a frozen database is abandoned at shutdown; its connection is
# reclaimed by the garbage collector once the database answers again.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_paused_database_neither_piles_up_nor_blocks_shutdown(own_postgres: Postgres) -> None:
    pg = own_postgres
    client = TestClient(create_app(make_settings(pg.url(), health_timeout_s=1.0)))
    client.__enter__()
    assert client.get("/api/health").status_code == 200
    pg.pause()
    try:
        # More checks than health threads: each one still answers within its deadline.
        for _ in range(4):
            status, body, elapsed = timed_get(client)
            assert (status, body) == (503, DOWN)
            assert elapsed < 1.5
        start = time.monotonic()
        client.__exit__(None, None, None)
        assert time.monotonic() - start < 3
    finally:
        pg.unpause()
    client.app.state.health.executor.shutdown(wait=True)  # type: ignore[attr-defined]
    gc.collect()


@pytest.mark.docker
def test_database_paused_is_503_not_a_hang(own_postgres: Postgres) -> None:
    pg = own_postgres
    with TestClient(create_app(make_settings(pg.url()))) as client:
        assert client.get("/api/health").status_code == 200
        pg.pause()
        try:
            status, body, elapsed = timed_get(client)
            assert (status, body) == (503, DOWN)
            assert elapsed < FAST
        finally:
            pg.unpause()
        # Once the database answers again, health is ok again.
        deadline = time.monotonic() + 10
        while client.get("/api/health").status_code != 200:
            assert time.monotonic() < deadline, "health did not recover after unpause"
            time.sleep(0.2)
