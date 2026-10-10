"""The `worker` service: consumes the Redis queues with arq. Run with `python -m app.worker`;
`python -m app.worker --check` is the container healthcheck (exit 0 when every queue's worker
recorded its health in Redis within HEALTH_INTERVAL_S).

Its own image, `jobapp-worker` (the `worker` target of api/Dockerfile: the api's code plus the
Claude Agent SDK and its bundled Claude Code CLI). One process, one event loop, one arq Worker per
queue (queues()): each queue has its own functions, cron jobs, concurrency and job timeout, and
they share the database engine, the clock and the credentials keyring through the job context.

Sweeps queue (app.queue.SWEEPS):
- run_sweep(sweep_id): one sweep (app/jobs/sweep.py), at most MAX_SWEEPS at a time; tried at
  most twice, so a sweep whose worker died is picked once more and failed at once (claim).
- scheduled_sweeps: cron every 5 minutes (SCHEDULE_MINUTES); starts a `schedule` sweep for every
  (user, slot) due in the window before the tick (sweep.scheduled_due, app/jobs/schedule.py:
  only users who enabled `search.sweep_schedule`, the default is off) and queues it under the
  job id schedule:<user>:<slot>. A slot fires once (advisory lock and the slot recorded in the
  sweep); a user already sweeping is skipped and the slot retried by the next ticks of the
  window.
- reap_sweeps: cron every minute, and once at start: fails running sweeps with no heartbeat for
  sweep.STALE_AFTER (worker crash, lost queue message).

Agents queue (app.queue.AGENTS, agents A2, app/agents/runner.py):
- run_agent(run_id): one agent run, at most MAX_AGENT_RUNS (2) at a time, so the Jetson's RAM
  holds two Claude Code processes next to the sweeps within the 1536m limit; a third waits in
  the queue. Tried once: a run whose worker died is never started again behind the user's back.
- reap_agent_runs: once at start (every run still `running` belonged to the process that died:
  failed as interrupted) and as a cron every minute (runs running far past any limit, runs queued
  so long that their message must be lost).

Shutdown (SIGTERM from docker stop, SIGINT): every Worker stops picking jobs and cancels the
running ones; a cancelled sweep is failed with "interrupted" (sweep.run_sweep) before exit, a
cancelled agent run likewise (runner.execute), its scratch directory removed.
"""

import argparse
import asyncio
import logging
import signal
import sys
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from arq import cron, func
from arq.connections import ArqRedis, create_pool
from arq.cron import CronJob
from arq.worker import Function, Worker
from sqlalchemy.orm import Session, sessionmaker

from app import queue
from app.agents import definitions
from app.agents import runs as agent_runs
from app.config import Settings, load_settings
from app.db import build_engine, build_sessionmaker
from app.identity.service import utcnow
from app.jobs import ats, service, sweep
from app.logging_config import configure_logging

logger = logging.getLogger("app.worker")

MAX_SWEEPS = 2
# Board bodies downloaded and decoded at once, across every sweep of the process: with
# ats.MAX_BODY (25 MB) and their decoded JSON, the worst case stays far under the 1536m limit.
BODIES_IN_FLIGHT = 4
SCHEDULE_MINUTES = set(range(0, 60, 5))  # schedule.TICK
HEALTH_INTERVAL_S = 10
SWEEP_JOB_TIMEOUT_S = sweep.SWEEP_TIMEOUT_S + 60
MAX_AGENT_RUNS = 2
# The runner stops a run at its wall time and gives the stream its grace to end; arq's own timeout
# only catches a runner stuck beyond that.
LONGEST_AGENT_S = max(agent.wall_time_s for agent in definitions.AGENTS.values())
AGENT_JOB_TIMEOUT_S = LONGEST_AGENT_S + 120
AGENT_STALE_RUNNING = timedelta(seconds=LONGEST_AGENT_S + 300)
AGENT_STALE_QUEUED = timedelta(hours=6)


# --- job context ------------------------------------------------------------------------------


