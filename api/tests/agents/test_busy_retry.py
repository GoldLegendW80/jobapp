"""Agents A13: a run Anthropic refuses as busy before it did anything waits and starts again,
and the starts of one person's credential are paced (app/agents/busy.py, runner.execute,
worker.start_again), on the fake backend answering busy and a real Postgres."""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.agents import busy, failures, runner, runs
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, RunStatus
from app.config import Settings
from tests.agents.run_helpers import (
    Backends,
    add_job,
    add_user,
    context,
    events,
    new_run,
    run_row,
    save_credential,
)
from tests.conftest import make_settings

T0 = datetime(2029, 12, 31, 23, 0, tzinfo=UTC)
RESETS = datetime(2030, 1, 1, tzinfo=UTC)  # the `resetsAt` of the busy_resets stream


class Clock:
    """A clock the test moves; `step` moves it a little on every read (time passing in a run)."""

    def __init__(self, at: datetime = T0, step: timedelta = timedelta(0)) -> None:
        self.at, self.step = at, step

    def __call__(self) -> datetime:
        self.at += self.step
        return self.at

    def move(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


def _settings(url: str, key: str, tmp_path: Path, **changes: Any) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return make_settings(url, credentials_key=key, agents_scratch_dir=str(scratch), **changes)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    return _settings(migrated_url, key, tmp_path)


@pytest.fixture
def person(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "e2e-busy-one@example.org")
    save_credential(factory, keyring, user)
    return user


def _context(settings: Settings, factory: Any, keyring: Keyring, clock: Clock, **kw: Any) -> Any:
    return replace(context(settings, factory, keyring, kw.pop("backends", None)), clock=clock)


def _run(rc: Any, run_id: uuid.UUID, **kw: Any) -> RunStatus | None:
    return asyncio.run(runner.execute(rc, run_id, **kw))


def _waits(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[dict[str, Any]]:
    return [data for kind, data in events(factory, run_id) if kind == "waiting"]


def test_a_run_refused_once_waits_the_first_delay_then_succeeds_with_no_failure_left(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, person: uuid.UUID
) -> None:
    clock = Clock()
    backends = Backends()
    rc = _context(settings, factory, keyring, clock, backends=backends)
    run_id = new_run(factory, person, {"message": "hello", "script": "busy_once"})

    assert _run(rc, run_id, may_wait=True) is RunStatus.queued
    row = run_row(factory, run_id)
    assert (row.status, row.error, row.finished_at) == (RunStatus.queued, None, None)
    [wait] = _waits(factory, run_id)
    assert wait == {
        "reason": "busy",
        "at": (T0 + timedelta(seconds=30)).isoformat(),
        "retry": "refused",
        "try": 2,
        "of": 4,
    }
    assert events(factory, run_id)[-2:] == [("status", {"status": "queued"}), ("waiting", wait)]

    clock.move(30)
    assert _run(rc, run_id, may_wait=True) is RunStatus.done
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.done, None)
    assert row.output == {"text": "You said: hello"}
    assert len(backends.made) == 2


def test_the_waits_grow_and_a_run_refused_for_ever_fails_after_its_fourth_try(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, person: uuid.UUID
) -> None:
    clock = Clock()
    rc = _context(settings, factory, keyring, clock)
    run_id = new_run(factory, person, {"message": "hello", "script": "busy"})
    for delay in (30, 120, 480):
        before = clock.at
        assert _run(rc, run_id, may_wait=True) is RunStatus.queued
        assert _waits(factory, run_id)[-1]["at"] == (before + timedelta(seconds=delay)).isoformat()
        clock.move(delay)
    assert _run(rc, run_id, may_wait=True) is RunStatus.failed
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.failed, failures.BUSY)
    assert [wait["try"] for wait in _waits(factory, run_id)] == [2, 3, 4]


def test_a_run_refused_three_times_succeeds_on_its_fourth_try(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, person: uuid.UUID
) -> None:
    clock = Clock()
    rc = _context(settings, factory, keyring, clock)
    run_id = new_run(factory, person, {"message": "hello", "script": "busy_thrice"})
    for delay in (30, 120, 480):
        assert _run(rc, run_id, may_wait=True) is RunStatus.queued
        clock.move(delay)
    assert _run(rc, run_id, may_wait=True) is RunStatus.done
    assert run_row(factory, run_id).error is None


