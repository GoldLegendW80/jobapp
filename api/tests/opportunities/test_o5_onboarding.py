"""The first login wizard's routes (opportunities O5): its state, "Skip for now" and "Start".

Real Postgres; the queue is a fake (no worker runs a sweep here). Accounts are invented
(e2e-*@example.org), members unless a test says admin.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.identity.models import Role, User
from app.jobs.models import Sweep, SweepStatus
from app.opportunities import onboarding
from app.profile.models import Profile
from tests.identity.api_helpers import Api, Clock, make_user
from tests.jobs.sweep_helpers import board, search, set_profile
from tests.jobs.test_sweep_api import FakeQueue

BASE = "/api/opportunities/onboarding"
NEW_A = "e2e-welcome-a@example.org"
NEW_B = "e2e-welcome-b@example.org"


class Who:
    def __init__(self, token: str, user_id: uuid.UUID) -> None:
        self.token, self.user_id = token, user_id


def _signed(factory: sessionmaker[Session], api: Api, email: str, role: Role) -> Who:
    make_user(factory, email=email, name=email.split("@")[0], role=role)
    token = api.login_token(email)
    with factory() as db:
        user_id = db.execute(sa.select(User.id).where(User.email == email)).scalar_one()
    return Who(token, user_id)


@pytest.fixture
def queue(app: FastAPI) -> FakeQueue:
    found = FakeQueue()
    app.state.queue = found
    return found


@pytest.fixture
def new_a(factory: sessionmaker[Session], api: Api) -> Who:
    return _signed(factory, api, NEW_A, Role.member)


@pytest.fixture
def new_b(factory: sessionmaker[Session], api: Api) -> Who:
    return _signed(factory, api, NEW_B, Role.member)


def state(api: Api, who: Who) -> dict[str, Any]:
    response = api.get(BASE, token=who.token)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def stored_onboarding(factory: sessionmaker[Session], who: Who) -> dict[str, Any]:
    with factory() as db:
        sections = db.get(Profile, who.user_id).sections or {}
    return (sections.get("preferences") or {}).get("onboarding") or {}


# --- the state ------------------------------------------------------------------------------


def test_a_new_member_is_sent_to_the_wizard(api: Api, new_a: Who) -> None:
    found = state(api, new_a)
    assert found == {
        "completed_at": None,
        "dismissed_at": None,
        "profile_empty": True,
        "done": [],
        "steps_left": ["profile", "looking", "where", "work"],
        "open_wizard": True,
        "show_card": True,
    }


def test_an_admin_with_an_empty_profile_is_never_sent(
    factory: sessionmaker[Session], api: Api
) -> None:
    owner = _signed(factory, api, "e2e-welcome-owner@example.org", Role.admin)
    found = state(api, owner)
    assert found["profile_empty"] is True
    assert found["open_wizard"] is False


def test_a_filled_profile_is_never_sent_and_sees_no_card(
    factory: sessionmaker[Session], api: Api, new_a: Who
) -> None:
    set_profile(factory, new_a.user_id, search(contract_types=["CDI"], remote_stance="hybrid"))
    with factory() as db, db.begin():
        profile = db.get(Profile, new_a.user_id)
        profile.sections = {**profile.sections, "identity": {"name": "Example Person"}}
    found = state(api, new_a)
    assert found["profile_empty"] is False
    assert found["open_wizard"] is False
    assert found["done"] == ["profile", "looking", "where", "work"]
    assert found["steps_left"] == []
    assert found["show_card"] is False


def test_a_profile_with_steps_left_sees_the_card_but_no_redirect(
    factory: sessionmaker[Session], api: Api, new_a: Who
) -> None:
    set_profile(factory, new_a.user_id, search())  # roles and places, no stance, no contract
    found = state(api, new_a)
    assert found["open_wizard"] is False
    assert found["done"] == ["looking"]
    assert found["show_card"] is True


@pytest.mark.parametrize(
    ("sections", "done"),
    [
        ({}, []),
        ({"identity": None, "search": None}, []),
        ({"identity": {"name": "A"}}, ["profile"]),
        ({"experience": {"employers": []}}, ["profile"]),
        ({"search": {"title_include": {"terms": []}}}, []),
        ({"search": {"title_include": {"terms": ["engineer"]}}}, ["looking"]),
        ({"search": {"title_include": ["engineer"]}}, []),  # a damaged shape counts as unanswered
        ({"search": {"title_include": None}}, []),
        ({"search": {"remote_stance": "any"}}, ["where"]),
        ({"search": {"remote_stance": None}}, []),
        ({"search": {"contract_types": []}}, []),
        ({"search": {"contract_types": ["Freelance"]}}, ["work"]),
        ({"search": {"compensation": {"salary": None, "daily_rate": None}}}, []),
        (
            {"search": {"compensation": {"salary": {"min": 1, "max": 2, "currency": "EUR"}}}},
            ["pay"],
        ),
        (
            {"search": {"compensation": {"daily_rate": {"min": 1, "max": 2, "currency": "EUR"}}}},
            ["pay"],
        ),
        ({"search": {"compensation": "lots"}}, []),
        ({"preferences": {"documents_tone": "warm"}}, []),  # apply and summary are never done
    ],
)
def test_done_steps(sections: dict[str, Any], done: list[str]) -> None:
    assert onboarding.done_steps(sections) == done


# --- Skip for now ---------------------------------------------------------------------------


def test_dismiss_records_once_and_keeps_the_card(
    factory: sessionmaker[Session], api: Api, clock: Clock, new_a: Who
) -> None:
    response = api.post(f"{BASE}/dismiss", token=new_a.token)
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["dismissed_at"] is not None
    assert first["open_wizard"] is False
    assert first["show_card"] is True
    clock.advance(hours=1)
    again = api.post(f"{BASE}/dismiss", token=new_a.token).json()
    assert again["dismissed_at"] == first["dismissed_at"]
    # a later sign in: still not sent, the card still shows
    assert state(api, new_a)["open_wizard"] is False


def test_dismiss_after_finishing_changes_nothing(
    factory: sessionmaker[Session], api: Api, queue: FakeQueue, new_a: Who
) -> None:
    assert api.post(f"{BASE}/complete", token=new_a.token).status_code == 200
    found = api.post(f"{BASE}/dismiss", token=new_a.token).json()
    assert found["dismissed_at"] is None
    assert found["completed_at"] is not None
    assert found["show_card"] is False


def test_dismiss_is_one_revision_and_a_profile_save_keeps_it(
    factory: sessionmaker[Session], api: Api, new_a: Who
) -> None:
    api.post(f"{BASE}/dismiss", token=new_a.token)
    profile = api.get("/api/profile", token=new_a.token).json()
    assert profile["revision"] == 1
    prefs = profile["sections"]["preferences"]
    saved = api.request(
        "PUT",
        "/api/profile/preferences",
        token=new_a.token,
        json={"base_revision": 1, "data": {**prefs, "documents_tone": "warm"}},
    )
    assert saved.status_code == 200, saved.text
    assert stored_onboarding(factory, new_a)["dismissed_at"] == prefs["onboarding"]["dismissed_at"]


# --- Start ----------------------------------------------------------------------------------


def test_complete_records_and_starts_the_first_sweep(
    factory: sessionmaker[Session], api: Api, queue: FakeQueue, new_a: Who
) -> None:
    set_profile(factory, new_a.user_id, search(board("fake-ok")))
    response = api.post(f"{BASE}/complete", token=new_a.token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sweep"] == "started"
    assert body["state"]["completed_at"] is not None
    assert body["state"]["show_card"] is False
    assert [call[0] for call in queue.calls] == ["run_sweep"]
    assert queue.calls[0][1] == (body["sweep_id"],)
    with factory() as db:
        row = db.get(Sweep, uuid.UUID(body["sweep_id"]))
        assert row.user_id == new_a.user_id
        assert row.status is SweepStatus.running


def test_complete_while_a_sweep_runs_ends_the_wizard_with_409(
    factory: sessionmaker[Session], api: Api, queue: FakeQueue, new_a: Who
) -> None:
    set_profile(factory, new_a.user_id, search(board("fake-ok")))
    running = api.post("/api/jobs/sweeps", token=new_a.token)
    assert running.status_code == 202, running.text
    response = api.post(f"{BASE}/complete", token=new_a.token)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "sweep_running"
    assert body["sweep_id"] == running.json()["id"]
    assert body["state"]["completed_at"] is not None
    assert stored_onboarding(factory, new_a)["completed_at"] is not None
    assert len(queue.calls) == 1  # only the sweep started by hand


def test_complete_without_boards_still_finishes(
    factory: sessionmaker[Session], api: Api, queue: FakeQueue, new_a: Who
) -> None:
    response = api.post(f"{BASE}/complete", token=new_a.token)
    assert response.status_code == 200
    assert response.json()["sweep"] == "no_boards"
    assert response.json()["sweep_id"] is None
    assert queue.calls == []
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Sweep)) == 0


def test_complete_with_the_queue_down_fails_the_sweep_at_once(
    factory: sessionmaker[Session], api: Api, queue: FakeQueue, new_a: Who
) -> None:
    set_profile(factory, new_a.user_id, search(board("fake-ok")))
    queue.down = True
    body = api.post(f"{BASE}/complete", token=new_a.token).json()
    assert body["sweep"] == "queue_down"
    assert body["state"]["completed_at"] is not None
    with factory() as db:
        statuses = db.scalars(sa.select(Sweep.status).where(Sweep.user_id == new_a.user_id))
        assert list(statuses) == [SweepStatus.failed]


def test_complete_twice_keeps_the_first_date(
    api: Api, clock: Clock, queue: FakeQueue, new_a: Who
) -> None:
    first = api.post(f"{BASE}/complete", token=new_a.token).json()["state"]["completed_at"]
    clock.advance(days=1)
    second = api.post(f"{BASE}/complete", token=new_a.token).json()["state"]["completed_at"]
    assert first == second


def test_finish_and_dismiss_at_once_end_finished(
    factory: sessionmaker[Session], new_a: Who
) -> None:
    """Two sessions under the profile row lock: whichever comes second, the wizard is finished,
    and a dismissal that comes after the finish records nothing."""
    with factory() as db:
        user = db.get(User, new_a.user_id)
        db.expunge(user)
    now = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    barrier = threading.Barrier(2)

    def run(action: Any) -> None:
        with factory() as db:
            barrier.wait()
            action(db, user, now)
            db.commit()

    threads = [
        threading.Thread(target=run, args=(onboarding.finish,)),
        threading.Thread(target=run, args=(onboarding.dismiss,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with factory() as db:
        found = onboarding.state(db, user, now)
    assert found.completed_at is not None
    assert found.show_card is False
    assert found.open_wizard is False


# --- people and sessions --------------------------------------------------------------------


def test_b_never_sees_a_s_wizard(api: Api, queue: FakeQueue, new_a: Who, new_b: Who) -> None:
    api.post(f"{BASE}/complete", token=new_a.token)
    found = state(api, new_b)
    assert found["completed_at"] is None
    assert found["open_wizard"] is True
    api.post(f"{BASE}/dismiss", token=new_b.token)
    assert state(api, new_a)["dismissed_at"] is None


@pytest.mark.parametrize(
    ("method", "path"), [("GET", ""), ("POST", "/dismiss"), ("POST", "/complete")]
)
def test_every_route_needs_a_session(api: Api, method: str, path: str) -> None:
    assert api.request(method, f"{BASE}{path}").status_code == 401


@pytest.mark.parametrize("path", ["/dismiss", "/complete"])
def test_the_writes_need_the_csrf_header(api: Api, new_a: Who, path: str) -> None:
    response = api.post(f"{BASE}{path}", token=new_a.token, csrf=False)
    assert response.status_code == 403
    assert state(api, new_a)["dismissed_at"] is None
