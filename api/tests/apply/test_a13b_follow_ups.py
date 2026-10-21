"""Follow ups of A13b (issue #522): the floor of Full auto's hold is 15 minutes in every
configuration a server can start with (owner, docs/decisions.md), a shorter one exists only on a
test stack of the fake forms server, and Cancel stops a Full auto item the app is still deciding
on, before any click."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.apply import caps, full_auto
from app.apply.models import State
from app.config import ConfigError, Settings, get_settings, load_settings
from app.main import create_app
from tests.apply.a6_support import FakeQueue
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application
from tests.apply.test_a6_gate import SECRET_KEY
from tests.apply.test_a13_full_auto import (
    HOLD,
    api,
    authorisations,
    filled,
    job,
    stub,
    turn_on,
)
from tests.conftest import make_settings
from tests.identity.api_helpers import Api

__all__ = ["api", "stub"]

URL = "postgresql+psycopg://x:y@db/z"
FORMS = "http://atsforms:8080"
FLOOR = 900
ROOT = Path(__file__).resolve().parents[3]


def settings(**values: object) -> Settings:
    return Settings(database_url=URL, **values)  # type: ignore[arg-type]


# --- APPLY_HOLD_S ------------------------------------------------------------------------------


@pytest.mark.parametrize("short", [899, 600, 60, 59, 1, 0, -1])
def test_apply_hold_s_refuses_less_than_fifteen_minutes(short: int) -> None:
    with pytest.raises(ValidationError, match="apply_hold_s"):
        settings(apply_hold_s=short)


@pytest.mark.parametrize("hold", [900, 901, 1800, 86_400])
def test_apply_hold_s_takes_fifteen_minutes_and_more(hold: int) -> None:
    assert settings(apply_hold_s=hold).apply_hold_floor_s == hold


def test_a_short_hold_in_the_environment_stops_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("APPLY_HOLD_S", "60")
    with pytest.raises(ConfigError, match="APPLY_HOLD_S"):
        load_settings()


# --- TEST_APPLY_HOLD_S, the test stacks' floor ---------------------------------------------------


def test_the_test_hold_is_the_floor_of_a_stack_of_the_fake_forms_server() -> None:
    for env in ("development", "test"):
        found = settings(app_env=env, apply_stub_base_url=FORMS, test_apply_hold_s=60)
        assert (found.apply_hold_s, found.apply_hold_floor_s) == (FLOOR, 60)


def test_the_test_hold_is_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="TEST_APPLY_HOLD_S"):
        settings(app_env="production", secret_key="s" * 40, test_apply_hold_s=60)


def test_the_test_hold_is_refused_without_the_fake_forms_server() -> None:
    for env in ("development", "test"):
        with pytest.raises(ValidationError, match=r"TEST_APPLY_HOLD_S .*APPLY_STUB_BASE_URL"):
            settings(app_env=env, test_apply_hold_s=60)


@pytest.mark.parametrize("value", [59, 0, -1, 900, 1800])
def test_the_test_hold_stays_between_one_and_fifteen_minutes(value: int) -> None:
    with pytest.raises(ValidationError, match="test_apply_hold_s"):
        settings(apply_stub_base_url=FORMS, test_apply_hold_s=value)


@pytest.mark.parametrize("hold", [None, 900, 1800])
@pytest.mark.parametrize("test_hold", [None, 60, 899])
@pytest.mark.parametrize("forms", [None, FORMS])
def test_every_production_configuration_that_starts_holds_fifteen_minutes_at_least(
    hold: int | None, test_hold: int | None, forms: str | None
) -> None:
    values: dict[str, object] = {"app_env": "production", "secret_key": "s" * 40}
    values |= {"apply_hold_s": hold} if hold is not None else {}
    values |= {"test_apply_hold_s": test_hold, "apply_stub_base_url": forms}
    try:
        found = settings(**values)
    except ValidationError:
        return
    assert found.apply_hold_floor_s >= FLOOR
    assert found.test_apply_hold_s is None


def test_the_floor_falls_back_to_fifteen_minutes_when_the_settings_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "s" * 40)
    monkeypatch.setenv("TEST_APPLY_HOLD_S", "60")
    get_settings.cache_clear()
    assert caps.hold_floor_s() == FLOOR


# Any assignment of APPLY_HOLD_S (not TEST_APPLY_HOLD_S) in a file that configures a stack.
ASSIGNED = re.compile(r"(?<![A-Z_])APPLY_HOLD_S[\"']?\s*[:=]\s*[\"']?(-?\d+)")
STACK_FILES = (
    "compose*.yml",
    "tests/*.compose.yml",
    ".env.example",
    "ops/*.sh",
    "deploy.sh",
    ".github/workflows/*.yml",
    "web/e2e/*.ts",
)


def stack_files() -> list[Path]:
    found = sorted({path for pattern in STACK_FILES for path in ROOT.glob(pattern)})
    assert any(path.name == "compose.yml" for path in found)
    return found


def test_no_stack_file_sets_a_hold_under_fifteen_minutes() -> None:
    values = {
        (path.relative_to(ROOT).as_posix(), int(value))
        for path in stack_files()
        for value in ASSIGNED.findall(path.read_text(encoding="utf-8"))
    }
    assert ("compose.yml", 60) not in values
    assert [one for one in values if one[1] < FLOOR] == []


def test_the_test_hold_is_set_only_by_the_test_stacks() -> None:
    setting = [
        path.relative_to(ROOT).as_posix()
        for path in stack_files()
        if "TEST_APPLY_HOLD_S" in path.read_text(encoding="utf-8")
    ]
    assert setting == ["tests/ats-forms-stub.compose.yml"]


def test_assignments_are_found_in_every_form_a_stack_file_writes() -> None:
    for text, value in (
        ('APPLY_HOLD_S: "60"', "60"),
        ("APPLY_HOLD_S: 60", "60"),
        ("APPLY_HOLD_S=60", "60"),
        ("- APPLY_HOLD_S=899", "899"),
        ("'APPLY_HOLD_S': '1'", "1"),
    ):
        assert ASSIGNED.findall(text) == [value], text
    assert ASSIGNED.findall('TEST_APPLY_HOLD_S: "60"') == []


# --- the api on a test stack ---------------------------------------------------------------------


@pytest.fixture
def test_stack(
    api: Api, migrated_url: str, stub: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Api]:
    """The api of a stack of the fake forms server with TEST_APPLY_HOLD_S 60 (`api` set the
    passwords); the worker's side reads the same through `get_settings`."""
    monkeypatch.setenv("DATABASE_URL", migrated_url)
    monkeypatch.setenv("APPLY_STUB_BASE_URL", stub)
    monkeypatch.setenv("TEST_APPLY_HOLD_S", "60")
    get_settings.cache_clear()
    built = make_settings(
        migrated_url, secret_key=SECRET_KEY, apply_stub_base_url=stub, test_apply_hold_s=60
    )
    app = create_app(built, clock=lambda: AT)
    app.state.queue = FakeQueue()
    with TestClient(app, follow_redirects=False) as client:
        yield Api(client)


