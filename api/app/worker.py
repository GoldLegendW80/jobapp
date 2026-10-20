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
- cron:browser_removals: every minute, the profile directories queued for removal are removed
  through browserd (browser B7, app/browser/removals.py). On this queue for its free slots.

Agents queue (app.queue.AGENTS, agents A2, app/agents/runner.py):
- run_agent(run_id): one agent run, at most MAX_AGENT_RUNS (2) at a time; a third waits in
  the queue. The apply fill's answer agent (`apply_answer`) is not counted: it runs inside the
  fill, in this process, one fill at a time, so the worker holds at most three Claude Code
  processes next to the sweeps (about 145 MiB plus 3 x 240 MiB measured in A2 and A5, within
  the 1536m limit). Tried once: a run whose worker died is never started again behind the
  user's back.
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

Documents queue (app.queue.DOCUMENTS, documents D6, app/documents/render_job.py):
- render_document(document_id, revision): one render of a CV or a letter (the fit loop through
  the `renderer` service, 256m, one render at a time). One job at a time (max_jobs 1), under
  render_client's own gate as well; tried once by arq (render_job tries the renderer once more
  when it is unavailable); its result is not kept in Redis, so the same render can be queued
  again once it ended.
- resend_queued_renders: cron every minute: a render marked queued whose job was lost is queued
  again. At start, every render left `rendering` by a dead process goes back to idle as
  `interrupted` (reset_interrupted_at_start).
- documents_tick: cron every minute (documents D10, `documents.tick.run`), registered only once
  app/documents/tick.py exists.

Apply queue (app.queue.APPLY, apply A5, app/apply/run.py):
- apply_fill(application_id): the fill of one application form on a browser lease, one at a time
  (max_jobs 1: the browser is one); tried once by arq, its result not kept, so the tick can queue
  it again under the same id once it ended. Its answer agent (`apply_answer`, app/apply/agents.py)
  is one agent run executed inside the fill, in this process, never on the agents queue.
- apply_submit(application_id): the submit lease of one `approved` application (apply A6,
  app/apply/submit.py): refill, read back, the one click through the gate.
