"""The final hook of a definition (ingestion G6, point 4): called once, by the runner that
recorded the run's end, after that end is committed and with nothing of the run held, for done,
cancelled and every failure the runner records itself. Not called for a run ended elsewhere (a
reaper, a cancel while queued), nor while the worker is stopping. What the hook raises is
logged and changes nothing of the run."""

import asyncio
import logging
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from arq.worker import Worker
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions, runner, runs
from app.agents import tools as app_tools
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, RunStatus
from app.config import Settings
from tests.agents.g6_helpers import (
    MARKER,
    Streams,
    agent,
    assistant,
    g6_tools,
    init,
    loop,
    run_context,
    streams,
    text,
)
from tests.agents.run_helpers import (
    Backends,
    add_user,
    events,
    execute,
    new_run,
    now,
    run_row,
    save_credential,
)
from tests.agents.test_live_stream import enqueue, worker_context
from tests.conftest import make_settings
from tests.jobs.test_worker import flush_redis, redis_url, serve_until, wait_for

__all__ = ["g6_tools", "redis_url", "streams"]

ANSWER = [init(), assistant("m1", text("Done."))]
STALL = [init(), {"sleep": 30}, assistant("m1", text("Too late."))]


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


@pytest.fixture
def alice_id(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)
    return user


class Hook:
    """An on_final hook that notes each call, and what the database and the disk held then."""

    def __init__(self, scratch: str | None = None) -> None:
        self.calls: list[definitions.FinalRun] = []
        self.rows: list[tuple[RunStatus, str | None, bool, str]] = []
        self.scratch_left: list[list[Path]] = []
        self.scratch = scratch

    async def __call__(self, final: definitions.FinalRun) -> None:
        self.calls.append(final)
        with final.sessions() as db:
            # The end is committed, and the runner holds no lock on the run: a hook may take
            # the locks of its own order, the run row included, without waiting.
            row = db.execute(
                sa.select(AgentRun).where(AgentRun.id == final.run_id).with_for_update(nowait=True)
            ).scalar_one()
            last = db.execute(
                sa.text("SELECT data::text FROM agent_events WHERE run_id = :r ORDER BY seq DESC"),
                {"r": final.run_id},
            ).scalar()
            self.rows.append((row.status, row.error, row.finished_at is not None, str(last)))
            db.rollback()
        self.look_at_the_disk()

    def look_at_the_disk(self) -> None:
        if self.scratch is not None:
            self.scratch_left.append(list(Path(self.scratch).iterdir()))


def gone(db: Session, run: AgentRun) -> None:
    return None


def broken(db: Session, run: AgentRun) -> None:
    raise RuntimeError("bug in the hook's module")


# name: (definition changes, run input, stream, with a credential, speed, status, error)
ENDINGS: dict[str, tuple[dict[str, Any], dict[str, Any], list[Any], bool, float, str, Any]] = {
    "done": ({}, {}, ANSWER, True, 0.01, "done", None),
    "claude code dies": (
        {},
        {},
        [init(), {"raise": "process"}],
        True,
        0.01,
        "failed",
        "The Claude Code process failed (exit code 1).",
    ),
    "no credential": ({}, {}, ANSWER, False, 0.01, "failed", runner.NO_CREDENTIAL),
    "the thing is gone": ({"bind": gone}, {}, ANSWER, True, 0.01, "failed", runner.BINDING_GONE),
    "the bind hook raises": ({"bind": broken}, {}, ANSWER, True, 0.01, "failed", runner.INTERNAL),
    "no budget left": ({}, {"token_budget": 0}, ANSWER, True, 0.01, "failed", runner.TOKEN_LIMIT),
    "budget used up": (
        {},
        {"token_budget": 1000},
        loop(3),
        True,
        0.01,
        "failed",
        runner.TOKEN_LIMIT,
    ),
    "time limit": (
        {"wall_time_s": 0.2},
        {},
        STALL,
        True,
        1.0,
        "failed",
        "The run took longer than its limit of 0.2 seconds and was stopped.",
    ),
    "turn limit": (
        {"max_turns": 1},
        {},
        loop(3),
        True,
        0.01,
        "failed",
        "The run reached its limit of 1 turns.",
    ),
}


