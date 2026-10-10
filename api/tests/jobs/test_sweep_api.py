"""/api/jobs/sweeps through the app: start (202, 409, 422, 503, stale sweeps), list, detail with
the jobs a sweep added, the server-sent event stream, isolation, CSRF and caching headers. The
queue is a recording fake here; tests/test_worker.py runs the real one on Redis."""

import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.identity.deps import COOKIE_NAME
from app.jobs import sweep
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from tests.identity.api_helpers import Api
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import board, fake_ats, now, run, search, set_profile
from tests.jobs.test_api import Signed, sign_in

__all__ = ["fake_ats"]


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], str, str]] = []
        self.down = False

    async def enqueue(self, function: str, *args: Any, queue: str, job_id: str) -> bool:
        if self.down:
            raise queues.QueueUnavailableError("down")
        self.calls.append((function, args, queue, job_id))
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_queue(app: FastAPI) -> FakeQueue:
    found = FakeQueue()
    app.state.queue = found
    app.state.sse_settings = {"interval_s": 0.05, "heartbeat_s": 0.3, "max_s": 10}
    return found


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "alice@example.org")


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "bob@example.org")


def start(api: Api, who: Signed, **kwargs: Any) -> httpx.Response:
    return api.request("POST", "/api/jobs/sweeps", token=who.token, **kwargs)


def rows(factory: sessionmaker[Session], user_id: uuid.UUID) -> list[Sweep]:
    with factory() as db:
        return list(
            db.execute(
                sa.select(Sweep).where(Sweep.user_id == user_id).order_by(Sweep.started_at)
            ).scalars()
        )


# --- start --------------------------------------------------------------------------------------


def test_start_queues_one_sweep_and_refuses_a_second(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search(board("fake-ok")))
    first = start(api, alice)
    assert first.status_code == 202, first.text
    assert first.headers["cache-control"] == "no-store"
    body = first.json()
    assert body["status"] == "running"
    assert body["trigger"] == "manual"
    assert body["finished_at"] is None
    assert body["boards_done"] == 0
    assert fake_queue.calls == [("run_sweep", (body["id"],), queues.SWEEPS, f"sweep:{body['id']}")]
    second = start(api, alice)
    assert second.status_code == 409
    assert second.json() == {"detail": "A sweep is already running.", "sweep_id": body["id"]}
    assert len(fake_queue.calls) == 1
    assert len(rows(factory, alice.id)) == 1