- apply_tick: cron every 30 seconds: asks a fill for the next `queued` application of every
  person whose fill is not running (a new one, one waiting for its documents or behind another
  person's lease, whose ticket lives 60 s), one per person so people take turns; and the submit
  job of every `approved` application (A6), every `held` one whose hold ended and every `ready`
  Full auto one, whose job runs Full auto's decision (A13a, `submit.due`).
  The fill's timeout is the `apply_fill` lease's ceiling (BROWSER_LEASE_MAX_S) plus the wait of a
  sign in hand off (BROWSER_SESSION_MAX_S, browser B12a) plus a margin, the submit's the
  `apply_submit` lease's ceiling plus a margin. At start, every application left `filling` by a dead
  process ends `failed` as `interrupted`, and every one left `submitting` ends `submit_unknown`:
  the click may have gone through (reset_fills_at_start).

Shutdown (SIGTERM from docker stop, SIGINT): every Worker stops picking jobs and cancels the
running ones; a cancelled sweep is failed with "interrupted" (sweep.run_sweep) before exit, a
cancelled agent run likewise (runner.execute), its scratch directory removed, a cancelled fill
likewise (apply run.fill, its lease released). Every Worker is
then closed (close_worker), even when a job was cancelled while still being picked up, and only
once the end of every job that was finishing is written (QueueWorker).
"""

import argparse
import asyncio
import contextlib
import functools
import importlib
import importlib.util
import logging
import math
import os
import re
import signal
import sys
import threading
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any

from arq import cron, func
from arq.connections import ArqRedis, create_pool
from arq.cron import CronJob
from arq.worker import Function, Worker
from sqlalchemy.orm import Session, sessionmaker

from app import queue
from app.agents import definitions
from app.agents import runs as agent_runs
from app.browser import removals as browser_removals
from app.config import Settings, get_settings
from app.db import build_engine, build_sessionmaker
from app.documents import render_job
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
# What a stop waits, in all, for the cancelled jobs, the job ends being written and the threads
# of asyncio.to_thread, before it disposes of the engine and returns whatever is left. Compose
# kills the worker 20 s after SIGTERM (`stop_grace_period: 20s` of the worker in compose.yml):
# 20 - 12 leaves 8 s for what comes after the wait and has timeouts of its own, the Redis pools
# to close, the engine to dispose of, the last log lines and the exit. A statement is ended by
# Postgres at 15 s (statement_timeout), so a thread stuck in one would outlast any bound that
# still lets the worker say why it stopped: that is the case the bound exists for.
STOP_WAIT_S = 12.0
INGESTION_ADVANCE_SECOND = 20  # the tick of the ingestion chain, once a minute
DOCUMENTS_RESEND_SECOND = 50  # the render queue's resend, once a minute
DOCUMENTS_TICK_SECOND = 5  # the automatic generation's tick (D10), once a minute
DOCUMENTS_TICK_TIMEOUT_S = 50


class AgentTooLongError(RuntimeError):
    """A registered agent may run longer than the job timeout of the agents queue."""


# The files of a module package (app/<module>/) that register something when they are imported:
# its agents and their tools (`agents`), the hook that chains its agents (`engine`, ingestion
# G10: `profile_extract`, its tools and the `on_final` hook), its arq jobs (`jobs`), its document
# writers (`writers`, documents D7), its status callbacks on the jobs service (`freeze`, documents
# D14). A module adds its file, not a line to this one (issue #279).
REGISTRATION_MODULES = ("agents", "engine", "freeze", "jobs", "writers")
APP_DIR = Path(__file__).resolve().parent


def registration_modules(app_dir: Path = APP_DIR) -> list[str]:
    """The dotted names of the registration modules of every package of app/, package by
    package in name order. Read from the files: nothing is imported."""
    found: list[str] = []
    for package in sorted(app_dir.iterdir()):
        if not (package / "__init__.py").is_file():
            continue
        for name in REGISTRATION_MODULES:
            if (package / f"{name}.py").is_file() or (package / name / "__init__.py").is_file():
                found.append(f"{app_dir.name}.{package.name}.{name}")
    return found


def register_modules() -> None:
    """Make this process know what every module registers, by importing its registration modules
    (registration_modules). Called where the queues are built, not at the top of this file:
    `python -m app.worker --check` must not need the SDK. A second call imports nothing anew."""
    for name in registration_modules():
        import_module(name)


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
        await sweep.run_sweep(
            sessions_of(ctx),
            uuid.UUID(sweep_id),
            client,
            clock_of(ctx),
            browser=browser_sources(ctx),
        )


def browser_sources(ctx: dict[str, Any]) -> sweep.BrowserHook | None:
    """The browser sources' run (browser B8): tests put theirs in the context."""
    if "browser_sources" in ctx:
        hook: sweep.BrowserHook | None = ctx["browser_sources"]
        return hook
    if "redis" not in ctx:
        return None
    from app.browser import sources

    return sources.runner_for(ctx["settings"], ctx["redis"], sessions_of(ctx))


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
            cron(browser_removals.drain, name="cron:browser_removals", second=20, timeout=60),
        ],
        # Two users' sweeps at once (each reads CONCURRENCY boards), plus room for the cron jobs.
        max_jobs=MAX_SWEEPS + 2,
        job_timeout_s=SWEEP_JOB_TIMEOUT_S,
        on_start=reap_sweeps,
    )


def agents_queue() -> QueueSpec:
    # The limits are computed here, after the imports that register the agents: a 15 minute
    # step of ingestion under a timeout computed before it would be cut at 12 minutes.
    register_modules()
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


# --- documents queue (D6; D10's tick joins it once its file exists) ----------------------------