def test_no_try_is_planned_past_the_window_of_the_first_refusal(
    migrated_url: str,
    key: str,
    tmp_path: Path,
    factory: sessionmaker[Session],
    keyring: Keyring,
    person: uuid.UUID,
) -> None:
    settings = _settings(migrated_url, key, tmp_path, agents_busy_retry_window_s=120)
    clock = Clock()
    rc = _context(settings, factory, keyring, clock)
    run_id = new_run(factory, person, {"message": "hello", "script": "busy"})
    assert _run(rc, run_id, may_wait=True) is RunStatus.queued  # 30 s: within 120
    clock.move(30)
    # The next wait, 120 s, would start the third try 150 s after the first refusal.
    assert _run(rc, run_id, may_wait=True) is RunStatus.failed
    assert run_row(factory, run_id).error == failures.BUSY


@pytest.mark.parametrize(
    ("before_reset", "waited"), [(45, 45), (3600, 600)], ids=["its own", "bounded"]
)
def test_anthropic_s_own_delay_is_taken_bounded_to_the_longest_wait(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    person: uuid.UUID,
    before_reset: int,
    waited: int,
) -> None:
    clock = Clock(RESETS - timedelta(seconds=before_reset))
    rc = _context(settings, factory, keyring, clock)
    run_id = new_run(factory, person, {"message": "hello", "script": "busy_resets"})
    start = clock.at
    assert _run(rc, run_id, may_wait=True) is RunStatus.queued
    assert _waits(factory, run_id)[-1]["at"] == (start + timedelta(seconds=waited)).isoformat()


def test_a_run_refused_after_a_tool_call_is_never_started_again(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, person: uuid.UUID
) -> None:
    backends = Backends()
    rc = _context(settings, factory, keyring, Clock(), backends=backends)
    job_id = add_job(factory, person)
    run_id = new_run(factory, person, {"message": "hello", "script": "busy_after_tool"}, job_id)
    assert _run(rc, run_id, may_wait=True) is RunStatus.failed
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.failed, failures.BUSY)
    assert [kind for kind, _ in events(factory, run_id)].count("tool_call") == 1
    assert _waits(factory, run_id) == []
    assert len(backends.made) == 1
    with factory() as db:
        assert not busy.refused_untouched(db, run_id, failures.BUSY)


def test_a_caller_that_cannot_wait_keeps_today_s_failure_and_reads_it_as_untouched(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, person: uuid.UUID
) -> None:
    rc = _context(settings, factory, keyring, Clock())
    run_id = new_run(factory, person, {"message": "hello", "script": "busy"})
    assert _run(rc, run_id) is RunStatus.failed
    assert run_row(factory, run_id).error == failures.BUSY
    assert _waits(factory, run_id) == []
    with factory() as db:
        assert busy.refused_untouched(db, run_id, failures.BUSY)
    done = new_run(factory, person, {"message": "hello"})
    assert _run(rc, done) is RunStatus.done
    with factory() as db:
        assert not busy.refused_untouched(db, done, failures.BUSY)


def test_cancel_while_it_waits_is_at_once_and_its_next_job_starts_nothing(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, person: uuid.UUID
) -> None:
    clock = Clock()
    backends = Backends()
    rc = _context(settings, factory, keyring, clock, backends=backends)
    run_id = new_run(factory, person, {"message": "hello", "script": "busy_once"})
    assert _run(rc, run_id, may_wait=True) is RunStatus.queued
    with factory() as db:
        assert runs.request_cancel(db, person, run_id, clock()) is runs.CancelOutcome.cancelled
        db.commit()
    assert run_row(factory, run_id).status is RunStatus.cancelled
    clock.move(30)
    assert _run(rc, run_id, may_wait=True) is None
    assert len(backends.made) == 1


async def _until_waiting(factory: sessionmaker[Session], run_id: uuid.UUID) -> None:
    """Until the run has its first `waiting` step (the frozen clock keeps it waiting)."""
    for _ in range(3000):
        if _waits(factory, run_id):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the run never waited")