@pytest.mark.parametrize("ending", list(ENDINGS))
def test_called_once_after_the_end_is_committed(
    ending: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    streams: Streams,
    g6_tools: None,
) -> None:
    changes, data, steps, with_credential, speed, status, error = ENDINGS[ending]
    user = add_user(factory, "alice@example.org")
    if with_credential:
        save_credential(factory, keyring, user)
    hook = Hook(settings.agents_scratch_dir)
    hooked = agent("g6_hooked", on_final=hook, **changes)
    streams.add("g6_hooked", steps)
    run_id = new_run(factory, user, data, agent="g6_hooked")
    rc = run_context(
        settings,
        factory,
        keyring,
        hooked,
        backends=Backends(speed=speed),
        services={"queue": "the worker's"},
    )
    assert execute(rc, run_id) is RunStatus(status)
    assert len(hook.calls) == 1
    final = hook.calls[0]
    assert (final.run_id, final.user_id, final.agent) == (run_id, user, "g6_hooked")
    assert final.status is RunStatus(status)
    assert final.sessions is factory
    assert final.settings is settings
    assert final.clock is now
    assert final.services == {"queue": "the worker's"}
    # What the hook found: the final status, its error and its last event, all committed.
    found_status, found_error, finished, last_event = hook.rows[0]
    assert (found_status, found_error, finished) == (RunStatus(status), error, True)
    assert f'"status": "{status}"' in last_event
    assert hook.scratch_left == [[]]  # nothing of the run is on disk any more
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus(status), error)
    assert "sessions" not in repr(final)  # the handles are not printed with it


