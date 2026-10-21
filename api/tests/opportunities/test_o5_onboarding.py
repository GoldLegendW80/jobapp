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
        "passed": [],
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


# --- the steps passed, kept in the profile --------------------------------------------------


def step(api: Api, who: Who, name: Any, *, csrf: bool = True) -> Any:
    return api.post(f"{BASE}/steps", token=who.token, json={"step": name}, csrf=csrf)


def test_a_step_passed_is_kept_once_in_order_on_every_device(
    factory: sessionmaker[Session], api: Api, new_a: Who
) -> None:
    for name in ("where", "profile", "where"):
        response = step(api, new_a, name)
        assert response.status_code == 200, response.text
    assert response.json()["passed"] == ["profile", "where"]
    # another device: a new session reads the same steps from the profile
    other = Who(api.login_token(NEW_A), new_a.user_id)
    assert state(api, other)["passed"] == ["profile", "where"]
    assert stored_onboarding(factory, new_a)["steps_passed"] == ["profile", "where"]
    # a step passed is no revision of the profile: another tab is never sent a 409 by it
    assert api.get("/api/profile", token=new_a.token).json()["revision"] == 0
    history = api.get("/api/profile/revisions?section=preferences", token=new_a.token).json()
    assert history["items"] == []
    # a step does not end the wizard nor stop it opening by itself
    assert state(api, new_a)["open_wizard"] is True


@pytest.mark.parametrize("name", ["nowhere", "", None, 3, "Where"])
def test_an_unknown_step_is_422(api: Api, new_a: Who, name: Any) -> None:
    assert step(api, new_a, name).status_code == 422
    assert api.get("/api/profile", token=new_a.token).json()["revision"] == 0


def test_the_steps_survive_a_save_and_an_import_and_a_save_never_writes_them(
    factory: sessionmaker[Session], api: Api, new_a: Who
) -> None:
    step(api, new_a, "looking")
    loaded = api.get("/api/profile", token=new_a.token).json()
    prefs = loaded["sections"]["preferences"]
    assert prefs["onboarding"]["steps_passed"] == ["looking"]

    def put(data: dict[str, Any], revision: int) -> Any:
        return api.request(
            "PUT",
            "/api/profile/preferences",
            token=new_a.token,
            json={"base_revision": revision, "data": data},
        )

    # sent back as loaded: saved, the steps kept
    assert put({**prefs, "documents_tone": "warm"}, loaded["revision"]).status_code == 200
    # a save that adds or clears a step is saved, and the stored steps stay (a tab loaded
    # before the last step holds an older list: it must not be refused for it)
    for offset, steps in enumerate((["looking", "pay"], [])):
        changed = {**prefs, "onboarding": {**prefs["onboarding"], "steps_passed": steps}}
        saved = put(changed, loaded["revision"] + 1 + offset)
        assert saved.status_code == 200, saved.text
        assert saved.json()["sections"]["preferences"]["onboarding"]["steps_passed"] == ["looking"]
    assert stored_onboarding(factory, new_a)["steps_passed"] == ["looking"]
    # an import of a file with other steps keeps the stored ones
    exported = api.get("/api/profile/export", token=new_a.token).json()
    exported["sections"]["preferences"]["onboarding"]["steps_passed"] = ["apply", "summary"]
    imported = api.post("/api/profile/import", token=new_a.token, json=exported)
    assert imported.status_code == 200, imported.text
    assert stored_onboarding(factory, new_a)["steps_passed"] == ["looking"]


