"""/api/agents and /api/agents/runs through the app (real Postgres, the queue recorded instead of
Redis): default deny, CSRF, validation, credential and job checks, limits, the queue being down,
user isolation for every route, cancel, and nothing secret in answers or logs."""

import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import runs
from app.agents.models import RunStatus
from tests.agents.conftest import ALICE, API_KEY, BOB, FakeAnthropic, build_app, client_for
from tests.agents.run_helpers import add_job, events, run_row
from tests.identity.api_helpers import Api, Clock

RUNS = "/api/agents/runs"


class RecordingQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], str, str]] = []
        self.down = False

    async def enqueue(self, function: str, *args: Any, queue: str, job_id: str) -> bool:
        if self.down:
            raise queues.QueueUnavailableError("down")
        self.jobs.append((function, args, queue, job_id))
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
def recorded(app: FastAPI) -> RecordingQueue:
    fake = RecordingQueue()
    app.state.queue = fake
    return fake


def save_key(api: Api, token: str, secret: str = API_KEY) -> None:
    response = api.request(
        "PUT",
        "/api/me/model-credential",
        token=token,
        json={"kind": "anthropic_api_key", "secret": secret},
    )
    assert response.status_code == 200, response.text


def user_id(api: Api, token: str) -> uuid.UUID:
    return uuid.UUID(api.me(token).json()["id"])


def start(api: Api, token: str, **body: Any) -> Any:
    payload = {"agent": "echo", "input": {"message": "hello"}, **body}
    return api.post(RUNS, token=token, json=payload)


def test_everything_needs_a_session_and_csrf(
    api: Api, alice: str, recorded: RecordingQueue
) -> None:
    run_id = uuid.uuid4()
    for method, path in (
        ("GET", "/api/agents"),
        ("GET", RUNS),
        ("POST", RUNS),
        ("GET", f"{RUNS}/{run_id}"),
        ("POST", f"{RUNS}/{run_id}/cancel"),
    ):
        assert api.request(method, path).status_code == 401, path
    save_key(api, alice)
    assert start(api, alice).status_code == 202
    # Writes without the CSRF header are refused even with a session.
    response = api.request(
        "POST", RUNS, token=alice, csrf=False, json={"agent": "echo", "input": {"message": "x"}}
    )
    assert response.status_code == 403
    response = api.request("POST", f"{RUNS}/{run_id}/cancel", token=alice, csrf=False)
    assert response.status_code == 403


def test_agents_list(api: Api, alice: str) -> None:
    response = api.get("/api/agents", token=alice)
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["name"] for item in items] == ["echo", "triage"]
    assert items[0]["model"] == "claude-opus-5-5"
    assert items[0]["test_only"] is True
    assert items[0]["needs_job"] is False
    assert items[1]["model"] == "claude-opus-5-5"
    assert items[1]["test_only"] is False
    assert items[1]["needs_job"] is True


