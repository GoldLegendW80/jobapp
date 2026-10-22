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
    # Tests only (CI e2e): the worker's schedule tick every minute instead of every 5, and the
    # ATS client's timeout short with a single attempt, so the browser journeys do not sit
    # through production waits. Refused in production.
    test_fast_timers: bool = False
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
    # The answer streams to the browser while it is written (issue #33, app/agents/live.py):
    # the worker asks Claude Code for partial messages and publishes the text to Redis, the
    # api's run stream relays it. Off: the answer arrives when it is complete, as before.
    agents_live_text: bool = True
    # Mail (mail M1, app/mail/google.py). The OAuth client of the Google Cloud project: both or
    # neither. Without them the mail routes answer that Gmail is not configured.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: SecretStr | None = None
    # Mail M4: the address Google sends the browser back to after the consent screen. Exactly
    # one of the redirect URIs registered on the OAuth client, and never built from a request
    # (a forged Host header must not choose it). Without it Gmail cannot be connected.
    google_oauth_redirect_uri: str | None = None
    # Tests only: where the fake Google is (api/tests/mail/fake_gmail.py, the service gmailstub
    # of tests/gmail-stub.compose.yml). GOOGLE_AUTH_URL is the consent page the browser is sent
    # to; the server calls GOOGLE_TOKEN_BASE_URL (/token, /revoke) and GOOGLE_API_BASE_URL (Gmail
    # and userinfo). Unset, the real Google addresses apply. All three refused in production.
    google_auth_url: str | None = None
    google_token_base_url: str | None = None
    google_api_base_url: str | None = None
    # A status change or a link concluded from a mail applies by itself at this confidence or
    # above, and only under the binding rule; under it, it is a proposal.
    mail_auto_apply_confidence: float = Field(default=0.90, ge=0.5, le=1.0)
    # Sends per user and per day, and per user, company and day. Empty: no limit (the owner's
    # decision of 2026-10-13: Gmail's own quota is the ceiling). The gate checks them.
    mail_send_daily_limit: int | None = Field(default=None, ge=1, le=2000)
    mail_send_company_daily_limit: int | None = Field(default=None, ge=1, le=2000)
    # Automatic model runs (triage, classification, drafts) per user and per day. 0: none.
    mail_auto_runs_per_day: int = Field(default=100, ge=0, le=1000)
    # The storage ceiling per user: stored messages, and bytes of mail text.
    mail_storage_max_messages: int = Field(default=20_000, ge=100, le=200_000)
    mail_storage_max_bytes: int = Field(
        default=300 * 1024 * 1024, ge=1024 * 1024, le=5 * 1024 * 1024 * 1024
    )
    # How long a row of the send log lives, in days.
    mail_sends_keep_days: int = Field(default=365, ge=30, le=3650)
    # The browser module (SPEC-browser). browserd's control API on the internal `browsing` network,
    # behind a shared secret only the api, the worker and the browser container hold. Empty: no
    # browser (the api answers that it is not available).
    browser_url: str = "http://browser:9300"
    browser_control_secret: SecretStr | None = None
    # Active seconds per lease purpose, "purpose=seconds,..." over browserd's table (900 for the
    # apply purposes, 1800 for a person's session); empty keeps the table.
    browser_lease_max_s: str = ""
    # A lease without a tool call or input for this long ends; a person's wait (sign in) has its
    # own deadline, BROWSER_SESSION_MAX_S, and suspends both timers.
    browser_idle_s: int = Field(default=300, ge=30, le=3600)
    browser_session_max_s: int = Field(default=1800, ge=60, le=7200)
    # Past this share of the browser container's memory limit, browserd closes the newest tab.
    browser_mem_guard_pct: int = Field(default=90, ge=50, le=99)
    # Tests only: the browser sources of the sweep (B8, app/browser/sources) read the fixture
    # pages under this address instead of Google Careers and mon-vie-via. Refused in production.
    browser_sources_base_url: str | None = None
    # Documents (SPEC-documents.md, app/documents/). Every setting of the module, added at once
    # by task D2 so that no later task edits this file. A render: its wall time in seconds, the
    # renders one renderer process runs at once, the largest PDF kept.
    documents_render_timeout_s: int = Field(default=30, ge=5, le=300)
    documents_renders_at_once: int = Field(default=1, ge=1, le=8)
    documents_max_pdf_bytes: int = Field(
        default=4 * 1024 * 1024, ge=256 * 1024, le=32 * 1024 * 1024
    )
    # Unfrozen and unapproved versions kept per document (the frozen and approved ones stay).
    documents_versions_kept: int = Field(default=30, ge=5, le=500)
    # One writer run: its turns, its wall time in seconds, its submits.
    documents_generation_max_turns: int = Field(default=10, ge=2, le=50)
    documents_generation_wall_s: int = Field(default=300, ge=30, le=1800)
    documents_max_submits: int = Field(default=4, ge=2, le=10)
    # Automatic generation: runs at once per person, how far back a first base CV reaches, the
    # wait before the one retry, and the defaults of the person's Advanced settings (the fit
    # score threshold and the safety ceiling per day).
    documents_auto_at_once: int = Field(default=1, ge=1, le=4)
    documents_auto_lookback_days: int = Field(default=30, ge=1, le=365)
    documents_auto_retry_after_s: int = Field(default=600, ge=60, le=86_400)
    documents_min_fit_default: int = Field(default=70, ge=0, le=100)
    documents_daily_ceiling_default: int = Field(default=100, ge=1, le=500)
    # Base CVs per person, and the passes of a design capture (one repair, one fidelity).
    documents_base_cvs_max: int = Field(default=10, ge=1, le=50)
    documents_design_passes: int = Field(default=2, ge=1, le=4)
    # The chat of a document: characters of one message, messages kept per document.
    documents_chat_max_chars: int = Field(default=2000, ge=200, le=10_000)
    documents_messages_kept: int = Field(default=200, ge=20, le=2000)
    # Apply (SPEC-apply.md, app/apply/). Every setting of the module, added at once by task A2 so
    # that no later task edits this file. The kill switch of every submission (the administrator's
    # pause in Redis is the other one); how long a person's approval is valid; Full auto's hold
    # before each submit, never under 15 minutes (owner, docs/decisions.md).
    apply_submit_enabled: bool = True
    apply_approval_ttl_s: int = Field(default=86_400, ge=300, le=7 * 86_400)
    apply_hold_s: int = Field(default=900, ge=900, le=86_400)
    # Full auto's guarded defaults (owner decisions 1 and 2): the runaway backstop of
    # submissions per day (no fixed daily number), the default fit threshold offered when the
    # person turns it on, one application per company in this many days, the rehearsals first.
    apply_full_auto_safety_ceiling: int = Field(default=30, ge=1, le=100)
    apply_full_auto_min_fit_default: int = Field(default=80, ge=0, le=100)
    apply_full_auto_company_days: int = Field(default=30, ge=1, le=365)
    apply_rehearsals: int = Field(default=3, ge=1, le=20)
    # The pace per ATS host across all people (the Jetson's one address), and LinkedIn's own.
    apply_host_min_interval_s: int = Field(default=180, ge=10, le=86_400)
    apply_host_daily_max: int = Field(default=20, ge=1, le=500)
    apply_linkedin_min_interval_s: int = Field(default=600, ge=60, le=86_400)
    apply_linkedin_daily_max: int = Field(default=15, ge=1, le=100)
    # Failures before the click: the waits before each retry, in seconds, comma separated (two
    # retries); how long a person is waited for (captcha, sign in) before the lease ends; how many
    # days step and error screenshots are kept.
    apply_retry_delays_s: str = "600,3600"
    apply_needs_person_wait_s: int = Field(default=1800, ge=60, le=86_400)
    apply_step_shots_keep_days: int = Field(default=30, ge=1, le=365)
    # Tests only: the base address of the fake ATS forms server (tests/ats_forms_stub.py); a form
    # address under it is read as the Ashby, Greenhouse or Lever form its path names. Refused in
    # production.
    apply_stub_base_url: str | None = None
    # Tests only: the floor of Full auto's hold on a stack of the fake forms server, in place of
    # APPLY_HOLD_S. Refused in production and without APPLY_STUB_BASE_URL.
    test_apply_hold_s: int | None = Field(default=None, ge=60, le=899)

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

    @field_validator(
        "mail_send_daily_limit",
        "mail_send_company_daily_limit",
        "google_oauth_client_id",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("google_oauth_client_secret", mode="after")
    @classmethod
    def _blank_secret_is_unset(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        return SecretStr(value.get_secret_value().strip())

    @field_validator(
        "ats_base_url",
        "apply_stub_base_url",
        "browser_sources_base_url",
        "model_api_base_url",
        "google_auth_url",
        "google_token_base_url",
        "google_api_base_url",
        mode="after",
    )
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

    @field_validator("apply_retry_delays_s", mode="after")
    @classmethod
    def _retry_delays(cls, value: str) -> str:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not 1 <= len(parts) <= 5 or not all(part.isascii() and part.isdigit() for part in parts):
            raise ValueError("APPLY_RETRY_DELAYS_S must be 1 to 5 whole numbers of seconds")
        delays = [int(part) for part in parts]
        if not all(60 <= delay <= 86_400 for delay in delays):
            raise ValueError("APPLY_RETRY_DELAYS_S values must be between 60 and 86400 seconds")
        return ",".join(str(delay) for delay in delays)

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
        if self.app_env == "production" and self.apply_stub_base_url is not None:
            raise ValueError(
                "APPLY_STUB_BASE_URL points the apply module at a test forms server and must not "
                "be set in production"
            )
        if self.app_env == "production" and self.test_fast_timers:
            raise ValueError(
                "TEST_FAST_TIMERS shortens the schedule tick and the board timeouts for tests and "
                "must not be set in production"
            )
        if self.app_env == "production" and self.test_apply_hold_s is not None:
            raise ValueError(
                "TEST_APPLY_HOLD_S shortens the Full auto hold for tests and must not be set in "
                "production"
            )
        if self.test_apply_hold_s is not None and self.apply_stub_base_url is None:
            raise ValueError(
                "TEST_APPLY_HOLD_S is for a stack of the fake forms server only: it needs "
                "APPLY_STUB_BASE_URL"
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
        if self.app_env == "production" and self.browser_sources_base_url is not None:
            raise ValueError(
                "BROWSER_SOURCES_BASE_URL points the browser sources at test pages and must not "
                "be set in production"
            )
        for name in ("google_auth_url", "google_token_base_url", "google_api_base_url"):
            if self.app_env == "production" and getattr(self, name) is not None:
                raise ValueError(
                    f"{name.upper()} points the mail module at a fake Google and must not be "
                    "set in production"
                )
        return self

    @field_validator("google_oauth_redirect_uri", mode="after")
    @classmethod
    def _redirect_uri(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.path != "/api/mail/oauth/callback"
            or parts.query
            or parts.fragment
            or parts.username
            or parts.password
        ):
            raise ValueError(
                "GOOGLE_OAUTH_REDIRECT_URI must be an http or https address whose path is "
                "/api/mail/oauth/callback"
            )
        return value

    @model_validator(mode="after")
    def _google_redirect_is_https(self) -> "Settings":
        uri = self.google_oauth_redirect_uri
        if self.app_env == "production" and uri is not None and not uri.startswith("https://"):
            raise ValueError("GOOGLE_OAUTH_REDIRECT_URI must be an https address in production")
        return self

    @model_validator(mode="after")
    def _google_client_pair(self) -> "Settings":
        if (self.google_oauth_client_id is None) != (self.google_oauth_client_secret is None):
            raise ValueError(
                "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET are set together or "
                "not at all"
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
    def apply_hold_floor_s(self) -> int:
        """The shortest Full auto hold: APPLY_HOLD_S, or a test stack's TEST_APPLY_HOLD_S."""
        return self.test_apply_hold_s or self.apply_hold_s

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
