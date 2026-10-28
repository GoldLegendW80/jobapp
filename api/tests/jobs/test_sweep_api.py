"""/api/jobs/sweeps through the app: start (202, 409, 422, 503, stale sweeps), list, detail with
the jobs a sweep added, a row written before the boards were deleted, the server-sent event
stream, isolation, CSRF and caching headers. The queue is a recording fake here;
tests/test_worker.py runs the real one on Redis. The sweeps run a fake of the website Runner
(tests/jobs/sweep_helpers.py)."""

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
from tests.jobs.sweep_helpers import (
    SITE,
    SITE_2,
    each_website,
    finding,
    new_sweep,
    now,
    run,
    search,
    set_profile,
)
from tests.jobs.test_api import Signed, sign_in


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


def engine_start(factory: sessionmaker[Session], who: Signed) -> dict[str, str]:
    """A sweep of the ticked websites, through the engine's internal start (the worker's)."""
    return {"id": str(new_sweep(factory, who.id))}


FOUND = (
    finding("AI Engineer", company="Acme AI"),
    finding("Applied AI Engineer, Agents", company="Acme AI", location="London, United Kingdom"),
)


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
    set_profile(factory, alice.id, search())
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


def test_start_without_an_enabled_website_is_422_and_stores_nothing(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    response = start(api, alice)
    assert response.status_code == 422
    assert response.json()["detail"] == sweep.NO_WEBSITES
    # A website switched off is no search, nor a saved section without websites.
    set_profile(factory, alice.id, search(interactive_sites=[{**SITE, "enabled": False}]))
    assert start(api, alice).json()["detail"] == sweep.NO_WEBSITES
    set_profile(factory, alice.id, search(interactive_sites=[]))
    assert start(api, alice).status_code == 422
    assert rows(factory, alice.id) == []
    assert fake_queue.calls == []


def test_start_with_a_search_section_that_no_longer_validates_asks_to_save_it_again(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    """Issue #11: a distinct 422, not the websites message, and nothing stored or queued."""
    broken = {"terms": ["(?P<x>agent)", "(?P<x>llm)"], "advanced_regex": True}
    set_profile(factory, alice.id, search(title_exclude=broken))
    response = start(api, alice)
    assert response.status_code == 422
    assert response.json()["detail"] == sweep.SEARCH_INVALID
    assert rows(factory, alice.id) == []
    assert fake_queue.calls == []


def test_start_with_the_queue_down_fails_the_sweep_at_once(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
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
    set_profile(factory, alice.id, search())
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
    set_profile(factory, alice.id, search())
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
) -> None:
    set_profile(factory, alice.id, search())
    started = engine_start(factory, alice)
    run(factory, uuid.UUID(started["id"]), *FOUND)

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
    assert (item["boards_total"], item["boards_ok"], item["new"], item["seen"]) == (0, 0, 2, 2)
    assert item["boards_failed"] == []
    [source] = item["sources"]
    assert (source["host"], source["outcome"], source["found"], source["new"]) == (
        "jobs.example-robotics.test", "complete", 2, 2,
    )  # fmt: skip
    assert item["search_scope"]["websites_only"] is True
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
    set_profile(factory, alice.id, search())
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


# A sweep row written before the boards, the feeds and the fixed readers were deleted (owner,
# 2026-10-28): board counters and failures, and `sources` entries of every deleted mode.
OLD_SOURCES = [
    {"source_id": "s-board", "mode": "api", "kind": "greenhouse", "query": "acme-ai",
     "phase": "done", "outcome": "complete", "reason": None, "found": 3, "postings": 2},
    {"source_id": "s-remoteok", "mode": "feed", "kind": "remoteok", "phase": "done",
     "outcome": "failed", "reason": "feed_unavailable", "error": "feed_unavailable",
     "items_skipped": 2},
    {"source_id": "s-hn", "mode": "feed", "kind": "hn_freelance", "phase": "done",
     "outcome": "no_matches", "reason": None, "found": 0},
    {"source_id": "s-wwr", "mode": "feed", "kind": "weworkremotely", "phase": "done",
     "outcome": "partial", "reason": "feed_timeout", "found": 1, "postings": 1,
     "skipped_by_rule": 1},
    {"source_id": "s-vie", "mode": "fixed_reader", "kind": "vie", "query": "AI Engineer",
     "location": "France", "result_pages": 2, "found": 4, "postings": 1, "phase": "done",
     "outcome": "complete"},
    {"kind": "google_careers", "query": "AI Engineer", "result_pages": 1, "found": 2,
     "postings": 0, "error": "layout_changed"},  # a pre-v1 reader entry, without a slot
]  # fmt: skip
OLD_STATS = {
    "boards_total": 3,
    "boards_done": 3,
    "boards_ok": 1,
    "boards_failed": [
        {"company": "Gone AI", "ats": "lever", "org": "gone-ai", "error": "not_found",
         "message": "board not found"},
        {"company": "Slow AI", "ats": "ashby", "org": "slow-ai", "error": "timeout"},
    ],
    "current_board": "Slow AI",
    "seen": 40, "matched": 6, "new": 4, "duplicates": 2, "skipped_by_rule": 1, "duration": 12.5,
    "picked_at": "2026-10-20T06:17:02+00:00",
    "heartbeat_at": "2026-10-20T06:17:14+00:00",
    "sources": OLD_SOURCES,
    "search_scope": {
        "version": 1, "profile_revision": 7, "created_at": "2026-10-20T06:17:00+00:00",
        "trigger": "schedule", "search": {"days": 30, "boards": [{"ats": "greenhouse"}]},
        "skills": None, "sources": OLD_SOURCES[:5],
    },
}  # fmt: skip


def test_a_row_written_before_the_boards_were_deleted_reads_as_stored(
    api: Api, factory: sessionmaker[Session], alice: Signed, bob: Signed, fake_queue: FakeQueue
) -> None:
    at = now() - timedelta(days=8)
    with factory() as db:
        row = Sweep(
            user_id=alice.id,
            trigger=SweepTrigger.schedule,
            status=SweepStatus.done,
            started_at=at,
            finished_at=at + timedelta(seconds=13),
            stats=OLD_STATS,
        )
        db.add(row)
        db.commit()
        sweep_id = str(row.id)
    listed = api.request("GET", "/api/jobs/sweeps", token=alice.token)
    assert listed.status_code == 200, listed.text
    [item] = listed.json()["items"]
    detail = api.request("GET", f"/api/jobs/sweeps/{sweep_id}", token=alice.token)
    assert detail.status_code == 200, detail.text
    for body in (item, detail.json()):
        assert body["id"] == sweep_id
        assert (body["status"], body["trigger"]) == ("done", "schedule")
        assert (body["boards_total"], body["boards_done"], body["boards_ok"]) == (3, 3, 1)
        assert body["boards_failed"] == [
            {"company": "Gone AI", "ats": "lever", "org": "gone-ai", "error": "not_found",
             "message": "board not found"},
            {"company": "Slow AI", "ats": "ashby", "org": "slow-ai", "error": "timeout",
             "message": "timeout"},
        ]  # fmt: skip
        assert body["current_board"] is None  # a finished row names no board
        assert (body["seen"], body["new"], body["duplicates"], body["duration"]) == (
            40, 4, 2, 12.5,
        )  # fmt: skip
        sources = body["sources"]
        assert [(s["mode"], s["kind"], s["outcome"]) for s in sources] == [
            ("api", "greenhouse", "complete"),
            ("feed", "remoteok", "failed"),
            ("feed", "hn_freelance", "no_matches"),
            ("feed", "weworkremotely", "partial"),
            ("fixed_reader", "vie", "complete"),
            (None, "google_careers", None),
        ]
        board, remoteok, _, wwr, vie, careers = sources
        assert (board["found"], board["new"], board["query"]) == (3, 2, "acme-ai")
        assert (remoteok["error"], remoteok["reason"], remoteok["items_skipped"]) == (
            "feed_unavailable", "feed_unavailable", 2,
        )  # fmt: skip
        assert (wwr["new"], wwr["skipped_by_rule"]) == (1, 1)
        assert (vie["location"], vie["result_pages"], vie["new"]) == ("France", 2, 1)
        assert (careers["error"], careers["found"], careers["host"]) == ("layout_changed", 2, None)
        scope = body["search_scope"]
        assert (scope["source_count"], scope["websites_only"]) == (5, False)
        assert scope["criteria"] == {"days": 30}
    assert detail.json()["new_jobs"] == []
    # Its events end at once with the row as stored, and it stays Alice's.
    response, found = events(api, alice, sweep_id)
    assert response.status_code == 200
    assert [name for name, _ in found] == ["done"]
    assert found[0][1]["boards_failed"][0]["org"] == "gone-ai"
    assert api.request("GET", f"/api/jobs/sweeps/{sweep_id}", token=bob.token).status_code == 404


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
) -> None:
    set_profile(factory, alice.id, search())
    started = engine_start(factory, alice)
    run(factory, uuid.UUID(started["id"]), *FOUND)
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
) -> None:
    set_profile(factory, alice.id, search(interactive_sites=[SITE, SITE_2]))
    started = engine_start(factory, alice)
    third = finding("Forward Deployed Engineer", url="https://careers.example-labs.test/fde")

    def later() -> None:
        time.sleep(0.4)
        run(
            factory,
            uuid.UUID(started["id"]),
            heartbeat_s=0.1,
            browser=each_website(FOUND, [third], hang_s=0.5, hang_at=0),
        )

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
    new_counts = [data["new"] for name, data in found if name == "progress"]
    assert new_counts == sorted(new_counts)
    assert 2 in new_counts  # the first website's findings, seen before the second one runs


def test_stream_ends_with_gone_when_the_sweep_disappears(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    started = engine_start(factory, alice)

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

    set_profile(factory, alice.id, search())
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

    # No ticked website: the schedule is kept but nothing runs.
    no_websites = {**stored, "enabled": True}
    set_profile(factory, alice.id, search(interactive_sites=[], sweep_schedule=no_websites))
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
    set_profile(factory, alice.id, search())
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
