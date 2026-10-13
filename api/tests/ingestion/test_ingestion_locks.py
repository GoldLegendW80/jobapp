"""The lock order of the ingestion module, for the pairs of paths that exist at G10, each run
from two connections against real Postgres.

The order (tasks/ingestion/todo.md, "Lock order"): the `ingestion_runs` row (3), the
`ingestion_documents` rows (4), the `ingestion_run_steps` row (5), the user's advisory lock of
`runs.create_run` (6), the `agent_runs` row (7). Who takes what today: `advance` 3, then 5, 6
and 7; cancel, resume and continue 3 then 7; a tool call of a step 4 (`FOR KEY SHARE`) then 5,
never the run row; a delete of a document 4; the runner and the reapers 7 only; the agents
route 6 then 7.

Each test holds the locks of one path in an open transaction, starts the other path in a
thread, asserts that it waits (or that it does not), releases, and asserts the end state. Two
paths that both go up the list cannot wait for each other, so each pair ends without Postgres
breaking a deadlock. The pairs of the merge, of the review and of the deletion come with G12,
G13 and G15, each in a file of its own."""

import threading
import uuid
from collections.abc import Callable
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runs as agent_runs
from app.agents.crypto import Keyring
from app.agents.models import AgentRun
from app.agents.models import RunStatus as AgentStatus
from app.config import Settings
from app.ingestion import delete_guard, documents, engine, tools
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import IngestionRunStep, RunStatus
from tests.agents.run_helpers import add_user, new_run, now, save_credential
from tests.ingestion import engine_helpers as eh
from tests.ingestion.extract_helpers import (
    add_document,
    add_step,
    call,
    key,
    keyring,
    settings,
    tools_for,
)

_FIXTURES = (key, keyring, settings)
WAIT_S = 0.6  # long enough to tell a path that waits from one that is merely slow to start
END_S = 15


@pytest.fixture
def alice(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)
    return user


class Path:
    """One path run in a thread: whether it has ended, and what it answered or raised."""

    def __init__(self, work: Callable[[], Any]) -> None:
        self.done = threading.Event()
        self.answer: Any = None
        self.error: BaseException | None = None

        def run() -> None:
            try:
                self.answer = work()
            except BaseException as exc:
                self.error = exc
            finally:
                self.done.set()

        self.thread = threading.Thread(target=run)
        self.thread.start()

    def waits(self) -> None:
        assert not self.done.wait(WAIT_S), "the path did not wait for the lock"

    def ends(self) -> Any:
        assert self.done.wait(END_S), "the path never ended"
        self.thread.join()
        if self.error is not None:
            raise self.error
        return self.answer


def tool_call_locks(db: Session, step: IngestionRunStep) -> None:
    """The locks of `propose_profile_changes` in flight: its document `FOR KEY SHARE` (4), then
    its step row `FOR UPDATE` (5)."""
    db.execute(
        sa.select(IngestionDocument.id)
        .where(IngestionDocument.id == step.document_id)
        .with_for_update(key_share=True)
    )
    db.execute(
        sa.select(IngestionRunStep.id).where(IngestionRunStep.id == step.id).with_for_update()
    )


def started(
    factory: sessionmaker[Session], user: uuid.UUID, settings: Settings, count: int = 2
) -> uuid.UUID:
    run_id = eh.start(factory, user, eh.documents(factory, user, count))
    eh.advance(factory, run_id, settings)
    return run_id


def cancel(factory: sessionmaker[Session], user: uuid.UUID, run_id: uuid.UUID) -> None:
    with factory() as db, db.begin():
        engine.request_cancel(db, user, run_id, now())


# --- advance against itself and against the user's actions ------------------------------------


def test_advance_and_advance(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 2))
    with factory() as db, db.begin():
        engine.advance(db, run_id, now(), settings)  # holds 3, 5, 6 and 7
        other = Path(lambda: eh.advance(factory, run_id, settings))
        other.waits()
    other.ends()
    assert len(eh.agent_runs_of(factory, alice)) == 1  # one effect


