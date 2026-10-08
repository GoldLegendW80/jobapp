import pytest
from pydantic import ValidationError

from app.config import ConfigError, Settings, get_settings, load_settings

URL = "postgresql+psycopg://jobapp:secret@db:5432/jobapp"


def test_reads_names_of_env_example(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("SECRET_KEY", "change-me")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")  # unknown to this task: ignored
    settings = load_settings()
    assert settings.app_env == "development"
    assert settings.log_level == "debug"
    assert settings.database_url.get_secret_value() == URL
    assert settings.secret_key is not None
    assert settings.secret_key.get_secret_value() == "change-me"


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    settings = load_settings()
    assert settings.app_env == "development"
    assert settings.log_level == "info"
    assert settings.secret_key is None
    assert settings.db_connect_timeout_s == 2
    assert settings.health_timeout_s == 2.5


def test_missing_database_url_is_a_clear_error() -> None:
    with pytest.raises(ConfigError, match=r"^Invalid configuration: DATABASE_URL is not set$"):
        load_settings()


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    assert get_settings() is get_settings()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgres://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
        ("  postgresql+psycopg://u:p@h/d  ", "postgresql+psycopg://u:p@h/d"),
    ],
)
def test_url_normalised_to_psycopg(raw: str, expected: str) -> None:
    assert Settings(database_url=raw).database_url.get_secret_value() == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("not a url", "not a valid SQLAlchemy URL"),
        ("mysql+pymysql://u:p@h/d", "PostgreSQL with psycopg"),
        ("sqlite:///x.db", "PostgreSQL with psycopg"),
        ("postgresql+psycopg2://u:p@h/d", "PostgreSQL with psycopg"),
        ("postgresql+psycopg://u:p@h", "host and a database"),
        ("postgresql+psycopg:///d", "host and a database"),
    ],
)
def test_invalid_url_rejected(raw: str, fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        Settings(database_url=raw)  # type: ignore[arg-type]


def test_invalid_url_message_through_load_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@h/d")
    with pytest.raises(ConfigError, match="DATABASE_URL must use PostgreSQL"):
        load_settings()


def test_encoded_unicode_password_round_trips() -> None:
    settings = Settings(database_url="postgresql+psycopg://u:p%C3%A4ss%40word%2F@h/d")  # type: ignore[arg-type]
    assert settings.sqlalchemy_url.password == "päss@word/"
    assert settings.sqlalchemy_url.database == "d"


def test_password_never_shown() -> None:
    settings = Settings(database_url="postgresql+psycopg://u:hunter2@h/d")  # type: ignore[arg-type]
    assert "hunter2" not in repr(settings)
    assert "hunter2" not in str(settings.model_dump())
    assert settings.safe_database_url == "postgresql+psycopg://u:***@h/d"


@pytest.mark.parametrize("value", ["INFO", " Warning ", "debug"])
def test_log_level_case_insensitive(value: str) -> None:
    settings = Settings(database_url=URL, log_level=value)  # type: ignore[arg-type]
    assert settings.log_level == value.strip().lower()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LOG_LEVEL", "verbose"),
        ("APP_ENV", "staging"),
        ("DB_CONNECT_TIMEOUT_S", "1"),
        ("DB_CONNECT_TIMEOUT_S", "abc"),
        ("DB_STATEMENT_TIMEOUT_MS", "10"),
        ("HEALTH_TIMEOUT_S", "0"),
        ("HEALTH_TIMEOUT_S", "-1"),
    ],
)
def test_invalid_values_rejected(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        load_settings()


def test_several_problems_reported_together(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "loud")
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert "LOG_LEVEL" in str(info.value)
    assert "DATABASE_URL is not set" in str(info.value)


@pytest.mark.parametrize("secret", [None, "", "change-me", "short"])
def test_production_requires_a_real_secret(secret: str | None) -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(database_url=URL, app_env="production", secret_key=secret)  # type: ignore[arg-type]


def test_production_accepts_a_long_secret() -> None:
    settings = Settings(database_url=URL, app_env="production", secret_key="x" * 48)  # type: ignore[arg-type]
    assert settings.app_env == "production"


def test_settings_are_immutable() -> None:
    settings = Settings(database_url=URL)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        settings.log_level = "debug"  # type: ignore[misc]