def documents_queue() -> QueueSpec:
    register_modules()  # D7's writers set render_job.on_overflow in this process
    functions = [
        # keep_result=0: nothing of a render is left in Redis, and the same render can be
        # queued again under its id once its job ended.
        func(
            render_job.render_document,
            name=render_job.FUNCTION,
            timeout=render_job.JOB_TIMEOUT_S,
            max_tries=1,
            keep_result=0,
        ),
        func(
            render_job.resend_queued,
            name="resend_queued_renders",
            timeout=60,
            max_tries=1,
            keep_result=0,
        ),
    ]
    cron_jobs = [
        cron(
            render_job.resend_queued,
            name="cron:resend_queued_renders",
            second=DOCUMENTS_RESEND_SECOND,
            timeout=60,
            keep_result=0,
        )
    ]
    if importlib.util.find_spec("app.documents.tick") is not None:
        tick = importlib.import_module("app.documents.tick")
        functions.append(
            func(
                tick.run,
                name="documents_tick",
                timeout=DOCUMENTS_TICK_TIMEOUT_S,
                max_tries=1,
                keep_result=0,
            )
        )
        cron_jobs.append(
            cron(
                tick.run,
                name="cron:documents_tick",
                second=DOCUMENTS_TICK_SECOND,
                timeout=DOCUMENTS_TICK_TIMEOUT_S,
                keep_result=0,
            )
        )
    return QueueSpec(
        name=queue.DOCUMENTS,
        functions=functions,
        cron_jobs=cron_jobs,
        # One render at a time: the renderer renders one page at a time in its 256m, and the
        # bytes of a PDF are in this process while it is stored. The crons wait their turn.
        max_jobs=1,
        job_timeout_s=render_job.JOB_TIMEOUT_S,
        on_start=render_job.reset_interrupted_at_start,
    )


# --- apply queue (A5) -----------------------------------------------------------------------------

APPLY_TICK_SECONDS = {0, 30}
APPLY_JOB_MARGIN_S = 120


def apply_services(ctx: dict[str, Any]) -> Any:
    """The fill's services: tests put theirs in the context; else the lease store and the tools
    of browser B3 and B5 on this worker's Redis, and the answer agent (`apply_answer`, run in
    this process on the runner's context of `agent_context`). B4's `Live.agent_turn` goes in
    `before_tool` once B4 is merged."""
    from app.apply import agents as apply_agents
    from app.apply import run as apply_run

    given = ctx.get("apply_services")
    if given is not None:
        return given
    from app.browser.client import BrowserdClient
    from app.browser.leases import DbProfiles, Leases, lease_max_table
    from app.browser.tools import LeaseTools

    settings: Settings = ctx["settings"]
    redis = ctx["redis"]

    async def pool() -> ArqRedis:
        found: ArqRedis = redis
        return found

    secret = settings.browser_control_secret
    client = BrowserdClient(
        settings.browser_url, secret.get_secret_value() if secret is not None else None
    )
    leases = Leases(
        pool,
        client,
        DbProfiles(sessions_of(ctx)),
        idle_s=settings.browser_idle_s,
        session_max_s=settings.browser_session_max_s,
        max_table=lease_max_table(settings.browser_lease_max_s),
    )
    return apply_run.Services(
        sessions=sessions_of(ctx),
        clock=clock_of(ctx),
        leases=leases,
        tools_for=lambda user, lease, token: LeaseTools(leases, client, user, lease, token),
        answer_agent=apply_agents.RuntimeAnswerAgent(lambda: agent_context(ctx)),
        stub_base=settings.apply_stub_base_url,
    )


async def apply_fill(ctx: dict[str, Any], application_id: str) -> None:
    from app.apply import run as apply_run

    await apply_run.fill(apply_services(ctx), uuid.UUID(application_id))


async def apply_submit(ctx: dict[str, Any], application_id: str) -> None:
    """The submit lease of one approved application (apply A6, app/apply/submit.py)."""
    from app.apply import submit as apply_submit_run

    services = ctx.get("apply_submit_services") or apply_submit_run.from_fill(
        apply_services(ctx), ctx["settings"], ctx.get("redis")
    )
    await apply_submit_run.submit(services, uuid.UUID(application_id))


