"""Durable platform guard history in the existing sweep stats, no new table."""

from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from app.opportunities.platforms import guard
from tests.identity.api_helpers import Api
from tests.jobs.sweep_helpers import set_profile
from tests.opportunities.conftest import Person
from tests.opportunities.test_o7b_api import PATH, accept, entry, profile, put


def history(
    factory: sessionmaker[Session],
    who: Person,
    at: Any,
    *,
    kind: str = "freework",
    status: SweepStatus = SweepStatus.done,
    **metadata: Any,
) -> Any:
    with factory() as db, db.begin():
        row = Sweep(
            user_id=who.user_id,
            trigger=SweepTrigger.manual,
            status=status,
            started_at=at,
            finished_at=at + timedelta(minutes=1),
            stats={"sources": [{"kind": kind, "result_pages": 0, **metadata}]},
        )
        db.add(row)
        db.flush()
        return row.id


@pytest.mark.parametrize("status", [SweepStatus.done, SweepStatus.failed])
def test_checkpoint_survives_new_sessions_until_exactly_seven_days(
    api: Api,
    person_a: Person,
    factory: sessionmaker[Session],
    clock: Any,
    status: SweepStatus,
) -> None:
    accept(api, person_a)
    met = clock() - timedelta(days=1)
    history(
        factory,
        person_a,
        met,
        status=status,
        opened_at=met.isoformat(),
        checkpoint_at=met.isoformat(),
        error="freework_checkpoint",
    )
    for now in (clock(), met + timedelta(days=7, seconds=-1)):
        with factory() as db:
            state = guard.state(db, person_a.user_id, "freework", now)
            assert state.paused_until == met + timedelta(days=7)
            assert state.allowed is False
            assert guard.allowed(db, person_a.user_id, "freework", now) is False
    with factory() as db:
        assert guard.state(db, person_a.user_id, "freework", met + timedelta(days=7)).allowed
    response = api.get(PATH, token=person_a.token)
    assert response.status_code == 200, response.text
    assert datetime.fromisoformat(response.json()["paused_until"]) == met + timedelta(days=7)


def test_checkpoint_survives_off_reaccept_remove_readd_and_later_failed_sweep(
    api: Api,
    person_a: Person,
    factory: sessionmaker[Session],
    clock: Any,
) -> None:
    accept(api, person_a)
    met = clock() - timedelta(hours=1)
    history(
        factory,
        person_a,
        met,
        opened_at=met.isoformat(),
        checkpoint_at=met.isoformat(),
        error="freework_checkpoint",
    )
    assert put(api, person_a, enabled=False).status_code == 200
    assert accept(api, person_a)["allowed"] is False
    source = entry(api, person_a)
    search = profile(api, person_a)["sections"]["search"]
    set_profile(factory, person_a.user_id, {**search, "browser_sources": []})
    set_profile(factory, person_a.user_id, {**search, "browser_sources": [source]})
    history(factory, person_a, clock(), status=SweepStatus.failed, error="browser_busy")
    state = api.get(PATH, token=person_a.token).json()
    assert state["enabled"] is True
    assert state["allowed"] is False
    assert datetime.fromisoformat(state["paused_until"]) == met + timedelta(days=7)


def test_reservation_before_failed_navigation_is_kept_with_its_actual_open_time(
    api: Api,
    person_a: Person,
    factory: sessionmaker[Session],
    clock: Any,
) -> None:
    accept(api, person_a)
    opened = clock() - timedelta(minutes=2)
    history(
        factory,
        person_a,
        opened - timedelta(hours=1),
        status=SweepStatus.failed,
        opened_at=opened.isoformat(),
        error="page_failed",
    )
    state = api.get(PATH, token=person_a.token).json()
    assert datetime.fromisoformat(state["last_sweep_at"]) == opened
    assert state["needs_signin"] is False


def test_last_opened_attempt_determines_signin_not_a_newer_busy_sweep(
    api: Api,
    person_a: Person,
    factory: sessionmaker[Session],
    clock: Any,
) -> None:
    accept(api, person_a)
    opened = clock() - timedelta(hours=1)
    history(factory, person_a, opened, opened_at=opened.isoformat(), error="freework_signin")
    history(factory, person_a, clock(), error="browser_busy")
    state = api.get(PATH, token=person_a.token).json()
    assert datetime.fromisoformat(state["last_sweep_at"]) == opened
    assert state["needs_signin"] is True


