"""Alembic against a real Postgres: baseline, idempotence, round trip, locking, naming."""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError

from app.config import ConfigError, get_settings
from app.models_base import NAMING_CONVENTION, Base

API_DIR = Path(__file__).resolve().parents[1]


def alembic_config() -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    return cfg


def current_revision(url: str) -> str | None:
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


@pytest.fixture
def migrated_env(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """DATABASE_URL points at the test database, which starts and ends at base."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.downgrade(alembic_config(), "base")
    yield database_url
    command.downgrade(alembic_config(), "base")


def test_single_head_and_baseline_is_first() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == ["0001"]
    assert script.get_base() == "0001"
    assert script.get_revision("0001").down_revision is None


def test_upgrade_head_reaches_baseline(migrated_env: str) -> None:
    assert current_revision(migrated_env) is None
    command.upgrade(alembic_config(), "head")
    assert current_revision(migrated_env) == "0001"


def test_upgrade_twice_is_idempotent(migrated_env: str) -> None:
    command.upgrade(alembic_config(), "head")
    command.upgrade(alembic_config(), "head")
    assert current_revision(migrated_env) == "0001"


def test_downgrade_base_then_upgrade(migrated_env: str) -> None:
    command.upgrade(alembic_config(), "head")
    command.downgrade(alembic_config(), "base")
    assert current_revision(migrated_env) is None
    command.upgrade(alembic_config(), "head")
    assert current_revision(migrated_env) == "0001"


def test_concurrent_upgrades_do_not_race(migrated_env: str) -> None:
    """Separate processes, as two deploys would run them: all succeed, one version row."""
    env = {**os.environ, "DATABASE_URL": migrated_env}
    command_line = [sys.executable, "-m", "alembic", "upgrade", "head"]
    procs = [
        subprocess.Popen(
            command_line, cwd=API_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        for _ in range(4)
    ]
    outputs = [proc.communicate(timeout=60)[0].decode() for proc in procs]
    assert [proc.returncode for proc in procs] == [0, 0, 0, 0], outputs
    assert current_revision(migrated_env) == "0001"
    engine = sa.create_engine(migrated_env, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.text("select version_num from alembic_version")).all()
    finally:
        engine.dispose()
    assert rows == [("0001",)]


def test_models_match_migrations(migrated_env: str) -> None:
    """`alembic check` finds no difference between Base.metadata and the migrated schema."""
    command.upgrade(alembic_config(), "head")
    command.check(alembic_config())


def test_unknown_revision_is_a_clear_error(migrated_env: str) -> None:
    with pytest.raises(CommandError):
        command.upgrade(alembic_config(), "9999")


def test_offline_sql_needs_no_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere.invalid:5432/x")
    command.upgrade(alembic_config(), "head", sql=True)
    out = capsys.readouterr().out
    assert "CREATE TABLE alembic_version" in out
    assert "INSERT INTO alembic_version (version_num) VALUES ('0001')" in out
    assert "nowhere.invalid" not in out


def test_missing_database_url_fails_with_config_error() -> None:
    with pytest.raises(ConfigError, match="DATABASE_URL is not set"):
        command.upgrade(alembic_config(), "head")


def test_naming_convention_names_constraints() -> None:
    assert Base.metadata.naming_convention == NAMING_CONVENTION
    scratch = sa.MetaData(naming_convention=NAMING_CONVENTION)
    parent = sa.Table("parent", scratch, sa.Column("id", sa.Integer, primary_key=True))
    child = sa.Table(
        "child",
        scratch,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parent_id", sa.ForeignKey(parent.c.id)),
        sa.Column("email", sa.String, unique=True, index=True),
        sa.CheckConstraint("id > 0", name="positive_id"),
    )
    names = {c.name for c in child.constraints} | {i.name for i in child.indexes}
    assert names >= {
        "pk_child",
        "fk_child_parent_id_parent",
        "ck_child_positive_id",
        "ix_child_email",
    }