def sessions_of(ctx: dict[str, Any]) -> sessionmaker[Session]:
    found: sessionmaker[Session] = ctx["sessions"]
    return found


def clock_of(ctx: dict[str, Any]) -> Callable[[], datetime]:
    found: Callable[[], datetime] = ctx["clock"]
    return found


def ats_client(ctx: dict[str, Any]) -> ats.AtsClient:
    """A fresh client per sweep (tests put a factory with a fake transport in the context)."""
    factory = ctx.get("ats_client")
    if factory is not None:
        client: ats.AtsClient = factory()
        return client
    settings: Settings = ctx["settings"]
    return ats.AtsClient(
        tz=sweep.SWEEP_TZ,
        stub_base_url=settings.ats_base_url,
        stub_org_prefix=settings.ats_base_url_org_prefix,
        body_gate=ctx.get("body_gate"),
    )


# --- sweeps queue -----------------------------------------------------------------------------


async def run_sweep(ctx: dict[str, Any], sweep_id: str) -> None:
    async with ats_client(ctx) as client:
        await sweep.run_sweep(sessions_of(ctx), uuid.UUID(sweep_id), client, clock_of(ctx))


def _reap(sessions: sessionmaker[Session], now: datetime) -> int:
    with sessions() as db:
        count = sweep.reap_stale(db, now)
        db.commit()
    return count


async def reap_sweeps(ctx: dict[str, Any]) -> int:
    count = await asyncio.to_thread(_reap, sessions_of(ctx), clock_of(ctx)())
    if count:
        logger.warning("stale sweeps failed", extra={"count": count})
    return count


def _start_scheduled(
    sessions: sessionmaker[Session], now: datetime
) -> list[tuple[uuid.UUID, sweep.DueSweep]]:
    """A running schedule sweep for every (user, slot) due; slots already started and users
    already sweeping are skipped."""
    started = []
    with sessions() as db:
        due = sweep.scheduled_due(db, now)
    for item in due:
        with sessions() as db:
            try:
                row = sweep.start_scheduled(db, item, now)
                db.commit()
            except service.SweepRunningError:
                db.rollback()
                continue
            if row is not None:
                started.append((row.id, item))
    return started


async def scheduled_sweeps(ctx: dict[str, Any]) -> int:
    sessions, clock = sessions_of(ctx), clock_of(ctx)
    started = await asyncio.to_thread(_start_scheduled, sessions, clock())
    redis: ArqRedis = ctx["redis"]
    for sweep_id, item in started:
        try:
            await redis.enqueue_job(
                "run_sweep",
                str(sweep_id),
                _job_id=queue.schedule_job_id(item.user_id, item.slot.key),
                _queue_name=queue.SWEEPS,
            )
        except Exception:
            logger.exception("scheduled sweep not queued", extra={"sweep_id": str(sweep_id)})
            await asyncio.to_thread(sweep.fail_sweep, sessions, sweep_id, sweep.QUEUE_DOWN, clock())
    if started:
        logger.info("scheduled sweeps queued", extra={"count": len(started)})
    return len(started)


# --- agents queue -----------------------------------------------------------------------------


def agent_context(ctx: dict[str, Any]) -> Any:
    """The runner's view of the job context. Imported here, not at the top: the SDK lives in the
    worker image only, and `python -m app.worker --check` must not need it."""
    from app.agents import backend, runner

    settings: Settings = ctx["settings"]
    factory = ctx.get("agent_backend") or backend.for_settings(settings)
    return runner.RunContext(
        settings=settings,
        sessions=sessions_of(ctx),
        clock=clock_of(ctx),
        keyring=ctx.get("credentials_keyring"),
        backend_factory=factory,
        cancel_poll_s=ctx.get("agent_cancel_poll_s", runner.CANCEL_POLL_S),
        interrupt_grace_s=ctx.get("agent_interrupt_grace_s", runner.INTERRUPT_GRACE_S),
        agents=ctx.get("agent_definitions"),
    )