def test_start_without_an_enabled_board_is_422_and_stores_nothing(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    response = start(api, alice)
    assert response.status_code == 422
    assert response.json()["detail"] == sweep.NO_BOARDS
    set_profile(factory, alice.id, search({**board("fake-ok"), "enabled": False}))
    assert start(api, alice).status_code == 422
    assert rows(factory, alice.id) == []
    assert fake_queue.calls == []


def test_start_with_a_search_section_that_no_longer_validates_asks_to_save_it_again(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    """Issue #11: a distinct 422, not "No board is enabled", and nothing stored or queued."""
    broken = {"terms": ["(?P<x>agent)", "(?P<x>llm)"], "advanced_regex": True}
    set_profile(factory, alice.id, search(board("fake-ok"), title_exclude=broken))
    response = start(api, alice)
    assert response.status_code == 422
    assert response.json()["detail"] == sweep.SEARCH_INVALID
    assert rows(factory, alice.id) == []
    assert fake_queue.calls == []


def test_start_with_the_queue_down_fails_the_sweep_at_once(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search(board("fake-ok")))
    fake_queue.down = True
    response = start(api, alice)
    assert response.status_code == 503
    assert response.json()["detail"] == sweep.QUEUE_DOWN
    [row] = rows(factory, alice.id)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.QUEUE_DOWN)
    fake_queue.down = False
    assert start(api, alice).status_code == 202  # not blocked by the failed one


def test_a_stale_running_sweep_does_not_block(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search(board("fake-ok")))
    with factory() as db:
        db.add(
            Sweep(
                user_id=alice.id,
                trigger=SweepTrigger.schedule,
                started_at=now() - timedelta(hours=1),
            )
        )
        db.commit()
    response = start(api, alice)
    assert response.status_code == 202
    old, new = rows(factory, alice.id)
    assert (old.status, old.error) == (SweepStatus.failed, sweep.NOT_PICKED)
    assert new.status is SweepStatus.running


def test_start_needs_a_session_and_the_csrf_header(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search(board("fake-ok")))
    assert api.request("POST", "/api/jobs/sweeps").status_code == 401
    assert start(api, alice, csrf=False).status_code == 403
    assert fake_queue.calls == []
    for path in ("/api/jobs/sweeps", f"/api/jobs/sweeps/{uuid.uuid4()}"):
        assert api.request("GET", path).status_code == 401
    assert api.request("GET", f"/api/jobs/sweeps/{uuid.uuid4()}/events").status_code == 401


# --- read ---------------------------------------------------------------------------------------


def test_list_detail_and_isolation(
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    bob: Signed,
    fake_queue: FakeQueue,
    fake_ats: FakeAts,
) -> None:
    set_profile(factory, alice.id, search(board("fake-ok"), board("fake-404", "lever", "Gone")))
    started = start(api, alice).json()
    run(factory, uuid.UUID(started["id"]), fake_ats)

    listed = api.request("GET", "/api/jobs/sweeps", token=alice.token)
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    body = listed.json()
    assert body["schedule"] == {
        "enabled": False,
        "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
        "times": ["06:17"],
        "timezone": "Europe/Paris",
        "active": False,
        "next_run": None,
    }
    [item] = body["items"]
    assert item["id"] == started["id"]
    assert item["status"] == "done"
    assert (item["boards_total"], item["boards_ok"], item["new"], item["seen"]) == (2, 1, 2, 3)
    assert item["boards_failed"] == [
        {
            "company": "Gone",
            "ats": "lever",
            "org": "fake-404",
            "error": "not_found",
            "message": "board not found",
        }
    ]
    assert item["current_board"] is None
    assert item["duration"] is not None

    detail = api.request("GET", f"/api/jobs/sweeps/{started['id']}", token=alice.token).json()
    assert [j["title"] for j in detail["new_jobs"]] == [
        "AI Engineer",
        "Applied AI Engineer, Agents",
    ] or [j["title"] for j in detail["new_jobs"]] == [
        "Applied AI Engineer, Agents",
        "AI Engineer",
    ]
    fits = [j["fit"] for j in detail["new_jobs"]]
    assert fits == sorted(fits, reverse=True)
    assert all(j["status"] == "found" for j in detail["new_jobs"])

    # Bob sees none of it.
    assert api.request("GET", "/api/jobs/sweeps", token=bob.token).json()["items"] == []
    for path in (f"/api/jobs/sweeps/{started['id']}", f"/api/jobs/sweeps/{started['id']}/events"):
        assert api.request("GET", path, token=bob.token).status_code == 404
    assert api.request("GET", "/api/jobs/sweeps/not-a-uuid", token=alice.token).status_code == 404
    assert (
        api.request("GET", "/api/jobs/sweeps/not-a-uuid/events", token=alice.token).status_code
        == 404
    )
    # Bob has no profile: his schedule reads as the default, off.
    assert api.request("GET", "/api/jobs/sweeps", token=bob.token).json()["schedule"] == {
        "enabled": False,
        "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
        "times": ["06:17"],
        "timezone": "Europe/Paris",
        "active": False,
        "next_run": None,
    }


def test_list_limit_and_order(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search(board("fake-ok")))
    base = now()
    with factory() as db:
        for n in range(25):
            at = base - timedelta(minutes=n)
            db.add(
                Sweep(
                    user_id=alice.id,
                    trigger=SweepTrigger.manual,
                    started_at=at,
                    finished_at=at,
                    status=SweepStatus.done,
                    stats={"new": n, "boards_failed": ["junk", {"company": "X"}], "duration": "x"},
                )
            )
        db.commit()
    body = api.request("GET", "/api/jobs/sweeps", token=alice.token).json()
    assert [i["new"] for i in body["items"]] == list(range(20))
    assert body["schedule"]["enabled"] is False
    assert body["items"][0]["boards_failed"] == [
        {"company": "X", "ats": "", "org": "", "error": "", "message": ""}
    ]
    assert body["items"][0]["duration"] is None
    assert (
        len(api.request("GET", "/api/jobs/sweeps?limit=100", token=alice.token).json()["items"])
        == 25
    )
    for bad in ("0", "101", "x"):
        assert (
            api.request("GET", f"/api/jobs/sweeps?limit={bad}", token=alice.token).status_code
            == 422
        )


def test_sweeps_route_is_not_read_as_a_job_id(
    api: Api, alice: Signed, fake_queue: FakeQueue
) -> None:
    assert api.request("GET", "/api/jobs/sweeps", token=alice.token).status_code == 200
    assert api.request("GET", f"/api/jobs/{uuid.uuid4()}", token=alice.token).status_code == 404


# --- event stream -------------------------------------------------------------------------------


def events(api: Api, who: Signed, sweep_id: str) -> tuple[httpx.Response, list[tuple[str, dict]]]:
    found: list[tuple[str, dict]] = []
    headers = {"Cookie": f"{COOKIE_NAME}={who.token}"}
    api.client.cookies.clear()
    with api.client.stream("GET", f"/api/jobs/sweeps/{sweep_id}/events", headers=headers) as r:
        name = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: ") and name:
                found.append((name, json.loads(line.removeprefix("data: "))))
        return r, found


def test_stream_of_a_finished_sweep_is_one_done_event(
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    fake_queue: FakeQueue,
    fake_ats: FakeAts,
) -> None:
    set_profile(factory, alice.id, search(board("fake-ok")))
    started = start(api, alice).json()
    run(factory, uuid.UUID(started["id"]), fake_ats)
    response, found = events(api, alice, started["id"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store, no-transform"
    assert [name for name, _ in found] == ["done"]
    assert found[0][1]["status"] == "done"
    assert len(found[0][1]["new_jobs"]) == 2


def test_stream_follows_a_running_sweep_to_its_end(
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    fake_queue: FakeQueue,
    fake_ats: FakeAts,
) -> None:
    set_profile(factory, alice.id, search(board("fake-ok"), board("fake-ok2", "lever")))
    started = start(api, alice).json()

    def later() -> None:
        time.sleep(0.4)
        run(factory, uuid.UUID(started["id"]), fake_ats)

    worker = threading.Thread(target=later)
    worker.start()
    try:
        _, found = events(api, alice, started["id"])
    finally:
        worker.join()
    names = [name for name, _ in found]
    assert names[0] == "progress"
    assert names[-1] == "done"
    assert names.count("done") == 1
    assert found[0][1]["status"] == "running"
    assert found[-1][1]["new"] == 3
    done_counts = [data["boards_done"] for name, data in found if name == "progress"]
    assert done_counts == sorted(done_counts)


def test_stream_ends_with_gone_when_the_sweep_disappears(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search(board("fake-ok")))
    started = start(api, alice).json()

    def remove() -> None:
        time.sleep(0.3)
        with factory() as db:
            db.execute(sa.delete(Sweep).where(Sweep.id == uuid.UUID(started["id"])))
            db.commit()

    remover = threading.Thread(target=remove)
    remover.start()
    try:
        _, found = events(api, alice, started["id"])
    finally:
        remover.join()
    assert [name for name, _ in found][-1] == "gone"


def test_the_schedule_is_set_through_the_profile_and_shows_its_next_run(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue, clock: Any
) -> None:
    from app.jobs import schedule
    from app.profile.schemas import SweepSchedule

    set_profile(factory, alice.id, search(board("fake-ok")))
    profile = api.request("GET", "/api/profile", token=alice.token).json()
    section = profile["sections"]["search"]
    wanted = {
        "enabled": True,
        "days": ["sunday", "monday"],
        "times": ["18:30", "07:00"],
        "timezone": "America/New_York",
    }
    saved = api.request(
        "PUT",
        "/api/profile/search",
        token=alice.token,
        json={"base_revision": profile["revision"], "data": {**section, "sweep_schedule": wanted}},
    )
    assert saved.status_code == 200, saved.text
    stored = saved.json()["sections"]["search"]["sweep_schedule"]
    assert stored == {**wanted, "days": ["monday", "sunday"], "times": ["07:00", "18:30"]}

    body = api.request("GET", "/api/jobs/sweeps", token=alice.token).json()["schedule"]
    expected = schedule.next_run(SweepSchedule.model_validate(stored), clock.now)
    assert expected is not None
    assert body["active"] is True
    assert datetime.fromisoformat(body["next_run"]) == expected

    # No enabled board: the schedule is kept but nothing runs.
    no_boards = {**stored, "enabled": True}
    set_profile(
        factory, alice.id, search({**board("fake-ok"), "enabled": False}, sweep_schedule=no_boards)
    )
    body = api.request("GET", "/api/jobs/sweeps", token=alice.token).json()["schedule"]
    assert (body["enabled"], body["active"], body["next_run"]) == (True, False, None)


@pytest.mark.parametrize(
    "bad",
    [
        {"timezone": "Mars/Olympus"},
        {"timezone": "CET+1"},
        {"times": []},
        {"times": ["7:00"]},
        {"times": ["24:00"]},
        {"times": ["01:00", "02:00", "03:00", "04:00", "05:00"]},
        {"days": []},
        {"days": ["mon"]},
        {"enabled": "yes"},
        {"cron": "* * * * *"},
    ],
)
def test_an_invalid_schedule_is_a_422(
    api: Api, factory: sessionmaker[Session], alice: Signed, bad: dict[str, Any]
) -> None:
    set_profile(factory, alice.id, search(board("fake-ok")))
    profile = api.request("GET", "/api/profile", token=alice.token).json()
    section = profile["sections"]["search"]
    response = api.request(
        "PUT",
        "/api/profile/search",
        token=alice.token,
        json={"base_revision": profile["revision"], "data": {**section, "sweep_schedule": bad}},
    )
    assert response.status_code == 422, response.text
    assert "sweep_schedule" in response.json()["detail"]
