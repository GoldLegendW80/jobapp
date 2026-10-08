"""Alembic environment: the database URL comes from app settings (DATABASE_URL), never the ini.

A caller can pass an open connection in `config.attributes["connection"]` (tests do); otherwise
an engine is built from settings with NullPool, so a migration run holds one connection only.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool, text

from app.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Two concurrent `alembic upgrade` runs (a deploy retried while the first still runs) queue on
# this transaction-scoped advisory lock instead of racing on the version table.
MIGRATION_LOCK_ID = 7_406_202_610


def run_migrations_offline() -> None:
    """Emit SQL to stdout (`alembic upgrade head --sql`) without a database connection."""
    context.configure(
        url=get_settings().sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        connection.execute(text("select pg_advisory_xact_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    settings = get_settings()
    engine = create_engine(
        settings.sqlalchemy_url,
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": settings.db_connect_timeout_s},
    )
    try:
        with engine.connect() as conn:
            _run(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