def test_a_second_tab_saves_after_the_wizard_passed_steps(
    factory: sessionmaker[Session], api: Api, new_a: Who
) -> None:
    """The other tab loaded the profile, then the wizard passed steps: its save goes through on
    the revision it loaded (no 409), and the steps stay."""
    loaded = api.get("/api/profile", token=new_a.token).json()
    for name in ("profile", "looking", "where"):
        step(api, new_a, name)
    prefs = loaded["sections"]["preferences"]
    saved = api.request(
        "PUT",
        "/api/profile/preferences",
        token=new_a.token,
        json={"base_revision": loaded["revision"], "data": {**prefs, "documents_tone": "warm"}},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["revision"] == loaded["revision"] + 1
    assert body["sections"]["preferences"]["documents_tone"] == "warm"
    assert body["sections"]["preferences"]["onboarding"]["steps_passed"] == [
        "profile",
        "looking",
        "where",
    ]
    # a stale revision is still a 409: the check stays honest for the person's own edits
    stale = api.request(
        "PUT",
        "/api/profile/preferences",
        token=new_a.token,
        json={"base_revision": loaded["revision"], "data": prefs},
    )
    assert stale.status_code == 409


@pytest.mark.parametrize(
    "sent",
    [
        {"completed_at": None, "dismissed_at": None},  # a page loaded before steps_passed
        {"dismissed_at": None},
        {},
        None,  # no onboarding at all
    ],
)
def test_a_save_without_an_onboarding_key_keeps_the_stored_value(
    factory: sessionmaker[Session], api: Api, new_a: Who, sent: dict[str, Any] | None
) -> None:
    api.post(f"{BASE}/dismiss", token=new_a.token)
    step(api, new_a, "where")
    loaded = api.get("/api/profile", token=new_a.token).json()
    before = loaded["sections"]["preferences"]["onboarding"]
    data = {k: v for k, v in loaded["sections"]["preferences"].items() if k != "onboarding"}
    if sent is not None:
        data["onboarding"] = {
            key: before[key] if before[key] is not None else value for key, value in sent.items()
        }
    saved = api.request(
        "PUT",
        "/api/profile/preferences",
        token=new_a.token,
        json={"base_revision": loaded["revision"], "data": {**data, "documents_tone": "formal"}},
    )
    assert saved.status_code == 200, saved.text
    assert stored_onboarding(factory, new_a) == before
    assert before["steps_passed"] == ["where"]
    assert before["dismissed_at"] is not None


def test_a_save_that_changes_a_date_is_still_refused(api: Api, new_a: Who) -> None:
    api.post(f"{BASE}/dismiss", token=new_a.token)
    loaded = api.get("/api/profile", token=new_a.token).json()
    prefs = loaded["sections"]["preferences"]
    for dates in ({"dismissed_at": None}, {"completed_at": "2026-10-20T08:00:00+00:00"}):
        refused = api.request(
            "PUT",
            "/api/profile/preferences",
            token=new_a.token,
            json={
                "base_revision": loaded["revision"],
                "data": {**prefs, "onboarding": dates},
            },
        )
        assert refused.status_code == 422, refused.text


def test_a_step_once_the_wizard_is_finished_records_nothing(
    factory: sessionmaker[Session], api: Api, queue: FakeQueue, new_a: Who
) -> None:
    step(api, new_a, "looking")
    assert api.post(f"{BASE}/complete", token=new_a.token).status_code == 200
    before = api.get("/api/profile", token=new_a.token).json()
    response = step(api, new_a, "pay")
    assert response.status_code == 200, response.text
    assert response.json()["passed"] == ["looking"]
    assert response.json()["completed_at"] is not None
    assert stored_onboarding(factory, new_a)["steps_passed"] == ["looking"]
    after = api.get("/api/profile", token=new_a.token).json()
    assert after["revision"] == before["revision"]
    assert after["updated_at"] == before["updated_at"]


def test_the_steps_are_never_shown_to_a_model(
    factory: sessionmaker[Session], api: Api, new_a: Who
) -> None:
    from app.agents import tools

    step(api, new_a, "pay")
    scope = tools.Scope(user_id=new_a.user_id, sessions=factory)
    shown = tools._section_payload(scope, "preferences")
    assert "steps_passed" not in shown
    assert "onboarding" not in shown


def test_b_never_sees_a_s_steps(api: Api, new_a: Who, new_b: Who) -> None:
    step(api, new_a, "where")
    assert state(api, new_b)["passed"] == []


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
    ("method", "path"),
    [("GET", ""), ("POST", "/dismiss"), ("POST", "/complete"), ("POST", "/steps")],
)
def test_every_route_needs_a_session(api: Api, method: str, path: str) -> None:
    assert api.request(method, f"{BASE}{path}").status_code == 401


@pytest.mark.parametrize("path", ["/dismiss", "/complete", "/steps"])
def test_the_writes_need_the_csrf_header(api: Api, new_a: Who, path: str) -> None:
    response = api.post(f"{BASE}{path}", token=new_a.token, json={"step": "pay"}, csrf=False)
    assert response.status_code == 403
    assert state(api, new_a)["dismissed_at"] is None
    assert state(api, new_a)["passed"] == []
