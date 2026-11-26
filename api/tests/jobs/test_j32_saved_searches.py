"""Saved searches (jobs J32, #831): the routes and their isolation, the limits, the scope of a
sweep (each key from the search, each from the person), the line and its order, two searches due
at the same minute, a slot met by a running sweep, a delete while its sweep runs, and the worker
starting the next run when a sweep ends. The queue is the recording fake of test_sweep_api."""

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app import worker
from app.browser import linkedin_guard
from app.identity.models import User
from app.jobs import searches, sweep
from app.jobs.models import (
    JobEvent,
    JobEventKind,
    SavedSearch,
    Sweep,
    SweepRequest,
    SweepStatus,
    SweepTrigger,
)
from app.profile.schemas import SearchSection
from tests.identity.api_helpers import Api, Clock
from tests.jobs.sweep_helpers import (
    SITE,
    SITE_2,
    now,
    read_sweep,
    search,
    set_profile,
    websites,
)
from tests.jobs.test_api import Signed, listing, seed, sign_in, walk
from tests.jobs.test_sweep_api import FakeQueue

BASE = "/api/jobs/searches"
TICK = datetime(2026, 10, 10, 4, 20, tzinfo=UTC)  # 06:20 in Paris
DAILY = {"enabled": True, "times": ["06:17"]}

# A value of its own for each key of one search, none of them the main search's.
CRITERIA: dict[str, Any] = {
    "title_include": {"terms": ["data engineer"], "advanced_regex": False},
    "title_exclude": {"terms": ["intern"], "advanced_regex": False},
    "location": {"terms": ["lyon"], "advanced_regex": False},
    "location_exclude": {"terms": ["marseille"], "advanced_regex": False},
    "remote_stance": "hybrid",
    "contract_types": ["CDD"],
    "days": 7,
    "interactive_sites": [SITE_2],
    "company_boards": [
        {"company": "Example Labs", "ats": "lever", "board": "examplelabs", "enabled": True}
    ],
    "job_feeds": ["himalayas"],
    "web_search": True,
    "interactive_instructions": "Only teams that ship weekly.",
}
ACCEPTED = "2026-10-19T08:00:00+00:00"
# The person's settings, each with a value a saved search could never set.
PERSON: dict[str, Any] = {
    "never_apply": ["No sales: sales (test rule)"],
    "min_fit": 55,
    "seniority": ["senior"],
    "industries": {"terms": ["robotics"], "advanced_regex": False},
    "company_types": ["startup"],
    "compensation": {"salary": {"min": 50000, "max": 60000, "currency": "EUR"}, "daily_rate": None},
    "documents": {"auto": False, "min_fit": 81, "daily_ceiling": 12},
    "browser_sources": [
        {
            "kind": "linkedin",
            "query": "Robotics engineer",
            "location": "Lyon",
            "enabled": True,
            "interactive_only": True,
            "accepted_at": ACCEPTED,
            "text_version": linkedin_guard.TEXT_VERSION,
        }
    ],
    "platform_consents": {
        "indeed": {"enabled": True, "accepted_at": ACCEPTED, "text_version": 3},
    },
    "linkedin_search": "browser",
    "alert_emails": {"indeed": True, "linkedin": False},
    "sweep_schedule": {
        "enabled": False,
        "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
        "times": ["07:00"],
        "timezone": "America/Montreal",
    },
}


@pytest.fixture
def fake_queue(app: FastAPI) -> FakeQueue:
    found = FakeQueue()
    app.state.queue = found
    return found


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-j32-alice@example.org")


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-j32-bob@example.org")


def call(api: Api, who: Signed, method: str, path: str = "", **kwargs: Any) -> httpx.Response:
    return api.request(method, BASE + path, token=who.token, **kwargs)


def create(api: Api, who: Signed, name: str = "Data in Lyon", **body: Any) -> httpx.Response:
    return call(api, who, "POST", json={"name": name, "criteria": CRITERIA, **body})


def sweeps(factory: sessionmaker[Session], user_id: uuid.UUID) -> list[Sweep]:
    with factory() as db:
        query = sa.select(Sweep).where(Sweep.user_id == user_id).order_by(Sweep.started_at)
        return list(db.execute(query).scalars())


def end(factory: sessionmaker[Session], sweep_id: str | uuid.UUID) -> None:
    with factory() as db:
        row = db.get(Sweep, uuid.UUID(str(sweep_id)))
        assert row is not None
        row.status, row.finished_at = SweepStatus.done, max(now(), row.started_at)
        db.commit()


def start_next(factory: sessionmaker[Session], user_id: uuid.UUID) -> Sweep | None:
    with factory() as db:
        row = sweep.start_next(db, user_id, now())
        db.commit()
        return row


# --- routes, isolation, limits ------------------------------------------------------------------


