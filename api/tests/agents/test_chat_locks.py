"""Lock order of issue #27: the worker ending a chat run locks the thread before it updates the
run, like `delete_thread` does (thread, then the run it cancels). With the old order (the run
updated, then the thread locked to store the answer) a delete racing the end of a run made each
side wait for the other's row, and Postgres aborted one of them as a deadlock."""

import logging
import threading
import time
import uuid
from dataclasses import replace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import chat, runner
from app.agents.crypto import Keyring
from app.agents.models import AgentMessage, AgentRun, AgentThread, MessageRole, RunStatus
from app.jobs import service as jobs_service
from tests.agents.run_helpers import (
    Backends,
    add_job,
    add_user,
    context,
    execute,
    now,
    run_row,
    save_credential,
)
from tests.conftest import make_settings

WAIT_S = 10

Stage = tuple[Any, uuid.UUID, uuid.UUID]


@pytest.fixture
def stage(migrated_url: str, key: str, factory: sessionmaker[Session]) -> Stage:
    """A runner context on the fake backend, a user with a credential, and the user's thread."""
    settings = make_settings(migrated_url, credentials_key=key, agents_fake_backend=True)
    keyring = Keyring.from_text(key)
    user = add_user(factory, "ada@example.org")
    save_credential(factory, keyring, user)
    job = add_job(factory, user, "Mistral AI", "Applied AI Engineer", "Build agents.")
    with factory() as db, db.begin():
        found = jobs_service.get_job(db, user, job)
        assert found is not None
        thread, _ = chat.thread_for_job(db, user, found, now())
        thread_id = thread.id
    return context(settings, factory, keyring, Backends()), user, thread_id


def say(factory: sessionmaker[Session], user: uuid.UUID, thread: uuid.UUID, text: str) -> uuid.UUID:
    with factory() as db, db.begin():
        row = chat.get_thread(db, user, thread, lock=True)
        assert row is not None
        _, run = chat.send(db, user, row, text, now())
        return run.id


def lock_wait_seen(factory: sessionmaker[Session], within_s: float = WAIT_S) -> bool:
    """Whether a session of this database came to wait for a lock within `within_s`, asked of
    Postgres itself (pg_stat_activity) instead of sleeping for a guessed time: the tests go on
    at the moment the other side is waiting, however slow the machine is."""
    until = time.monotonic() + within_s
    with factory() as db:
        while time.monotonic() < until:
            waiting = db.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
            db.rollback()
            if waiting:
                return True
            time.sleep(0.01)
    return False


def count(factory: sessionmaker[Session], model: Any) -> int:
    with factory() as db:
        return db.scalar(sa.select(sa.func.count()).select_from(model)) or 0


@pytest.mark.parametrize("iteration", range(10))
def test_delete_racing_the_end_of_a_run_does_not_deadlock(
    stage: Stage, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, iteration: int
) -> None:
    """The delete holds the thread's lock when the worker starts the transaction that ends the
    run; the delete goes on (cancels the run, deletes the thread) once the worker is in it. With
    the old order the worker has updated the run by then and waits for the thread, the delete
    waits for the run, and Postgres aborts one of the two (deadlock detected). With the thread
    locked first, the worker waits for the delete, then finds the thread gone and stores no
    answer."""
    rc, user, thread = stage
    run = say(factory, user, thread, "Does this fit me?")

    delete_has_thread = threading.Event()
    worker_is_finishing = threading.Event()
    errors: list[BaseException] = []
    outcome: dict[str, Any] = {}

    real_get_thread = chat.get_thread
    real_finish = runner.Recorder._finish

    def get_thread_then_wait(*args: Any, **kwargs: Any) -> AgentThread | None:
        locked = real_get_thread(*args, **kwargs)
        delete_has_thread.set()
        # Let the worker take its first lock (with the old order, the run's) before going on.
        assert worker_is_finishing.wait(WAIT_S)
        lock_wait_seen(factory, 2.0)  # the worker waits for a row: the thread's, or the delete's
        return locked

    def finish_after_the_delete_started(self: runner.Recorder, *args: Any) -> bool:
        assert delete_has_thread.wait(WAIT_S)
        worker_is_finishing.set()
        return real_finish(self, *args)

    monkeypatch.setattr(chat, "get_thread", get_thread_then_wait)
    monkeypatch.setattr(runner.Recorder, "_finish", finish_after_the_delete_started)

    def work() -> None:
        try:
            outcome["status"] = execute(rc, run)
        except BaseException as exc:
            errors.append(exc)
            worker_is_finishing.set()

    def delete() -> None:
        try:
            with factory() as db, db.begin():
                outcome["deleted"] = chat.delete_thread(db, user, thread, now())
        except BaseException as exc:
            errors.append(exc)
            delete_has_thread.set()

    # Daemon threads: a test that fails with one of them still waiting must not keep pytest alive.
    threads = [
        threading.Thread(target=work, daemon=True),
        threading.Thread(target=delete, daemon=True),
    ]
    for each in threads:
        each.start()
    for each in threads:
        each.join(WAIT_S * 2)
    assert not any(each.is_alive() for each in threads)
    assert errors == []
    # The delete went first: the run was asked to stop and the thread is gone; the worker then
    # ended its run (the answer was complete) and stored it nowhere.
    assert outcome == {"status": RunStatus.done, "deleted": True}
    assert count(factory, AgentThread) == 0
    assert count(factory, AgentMessage) == 0
    row = run_row(factory, run)
    assert row.status is RunStatus.done
    assert row.cancel_requested_at is not None
    assert row.output is not None
    assert row.output["text"].startswith("You asked: Does this fit me?")


