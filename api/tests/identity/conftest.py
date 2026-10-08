"""Fixtures of the identity tests: a real Postgres migrated to head, emptied after each test."""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
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