async def run_agent(ctx: dict[str, Any], run_id: str) -> str | None:
    from app.agents import runner

    status = await runner.execute(agent_context(ctx), uuid.UUID(run_id))
    return status.value if status is not None else None


def _reap_agent_runs(sessions: sessionmaker[Session], now: datetime, at_start: bool) -> int:
    with sessions() as db:
        if at_start:
            count = agent_runs.reap_orphans(db, now)
        else:
            count = agent_runs.reap_stale(db, now, AGENT_STALE_RUNNING, AGENT_STALE_QUEUED)
        db.commit()
    return count


async def reap_agent_runs(ctx: dict[str, Any], at_start: bool = False) -> int:
    count = await asyncio.to_thread(_reap_agent_runs, sessions_of(ctx), clock_of(ctx)(), at_start)
    if count:
        logger.warning("agent runs failed by the reaper", extra={"count": count, "start": at_start})
    return count


async def reap_agent_runs_at_start(ctx: dict[str, Any]) -> int:
    return await reap_agent_runs(ctx, at_start=True)


# --- queues -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class QueueSpec:
    """One queue and how the worker consumes it."""

    name: str
    functions: Sequence[Function]
    cron_jobs: Sequence[CronJob] = field(default_factory=tuple)
    max_jobs: int = 1
    job_timeout_s: float = 300
    # Run once before the Workers start. A failure is logged and the worker starts anyway: the
    # database may not be migrated yet on a first deploy, and the crons catch up later.
    on_start: Callable[[dict[str, Any]], Awaitable[Any]] | None = None


def sweeps_queue() -> QueueSpec:
    return QueueSpec(
        name=queue.SWEEPS,
        functions=[
            func(run_sweep, name="run_sweep", timeout=SWEEP_JOB_TIMEOUT_S, max_tries=2),
            func(scheduled_sweeps, name="scheduled_sweeps", timeout=240, max_tries=1),
            func(reap_sweeps, name="reap_sweeps", timeout=60, max_tries=1),
        ],
        cron_jobs=[
            cron(
                scheduled_sweeps,
                name="cron:scheduled_sweeps",
                minute=SCHEDULE_MINUTES,
                second=0,
                timeout=240,
            ),
            cron(reap_sweeps, name="cron:reap_sweeps", second=30, timeout=60),
        ],
        # Two users' sweeps at once (each reads CONCURRENCY boards), plus room for the cron jobs.
        max_jobs=MAX_SWEEPS + 2,
        job_timeout_s=SWEEP_JOB_TIMEOUT_S,
        on_start=reap_sweeps,
    )


def agents_queue() -> QueueSpec:
    return QueueSpec(
        name=queue.AGENTS,
        functions=[
            func(run_agent, name="run_agent", timeout=AGENT_JOB_TIMEOUT_S, max_tries=1),
            func(reap_agent_runs, name="reap_agent_runs", timeout=60, max_tries=1),
        ],
        cron_jobs=[cron(reap_agent_runs, name="cron:reap_agent_runs", second=45, timeout=60)],
        # Two jobs at once: a third run waits in the queue (the reaper cron, a few milliseconds,
        # waits for a free slot too).
        max_jobs=MAX_AGENT_RUNS,
        job_timeout_s=AGENT_JOB_TIMEOUT_S,
        on_start=reap_agent_runs_at_start,
    )


def queues() -> list[QueueSpec]:
    """Every queue this worker consumes."""
    return [sweeps_queue(), agents_queue()]


def build_worker(spec: QueueSpec, settings: Settings, ctx: dict[str, Any]) -> Worker:
    return Worker(
        functions=list(spec.functions),
        queue_name=spec.name,
        cron_jobs=list(spec.cron_jobs),
        redis_settings=queue.redis_settings(settings.redis_url.get_secret_value()),
        handle_signals=False,
        max_jobs=spec.max_jobs,
        job_timeout=spec.job_timeout_s,
        keep_result=3600,
        max_tries=2,
        poll_delay=0.5,
        health_check_interval=HEALTH_INTERVAL_S,
        ctx=ctx,
        job_serializer=queue.serialize,
        job_deserializer=queue.deserialize,
        timezone=sweep.SWEEP_TZ,  # type: ignore[arg-type]  # arq evaluates cron in this zone
        log_results=False,
    )


