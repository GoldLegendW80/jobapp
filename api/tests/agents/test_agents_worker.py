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
from arq.connections import ArqRedis
from arq.constants import in_progress_key_prefix, retry_key_prefix
from arq.worker import Worker, func
from fastapi.testclient import TestClient
from redis.asyncio.client import Pipeline
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


def test_stopping_the_worker_in_a_poll_gives_every_pipeline_its_connection_back(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The poll holds a WATCH pipeline whose end waits for UNWATCH. A stop that cancels the poll
    there used to leave the pipeline holding its connection (issue #104). Here the wait is held
    until the poll task is done, which is the cancel, whatever the load."""
    made: list[Pipeline] = []
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    init, reset = Pipeline.__init__, Pipeline.reset

    def tracked(self: Pipeline, *args: Any, **kwargs: Any) -> None:
        init(self, *args, **kwargs)
        made.append(self)

    async def held(self: Pipeline) -> None:
        if self.watching:
            entered.set()
            await cancelled.wait()
        await reset(self)

    monkeypatch.setattr(Pipeline, "__init__", tracked)
    monkeypatch.setattr(Pipeline, "reset", held)

    async def scenario() -> None:
        ctx = worker.make_context(settings)
        try:
            queue_worker = worker.build_worker(worker.agents_queue(), settings, ctx)

            async def watching(job_ids: list[bytes]) -> None:
                async with queue_worker.pool.pipeline(transaction=True) as pipe:
                    await pipe.watch("never-set")

            monkeypatch.setattr(queue_worker, "start_jobs", watching)
            poll = queue_worker.main_task = asyncio.create_task(queue_worker.main())
            poll.add_done_callback(lambda _: cancelled.set())
            await entered.wait()
            assert await worker.close_worker(queue_worker) == (0, 0)
            assert [pipe.connection for pipe in made if pipe.connection is not None] == []
        finally:
            ctx["engine"].dispose()

    asyncio.run(scenario())


def test_a_stop_during_the_queue_read_starts_no_job_and_spends_no_try(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """arq reads allow_pick_jobs once, before the queue read. The iteration the stop shields must
    not go on to start the job it reads: the job stays queued, with no try spent. The read is
    held until the poll task is done, which is the cancel, whatever the load."""
    started: list[str] = []
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    read = ArqRedis.zrangebyscore

    async def held(self: ArqRedis, name: str, *args: Any, **kwargs: Any) -> Any:
        ids = await read(self, name, *args, **kwargs)
        if name == queues.AGENTS:
            entered.set()
            await cancelled.wait()
        return ids

    async def never(ctx: dict[str, Any], run_id: str) -> None:
        started.append(run_id)

    monkeypatch.setattr(ArqRedis, "zrangebyscore", held)

    async def scenario() -> None:
        job_id = "agent:stop-in-the-queue-read"
        api_queue = queues.Queue(settings.redis_url.get_secret_value())
        ctx = worker.make_context(settings)
        try:
            queue_worker = worker.build_worker(worker.agents_queue(), settings, ctx)
            queue_worker.functions["run_agent"] = func(never, name="run_agent")
            assert await api_queue.enqueue("run_agent", "x", queue=queues.AGENTS, job_id=job_id)
            poll = queue_worker.main_task = asyncio.create_task(queue_worker.main())
            poll.add_done_callback(lambda _: cancelled.set())
            await entered.wait()
            assert await worker.close_worker(queue_worker) == (0, 0)
            pool = await api_queue.pool()
            assert started == []
            assert not await pool.exists(retry_key_prefix + job_id)
            assert not await pool.exists(in_progress_key_prefix + job_id)
            assert await pool.zscore(queues.AGENTS, job_id) is not None
        finally:
            await api_queue.close()
            ctx["engine"].dispose()

    asyncio.run(scenario())


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
