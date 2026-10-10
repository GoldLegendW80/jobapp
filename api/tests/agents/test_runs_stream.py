"""GET /api/agents/runs/{id}/stream: the run's events replayed after Last-Event-ID (or ?after=),
followed live while the worker appends them, `end` once the run has ended; isolation and
authentication. Real Postgres; the worker is played by a thread writing events."""

import json
import threading
import time
import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runs
from app.agents.models import AgentRun, RunStatus
from app.identity.deps import COOKIE_NAME
from tests.agents.run_helpers import add_user, new_run, now
from tests.identity.api_helpers import Api

Found = list[tuple[str, int | None, dict[str, Any]]]


@pytest.fixture(autouse=True)
def fast_stream(app: FastAPI) -> None:
    app.state.sse_settings = {"interval_s": 0.05, "heartbeat_s": 0.3, "max_s": 10}


def user_id(api: Api, token: str) -> uuid.UUID:
    return uuid.UUID(api.me(token).json()["id"])


def stream(
    api: Api, token: str, run_id: object, headers: dict[str, str] | None = None, query: str = ""
) -> tuple[httpx.Response, Found]:
    found: Found = []
    sent = {"Cookie": f"{COOKIE_NAME}={token}", **(headers or {})}
    api.client.cookies.clear()
    path = f"/api/agents/runs/{run_id}/stream{query}"
    with api.client.stream("GET", path, headers=sent) as response:
        name: str | None = None
        event_id: int | None = None
        for line in response.iter_lines():
            if line.startswith("id: "):
                event_id = int(line.removeprefix("id: "))
            elif line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: ") and name:
                found.append((name, event_id, json.loads(line.removeprefix("data: "))))
                name, event_id = None, None
        return response, found


def ids(found: Found) -> list[int]:
    return [event_id for _, event_id, _ in found if event_id is not None]


def run_to_end(factory: sessionmaker[Session], run_id: uuid.UUID, texts: int) -> None:
    with factory() as db:
        runs.claim(db, run_id, now())
        seq = runs.next_seq(db, run_id)
        runs.append_event(db, run_id, seq, "status", {"status": "running"}, now())
        for i in range(texts):
            runs.append_event(db, run_id, seq + 1 + i, "text", {"text": f"t{i}"}, now())
        runs.finish(db, run_id, runs.Outcome(status=RunStatus.done), now())
        runs.append_event(db, run_id, seq + 1 + texts, "status", {"status": "done"}, now())
        db.commit()