def test_the_main_search_is_listed_first_and_changes_only_in_settings(
    api: Api, factory: sessionmaker[Session], alice: Signed
) -> None:
    set_profile(factory, alice.id, search(**PERSON))
    listed = call(api, alice, "GET").json()
    [main] = listed["items"]
    assert (main["id"], main["main"], main["name"]) == ("main", True, "Main search")
    assert main["criteria"]["interactive_sites"] == [SITE]
    assert set(main["criteria"]) == set(searches.CRITERIA)
    assert main["schedule"]["timezone"] == "America/Montreal"
    assert call(api, alice, "GET", "/main").json() == main
    for method in ("PUT", "DELETE"):
        refused = call(api, alice, method, "/main", json={"revision": 1, "name": "x"})
        assert refused.status_code == 422, method
    assert call(api, alice, "PUT", "/main", json={"name": "x", "revision": 1}).json()["detail"] == (
        "The main search is your profile's search: change it in Settings."
    )


def test_create_read_change_delete_with_the_revision(
    api: Api, factory: sessionmaker[Session], alice: Signed
) -> None:
    set_profile(factory, alice.id, search(**PERSON))
    made = create(api, alice, schedule=DAILY)
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["main"], body["revision"], body["criteria"]["days"]) == (False, 1, 7)
    # The time zone is the person's.
    assert body["schedule"]["timezone"] == "America/Montreal"
    assert body["schedule"]["next_run"] is not None
    assert [item["id"] for item in call(api, alice, "GET").json()["items"]] == ["main", body["id"]]
    assert call(api, alice, "GET", f"/{body['id']}").json() == body
    change = {"name": "Data, Lyon", "criteria": {**CRITERIA, "days": 3}, "revision": 1}
    changed = call(api, alice, "PUT", f"/{body['id']}", json=change)
    assert changed.status_code == 200, changed.text
    assert (changed.json()["revision"], changed.json()["criteria"]["days"]) == (2, 3)
    stale = call(api, alice, "PUT", f"/{body['id']}", json=change)
    assert stale.status_code == 409
    assert stale.json() == {
        "detail": "This search changed in another tab. Open it again.",
        "revision": 2,
    }
    assert call(api, alice, "DELETE", f"/{body['id']}").status_code == 204
    assert call(api, alice, "GET", f"/{body['id']}").status_code == 404
    assert call(api, alice, "DELETE", f"/{body['id']}").status_code == 404


def test_another_persons_search_answers_404_everywhere(
    api: Api, factory: sessionmaker[Session], alice: Signed, bob: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    set_profile(factory, bob.id, search())
    mine = create(api, alice).json()["id"]
    # Alice's run of it waits behind her sweep: Bob's take back must not find it.
    call(api, alice, "POST", "/main/run")
    waits = call(api, alice, "POST", f"/{mine}/run").json()["request"]["id"]
    asked = len(fake_queue.calls)
    change = {"name": "Mine now", "criteria": CRITERIA, "revision": 1}
    for method, path, body in (
        ("GET", f"/{mine}", None),
        ("PUT", f"/{mine}", change),
        ("DELETE", f"/{mine}", None),
        ("POST", f"/{mine}/run", None),
        ("DELETE", f"/{mine}/request", None),
        ("GET", "/not-an-id", None),
    ):
        assert call(api, bob, method, path, json=body).status_code == 404, (method, path)
    assert [item["id"] for item in call(api, bob, "GET").json()["items"]] == ["main"]
    assert sweeps(factory, bob.id) == []
    assert len(fake_queue.calls) == asked
    with factory() as db:
        assert db.get(SavedSearch, uuid.UUID(mine)) is not None
        assert [row.id for row in searches.waiting(db, alice.id)] == [uuid.UUID(waits)]


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"name": ""}, "at least 1"),
        ({"name": "x" * 61}, "at most 60"),
        ({"criteria": {"min_fit": 10}}, "criteria.min_fit: not a key of one search"),
        ({"criteria": {"sweep_schedule": {}}}, "criteria.sweep_schedule: not a key"),
        ({"criteria": {"days": 0}}, "criteria.days"),
        ({"criteria": {"interactive_sites": [{**SITE, "mode": "fixed"}]}}, "interactive_sites"),
        ({"schedule": {"enabled": True, "times": ["25:00"]}}, "HH:MM"),
        ({"schedule": {"enabled": True, "timezone": "UTC"}}, "timezone"),
    ],
)
def test_the_checks_of_one_search_refuse(
    api: Api, factory: sessionmaker[Session], alice: Signed, body: dict[str, Any], detail: str
) -> None:
    set_profile(factory, alice.id, search())
    refused = call(api, alice, "POST", json={"name": "Data", "criteria": {}, **body})
    assert refused.status_code == 422
    assert detail in refused.text
    assert call(api, alice, "GET").json()["items"][1:] == []


