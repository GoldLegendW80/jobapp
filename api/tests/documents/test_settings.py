"""The module's settings (D2): every variable in config.py and .env.example with its default,
and the person's writing rules."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.documents.settings import BANNED_PHRASES_MAX, DEFAULT, DocumentSettings

DEFAULTS = {
    "DOCUMENTS_RENDER_TIMEOUT_S": 30,
    "DOCUMENTS_RENDERS_AT_ONCE": 1,
    "DOCUMENTS_MAX_PDF_BYTES": 4 * 1024 * 1024,
    "DOCUMENTS_VERSIONS_KEPT": 30,
    "DOCUMENTS_GENERATION_MAX_TURNS": 10,
    "DOCUMENTS_GENERATION_WALL_S": 300,
    "DOCUMENTS_MAX_SUBMITS": 4,
    "DOCUMENTS_AUTO_AT_ONCE": 1,
    "DOCUMENTS_AUTO_LOOKBACK_DAYS": 30,
    "DOCUMENTS_AUTO_RETRY_AFTER_S": 600,
    "DOCUMENTS_MIN_FIT_DEFAULT": 70,
    "DOCUMENTS_DAILY_CEILING_DEFAULT": 100,
    "DOCUMENTS_BASE_CVS_MAX": 10,
    "DOCUMENTS_DESIGN_PASSES": 2,
    "DOCUMENTS_CHAT_MAX_CHARS": 2000,
    "DOCUMENTS_MESSAGES_KEPT": 200,
}
URL = "postgresql+psycopg://jobapp:secret@db:5432/jobapp"


def test_every_setting_has_its_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in DEFAULTS:
        monkeypatch.delenv(name, raising=False)
    settings = Settings(database_url=URL)  # type: ignore[arg-type]
    for name, value in DEFAULTS.items():
        assert getattr(settings, name.lower()) == value, name


def test_env_example_names_every_setting_with_its_default() -> None:
    template = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
    lines = dict(
        line.split("=", 1)
        for line in template.splitlines()
        if "=" in line and not line.startswith("#")
    )
    for name, value in DEFAULTS.items():
        assert lines.get(name) == str(value), name


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DOCUMENTS_MIN_FIT_DEFAULT", "101"),
        ("DOCUMENTS_DAILY_CEILING_DEFAULT", "0"),
        ("DOCUMENTS_DAILY_CEILING_DEFAULT", "501"),
        ("DOCUMENTS_MAX_SUBMITS", "1"),
    ],
)
def test_out_of_range_values_are_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(database_url=URL)  # type: ignore[arg-type]


def test_the_writing_rules_are_off_for_a_new_user() -> None:
    assert DEFAULT.no_dash_separators is False
    assert DEFAULT.banned_phrases == ()


def test_banned_phrases_are_cleaned_and_deduplicated() -> None:
    settings = DocumentSettings.model_validate(
        {"banned_phrases": ["  team   player ", "Team Player", "passionné", "PASSIONNE", " "]}
    )
    assert settings.banned_phrases == ("team player", "passionné")


def test_banned_phrases_are_capped() -> None:
    with pytest.raises(ValidationError):
        DocumentSettings.model_validate({"banned_phrases": ["p"] * (BANNED_PHRASES_MAX + 1)})
    with pytest.raises(ValidationError):
        DocumentSettings.model_validate({"banned_phrases": ["x" * 101]})
    with pytest.raises(ValidationError):
        DocumentSettings.model_validate({"no_dash_separators": "yes"})