def test_a_finished_run_replays_everything_then_ends(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    run_id = new_run(factory, user_id(api, alice))
    run_to_end(factory, run_id, texts=3)
    response, found = stream(api, alice, run_id)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store, no-transform"
    steps = [(event_id, data) for name, event_id, data in found if name == "step"]
    assert [event_id for event_id, _ in steps] == [1, 2, 3, 4, 5, 6]
    assert all(event_id == data["seq"] for event_id, data in steps)
    assert [data["kind"] for _, data in steps] == [
        "status",
        "status",
        "text",
        "text",
        "text",
        "status",
    ]
    assert steps[2][1]["data"] == {"text": "t0"}
    assert found[-1] == ("end", None, {})


def test_resume_after_last_event_id_or_after(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    run_id = new_run(factory, user_id(api, alice))
    run_to_end(factory, run_id, texts=3)
    assert ids(stream(api, alice, run_id, headers={"Last-Event-ID": "4"})[1]) == [5, 6]
    assert ids(stream(api, alice, run_id, query="?after=2")[1]) == [3, 4, 5, 6]
    # The header (sent by EventSource on its own reconnects) wins over the query.
    found = stream(api, alice, run_id, headers={"Last-Event-ID": "5"}, query="?after=1")[1]
    assert ids(found) == [6]
    assert stream(api, alice, run_id, headers={"Last-Event-ID": "6"})[1] == [("end", None, {})]
    found = stream(api, alice, run_id, headers={"Last-Event-ID": "garbage"})[1]
    assert ids(found) == [1, 2, 3, 4, 5, 6]


def test_follows_a_running_run_live_and_resumes_mid_run(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    run_id = new_run(factory, user_id(api, alice))

    def worker() -> None:
        with factory() as db:
            runs.claim(db, run_id, now())
            db.commit()
        for seq in range(2, 9):
            time.sleep(0.05)
            with factory() as db:
                runs.append_event(db, run_id, seq, "text", {"text": str(seq)}, now())
                if seq == 8:
                    runs.finish(db, run_id, runs.Outcome(status=RunStatus.done), now())
                db.commit()

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        found = stream(api, alice, run_id)[1]
    finally:
        thread.join()
    assert ids(found) == list(range(1, 9))
    assert found[-1][0] == "end"
    # A reconnect with the id of a step in the middle gets exactly the rest.
    assert ids(stream(api, alice, run_id, headers={"Last-Event-ID": "5"})[1]) == [6, 7, 8]


def test_many_events_are_paged_without_gaps(
    api: Api, alice: str, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agents import runs_router

    monkeypatch.setattr(runs_router, "STREAM_PAGE", 4)
    run_id = new_run(factory, user_id(api, alice))
    run_to_end(factory, run_id, texts=13)
    found = stream(api, alice, run_id)[1]
    assert ids(found) == list(range(1, 17))
    assert found[-1][0] == "end"


def test_isolation_and_authentication(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    run_id = new_run(factory, user_id(api, alice))
    run_to_end(factory, run_id, texts=1)
    response, found = stream(api, bob, run_id)
    assert response.status_code == 404
    assert found == []
    assert api.get(f"/api/agents/runs/{run_id}/stream").status_code == 401
    assert stream(api, alice, uuid.uuid4())[0].status_code == 404
    assert stream(api, alice, "not-a-uuid")[0].status_code == 404
    carol = add_user(factory, "carol@example.org")
    assert stream(api, alice, new_run(factory, carol))[0].status_code == 404


def test_run_deleted_mid_stream_ends_with_gone(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    run_id = new_run(factory, user_id(api, alice))

    def delete_later() -> None:
        time.sleep(0.3)
        with factory() as db, db.begin():
            db.execute(sa.delete(AgentRun).where(AgentRun.id == run_id))

    thread = threading.Thread(target=delete_later)
    thread.start()
    try:
        found = stream(api, alice, run_id)[1]
    finally:
        thread.join()
    assert [name for name, _, _ in found] == ["step", "gone"]


def test_stream_slots_per_user() -> None:
    import gc

    from app.agents.runs_router import StreamSlots

    class Stream:
        pass

    slots = StreamSlots(2)
    alice, bob = uuid.uuid4(), uuid.uuid4()
    first, second, third = Stream(), Stream(), Stream()
    assert slots.take(alice, first)
    assert slots.take(alice, second)
    assert not slots.take(alice, third)
    assert slots.take(bob, third)  # Bob is not limited by Alice's streams
    slots.release(alice, first)
    assert slots.count(alice) == 1
    # A stream dropped without ever running gives its slot back too.
    del second
    gc.collect()
    assert slots.count(alice) == 0
    slots.release(alice, first)
    assert alice not in slots.open
    slots.release(uuid.uuid4(), first)  # unknown user: nothing to do


def test_too_many_streams_is_429_and_an_ended_stream_frees_its_slot(
    api: Api, alice: str, factory: sessionmaker[Session], app: FastAPI
) -> None:
    from app.agents.runs_router import StreamSlots

    me = user_id(api, alice)
    run_id = new_run(factory, me)
    run_to_end(factory, run_id, texts=1)
    app.state.run_stream_slots = StreamSlots(1)
    response, found = stream(api, alice, run_id)
    assert response.status_code == 200
    assert found[-1][0] == "end"
    assert app.state.run_stream_slots.count(me) == 0
    app.state.run_stream_slots = StreamSlots(0)
    response, _ = stream(api, alice, run_id)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "10"
