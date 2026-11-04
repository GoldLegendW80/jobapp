"""B20 (owner, 2026-11-04: "Time: as long as it takes"): the job websites read in the browser
have no time cap. The sources that make no browser call keep the sweep's SWEEP_TIMEOUT_S; the
websites run after them until BROWSER_SEARCH_MAX_S from the sweep's start, the guard against a
dead process that ends before arq's own job timeout.

The worker's `run_sweep` is the one path of the Find jobs button and of a scheduled sweep (both
queue it), so these run it with the defaults the worker passes. The event loop's clock is the
test's (`Loop.offset`): asyncio.timeout reads it, so a source "takes" an hour without waiting.
"""

import asyncio
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.jobs import sweep
from app.jobs.models import SweepStatus
from tests.jobs.sweep_helpers import (
    each_website,
    finding,
    new_sweep,
    now,
    read_sweep,
    search,
    set_profile,
)


class Loop(asyncio.SelectorEventLoop):
    """An event loop whose clock moves on when the test says so."""

    def __init__(self) -> None:
        super().__init__()
        self.offset = 0.0

    def time(self) -> float:
        return super().time() + self.offset


async def spend(seconds: float) -> None:
    """Let `seconds` pass on the loop's clock, then give every timer due by then its turn."""
    loop = asyncio.get_running_loop()
    assert isinstance(loop, Loop)
    loop.offset += seconds
    for _ in range(5):
        await asyncio.sleep(0)


def run_worker(ctx: dict[str, Any], sweep_id: uuid.UUID) -> None:
    with asyncio.Runner(loop_factory=Loop) as runner:
        runner.run(worker.run_sweep(ctx, str(sweep_id)))


def context(
    factory: sessionmaker[Session],
    browser: sweep.BrowserHook,
    alerts: sweep.BrowserHook | None = None,
) -> dict[str, Any]:
    # No company board is saved (Boards sends nothing) and no feed is ticked.
    return {
        "sessions": factory,
        "clock": now,
        "alert_sources": alerts,
        "feed_sources": None,
        "browser_sources": browser,
    }


def test_the_websites_run_past_the_sweeps_fifteen_minutes(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """Before B20's fix the one 900 s timeout of the sweep cancelled the websites here."""
    set_profile(factory, alice, search())
    sweep_id = new_sweep(factory, alice)
    log: list[str] = []
    told: list[float] = []

    found = each_website([finding("GenAI Engineer")])

    async def websites(plan: sweep.Plan, stats: sweep.Stats, progress: Any, deadline: float):
        log.append("websites started")
        told.append(deadline - time.monotonic())
        await spend(3 * 3600)  # three hours of searching
        await found(plan, stats, progress, deadline)
        log.append("websites ended")

    run_worker(context(factory, websites), sweep_id)
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.done, None)
    assert log == ["websites started", "websites ended"]
    assert row.stats["new"] == 1
    # The websites are told the sweep's own bound, a day from its start, never the 900 s.
    [left] = told
    assert sweep.BROWSER_SEARCH_MAX_S - 60 < left <= sweep.BROWSER_SEARCH_MAX_S


def test_the_sources_without_a_browser_keep_their_deadline(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """The alert e-mails, the feeds, the boards and LinkedIn's public job search are unchanged:
    one still running at SWEEP_TIMEOUT_S fails the sweep, and no website starts."""
    set_profile(factory, alice, search())
    sweep_id = new_sweep(factory, alice)
    log: list[str] = []

    async def alerts(plan: sweep.Plan, stats: sweep.Stats, progress: Any, deadline: float):
        log.append("alerts started")
        try:
            await spend(sweep.SWEEP_TIMEOUT_S + 1)
        except asyncio.CancelledError:
            log.append("alerts cancelled")
            raise

    async def websites(plan: sweep.Plan, stats: sweep.Stats, progress: Any, deadline: float):
        log.append("websites started")

    run_worker(context(factory, websites, alerts), sweep_id)
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.TIMED_OUT)
    assert log == ["alerts started", "alerts cancelled"]


def test_the_websites_end_at_the_sweeps_own_bound(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """A search that never ends (a dead process) is stopped at BROWSER_SEARCH_MAX_S from the
    sweep's start, a minute before arq's job timeout would kill the job."""
    set_profile(factory, alice, search())
    sweep_id = new_sweep(factory, alice)
    log: list[str] = []

    async def websites(plan: sweep.Plan, stats: sweep.Stats, progress: Any, deadline: float):
        await spend(sweep.BROWSER_SEARCH_MAX_S - 1)
        log.append("still searching a second before the bound")
        try:
            await spend(2)
        except asyncio.CancelledError:
            log.append("websites cancelled")
            raise

    run_worker(context(factory, websites), sweep_id)
    row = read_sweep(factory, sweep_id)
    assert (row.status, row.error) == (SweepStatus.failed, sweep.TIMED_OUT)
    assert log == ["still searching a second before the bound", "websites cancelled"]
    assert worker.SWEEP_JOB_TIMEOUT_S > sweep.BROWSER_SEARCH_MAX_S
