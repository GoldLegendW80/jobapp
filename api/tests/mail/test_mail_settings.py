"""Every setting of the mail module (mail M1): its default, its range, and what production
refuses."""

from __future__ import annotations

import base64
import secrets
from pathlib import Path

import pytest

from app.config import ConfigError, Settings, load_settings
from tests.mail.conftest import MAIL_ENV, NO_DATABASE

PRODUCTION = {
    "APP_ENV": "production",
    "DATABASE_URL": NO_DATABASE,
    "SECRET_KEY": "k" * 48,
    # Made for this run: a constant here would look like a key to the secret scan.
    "CREDENTIALS_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
}


def _load(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.setenv("DATABASE_URL", NO_DATABASE)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return load_settings()


def test_the_defaults_are_the_plans(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _load(monkeypatch)
    assert settings.google_auth_url is None
    assert settings.google_token_base_url is None
    assert settings.google_api_base_url is None
    assert settings.google_oauth_client_id is None
    assert settings.google_oauth_client_secret is None
    assert settings.mail_auto_apply_confidence == 0.90
    assert settings.mail_send_daily_limit is None
    assert settings.mail_send_company_daily_limit is None
    assert settings.mail_auto_runs_per_day == 100
    assert settings.mail_storage_max_messages == 20_000
    assert settings.mail_storage_max_bytes == 300 * 1024 * 1024
    assert settings.mail_sends_keep_days == 365


@pytest.mark.parametrize(
    "name", ["GOOGLE_AUTH_URL", "GOOGLE_TOKEN_BASE_URL", "GOOGLE_API_BASE_URL"]
)
def test_the_three_settings_set_under_production_fail_the_start(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    assert _load(monkeypatch, **PRODUCTION).app_env == "production"
    # Refused whatever the value: a fake, and Google's own address too.
    for value in ("http://gmailstub:8098", "https://gmail.googleapis.com"):
        with pytest.raises(ConfigError, match=f"{name} points the mail module at a fake Google"):
            _load(monkeypatch, **PRODUCTION, **{name: value})
    # Empty is unset: the template's empty line starts production.
    assert _load(monkeypatch, **PRODUCTION, **{name: ""}).app_env == "production"


def test_the_three_settings_are_plain_http_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _load(
        monkeypatch,
        APP_ENV="test",
        GOOGLE_AUTH_URL="http://127.0.0.1:8098/o/oauth2/v2/auth",
        GOOGLE_TOKEN_BASE_URL="http://gmailstub:8098/",
        GOOGLE_API_BASE_URL=" http://gmailstub:8098 ",
    )
    assert settings.google_auth_url == "http://127.0.0.1:8098/o/oauth2/v2/auth"
    assert settings.google_token_base_url == "http://gmailstub:8098"
    assert settings.google_api_base_url == "http://gmailstub:8098"
    for bad in (
        "gmailstub:8098",
        "ftp://gmailstub",
        "http://user:pw@gmailstub",
        "http://gmailstub/?a=1",
    ):
        with pytest.raises(ConfigError, match="GOOGLE_API_BASE_URL"):
            _load(monkeypatch, GOOGLE_API_BASE_URL=bad)


@pytest.mark.parametrize(
    ("name", "inside", "outside"),
    [
        ("MAIL_AUTO_APPLY_CONFIDENCE", ["0.5", "0.9", "1"], ["0.49", "1.01", "-1", "high", "nan"]),
        ("MAIL_SEND_DAILY_LIMIT", ["1", "500", "2000"], ["0", "-5", "2001", "many"]),
        ("MAIL_SEND_COMPANY_DAILY_LIMIT", ["1", "2000"], ["0", "2001", "1.5"]),
        ("MAIL_AUTO_RUNS_PER_DAY", ["0", "100", "1000"], ["-1", "1001", "x"]),
        ("MAIL_STORAGE_MAX_MESSAGES", ["100", "20000", "200000"], ["99", "200001", "0"]),
        (
            "MAIL_STORAGE_MAX_BYTES",
            ["1048576", "314572800", "5368709120"],
            ["1048575", "5368709121", "0"],
        ),
        ("MAIL_SENDS_KEEP_DAYS", ["30", "365", "3650"], ["29", "3651", "0"]),
    ],
)
def test_each_setting_is_refused_when_it_is_out_of_its_range(
    monkeypatch: pytest.MonkeyPatch, name: str, inside: list[str], outside: list[str]
) -> None:
    for value in inside:
        _load(monkeypatch, **{name: value})
    for value in outside:
        with pytest.raises(ConfigError, match=name):
            _load(monkeypatch, **{name: value})


def test_an_empty_sending_limit_means_no_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _load(monkeypatch, MAIL_SEND_DAILY_LIMIT="", MAIL_SEND_COMPANY_DAILY_LIMIT="  ")
    assert settings.mail_send_daily_limit is None
    assert settings.mail_send_company_daily_limit is None


def test_the_oauth_client_is_given_whole_or_not_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    whole = _load(
        monkeypatch, GOOGLE_OAUTH_CLIENT_ID="id.apps.example", GOOGLE_OAUTH_CLIENT_SECRET="s" * 24
    )
    assert whole.google_oauth_client_id == "id.apps.example"
    assert "sssss" not in repr(whole)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    with pytest.raises(ConfigError, match="set together or not at all"):
        load_settings()
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", " ")
    assert load_settings().google_oauth_client_id is None


def test_env_example_names_every_setting_of_the_module_with_its_default() -> None:
    template = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
    lines = dict(
        line.split("=", 1)
        for line in template.splitlines()
        if "=" in line and not line.startswith("#")
    )
    for name in MAIL_ENV:
        assert name in lines, f"{name} is missing from .env.example"
    assert lines["MAIL_AUTO_APPLY_CONFIDENCE"] == "0.90"
    assert lines["MAIL_AUTO_RUNS_PER_DAY"] == "100"
    assert lines["MAIL_STORAGE_MAX_MESSAGES"] == "20000"
    assert lines["MAIL_STORAGE_MAX_BYTES"] == "314572800"
    assert lines["MAIL_SENDS_KEEP_DAYS"] == "365"
    for name in (
        "MAIL_SEND_DAILY_LIMIT",
        "MAIL_SEND_COMPANY_DAILY_LIMIT",
        "GOOGLE_AUTH_URL",
        "GOOGLE_TOKEN_BASE_URL",
        "GOOGLE_API_BASE_URL",
    ):
        assert lines[name] == "", f"{name} is empty in the template: none, and the real Google"