def test_names_are_unique_case_aside_and_twenty_at_most(
    api: Api, factory: sessionmaker[Session], alice: Signed, bob: Signed
) -> None:
    set_profile(factory, alice.id, search())
    assert create(api, alice, "Data in Lyon").status_code == 201
    for taken in ("DATA IN LYON", "  data in lyon  ", "main search"):
        answer = create(api, alice, taken)
        assert (answer.status_code, answer.json()["detail"]) == (
            409,
            "You already have a search with this name.",
        ), taken
    assert create(api, bob, "Data in Lyon").status_code == 201  # Bob's own names
    for index in range(2, searches.SAVED_MAX + 1):
        assert create(api, alice, f"Search {index}").status_code == 201
    past = create(api, alice, "One too many")
    assert (past.status_code, past.json()["detail"]) == (422, "You can save at most 20 searches.")


def test_a_saved_search_lists_only_websites_the_person_gave_permission_for(
    api: Api, factory: sessionmaker[Session], alice: Signed
) -> None:
    linkedin = {"platform": "linkedin", "url": "https://www.linkedin.com/jobs/", "enabled": True}
    site = {**linkedin, "mode": "llm_browser"}
    set_profile(factory, alice.id, search())
    refused = create(api, alice, criteria={**CRITERIA, "interactive_sites": [site]})
    assert refused.status_code == 422
    assert (
        refused.json()["detail"]
        == "Give your permission to search www.linkedin.com in Find jobs first."
    )
    # Unticked, it needs none; given in Find jobs (the main search's consent), it is listed.
    unticked = {**site, "enabled": False}
    assert (
        create(
            api, alice, "Off", criteria={**CRITERIA, "interactive_sites": [unticked]}
        ).status_code
        == 201
    )
    consent = {
        "kind": "linkedin",
        "query": "AI Engineer",
        "enabled": True,
        "accepted_at": "2026-10-19T08:00:00+00:00",
        "text_version": linkedin_guard.TEXT_VERSION,
    }
    set_profile(factory, alice.id, search(browser_sources=[consent]))
    assert create(api, alice, criteria={**CRITERIA, "interactive_sites": [site]}).status_code == 201


# --- the scope ----------------------------------------------------------------------------------


def test_the_scope_is_the_searchs_criteria_over_the_persons_settings(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search(**PERSON))
    made = create(api, alice).json()
    ran = call(api, alice, "POST", f"/{made['id']}/run")
    assert ran.status_code == 202, ran.text
    assert ran.json()["outcome"] == "started"
    [row] = sweeps(factory, alice.id)
    scope = row.stats["search_scope"]
    frozen = scope["search"]
    for key in searches.CRITERIA:
        if key == "web_search":
            assert "web_search" not in frozen  # its default is left out for the release before
        else:
            assert frozen[key] == made["criteria"][key], key
    # Every key of the search section is one search's or the person's, and the tables hold them.
    assert not set(searches.CRITERIA) & set(searches.PERSON)
    assert set(searches.CRITERIA) | set(searches.PERSON) == set(SearchSection.model_fields)
    assert (set(CRITERIA), set(PERSON)) == (set(searches.CRITERIA), set(searches.PERSON))
    for key, value in PERSON.items():
        assert frozen[key] == value, key
    assert scope["saved_search"] == {"id": made["id"], "name": "Data in Lyon", "revision": 1}
    assert row.saved_search_id == uuid.UUID(made["id"])
    # Its sources are its own: its website, its board, its feed and the web, never the main's.
    hosts = {source.get("host") for source in scope["sources"]}
    assert "careers.example-labs.test" in hosts
    assert "jobs.example-robotics.test" not in hosts
    assert ran.json()["sweep"]["search"] == {"id": made["id"], "name": "Data in Lyon"}
    # The claim reads the merged search back.
    plan = sweep.claim(factory, row.id, now())
    assert plan is not None
    assert plan.search is not None
    assert (plan.search.days, plan.search.min_fit, plan.search.contract_types) == (7, 55, ["CDD"])


