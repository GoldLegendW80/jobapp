"""The worker on a real Redis 7 (TEST_REDIS_URL or REDIS_URL, else a throwaway container) and a
real Postgres: a sweep queued by the api's Queue runs to its end, the healthcheck, the schedule
cron enqueueing each due slot once (frozen clock), job ids deduplicating, stale sweeps reaped at
start, a stop interrupting a running sweep cleanly, and a Redis that is down."""

import asyncio
import gc
import logging
import os
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import pytest
import redis.asyncio as aioredis
import sqlalchemy as sa
from arq.utils import to_unix_ms
from arq.worker import Worker
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app import worker
from app.config import Settings
from app.ingestion import jobs as ingestion_jobs
from app.jobs import sweep
from app.jobs.models import Job, Sweep, SweepStatus, SweepTrigger
from tests.conftest import make_settings
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import board, client, fake_ats, new_sweep, now, search, set_profile
from tests.pg import docker_available
from tests.redis_server import start_redis

__all__ = ["fake_ats"]

# How long the write of a job's end is held open for a stop to land in it.
END_WRITE_PAUSE_S = 0.3
# How long a thread goes on holding its connection once the Workers are being closed: the
# engine used to be disposed of within it.
THREAD_PAUSE_S = 0.3


def worker_redis_url(url: str) -> str:
    """Under pytest-xdist, worker gwN uses Redis database N + 1 of that server, so one worker's
    flushdb never empties another's queues; without xdist, the URL as given."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return url
    index = int(worker.removeprefix("gw")) + 1
    assert index < 16, "Redis has 16 databases: run at most 15 pytest-xdist workers"
    return urlsplit(url)._replace(path=f"/{index}").geturl()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    # CI gives a Redis service container in REDIS_URL; elsewhere a throwaway container.
    external = os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")
    if external:
        yield worker_redis_url(external)
        return
    if not docker_available():
        pytest.skip("Docker is not available and TEST_REDIS_URL is not set")
    server = start_redis()
    yield worker_redis_url(server.url)
    server.remove()


def flush_redis(url: str) -> None:
    """Empty this worker's Redis database. The client closes its connections on every path
    (async with), so no socket outlives the call: a leaked one surfaces later as a
    ResourceWarning, an error in whichever test the garbage collector happens to run under."""

    async def flush() -> None:
        async with aioredis.from_url(url) as client:
            await client.flushdb()

    asyncio.run(flush())


@pytest.fixture
def settings(migrated_url: str, redis_url: str, fake_ats: FakeAts) -> Iterator[Settings]:
    flush_redis(redis_url)
    return make_settings(
        migrated_url,
        redis_url=redis_url,
        ats_base_url=fake_ats.url,
        ats_base_url_org_prefix="fake-",
    )


def context(settings: Settings, fake: FakeAts, **options: Any) -> dict[str, Any]:
    ctx = worker.make_context(settings)
    ctx["ats_client"] = lambda: client(fake, **options)
    return ctx


def serve_until(
    settings: Settings,
    ctx: dict[str, Any],
    scenario: Callable[[list[Worker]], Awaitable[None]],
    timeout: float = 60,
) -> int:
    """Run the worker, play `scenario` once it is ready, then stop it: what serve() returned,
    the number of jobs, job ends and threads it left running at its bound (0: a clean stop)."""

    async def go() -> int:
        stop = asyncio.Event()
        failure: list[BaseException] = []

        async def ready(workers: list[Worker]) -> None:
            async def play() -> None:
                try:
                    await scenario(workers)
                except BaseException as exc:
                    failure.append(exc)
                finally:
                    stop.set()

            asyncio.get_running_loop().create_task(play())

        left = await asyncio.wait_for(
            worker.serve(settings, stop=stop, ctx=ctx, on_ready=ready), timeout
        )
        if failure:
            raise failure[0]
        return left

    return asyncio.run(go())


async def wait_for(predicate: Callable[[], bool], within: float = 30) -> None:
    deadline = time.monotonic() + within
    while not await asyncio.to_thread(predicate):
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.1)


def status_of(factory: sessionmaker[Session], sweep_id: uuid.UUID) -> SweepStatus:
    with factory() as db:
        row = db.get(Sweep, sweep_id)
        assert row is not None
        return row.status


# --- a sweep through the queue ------------------------------------------------------------------


def test_a_queued_sweep_runs_and_the_worker_reports_healthy(
    settings: Settings, factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-ok"), board("fake-ok2", "lever")))
    sweep_id = new_sweep(factory, alice)
    api_queue = queues.Queue(settings.redis_url.get_secret_value())

    async def scenario(workers: list[Worker]) -> None:
        try:
            await play(workers)
        finally:
            await api_queue.close()

    async def play(workers: list[Worker]) -> None:
        assert [w.queue_name for w in workers] == [
            queues.SWEEPS,
            queues.AGENTS,
            queues.INGESTION,
            queues.DOCUMENTS,
        ]
        assert await api_queue.enqueue(
            "run_sweep", str(sweep_id), queue=queues.SWEEPS, job_id=queues.sweep_job_id(sweep_id)
        )
        # The same id again is refused: a sweep is queued once.
        assert not await api_queue.enqueue(
            "run_sweep", str(sweep_id), queue=queues.SWEEPS, job_id=queues.sweep_job_id(sweep_id)
        )
        await wait_for(lambda: status_of(factory, sweep_id) is SweepStatus.done)
        deadline = time.monotonic() + 15
        while await worker.check(settings) != 0:
            assert time.monotonic() < deadline, "no health record"
            await asyncio.sleep(0.5)

    serve_until(settings, context(settings, fake_ats), scenario)
    with factory() as db:
        count = db.execute(
            sa.select(sa.func.count()).select_from(Job).where(Job.user_id == alice)
        ).scalar_one()
    assert count == 3
    # Stopped: the health record is deleted, the check fails.
    assert asyncio.run(worker.check(settings)) == 1


def test_stop_interrupts_a_running_sweep_cleanly(
    settings: Settings, factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(board("fake-timeout")))
    sweep_id = new_sweep(factory, alice)

    async def scenario(workers: list[Worker]) -> None:
        pool = workers[0].pool
        await pool.enqueue_job(
            "run_sweep",
            str(sweep_id),
            _job_id=queues.sweep_job_id(sweep_id),
            _queue_name=queues.SWEEPS,
        )

        def picked() -> bool:
            with factory() as db:
                row = db.get(Sweep, sweep_id)
                return bool(row and row.stats.get("picked_at"))

        def asked() -> bool:
            with fake_ats.state.lock:
                return any(path.endswith("/fake-timeout") for path in fake_ats.state.counts)

        await wait_for(picked)
        # Stop once the request is at the server, not while it is still connecting: a connect
        # cancelled half way leaves a socket that nobody owns, which surfaced as a ResourceWarning
        # in a later test (the flake of #104). The client owns the connection once it is made,
        # and closes it with the sweep.
        await wait_for(asked)

    serve_until(settings, context(settings, fake_ats, timeout_s=20.0), scenario)
    with factory() as db:
        row = db.get(Sweep, sweep_id)
        assert row is not None
        assert (row.status, row.error) == (SweepStatus.failed, sweep.INTERRUPTED)


def test_stop_while_a_job_is_being_picked_up_closes_every_worker(
    settings: Settings, fake_ats: FakeAts
) -> None:
    """A stop that cancels a job outside its function (arq's run_job still reading the job from
    Redis, the CI failure of 2026-10-10) used to make arq's Worker.close() raise CancelledError
    from its gather: serve() failed, and the next workers' Redis pools were never closed."""
    workers_seen: list[Worker] = []

    async def scenario(workers: list[Worker]) -> None:
        workers_seen.extend(workers)
        # What arq's poll leaves in `tasks` while run_job awaits Redis: a task whose cancellation
        # no job handler catches.
        for index, item in enumerate(workers):
            item.tasks[f"picking-{index}"] = asyncio.get_running_loop().create_task(
                asyncio.sleep(60)
            )

    serve_until(settings, context(settings, fake_ats), scenario)
    assert len(workers_seen) == 4  # sweeps, agents, ingestion, documents
    for item in workers_seen:
        assert item._pool is None  # closed, health key deleted
        assert all(task.done() for task in item.tasks.values())
    # A pool left open would surface here as an unclosed socket ResourceWarning (an error under
    # the suite's filterwarnings), not in some later test.
    workers_seen.clear()
    gc.collect()


