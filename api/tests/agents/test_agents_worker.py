"""The agents queue in the real worker (app/worker.py) on a real Redis and Postgres, with the fake
backend: a run started through the api ends done, at most two run at once while a third waits,
a cancel through the api stops a running run, runs left running by a dead worker are failed at
start, and a stopping worker fails its running run cleanly."""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from arq.worker import Worker
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import queue as queues
from app import worker
from app.agents import runs
from app.agents.crypto import Keyring
from app.agents.models import RunStatus
from app.config import Settings
from app.main import create_app
from tests.agents.conftest import ALICE, API_KEY
from tests.agents.run_helpers import Backends, add_user, new_run, now, run_row, save_credential
from tests.conftest import make_settings
from tests.identity.api_helpers import Api, make_user
from tests.jobs.test_worker import flush_redis, redis_url, serve_until, wait_for

__all__ = ["redis_url"]


@pytest.fixture
def settings(migrated_url: str, redis_url: str, key: str, tmp_path: Path) -> Iterator[Settings]:
    flush_redis(redis_url)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(
        migrated_url,
        redis_url=redis_url,
        credentials_key=key,
        agents_fake_backend=True,
        agents_scratch_dir=str(scratch),
    )


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


def context(settings: Settings, speed: float = 0.01) -> dict[str, Any]:
    ctx = worker.make_context(settings)
    ctx["agent_backend"] = Backends(speed=speed)
    ctx["agent_cancel_poll_s"] = 0.05
    return ctx


def status(factory: sessionmaker[Session], run_id: uuid.UUID) -> RunStatus:
    return run_row(factory, run_id).status


async def enqueue(workers: list[Worker], run_id: uuid.UUID) -> None:
    agents = next(w for w in workers if w.queue_name == queues.AGENTS)
    await agents.pool.enqueue_job(
        "run_agent", str(run_id), _job_id=queues.agent_run_job_id(run_id), _queue_name=queues.AGENTS
    )


def test_a_run_started_through_the_api_ends_done(
    settings: Settings, factory: sessionmaker[Session]
) -> None:
    app = create_app(settings)
    make_user(factory, email=ALICE, name="Alice")
    with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="*")) as client:
        api = Api(client)
        token = api.login_token(ALICE)
        api.request(
            "PUT",
            "/api/me/model-credential",
            token=token,
            json={"kind": "anthropic_api_key", "secret": API_KEY},
        )
        response = api.post(
            "/api/agents/runs", token=token, json={"agent": "echo", "input": {"message": "hi"}}
        )
        assert response.status_code == 202, response.text
        run_id = uuid.UUID(response.json()["id"])

        async def scenario(_: list[Worker]) -> None:
            await wait_for(lambda: status(factory, run_id) is RunStatus.done)

        serve_until(settings, context(settings), scenario)
        body = api.get(f"/api/agents/runs/{run_id}", token=token).json()
    assert body["status"] == "done"
    assert body["output"] == {"text": "You said: hi"}
    assert [e["kind"] for e in body["events"]][-1] == "status"
    assert body["cost_usd"] == pytest.approx(0.0123)


def test_at_most_two_runs_at_once_and_a_third_waits(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    users = [add_user(factory, f"u{i}@example.org") for i in range(3)]
    for user in users:
        save_credential(factory, keyring, user)
    run_ids = [new_run(factory, user, {"message": "x", "script": "slow"}) for user in users]
    peak: list[int] = []

    async def scenario(workers: list[Worker]) -> None:
        for run_id in run_ids:
            await enqueue(workers, run_id)

        def all_ended() -> bool:
            states = [status(factory, run_id) for run_id in run_ids]
            peak.append(states.count(RunStatus.running))
            return all(state is RunStatus.done for state in states)

        await wait_for(lambda: [status(factory, r) for r in run_ids].count(RunStatus.running) == 2)
        assert [status(factory, r) for r in run_ids].count(RunStatus.queued) == 1
        await wait_for(all_ended, within=60)

    # 30 steps of 1 s at speed 0.05: about 1.5 s per run.
    serve_until(settings, context(settings, speed=0.05), scenario, timeout=90)
    assert max(peak) == 2
    for run_id in run_ids:
        assert status(factory, run_id) is RunStatus.done


def test_cancel_through_the_api_stops_a_running_run(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    alice = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, alice)
    run_id = new_run(factory, alice, {"message": "x", "script": "slow"})

    async def scenario(workers: list[Worker]) -> None:
        await enqueue(workers, run_id)
        await wait_for(lambda: status(factory, run_id) is RunStatus.running)
        with factory() as db:
            assert runs.request_cancel(db, alice, run_id, now()) is runs.CancelOutcome.requested
            db.commit()
        await wait_for(lambda: status(factory, run_id) is RunStatus.cancelled, within=10)

    serve_until(settings, context(settings, speed=1.0), scenario)
    assert run_row(factory, run_id).finished_at is not None
    assert list(Path(settings.agents_scratch_dir or "").iterdir()) == []


def test_runs_left_running_are_failed_when_the_worker_starts(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    alice = add_user(factory, "alice@example.org")
    orphan = new_run(factory, alice)
    queued = new_run(factory, alice)
    with factory() as db:
        runs.claim(db, orphan, now())
        db.commit()

    async def scenario(_: list[Worker]) -> None:
        return None

    serve_until(settings, context(settings), scenario)
    row = run_row(factory, orphan)
    assert (row.status, row.error) == (RunStatus.failed, runs.INTERRUPTED)
    assert status(factory, queued) is RunStatus.queued  # never enqueued: left for the reaper


def test_stopping_the_worker_fails_its_running_run(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    alice = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, alice)
    run_id = new_run(factory, alice, {"message": "x", "script": "slow"})

    async def scenario(workers: list[Worker]) -> None:
        await enqueue(workers, run_id)
        await wait_for(lambda: status(factory, run_id) is RunStatus.running)
        await asyncio.sleep(0.3)

    serve_until(settings, context(settings, speed=1.0), scenario)
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.failed, runs.STOPPED)
    assert list(Path(settings.agents_scratch_dir or "").iterdir()) == []


def test_reaper_cron_function(settings: Settings, factory: sessionmaker[Session]) -> None:
    ctx = worker.make_context(settings)
    try:
        assert asyncio.run(worker.reap_agent_runs(ctx)) == 0
        alice = add_user(factory, "alice@example.org")
        run_id = new_run(factory, alice)
        with factory() as db:
            runs.claim(db, run_id, now())
            db.commit()
        assert asyncio.run(worker.reap_agent_runs_at_start(ctx)) == 1
        assert status(factory, run_id) is RunStatus.failed
    finally:
        ctx["engine"].dispose()


def test_agent_context_defaults(settings: Settings) -> None:
    from app.agents import fake_backend, runner

    ctx = worker.make_context(settings)
    try:
        rc = worker.agent_context(ctx)
        assert rc.backend_factory is fake_backend.FakeBackend  # AGENTS_FAKE_BACKEND is set
        assert rc.cancel_poll_s == runner.CANCEL_POLL_S
        assert rc.keyring is not None
        assert worker.agents_queue().max_jobs == worker.MAX_AGENT_RUNS == 2
    finally:
        ctx["engine"].dispose()
