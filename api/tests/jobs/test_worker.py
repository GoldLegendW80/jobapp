"""The worker on a real Redis 7 (TEST_REDIS_URL or REDIS_URL, else a throwaway container) and a
real Postgres: a sweep queued by the api's Queue runs to its end, the healthcheck, the schedule
cron enqueueing each due slot once (frozen clock), job ids deduplicating, stale sweeps reaped at
start, a stop interrupting a running sweep cleanly, and a Redis that is down."""

import asyncio
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import redis.asyncio as aioredis
import sqlalchemy as sa
from arq.worker import Worker
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app import worker
from app.config import Settings
from app.jobs import sweep
from app.jobs.models import Job, Sweep, SweepStatus, SweepTrigger
from tests.conftest import make_settings
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import board, client, fake_ats, new_sweep, now, search, set_profile
from tests.pg import docker_available
from tests.redis_server import start_redis

__all__ = ["fake_ats"]


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    # CI gives a Redis service container in REDIS_URL; elsewhere a throwaway container.
    external = os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")
    if external:
        yield external
        return
    if not docker_available():
        pytest.skip("Docker is not available and TEST_REDIS_URL is not set")
    server = start_redis()
    yield server.url
    server.remove()


@pytest.fixture
def settings(migrated_url: str, redis_url: str, fake_ats: FakeAts) -> Iterator[Settings]:
    async def flush() -> None:
        r = aioredis.from_url(redis_url)
        await r.flushdb()
        await r.aclose()

    asyncio.run(flush())
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
) -> None:
    """Run the worker, play `scenario` once it is ready, then stop it."""

    async def go() -> None:
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

        await asyncio.wait_for(worker.serve(settings, stop=stop, ctx=ctx, on_ready=ready), timeout)
        if failure:
            raise failure[0]

    asyncio.run(go())


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
        assert [w.queue_name for w in workers] == [queues.SWEEPS]
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
        await api_queue.close()

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

        await wait_for(picked)

    serve_until(settings, context(settings, fake_ats, timeout_s=20.0), scenario)
    with factory() as db:
        row = db.get(Sweep, sweep_id)
        assert row is not None
        assert (row.status, row.error) == (SweepStatus.failed, sweep.INTERRUPTED)


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
        # job id lets one tick through.
        tick = datetime(2026, 10, 10, 6, 19, 59, 900_000, tzinfo=sweep.SWEEP_TZ)
        others = [c for c in sweeps_worker.cron_jobs if c is not cron_job]
        sweeps_worker.cron_jobs = [cron_job]
        try:
            for _ in range(2):
                cron_job.next_run = None
                cron_job.calculate_next(tick)
                await sweeps_worker.run_cron(tick, 0.5)
        finally:
            sweeps_worker.cron_jobs = [cron_job, *others]
        queued = await sweeps_worker.pool.queued_jobs(queue_name=queues.SWEEPS)
        assert [j.function for j in queued].count("cron:scheduled_sweeps") == 1

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
        await q.enqueue("run_sweep", "abc", queue=queues.SWEEPS, job_id="sweep:abc")
        pool = await q.pool()
        raw = await pool.get("arq:job:sweep:abc")
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
