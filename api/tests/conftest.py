import os
import socket
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from tests.pg import Postgres, docker_available, start_postgres

ENV_NAMES = (
    "APP_ENV",
    "LOG_LEVEL",
    "SECRET_KEY",
    "DATABASE_URL",
    "DB_CONNECT_TIMEOUT_S",
    "DB_STATEMENT_TIMEOUT_MS",
    "HEALTH_TIMEOUT_S",
    "ATS_BASE_URL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts from an environment without app variables and an empty settings cache."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A real Postgres: TEST_DATABASE_URL when set (CI, compose), else a throwaway container."""
    external = os.environ.get("TEST_DATABASE_URL")
    if external:
        yield external
        return
    if not docker_available():
        pytest.skip("Docker is not available and TEST_DATABASE_URL is not set")
    pg = start_postgres()
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
