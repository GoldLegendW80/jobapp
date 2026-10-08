"""Database engine and the request-scoped session dependency."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def build_engine(settings: Settings) -> Engine:
    """Create the engine lazily: no connection is opened until the first query."""
    return create_engine(
        settings.sqlalchemy_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=settings.db_connect_timeout_s,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": settings.db_connect_timeout_s,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
            "application_name": "jobapp-api",
        },
    )


def build_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    factory: sessionmaker[Session] = request.app.state.sessionmaker
    with factory() as session:
        yield session
