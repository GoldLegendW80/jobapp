"""Fixtures of the identity tests: a real Postgres migrated to head, emptied after each test."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import get_settings
from app.main import create_app
from tests.conftest import make_settings
from tests.identity.api_helpers import Api, Clock
from tests.test_migrations import alembic_config

IDENTITY_TABLES = ("login_attempts", "invites", "sessions", "users")


@pytest.fixture
def migrated_url(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """DATABASE_URL set and the schema at head (other tests may have left it at base).
    The identity tables are emptied afterwards."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.upgrade(alembic_config(), "head")
    yield database_url
    engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(f"TRUNCATE {', '.join(IDENTITY_TABLES)} RESTART IDENTITY CASCADE"))
    finally:
        engine.dispose()


@pytest.fixture
def engine(migrated_url: str) -> Iterator[sa.Engine]:
    engine = sa.create_engine(migrated_url, poolclass=sa.pool.NullPool)
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine: sa.Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        yield session
        session.rollback()


@pytest.fixture
def clock() -> Clock:
    # Real time (the database stamps created_at with now()), frozen for the test.
    return Clock(datetime.now(UTC).replace(microsecond=0))


@pytest.fixture
def app(migrated_url: str, clock: Clock) -> FastAPI:
    return create_app(make_settings(migrated_url), clock=clock)


@pytest.fixture
def api(app: FastAPI) -> Iterator[Api]:
    # As in production: uvicorn --proxy-headers trusts the X-Forwarded-Proto caddy sends.
    with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="*")) as client:
        yield Api(client)