# --- process ----------------------------------------------------------------------------------


def make_context(settings: Settings, clock: Callable[[], datetime] = utcnow) -> dict[str, Any]:
    from app.main import credentials_keyring

    engine = build_engine(settings)
    return {
        "settings": settings,
        "engine": engine,
        "sessions": build_sessionmaker(engine),
        "clock": clock,
        # Decrypts a user's model credential for the length of one agent run (agents A2).
        "credentials_keyring": credentials_keyring(settings),
    }


async def serve(
    settings: Settings,
    specs: Sequence[QueueSpec] | None = None,
    *,
    stop: asyncio.Event | None = None,
    ctx: dict[str, Any] | None = None,
    on_ready: Callable[[list[Worker]], Awaitable[None]] | None = None,
) -> None:
    """Run one Worker per queue until `stop` is set (SIGTERM or SIGINT when not given)."""
    ctx = ctx if ctx is not None else make_context(settings)
    ctx.setdefault("body_gate", asyncio.Semaphore(BODIES_IN_FLIGHT))
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = []
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop.set)
            installed.append(signum)
        except (NotImplementedError, RuntimeError):  # pragma: no cover, not the main thread
            pass
    specs = specs or queues()
    try:
        for spec in specs:
            if spec.on_start is None:
                continue
            try:
                await spec.on_start(ctx)
            except Exception:
                # The database may not be migrated yet (a first deploy migrates after `up`): the
                # minute crons reap later, so the worker starts anyway.
                logger.warning("start hook failed", extra={"queue": spec.name}, exc_info=True)
        workers = [build_worker(spec, settings, ctx) for spec in specs]
        runs = [asyncio.create_task(worker.async_run()) for worker in workers]
        # Each Worker connects to Redis in its own task: ready once every one has its pool.
        # arq offers no "ready" event to wait on, hence the short poll.
        while any(getattr(w, "_pool", None) is None for w in workers) and not any(  # noqa: ASYNC110
            run.done() for run in runs
        ):
            await asyncio.sleep(0.05)
        logger.info("worker started", extra={"queues": [w.queue_name for w in workers]})
        if on_ready is not None:
            await on_ready(workers)
        stopper = asyncio.create_task(stop.wait())
        await asyncio.wait([stopper, *runs], return_when=asyncio.FIRST_COMPLETED)
        stopper.cancel()
        for worker in workers:
            await worker.close()
        for run in runs:
            run.cancel()
        await asyncio.gather(*runs, return_exceptions=True)
        failed = [run for run in runs if run.done() and not run.cancelled() and run.exception()]
        if failed and not stop.is_set():
            raise RuntimeError("a queue worker stopped") from failed[0].exception()
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)
        if "engine" in ctx:
            ctx["engine"].dispose()
        logger.info("worker stopped")


async def check(settings: Settings, specs: Sequence[QueueSpec] | None = None) -> int:
    """0 when every queue's worker recorded its health recently, 1 otherwise."""
    try:
        async with asyncio.timeout(5):
            pool = await create_pool(queue.redis_settings(settings.redis_url.get_secret_value()))
            try:
                for spec in specs or queues():
                    if not await pool.exists(spec.name + ":health-check"):
                        print(f"{spec.name}: no recent health record", file=sys.stderr)
                        return 1
            finally:
                await pool.aclose()
    except Exception as exc:
        print(f"redis unreachable: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker", description=__doc__)
    parser.add_argument("--check", action="store_true", help="healthcheck: exit 0 when healthy")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.check:
        return asyncio.run(check(settings))
    configure_logging(settings.log_level)
    asyncio.run(serve(settings))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
