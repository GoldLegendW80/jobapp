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
        # Errors and logs never carry bound values: token hashes, password hashes, emails.
        hide_parameters=True,
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


def read_snapshot(db: Session) -> None:
    """Make the reads that follow, up to the end of the session's transaction, see one snapshot.

    Call it before the first statement of a read only route that answers from several queries
    (a page and its counts, a row and its events). At READ COMMITTED, Postgres' default, each
    statement sees what is committed when it starts, so a write committing in between gave an
    answer that agrees with itself nowhere (a page without the row a count includes). At
    REPEATABLE READ they all see the database as it was at the first statement. The level is the
    connection's for this transaction only: the pool resets it when the session is closed, and
    the call is refused once a statement has run (the level of a running transaction cannot be
    changed, and SQLAlchemy would only warn and go on at READ COMMITTED), so a route that
    already read or wrote cannot believe it has a snapshot."""
    if db.in_transaction():
        raise RuntimeError("read_snapshot must be called before the session's first statement")
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