def test_stop_while_a_job_writes_its_end_waits_for_the_write(
    settings: Settings, fake_ats: FakeAts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """arq shields the write of a job's end (its result, its in progress key, its place in the
    queue) from the job's cancellation. A stop landing during that write used to end the job's
    task and leave the write running by itself: close() then closed the Redis pool under it,
    the write reconnected on the closed pool or was dropped with the loop, and the socket it
    opened was never closed (the ResourceWarning of issues #104 and #117, and a job left in
    progress in Redis until its key expired)."""
    job_id = "ends-at-the-stop"
    written: list[str] = []
    finish_job = Worker.finish_job

    async def scenario(workers: list[Worker]) -> None:
        writing = asyncio.Event()

        async def slow_finish(self: Worker, ended: str, *args: Any, **kwargs: Any) -> None:
            if ended != job_id:
                return await finish_job(self, ended, *args, **kwargs)
            writing.set()
            await asyncio.sleep(
                END_WRITE_PAUSE_S
            )  # the stop arrives here, in the middle of the write
            await finish_job(self, ended, *args, **kwargs)
            written.append(ended)

        monkeypatch.setattr(Worker, "finish_job", slow_finish)
        await workers[0].pool.enqueue_job("reap_sweeps", _job_id=job_id, _queue_name=queues.SWEEPS)
        await asyncio.wait_for(writing.wait(), 30)

    serve_until(settings, context(settings, fake_ats), scenario)
    assert written == [job_id]

    async def left_in_redis() -> tuple[int, int, float | None]:
        async with aioredis.from_url(settings.redis_url.get_secret_value()) as redis:
            return (
                await redis.exists(f"arq:in-progress:{job_id}"),
                await redis.exists(f"arq:result:{job_id}"),
                await redis.zscore(queues.SWEEPS, job_id),
            )

    # The end is whole: no longer in progress, its result kept, out of the queue.
    assert asyncio.run(left_in_redis()) == (0, 1, None)
    # A write left behind would surface here as an unclosed socket ResourceWarning.
    gc.collect()


@pytest.mark.parametrize("job", ["reap_sweeps", "reap_documents"])
def test_stop_while_a_job_works_in_a_thread_waits_for_the_thread_before_the_engine_goes(
    job: str, settings: Settings, fake_ats: FakeAts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job that is cancelled stops waiting for its thread (asyncio.to_thread), and the thread
    goes on with its session. serve() used to dispose of the engine while that thread still
    held a connection: it came back to a pool already disposed of, which nobody closes, and was
    found open by the garbage collector during a later test (the `BaseConnection.__del__`
    warning of issue #104). One job of each module that owns a cron, both through plain
    asyncio.to_thread: `reap_sweeps` of worker.py, queued here, and `reap_documents` of
    ingestion/jobs.py, the cron every worker runs when it starts and four times a minute (PR
    #156, B1). The pause below bounds nothing of the fixed code, which waits for the thread:
    it only gives the code without the fix the time to dispose of the engine too early."""
    ctx = context(settings, fake_ats)
    engine = ctx["engine"]
    made: list[Any] = []
    sa.event.listen(engine, "connect", lambda connection, _record: made.append(connection))
    holding, closing = threading.Event(), threading.Event()
    order: list[str] = []
    checked_out: list[int] = []
    close = Worker.close
    dispose = engine.dispose

    def hold(db: Session) -> None:
        db.execute(sa.text("SELECT 1"))  # from here the thread holds a connection of the pool
        holding.set()
        closing.wait(30)  # the stop has cancelled the job, and closes the Workers
        time.sleep(THREAD_PAUSE_S)
        order.append("the thread ends")

    def held_sweeps(db: Session, _now: datetime) -> int:
        hold(db)
        return 0

    def held_documents(sessions: sessionmaker[Session], _now: datetime) -> ingestion_jobs.Reaped:
        if not holding.is_set():  # the first run only: the cron of the start
            with sessions() as db:
                hold(db)
        return ingestion_jobs.Reaped(queue=[], failed=0)

    async def closing_close(self: Worker) -> None:
        closing.set()
        await close(self)

    def watched_dispose(*args: Any, **kwargs: Any) -> None:
        order.append("the engine is disposed of")
        checked_out.append(engine.pool.checkedout())  # type: ignore[attr-defined]
        dispose(*args, **kwargs)

    monkeypatch.setattr(Worker, "close", closing_close)
    monkeypatch.setattr(engine, "dispose", watched_dispose)
    if job == "reap_documents":
        monkeypatch.setattr(ingestion_jobs, "reap", held_documents)

    async def scenario(workers: list[Worker]) -> None:
        if job == "reap_sweeps":
            # Not before: the start of the worker reaps too, and waits for it.
            monkeypatch.setattr(sweep, "reap_stale", held_sweeps)
            await workers[0].pool.enqueue_job("reap_sweeps", _queue_name=queues.SWEEPS)
        await wait_for(holding.is_set)

    left = serve_until(settings, ctx, scenario)
    assert order == ["the thread ends", "the engine is disposed of"]
    # Nothing was checked out of the pool when it was disposed of, and no connection the
    # engine ever made is left open.
    assert checked_out == [0]
    assert made
    assert [connection.closed for connection in made] == [True] * len(made)
    assert left == 0  # a clean stop: nothing was given up at the bound


def test_a_stop_is_bounded_when_a_thread_never_returns(
    settings: Settings,
    fake_ats: FakeAts,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every deploy stops the worker, and Compose kills it at the end of its grace period. A
    thread that does not return must not hold the stop until that kill: at the bound serve()
    says what it leaves, by function and count and nothing else, disposes of the engine, logs
    "worker stopped" and returns (PR #156, N1). The bound is set to half a second here and the
    thread would hold thirty: the margin is what a loaded machine adds, not part of the rule."""
    bound, margin = 0.5, 10.0
    monkeypatch.setattr(worker, "STOP_WAIT_S", bound)
    ctx = context(settings, fake_ats)
    holding, release, ended = threading.Event(), threading.Event(), threading.Event()
    disposed: list[bool] = []
    stopped_at: list[float] = []
    dispose = ctx["engine"].dispose

    def never(_db: Session, _now: datetime) -> int:  # no statement: no connection is held
        holding.set()
        release.wait(30)
        ended.set()
        return 0

    def watched_dispose(*args: Any, **kwargs: Any) -> None:
        disposed.append(ended.is_set())
        dispose(*args, **kwargs)

    async def scenario(workers: list[Worker]) -> None:
        monkeypatch.setattr(sweep, "reap_stale", never)
        monkeypatch.setattr(ctx["engine"], "dispose", watched_dispose)
        await workers[0].pool.enqueue_job("reap_sweeps", _queue_name=queues.SWEEPS)
        await wait_for(holding.is_set)
        stopped_at.append(time.monotonic())

    try:
        with caplog.at_level(logging.INFO, logger="app.worker"):
            left = serve_until(settings, ctx, scenario)
        took = time.monotonic() - stopped_at[0]
        assert not ended.is_set()  # serve() returned under a thread still running
        assert bound <= took < bound + margin
        assert left == 1
        assert disposed == [False]  # disposed of anyway, the thread still there
        said = [r for r in caplog.records if r.getMessage() == "worker stop bound reached"]
        assert len(said) == 1
        assert said[0].levelno == logging.WARNING
        assert (said[0].bound_s, said[0].jobs, said[0].job_ends) == (bound, 0, 0)  # type: ignore[attr-defined]
        assert said[0].threads == {"app.worker._reap": 1}  # type: ignore[attr-defined]
        messages = [r.getMessage() for r in caplog.records]
        assert messages[-2:] == ["worker stop bound reached", "worker stopped"]
    finally:
        release.set()
        assert ended.wait(30)


def test_a_clean_stop_leaves_the_loop_an_executor_and_names_nothing(
    settings: Settings, fake_ats: FakeAts, caplog: pytest.LogCaptureFixture
) -> None:
    """serve() gives asyncio.to_thread an executor of its own and joins it at the stop. The
    loop that called it goes on afterwards (the tests, a caller that serves twice): it must
    find an executor that still takes work, and a clean stop logs no bound."""

    async def go() -> tuple[int, int]:
        stop = asyncio.Event()

        async def ready(_workers: list[Worker]) -> None:
            assert await asyncio.to_thread(lambda: 1) == 1
            stop.set()

        ctx = context(settings, fake_ats)
        left = await asyncio.wait_for(
            worker.serve(settings, stop=stop, ctx=ctx, on_ready=ready), 60
        )
        return left, await asyncio.to_thread(lambda: 2)

    with caplog.at_level(logging.INFO, logger="app.worker"):
        assert asyncio.run(go()) == (0, 2)
    messages = [r.getMessage() for r in caplog.records]
    assert "worker stop bound reached" not in messages
    assert messages[-1] == "worker stopped"


def test_main_exits_at_once_when_the_stop_left_threads(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interpreter joins every thread of an executor before it exits: with one left at the
    bound, a plain return would hold the process until Compose kills it. main() flushes the
    logs and exits without that join; after a clean stop it returns as before."""
    events: list[Any] = []
    left = [2]

    async def served(_settings: Settings, _specs: Any) -> int:
        return left[0]

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "configure_logging", lambda _level: None)
    monkeypatch.setattr(worker, "drop_secrets_from_environ", lambda: [])
    monkeypatch.setattr(worker, "serve", served)
    monkeypatch.setattr(worker.logging, "shutdown", lambda: events.append("logs flushed"))
    monkeypatch.setattr(worker.os, "_exit", lambda code: events.append(("exit", code)))
    assert worker.main([]) == 0
    assert events == ["logs flushed", ("exit", 0)]
    events.clear()
    left[0] = 0
    assert worker.main([]) == 0
    assert events == []


def test_stale_sweeps_are_reaped_when_the_worker_starts(
    settings: Settings, factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    long_ago = datetime(2026, 1, 1, tzinfo=UTC)
    with factory() as db:
        row = Sweep(
            user_id=alice,
            trigger=SweepTrigger.manual,
            started_at=long_ago,
            stats={"picked_at": long_ago.isoformat(), "heartbeat_at": long_ago.isoformat()},
        )
        db.add(row)
        db.commit()

    async def scenario(_: list[Worker]) -> None:
        assert status_of(factory, row.id) is SweepStatus.failed

    serve_until(settings, context(settings, fake_ats), scenario)


# --- schedules ----------------------------------------------------------------------------------

TICK = datetime(2026, 10, 10, 4, 20, tzinfo=UTC)  # 06:20 in Paris
DAILY = {"enabled": True, "times": ["06:17"], "timezone": "Europe/Paris"}


def test_the_schedule_cron_enqueues_each_due_slot_once(
    settings: Settings,
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    bob: uuid.UUID,
    fake_ats: FakeAts,
) -> None:
    from tests.jobs.conftest import new_user

    carol = new_user(factory, "carol@example.org")
    set_profile(factory, alice, search(board("fake-ok"), sweep_schedule=DAILY))
    set_profile(
        factory,
        bob,
        search(board("fake-ok2", "lever"), sweep_schedule={**DAILY, "times": ["06:15"]}),
    )
    set_profile(factory, carol, search(board("fake-ok")))  # schedule off: never enqueued
    ctx = context(settings, fake_ats)
    ctx["clock"] = lambda: TICK

    async def scenario(workers: list[Worker]) -> None:
        sweeps_worker = workers[0]
        [cron_job] = [c for c in sweeps_worker.cron_jobs if c.name == "cron:scheduled_sweeps"]
        assert cron_job.minute == set(range(0, 60, 5))
        assert (cron_job.second, cron_job.unique) == (0, True)
        # Two cron passes over the same 5 minute mark (two workers, or a restart): arq's unique
        # job id lets one tick through. The mark is one still to come, as it is for the worker
        # (arq queues a tick within the second before its mark, deferred to the mark): the job
        # waits in the queue, where it is counted, and the worker's own poll, which reads the
        # real clock and runs beside this scenario, finds nothing due on this cron. With the
        # mark of 2026-10-10, once that day was over, each poll queued the next mark (06:25,
        # 06:30, ...) as a second job and ran it at once (issue #104).
        tick = datetime(2100, 10, 10, 6, 19, 59, 900_000, tzinfo=sweep.SWEEP_TZ)
        others = [c for c in sweeps_worker.cron_jobs if c is not cron_job]
        sweeps_worker.cron_jobs = [cron_job]
        try:
            for _ in range(2):
                cron_job.next_run = None
                cron_job.calculate_next(tick)
                mark = cron_job.next_run
                assert mark is not None
                assert (mark.hour, mark.minute, mark.second) == (6, 20, 0)
                await sweeps_worker.run_cron(tick, 0.5)
        finally:
            sweeps_worker.cron_jobs = [cron_job, *others]
        queued = await sweeps_worker.pool.queued_jobs(queue_name=queues.SWEEPS)
        assert [j.job_id for j in queued] == [f"cron:scheduled_sweeps:{to_unix_ms(mark)}"]

        # The tick itself, three times: each due slot starts once, under its own job id.
        redis_ctx = ctx | {"redis": sweeps_worker.pool}
        assert await worker.scheduled_sweeps(redis_ctx) == 2
        assert await worker.scheduled_sweeps(redis_ctx) == 0
        assert await worker.scheduled_sweeps(redis_ctx) == 0

        def all_done() -> bool:
            with factory() as db:
                rows = db.execute(sa.select(Sweep)).scalars().all()
                return len(rows) == 2 and all(r.status is SweepStatus.done for r in rows)

        await wait_for(all_done)
        for user_id, clock in ((alice, "06:17"), (bob, "06:15")):
            key = queues.schedule_job_id(user_id, f"2026-10-10T{clock} Europe/Paris")
            assert await sweeps_worker.pool.exists(f"arq:result:{key}")

    serve_until(settings, ctx, scenario)
    with factory() as db:
        rows = db.execute(sa.select(Sweep).order_by(Sweep.user_id)).scalars().all()
    assert sorted(r.user_id for r in rows) == sorted([alice, bob])
    assert {r.trigger for r in rows} == {SweepTrigger.schedule}
    assert {r.stats["schedule_slot"] for r in rows} == {
        "2026-10-10T06:17 Europe/Paris",
        "2026-10-10T06:15 Europe/Paris",
    }


def test_the_schedule_tick_skips_a_user_already_sweeping_and_fails_what_it_cannot_queue(
    settings: Settings,
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    bob: uuid.UUID,
    fake_ats: FakeAts,
) -> None:
    set_profile(factory, alice, search(board("fake-ok"), sweep_schedule=DAILY))
    set_profile(factory, bob, search(board("fake-ok"), sweep_schedule=DAILY))
    with factory() as db:
        manual = sweep.start(db, alice, SweepTrigger.manual, TICK).id
        db.commit()

    class Broken:
        async def enqueue_job(self, *_: Any, **__: Any) -> None:
            raise ConnectionError("redis gone")

    ctx = context(settings, fake_ats)
    ctx["clock"] = lambda: TICK
    try:
        assert asyncio.run(worker.scheduled_sweeps(ctx | {"redis": Broken()})) == 1
    finally:
        ctx["engine"].dispose()
    with factory() as db:
        bobs = db.execute(sa.select(Sweep).where(Sweep.user_id == bob)).scalar_one()
        assert (bobs.status, bobs.error) == (SweepStatus.failed, sweep.QUEUE_DOWN)
        assert db.get(Sweep, manual).status is SweepStatus.running


# --- the queue from the api's side ---------------------------------------------------------------


def test_queue_down_is_reported_fast() -> None:
    q = queues.Queue("redis://127.0.0.1:1/0")
    started = time.monotonic()
    with pytest.raises(queues.QueueUnavailableError):
        asyncio.run(q.enqueue("run_sweep", "x", queue=queues.SWEEPS, job_id="sweep:x"))
    assert time.monotonic() - started < 6


def test_messages_are_json_not_pickle(settings: Settings) -> None:
    async def go() -> bytes:
        q = queues.Queue(settings.redis_url.get_secret_value())
        try:
            await q.enqueue("run_sweep", "abc", queue=queues.SWEEPS, job_id="sweep:abc")
            pool = await q.pool()
            raw: bytes = await pool.get("arq:job:sweep:abc")
        finally:
            await q.close()
        return raw

    raw = asyncio.run(go())
    assert raw.startswith(b"{")
    assert b'"run_sweep"' in raw
    assert b'"abc"' in raw


def test_check_without_redis_fails(migrated_url: str) -> None:
    dead = make_settings(migrated_url, redis_url="redis://127.0.0.1:1/0")
    assert asyncio.run(worker.check(dead)) == 1


def test_main_check_uses_the_environment(
    monkeypatch: pytest.MonkeyPatch, migrated_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated_url)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    assert worker.main(["--check"]) == 1


def test_production_refuses_the_ats_stub(migrated_url: str) -> None:
    from app.config import ats_stub_for

    with pytest.raises(ValueError, match="must not be set in production"):
        Settings(
            database_url=migrated_url,  # type: ignore[arg-type]
            app_env="production",
            secret_key="x" * 40,  # type: ignore[arg-type]
            ats_base_url="http://fakeats:8099",
        )
    with pytest.raises(ValueError, match="REDIS_URL"):
        make_settings(migrated_url, redis_url="http://redis:6379")
    every = make_settings(migrated_url, ats_base_url="http://stub:8099/")
    some = make_settings(
        migrated_url, ats_base_url="http://stub:8099", ats_base_url_org_prefix=" fake- "
    )
    none = make_settings(migrated_url, ats_base_url_org_prefix="fake-")
    assert ats_stub_for(every, "mistral.ai") == "http://stub:8099"
    assert ats_stub_for(some, "fake-ok") == "http://stub:8099"
    assert ats_stub_for(some, "mistral.ai") is None
    assert ats_stub_for(none, "fake-ok") is None
    assert now().tzinfo is not None