async def apply_tick(ctx: dict[str, Any]) -> int:
    """Queues a fill for every application waiting for one, and the submit of every approved
    one (apply A6). Answers a count, nothing else."""
    from app.apply import run as apply_run
    from app.apply import submit as apply_submit_run

    with sessions_of(ctx)() as db:
        due = apply_run.due(db)
        approved = apply_submit_run.due(db)
    redis: ArqRedis | None = ctx.get("redis")
    if redis is None:
        return 0
    queued = 0
    for application_id in due:
        job = await redis.enqueue_job(
            "apply_fill",
            str(application_id),
            _job_id=queue.apply_fill_job_id(application_id),
            _queue_name=queue.APPLY,
        )
        queued += job is not None
    for application_id in approved:
        job = await redis.enqueue_job(
            "apply_submit",
            str(application_id),
            _job_id=queue.apply_submit_job_id(application_id),
            _queue_name=queue.APPLY,
        )
        queued += job is not None
    return queued


async def reset_fills_at_start(ctx: dict[str, Any]) -> int:
    """No fill runs when the apply queue starts: an application left `filling` by a killed
    worker ends `failed` with `interrupted`."""
    from app.apply import run as apply_run
    from app.apply import submit as apply_submit_run

    clock = clock_of(ctx)

    def step(db: Session) -> int:
        # A6: a `submitting` left by a killed worker is `submit_unknown`, never freed.
        return apply_run.reset_interrupted(db, clock()) + apply_submit_run.reset_interrupted(
            db, clock()
        )

    count = await asyncio.to_thread(apply_run._in_db, sessions_of(ctx), step)
    if count:
        logger.info("apply.fill.reset", extra={"count": count})
    return count


def apply_queue(settings: Settings | None = None) -> QueueSpec:
    from app.browser.leases import LEASE_MAX_S, lease_max_table

    # The lease's own ceiling, BROWSER_LEASE_MAX_S included, and a margin to write the end.
    table = lease_max_table(settings.browser_lease_max_s) if settings is not None else LEASE_MAX_S
    session_max_s = (
        settings.browser_session_max_s
        if settings is not None
        else Settings.model_fields["browser_session_max_s"].default
    )
    # Browser B12a: a fill outlives a sign in hand off, whose wait stops the lease's clock.
    fill_timeout_s = table["apply_fill"] + session_max_s + APPLY_JOB_MARGIN_S
    submit_timeout_s = table["apply_submit"] + APPLY_JOB_MARGIN_S
    return QueueSpec(
        name=queue.APPLY,
        functions=[
            # keep_result=0: nothing of a fill is left in Redis, and the tick can queue the same
            # application again under its id once the job ended.
            func(apply_fill, name="apply_fill", timeout=fill_timeout_s, max_tries=1, keep_result=0),
            func(
                apply_submit,
                name="apply_submit",
                timeout=submit_timeout_s,
                max_tries=1,
                keep_result=0,
            ),
            func(apply_tick, name="apply_tick", timeout=60, max_tries=1, keep_result=0),
        ],
        cron_jobs=[
            cron(
                apply_tick,
                name="cron:apply_tick",
                second=APPLY_TICK_SECONDS,
                timeout=60,
                keep_result=0,
            )
        ],
        # One fill at a time: there is one browser. The tick waits its turn behind a fill.
        max_jobs=1,
        job_timeout_s=max(fill_timeout_s, submit_timeout_s),
        on_start=reset_fills_at_start,
    )


def queues(settings: Settings | None = None) -> list[QueueSpec]:
    """Every queue this worker consumes (TEST_FAST_TIMERS shortens the schedule tick)."""
    register_modules()
    fast = bool(settings is not None and settings.test_fast_timers)
    return [
        sweeps_queue(fast=fast),
        agents_queue(),
        ingestion_queue(),
        documents_queue(),
        apply_queue(settings),
    ]