def test_a_run_of_the_panel_waits_in_its_job_and_a_cancel_ends_the_wait(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, person: uuid.UUID
) -> None:
    clock = Clock(step=timedelta(seconds=5))
    rc = _context(settings, factory, keyring, clock)
    run_id = new_run(factory, person, {"message": "hello", "script": "busy_once"})
    assert asyncio.run(runner.execute_waiting(rc, run_id)) is RunStatus.done
    assert run_row(factory, run_id).error is None

    stopped = new_run(factory, person, {"message": "hello", "script": "busy"})
    frozen = _context(settings, factory, keyring, Clock())

    async def cancel_while_waiting() -> RunStatus | None:
        task = asyncio.create_task(runner.execute_waiting(frozen, stopped))
        await _until_waiting(factory, stopped)
        with factory() as db:
            runs.request_cancel(db, person, stopped, T0)
            db.commit()
        return await task

    assert asyncio.run(cancel_while_waiting()) is None
    assert run_row(factory, stopped).status is RunStatus.cancelled


def test_a_job_stopped_while_its_run_waits_fails_the_run_as_stopped(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, person: uuid.UUID
) -> None:
    rc = _context(settings, factory, keyring, Clock())
    run_id = new_run(factory, person, {"message": "hello", "script": "busy"})

    async def stop() -> None:
        task = asyncio.create_task(runner.execute_waiting(rc, run_id))
        await _until_waiting(factory, run_id)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(stop())
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.failed, runs.STOPPED)


def _paced(migrated_url: str, key: str, tmp_path: Path, per_minute: int) -> Settings:
    return _settings(migrated_url, key, tmp_path, agents_starts_per_minute=per_minute)


def test_the_starts_of_one_credential_are_paced_in_order_and_another_person_is_not(
    migrated_url: str,
    key: str,
    tmp_path: Path,
    factory: sessionmaker[Session],
    keyring: Keyring,
    person: uuid.UUID,
) -> None:
    settings = _paced(migrated_url, key, tmp_path, per_minute=2)
    clock = Clock()
    rc = _context(settings, factory, keyring, clock)
    first, second, third, fourth = (new_run(factory, person) for _ in range(4))
    assert _run(rc, first, may_wait=True, paced=True) is RunStatus.done
    clock.move(10)
    assert _run(rc, second, may_wait=True, paced=True) is RunStatus.done
    clock.move(10)
    assert _run(rc, third, may_wait=True, paced=True) is RunStatus.queued
    assert _waits(factory, third)[-1]["reason"] == "pace"
    assert _waits(factory, third)[-1]["at"] == (T0 + timedelta(seconds=60)).isoformat()
    assert _run(rc, fourth, may_wait=True, paced=True) is RunStatus.queued
    assert _waits(factory, fourth)[-1]["at"] == (T0 + timedelta(seconds=70)).isoformat()
    # Another person's credential has its own minute.
    other = add_user(factory, "e2e-busy-two@example.org")
    save_credential(factory, keyring, other)
    assert _run(rc, new_run(factory, other), may_wait=True, paced=True) is RunStatus.done
    # At its turn, the third starts; the fourth, earlier than its turn, waits again.
    clock.at = T0 + timedelta(seconds=60)
    assert _run(rc, third, may_wait=True, paced=True) is RunStatus.done
    assert _run(rc, fourth, may_wait=True, paced=True) is RunStatus.queued
    clock.at = T0 + timedelta(seconds=70)
    assert _run(rc, fourth, may_wait=True, paced=True) is RunStatus.done


def test_two_workers_claiming_at_once_start_one_run_of_a_full_minute(
    migrated_url: str,
    key: str,
    tmp_path: Path,
    factory: sessionmaker[Session],
    keyring: Keyring,
    person: uuid.UUID,
) -> None:
    settings = _paced(migrated_url, key, tmp_path, per_minute=1)
    one = _context(settings, factory, keyring, Clock())
    two = _context(settings, factory, keyring, Clock())
    a, b = new_run(factory, person), new_run(factory, person)

    async def both() -> list[RunStatus | None]:
        return list(
            await asyncio.gather(
                runner.execute(one, a, may_wait=True, paced=True),
                runner.execute(two, b, may_wait=True, paced=True),
            )
        )

    assert sorted(str(status) for status in asyncio.run(both())) == ["done", "queued"]


