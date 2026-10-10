"""CREDENTIALS_KEY, CREDENTIALS_KEY_PREVIOUS and MODEL_API_BASE_URL in the settings: required and
valid in production, optional elsewhere, never echoed in an error, and the app refusing to start
in production without a valid key."""

import base64

import pytest

from app.config import ConfigError, Settings, load_settings
from app.main import create_app, credentials_keyring
from tests.agents.conftest import new_key

URL = "postgresql+psycopg://jobapp:secret@db:5432/jobapp"
STRONG = "x" * 48


def prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", STRONG)


def test_optional_outside_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    settings = load_settings()
    assert settings.credentials_key is None
    assert settings.credentials_key_previous is None
    assert settings.model_api_base_url is None
    assert credentials_keyring(settings) is None


def test_empty_value_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("CREDENTIALS_KEY", "  ")
    assert load_settings().credentials_key is None


def test_production_requires_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    prod(monkeypatch)
    with pytest.raises(ConfigError, match="CREDENTIALS_KEY must be set in production"):
        load_settings()


def test_production_accepts_a_valid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    prod(monkeypatch)
    key = new_key()
    monkeypatch.setenv("CREDENTIALS_KEY", key)
    settings = load_settings()
    keyring = credentials_keyring(settings)
    assert keyring is not None
    assert keyring.current == base64.b64decode(key)
    assert keyring.previous is None


@pytest.mark.parametrize("env", ["development", "production"])
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("change-me-not-base64!", "must be base64"),
        (base64.b64encode(b"k" * 24).decode(), "must decode to 32 bytes, not 24"),
    ],
)
def test_invalid_key_is_refused_without_echo(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str, reason: str
) -> None:
    prod(monkeypatch)
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("CREDENTIALS_KEY", value)
    with pytest.raises(ConfigError, match=f"CREDENTIALS_KEY {reason}") as info:
        load_settings()
    assert value not in str(info.value)


def test_previous_key_is_validated_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("CREDENTIALS_KEY", new_key())
    monkeypatch.setenv("CREDENTIALS_KEY_PREVIOUS", "nope")
    with pytest.raises(ConfigError, match="CREDENTIALS_KEY_PREVIOUS must"):
        load_settings()


def test_previous_without_current_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("CREDENTIALS_KEY_PREVIOUS", new_key())
    with pytest.raises(ConfigError, match="CREDENTIALS_KEY_PREVIOUS is set without"):
        load_settings()


def test_previous_equal_to_current_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    key = new_key()
    monkeypatch.setenv("CREDENTIALS_KEY", key)
    monkeypatch.setenv("CREDENTIALS_KEY_PREVIOUS", f" {key} ")
    with pytest.raises(ConfigError, match="must differ"):
        load_settings()


def test_rotation_pair_builds_a_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    current, previous = new_key(), new_key()
    monkeypatch.setenv("CREDENTIALS_KEY", current)
    monkeypatch.setenv("CREDENTIALS_KEY_PREVIOUS", previous)
    keyring = credentials_keyring(load_settings())
    assert keyring is not None
    assert keyring.previous == base64.b64decode(previous)


def test_secrets_are_not_in_the_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    current, previous = new_key(), new_key()
    monkeypatch.setenv("CREDENTIALS_KEY", current)
    monkeypatch.setenv("CREDENTIALS_KEY_PREVIOUS", previous)
    text = repr(load_settings())
    assert current not in text
    assert previous not in text


def test_live_text_is_on_by_default_and_can_be_turned_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AGENTS_LIVE_TEXT (issue #33): the kill switch of the answer streamed while written."""
    monkeypatch.setenv("DATABASE_URL", URL)
    assert load_settings().agents_live_text is True
    monkeypatch.setenv("AGENTS_LIVE_TEXT", "false")
    assert load_settings().agents_live_text is False
    # Production takes it too: unlike the test switches, it is a setting of the real thing.
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "s" * 40)
    monkeypatch.setenv("CREDENTIALS_KEY", new_key())
    assert load_settings().agents_live_text is False


def test_model_api_stub_read_outside_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("MODEL_API_BASE_URL", " http://anthropic-stub:8098/ ")
    assert load_settings().model_api_base_url == "http://anthropic-stub:8098"


@pytest.mark.parametrize("value", ["ftp://x", "http://u:p@x", "http://x/?q=1", "nohost"])
def test_model_api_stub_must_be_a_plain_http_address(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("MODEL_API_BASE_URL", value)
    with pytest.raises(ConfigError, match="MODEL_API_BASE_URL must be"):
        load_settings()


def test_model_api_stub_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    prod(monkeypatch)
    monkeypatch.setenv("CREDENTIALS_KEY", new_key())
    monkeypatch.setenv("MODEL_API_BASE_URL", "http://anthropic-stub:8098")
    with pytest.raises(ConfigError, match=r"MODEL_API_BASE_URL .* must not be set in production"):
        load_settings()


def test_app_refuses_to_start_in_production_without_a_key() -> None:
    with pytest.raises(ValueError, match="CREDENTIALS_KEY must be set in production"):
        create_app(Settings(database_url=URL, app_env="production", secret_key=STRONG))  # type: ignore[arg-type]


def test_app_without_key_logs_that_credentials_are_disabled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = create_app(Settings(database_url=URL))  # type: ignore[arg-type]
    assert app.state.credentials_keyring is None
    assert "CREDENTIALS_KEY is not set" in capsys.readouterr().out
