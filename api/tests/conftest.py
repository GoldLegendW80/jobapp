import os
import socket
from collections.abc import Iterator

import psycopg
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy.engine import make_url

from app.config import Settings, get_settings
from app.identity import security
from app.main import create_app
from tests.pg import Postgres, docker_available, start_postgres

# argon2id at the lowest cost argon2-cffi accepts that still differs from the "weak" hashes the
# rehash tests build (t=1, m=8 and t=1, m=8192, both p=1): about 1 ms instead of about 50 ms per
# hash or verification, which the identity tests do thousands of times. Tests that measure
# argon2 timing, or the production cost itself, are marked `production_argon2` and keep the real
# parameters. The application never sees this hasher outside the test process.
FAST_HASHER = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)

ENV_NAMES = (
    "APP_ENV",
    "LOG_LEVEL",
    "SECRET_KEY",
    "DATABASE_URL",
    "DB_CONNECT_TIMEOUT_S",
    "DB_STATEMENT_TIMEOUT_MS",
    "HEALTH_TIMEOUT_S",
    "ATS_BASE_URL",
    "ATS_BASE_URL_ORG_PREFIX",
    "REDIS_URL",
    "CREDENTIALS_KEY",
    "CREDENTIALS_KEY_PREVIOUS",
    "MODEL_API_BASE_URL",
    "AGENTS_FAKE_BACKEND",
    "AGENTS_SCRATCH_DIR",
    "AGENTS_LIVE_TEXT",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_AUTH_URL",
    "GOOGLE_TOKEN_BASE_URL",
    "GOOGLE_API_BASE_URL",
    "MAIL_AUTO_APPLY_CONFIDENCE",
    "MAIL_SEND_DAILY_LIMIT",
    "MAIL_SEND_COMPANY_DAILY_LIMIT",
    "MAIL_AUTO_RUNS_PER_DAY",
    "MAIL_STORAGE_MAX_MESSAGES",
    "MAIL_STORAGE_MAX_BYTES",
    "MAIL_SENDS_KEEP_DAYS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts from an environment without app variables and an empty settings cache."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def fast_argon2(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The cheap hasher for every test but those marked `production_argon2`."""
    if request.node.get_closest_marker("production_argon2") is None:
        monkeypatch.setattr(security, "_hasher", FAST_HASHER)
    # The cached dummy hash carries the cost of the hasher that made it: rebuild it per test.
    security._dummy_hash.cache_clear()
    yield
    security._dummy_hash.cache_clear()


def _worker_database(base_url: str, worker: str) -> Iterator[str]:
    """A database of its own for one pytest-xdist worker, next to TEST_DATABASE_URL's, made
    fresh and dropped at the end. Migrations, TRUNCATE and advisory locks then never cross
    workers."""
    base = make_url(base_url)
    name = f"{base.database}_{worker}"
    admin = base.set(drivername="postgresql").render_as_string(hide_password=False)
    statements = (
        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)),
        sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)),
    )
    with psycopg.connect(admin, autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)
    yield base.set(database=name).render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(statements[0])


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A real Postgres: TEST_DATABASE_URL when set (CI, compose), else a throwaway container.
    Under pytest-xdist each worker gets a database of its own on that server (or its own
    container)."""
    external = os.environ.get("TEST_DATABASE_URL")
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if external and worker:
        yield from _worker_database(external, worker)
        return
    if external:
        yield external
        return
    if not docker_available():
        pytest.skip("Docker is not available and TEST_DATABASE_URL is not set")
    pg = start_postgres(durable=False)
    yield pg.url()
    pg.remove()


@pytest.fixture
def own_postgres() -> Iterator[Postgres]:
    """A container of its own on a fixed host port, for tests that stop or pause the database."""
    if not docker_available():
        pytest.skip("Docker is not available")
    pg = start_postgres(port=free_port())
    yield pg
    pg.remove()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_settings(database_url: str, **overrides: object) -> Settings:
    return Settings(database_url=database_url, app_env="test", **overrides)  # type: ignore[arg-type]


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings(database_url))) as test_client:
        yield test_client
