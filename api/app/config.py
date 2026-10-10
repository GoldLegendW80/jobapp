"""Settings of the api service, read from environment variables.

Variable names match `.env.example` at the repository root. Nothing is read from a file:
Compose injects the environment, and tests set it explicitly.
"""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

PLACEHOLDER_SECRET = "change-me"  # noqa: S105, the template value that production must replace


class ConfigError(RuntimeError):
    """The environment does not describe a runnable service."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", frozen=True)

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    secret_key: SecretStr | None = None
    database_url: SecretStr
    # Connection establishment timeout passed to libpq, in seconds (libpq minimum is 2).
    db_connect_timeout_s: int = Field(default=2, ge=2, le=60)
    # Default server-side statement timeout for every session, in milliseconds.
    db_statement_timeout_ms: int = Field(default=15_000, ge=100, le=600_000)
    # Hard ceiling on the whole health check, in seconds, whatever the database does.
    health_timeout_s: float = Field(default=2.5, gt=0, le=30)
    # Tests only: the base address of a stub serving the Ashby, Greenhouse and Lever posting APIs
    # under /ashby, /greenhouse and /lever (the CI e2e job's tests/ats_stub.py, the sweep tests'
    # api/tests/jobs/fake_ats.py), used by the board check and the sweeps alike. With
    # ATS_BASE_URL_ORG_PREFIX only the orgs starting with it go there (a scratch stack sweeping a
    # fake board and real boards side by side). Both refused in production.
    ats_base_url: str | None = None
    ats_base_url_org_prefix: str | None = None
    # Queue between the api and the worker (jobs J5): arq on Redis.
    redis_url: SecretStr = SecretStr("redis://redis:6379/0")
    # Encryption of the users' model credentials (agents A1, app/agents/crypto.py): 32 random
    # bytes, base64. Required in production; elsewhere the credential routes answer 503 without
    # it. CREDENTIALS_KEY_PREVIOUS is the key being rotated out: still read, never written.
    credentials_key: SecretStr | None = None
    credentials_key_previous: SecretStr | None = None
    # Tests only: the base address of a stub of the Anthropic API that POST
    # /api/me/model-credential/verify calls instead of https://api.anthropic.com. Refused in
    # production.
    model_api_base_url: str | None = None
    # Agent runs (agents A2, app/agents/runner.py). Tests and scratch stacks only: replace the
    # Claude Agent SDK with a replayer of recorded message streams (app/agents/fake_backend.py),
    # so no model is ever called. Refused in production, and the runner checks again.
    agents_fake_backend: bool = False
    # Where each run's scratch directory is made (deleted when the run ends). Default: the
    # system temporary directory, a tmpfs in the worker container (compose.yml).
    agents_scratch_dir: str | None = None

    @field_validator("app_env", "log_level", mode="before")
    @classmethod
    def _lowercase(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("database_url", mode="after")
    @classmethod
    def _postgres_psycopg(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if not raw:
            raise ValueError("DATABASE_URL is empty")
        try:
            url = make_url(raw)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL is not a valid SQLAlchemy URL") from exc
        if url.drivername in ("postgresql", "postgres"):
            url = url.set(drivername="postgresql+psycopg")
        if url.drivername != "postgresql+psycopg":
            raise ValueError(
                "DATABASE_URL must use PostgreSQL with psycopg (postgresql+psycopg://...)"
            )
        if not url.host or not url.database:
            raise ValueError("DATABASE_URL needs a host and a database name")
        return SecretStr(url.render_as_string(hide_password=False))

    @field_validator("ats_base_url", "model_api_base_url", mode="after")
    @classmethod
    def _http_base(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None or not value.strip():
            return None
        name = (info.field_name or "").upper()
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"{name} must be an http or https address")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError(f"{name} must be a plain address without query or credentials")
        return value

    @field_validator("credentials_key", "credentials_key_previous", mode="after")
    @classmethod
    def _credentials_key(cls, value: SecretStr | None, info: ValidationInfo) -> SecretStr | None:
        # Imported here: app.agents imports this module.
        from app.agents.crypto import KeyFormatError, parse_key

        if value is None or not value.get_secret_value().strip():
            return None
        try:
            parse_key(value.get_secret_value())
        except KeyFormatError as exc:
            raise ValueError(f"{(info.field_name or '').upper()} {exc}") from None
        return SecretStr(value.get_secret_value().strip())

    @field_validator("ats_base_url_org_prefix", mode="after")
    @classmethod
    def _org_prefix(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @field_validator("redis_url", mode="after")
    @classmethod
    def _redis(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if not raw.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must start with redis:// or rediss://")
        return SecretStr(raw)

    @model_validator(mode="after")
    def _no_stub_in_production(self) -> "Settings":
        if self.app_env == "production" and self.ats_base_url is not None:
            raise ValueError(
                "ATS_BASE_URL points the board check at a test stub and must not be set "
                "in production"
            )
        if self.app_env == "production" and self.model_api_base_url is not None:
            raise ValueError(
                "MODEL_API_BASE_URL points the credential check at a test stub and must not be "
                "set in production"
            )
        if self.app_env == "production" and self.agents_fake_backend:
            raise ValueError(
                "AGENTS_FAKE_BACKEND replaces the model with recorded streams and must not be "
                "set in production"
            )
        return self

    @model_validator(mode="after")
    def _production_secret(self) -> "Settings":
        if self.app_env == "production":
            secret = self.secret_key.get_secret_value() if self.secret_key else ""
            if secret in ("", PLACEHOLDER_SECRET) or len(secret) < 32:
                raise ValueError(
                    "SECRET_KEY must be set to a random value of at least 32 characters "
                    "in production"
                )
        return self

    # After the SECRET_KEY and stub checks (pydantic runs them in definition order), so their
    # messages come first.
    @model_validator(mode="after")
    def _credentials_keys(self) -> "Settings":
        if self.app_env == "production" and self.credentials_key is None:
            raise ValueError(
                "CREDENTIALS_KEY must be set in production (32 random bytes, base64: "
                'python3 -c "import secrets, base64; '
                'print(base64.b64encode(secrets.token_bytes(32)).decode())")'
            )
        if self.credentials_key_previous is not None and self.credentials_key is None:
            raise ValueError("CREDENTIALS_KEY_PREVIOUS is set without CREDENTIALS_KEY")
        if (
            self.credentials_key is not None
            and self.credentials_key_previous is not None
            and self.credentials_key.get_secret_value()
            == self.credentials_key_previous.get_secret_value()
        ):
            raise ValueError("CREDENTIALS_KEY_PREVIOUS must differ from CREDENTIALS_KEY")
        return self

    @property
    def sqlalchemy_url(self) -> URL:
        return make_url(self.database_url.get_secret_value())

    @property
    def safe_database_url(self) -> str:
        """The database URL with the password masked, for logs."""
        return self.sqlalchemy_url.render_as_string(hide_password=True)


def load_settings() -> Settings:
    """Build settings from the environment, turning validation errors into one clear message."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            name = ".".join(str(part) for part in error["loc"]).upper() or "settings"
            if error["type"] == "missing":
                message = "is not set"
            else:
                message = error["msg"].removeprefix("Value error, ")
            problems.append(f"{name} {message}")
        raise ConfigError("Invalid configuration: " + "; ".join(problems)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def ats_stub_for(settings: Settings, org: str) -> str | None:
    """The test stub's base address when this org's ATS calls go to it (ATS_BASE_URL, filtered
    by ATS_BASE_URL_ORG_PREFIX), else None: the real ATS."""
    if settings.ats_base_url is None:
        return None
    prefix = settings.ats_base_url_org_prefix
    if prefix is not None and not org.startswith(prefix):
        return None
    return settings.ats_base_url