class QueueWorker(Worker):
    """arq's Worker, which also knows the job ends it is still writing.

    arq writes the end of a job (its result, its in progress key, its place in the queue) in a
    task of its own, shielded from the job's cancellation, and keeps no reference to it. A stop
    that cancels a job during that write ends the job's task at once and leaves the write
    running: closing the Redis pool then cuts it, or it reconnects on the closed pool and its
    socket is never closed, and the job stays "in progress" in Redis until that key expires.
    `endings` holds those writes so that close_worker can wait for them.

    The poll is the other place where arq holds a Redis pipeline: a poll iteration reads the
    queue and starts jobs (a WATCH pipeline), records the health and enqueues the crons (WATCH
    pipelines again). redis-py's pipeline, when it ends, sends UNWATCH and waits for the answer
    before it gives its connection back. A stop that cancels the poll during that wait leaves
    the pipeline holding its connection: it is never given back, and the garbage collector
    reports "Unclosed client session" on whichever test or log line comes next (issue #104).
    An iteration is therefore not cancellable: it runs to its end as one of the `endings`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.endings: set[asyncio.Task[Any]] = set()

    async def _poll_iteration(self) -> None:
        # Registered when it is made, not when it first runs: a cancel of the poll at once
        # must still find it in `endings`.
        iteration = asyncio.ensure_future(super()._poll_iteration())
        self.endings.add(iteration)
        iteration.add_done_callback(self.endings.discard)
        await asyncio.shield(iteration)

    async def start_jobs(self, job_ids: list[bytes]) -> None:
        # arq reads allow_pick_jobs once, before the queue read: a stop since then starts nothing.
        for job_id in job_ids:
            if not self.allow_pick_jobs:
                return
            await super().start_jobs([job_id])

    async def _ending(self, write: Awaitable[None]) -> None:
        task = asyncio.current_task()
        if task is None:  # pragma: no cover, a coroutine always runs in a task
            await write
            return
        self.endings.add(task)
        try:
            await write
        finally:
            self.endings.discard(task)

    async def finish_job(self, *args: Any, **kwargs: Any) -> None:
        await self._ending(super().finish_job(*args, **kwargs))

    async def finish_failed_job(self, *args: Any, **kwargs: Any) -> None:
        await self._ending(super().finish_failed_job(*args, **kwargs))


def checked_queues(settings: Settings | None = None) -> list[QueueSpec]:
    """The queues the worker serves, or `AgentTooLongError`: the worker refuses to start when an
    agent registered since the agents queue was built can outlive its job timeout."""
    specs = queues(settings)
    for spec in specs:
        if spec.name == queue.AGENTS:
            check_agents_fit(spec.job_timeout_s)
    return specs


def build_worker(spec: QueueSpec, settings: Settings, ctx: dict[str, Any]) -> QueueWorker:
    return QueueWorker(
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


def _function_name(call: Callable[..., Any]) -> str:
    """The function a piece of work runs, by module and name, never its arguments.
    asyncio.to_thread submits partial(Context.run, function, *args): the function is the
    first argument of that partial."""
    while isinstance(call, functools.partial):
        inner = call.args[0] if call.args else None
        call = (
            inner if callable(inner) and getattr(call.func, "__name__", "") == "run" else call.func
        )
    name = getattr(call, "__qualname__", type(call).__qualname__)
    return f"{getattr(call, '__module__', None) or '?'}.{name}"


class JobThreads(ThreadPoolExecutor):
    """The executor of asyncio.to_thread while serve() runs, which knows what has not ended.

    A job that is cancelled stops waiting for its thread, and the thread goes on: it holds a
    session, so a connection of the pool. serve() used to dispose of the engine under it, and
    the connection then came back to a pool nobody closes any more: it stayed open until the
    garbage collector found it (issue #104). Every job of every module reaches its threads
    through asyncio.to_thread, so through the default executor of the loop: serve() makes it
    this one and joins it before the engine goes, whatever module submitted the work. The
    names it keeps are those of the functions submitted and not yet ended, for the one line
    logged when the wait reaches its bound."""

    def __init__(self) -> None:
        super().__init__(thread_name_prefix="worker-job")
        self._names_lock = threading.Lock()
        self._names: Counter[str] = Counter()

    def submit[**P, T](self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> Future[T]:
        name = _function_name(fn)
        future = super().submit(fn, *args, **kwargs)
        with self._names_lock:
            self._names[name] += 1
        future.add_done_callback(lambda _future: self._ended(name))
        return future

    def _ended(self, name: str) -> None:
        with self._names_lock:
            self._names[name] -= 1

    def left(self) -> dict[str, int]:
        """What was submitted and has not ended (or been cancelled before it began)."""
        with self._names_lock:
            return {name: count for name, count in sorted(self._names.items()) if count > 0}

    async def join(self, within: float) -> dict[str, int]:
        """Take no more work, then wait for every thread to end, `within` seconds at most.
        Returns what is left by then: nothing after a whole join."""
        loop = asyncio.get_running_loop()
        joined: asyncio.Future[None] = loop.create_future()
        self.shutdown(wait=False)

        def done() -> None:
            if not joined.done():
                joined.set_result(None)

        def join() -> None:
            self.shutdown(wait=True)
            with contextlib.suppress(RuntimeError):  # the loop is closed: nobody waits any more
                loop.call_soon_threadsafe(done)

        # A daemon: at the bound it waits on a thread that may never end, and must not hold
        # the exit of the process itself.
        threading.Thread(target=join, name="worker-stop", daemon=True).start()
        try:
            async with asyncio.timeout(within):
                await joined
        except TimeoutError:
            self.shutdown(wait=False, cancel_futures=True)
        return self.left()


def _retrieved(task: asyncio.Future[Any]) -> None:
    if not task.cancelled():
        task.exception()


async def _ended(tasks: Collection[asyncio.Future[Any]], deadline: float | None) -> int:
    """Wait for every task, until `deadline` (a time of the loop) when one is given. Returns
    how many had not ended by then. Like gather(return_exceptions=True) it raises nothing of
    theirs and leaves no exception unretrieved, even for a task that ends after the bound."""
    if not tasks:
        return 0
    timeout = None
    if deadline is not None:
        timeout = max(0.0, deadline - asyncio.get_running_loop().time())
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in done:
        _retrieved(task)
    for task in pending:
        task.add_done_callback(_retrieved)
    return len(pending)


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


async def close_worker(worker: QueueWorker, deadline: float | None = None) -> tuple[int, int]:
    """Stop one Worker: stop its poll, cancel its jobs still in flight, wait for them and for
    the job ends being written, then close it. The waits end at `deadline` (a time of the
    loop) when one is given: the jobs and the job ends that had not ended by then are counted
    in the result and left behind, so that the Worker is closed all the same.

    arq's Worker.close() cancels the jobs itself, then awaits them with a plain asyncio.gather. A
    job cancelled inside its function is caught by arq (run_job records it), but one cancelled
    while run_job still awaits Redis (a job just picked up) re-raises CancelledError from that
    gather: close() then stops before deleting the health key and closing the Redis pool, and
    serve() never reaches the next Worker. Draining the tasks here, with return_exceptions,
    leaves close() nothing to raise. The poll is stopped first, so that no job is picked up
    while the others are drained, and the ends still being written (QueueWorker) are awaited
    last: a job that finished as the stop arrived is recorded as finished, and nothing uses
    the pool once close() has closed it. Done tasks are dropped from `tasks` as arq's own poll
    does."""
    worker.allow_pick_jobs = False  # an iteration that ends after the cancel starts no job
    poll = worker.main_task
    if poll is not None:
        poll.cancel()
        await asyncio.gather(poll, return_exceptions=True)
    # The iteration the poll was in the middle of ends first, so that no job it starts is missed
    # by `in_flight` below and no pipeline of it is cut by the closing of the pool.
    ends_left = await _ended(list(worker.endings), deadline)
    in_flight = list(worker.tasks.values())
    for task in in_flight:
        if not task.done():
            task.cancel()
    jobs_left = await _ended(in_flight, deadline)
    ends_left = max(ends_left, await _ended(list(worker.endings), deadline))
    for job_id, task in list(worker.tasks.items()):
        # A job given up at the bound goes too: arq's close() awaits what is left, unbounded.
        if task.done() or jobs_left:
            del worker.tasks[job_id]
    await worker.close()
    return jobs_left, ends_left


async def serve(
    settings: Settings,
    specs: Sequence[QueueSpec] | None = None,
    *,
    stop: asyncio.Event | None = None,
    ctx: dict[str, Any] | None = None,
    on_ready: Callable[[list[Worker]], Awaitable[None]] | None = None,
) -> int:
    """Run one Worker per queue until `stop` is set (SIGTERM or SIGINT when not given).

    The stop is bounded by STOP_WAIT_S. It returns how many jobs, job ends and threads were
    still running when that bound was reached: 0 after a clean stop, which is every stop that
    finds nothing stuck."""
    skip_sdk_version_probe()
    ctx = ctx if ctx is not None else make_context(settings)
    ctx.setdefault("body_gate", asyncio.Semaphore(BODIES_IN_FLIGHT))
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    # Every asyncio.to_thread of every job, in every module, runs on this executor from here.
    threads = JobThreads()
    loop.set_default_executor(threads)
    deadline = math.inf  # no stop yet
    jobs_left = ends_left = 0
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
        deadline = loop.time() + STOP_WAIT_S  # one bound for every wait of the stop
        for worker in workers:
            # One Worker failing to close must not leave the others' Redis pools open.
            try:
                jobs, ends = await close_worker(worker, deadline)
                jobs_left, ends_left = jobs_left + jobs, ends_left + ends
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
        threads_left: dict[str, int] = {}
        try:
            # The jobs are cancelled, their threads are not: each still holds a connection
            # until its session ends. The process waited for them anyway, when asyncio.run()
            # closes the executor, but after the engine was disposed of.
            bound = min(deadline, loop.time() + STOP_WAIT_S)
            threads_left = await threads.join(max(0.0, bound - loop.time()))
        finally:
            # The loop goes on after serve() for some callers (the tests): it gets an executor
            # that takes work, and asyncio.run() does not join again a thread given up here.
            loop.set_default_executor(ThreadPoolExecutor(thread_name_prefix="asyncio"))
            if jobs_left or ends_left or threads_left:
                # By function and count, nothing else: no argument, no id, no user data.
                logger.warning(
                    "worker stop bound reached",
                    extra={
                        "bound_s": STOP_WAIT_S,
                        "jobs": jobs_left,
                        "job_ends": ends_left,
                        "threads": threads_left,
                    },
                )
            if "engine" in ctx:
                ctx["engine"].dispose()
            logger.info("worker stopped")
    return jobs_left + ends_left + sum(threads_left.values())


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
    # Loaded through the cache: job code that reads get_settings() (the documents renders,
    # render_client and store) runs after the secrets below left the environment.
    settings = get_settings()
    if args.check:
        return asyncio.run(check(settings))
    configure_logging(settings.log_level)
    skip_sdk_version_probe()
    dropped = drop_secrets_from_environ()
    logger.info("secrets dropped from the environment", extra={"names": dropped})
    if asyncio.run(serve(settings, checked_queues(settings))):
        # Something was still running at the bound of the stop, and the interpreter joins
        # every thread of an executor before it exits: it would wait here until Compose kills
        # the process. All is said and the engine is disposed of, so leave now. The
        # transaction of a thread cut this way rolls back, and arq runs its job again.
        logging.shutdown()
        os._exit(0)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