def test_history_is_scoped_to_person_and_kind(
    api: Api,
    person_a: Person,
    person_b: Person,
    factory: sessionmaker[Session],
    clock: Any,
) -> None:
    accept(api, person_a)
    accept(api, person_b)
    met = clock() - timedelta(minutes=1)
    history(
        factory,
        person_a,
        met,
        opened_at=met.isoformat(),
        checkpoint_at=met.isoformat(),
        error="freework_checkpoint",
    )
    history(
        factory,
        person_b,
        met,
        kind="linkedin",
        opened_at=met.isoformat(),
        checkpoint_at=met.isoformat(),
        error="linkedin_checkpoint",
    )
    a = api.get(PATH, token=person_a.token).json()
    b = api.get(PATH, token=person_b.token).json()
    assert a["allowed"] is False
    assert (b["allowed"], b["paused_until"], b["last_sweep_at"], b["needs_signin"]) == (
        True,
        None,
        None,
        False,
    )


def test_new_text_version_stops_previously_accepted_person(
    api: Api,
    person_a: Person,
    factory: sessionmaker[Session],
    clock: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accept(api, person_a)
    monkeypatch.setattr(guard, "TEXT_VERSION", 2)
    with factory() as db:
        assert guard.allowed(db, person_a.user_id, "freework", clock()) is False
    stale = put(api, person_a, enabled=True, accept_version=1)
    assert stale.status_code == 409
    state = api.get(PATH, token=person_a.token).json()
    assert (state["enabled"], state["accepted"], state["allowed"], state["text_version"]) == (
        True,
        False,
        False,
        2,
    )


@pytest.mark.parametrize("kind", ["malt", "linkedin", "unknown"])
def test_unimplemented_kind_fails_closed_in_guard(
    api: Api,
    person_a: Person,
    factory: sessionmaker[Session],
    clock: Any,
    kind: str,
) -> None:
    accept(api, person_a)
    with factory() as db:
        assert guard.allowed(db, person_a.user_id, kind, clock()) is False


@pytest.mark.parametrize(
    ("opened", "now"),
    [
        ("2026-01-02T22:59:59+00:00", "2026-01-02T23:00:00+00:00"),
        ("2026-03-28T23:00:00+00:00", "2026-03-29T21:59:59+00:00"),
        ("2026-10-24T22:00:00+00:00", "2026-10-25T22:59:59+00:00"),
        ("2026-10-28T08:00:00+00:00", "2026-10-28T08:05:00+00:00"),
    ],
)
def test_a_sweep_that_opened_the_platform_today_refuses_no_other(
    api: Api,
    person_a: Person,
    person_b: Person,
    factory: sessionmaker[Session],
    opened: str,
    now: str,
) -> None:
    """Owner, 2026-10-28: no daily limit on any website. Consent and the 7 day pause are what
    refuse a sweep; a sweep of the same day that opened the platform does not."""
    accept(api, person_a)
    at, checked_at = datetime.fromisoformat(opened), datetime.fromisoformat(now)
    history(factory, person_a, at, opened_at=opened, error="page_failed")
    with factory() as db:
        assert guard.sweep_refusal(db, person_a.user_id, "freework", None, checked_at) is None
        assert guard.sweep_refusal(db, person_b.user_id, "freework", None, checked_at) == (
            "freework_off"
        )


def test_guard_state_does_not_return_or_log_unrecognized_snapshot_metadata(
    api: Api,
    person_a: Person,
    factory: sessionmaker[Session],
    clock: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import uuid

    accept(api, person_a)
    marker = f"o7b_{uuid.uuid4().hex}"
    history(
        factory, person_a, clock(), opened_at=clock().isoformat(), cookie=marker, snapshot=marker
    )
    response = api.get(PATH, token=person_a.token)
    assert response.status_code == 200, response.text
    assert marker not in response.text
    assert marker not in caplog.text