def test_called_once_for_a_cancelled_run(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    hook = Hook(settings.agents_scratch_dir)
    streams.add("g6_hooked", STALL)
    run_id = new_run(factory, alice_id, {}, agent="g6_hooked")
    rc = run_context(
        settings, factory, keyring, agent("g6_hooked", on_final=hook), backends=Backends(1.0)
    )

    async def go() -> Any:
        task = asyncio.create_task(runner.execute(rc, run_id))
        while len(events(factory, run_id)) < 3:  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        with factory() as db:
            runs.request_cancel(db, alice_id, run_id, now())
            db.commit()
        return await task

    assert asyncio.run(go()) is RunStatus.cancelled
    assert [final.status for final in hook.calls] == [RunStatus.cancelled]
    assert hook.rows[0][:3] == (RunStatus.cancelled, None, True)
    assert hook.scratch_left == [[]]


def test_called_for_a_crash_inside_the_runner(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def crash(*_: Any, **__: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(app_tools, "build", crash)
    hook = Hook(settings.agents_scratch_dir)
    streams.add("g6_hooked", ANSWER)
    run_id = new_run(factory, alice_id, {}, agent="g6_hooked")
    rc = run_context(settings, factory, keyring, agent("g6_hooked", on_final=hook))
    assert execute(rc, run_id) is RunStatus.failed
    assert [final.status for final in hook.calls] == [RunStatus.failed]
    assert hook.rows[0][:2] == (RunStatus.failed, runner.INTERNAL)
    assert hook.scratch_left == [[]]


# --- not called -----------------------------------------------------------------------------------


def test_not_called_for_a_run_a_reaper_failed(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """The reaper fails the run while its runner is still at work: the runner's own end is
    refused (the run is no longer running), so it recorded nothing and calls nothing. Whoever
    chains runs learns of that end on its timer."""
    hook = Hook()
    streams.add("g6_hooked", ANSWER)
    run_id = new_run(factory, alice_id, {}, agent="g6_hooked")

    class Reaped(Backends):
        def __call__(self) -> Any:
            with factory() as db:
                assert runs.reap_orphans(db, now()) == 1
                db.commit()
            return super().__call__()

    rc = run_context(
        settings, factory, keyring, agent("g6_hooked", on_final=hook), backends=Reaped()
    )
    # The runner's next event collides with the reaper's last one and its stream stops there.
    assert execute(rc, run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.failed, runs.INTERRUPTED)  # the reaper's end
    assert events(factory, run_id)[-1] == (
        "status",
        {"status": "failed", "error": runs.INTERRUPTED},
    )
    assert hook.calls == []


def test_not_called_while_the_worker_is_stopping(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    hook = Hook()
    streams.add("g6_hooked", STALL)
    run_id = new_run(factory, alice_id, {}, agent="g6_hooked")
    rc = run_context(
        settings, factory, keyring, agent("g6_hooked", on_final=hook), backends=Backends(1.0)
    )

    async def go() -> None:
        task = asyncio.create_task(runner.execute(rc, run_id))
        while len(events(factory, run_id)) < 3:  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.failed, runs.STOPPED)
    assert hook.calls == []  # a stopping process does the minimum: the next one's timer acts


def test_not_called_for_a_run_that_was_not_queued_any_more(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    hook = Hook()
    streams.add("g6_hooked", ANSWER)
    run_id = new_run(factory, alice_id, {}, agent="g6_hooked")
    with factory() as db:
        assert runs.request_cancel(db, alice_id, run_id, now()) is runs.CancelOutcome.cancelled
        db.commit()
    rc = run_context(settings, factory, keyring, agent("g6_hooked", on_final=hook))
    assert execute(rc, run_id) is None
    assert hook.calls == []


def test_not_called_when_the_end_could_not_be_recorded(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(*_: Any, **__: Any) -> bool:
        raise RuntimeError("database gone")

    monkeypatch.setattr(runs, "finish", failing)
    hook = Hook()
    streams.add("g6_hooked", ANSWER)
    run_id = new_run(factory, alice_id, {}, agent="g6_hooked")
    rc = run_context(settings, factory, keyring, agent("g6_hooked", on_final=hook))
    assert execute(rc, run_id) is RunStatus.done
    assert run_row(factory, run_id).status is RunStatus.running
    assert hook.calls == []


def test_an_agent_without_a_hook_is_left_alone(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
) -> None:
    run_id = new_run(factory, alice_id)  # echo, as before
    assert execute(run_context(settings, factory, keyring), run_id) is RunStatus.done


# --- a hook that misbehaves -----------------------------------------------------------------------


@pytest.mark.parametrize("private", [False, True], ids=["public", "private"])
def test_a_hook_that_raises_changes_nothing_of_the_run(
    private: bool,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[RunStatus] = []

    async def raising(final: definitions.FinalRun) -> None:
        calls.append(final.status)
        raise LookupError(f"the chain broke on {MARKER}")

    hooked = agent("g6_hooked", on_final=raising, private_io=private)
    streams.add("g6_hooked", ANSWER)
    run_id = new_run(factory, alice_id, {}, agent="g6_hooked")
    with caplog.at_level(logging.DEBUG):
        assert execute(run_context(settings, factory, keyring, hooked), run_id) is RunStatus.done
    assert calls == [RunStatus.done]  # once, and not again after it raised
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.done, None)
    assert events(factory, run_id)[-1] == ("status", {"status": "done"})
    failed = [r for r in caplog.records if r.getMessage() == "agent final hook failed"]
    assert len(failed) == 1
    assert failed[0].run_id == str(run_id)  # type: ignore[attr-defined]
    text_logged = caplog.text + capsys.readouterr().out
    if private:
        assert failed[0].error == "LookupError"  # type: ignore[attr-defined]
        assert failed[0].exc_info is None
        assert MARKER not in text_logged
    else:
        assert failed[0].exc_info is not None
        # Its frames and type only: no message reaches a log line at all (issue #670).
        assert ", in raising\nLookupError" in text_logged
        assert MARKER not in text_logged


def test_a_hook_that_never_returns_is_given_up(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entered: list[float] = []

    async def stuck(final: definitions.FinalRun) -> None:
        entered.append(time.monotonic())
        await asyncio.sleep(3600)

    hooked = agent("g6_hooked", on_final=stuck, private_io=True)
    streams.add("g6_hooked", ANSWER)
    run_id = new_run(factory, alice_id, {}, agent="g6_hooked")
    rc = run_context(settings, factory, keyring, hooked, final_hook_timeout_s=0.2)
    with caplog.at_level(logging.DEBUG):
        assert execute(rc, run_id) is RunStatus.done
    assert time.monotonic() - entered[0] < 5
    failed = [r for r in caplog.records if r.getMessage() == "agent final hook failed"]
    assert [r.error for r in failed] == ["TimeoutError"]  # type: ignore[attr-defined]
    assert run_row(factory, run_id).status is RunStatus.done
    assert runner.FINAL_HOOK_TIMEOUT_S == 30.0


def test_the_worker_stopping_during_the_hook_ends_it(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """The run's end is already committed when the hook runs: a stop then cuts the hook, not
    the run."""
    entered = asyncio.Event()

    async def stuck(final: definitions.FinalRun) -> None:
        entered.set()
        await asyncio.sleep(3600)

    hooked = agent("g6_hooked", on_final=stuck)
    streams.add("g6_hooked", ANSWER)
    run_id = new_run(factory, alice_id, {}, agent="g6_hooked")
    rc = run_context(settings, factory, keyring, hooked)

    async def go() -> None:
        task = asyncio.create_task(runner.execute(rc, run_id))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.done, None)


# --- through the real worker ----------------------------------------------------------------------


@pytest.fixture
def worker_settings(migrated_url: str, redis_url: str, key: str, tmp_path: Path) -> Settings:
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


def test_through_the_worker_once_per_run_and_never_for_an_orphan(
    worker_settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """A worker restart: the run its dead predecessor left running is failed by the reaper when
    this worker starts, and gets no hook (nobody ran it here). The runs this worker runs get
    one call each, whatever their end, the hook raising included."""
    calls: list[tuple[uuid.UUID, RunStatus]] = []

    async def hook(final: definitions.FinalRun) -> None:
        calls.append((final.run_id, final.status))
        raise RuntimeError("the hook fails every time")

    hooked = agent("g6_hooked", on_final=hook)
    streams.add("g6_hooked", ANSWER)
    streams.add("g6_dies", [init(), {"raise": "process"}])
    orphan = new_run(factory, alice_id, {}, agent="g6_hooked")
    with factory() as db:
        runs.claim(db, orphan, now())
        db.commit()
    good = new_run(factory, alice_id, {}, agent="g6_hooked")
    bad = new_run(factory, alice_id, {"script": "g6_dies"}, agent="g6_hooked")
    ctx = worker_context(worker_settings, speed=0.01)
    ctx["agent_definitions"] = {hooked.name: hooked}

    async def scenario(workers: list[Worker]) -> None:
        await enqueue(workers, good)
        await enqueue(workers, bad)
        await wait_for(lambda: len(calls) == 2)

    serve_until(worker_settings, ctx, scenario)
    assert sorted(calls, key=lambda call: str(call[1])) == sorted(
        [(good, RunStatus.done), (bad, RunStatus.failed)], key=lambda call: str(call[1])
    )
    assert (run_row(factory, orphan).status, run_row(factory, orphan).error) == (
        RunStatus.failed,
        runs.INTERRUPTED,
    )
    assert run_row(factory, good).status is RunStatus.done  # the raising hook changed nothing
    assert replace(hooked, on_final=None).on_final is None


def test_not_called_when_the_queues_job_timeout_ends_the_run(
    worker_settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """Review of PR #95: arq ends a job that outlives its timeout by cancelling it, which is
    exactly what a stopping worker does to it, and the runner cannot tell the two apart. The
    run is recorded as stopped and its hook is not called, although the worker lives: it goes
    on to run the next job, whose hook is called. A missed hook is never called again; the
    timer of whoever chains runs is the guarantee. Here through the real worker and a real arq
    timeout of one second."""
    from arq import func

    from app import queue as queues
    from app import worker

    calls: list[tuple[uuid.UUID, RunStatus]] = []

    async def hook(final: definitions.FinalRun) -> None:
        calls.append((final.run_id, final.status))

    hooked = agent("g6_hooked", on_final=hook)
    streams.add("g6_hooked", ANSWER)
    streams.add("g6_stalls", STALL)
    too_long = new_run(factory, alice_id, {"script": "g6_stalls"}, agent="g6_hooked")
    after_it = new_run(factory, alice_id, {}, agent="g6_hooked")
    ctx = worker_context(worker_settings, speed=1.0)
    ctx["agent_definitions"] = {hooked.name: hooked}
    spec = worker.QueueSpec(
        name=queues.AGENTS,
        functions=[func(worker.run_agent, name="run_agent", timeout=1, max_tries=1)],
        max_jobs=2,
        job_timeout_s=1,
    )

    async def go() -> None:
        stop = asyncio.Event()
        failure: list[BaseException] = []

        async def play(workers: list[Worker]) -> None:
            try:
                await enqueue(workers, too_long)
                await wait_for(lambda: run_row(factory, too_long).status is RunStatus.failed)
                # One second is shorter than a whole run takes on a loaded machine (issue #104):
                # the run that must end well gets the time of a real one.
                for each in workers:
                    each.functions["run_agent"].timeout_s = 60
                    each.in_progress_timeout_s = 70
                await enqueue(workers, after_it)
                await wait_for(lambda: len(calls) == 1, within=90)
            except BaseException as exc:
                failure.append(exc)
            finally:
                stop.set()

        async def ready(workers: list[Worker]) -> None:
            asyncio.get_running_loop().create_task(play(workers))

        serving = worker.serve(worker_settings, [spec], stop=stop, ctx=ctx, on_ready=ready)
        await asyncio.wait_for(serving, 180)
        if failure:
            raise failure[0]

    asyncio.run(go())
    row = run_row(factory, too_long)
    assert (row.status, row.error) == (RunStatus.failed, runs.STOPPED)
    assert calls == [(after_it, RunStatus.done)]  # the worker lived, and only this hook ran
