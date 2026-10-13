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

Ingestion queue (app.queue.INGESTION, ingestion G2b, app/ingestion/jobs.py):
- parse_document(document_id): reads one uploaded document. One at a time (max_jobs 1): the
  parser service runs one job at a time too, and the bytes of one document are in this
  process while it is read. Tried once by arq; its result is not kept in Redis.
- reap_documents: cron every 15 seconds, and once at start: queues the pending documents that
  no job holds (a lost job, a document stored before its kind had a reader), queues again the
  ones whose try failed for the server, and fails the ones whose last try never came back.

Shutdown (SIGTERM from docker stop, SIGINT): every Worker stops picking jobs and cancels the
running ones; a cancelled sweep is failed with "interrupted" (sweep.run_sweep) before exit, a
cancelled agent run likewise (runner.execute), its scratch directory removed. Every Worker is
then closed (close_worker), even when a job was cancelled while still being picked up.
"""

import argparse
import asyncio
import logging
import os
import re
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
from app.ingestion import jobs as ingestion_jobs
from app.jobs import ats, service, sweep
from app.logging_config import configure_logging

logger = logging.getLogger("app.worker")

MAX_SWEEPS = 2
# Board bodies downloaded and decoded at once, across every sweep of the process: with
# ats.MAX_BODY (25 MB) and their decoded JSON, the worst case stays far under the 1536m limit.
BODIES_IN_FLIGHT = 4
SCHEDULE_MINUTES = set(range(0, 60, 5))  # schedule.TICK
# TEST_FAST_TIMERS (tests only, refused in production): every minute, and a short board timeout
# with a single attempt. The slots are idempotent, so a faster tick never fires one twice.
# A stack running with it (the CI e2e one) therefore proves neither the retry and backoff path
# nor the five minute tick: the unit tests of tests/jobs do. If a large board starts timing out
# on a slow runner, raise FAST_ATS_TIMEOUT_S first.
FAST_SCHEDULE_MINUTES = set(range(60))
FAST_ATS_TIMEOUT_S = 2.0
HEALTH_INTERVAL_S = 10
SWEEP_JOB_TIMEOUT_S = sweep.SWEEP_TIMEOUT_S + 60
MAX_AGENT_RUNS = 2
# The runner stops a run at its wall time and gives the stream its grace to end; arq's own timeout
# only catches a runner stuck beyond that.
AGENT_JOB_MARGIN_S = 120
AGENT_STALE_MARGIN_S = 300
AGENT_STALE_QUEUED = timedelta(hours=6)
INGESTION_ADVANCE_SECOND = 20  # the tick of the ingestion chain, once a minute


class AgentTooLongError(RuntimeError):
    """A registered agent may run longer than the job timeout of the agents queue."""


def register_ingestion_agents() -> None:
    """Make this process know the ingestion agents and the engine that chains them (ingestion
    G10): the import registers `profile_extract`, its tools and the `on_final` hook. Called
    where the agents queue is built and where a run's context is made, not at the top of this
    file: `python -m app.worker --check` must not need the SDK."""
    from app.ingestion import engine  # noqa: F401


def longest_agent_s() -> float:
    """The longest wall time of any registered agent. Computed when it is asked, never as a
    module constant: an agent registered after this file was imported counts too."""
    return max(agent.wall_time_s for agent in definitions.AGENTS.values())


def agent_job_timeout_s() -> float:
    return longest_agent_s() + AGENT_JOB_MARGIN_S


def agent_stale_running() -> timedelta:
    return timedelta(seconds=longest_agent_s() + AGENT_STALE_MARGIN_S)


def check_agents_fit(job_timeout_s: float) -> None:
    """Refuse to start when a registered agent can outlive the job timeout: arq would cut its
    run as `STOPPED`, no hook would be called, and the step would run and be paid twice."""
    for agent in definitions.AGENTS.values():
        if agent.wall_time_s > job_timeout_s:
            raise AgentTooLongError(
                f"agent {agent.name} may run {agent.wall_time_s:.0f} s, "
                f"longer than the job timeout of {job_timeout_s:.0f} s"
            )


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
    fast: dict[str, Any] = (
        {"timeout_s": FAST_ATS_TIMEOUT_S, "attempts": 1, "backoff_s": 0.1}
        if settings.test_fast_timers
        else {}
    )
    return ats.AtsClient(
        tz=sweep.SWEEP_TZ,
        stub_base_url=settings.ats_base_url,
        stub_org_prefix=settings.ats_base_url_org_prefix,
        body_gate=ctx.get("body_gate"),
        **fast,
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
    from app.agents import backend, live, runner
    from app.ingestion import engine as ingestion_engine

    settings: Settings = ctx["settings"]
    # What the `on_final` hook of the ingestion agents needs to start the next step.
    services: dict[str, Any] = {}
    if ctx.get("redis") is not None:
        services[ingestion_engine.ENQUEUE_SERVICE] = ingestion_engine.pool_enqueue(ctx["redis"])
    factory = ctx.get("agent_backend") or backend.for_settings(settings)
    # The answer being written goes to Redis for the api's run stream (issue #33): arq puts its
    # pool in the job context. Without one, or with AGENTS_LIVE_TEXT off, no live text.
    store = ctx.get("agent_live_store")
    if store is None and ctx.get("redis") is not None:
        pool = ctx["redis"]
        store = live.RedisLiveStore(lambda: pool)
    return runner.RunContext(
        settings=settings,
        sessions=sessions_of(ctx),
        clock=clock_of(ctx),
        keyring=ctx.get("credentials_keyring"),
        backend_factory=factory,
        cancel_poll_s=ctx.get("agent_cancel_poll_s", runner.CANCEL_POLL_S),
        interrupt_grace_s=ctx.get("agent_interrupt_grace_s", runner.INTERRUPT_GRACE_S),
        agents=ctx.get("agent_definitions"),
        live_store=store if settings.agents_live_text else None,
        services=services,
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
            count = agent_runs.reap_stale(db, now, agent_stale_running(), AGENT_STALE_QUEUED)
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


def sweeps_queue(fast: bool = False) -> QueueSpec:
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
                minute=FAST_SCHEDULE_MINUTES if fast else SCHEDULE_MINUTES,
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
    # The limits are computed here, after the import that registers the ingestion agents: a
    # 15 minute step under a timeout computed before it would be cut at 12 minutes.
    register_ingestion_agents()
    job_timeout_s = agent_job_timeout_s()
    check_agents_fit(job_timeout_s)
    return QueueSpec(
        name=queue.AGENTS,
        functions=[
            func(run_agent, name="run_agent", timeout=job_timeout_s, max_tries=1),
            func(reap_agent_runs, name="reap_agent_runs", timeout=60, max_tries=1),
        ],
        cron_jobs=[cron(reap_agent_runs, name="cron:reap_agent_runs", second=45, timeout=60)],
        # Two jobs at once: a third run waits in the queue (the reaper cron, a few milliseconds,
        # waits for a free slot too).
        max_jobs=MAX_AGENT_RUNS,
        job_timeout_s=job_timeout_s,
        on_start=reap_agent_runs_at_start,
    )


# --- ingestion queue (G2b; G10 adds its `advance` cron to the same block) ----------------------


async def advance_ingestion_runs(ctx: dict[str, Any]) -> int:
    """The tick of the ingestion chain (G10): every run that is not final is advanced. It is the
    only guarantee that a chain moves: the `on_final` hook of a step is a shortcut that is not
    called for a run a reaper failed, nor when the worker dies. Answers a count, nothing else."""
    from app.ingestion import engine as ingestion_engine

    pool = ctx.get("redis")
    enqueue = ingestion_engine.pool_enqueue(pool) if pool is not None else None
    return await ingestion_engine.tick(sessions_of(ctx), enqueue, clock_of(ctx), ctx["settings"])


def ingestion_queue() -> QueueSpec:
    return QueueSpec(
        name=queue.INGESTION,
        functions=[
            # keep_result=0: nothing about a document's reading is left in Redis, and a try
            # whose job ended can be queued again under the same id by the reaper.
            func(
                ingestion_jobs.parse_document,
                name="parse_document",
                timeout=ingestion_jobs.JOB_TIMEOUT_S,
                max_tries=1,
                keep_result=0,
            ),
            func(
                ingestion_jobs.reap_documents,
                name="reap_documents",
                timeout=60,
                max_tries=1,
                keep_result=0,
            ),
            func(
                advance_ingestion_runs,
                name="advance_ingestion_runs",
                timeout=120,
                max_tries=1,
                keep_result=0,
            ),
        ],
        cron_jobs=[
            cron(
                advance_ingestion_runs,
                name="cron:advance_ingestion_runs",
                second=INGESTION_ADVANCE_SECOND,
                timeout=120,
                keep_result=0,
            ),
            cron(
                ingestion_jobs.reap_documents,
                name="cron:reap_documents",
                second=set(ingestion_jobs.REAP_SECONDS),
                timeout=60,
                # The first tick comes with the worker: the documents that waited for this
                # version are read at the deploy, not a quarter of a minute later.
                run_at_startup=True,
            ),
        ],
        # One document at a time; the reaper's tick waits its turn behind a reading.
        max_jobs=1,
        job_timeout_s=ingestion_jobs.JOB_TIMEOUT_S,
    )


def queues(settings: Settings | None = None) -> list[QueueSpec]:
    """Every queue this worker consumes (TEST_FAST_TIMERS shortens the schedule tick)."""
    fast = bool(settings is not None and settings.test_fast_timers)
    return [sweeps_queue(fast=fast), agents_queue(), ingestion_queue()]


def build_worker(spec: QueueSpec, settings: Settings, ctx: dict[str, Any]) -> Worker:
    if spec.name == queue.AGENTS:
        check_agents_fit(spec.job_timeout_s)  # an agent registered since the queue was built
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


async def close_worker(worker: Worker) -> None:
    """Stop one Worker: cancel its jobs still in flight, wait for them, then close it.

    arq's Worker.close() cancels them itself, then awaits them with a plain asyncio.gather. A job
    cancelled inside its function is caught by arq (run_job records it), but one cancelled while
    run_job still awaits Redis (a job just picked up) re-raises CancelledError from that gather:
    close() then stops before deleting the health key and closing the Redis pool, and serve()
    never reaches the next Worker. Draining the tasks here, with return_exceptions, leaves
    close() nothing to raise. Done tasks are dropped from `tasks` as arq's own poll does."""
    in_flight = list(worker.tasks.values())
    for task in in_flight:
        if not task.done():
            task.cancel()
    await asyncio.gather(*in_flight, return_exceptions=True)
    for job_id, task in list(worker.tasks.items()):
        if task.done():
            del worker.tasks[job_id]
    await worker.close()


