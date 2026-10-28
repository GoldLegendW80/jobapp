"""TEST_FAST_TIMERS: a short schedule tick for CI e2e, refused in production."""

import base64

import pytest
from pydantic import SecretStr, ValidationError

from app import worker
from app.config import Settings

URL = "postgresql+psycopg://jobapp:secret@db:5432/jobapp"
SECRET = "s" * 48
KEY = base64.b64encode(b"k" * 32).decode()


def _cron_minutes(settings: Settings | None) -> set[int]:
    name = worker.sweeps_queue().name
    sweeps = next(spec for spec in worker.queues(settings) if spec.name == name)
    cron = next(job for job in sweeps.cron_jobs if job.name == "cron:scheduled_sweeps")
    return set(cron.minute)  # type: ignore[arg-type]


def test_off_by_default_ticks_every_five_minutes() -> None:
    settings = Settings(database_url=URL)  # type: ignore[arg-type]
    assert settings.test_fast_timers is False
    assert _cron_minutes(settings) == set(range(0, 60, 5))
    assert _cron_minutes(None) == set(range(0, 60, 5))


def test_fast_timers_tick_every_minute() -> None:
    settings = Settings(database_url=URL, test_fast_timers=True)  # type: ignore[arg-type]
    assert _cron_minutes(settings) == set(range(60))


def test_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="TEST_FAST_TIMERS"):
        Settings(
            database_url=URL,  # type: ignore[arg-type]
            app_env="production",
            secret_key=SecretStr(SECRET),
            credentials_key=SecretStr(KEY),
            test_fast_timers=True,
        )
