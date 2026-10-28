"""Browser B10 over real Postgres: the LinkedIn guard (acceptance of the current sentence, the
switch, the 7 day pause of a checkpoint kept in Postgres, no daily limit), GET and PUT
/api/browser/linkedin, and person B never affected by A. LinkedIn's own fixed reader was deleted
(owner, 2026-10-28); LinkedIn is searched as a ticked job website (tests/browser)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.browser import linkedin_guard as guard
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from tests.identity.api_helpers import Api, make_user
from tests.jobs.sweep_helpers import search, set_profile

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
ACCEPTED = {"accepted_at": "2026-10-19T08:00:00+00:00", "text_version": guard.TEXT_VERSION}


def linkedin(**kw: Any) -> dict[str, Any]:
    return {"kind": "linkedin", "query": "AI Engineer", "enabled": True} | ACCEPTED | kw


def add_sweep(
    factory: sessionmaker[Session], user: uuid.UUID, entry: dict[str, Any], at: datetime
) -> uuid.UUID:
    with factory() as db, db.begin():
        row = Sweep(
            user_id=user,
            trigger=SweepTrigger.manual,
            status=SweepStatus.done,
            started_at=at,
            finished_at=at + timedelta(minutes=5),
            stats={"sources": [{"kind": "linkedin", "result_pages": 1} | entry]},
        )
        db.add(row)
        db.flush()
        return row.id


def refusal(factory: sessionmaker[Session], user: uuid.UUID, now: datetime = NOW) -> str | None:
    with factory() as db:
        return guard.refusal(db, user, now)


# --- the guard ----------------------------------------------------------------------------------


def test_not_accepted_switched_off_or_absent_refuses_every_path(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    assert refusal(factory, alice) == "linkedin_off"  # no profile at all
    for entry in (
        {"kind": "linkedin", "query": "AI Engineer", "enabled": True},  # never accepted
        linkedin(enabled=False),  # accepted, then switched off
    ):
        set_profile(factory, alice, search(browser_sources=[entry]))
        assert refusal(factory, alice) == "linkedin_off"
        with factory() as db:
            # What apply's Easy Apply (A16) and profile work (A18) call.
            assert guard.allowed(db, alice, NOW) is False
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    with factory() as db:
        assert guard.allowed(db, alice, NOW) is True


def test_a_new_version_of_the_sentence_stops_work_until_accepted(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    assert refusal(factory, alice) is None
    monkeypatch.setattr(guard, "TEXT_VERSION", guard.TEXT_VERSION + 1)
    assert refusal(factory, alice) == "linkedin_off"


def checkpoint(
    factory: sessionmaker[Session], user: uuid.UUID, at: datetime, origin: str = "sweep"
) -> None:
    with factory() as db:
        guard.record_checkpoint(db, user, at, origin)
        db.commit()


def test_a_checkpoint_pauses_7_days_kept_in_postgres(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    met = NOW - timedelta(days=1)
    checkpoint(factory, alice, met)
    # A new session on a new connection: what a restart of the api and the worker reads.
    assert refusal(factory, alice) == "linkedin_paused"
    assert refusal(factory, alice, met + timedelta(days=7, seconds=-1)) == "linkedin_paused"
    assert refusal(factory, alice, met + timedelta(days=7)) is None
    with factory() as db:
        assert guard.state(db, alice, NOW).paused_until == met + guard.PAUSE


@pytest.mark.parametrize("origin", ["sweep", "easy_apply", "profile"])
def test_record_checkpoint_is_the_one_way_for_every_path(
    factory: sessionmaker[Session], alice: uuid.UUID, origin: str
) -> None:
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    checkpoint(factory, alice, NOW, origin)
    assert refusal(factory, alice, NOW + timedelta(hours=1)) == "linkedin_paused"
    with factory() as db, pytest.raises(ValueError, match="origin"):
        guard.record_checkpoint(db, alice, NOW, "someone")
    # The sweep's own stats no longer start a pause: only the table does.
    add_sweep(factory, alice, {"error": "linkedin_checkpoint"}, NOW + timedelta(days=30))
    assert refusal(factory, alice, NOW + timedelta(days=31)) is None


def test_no_daily_limit(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    """Owner, 2026-10-28: no limit on searches a day, LinkedIn too. A sweep of the day that
    opened LinkedIn refuses no other; the switch and the 7 day pause still do."""
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    today = add_sweep(factory, alice, {"error": None}, NOW - timedelta(hours=2))
    with factory() as db:
        assert guard.sweep_refusal(db, alice, None, NOW) is None
        assert guard.sweep_refusal(db, alice, today, NOW) is None
        assert guard.sweep_refusal(db, alice, None, NOW + timedelta(days=1)) is None


def test_person_b_never_reads_a(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    add_sweep(factory, alice, {"error": "linkedin_signin"}, NOW - timedelta(hours=1))
    assert refusal(factory, bob) == "linkedin_off"
    with factory() as db:
        b = guard.state(db, bob, NOW)
        assert (b.enabled, b.accepted, b.needs_signin, b.last_sweep_at) == (
            False,
            False,
            False,
            None,
        )
        assert guard.state(db, alice, NOW).needs_signin is True
    # A's checkpoint never pauses B.
    set_profile(factory, bob, search(browser_sources=[linkedin()]))
    checkpoint(factory, alice, NOW - timedelta(hours=1))
    assert refusal(factory, alice) == "linkedin_paused"
    assert refusal(factory, bob) is None


# --- the routes ---------------------------------------------------------------------------------


@pytest.fixture
def people(factory: sessionmaker[Session], api: Api) -> tuple[str, str, uuid.UUID]:
    a = make_user(factory, email="e2e-b10-a@example.org")
    make_user(factory, email="e2e-b10-b@example.org")
    return api.login_token("e2e-b10-a@example.org"), api.login_token("e2e-b10-b@example.org"), a.id


def put(api: Api, token: str, **body: Any) -> Any:
    return api.request("PUT", "/api/browser/linkedin", token=token, json=body)


def test_the_switch_is_off_by_default_and_needs_the_sentence(
    api: Api, people: tuple[str, str, uuid.UUID], factory: sessionmaker[Session]
) -> None:
    a, b, a_id = people
    got = api.get("/api/browser/linkedin", token=a).json()
    assert (got["enabled"], got["accepted"], got["allowed"]) == (False, False, False)
    assert got["text"] == guard.TEXT
    assert not any(dash in got["text"] for dash in (" - ", chr(0x2014), chr(0x2013)))
    assert put(api, a, enabled=True, query="AI Engineer").status_code == 409
    stale = put(api, a, enabled=True, query="AI Engineer", accept_version=guard.TEXT_VERSION + 1)
    assert stale.status_code == 409
    assert put(api, a, enabled=True, accept_version=guard.TEXT_VERSION).status_code == 422
    on = put(api, a, enabled=True, query="AI Engineer", accept_version=guard.TEXT_VERSION)
    assert on.status_code == 200, on.text
    assert (on.json()["enabled"], on.json()["accepted"], on.json()["allowed"]) == (True, True, True)
    assert on.json()["accepted_version"] == guard.TEXT_VERSION
    off = put(api, a, enabled=False)
    assert (off.json()["enabled"], off.json()["accepted"]) == (False, True)
    assert put(api, a, enabled=True).json()["allowed"] is True  # the same version: no new click
    # B's switch is untouched by A's.
    got_b = api.get("/api/browser/linkedin", token=b).json()
    assert (got_b["enabled"], got_b["accepted"]) == (False, False)
    with factory() as db:
        assert guard.allowed(db, a_id) is True


def test_the_routes_need_a_session(api: Api) -> None:
    assert api.get("/api/browser/linkedin").status_code == 401
    assert api.request("PUT", "/api/browser/linkedin", json={"enabled": False}).status_code == 401
