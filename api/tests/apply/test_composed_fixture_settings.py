"""The fake ATS overrides form valid settings for both API and worker."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.config import Settings

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("service", ["api", "worker"])
def test_fake_forms_stack_has_all_required_timer_guards(
    service: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@db/z")
    for name in ("ats-stub.compose.yml", "ats-forms-stub.compose.yml"):
        stack = yaml.safe_load((ROOT / "tests" / name).read_text(encoding="utf-8"))
        for key, value in stack["services"][service].get("environment", {}).items():
            monkeypatch.setenv(key, value)

    settings = Settings()
    assert settings.test_fast_timers is True
    assert settings.apply_stub_base_url == "http://atsforms:8080"
    assert settings.test_apply_linkedin_min_interval_s == 60
    assert settings.apply_linkedin_min_interval_s == 600
    assert settings.apply_linkedin_daily_max == 15
    assert settings.apply_hold_s == 900
    assert settings.apply_hold_floor_s == 60

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValidationError, match="TEST_APPLY_LINKEDIN_MIN_INTERVAL_S"):
        Settings()