@pytest.mark.parametrize("action", [engine.request_cancel, engine.resume, engine.continue_])
def test_a_users_action_and_advance(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings, action: Any
) -> None:
    run_id = started(factory, alice, settings)
    eh.finish_step(factory, run_id, settings, tokens=eh.run_of(factory, run_id).token_ceiling)
    if action is engine.resume:  # paused for another reason than its ceiling
        eh.set_run(factory, run_id, paused_reason=engine.QUEUE_DOWN)
    # Continue carries the figure the page showed: one `H` here, the next step was never cut.
    high = eh.run_of(factory, run_id).estimate_high
    more = {"adds": high} if action is engine.continue_ else {}
    with factory() as db, db.begin():
        assert action(db, alice, run_id, now(), **more) is not None  # holds the run row (3)
        other = Path(lambda: eh.advance(factory, run_id, settings))
        other.waits()
    other.ends()
    run = eh.run_of(factory, run_id)
    if action is engine.request_cancel:
        assert run.status is RunStatus.cancelled
    elif action is engine.resume:
        # Resumed, and paused again before the step: the ceiling is looked at whatever paused it.
        assert (run.status, run.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
    else:
        assert (run.status, len(run.ceiling_history)) == (RunStatus.running, 1)


def test_advance_and_cancel(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    eh.end_agent(factory, eh.running(factory, run_id).agent_run_id)
    with factory() as db, db.begin():
        engine.advance(db, run_id, now(), settings)  # records the end, starts the next step
        other = Path(lambda: cancel(factory, alice, run_id))
        other.waits()
    other.ends()
    eh.advance(factory, run_id, settings)
    assert eh.run_of(factory, run_id).status is RunStatus.cancelled
    assert eh.statuses(factory, run_id) == ["done", "cancelled"]


# --- a tool call of the running step ----------------------------------------------------------


def test_a_tool_call_in_flight_and_advance_recording_the_end_of_its_step(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    step = eh.running(factory, run_id)
    eh.end_agent(factory, step.agent_run_id)
    with factory() as db, db.begin():
        tool_call_locks(db, step)  # 4 then 5, never the run row
        other = Path(lambda: eh.advance(factory, run_id, settings))
        other.waits()  # `advance` holds the run row (3) and waits for the step row (5)
    other.ends()
    assert eh.statuses(factory, run_id) == ["done", "running"]


def test_a_tool_call_in_flight_and_cancel(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    step = eh.running(factory, run_id)
    eh.set_agent(factory, step.agent_run_id, status=AgentStatus.running)
    with factory() as db, db.begin():
        tool_call_locks(db, step)
        # Cancel takes the run row (3) and the agent run (7): nothing the tool call holds.
        Path(lambda: cancel(factory, alice, run_id)).ends()
    with factory() as db:
        assert db.get_one(AgentRun, step.agent_run_id).cancel_requested_at is not None


def test_a_tool_call_in_flight_and_the_delete_of_its_document(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    document_id = add_document(factory, alice, eh.text_of(0))
    step = add_step(factory, alice, document_id)
    made = tools_for(factory, step)  # the tools of the step's agent run, bound before the delete
    with factory() as db:
        row = db.get_one(IngestionRunStep, step.step_id)

    def delete() -> bool:
        with factory() as other, other.begin():
            return documents.delete_document(other, alice, document_id)

    with factory() as db, db.begin():
        tool_call_locks(db, row)
        path = Path(delete)
        path.waits()  # the delete waits for the tool call in flight
    # Until G15 the guard then refuses: a step has read this document.
    with pytest.raises(documents.DeleteRefusedError, match=delete_guard.READ_BY_A_RUN):
        path.ends()
    # With the document gone (what G15's delete will do), the next call finds nothing.
    with factory() as db, db.begin():
        db.execute(sa.delete(IngestionDocument).where(IngestionDocument.id == document_id))
    assert call(made, "read_document", offset=0) == (tools.NOT_FOUND, True)


def test_the_delete_of_a_document_no_step_has_read_and_advance(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    selected = eh.documents(factory, alice, 2)
    run_id = eh.start(factory, alice, selected)
    with factory() as db, db.begin():
        engine.advance(db, run_id, now(), settings)  # holds the run row, no document row

        def delete() -> bool:
            with factory() as other, other.begin():
                return documents.delete_document(other, alice, selected[1])

        assert Path(delete).ends() is True  # the delete takes the document row (4) only
    eh.finish_step(factory, run_id, settings)
    assert eh.statuses(factory, run_id) == ["done", "failed"]  # its step finds no document


# --- the agents module: its route, its runner, its reapers ------------------------------------


def test_the_agents_route_creating_a_run_and_advance(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 1))
    key_of = agent_runs._lock_key(alice)
    with factory() as db, db.begin():
        db.execute(sa.select(sa.func.pg_advisory_xact_lock(key_of)))  # the route holds 6
        other = Path(lambda: eh.advance(factory, run_id, settings))
        other.waits()  # `advance` holds 3 and 5 and waits for 6: the route takes neither
    other.ends()
    assert eh.statuses(factory, run_id) == ["running"]


def test_advance_creating_a_step_and_the_agents_route(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 1))
    with factory() as db, db.begin():
        engine.advance(db, run_id, now(), settings)  # holds 3, 5, 6 and 7
        other = Path(lambda: new_run(factory, alice))
        other.waits()
    other.ends()
    assert len(eh.agent_runs_of(factory, alice)) == 2


def test_the_runner_writing_a_running_step_and_advance(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    agent_id = eh.running(factory, run_id).agent_run_id
    assert agent_id is not None
    eh.set_agent(factory, agent_id, status=AgentStatus.running)
    with factory() as db, db.begin():
        agent_runs.record_tokens(db, agent_id, 10, 1)  # the runner holds the agent row (7)
        # `advance` reads that row without a lock: it does not wait, and changes nothing.
        Path(lambda: eh.advance(factory, run_id, settings)).ends()
    assert eh.statuses(factory, run_id) == ["running", "planned"]


def test_the_runner_writing_a_running_step_and_the_cancel_of_the_run(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    agent_id = eh.running(factory, run_id).agent_run_id
    assert agent_id is not None
    eh.set_agent(factory, agent_id, status=AgentStatus.running)
    with factory() as db, db.begin():
        agent_runs.record_tokens(db, agent_id, 10, 1)
        other = Path(lambda: cancel(factory, alice, run_id))
        other.waits()  # the cancel holds the run row (3) and waits for the agent row (7)
    other.ends()  # the runner never takes the run row, so nothing waits for the cancel
    with factory() as db:
        assert db.get_one(AgentRun, agent_id).cancel_requested_at is not None


def test_a_reaper_failing_the_step_and_advance(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    step = eh.running(factory, run_id)
    eh.set_agent(factory, step.agent_run_id, status=AgentStatus.running, started_at=now())
    with factory() as db, db.begin():
        assert agent_runs.reap_orphans(db, now()) == 1  # holds the agent row (7), uncommitted
        Path(lambda: eh.advance(factory, run_id, settings)).ends()  # reads the old state
        assert eh.statuses(factory, run_id) == ["running", "planned"]
    eh.advance(factory, run_id, settings)  # the next tick reads the failure: once more
    again = eh.running(factory, run_id)
    assert (again.id, again.attempt) == (step.id, 2)
    assert again.agent_run_id != step.agent_run_id