def test_post_sweeps_still_runs_the_main_search_and_answers_409(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    create(api, alice)
    first = api.request("POST", "/api/jobs/sweeps", token=alice.token)
    assert first.status_code == 202
    assert first.json()["search"] == {"id": "main", "name": "Main search"}
    second = api.request("POST", "/api/jobs/sweeps", token=alice.token)
    assert second.status_code == 409
    assert second.json() == {
        "detail": "A sweep is already running.",
        "sweep_id": first.json()["id"],
    }
    with factory() as db:
        assert db.execute(sa.select(SweepRequest)).scalars().all() == []


def test_a_delete_while_its_sweep_runs_keeps_the_sweep_and_its_name(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    made = create(api, alice).json()
    sweep_id = call(api, alice, "POST", f"/{made['id']}/run").json()["sweep"]["id"]
    assert call(api, alice, "DELETE", f"/{made['id']}").status_code == 204
    shown = api.request("GET", f"/api/jobs/sweeps/{sweep_id}", token=alice.token).json()
    assert shown["status"] == "running"
    assert shown["search"] == {"id": None, "name": "Data in Lyon"}
    plan = sweep.claim(factory, uuid.UUID(sweep_id), now())
    assert plan is not None
    assert plan.search is not None
    assert plan.search.days == 7


# --- the line -----------------------------------------------------------------------------------


def test_a_run_asked_while_one_runs_waits_in_line_and_starts_in_order(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    one = create(api, alice, "One").json()["id"]
    two = create(api, alice, "Two").json()["id"]
    running = call(api, alice, "POST", "/main/run").json()["sweep"]["id"]
    queued = call(api, alice, "POST", f"/{two}/run")
    assert queued.status_code == 202
    assert queued.json()["outcome"] == "queued"
    assert queued.json()["running_sweep_id"] == running
    assert queued.json()["request"]["place"] == 1
    second = call(api, alice, "POST", f"/{one}/run").json()
    assert second["request"]["place"] == 2
    # Asked again: the same request, the same place.
    again = call(api, alice, "POST", f"/{two}/run").json()["request"]
    assert (again["id"], again["place"]) == (queued.json()["request"]["id"], 1)
    listed = {item["id"]: item["request"] for item in call(api, alice, "GET").json()["items"]}
    assert (listed["main"], listed[two]["place"], listed[one]["place"]) == (None, 1, 2)
    assert start_next(factory, alice.id) is None  # the main search still runs
    end(factory, running)
    first = start_next(factory, alice.id)
    assert first is not None
    assert first.saved_search_id == uuid.UUID(two)
    assert first.trigger is SweepTrigger.manual
    end(factory, first.id)
    after = start_next(factory, alice.id)
    assert after is not None
    assert after.saved_search_id == uuid.UUID(one)
    end(factory, after.id)
    assert start_next(factory, alice.id) is None
    assert len(fake_queue.calls) == 1  # the line's starts are the worker's to queue


def test_a_request_can_be_taken_back(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    one = create(api, alice, "One").json()["id"]
    running = call(api, alice, "POST", f"/{one}/run").json()["sweep"]["id"]
    assert call(api, alice, "POST", "/main/run").json()["request"]["search_id"] == "main"
    assert call(api, alice, "DELETE", "/main/request").status_code == 204
    gone = call(api, alice, "DELETE", "/main/request")
    assert (gone.status_code, gone.json()["detail"]) == (404, "No run of this search is waiting.")
    end(factory, running)
    assert start_next(factory, alice.id) is None


def test_run_all_asks_each_search_in_order(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    one = create(api, alice, "One").json()["id"]
    empty = create(api, alice, "Empty", criteria={**CRITERIA, "interactive_sites": [],
        "company_boards": None, "job_feeds": [], "web_search": False}).json()["id"]  # fmt: skip
    two = create(api, alice, "Two").json()["id"]
    ran = call(api, alice, "POST", "/run")
    assert ran.status_code == 202
    outcomes = [(item["search_id"], item["outcome"]) for item in ran.json()["items"]]
    assert outcomes == [
        ("main", "started"),
        (one, "queued"),
        (empty, "no_websites"),
        (two, "queued"),
    ]
    assert [item["request"]["place"] for item in ran.json()["items"][1::2]] == [1, 2]


# --- schedules ----------------------------------------------------------------------------------


def due(factory: sessionmaker[Session], at: datetime = TICK) -> list[sweep.DueSweep]:
    with factory() as db:
        return sweep.scheduled_due(db, at)


def scheduled(
    factory: sessionmaker[Session], item: sweep.DueSweep, at: datetime = TICK
) -> Sweep | None:
    with factory() as db:
        row = sweep.start_scheduled(db, item, at)
        db.commit()
        return row


def test_two_searches_due_at_the_same_minute_both_fire_one_after_the_other(
    api: Api, factory: sessionmaker[Session], alice: Signed
) -> None:
    set_profile(factory, alice.id, search(sweep_schedule={**DAILY, "timezone": "Europe/Paris"}))
    saved = create(api, alice, schedule=DAILY).json()["id"]
    off = create(api, alice, "Off").json()["id"]  # no schedule: never due
    found = due(factory)
    main_key = "2026-10-10T06:17 Europe/Paris"
    assert [(item.saved_search_id, item.slot.key) for item in found] == [
        (None, main_key),
        (uuid.UUID(saved), f"{main_key} {saved}"),
    ]
    assert off not in {str(item.saved_search_id) for item in found}
    first = scheduled(factory, found[0])
    assert first is not None
    assert first.saved_search_id is None
    # The second meets the first one running: a request in the line holding its slot.
    assert scheduled(factory, found[1]) is None
    assert due(factory, TICK + timedelta(minutes=5)) == []
    assert due(factory, TICK + timedelta(minutes=20)) == []  # past the window: kept, not lost
    end(factory, first.id)
    second = start_next(factory, alice.id)
    assert second is not None
    assert (second.trigger, second.saved_search_id) == (SweepTrigger.schedule, uuid.UUID(saved))
    assert second.stats["schedule_slot"] == f"{main_key} {saved}"
    assert due(factory, TICK + timedelta(minutes=5)) == []


def test_a_slot_met_by_a_running_sweep_waits_in_line_for_the_main_search_too(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search(sweep_schedule={**DAILY, "timezone": "Europe/Paris"}))
    saved = create(api, alice).json()["id"]
    running = call(api, alice, "POST", f"/{saved}/run").json()["sweep"]["id"]
    # The person asked the main search by hand meanwhile: the slot joins that request.
    assert call(api, alice, "POST", "/main/run").json()["request"]["place"] == 1
    [item] = due(factory)
    assert scheduled(factory, item) is None
    with factory() as db:
        [request] = db.execute(sa.select(SweepRequest)).scalars().all()
    assert (request.schedule_slot, request.trigger) == (item.slot.key, SweepTrigger.schedule)
    assert due(factory, TICK + timedelta(minutes=10)) == []
    end(factory, running)
    row = start_next(factory, alice.id)
    assert row is not None
    assert row.saved_search_id is None
    assert (row.trigger, row.stats["schedule_slot"]) == (SweepTrigger.schedule, item.slot.key)


def test_the_worker_starts_the_next_run_when_a_sweep_ends(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    saved = create(api, alice).json()["id"]
    running = call(api, alice, "POST", "/main/run").json()["sweep"]["id"]
    call(api, alice, "POST", f"/{saved}/run")
    queued: list[tuple[Any, ...]] = []

    class Redis:
        async def enqueue_job(self, *args: Any, **kwargs: Any) -> None:
            queued.append((*args, kwargs["_job_id"]))

    ctx = {"sessions": factory, "clock": now, "redis": Redis()}
    end(factory, running)
    assert (
        asyncio.run(worker.start_the_line(ctx, worker.owner_of(factory, uuid.UUID(running)))) == 1
    )
    [row] = [r for r in sweeps(factory, alice.id) if r.status is SweepStatus.running]
    assert row.saved_search_id == uuid.UUID(saved)
    assert queued == [("run_sweep", str(row.id), f"sweep:{row.id}")]
    # Nothing waits any more: the tick's pass over everyone starts nothing.
    with factory() as db:
        assert searches.users_waiting(db) == []


def test_two_ends_at_once_start_the_line_once(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    """A sweep's end and the tick, side by side: one starts the next run, the other finds it
    running, and the run after it keeps its place."""
    set_profile(factory, alice.id, search())
    one = create(api, alice, "One").json()["id"]
    two = create(api, alice, "Two").json()["id"]
    running = call(api, alice, "POST", "/main/run").json()["sweep"]["id"]
    call(api, alice, "POST", f"/{one}/run")
    call(api, alice, "POST", f"/{two}/run")
    end(factory, running)
    barrier = threading.Barrier(2)
    started: list[Sweep | None] = []

    def tick() -> None:
        barrier.wait()
        started.append(start_next(factory, alice.id))

    threads = [threading.Thread(target=tick) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    [row] = [item for item in started if item is not None]
    assert row.saved_search_id == uuid.UUID(one)
    with factory() as db:
        [left] = searches.waiting(db, alice.id)
    assert left.saved_search_id == uuid.UUID(two)


def test_a_run_of_the_line_the_queue_refuses_fails_and_the_tick_goes_on(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    one = create(api, alice, "One").json()["id"]
    two = create(api, alice, "Two").json()["id"]
    running = call(api, alice, "POST", "/main/run").json()["sweep"]["id"]
    call(api, alice, "POST", f"/{one}/run")
    call(api, alice, "POST", f"/{two}/run")
    end(factory, running)

    class Broken:
        async def enqueue_job(self, *_: Any, **__: Any) -> None:
            raise ConnectionError("redis gone")

    ctx = {"sessions": factory, "clock": now, "redis": Broken()}
    assert asyncio.run(worker.start_the_line(ctx, [alice.id])) == 1
    failed = [row for row in sweeps(factory, alice.id) if row.saved_search_id == uuid.UUID(one)]
    assert [(row.status, row.error) for row in failed] == [(SweepStatus.failed, sweep.QUEUE_DOWN)]
    # Nothing runs: the tick's pass over everyone waiting starts the next one.
    queued: list[str] = []

    class Redis:
        async def enqueue_job(self, *args: Any, **kwargs: Any) -> None:
            queued.append(kwargs["_job_id"])

    with factory() as db:
        assert searches.users_waiting(db) == [alice.id]
    assert asyncio.run(worker.start_the_line(ctx | {"redis": Redis()}, [alice.id])) == 1
    [row] = [r for r in sweeps(factory, alice.id) if r.status is SweepStatus.running]
    assert (row.saved_search_id, queued) == (uuid.UUID(two), [f"sweep:{row.id}"])


class Recording:
    """The worker's Redis pool, recording what it queues."""

    def __init__(self) -> None:
        self.queued: list[tuple[str, str, str]] = []

    async def enqueue_job(self, *args: Any, **kwargs: Any) -> None:
        self.queued.append((args[0], args[1], kwargs["_job_id"]))


def running_of(factory: sessionmaker[Session], user_id: uuid.UUID) -> list[Sweep]:
    return [row for row in sweeps(factory, user_id) if row.status is SweepStatus.running]


def waiting_of(factory: sessionmaker[Session], user_id: uuid.UUID) -> list[SweepRequest]:
    with factory() as db:
        return searches.waiting(db, user_id)


# Websites only: the worker's fakes read them, and nothing else is ticked.
SITE_ONLY = {**CRITERIA, "company_boards": None, "job_feeds": [], "web_search": False}
NO_SOURCES = {**SITE_ONLY, "interactive_sites": []}


def test_the_end_of_run_sweep_starts_the_line_past_a_search_that_lost_its_websites(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    one = create(api, alice, "One", criteria=SITE_ONLY).json()["id"]
    two = create(api, alice, "Two", criteria=SITE_ONLY).json()["id"]
    running = call(api, alice, "POST", "/main/run").json()["sweep"]["id"]
    call(api, alice, "POST", f"/{one}/run")
    call(api, alice, "POST", f"/{two}/run")
    # One loses its websites while it waits.
    with factory() as db:
        row = db.get(SavedSearch, uuid.UUID(one))
        assert row is not None
        row.criteria = NO_SOURCES
        db.commit()
    redis = Recording()
    ctx = {
        "sessions": factory,
        "clock": now,
        "redis": redis,
        "alert_sources": None,
        "browser_sources": websites(),
    }
    asyncio.run(worker.run_sweep(ctx, running))
    assert read_sweep(factory, uuid.UUID(running)).status is SweepStatus.done
    [first] = running_of(factory, alice.id)
    assert first.saved_search_id == uuid.UUID(one)
    assert redis.queued == [("run_sweep", str(first.id), f"sweep:{first.id}")]
    # Its claim fails it with the reason, and its end starts the run after it.
    asyncio.run(worker.run_sweep(ctx, str(first.id)))
    failed = read_sweep(factory, first.id)
    assert (failed.status, failed.error) == (SweepStatus.failed, sweep.NO_WEBSITES)
    [second] = running_of(factory, alice.id)
    assert second.saved_search_id == uuid.UUID(two)
    assert redis.queued[1:] == [("run_sweep", str(second.id), f"sweep:{second.id}")]
    assert waiting_of(factory, alice.id) == []


def test_the_schedule_tick_starts_a_run_waiting_when_no_sweep_runs(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    one = create(api, alice, "One").json()["id"]
    running = call(api, alice, "POST", "/main/run").json()["sweep"]["id"]
    call(api, alice, "POST", f"/{one}/run")
    # The reaper failed the sweep: no end of its own starts the line.
    sweep.fail_sweep(factory, uuid.UUID(running), sweep.CRASHED, now())
    redis = Recording()
    ctx = {"sessions": factory, "clock": now, "redis": redis}
    assert asyncio.run(worker.scheduled_sweeps(ctx)) == 1
    [row] = running_of(factory, alice.id)
    assert row.saved_search_id == uuid.UUID(one)
    assert redis.queued == [("run_sweep", str(row.id), f"sweep:{row.id}")]
    assert asyncio.run(worker.scheduled_sweeps(ctx)) == 0


def test_a_delete_of_a_search_whose_run_waits_takes_it_out_of_the_line(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    one = create(api, alice, "One").json()["id"]
    two = create(api, alice, "Two").json()["id"]
    running = call(api, alice, "POST", "/main/run").json()["sweep"]["id"]
    call(api, alice, "POST", f"/{one}/run")
    assert call(api, alice, "POST", f"/{two}/run").json()["request"]["place"] == 2
    assert call(api, alice, "DELETE", f"/{one}").status_code == 204
    assert [row.saved_search_id for row in waiting_of(factory, alice.id)] == [uuid.UUID(two)]
    listed = {item["id"]: item["request"] for item in call(api, alice, "GET").json()["items"]}
    assert listed[two]["place"] == 1
    end(factory, running)
    row = start_next(factory, alice.id)
    assert row is not None
    assert row.saved_search_id == uuid.UUID(two)
    assert waiting_of(factory, alice.id) == []


def test_a_slot_taken_back_stays_taken_until_it_leaves_its_window(
    api: Api, factory: sessionmaker[Session], alice: Signed, clock: Clock
) -> None:
    slot = clock.now.replace(second=0)
    paris = slot.astimezone(ZoneInfo("Europe/Paris"))
    plan = {"enabled": True, "times": [paris.strftime("%H:%M")], "timezone": "Europe/Paris"}
    set_profile(factory, alice.id, search(sweep_schedule=plan))
    with factory() as db:
        running = sweep.start(db, alice.id, SweepTrigger.manual, slot - timedelta(minutes=1)).id
        db.commit()
    redis = Recording()
    at = [slot + timedelta(minutes=3)]
    ctx = {"sessions": factory, "clock": lambda: at[0], "redis": redis}

    def tick(minutes: int) -> int:
        at[0] = slot + timedelta(minutes=minutes)
        return asyncio.run(worker.scheduled_sweeps(ctx))

    assert tick(3) == 0
    [request] = waiting_of(factory, alice.id)
    key = f"{paris:%Y-%m-%dT%H:%M} Europe/Paris"
    assert request.schedule_slot == key
    clock.now = slot + timedelta(minutes=4)
    assert call(api, alice, "DELETE", "/main/request").status_code == 204
    # The sweep still runs: the slot does not come back in the line.
    assert tick(8) == 0
    assert waiting_of(factory, alice.id) == []
    # The sweep ended, the slot is still in its window: nothing starts.
    end(factory, running)
    assert tick(13) == 0
    assert (running_of(factory, alice.id), redis.queued) == ([], [])
    assert call(api, alice, "GET").json()["items"][0]["request"] is None
    # Past its window, the run taken back is forgotten, and still nothing starts.
    with factory() as db:
        assert db.execute(sa.select(SweepRequest.taken_back_at)).scalars().all() == [clock.now]
    assert tick(25) == 0
    with factory() as db:
        assert db.execute(sa.select(SweepRequest)).scalars().all() == []
    assert (running_of(factory, alice.id), redis.queued) == ([], [])


def test_a_run_asked_by_hand_and_taken_back_is_gone(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    running = call(api, alice, "POST", "/main/run").json()["sweep"]["id"]
    one = create(api, alice, "One").json()["id"]
    call(api, alice, "POST", f"/{one}/run")
    assert call(api, alice, "DELETE", f"/{one}/request").status_code == 204
    with factory() as db:
        assert db.execute(sa.select(SweepRequest)).scalars().all() == []
    # Asked again, it waits again.
    assert call(api, alice, "POST", f"/{one}/run").json()["request"]["place"] == 1
    end(factory, running)
    row = start_next(factory, alice.id)
    assert row is not None
    assert row.saved_search_id == uuid.UUID(one)


def test_one_persons_error_never_stops_the_tick_for_the_others(
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    bob: Signed,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The first person's saved search is deleted between its read and the profile lock: the
    tick fails that slot alone, and the other person's slot starts."""
    first, other = sorted([alice, bob], key=lambda who: str(who.id))
    off = {"enabled": False, "times": ["06:17"], "timezone": "Europe/Paris"}
    set_profile(factory, first.id, search(sweep_schedule=off))
    set_profile(factory, other.id, search(sweep_schedule={**DAILY, "timezone": "Europe/Paris"}))
    saved = create(api, first, schedule=DAILY).json()["id"]
    read = searches.get

    def deleted_after_its_read(db: Session, user_id: uuid.UUID, *args: Any, **kw: Any) -> Any:
        row = read(db, user_id, *args, **kw)
        with factory() as elsewhere:
            searches.delete(elsewhere, user_id, uuid.UUID(saved))
            elsewhere.commit()
        return row

    monkeypatch.setattr(searches, "get", deleted_after_its_read)
    redis = Recording()
    ctx = {"sessions": factory, "clock": lambda: TICK, "redis": redis}
    with caplog.at_level("WARNING", logger="app.worker"):
        assert asyncio.run(worker.scheduled_sweeps(ctx)) == 1
    assert sweeps(factory, first.id) == []
    [row] = running_of(factory, other.id)
    job_id = queues.schedule_job_id(other.id, row.stats["schedule_slot"])
    assert redis.queued == [("run_sweep", str(row.id), job_id)]
    [record] = [r for r in caplog.records if r.getMessage() == "scheduled slots not started"]
    assert record.__dict__["count"] == 1
    assert str(first.id) not in caplog.text


def test_one_persons_error_never_stops_the_line_of_the_others(
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    bob: Signed,
    fake_queue: FakeQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, other = sorted([alice, bob], key=lambda who: str(who.id))
    for who in (first, other):
        set_profile(factory, who.id, search())
        one = create(api, who, "One").json()["id"]
        running = call(api, who, "POST", "/main/run").json()["sweep"]["id"]
        call(api, who, "POST", f"/{one}/run")
        end(factory, running)
    start = sweep.start_next

    def broken_for_the_first(db: Session, user_id: uuid.UUID, at: datetime) -> Sweep | None:
        if user_id == first.id:
            raise RuntimeError("one person's error")
        return start(db, user_id, at)

    monkeypatch.setattr(sweep, "start_next", broken_for_the_first)
    redis = Recording()
    ctx = {"sessions": factory, "clock": now, "redis": redis}
    assert asyncio.run(worker.scheduled_sweeps(ctx)) == 1
    assert running_of(factory, first.id) == []
    assert len(waiting_of(factory, first.id)) == 1
    [row] = running_of(factory, other.id)
    assert redis.queued == [("run_sweep", str(row.id), f"sweep:{row.id}")]


def test_a_disabled_persons_waiting_runs_never_start(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search())
    one = create(api, alice, "One").json()["id"]
    running = call(api, alice, "POST", "/main/run").json()["sweep"]["id"]
    call(api, alice, "POST", f"/{one}/run")
    with factory() as db:
        user = db.get(User, alice.id)
        assert user is not None
        user.is_active = False
        db.commit()
    end(factory, running)
    redis = Recording()
    ctx = {"sessions": factory, "clock": now, "redis": redis}
    # The end of her sweep, then the tick: neither starts her run.
    assert asyncio.run(worker.start_the_line(ctx, [alice.id])) == 0
    assert asyncio.run(worker.scheduled_sweeps(ctx)) == 0
    assert (running_of(factory, alice.id), redis.queued) == ([], [])
    assert len(waiting_of(factory, alice.id)) == 1


# --- the Jobs list by search --------------------------------------------------------------------


def test_the_jobs_list_filters_by_search(
    api: Api, factory: sessionmaker[Session], alice: Signed, bob: Signed
) -> None:
    set_profile(factory, alice.id, search())
    set_profile(factory, bob.id, search())
    one = create(api, alice, "One").json()["id"]
    two = create(api, alice, "Two").json()["id"]
    gone = create(api, alice, "Gone").json()["id"]
    bobs = create(api, bob, "Bob's").json()["id"]

    def swept(who: Signed, saved_id: str | None, *slugs: str) -> None:
        """A sweep of the search that added these postings, ended."""
        with factory() as db:
            saved = searches.get(db, who.id, uuid.UUID(saved_id)) if saved_id else None
            row = sweep.start(db, who.id, SweepTrigger.manual, now(), saved=saved)
            row.status, row.finished_at = SweepStatus.done, now()
            db.commit()
            sweep_id = row.id
        rows = [{"slug": slug, "url": f"https://jobs.example.test/{slug}"} for slug in slugs]
        jobs = seed(factory, who.id, rows)
        with factory() as db, db.begin():
            for job in jobs:
                data = {"sweep_id": str(sweep_id)}
                db.add(JobEvent(job_id=job.id, user_id=who.id, kind=JobEventKind.sweep, data=data))

    swept(alice, None, "main-a")
    swept(alice, one, "one-a")
    swept(alice, one, "one-b")
    swept(alice, two, "two-a")
    swept(alice, gone, "gone-a")
    swept(bob, None, "bob-main")
    swept(bob, bobs, "bob-saved")
    # A sweep from before saved searches, with no scope: the main search's.
    with factory() as db:
        legacy = Sweep(
            user_id=alice.id, trigger=SweepTrigger.manual, status=SweepStatus.done, stats={}
        )
        db.add(legacy)
        db.commit()
    [legacy_job] = seed(
        factory, alice.id, [{"slug": "legacy", "url": "https://jobs.example.test/l"}]
    )
    with factory() as db, db.begin():
        data = {"sweep_id": str(legacy.id)}
        db.add(JobEvent(job_id=legacy_job.id, user_id=alice.id, kind=JobEventKind.sweep, data=data))
    seed(factory, alice.id, [{"slug": "by-hand", "url": "https://jobs.example.test/by-hand"}])
    # A deleted search's sweeps keep its name: never the main search's.
    assert call(api, alice, "DELETE", f"/{gone}").status_code == 204

    def found(who: Signed, value: str) -> set[str]:
        return set(walk(api, who, 50, search=value))

    assert found(alice, "main") == {"main-a", "legacy"}
    assert found(alice, one) == {"one-a", "one-b"}
    assert found(alice, two) == {"two-a"}
    for nothing in (gone, bobs, str(uuid.uuid4())):
        assert found(alice, nothing) == set(), nothing
    assert found(bob, "main") == {"bob-main"}
    assert found(bob, bobs) == {"bob-saved"}
    assert found(bob, one) == set()
    assert set(walk(api, alice, 50, search=one, sweep=str(uuid.uuid4()))) == set()
    places = api.get("/api/jobs/places", token=alice.token, params={"search": one})
    assert (places.status_code, places.json()["total"]) == (200, 2)
    for bad in ("Main", "not-an-id", ""):
        assert listing(api, alice, search=bad).status_code == 422, bad
