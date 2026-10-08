"""Engine and session dependency against a real Postgres."""

import time

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import build_engine, build_sessionmaker, get_session
from app.identity.deps import default_deny
from app.main import create_app
from tests.conftest import make_settings


def test_engine_round_trip_and_settings(database_url: str) -> None:
    engine = build_engine(make_settings(database_url, db_statement_timeout_ms=1234))
    try:
        with engine.connect() as conn:
            assert conn.execute(text("select 1")).scalar_one() == 1
            assert conn.execute(text("show statement_timeout")).scalar_one() == "1234ms"
            assert conn.execute(text("show application_name")).scalar_one() == "jobapp-api"
            assert conn.execute(text("select 'café ✓'")).scalar_one() == "café ✓"
    finally:
        engine.dispose()


def test_statement_timeout_cancels_long_queries(database_url: str) -> None:
    engine = build_engine(make_settings(database_url, db_statement_timeout_ms=300))
    try:
        start = time.monotonic()
        with engine.connect() as conn, pytest.raises(OperationalError, match="statement timeout"):
            conn.execute(text("select pg_sleep(5)"))
        assert time.monotonic() - start < 2
    finally:
        engine.dispose()


def test_sessionmaker_does_not_expire_on_commit(database_url: str) -> None:
    engine = build_engine(make_settings(database_url))
    try:
        factory = build_sessionmaker(engine)
        with factory() as session:
            assert session.execute(text("select 2")).scalar_one() == 2
        assert factory.kw["expire_on_commit"] is False
        assert factory.kw["autoflush"] is False
    finally:
        engine.dispose()


def test_get_session_dependency_closes_session(database_url: str) -> None:
    app = create_app(make_settings(database_url))
    # The probe route is not about sessions: lift the default deny for it.
    app.dependency_overrides[default_deny] = lambda: None
    seen: list[Session] = []

    @app.get("/probe")
    def probe(session: Session = Depends(get_session)) -> dict[str, int]:  # noqa: B008
        seen.append(session)
        return {"value": session.execute(text("select 41 + 1")).scalar_one()}

    with TestClient(app) as client:
        assert client.get("/probe").json() == {"value": 42}
        assert app.state.engine.pool.checkedout() == 0
    assert len(seen) == 1
    assert not seen[0].in_transaction()