def test_on_a_test_stack_the_settings_name_its_floor_and_take_it(
    test_stack: Api, db: Session, person_a: Person
) -> None:
    token = test_stack.login_token(person_a.email)
    shown = test_stack.get("/api/apply/settings", token=token).json()
    assert (shown["hold_s"], shown["hold_min_s"]) == (60, 60)
    assert full_auto.hold_s(caps.settings_of(db, person_a.user_id)) == 60
    short = test_stack.request("PUT", "/api/apply/settings", token=token, json={"hold_s": 59})
    assert (short.status_code, short.json()["code"]) == (422, "hold_too_short")
    floor = test_stack.request("PUT", "/api/apply/settings", token=token, json={"hold_s": 60})
    assert (floor.status_code, floor.json()["hold_s"]) == (200, 60)


# --- Cancel while the app decides ----------------------------------------------------------------


def test_cancel_of_an_item_the_app_still_decides_on_stops_it_before_any_click(
    api: Api,
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    stub: str,
) -> None:
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    assert (application(db, app_id).state, application(db, app_id).mode.value) == (
        State.ready,
        "full_auto",
    )
    other = api.post(f"/api/applications/{app_id}/cancel", token=api.login_token(person_b.email))
    assert other.status_code == 404
    cancelled = api.post(
        f"/api/applications/{app_id}/cancel", token=api.login_token(person_a.email)
    )
    assert (cancelled.status_code, cancelled.json()["state"]) == (200, "cancelled")
    _, decided = job(factory, stub, app_id, AT)
    _, after_hold = job(factory, stub, app_id, AT + HOLD)
    assert (decided.gate_clicks, after_hold.gate_clicks) == ([], [])
    db.expire_all()
    assert application(db, app_id).state is State.cancelled
    assert authorisations(db, app_id) == []
