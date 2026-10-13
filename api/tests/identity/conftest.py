"""Fixtures of the identity tests: a real Postgres migrated to head, emptied after each test."""

import atexit
import functools
from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import get_settings
from app.main import create_app
from tests.conftest import make_settings
from tests.identity.api_helpers import Api, Clock
from tests.test_migrations import alembic_config

IDENTITY_TABLES = ("login_attempts", "invites", "sessions", "users")


@functools.lru_cache(maxsize=1)
def script_head() -> str:
    """The head revision of api/alembic, read from the scripts once per test process."""
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    assert head is not None, "api/alembic has no single head"
    return head


# One autocommit connection per test process and database for the two statements every test
# runs around its body (the version check before, the TRUNCATE after). Opening a connection costs
# about 8 ms against the test Postgres, and the fixture used to open two per test.
_housekeeping: dict[str, psycopg.Connection[tuple[object, ...]]] = {}


@atexit.register
def _close_housekeeping() -> None:
    """At the end of the test process (each pytest-xdist worker is one)."""
    for conn in _housekeeping.values():
        conn.close()
    _housekeeping.clear()


def housekeeping(database_url: str, statement: str) -> list[tuple[object, ...]]:
    """Run one statement on the housekeeping connection and return its rows (none for a command).
    A connection the server closed meanwhile (a database dropped, a server restarted) is opened
    again once."""
    for attempt in (1, 2):
        conn = _housekeeping.get(database_url)
        if conn is None or conn.closed:
            libpq = make_url(database_url).set(drivername="postgresql")
            conn = psycopg.connect(libpq.render_as_string(hide_password=False), autocommit=True)
            _housekeeping[database_url] = conn
        try:
            cursor = conn.execute(statement.encode())
            return cursor.fetchall() if cursor.description else []
        except psycopg.OperationalError:
            conn.close()
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def at_head(database_url: str) -> bool:
    """One query on the version table: is this database at the head revision already?"""
    try:
        rows = housekeeping(database_url, "select version_num from alembic_version")
    except psycopg.errors.UndefinedTable:
        return False
    return rows == [(script_head(),)]


@pytest.fixture
def migrated_url(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """DATABASE_URL set and the schema at head (other tests may have left it at base).
    The identity tables are emptied afterwards.

    Nearly every test finds the schema at head, and `alembic upgrade` with nothing to do still
    loads env.py and every migration script on each call (issue #91). So the version table is
    asked first, and alembic runs only when it has work."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    if at_head(database_url):
        # What env.py leaves behind when it runs: the settings read once from this environment.
        get_settings()
    else:
        command.upgrade(alembic_config(), "head")
    yield database_url
    housekeeping(database_url, f"TRUNCATE {', '.join(IDENTITY_TABLES)} RESTART IDENTITY CASCADE")


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
def one_connection(app: FastAPI, migrated_url: str) -> Iterator[sa.Engine]:
    """The app's sessions on an engine whose pool holds ONE connection, so a request and the
    check that follows it use the same Postgres connection (the `factory` engine is a NullPool:
    every session of it is a new connection, and says nothing about the app's pool)."""
    pooled = sa.create_engine(migrated_url, pool_size=1, max_overflow=0, pool_timeout=10)
    app.state.sessionmaker = sessionmaker(bind=pooled, autoflush=False, expire_on_commit=False)
    yield pooled
    pooled.dispose()


@pytest.fixture
def api(app: FastAPI) -> Iterator[Api]:
    # As in production: uvicorn --proxy-headers trusts the X-Forwarded-Proto caddy sends.
    with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="*")) as client:
        yield Api(client)