def test_start_queues_and_enqueues(
    api: Api, alice: str, recorded: RecordingQueue, factory: sessionmaker[Session]
) -> None:
    save_key(api, alice)
    response = start(api, alice, input={"message": "Bonjour 👋"})
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "queued"
    assert body["agent"] == "echo"
    assert body["input"] == {"message": "Bonjour 👋"}
    assert body["model"] == "claude-opus-5-5"
    assert body["cancel_requested"] is False
    assert body["duration_s"] is None
    run_id = body["id"]
    assert recorded.jobs == [
        ("run_agent", (run_id,), queues.AGENTS, queues.agent_run_job_id(run_id))
    ]
    assert events(factory, uuid.UUID(run_id)) == [("status", {"status": "queued"})]


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"agent": "nope", "input": {"message": "x"}}, "agent: no such agent here."),
        ({"agent": "echo", "input": {}}, "input.message: Field required"),
        ({"agent": "echo", "input": {"message": ""}}, "input.message: String should have"),
        ({"agent": "echo", "input": {"message": "x" * 2001}}, "input.message: String should"),
        ({"agent": "echo", "input": {"message": "x", "extra": 1}}, "input.extra: Extra inputs"),
        ({"agent": "echo", "input": {"message": "x", "script": "../etc"}}, "input.script"),
        ({"agent": "echo", "input": {"message": "x" * 17000}}, "input: at most 16384 bytes"),
        ({"agent": "", "input": {}}, "agent: String should have at least 1 character"),
        ({"agent": "echo", "job_id": "not-a-uuid", "input": {"message": "x"}}, "job_id"),
        ({"agent": "echo", "input": {"message": "x"}, "user_id": "x"}, "user_id: Extra"),
    ],
)
def test_start_validation(
    api: Api, alice: str, recorded: RecordingQueue, body: dict[str, Any], detail: str
) -> None:
    save_key(api, alice)
    response = api.post(RUNS, token=alice, json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"].startswith(detail)
    assert recorded.jobs == []


def test_start_without_credential_is_409(api: Api, alice: str, recorded: RecordingQueue) -> None:
    response = start(api, alice)
    assert response.status_code == 409
    assert "Account page" in response.json()["detail"]
    assert recorded.jobs == []
    assert api.get(RUNS, token=alice).json()["items"] == []


def test_start_with_another_users_job_is_404(
    api: Api,
    alice: str,
    bob: str,
    recorded: RecordingQueue,
    factory: sessionmaker[Session],
) -> None:
    save_key(api, alice)
    bobs_job = add_job(factory, user_id(api, bob))
    response = start(api, alice, job_id=str(bobs_job))
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found."
    alices_job = add_job(factory, user_id(api, alice))
    response = start(api, alice, job_id=str(alices_job))
    assert response.status_code == 202
    assert response.json()["job_id"] == str(alices_job)


def test_too_many_active_runs_is_429(api: Api, alice: str, recorded: RecordingQueue) -> None:
    save_key(api, alice)
    for _ in range(runs.ACTIVE_PER_USER):
        assert start(api, alice).status_code == 202
    response = start(api, alice)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"


def test_queue_down_fails_the_run_at_once(
    api: Api, alice: str, recorded: RecordingQueue, factory: sessionmaker[Session]
) -> None:
    save_key(api, alice)
    recorded.down = True
    response = start(api, alice)
    assert response.status_code == 503
    assert response.json()["detail"] == runs.QUEUE_DOWN
    [item] = api.get(RUNS, token=alice).json()["items"]
    assert item["status"] == "failed"
    assert item["error"] == runs.QUEUE_DOWN
    # It does not count against the limit.
    recorded.down = False
    assert start(api, alice).status_code == 202


def test_no_credentials_key_is_503(
    migrated_url: str, clock: Clock, anthropic_api: FakeAnthropic, key: str
) -> None:
    app = build_app(migrated_url, clock, anthropic_api, None)
    for api in client_for(app):
        from tests.identity.api_helpers import make_user

        make_user(app.state.sessionmaker, email=ALICE, name="Alice")
        token = api.login_token(ALICE)
        response = start(api, token)
        assert response.status_code == 503
        assert "CREDENTIALS_KEY" in response.json()["detail"]


def test_test_agents_are_refused_in_production(
    migrated_url: str, clock: Clock, anthropic_api: FakeAnthropic, key: str
) -> None:
    from app.agents.verify import Verifier
    from app.main import create_app
    from tests.conftest import make_settings

    settings = make_settings(migrated_url, credentials_key=key, secret_key="s" * 40).model_copy(
        update={"app_env": "production"}
    )
    app = create_app(settings, clock=clock)
    app.state.model_verifier = Verifier()
    app.state.queue = RecordingQueue()
    for api in client_for(app):
        from tests.identity.api_helpers import make_user

        make_user(app.state.sessionmaker, email=ALICE, name="Alice")
        token = api.login_token(ALICE)
        names = [item["name"] for item in api.get("/api/agents", token=token).json()["items"]]
        assert names == ["triage"]
        save_key(api, token)
        response = start(api, token)
        assert response.status_code == 422
        assert response.json()["detail"] == "agent: no such agent here."


def test_list_detail_and_isolation(
    api: Api, alice: str, bob: str, recorded: RecordingQueue, clock: Clock
) -> None:
    save_key(api, alice)
    save_key(api, bob)
    first = start(api, alice, input={"message": "one"}).json()["id"]
    clock.advance(seconds=1)
    second = start(api, alice, input={"message": "two"}).json()["id"]
    bobs = start(api, bob).json()["id"]
    listed = api.get(RUNS, token=alice).json()["items"]
    assert [item["id"] for item in listed] == [second, first]
    assert [item["id"] for item in api.get(RUNS, token=bob).json()["items"]] == [bobs]
    assert [i["id"] for i in api.get(f"{RUNS}?limit=1", token=alice).json()["items"]] == [second]
    before = listed[0]["created_at"]
    paged = api.get(RUNS, token=alice, params={"before": before}).json()["items"]
    assert [i["id"] for i in paged] == [first]
    assert api.get(f"{RUNS}?limit=0", token=alice).status_code == 422
    assert api.get(f"{RUNS}?limit=101", token=alice).status_code == 422

    detail = api.get(f"{RUNS}/{first}", token=alice)
    assert detail.status_code == 200
    body = detail.json()
    assert body["events"] == [
        {"seq": 1, "at": body["events"][0]["at"], "kind": "status", "data": {"status": "queued"}}
    ]
    assert body["events_truncated"] is False
    # Bob cannot read, nor cancel, Alice's run: the same 404 as a run that does not exist.
    for path in (f"{RUNS}/{first}",):
        assert api.get(path, token=bob).status_code == 404
    assert api.post(f"{RUNS}/{first}/cancel", token=bob).status_code == 404
    assert api.get(f"{RUNS}/{uuid.uuid4()}", token=alice).status_code == 404
    assert api.get(f"{RUNS}/not-a-uuid", token=alice).status_code == 404
    assert api.post(f"{RUNS}/not-a-uuid/cancel", token=alice).status_code == 404


def test_cancel_queued_running_and_ended(
    api: Api, alice: str, recorded: RecordingQueue, factory: sessionmaker[Session]
) -> None:
    save_key(api, alice)
    queued = start(api, alice).json()["id"]
    response = api.post(f"{RUNS}/{queued}/cancel", token=alice)
    assert response.status_code == 202
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancel_requested"] is True
    assert api.post(f"{RUNS}/{queued}/cancel", token=alice).status_code == 409

    running = start(api, alice).json()["id"]
    with factory() as db:
        runs.claim(db, uuid.UUID(running), clock_now())
        db.commit()
    response = api.post(f"{RUNS}/{running}/cancel", token=alice)
    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert response.json()["cancel_requested"] is True
    assert run_row(factory, uuid.UUID(running)).status is RunStatus.running


def clock_now() -> Any:
    from tests.agents.run_helpers import now

    return now()


def test_detail_caps_events(
    api: Api,
    alice: str,
    recorded: RecordingQueue,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agents import runs_router

    monkeypatch.setattr(runs_router, "EVENTS_MAX", 3)
    save_key(api, alice)
    run_id = start(api, alice).json()["id"]
    with factory() as db:
        for seq in range(2, 6):
            runs.append_event(db, uuid.UUID(run_id), seq, "text", {"text": "x"}, clock_now())
        db.commit()
    body = api.get(f"{RUNS}/{run_id}", token=alice).json()
    assert [e["seq"] for e in body["events"]] == [1, 2, 3]
    assert body["events_truncated"] is True


def test_finished_run_shows_duration_tokens_and_cost(
    api: Api, alice: str, recorded: RecordingQueue, factory: sessionmaker[Session]
) -> None:
    from decimal import Decimal

    save_key(api, alice)
    run_id = uuid.UUID(start(api, alice).json()["id"])
    with factory() as db:
        runs.claim(db, run_id, clock_now())
        runs.finish(
            db,
            run_id,
            runs.Outcome(
                status=RunStatus.done,
                output={"text": "ok"},
                input_tokens=100,
                output_tokens=7,
                cost_usd=Decimal("0.5"),
            ),
            clock_now(),
        )
        db.commit()
    body = api.get(f"{RUNS}/{run_id}", token=alice).json()
    assert body["status"] == "done"
    assert body["output"] == {"text": "ok"}
    assert (body["input_tokens"], body["output_tokens"], body["cost_usd"]) == (100, 7, 0.5)
    assert body["duration_s"] is not None
    assert body["duration_s"] >= 0


@pytest.fixture
def logs(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    with caplog.at_level(logging.DEBUG):
        yield caplog


def test_no_secret_or_input_in_answers_or_logs(
    api: Api, alice: str, recorded: RecordingQueue, logs: pytest.LogCaptureFixture
) -> None:
    save_key(api, alice)
    response = start(api, alice, input={"message": "private note about my salary"})
    assert API_KEY not in response.text
    detail = api.get(f"{RUNS}/{response.json()['id']}", token=alice).text
    assert API_KEY not in detail
    assert API_KEY not in logs.text
    assert "private note" not in logs.text


__all__ = ["BOB"]