def test_after_a_refusal_runs_of_that_credential_start_one_at_a_time(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, person: uuid.UUID
) -> None:
    clock = Clock()
    rc = _context(settings, factory, keyring, clock)
    refused = new_run(factory, person, {"message": "hello", "script": "busy_once"})
    assert _run(rc, refused, may_wait=True) is RunStatus.queued
    going = new_run(factory, person)
    with factory() as db:
        db.execute(sa.update(AgentRun).where(AgentRun.id == going).values(status=RunStatus.running))
        db.commit()
    waiting = new_run(factory, person)
    assert _run(rc, waiting, may_wait=True, paced=True) is RunStatus.queued
    assert _waits(factory, waiting)[-1]["reason"] == "busy"
    clock.move(30)
    assert _run(rc, refused, may_wait=True) is RunStatus.queued  # the try waits its turn too
    assert _waits(factory, refused)[-1]["retry"] == "held"
    with factory() as db:
        db.execute(sa.update(AgentRun).where(AgentRun.id == going).values(status=RunStatus.done))
        db.commit()
    assert _run(rc, refused, may_wait=True) is RunStatus.done


class Pool:
    def __init__(self, fails: bool = False) -> None:
        self.jobs: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.fails = fails

    async def enqueue_job(self, *args: Any, **kwargs: Any) -> object | None:
        if self.fails:
            raise ConnectionError("down")
        self.jobs.append((args, kwargs))
        return object()


def test_the_worker_queues_a_waiting_run_at_its_time_or_fails_it_when_it_cannot(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, person: uuid.UUID
) -> None:
    rc = _context(settings, factory, keyring, Clock())
    run_id = new_run(factory, person, {"message": "hello", "script": "busy"})
    assert _run(rc, run_id, may_wait=True) is RunStatus.queued
    pool = Pool()
    ctx = {"settings": settings, "sessions": factory, "redis": pool, "clock": Clock()}
    assert asyncio.run(worker.start_again(ctx, run_id))
    [(args, kwargs)] = pool.jobs
    seq = [kind for kind, _ in events(factory, run_id)].index("waiting") + 1
    assert args == ("run_agent", str(run_id))
    assert kwargs["_job_id"] == f"agent-run:{run_id}:{seq}"
    assert kwargs["_defer_until"] == T0 + timedelta(seconds=30)

    ctx["redis"] = Pool(fails=True)
    assert not asyncio.run(worker.start_again(ctx, run_id))
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.failed, failures.BUSY)


def test_a_wait_nobody_ended_is_failed_by_the_reaper(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring, person: uuid.UUID
) -> None:
    rc = _context(settings, factory, keyring, Clock())
    run_id = new_run(factory, person, {"message": "hello", "script": "busy"})
    assert _run(rc, run_id, may_wait=True) is RunStatus.queued
    at = T0 + timedelta(seconds=30)
    with factory() as db:
        kept = runs.reap_stale(
            db, at + timedelta(minutes=9), timedelta(hours=1), timedelta(days=36500)
        )
        assert kept == 0
        gone = runs.reap_stale(
            db, at + timedelta(minutes=11), timedelta(hours=1), timedelta(days=36500)
        )
        db.commit()
    assert gone == 1
    assert run_row(factory, run_id).error == runs.LOST


def test_plan_counts_the_tries_and_the_window() -> None:
    limits = busy.Bounds()
    assert busy.plan([], T0, None, limits) == T0 + timedelta(seconds=30)
    assert busy.plan([T0], T0, None, limits) == T0 + timedelta(seconds=120)
    assert busy.plan([T0, T0], T0, None, limits) == T0 + timedelta(seconds=480)
    assert busy.plan([T0, T0, T0], T0, None, limits) is None
    assert busy.plan([], T0, 5.0, limits) == T0 + timedelta(seconds=5)
    assert busy.plan([], T0, 9999.0, limits) == T0 + timedelta(seconds=600)
    late = T0 + timedelta(seconds=1700)
    assert busy.plan([T0], late, 200.0, limits) is None
    assert busy.untouched(0, 0, 0, None, None)
    assert busy.untouched(0, 0, 0, 0, 0)
    for acted in ((1, 0, 0, 0, 0), (0, 1, 0, 0, 0), (0, 0, 1, 0, 0), (0, 0, 0, 5, 0)):
        assert not busy.untouched(*acted)