def test_a_delete_arriving_while_the_answer_is_stored_waits_for_it(
    stage: Stage, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other way round: the worker holds the thread and the run when the delete arrives.
    The delete waits for the thread, then finds the answer stored and nothing to cancel."""
    rc, user, thread = stage
    run = say(factory, user, thread, "Does this fit me?")

    worker_has_both = threading.Event()
    errors: list[BaseException] = []
    outcome: dict[str, Any] = {}
    seen: dict[str, Any] = {}
    real_record_reply = chat.record_reply

    def record_then_wait(db: Session, *args: Any) -> bool:
        stored = real_record_reply(db, *args)
        worker_has_both.set()
        assert lock_wait_seen(factory)  # the delete is waiting for the thread's lock
        seen["thread_still_there"] = count(factory, AgentThread) == 1
        return stored

    monkeypatch.setattr(chat, "record_reply", record_then_wait)

    def work() -> None:
        try:
            outcome["status"] = execute(rc, run)
        except BaseException as exc:
            errors.append(exc)
            worker_has_both.set()

    def delete() -> None:
        assert worker_has_both.wait(WAIT_S)
        try:
            with factory() as db, db.begin():
                # The run's end is committed by the time the thread's lock is granted.
                locked = chat.get_thread(db, user, thread, lock=True)
                assert locked is not None
                seen["active"] = chat.active_run(db, thread)
                seen["roles"] = list(
                    db.scalars(
                        sa.select(AgentMessage.role)
                        .where(AgentMessage.thread_id == thread)
                        .order_by(AgentMessage.id)
                    )
                )
                outcome["deleted"] = chat.delete_thread(db, user, thread, now())
        except BaseException as exc:
            errors.append(exc)

    # Daemon threads: a test that fails with one of them still waiting must not keep pytest alive.
    threads = [
        threading.Thread(target=work, daemon=True),
        threading.Thread(target=delete, daemon=True),
    ]
    for each in threads:
        each.start()
    for each in threads:
        each.join(WAIT_S * 2)
    assert not any(each.is_alive() for each in threads)
    assert errors == []
    assert outcome == {"status": RunStatus.done, "deleted": True}
    assert seen == {
        "thread_still_there": True,
        "active": None,
        "roles": [MessageRole.user, MessageRole.assistant],
    }
    assert count(factory, AgentThread) == 0
    assert count(factory, AgentMessage) == 0
    row = run_row(factory, run)
    assert row.status is RunStatus.done
    assert row.cancel_requested_at is None


def _statements(engine: sa.Engine) -> tuple[list[str], Any]:
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        seen.append(" ".join(statement.split()))

    sa.event.listen(engine, "before_cursor_execute", record)
    return seen, record


def test_the_worker_locks_the_thread_before_it_updates_the_run(
    stage: Stage, factory: sessionmaker[Session], engine: sa.Engine
) -> None:
    """The order itself, statement by statement: in the transaction that ends a chat run, the
    thread's SELECT ... FOR UPDATE comes before the run's UPDATE."""
    rc, user, thread = stage
    run = say(factory, user, thread, "Does this fit me?")
    statements, listener = _statements(engine)
    try:
        assert execute(rc, run) is RunStatus.done
    finally:
        sa.event.remove(engine, "before_cursor_execute", listener)
    lock = next(
        i for i, s in enumerate(statements) if "FROM agent_threads" in s and "FOR UPDATE" in s
    )
    # The last update of the run's status is its end (the first one is the claim).
    update = max(i for i, s in enumerate(statements) if s.startswith("UPDATE agent_runs SET"))
    reply = next(i for i, s in enumerate(statements) if s.startswith("INSERT INTO agent_messages"))
    assert lock < update < reply
    assert count(factory, AgentMessage) == 2


def test_a_failed_chat_run_takes_no_thread_lock(
    stage: Stage, factory: sessionmaker[Session], engine: sa.Engine
) -> None:
    """Only a run that stores an answer needs the thread: a failed one touches its run alone,
    so it can never wait for (or hold up) a send or a delete on the thread."""
    rc, user, thread = stage
    run = say(factory, user, thread, "hello")
    with factory() as db, db.begin():
        row = db.get(AgentRun, run)
        assert row is not None
        row.input = {**row.input, "script": "crash"}
    statements, listener = _statements(engine)
    try:
        assert execute(rc, run) is RunStatus.failed
    finally:
        sa.event.remove(engine, "before_cursor_execute", listener)
    assert not [s for s in statements if "agent_threads" in s and "FOR UPDATE" in s]


def test_lock_thread_of_run(stage: Stage, factory: sessionmaker[Session]) -> None:
    _, user, thread = stage
    run = say(factory, user, thread, "hello")
    with factory() as db, db.begin():
        assert chat.lock_thread_of_run(db, run) == thread
        # Held until the transaction ends: another transaction cannot take it.
        with factory() as other, other.begin():
            taken = other.scalar(
                sa.select(AgentThread.id)
                .where(AgentThread.id == thread)
                .with_for_update(skip_locked=True)
            )
            assert taken is None
        # A run that answers no message (unknown, or of another agent) has no thread.
        assert chat.lock_thread_of_run(db, uuid.uuid4()) is None
    with factory() as db, db.begin():
        db.execute(sa.delete(AgentThread).where(AgentThread.id == thread))
    with factory() as db, db.begin():
        assert chat.lock_thread_of_run(db, run) is None
        assert chat.record_reply(db, run, "too late", now()) is False


def test_the_wait_for_the_threads_lock_has_a_bound(
    stage: Stage, factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    """Issue #37, point 6. A session holds the thread's row and does not let go (a stuck
    request, a forgotten psql). The worker ending a chat run used to wait for it without any
    bound, its slot taken for as long. It now waits `thread_lock_wait_s`: the end is not
    recorded by this worker, which says so and is free again, and nothing is stored twice."""
    rc, user, thread = stage
    rc = replace(rc, thread_lock_wait_s=0.3)
    run = say(factory, user, thread, "Does this fit me?")
    outcome: dict[str, Any] = {}
    errors: list[BaseException] = []

    def work() -> None:
        try:
            outcome["status"] = execute(rc, run)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=work, daemon=True)
    with factory() as holder, caplog.at_level(logging.INFO):
        held = holder.scalar(
            sa.select(AgentThread.id).where(AgentThread.id == thread).with_for_update()
        )
        assert held == thread
        worker.start()
        worker.join(WAIT_S)
        still_waiting = worker.is_alive()
        holder.rollback()
    worker.join(WAIT_S)
    assert not still_waiting  # the worker came back while the row was still held
    assert errors == []
    assert "agent run end not recorded" in caplog.text
    row = run_row(factory, run)
    assert row.finished_at is None  # left to the reaper, like any run its worker could not end
    with factory() as db:
        answers = db.scalar(
            sa.select(sa.func.count())
            .select_from(AgentMessage)
            .where(AgentMessage.role == MessageRole.assistant)
        )
    assert answers == 0


def test_the_bound_is_the_transactions_own(stage: Stage, factory: sessionmaker[Session]) -> None:
    """`SET LOCAL`: the timeout holds inside the transaction that ends the run and is gone
    with it, so the next user of the pooled connection waits as it always did."""
    rc, user, thread = stage
    run = say(factory, user, thread, "Does this fit me?")
    assert rc.thread_lock_wait_s == runner.THREAD_LOCK_WAIT_S == 10.0
    with factory() as db:
        runner._chat_lock(run, 0.25)(db)
        assert db.scalar(sa.text("SHOW lock_timeout")) == "250ms"
        db.rollback()
        assert db.scalar(sa.text("SHOW lock_timeout")) == "0"