async def serve(
    settings: Settings,
    specs: Sequence[QueueSpec] | None = None,
    *,
    stop: asyncio.Event | None = None,
    ctx: dict[str, Any] | None = None,
    on_ready: Callable[[list[Worker]], Awaitable[None]] | None = None,
) -> None:
    """Run one Worker per queue until `stop` is set (SIGTERM or SIGINT when not given)."""
    skip_sdk_version_probe()
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
    specs = specs or queues(settings)
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
            # One Worker failing to close must not leave the others' Redis pools open.
            try:
                await close_worker(worker)
            except Exception:
                logger.exception("queue worker close failed", extra={"queue": worker.queue_name})
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
                for spec in specs or queues(settings):
                    if not await pool.exists(spec.name + ":health-check"):
                        print(f"{spec.name}: no recent health record", file=sys.stderr)
                        return 1
            finally:
                await pool.aclose()
    except Exception as exc:
        print(f"redis unreachable: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


# Environment variables whose names say they hold a secret (DATABASE_URL, REDIS_URL,
# SECRET_KEY, CREDENTIALS_KEY, POSTGRES_PASSWORD, TS_AUTHKEY...).
_SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASSWD|AUTH|DSN|DATABASE_URL|REDIS_URL")


def skip_sdk_version_probe() -> None:
    """Before each run the Claude Agent SDK starts `claude -v` without env= (0.2.163,
    SubprocessCLITransport._check_claude_version), so that process would inherit this
    process's environment. The CLI is pinned by the wheel: skip the probe (the image sets it
    too)."""
    os.environ.setdefault("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")


def drop_secrets_from_environ() -> list[str]:
    """Remove the secrets from this process's environment once the settings hold them, so no
    child process (whatever the SDK spawns, now or in a later version) can inherit them. Returns
    the names removed (for the log; never the values)."""
    names = sorted(name for name in os.environ if _SECRET_NAME.search(name.upper()))
    for name in names:
        del os.environ[name]
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker", description=__doc__)
    parser.add_argument("--check", action="store_true", help="healthcheck: exit 0 when healthy")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.check:
        return asyncio.run(check(settings))
    configure_logging(settings.log_level)
    skip_sdk_version_probe()
    dropped = drop_secrets_from_environ()
    logger.info("secrets dropped from the environment", extra={"names": dropped})
    asyncio.run(serve(settings))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
