"""The chain of an ingestion run (ingestion G10) against real Postgres: start, advance, pause,
resume, continue, cancel, and every row of "Failure rules". Each test is named after the edge
case of the plan it covers.

The end of a step's agent run is written to `agent_runs` as the runner or a reaper writes it
(`engine_helpers.end_agent`), so each failure is exactly the one named; the last tests run the
real runner on the fake backend, through the `on_final` hook."""

import asyncio
import itertools
import math
import subprocess
import sys
import threading
import uuid
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import failures
from app.agents import runs as agent_runs
from app.agents.crypto import Keyring
from app.agents.models import AgentRun
from app.agents.models import RunStatus as AgentStatus
from app.config import Settings
from app.ingestion import agents, deletion, engine, estimate, estimate_router, plan
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import IngestionRun, RunStatus, text_sha256
from tests.agents.g6_helpers import run_context
from tests.agents.push_backend import COUNTED, INPUT_SIDE, OUTPUT, PushBackend, looping
from tests.agents.run_helpers import add_user, execute, new_run, now, save_credential
from tests.identity.api_helpers import Api
from tests.ingestion import engine_helpers as eh
from tests.ingestion.conftest import CAROL
from tests.ingestion.extract_helpers import add_document, key, keyring, settings
from tests.ingestion.test_estimate_api import user_id

_FIXTURES = (key, keyring, settings)

PROVIDER = [
    failures.NO_CREDENTIAL,
    failures.UNREADABLE,
    failures.UNUSABLE,  # a stored credential the provider will not take
    failures.REFUSED,
    failures.BUSY,
    failures.NO_CREDIT,  # a key without credit, whatever its HTTP status
    agent_runs.QUEUE_DOWN,
    failures.API_ERROR.format(status=500),
    failures.API_ERROR.format(status=402),
]
BOX = [
    failures.NO_CLI,
    failures.NO_START,
    failures.PROCESS.format(code=1),
    failures.INTERNAL,
    failures.UNKNOWN_AGENT,
]
REAPED = [agent_runs.INTERRUPTED, agent_runs.STOPPED, agent_runs.LOST, agent_runs.STALE]


@pytest.fixture
def alice(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)  # an API key
    return user


@pytest.fixture
def bob(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "bob@example.org")
    save_credential(factory, keyring, user)
    return user


def minute(number: int) -> Any:
    """The clock of the cron's tick number `number`: one minute after the tick before."""
    return lambda: now() + timedelta(minutes=number)


def started(
    factory: sessionmaker[Session], user: uuid.UUID, settings: Settings, count: int = 3
) -> uuid.UUID:
    """A run over `count` small documents, its first step running."""
    run_id = eh.start(factory, user, eh.documents(factory, user, count))
    assert eh.advance(factory, run_id, settings).enqueue is not None
    return run_id


def at_step(
    factory: sessionmaker[Session], user: uuid.UUID, settings: Settings, index: int
) -> uuid.UUID:
    """A run of three steps whose step number `index` is running."""
    run_id = started(factory, user, settings)
    for _ in range(index):
        eh.finish_step(factory, run_id, settings)
    assert eh.running(factory, run_id).position == index
    return run_id


# --- the start --------------------------------------------------------------------------------


def test_a_document_of_exactly_one_part_and_of_three_parts(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    one = "a" * plan.PART_MAX_CHARS
    three = ("x" * 998 + "\n\n") * 250
    first = add_document(factory, alice, one)
    second = add_document(factory, alice, three)
    run_id = eh.start(factory, alice, [first, second])
    steps = eh.steps_of(factory, run_id)
    assert [(step.document_id, step.part) for step in steps] == [
        (first, 0),
        (second, 0),
        (second, 1),
        (second, 2),
    ]
    assert (steps[0].range_start, steps[0].range_end) == (0, plan.PART_MAX_CHARS)
    assert steps[1].range_start == 0
    assert [step.range_start for step in steps[2:]] == [step.range_end for step in steps[1:3]]
    assert steps[3].range_end == len(three)
    # Every extraction step carries the hash of the stored text it was planned on.
    assert bytes(steps[0].text_sha256 or b"") == text_sha256(one)
    assert {bytes(step.text_sha256 or b"") for step in steps[1:]} == {text_sha256(three)}
    run = eh.run_of(factory, run_id)
    assert run.status is RunStatus.running
    assert run.token_ceiling == 2 * run.estimate_high > 0
    assert run.document_ids == [first, second]

    moved = eh.advance(factory, run_id, settings)
    step = eh.running(factory, run_id)
    assert step.position == 0
    assert moved.enqueue == step.agent_run_id
    (agent,) = eh.agent_runs_of(factory, alice)
    assert agent.agent == agents.EXTRACT_NAME
    # The four keys of a step's input, `script` only under the fake backend.
    assert agent.input == {
        "ingestion_run_id": str(run_id),
        "step_id": str(step.id),
        "token_budget": step.token_budget,
    }
    assert step.token_budget == min(
        estimate.extraction(plan.PART_MAX_CHARS).backstop, run.token_ceiling
    )


def test_two_starts_at_once_give_one_run_and_one_409_from_the_index(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = eh.documents(factory, alice, 1)
    both = threading.Barrier(2, timeout=10)
    real = engine.active_run_id
    calls = threading.local()

    def seen_by_both(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
        found = real(db, user_id)
        if not getattr(calls, "waited", False):
            calls.waited = True
            both.wait()  # neither has inserted yet: only the index can tell them apart
        return found

    monkeypatch.setattr(engine, "active_run_id", seen_by_both)
    outcomes: list[Any] = []

    def one() -> None:
        try:
            outcomes.append(eh.start(factory, alice, selected))
        except engine.ActiveRunError as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    made = [one for one in outcomes if isinstance(one, uuid.UUID)]
    refused = [one for one in outcomes if isinstance(one, engine.ActiveRunError)]
    assert len(made) == 1
    assert len(refused) == 1
    assert refused[0].run_id == made[0]  # the 409 names the active run
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(IngestionRun)) == 1


def test_a_second_start_while_a_run_is_paused_is_refused(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings, count=1)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.BUSY)
    assert eh.run_of(factory, run_id).status is RunStatus.paused
    with pytest.raises(engine.ActiveRunError) as refused:
        eh.start(factory, alice, eh.documents(factory, alice, 1, first=10))
    assert refused.value.run_id == run_id


def test_a_start_with_four_other_runs_active_waits_then_goes_on(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    others = [new_run(factory, alice) for _ in range(agent_runs.ACTIVE_PER_USER)]
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 1))
    moved = eh.advance(factory, run_id, settings)
    run = eh.run_of(factory, run_id)
    assert moved.enqueue is None
    assert (run.status, run.waiting) == (RunStatus.running, engine.WAITING_SLOTS)
    assert eh.statuses(factory, run_id) == ["planned"]
    assert len(eh.agent_runs_of(factory, alice)) == len(others)

    eh.end_agent(factory, others[0])
    moved = eh.advance(factory, run_id, settings)
    run = eh.run_of(factory, run_id)
    assert moved.enqueue is not None
    assert run.waiting is None
    assert eh.statuses(factory, run_id) == ["running"]


# --- failure rules ----------------------------------------------------------------------------


@pytest.mark.parametrize("index", [0, 2], ids=["first_step", "third_step"])
@pytest.mark.parametrize("error", PROVIDER)
def test_each_provider_failure_pauses_and_resume_runs_the_failed_step_first(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings, error: str, index: int
) -> None:
    run_id = at_step(factory, alice, settings, index)
    failed = eh.running(factory, run_id)
    before = len(eh.agent_runs_of(factory, alice))
    moved = eh.finish_step(factory, run_id, settings, AgentStatus.failed, error)
    run = eh.run_of(factory, run_id)
    assert (run.status, run.paused_reason) == (RunStatus.paused, error)
    assert moved.enqueue is None
    assert len(eh.agent_runs_of(factory, alice)) == before  # no further agent run created
    assert eh.statuses(factory, run_id) == ["done"] * index + ["planned"] * (3 - index)
    # The cron changes nothing while the run is paused.
    assert eh.advance(factory, run_id, settings).enqueue is None
    assert len(eh.agent_runs_of(factory, alice)) == before

    with factory() as db, db.begin():
        assert engine.resume(db, alice, run_id, now()) is not None
    moved = eh.advance(factory, run_id, settings)
    again = eh.running(factory, run_id)
    assert again.id == failed.id
    assert again.agent_run_id != failed.agent_run_id
    assert moved.enqueue == again.agent_run_id
    assert eh.run_of(factory, run_id).status is RunStatus.running


@pytest.mark.parametrize("index", [0, 2], ids=["first_step", "third_step"])
@pytest.mark.parametrize("error", BOX)
def test_each_failure_of_the_box_pauses_and_resume_runs_the_failed_step_first(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings, error: str, index: int
) -> None:
    run_id = at_step(factory, alice, settings, index)
    failed = eh.running(factory, run_id)
    before = len(eh.agent_runs_of(factory, alice))
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, error)
    run = eh.run_of(factory, run_id)
    assert (run.status, run.paused_reason) == (RunStatus.paused, error)
    assert run.paused_reason != engine.TOKEN_CEILING  # never read as the ceiling
    assert len(eh.agent_runs_of(factory, alice)) == before
    assert eh.statuses(factory, run_id)[index] == "planned"

    with factory() as db, db.begin():
        engine.resume(db, alice, run_id, now())
    eh.advance(factory, run_id, settings)
    assert eh.running(factory, run_id).id == failed.id


def test_a_second_failure_of_the_box_on_one_step_is_its_own_and_the_chain_goes_on(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.INTERNAL)
    with factory() as db, db.begin():
        engine.resume(db, alice, run_id, now())
    eh.advance(factory, run_id, settings)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.INTERNAL)
    assert eh.run_of(factory, run_id).status is RunStatus.running
    assert eh.statuses(factory, run_id) == ["failed", "running", "planned"]


def test_resume_with_the_credential_still_broken(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.REFUSED)
    with factory() as db, db.begin():
        engine.resume(db, alice, run_id, now())
    eh.advance(factory, run_id, settings)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.REFUSED)
    run = eh.run_of(factory, run_id)
    assert (run.status, run.paused_reason) == (RunStatus.paused, failures.REFUSED)
    assert eh.statuses(factory, run_id) == ["planned"] * 3
    assert len(eh.agent_runs_of(factory, alice)) == 2


@pytest.mark.parametrize("status", sorted(failures.CONTENT_STATUSES))
def test_api_error_400_on_one_step_is_a_content_failure_and_the_chain_goes_on(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings, status: int
) -> None:
    run_id = started(factory, alice, settings)
    moved = eh.finish_step(
        factory, run_id, settings, AgentStatus.failed, failures.API_ERROR.format(status=status)
    )
    assert moved.enqueue is not None
    assert eh.run_of(factory, run_id).status is RunStatus.running
    assert eh.statuses(factory, run_id) == ["failed", "running", "planned"]
    run = eh.run_to_the_end(factory, run_id, settings)
    assert (run.status, run.partial) == (RunStatus.done, True)


@pytest.mark.parametrize(
    "error",
    [
        failures.TURN_LIMIT.format(turns=40),
        failures.TIME_LIMIT.format(minutes="15 minutes"),
        failures.TOKEN_LIMIT,
        failures.NO_RESULT,
    ],
    ids=["turn_limit", "time_limit", "backstop", "no_result"],
)
def test_a_step_failing_on_its_turn_limit_its_time_limit_and_its_backstop(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings, error: str
) -> None:
    selected = eh.documents(factory, alice, 2)
    run_id = eh.start(factory, alice, selected)
    eh.set_run(factory, run_id, token_ceiling=10**9)  # so the budget is the step's backstop
    eh.advance(factory, run_id, settings)
    step = eh.running(factory, run_id)
    assert step.token_budget == estimate.extraction(len(eh.text_of(0))).backstop
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, error, tokens=5_000)
    assert eh.run_of(factory, run_id).status is RunStatus.running
    assert eh.statuses(factory, run_id) == ["failed", "running"]


def test_a_time_limit_with_no_api_message_received_pauses_the_chain(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    error = failures.TIME_LIMIT.format(minutes="15 minutes")
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, error, tokens=0)
    run = eh.run_of(factory, run_id)
    assert (run.status, run.paused_reason) == (RunStatus.paused, error)
    assert eh.statuses(factory, run_id) == ["planned"] * 3


def test_a_paused_run_24_hours_old_is_merged_and_closed_partial(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = at_step(factory, alice, settings, 1)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.BUSY)
    eh.advance(factory, run_id, settings, at=now() + timedelta(hours=23, minutes=58))
    assert eh.run_of(factory, run_id).status is RunStatus.paused
    eh.advance(factory, run_id, settings, at=now() + timedelta(hours=24, minutes=1))
    run = eh.run_of(factory, run_id)
    assert (run.status, run.partial) == (RunStatus.done, True)
    assert run.finished_at is not None
    assert eh.statuses(factory, run_id) == ["done", "cancelled", "cancelled"]


@pytest.mark.parametrize("ending", ["continue", "wait"])
def test_a_ceiling_pause_right_after_a_resume_is_a_pause_of_its_own(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings, ending: str
) -> None:
    """The probe of the review of PR #148 (B2): the provider fails a step that spent most of
    what remained, the user resumes ten hours later and the chain pauses at once on its
    ceiling. That second pause follows no agent run, no Continue and no creation: derived from
    those, its start was the first pause's, the run was closed 14 hours after the Resume and the
    deadline moved by the first pause twice."""
    run_id = started(factory, alice, settings, count=1)
    run = eh.run_of(factory, run_id)
    low = math.ceil(estimate.extraction(len(eh.text_of(0))).low)
    start, deadline = now(), run.deadline_at
    eh.end_agent(
        factory,
        eh.running(factory, run_id).agent_run_id,
        AgentStatus.failed,
        failures.BUSY,
        tokens=run.token_ceiling - low + 1,
        at=start,
    )
    eh.advance(factory, run_id, settings, at=start)
    first = eh.run_of(factory, run_id)
    assert (first.status, first.paused_reason, first.paused_at) == (
        RunStatus.paused,
        failures.BUSY,
        start,
    )

    resumed = start + timedelta(hours=10)
    with factory() as db, db.begin():
        engine.resume(db, alice, run_id, resumed)
    agents_before = len(eh.agent_runs_of(factory, alice))
    eh.advance(factory, run_id, settings, at=resumed)
    second = eh.run_of(factory, run_id)
    assert (second.status, second.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
    assert len(eh.agent_runs_of(factory, alice)) == agents_before
    assert second.paused_at == resumed
    assert second.deadline_at == deadline + timedelta(hours=10)

    # 24 hours and one minute after the first pause, 14 hours into this one: still waiting.
    eh.advance(factory, run_id, settings, at=start + timedelta(hours=24, minutes=1))
    assert eh.run_of(factory, run_id).status is RunStatus.paused
    eh.advance(factory, run_id, settings, at=resumed + timedelta(hours=23, minutes=59))
    assert eh.run_of(factory, run_id).status is RunStatus.paused
    if ending == "wait":
        eh.advance(factory, run_id, settings, at=resumed + timedelta(hours=24, minutes=1))
        closed = eh.run_of(factory, run_id)
        assert (closed.status, closed.paused_at) == (RunStatus.failed, None)
        return
    with factory() as db, db.begin():
        engine.continue_(db, alice, run_id, start + timedelta(hours=34), shown(db, run_id))
    going = eh.run_of(factory, run_id)
    # Paused 10 hours, then 24: the clock stopped for 34 hours, counted once.
    assert (going.status, going.paused_at) == (RunStatus.running, None)
    assert going.deadline_at == deadline + timedelta(hours=34)


@pytest.mark.parametrize("error", REAPED)
def test_the_worker_restarted_mid_step(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings, error: str
) -> None:
    run_id = started(factory, alice, settings)
    first = eh.running(factory, run_id)
    moved = eh.finish_step(factory, run_id, settings, AgentStatus.failed, error, tokens=700)
    second = eh.running(factory, run_id)
    # The same step once more, as a new agent run: the old one is final, so nothing runs twice.
    assert second.id == first.id
    assert second.agent_run_id != first.agent_run_id
    assert (second.attempt, moved.enqueue) == (2, second.agent_run_id)
    assert eh.run_of(factory, run_id).status is RunStatus.running
    # A second such failure is the step's own.
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, error, tokens=300)
    assert eh.statuses(factory, run_id) == ["failed", "running", "planned"]
    assert eh.run_of(factory, run_id).tokens_spent == 1_000


def test_a_worker_killed_in_the_middle_of_a_step_that_had_counted_tokens(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings, count=1)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, agent_runs.INTERRUPTED, 4_000)
    assert eh.run_of(factory, run_id).tokens_spent == 4_000
    eh.finish_step(factory, run_id, settings, tokens=9_000)
    run = eh.run_of(factory, run_id)
    assert (run.status, run.tokens_spent) == (RunStatus.done, 13_000)


# --- the token ceiling ------------------------------------------------------------------------


def test_the_ceiling_reached_between_two_steps_and_reached_twice(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    run = eh.run_of(factory, run_id)
    ceiling, high = run.token_ceiling, run.estimate_high
    before = len(eh.agent_runs_of(factory, alice))
    moved = eh.finish_step(factory, run_id, settings, tokens=ceiling)  # ended `done`
    run = eh.run_of(factory, run_id)
    assert (run.status, run.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
    assert moved.enqueue is None
    assert len(eh.agent_runs_of(factory, alice)) == before
    assert eh.statuses(factory, run_id) == ["done", "planned", "planned"]

    with factory() as db, db.begin():
        engine.continue_(db, alice, run_id, now(), shown(db, run_id))
    run = eh.run_of(factory, run_id)
    assert run.token_ceiling == ceiling + high
    assert run.status is RunStatus.running
    (entry,) = run.ceiling_history
    assert (entry["spent"], entry["from"], entry["to"]) == (ceiling, ceiling, ceiling + high)
    eh.advance(factory, run_id, settings)
    assert eh.running(factory, run_id).position == 1

    eh.finish_step(factory, run_id, settings, tokens=high)  # at the raised ceiling again
    assert eh.run_of(factory, run_id).paused_reason == engine.TOKEN_CEILING
    with factory() as db, db.begin():
        engine.continue_(db, alice, run_id, now(), shown(db, run_id))
    run = eh.run_of(factory, run_id)
    assert len(run.ceiling_history) == 2
    assert run.token_ceiling == ceiling + 2 * high


def test_the_ceiling_reached_inside_a_step(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings, count=1)
    step = eh.running(factory, run_id)
    run = eh.run_of(factory, run_id)
    assert step.token_budget == run.token_ceiling  # the remainder, under the step's backstop
    eh.add_item(factory, alice, step)
    eh.finish_step(
        factory, run_id, settings, AgentStatus.failed, failures.TOKEN_LIMIT, run.token_ceiling + 9
    )
    paused = eh.run_of(factory, run_id)
    assert (paused.status, paused.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
    # The interrupted step is back to `planned` and keeps its items.
    assert eh.statuses(factory, run_id) == ["planned"]
    assert eh.items_of(factory, run_id) == 1
    # One `H` would start it again on less than the budget that was not enough: Continue adds
    # what gives it twice that budget, and the run says so before the click.
    with factory() as db:
        adds = engine.continue_adds(db, paused, eh.steps_of(factory, run_id), now())
    assert adds == 2 * run.token_ceiling + 9 > paused.estimate_high

    with factory() as db, db.begin():
        engine.continue_(db, alice, run_id, now(), shown(db, run_id))
    eh.advance(factory, run_id, settings)
    again = eh.running(factory, run_id)
    assert again.id == step.id
    assert again.agent_run_id != step.agent_run_id
    assert again.token_budget == 2 * run.token_ceiling > step.token_budget
    eh.finish_step(factory, run_id, settings, tokens=1_000)
    done = eh.run_of(factory, run_id)
    assert done.status is RunStatus.done
    assert [(one["from"], one["to"]) for one in done.ceiling_history] == [
        (run.token_ceiling, run.token_ceiling + adds)
    ]
    assert eh.items_of(factory, run_id) == 1  # stored once: the step row is the same
    assert eh.steps_of(factory, run_id)[0].items_count == 1


def shown(db: Session, run_id: uuid.UUID) -> int:
    """What the page shows before the click (`tokens.continue_adds` of now): the click sends it
    back, and the engine applies that figure or nothing."""
    run = db.get_one(IngestionRun, run_id)
    return engine.continue_adds(db, run, engine._steps(db, run_id), now())


def cut_and_continue(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    settings: Settings,
    run_id: uuid.UUID,
    overshoots: Sequence[int],
) -> tuple[list[int], int]:
    """The step of a one step run is cut at its budget every time (plus the overshoot of that
    attempt) and the user clicks Continue every time the run asks. The budgets of the step and
    the number of Continues, once the run has ended."""
    budgets: list[int] = []
    clicks = 0
    for _ in range(40):
        run = eh.run_of(factory, run_id)
        if run.status not in engine.ACTIVE:
            return budgets, clicks
        if run.status is RunStatus.paused:
            assert run.paused_reason == engine.TOKEN_CEILING
            spent, agents = run.tokens_spent, len(eh.agent_runs_of(factory, user))
            with factory() as db, db.begin():
                engine.continue_(db, user, run_id, now(), shown(db, run_id))
            clicks += 1
            eh.advance(factory, run_id, settings)
            # The click itself spends nothing and always starts the step.
            assert eh.run_of(factory, run_id).tokens_spent == spent
            assert len(eh.agent_runs_of(factory, user)) == agents + 1
            continue
        budget = eh.running(factory, run_id).token_budget
        assert budget is not None
        budgets.append(budget)
        over = overshoots[(len(budgets) - 1) % len(overshoots)] if overshoots else 0
        eh.finish_step(
            factory, run_id, settings, AgentStatus.failed, failures.TOKEN_LIMIT, budget + over
        )
    raise AssertionError("the run never ended")


def test_a_step_the_ceiling_cut_never_starts_again_on_a_budget_that_already_failed(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """The probe of the review of PR #148 (B1): one document, the step cut exactly at its
    budget, Continue after Continue. It read budgets of `[2H, H, H, H]` and a run that never
    ended."""
    run_id = started(factory, alice, settings, count=1)
    first = eh.run_of(factory, run_id)
    high = first.estimate_high
    backstop = estimate.extraction(len(eh.text_of(1))).backstop
    budgets, clicks = cut_and_continue(factory, alice, settings, run_id, ())
    assert budgets[0] == 2 * high
    assert all(later > earlier for earlier, later in itertools.pairwise(budgets))
    assert budgets == [min(backstop, 2 * high * 2**index) for index in range(len(budgets))]
    assert budgets[-1] == backstop
    ended = eh.run_of(factory, run_id)
    # Cut at its backstop, the step has failed for good: nothing was read, the run is closed.
    assert (ended.status, eh.statuses(factory, run_id)) == (RunStatus.failed, ["failed"])
    assert ended.tokens_spent == sum(budgets) < 3 * backstop
    assert clicks == len(budgets) - 1 == len(ended.ceiling_history)


@pytest.mark.parametrize(
    "overshoots",
    [(0,), (1,), (9, 0, 70_000), (88_110,), (300_000, 1), (5, 500_000), (1_000_000,)],
)
def test_any_sequence_of_continues_on_one_step_is_bounded_and_ends(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings, overshoots: tuple[int]
) -> None:
    """Whatever each attempt overshoots by: the budgets of the step grow strictly, the last is
    its backstop at most, the cuts number `1 + ceil(log2(backstop / first budget))` at most,
    and the run has spent under three backstops plus the overshoots when it ends."""
    run_id = started(factory, alice, settings, count=1)
    backstop = estimate.extraction(len(eh.text_of(1))).backstop
    budgets, clicks = cut_and_continue(factory, alice, settings, run_id, overshoots)
    assert all(later > earlier for earlier, later in itertools.pairwise(budgets))
    assert budgets[-1] <= backstop
    assert len(budgets) <= 1 + math.ceil(math.log2(backstop / budgets[0]))
    ended = eh.run_of(factory, run_id)
    assert ended.status is RunStatus.failed
    over = sum(overshoots[turn % len(overshoots)] for turn in range(len(budgets)))
    assert ended.tokens_spent == sum(budgets) + over
    assert sum(budgets) < 3 * backstop
    assert clicks == len(ended.ceiling_history) == len(budgets) - 1


def click(
    factory: sessionmaker[Session], user: uuid.UUID, settings: Settings, run_id: uuid.UUID
) -> int:
    """One Continue on the figure the page shows, then the engine moves. That figure."""
    with factory() as db, db.begin():
        adds = shown(db, run_id)
        engine.continue_(db, user, run_id, now(), adds)
    eh.advance(factory, run_id, settings)
    return adds


def test_the_floor_of_a_cut_step_survives_a_worker_restart_and_a_provider_failure(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """N1 of the second review of PR #148, the reviewer's sequence: cut, worker restart, cut,
    provider failure, cut. The floor was read from the step's last agent run, so any other
    failure reset it to the low estimate: the step ran again on 52,440 and 104,880 after a cut
    at 176,220, six cuts against a stated three. The largest budget the ceiling cut a step on is
    now a stored fact of the step, and every attempt starts above it or does not start."""
    run_id = started(factory, alice, settings, count=1)
    backstop = estimate.extraction(len(eh.text_of(1))).backstop
    cuts: list[int] = []
    others: list[int] = []

    def budget() -> int:
        value = eh.running(factory, run_id).token_budget
        assert value is not None
        # Never on a budget the ceiling already cut it on, nor on less.
        assert all(value > cut for cut in cuts), (value, cuts)
        return value

    def cut() -> None:
        cuts.append(budget())
        eh.finish_step(
            factory, run_id, settings, AgentStatus.failed, failures.TOKEN_LIMIT, cuts[-1]
        )

    def on_the_ceiling() -> None:
        run = eh.run_of(factory, run_id)
        assert (run.status, run.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
        assert eh.steps_of(factory, run_id)[0].cut_budget == cuts[-1]

    cut()  # 1. cut on its first budget
    on_the_ceiling()
    assert click(factory, alice, settings, run_id) == 2 * cuts[0]

    # 2. The worker restarts under the second attempt. The engine runs the step again with no
    # click, and what remains under the ceiling is less than the budget that already failed.
    agents_before = len(eh.agent_runs_of(factory, alice))
    others.append(budget() - 52_440)
    eh.finish_step(
        factory, run_id, settings, AgentStatus.failed, agent_runs.INTERRUPTED, others[-1]
    )
    on_the_ceiling()  # no agent run on 52,440: a pause that says what the step needs
    assert len(eh.agent_runs_of(factory, alice)) == agents_before
    assert click(factory, alice, settings, run_id) == 2 * cuts[0] - 52_440

    cut()  # 3. cut again, on twice the first cut
    on_the_ceiling()
    assert click(factory, alice, settings, run_id) == backstop

    # 4. A provider failure under the attempt at the backstop, then Resume.
    assert budget() == backstop
    others.append(70_000)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.BUSY, others[-1])
    assert eh.run_of(factory, run_id).paused_reason == failures.BUSY
    with factory() as db, db.begin():
        engine.resume(db, alice, run_id, now())
    eh.advance(factory, run_id, settings)
    on_the_ceiling()  # the remainder is under the floor: again no agent run
    assert len(eh.agent_runs_of(factory, alice)) == agents_before + 2
    click(factory, alice, settings, run_id)

    cut()  # 5. cut at its backstop: the step's own failure, the run is over
    ended = eh.run_of(factory, run_id)
    assert (ended.status, eh.statuses(factory, run_id)) == (RunStatus.failed, ["failed"])
    # The bound as stated: the budgets that were cut at least double and stop at the backstop.
    assert cuts == [cuts[0], 2 * cuts[0], backstop]
    assert len(cuts) == 1 + math.ceil(math.log2(backstop / cuts[0]))
    assert sum(cuts) < 3 * backstop
    # Each attempt that something else ended adds what it spent, under one backstop each.
    assert ended.tokens_spent == sum(cuts) + sum(others) < (3 + len(others)) * backstop
    assert len(ended.ceiling_history) == 4  # every raise was a click on a figure that was shown


def test_a_continue_applies_the_figure_the_click_carried_or_nothing(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """B1 of the second review of PR #148: the figure is compared under the run's row lock and a
    click that carries another one, lower or higher, writes nothing."""
    run_id = started(factory, alice, settings, count=1)
    budget = eh.running(factory, run_id).token_budget
    assert budget is not None
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.TOKEN_LIMIT, budget)
    paused = eh.run_of(factory, run_id)
    with factory() as db:
        current = shown(db, run_id)
    assert current == 2 * budget > paused.estimate_high
    for stale in (paused.estimate_high, current - 1, current + 1):
        with factory() as db, db.begin(), pytest.raises(engine.ContinueChangedError) as refused:
            engine.continue_(db, alice, run_id, now(), stale)
        assert refused.value.adds == current
        same = eh.run_of(factory, run_id)
        assert (same.status, same.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
        assert (same.token_ceiling, same.ceiling_history) == (paused.token_ceiling, [])
        assert same.paused_at == paused.paused_at
    with factory() as db, db.begin():
        engine.continue_(db, alice, run_id, now(), current)
    raised = eh.run_of(factory, run_id)
    assert raised.token_ceiling == paused.token_ceiling + current
    assert raised.status is RunStatus.running


def test_a_cut_step_whose_floor_the_remainder_does_not_reach_pauses_without_a_model_call(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """The rule holds where agent runs are created, not only in Continue: a ceiling lowered
    behind the engine's back (written by hand here) starts nothing."""
    run_id = started(factory, alice, settings, count=1)
    run = eh.run_of(factory, run_id)
    budget = run.token_ceiling
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.TOKEN_LIMIT, budget)
    # What one `H` used to leave: the cut step would start on half the budget that failed.
    eh.set_run(
        factory,
        run_id,
        status=RunStatus.running,
        paused_reason=None,
        token_ceiling=budget + run.estimate_high,
    )
    before = len(eh.agent_runs_of(factory, alice))
    eh.advance(factory, run_id, settings)
    paused = eh.run_of(factory, run_id)
    assert (paused.status, paused.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
    assert len(eh.agent_runs_of(factory, alice)) == before
    assert paused.tokens_spent == budget


def test_what_remains_just_below_the_next_steps_low_estimate_pauses_before_the_step(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings, count=2)
    run = eh.run_of(factory, run_id)
    low = estimate.extraction(len(eh.text_of(1))).low
    before = len(eh.agent_runs_of(factory, alice))
    eh.finish_step(factory, run_id, settings, tokens=run.token_ceiling - int(low) + 1)
    run = eh.run_of(factory, run_id)
    assert (run.status, run.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
    assert len(eh.agent_runs_of(factory, alice)) == before  # no agent run created
    assert run.tokens_spent < run.token_ceiling


def test_what_remains_at_the_next_steps_low_estimate_starts_it(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings, count=2)
    run = eh.run_of(factory, run_id)
    low = int(estimate.extraction(len(eh.text_of(1))).low)
    eh.finish_step(factory, run_id, settings, tokens=run.token_ceiling - low)
    assert eh.run_of(factory, run_id).status is RunStatus.running
    assert eh.running(factory, run_id).token_budget == low


def test_a_step_whose_last_request_reached_its_budget_and_ended_done(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """`TOKEN_LIMIT` is not the only sign of a spent ceiling: the ceiling is compared with
    `tokens_spent` before the next step."""
    run_id = started(factory, alice, settings, count=2)
    budget = eh.running(factory, run_id).token_budget or 0
    eh.finish_step(factory, run_id, settings, AgentStatus.done, tokens=budget + 50)
    run = eh.run_of(factory, run_id)
    assert (run.status, run.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
    assert eh.statuses(factory, run_id) == ["done", "planned"]


def test_continue_on_a_run_paused_for_another_reason_and_by_another_user(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    with factory() as db, db.begin(), pytest.raises(engine.WrongStateError):
        engine.continue_(db, alice, run_id, now(), shown(db, run_id))  # running
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.BUSY)
    with factory() as db, db.begin(), pytest.raises(engine.WrongStateError):
        engine.continue_(
            db, alice, run_id, now(), shown(db, run_id)
        )  # paused, but not on its ceiling
    with factory() as db, db.begin():
        assert engine.continue_(db, bob, run_id, now(), shown(db, run_id)) is None
        assert engine.resume(db, bob, run_id, now()) is None
        assert engine.request_cancel(db, bob, run_id, now()) is None
    run = eh.run_of(factory, run_id)
    assert (run.status, run.ceiling_history) == (RunStatus.paused, [])
    assert run.cancel_requested_at is None


def test_resume_on_a_run_paused_on_its_ceiling_is_refused(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    eh.finish_step(factory, run_id, settings, tokens=eh.run_of(factory, run_id).token_ceiling)
    with factory() as db, db.begin(), pytest.raises(engine.WrongStateError):
        engine.resume(db, alice, run_id, now())


# --- the deadline -----------------------------------------------------------------------------


def test_the_60_minute_deadline_while_a_step_runs(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = at_step(factory, alice, settings, 1)
    step = eh.running(factory, run_id)
    eh.set_agent(factory, step.agent_run_id, status=AgentStatus.running, started_at=now())
    late = now() + timedelta(minutes=61)
    before = len(eh.agent_runs_of(factory, alice))
    eh.advance(factory, run_id, settings, at=late)
    with factory() as db:
        assert db.get_one(AgentRun, step.agent_run_id).cancel_requested_at is not None
    assert eh.run_of(factory, run_id).status is RunStatus.running  # until the step has ended

    eh.end_agent(factory, step.agent_run_id, AgentStatus.cancelled, tokens=200)
    eh.advance(factory, run_id, settings, at=late)
    run = eh.run_of(factory, run_id)
    assert (run.status, run.partial) == (RunStatus.done, True)
    assert run.cancel_requested_at is None  # the deadline, not a cancel
    assert eh.statuses(factory, run_id) == ["done", "cancelled", "cancelled"]
    assert len(eh.agent_runs_of(factory, alice)) == before  # no further step created


def test_the_60_minute_deadline_while_the_run_waits_in_the_queue(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)  # its agent run is queued
    step = eh.running(factory, run_id)
    eh.advance(factory, run_id, settings, at=now() + timedelta(minutes=59))
    assert eh.run_of(factory, run_id).status is RunStatus.running
    eh.advance(factory, run_id, settings, at=now() + timedelta(minutes=61))
    run = eh.run_of(factory, run_id)
    assert run.status is RunStatus.failed
    assert run.finished_at is not None
    assert eh.statuses(factory, run_id) == ["cancelled"] * 3
    with factory() as db:
        assert db.get_one(AgentRun, step.agent_run_id).status is AgentStatus.cancelled


def test_a_pause_stops_the_clock_of_the_deadline(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    deadline = eh.run_of(factory, run_id).deadline_at
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.BUSY)
    later = now() + timedelta(hours=3)
    with factory() as db, db.begin():
        engine.resume(db, alice, run_id, later)
    moved = eh.advance(factory, run_id, settings, at=later)
    assert moved.enqueue is not None
    shift = eh.run_of(factory, run_id).deadline_at - deadline
    assert timedelta(hours=2, minutes=59) < shift <= timedelta(hours=3, minutes=1)


# --- cancel -----------------------------------------------------------------------------------


def cancel(factory: sessionmaker[Session], user: uuid.UUID, run_id: uuid.UUID) -> None:
    with factory() as db, db.begin():
        assert engine.request_cancel(db, user, run_id, now()) is not None


def test_cancel_while_planned_and_waiting(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    for _ in range(agent_runs.ACTIVE_PER_USER):
        new_run(factory, alice)
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 2))
    eh.advance(factory, run_id, settings)
    assert eh.run_of(factory, run_id).waiting == engine.WAITING_SLOTS
    cancel(factory, alice, run_id)
    eh.advance(factory, run_id, settings)
    run = eh.run_of(factory, run_id)
    assert (run.status, run.waiting) == (RunStatus.cancelled, None)
    assert eh.statuses(factory, run_id) == ["cancelled", "cancelled"]


def test_cancel_while_queued(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    step = eh.running(factory, run_id)
    cancel(factory, alice, run_id)
    eh.advance(factory, run_id, settings)
    assert eh.run_of(factory, run_id).status is RunStatus.cancelled
    assert eh.statuses(factory, run_id) == ["cancelled"] * 3
    with factory() as db:
        assert db.get_one(AgentRun, step.agent_run_id).status is AgentStatus.cancelled


def test_cancel_mid_step_keeps_the_items_of_finished_steps(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = at_step(factory, alice, settings, 1)
    eh.add_item(factory, alice, eh.steps_of(factory, run_id)[0])
    step = eh.running(factory, run_id)
    eh.set_agent(factory, step.agent_run_id, status=AgentStatus.running, started_at=now())
    cancel(factory, alice, run_id)
    with factory() as db:  # stopped through the runner's own means
        assert db.get_one(AgentRun, step.agent_run_id).cancel_requested_at is not None
    eh.advance(factory, run_id, settings)
    assert eh.run_of(factory, run_id).status is RunStatus.running  # honoured between steps
    assert eh.statuses(factory, run_id)[1] == "running"

    eh.end_agent(factory, step.agent_run_id, AgentStatus.cancelled)
    eh.advance(factory, run_id, settings)
    run = eh.run_of(factory, run_id)
    assert (run.status, run.partial) == (RunStatus.cancelled, False)  # no merge
    assert eh.statuses(factory, run_id) == ["done", "cancelled", "cancelled"]
    assert eh.items_of(factory, run_id) == 1


def test_cancel_while_paused(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.BUSY)
    cancel(factory, alice, run_id)
    eh.advance(factory, run_id, settings)
    assert eh.run_of(factory, run_id).status is RunStatus.cancelled
    # A later run can start: the cancelled one is no longer the user's active run.
    eh.start(factory, alice, eh.documents(factory, alice, 1, first=10))


def test_a_single_step_cancelled_through_the_agents_route(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = at_step(factory, alice, settings, 1)
    step = eh.running(factory, run_id)
    with factory() as db, db.begin():  # what POST /api/agents/runs/{id}/cancel does
        assert step.agent_run_id is not None
        agent_runs.request_cancel(db, alice, step.agent_run_id, now())
    eh.advance(factory, run_id, settings)
    run = eh.run_of(factory, run_id)
    assert run.status is RunStatus.cancelled
    assert run.cancel_requested_at is not None
    assert eh.statuses(factory, run_id) == ["done", "cancelled", "cancelled"]


def test_cancel_during_advance_waits_for_the_row_lock(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 2))
    done = threading.Event()

    def cancelling() -> None:
        cancel(factory, alice, run_id)
        done.set()

    with factory() as db, db.begin():
        engine.advance(db, run_id, now(), settings)  # holds the run row
        thread = threading.Thread(target=cancelling)
        thread.start()
        assert not done.wait(0.5)  # the cancel waits for `advance`
    thread.join(timeout=10)
    assert done.is_set()
    eh.advance(factory, run_id, settings)
    assert eh.run_of(factory, run_id).status is RunStatus.cancelled
    (agent,) = eh.agent_runs_of(factory, alice)  # the step `advance` created, cancelled queued
    assert agent.status is AgentStatus.cancelled


# --- one effect -------------------------------------------------------------------------------


def test_advance_called_from_two_connections_at_once_and_ten_times_in_a_row(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 3))
    go = threading.Barrier(2, timeout=10)
    moved: list[engine.Moved] = []

    def one() -> None:
        go.wait()
        moved.append(eh.advance(factory, run_id, settings))

    threads = [threading.Thread(target=one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert len(moved) == 2
    for _ in range(10):
        eh.advance(factory, run_id, settings)
    (agent,) = eh.agent_runs_of(factory, alice)  # one step started, once
    assert eh.statuses(factory, run_id) == ["running", "planned", "planned"]
    assert {one.enqueue for one in moved} == {agent.id}  # offered again while queued, same id

    eh.end_agent(factory, agent.id, tokens=1_234)
    for _ in range(10):
        eh.advance(factory, run_id, settings)
    assert eh.run_of(factory, run_id).tokens_spent == 1_234  # counted once
    assert len(eh.agent_runs_of(factory, alice)) == 2


def test_advance_called_while_a_running_step_writes_its_events(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings)
    agent_id = eh.running(factory, run_id).agent_run_id
    assert agent_id is not None
    eh.set_agent(factory, agent_id, status=AgentStatus.running, started_at=now())
    with factory() as db, db.begin():
        engine.advance(db, run_id, now(), settings)  # not committed yet
        with factory() as other, other.begin():
            other.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
            # No event write and no token record waits on `advance`.
            agent_runs.append_event(
                other, agent_id, agent_runs.next_seq(other, agent_id), "note", {}, now()
            )
            agent_runs.record_tokens(other, agent_id, 10, 1)


def test_redis_down_at_the_enqueue(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 2))

    async def down(agent_run_id: uuid.UUID) -> None:
        raise queues.QueueUnavailableError("down")

    status = asyncio.run(engine.drive(factory, down, run_id, now, settings))
    run = eh.run_of(factory, run_id)
    assert status is RunStatus.paused
    assert (run.status, run.paused_reason) == (RunStatus.paused, engine.QUEUE_DOWN)
    (agent,) = eh.agent_runs_of(factory, alice)
    assert (agent.status, agent.error) == (AgentStatus.failed, agent_runs.QUEUE_DOWN)
    assert eh.statuses(factory, run_id) == ["planned", "planned"]

    offered: list[uuid.UUID] = []

    async def up(agent_run_id: uuid.UUID) -> None:
        offered.append(agent_run_id)

    with factory() as db, db.begin():
        engine.resume(db, alice, run_id, now())
    asyncio.run(engine.drive(factory, up, run_id, now, settings))
    assert offered == [eh.running(factory, run_id).agent_run_id]


def test_the_hook_never_called_the_cron_advances_within_a_minute(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID, settings: Settings
) -> None:
    first = started(factory, alice, settings)
    second = started(factory, bob, settings, count=1)
    eh.end_agent(factory, eh.running(factory, first).agent_run_id)  # and no hook
    eh.end_agent(factory, eh.running(factory, second).agent_run_id)
    offered: list[uuid.UUID] = []

    async def enqueue(agent_run_id: uuid.UUID) -> None:
        offered.append(agent_run_id)

    assert asyncio.run(engine.tick(factory, enqueue, now, settings)) == 2
    assert eh.statuses(factory, first) == ["done", "running", "planned"]
    assert offered == [eh.running(factory, first).agent_run_id]
    assert eh.run_of(factory, second).status is RunStatus.done
    # A tick with nothing ended offers the queued agent run again, under the same job id.
    asyncio.run(engine.tick(factory, enqueue, now, settings))
    assert offered == [eh.running(factory, first).agent_run_id] * 2
    assert len(eh.agent_runs_of(factory, alice)) == 2


def test_the_hook_cut_while_its_thread_still_works_has_one_effect(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """The hook's `advance` and the cron's, at once, on the same ended step."""
    run_id = started(factory, alice, settings)
    eh.end_agent(factory, eh.running(factory, run_id).agent_run_id, tokens=500)

    async def enqueue(agent_run_id: uuid.UUID) -> None:
        return None

    async def both() -> None:
        await asyncio.gather(
            engine.drive(factory, enqueue, run_id, now, settings),
            engine.tick(factory, enqueue, now, settings),
        )

    asyncio.run(both())
    assert eh.statuses(factory, run_id) == ["done", "running", "planned"]
    assert eh.run_of(factory, run_id).tokens_spent == 500
    assert len(eh.agent_runs_of(factory, alice)) == 2


# --- reuse ------------------------------------------------------------------------------------


def test_a_rerun_of_the_same_documents_reuses_every_step_and_never_pauses(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    selected = eh.documents(factory, alice, 2)
    first = eh.run_to_the_end(factory, eh.start(factory, alice, selected), settings)
    assert (first.status, first.partial) == (RunStatus.done, False)
    before = len(eh.agent_runs_of(factory, alice))

    # The estimate after a finished run: the documents are shown as reused.
    with factory() as db:
        quote = estimate_router.quote(db, alice, [str(one) for one in selected])
    assert [(one.steps, one.reused_steps) for one in quote.documents] == [(1, 1), (1, 1)]
    assert (quote.estimate.low, quote.estimate.high, quote.estimate.ceiling) == (0, 0, 0)

    run_id = eh.start(factory, alice, selected)
    assert eh.statuses(factory, run_id) == ["reused", "reused"]
    moved = eh.advance(factory, run_id, settings)
    run = eh.run_of(factory, run_id)
    # `C = 0` and nothing spent: the ceiling is not looked at, the run goes straight to the merge.
    assert (run.status, run.partial, run.token_ceiling) == (RunStatus.done, False, 0)
    assert moved.enqueue is None
    assert run.paused_reason is None
    assert len(eh.agent_runs_of(factory, alice)) == before  # zero agent runs


def test_reread_document_ids_reads_a_finished_document_again(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    selected = eh.documents(factory, alice, 2)
    eh.run_to_the_end(factory, eh.start(factory, alice, selected), settings)
    run_id = eh.start(factory, alice, selected, reread=[selected[1]])
    assert eh.statuses(factory, run_id) == ["reused", "planned"]
    assert eh.advance(factory, run_id, settings).enqueue is not None
    assert eh.running(factory, run_id).document_id == selected[1]


def test_the_steps_of_a_cancelled_run_are_reused_and_those_of_another_user_are_not(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID, settings: Settings
) -> None:
    selected = eh.documents(factory, alice, 2)
    run_id = eh.start(factory, alice, selected)
    eh.advance(factory, run_id, settings)
    eh.finish_step(factory, run_id, settings)
    cancel(factory, alice, run_id)
    eh.advance(factory, run_id, settings)
    assert eh.statuses(factory, eh.start(factory, alice, selected)) == ["reused", "planned"]
    # The same bytes under another user: nothing of Alice's counts for Bob.
    with factory() as db:
        digests = [db.get_one(IngestionDocument, one).sha256 for one in selected]
    theirs = [
        add_document(factory, bob, eh.text_of(number), digest=digest)
        for number, digest in enumerate(digests)
    ]
    assert eh.statuses(factory, eh.start(factory, bob, theirs)) == ["planned", "planned"]


# --- a replaced or deleted document -----------------------------------------------------------


def replace_text(factory: sessionmaker[Session], document_id: uuid.UUID, text: str) -> None:
    with factory() as db, db.begin():
        db.execute(
            sa.update(IngestionDocument)
            .where(IngestionDocument.id == document_id)
            .values(text=text)
        )


def test_a_text_replaced_before_its_step_is_planned_again_from_the_new_text(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    selected = eh.documents(factory, alice, 2)
    run_id = started_over(factory, alice, settings, selected)
    first = eh.running(factory, run_id)
    eh.add_item(factory, alice, first)
    new_text = "Another made-up text.\n\n" + "other " * 400
    replace_text(factory, selected[0], new_text)  # the text its running step was planned on
    replace_text(factory, selected[1], new_text + "second")
    # The step that ran on the old text: the runner fails its next agent run before any call.
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.BINDING_GONE)
    steps = eh.steps_of(factory, run_id)
    assert [(step.document_id, step.status.value) for step in steps] == [
        (selected[0], "failed"),
        (selected[0], "running"),
        (selected[1], "planned"),
    ]
    # Both are planned on the text stored now, with its ranges.
    assert bytes(steps[1].text_sha256 or b"") == text_sha256(new_text)
    assert (steps[1].range_start, steps[1].range_end) == (0, len(new_text))
    assert bytes(steps[2].text_sha256 or b"") == text_sha256(new_text + "second")
    assert (steps[2].range_start, steps[2].range_end) == (0, len(new_text) + 6)
    run = eh.run_to_the_end(factory, run_id, settings)
    assert run.status is RunStatus.done
    # The item of the replaced text is deleted in the transaction that closes the run.
    assert eh.items_of(factory, run_id) == 0


def started_over(
    factory: sessionmaker[Session], user: uuid.UUID, settings: Settings, selected: list[uuid.UUID]
) -> uuid.UUID:
    run_id = eh.start(factory, user, selected)
    eh.advance(factory, run_id, settings)
    return run_id


def test_a_document_deleted_before_its_step_fails_that_step_without_an_agent_run(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    selected = eh.documents(factory, alice, 3)
    run_id = started_over(factory, alice, settings, selected)
    with factory() as db, db.begin():
        db.execute(sa.delete(IngestionDocument).where(IngestionDocument.id == selected[1]))
    before = len(eh.agent_runs_of(factory, alice))
    eh.finish_step(factory, run_id, settings)
    assert eh.statuses(factory, run_id) == ["done", "failed", "running"]
    assert len(eh.agent_runs_of(factory, alice)) == before + 1
    run = eh.run_to_the_end(factory, run_id, settings)
    assert (run.status, run.partial) == (RunStatus.done, True)


def test_replacing_a_document_read_by_a_step_and_one_no_step_has_read(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    selected = eh.documents(factory, alice, 2)
    never = add_document(factory, alice, eh.text_of(9))
    started_over(factory, alice, settings, selected)
    with factory() as db:
        read, planned, unread = (
            db.get_one(IngestionDocument, one) for one in (selected[0], selected[1], never)
        )
        assert deletion.read_by_a_run(db, read) == deletion.REPLACEMENT_REFUSED
        assert deletion.read_by_a_run(db, planned) is None  # only planned: nothing read
        assert deletion.read_by_a_run(db, unread) is None


def guarded(factory: sessionmaker[Session], document_id: uuid.UUID) -> str | None:
    with factory() as db:
        return deletion.read_by_a_run(db, db.get_one(IngestionDocument, document_id))


def test_a_run_that_ended_without_one_token_holds_no_document_back(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """N1 of the review of PR #148: a step that failed at once on a missing credential has an
    agent run and has read nothing. Its run still holds the document while it is the user's
    active run, and no longer once it is cancelled."""
    (document_id,) = eh.documents(factory, alice, 1)
    run_id = eh.start(factory, alice, [document_id])
    eh.advance(factory, run_id, settings)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.NO_CREDENTIAL)
    assert eh.run_of(factory, run_id).status is RunStatus.paused
    assert guarded(factory, document_id) == deletion.REPLACEMENT_REFUSED
    cancel(factory, alice, run_id)
    eh.advance(factory, run_id, settings)
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.tokens_spent) == (RunStatus.cancelled, 0)
    assert guarded(factory, document_id) is None


def test_a_failed_run_that_spent_tokens_still_holds_its_document(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """No item kept, but the events of the step quote the document until a delete removes them."""
    (document_id,) = eh.documents(factory, alice, 1)
    run_id = eh.start(factory, alice, [document_id])
    eh.advance(factory, run_id, settings)
    eh.finish_step(
        factory, run_id, settings, AgentStatus.failed, "A made-up failure of the step.", 5_000
    )
    assert eh.run_of(factory, run_id).status is RunStatus.failed
    assert eh.items_of(factory, run_id) == 0
    assert guarded(factory, document_id) == deletion.REPLACEMENT_REFUSED
    assert " - " not in deletion.REPLACEMENT_REFUSED


def test_a_run_closed_from_a_pause_keeps_no_pause_reason(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = at_step(factory, alice, settings, 1)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.BUSY)
    assert eh.run_of(factory, run_id).paused_reason == failures.BUSY
    cancel(factory, alice, run_id)
    eh.advance(factory, run_id, settings)
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.paused_reason, closed.paused_at) == (
        RunStatus.cancelled,
        None,
        None,
    )


def test_a_run_whose_advance_keeps_raising_is_closed_and_frees_the_user(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """N5 of the review of PR #148: `advance` raising in `_record` (or in the merge) left the run
    active for ever, and the unique index refused every new start."""
    run_id = started(factory, alice, settings, count=2)
    eh.end_agent(factory, eh.running(factory, run_id).agent_run_id, tokens=1_000)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("a made-up value of an item")

    monkeypatch.setattr(engine, "_record", broken)
    with caplog.at_level("INFO", logger="app.ingestion.engine"):
        for number in range(1, engine.ADVANCE_FAILURES):
            assert asyncio.run(engine.tick(factory, None, minute(number), settings)) == 0
            run = eh.run_of(factory, run_id)
            assert (run.status, run.advance_failures) == (RunStatus.running, number)
        asyncio.run(engine.tick(factory, None, minute(engine.ADVANCE_FAILURES), settings))
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.closed_reason) == (RunStatus.failed, engine.STUCK)
    # The step that was running had spent 1,000 tokens: the closed run says so.
    assert closed.tokens_spent == 1_000
    assert closed.finished_at is not None
    assert eh.statuses(factory, run_id) == ["cancelled", "cancelled"]
    with factory() as db:
        assert engine.active_run_id(db, alice) is None  # a new run can start
    # Logged by its type only: the message of an error may quote what it was handling.
    failed = [one for one in caplog.records if one.getMessage() == "ingestion_advance_failed"]
    assert len(failed) == engine.ADVANCE_FAILURES
    assert {one.__dict__["error_type"] for one in failed} == {"RuntimeError"}
    assert all(one.exc_info is None for one in caplog.records)
    assert "made-up value" not in caplog.text
    assert " - " not in engine.STUCK
    # A closed run is left alone by the next tick.
    assert asyncio.run(engine.tick(factory, None, now, settings)) == 0
    assert eh.run_of(factory, run_id).advance_failures == engine.ADVANCE_FAILURES


def test_an_advance_that_succeeds_forgets_the_failures_before_it(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = started(factory, alice, settings, count=2)
    eh.end_agent(factory, eh.running(factory, run_id).agent_run_id, tokens=1_000)
    record = engine._record

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError

    monkeypatch.setattr(engine, "_record", broken)
    for number in range(1, engine.ADVANCE_FAILURES):
        asyncio.run(engine.tick(factory, None, minute(number), settings))
    assert eh.run_of(factory, run_id).advance_failures == engine.ADVANCE_FAILURES - 1
    monkeypatch.setattr(engine, "_record", record)
    assert asyncio.run(engine.tick(factory, None, now, settings)) == 1
    run = eh.run_of(factory, run_id)
    assert (run.status, run.advance_failures) == (RunStatus.running, 0)
    assert run.advance_failed_at is None
    assert eh.statuses(factory, run_id) == ["done", "running"]


def test_ticks_that_pile_up_after_a_stall_count_as_one_failure(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N2 (a) of the second review of PR #148: the exit counted five ticks, and ticks that had
    waited behind a long job ran back to back, so five failures at one instant closed a run.
    Failures closer than `ADVANCE_FAILURE_GAP` count once."""
    run_id = started(factory, alice, settings, count=2)
    eh.end_agent(factory, eh.running(factory, run_id).agent_run_id, tokens=1_000)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError

    monkeypatch.setattr(engine, "_record", broken)
    # One fixed instant for the whole test: with the real clock, the gap was measured from the
    # first failure while the later ticks read a later "now", so a slow machine pushed the
    # "just under the gap" tick over it (seen in the full local check of 2026-10-13).
    first = now()
    for _ in range(2 * engine.ADVANCE_FAILURES):
        asyncio.run(engine.tick(factory, None, lambda: first, settings))
    run = eh.run_of(factory, run_id)
    assert (run.status, run.advance_failures) == (RunStatus.running, 1)
    just_under = engine.ADVANCE_FAILURE_GAP - timedelta(seconds=1)
    asyncio.run(engine.tick(factory, None, lambda: first + just_under, settings))
    assert eh.run_of(factory, run_id).advance_failures == 1
    asyncio.run(engine.tick(factory, None, lambda: first + engine.ADVANCE_FAILURE_GAP, settings))
    run = eh.run_of(factory, run_id)
    assert (run.status, run.advance_failures) == (RunStatus.running, 2)
    # The cron ticks every minute: real ticks are always further apart than the gap.
    assert timedelta(seconds=30) <= engine.ADVANCE_FAILURE_GAP < timedelta(seconds=45)


def test_a_run_closed_as_stuck_records_the_tokens_of_a_step_that_ends_afterwards(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N2 (b) of the second review of PR #148: the run was closed while its step's agent run was
    at work, the agent run ended on 70,000 tokens and the run recorded none of them."""
    run_id = started(factory, alice, settings, count=2)
    agent_id = eh.running(factory, run_id).agent_run_id
    eh.set_agent(factory, agent_id, status=AgentStatus.running, started_at=now())

    def broken(*args: object, **kwargs: object) -> engine.Moved:
        raise RuntimeError

    with monkeypatch.context() as patched:
        patched.setattr(engine, "advance", broken)
        for number in range(1, engine.ADVANCE_FAILURES + 1):
            asyncio.run(engine.tick(factory, None, minute(number), settings))
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.closed_reason) == (RunStatus.failed, engine.STUCK)
    assert closed.tokens_spent == 0
    # Asked to stop and still at work: its step stays `running` until its end is known.
    assert eh.statuses(factory, run_id) == ["running", "cancelled"]
    with factory() as db:
        assert db.get_one(AgentRun, agent_id).cancel_requested_at is not None
        assert engine.active_run_id(db, alice) is None  # and a new run can start meanwhile
    finished = closed.finished_at

    eh.end_agent(factory, agent_id, AgentStatus.cancelled, tokens=70_000)
    assert asyncio.run(engine.tick(factory, None, minute(9), settings)) == 1  # the cron finds it
    late = eh.run_of(factory, run_id)
    assert (late.status, late.tokens_spent) == (RunStatus.failed, 70_000)
    assert (late.finished_at, late.closed_reason) == (finished, engine.STUCK)
    assert eh.statuses(factory, run_id) == ["cancelled", "cancelled"]
    # Once, whoever calls again: the hook and the cron both may.
    eh.advance(factory, run_id, settings)
    assert asyncio.run(engine.tick(factory, None, minute(10), settings)) == 0
    assert eh.run_of(factory, run_id).tokens_spent == 70_000


def test_the_merge_path_imports_without_the_sdk() -> None:
    """N3 of the review of PR #148: the merge runs inside `advance`, and `advance` runs in the
    api, whose image holds no Claude Agent SDK. What the merge reads its items with
    (`stored_items`) imports with the SDK absent and loads no module that builds a tool."""
    code = (
        "import sys\n"
        "sys.modules['claude_agent_sdk'] = None  # as in the api image: the import fails\n"
        "from app.ingestion import engine, merge, stored_items\n"
        "assert engine.advance and merge.run\n"
        "assert stored_items.as_item and stored_items.as_citations\n"
        "assert stored_items.of_stored_text() is not None\n"
        "loaded = [name for name in ('app.ingestion.tools', 'app.agents.tools', "
        "'app.agents.runner', 'claude_agent_sdk') if sys.modules.get(name) is not None]\n"
        "assert loaded == [], loaded\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert done.returncode == 0, done.stderr


def test_the_events_of_a_step_whose_document_was_deleted_are_purged_again(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = started(factory, alice, settings, count=2)
    step = eh.running(factory, run_id)
    with factory() as db, db.begin():  # what the delete of G15 leaves behind
        db.execute(
            sa.text("UPDATE ingestion_run_steps SET purge_events = true WHERE id = :id"),
            {"id": step.id},
        )
    assert eh.events_of(factory, step.agent_run_id) > 0
    eh.finish_step(factory, run_id, settings, AgentStatus.cancelled)
    assert eh.events_of(factory, step.agent_run_id) == 0
    # The chain goes on: the step failed for its document, the run is not cancelled.
    assert eh.statuses(factory, run_id) == ["failed", "running"]
    with factory() as db, db.begin():  # a write of the runner that landed late
        assert step.agent_run_id is not None
        agent_runs.append_event(db, step.agent_run_id, 99, "note", {}, now())

    async def enqueue(agent_run_id: uuid.UUID) -> None:
        return None

    asyncio.run(engine.tick(factory, enqueue, now, settings))  # one more tick
    assert eh.events_of(factory, step.agent_run_id) == 0


# --- the fake backend -------------------------------------------------------------------------


def test_the_fake_stream_name_is_never_computed_in_production(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 1))
    (step,) = eh.steps_of(factory, run_id)
    fake = settings.model_copy(update={"agents_fake_backend": True})
    assert engine.stream_name(fake, step) == "ingest_extract_default"
    assert engine.stream_name(settings, step) is None  # the fake backend is off
    production = settings.model_copy(update={"agents_fake_backend": True, "app_env": "production"})
    assert engine.stream_name(production, step) is None
    eh.advance(factory, run_id, production)
    (agent,) = eh.agent_runs_of(factory, alice)
    assert "script" not in agent.input


def test_a_stream_named_after_a_documents_hash_is_replayed_when_its_file_exists(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    settings: Settings,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 1))
    (step,) = eh.steps_of(factory, run_id)
    name = f"ingest_extract_{bytes(step.sha256).hex()[:12]}_p0"
    (tmp_path / f"{name}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(engine, "STREAMS", tmp_path)
    fake = settings.model_copy(update={"agents_fake_backend": True})
    assert engine.stream_name(fake, step) == name
    assert agents.ExtractInput.model_fields["script"].metadata  # the name fits its pattern
    agents.EXTRACT.validate_input(
        {
            "ingestion_run_id": str(run_id),
            "step_id": str(step.id),
            "token_budget": 1,
            "script": name,
        }
    )


# --- the real runner, through the hook --------------------------------------------------------


def test_a_run_of_two_documents_through_the_runner_and_the_hook(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings, keyring: Keyring
) -> None:
    fake = settings.model_copy(update={"agents_fake_backend": True})
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 2))
    queue: list[uuid.UUID] = []

    async def enqueue(agent_run_id: uuid.UUID) -> None:
        queue.append(agent_run_id)

    asyncio.run(engine.drive(factory, enqueue, run_id, now, fake))
    context = run_context(
        fake, factory, keyring, agents.EXTRACT, services={engine.ENQUEUE_SERVICE: enqueue}
    )
    ran = 0
    while queue:  # each end calls the hook, which starts the next step
        assert execute(context, queue.pop(0)) is AgentStatus.done
        ran += 1
    run = eh.run_of(factory, run_id)
    assert ran == 2
    assert (run.status, run.partial) == (RunStatus.done, False)
    assert eh.statuses(factory, run_id) == ["done", "done"]
    assert run.tokens_spent > 0
    assert run.tokens_spent == sum(
        (one.input_tokens or 0) + (one.output_tokens or 0)
        for one in eh.agent_runs_of(factory, alice)
    )
    assert all(
        one.input["script"] == "ingest_extract_default" for one in eh.agent_runs_of(factory, alice)
    )


def test_unknown_agent_from_a_worker_that_does_not_know_the_agent(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """An older worker during a deploy fails the step `UNKNOWN_AGENT` and calls no hook: the
    cron finds it, and the chain pauses instead of failing every remaining document."""
    run_id = started(factory, alice, settings, count=2)
    eh.end_agent(
        factory,
        eh.running(factory, run_id).agent_run_id,
        AgentStatus.failed,
        failures.UNKNOWN_AGENT,
    )

    async def enqueue(agent_run_id: uuid.UUID) -> None:
        return None

    asyncio.run(engine.tick(factory, enqueue, now, settings))
    run = eh.run_of(factory, run_id)
    assert (run.status, run.paused_reason) == (RunStatus.paused, failures.UNKNOWN_AGENT)
    assert eh.statuses(factory, run_id) == ["planned", "planned"]
    assert len(eh.agent_runs_of(factory, alice)) == 1


# --- the hook that did not finish, on the runner's own paths ----------------------------------


def test_arq_ends_the_job_inside_the_hook_and_the_cron_offers_the_step_it_left_queued(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings, keyring: Keyring
) -> None:
    """arq ends a job at its timeout by cancelling its task. Here that lands while the hook waits
    on the queue: the end of the first step and the hook's `advance` are committed, the agent
    run of the second step exists and was never offered. The cron's next tick offers that very
    agent run, and no step runs twice."""
    from app.agents import runner

    fake = settings.model_copy(update={"agents_fake_backend": True})
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 2))
    queue: list[uuid.UUID] = []

    async def enqueue(agent_run_id: uuid.UUID) -> None:
        queue.append(agent_run_id)

    asyncio.run(engine.drive(factory, enqueue, run_id, now, fake))
    first = queue.pop(0)

    async def job() -> None:
        reached = asyncio.Event()

        async def never(agent_run_id: uuid.UUID) -> None:
            reached.set()
            await asyncio.Event().wait()

        context = run_context(
            fake, factory, keyring, agents.EXTRACT, services={engine.ENQUEUE_SERVICE: never}
        )
        task = asyncio.create_task(runner.execute(context, first))
        await asyncio.wait_for(reached.wait(), timeout=30)
        task.cancel()  # arq, at the job timeout
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(job())
    assert eh.statuses(factory, run_id) == ["done", "running"]
    second = eh.running(factory, run_id).agent_run_id
    assert second != first
    assert queue == []

    asyncio.run(engine.tick(factory, enqueue, now, fake))
    assert queue == [second]
    assert len(eh.agent_runs_of(factory, alice)) == 2  # the one the hook created, not another
    context = run_context(
        fake, factory, keyring, agents.EXTRACT, services={engine.ENQUEUE_SERVICE: enqueue}
    )
    assert execute(context, queue.pop(0)) is AgentStatus.done
    run = eh.run_of(factory, run_id)
    assert (run.status, run.partial) == (RunStatus.done, False)
    assert eh.statuses(factory, run_id) == ["done", "done"]
    assert queue == []
    ran = eh.agent_runs_of(factory, alice)
    assert sorted(str(step.agent_run_id) for step in eh.steps_of(factory, run_id)) == sorted(
        str(one.id) for one in ran
    )
    assert run.tokens_spent == sum(
        (one.input_tokens or 0) + (one.output_tokens or 0) for one in ran
    )


def test_the_hook_cut_at_its_time_limit_leaves_a_thread_that_ends_after_the_cron(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    settings: Settings,
    keyring: Keyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner gives the hook `final_hook_timeout_s` (30 seconds in the worker, a fraction of
    one here) and that limit does not stop the work the hook handed to a thread. The thread is
    held before its `advance`; the cron advances the run meanwhile; then the thread goes on,
    finds the step counted and the next one running, and changes nothing."""
    from app.agents import runner

    fake = settings.model_copy(update={"agents_fake_backend": True})
    run_id = eh.start(factory, alice, eh.documents(factory, alice, 2))
    queue: list[uuid.UUID] = []

    async def enqueue(agent_run_id: uuid.UUID) -> None:
        queue.append(agent_run_id)

    asyncio.run(engine.drive(factory, enqueue, run_id, now, fake))
    first = queue.pop(0)
    real = engine._advance
    held, release = threading.Event(), threading.Event()
    calls: list[int] = []

    def advance(*args: Any) -> Any:
        calls.append(len(calls))
        if len(calls) == 1:  # the hook's, in its thread
            held.set()
            assert release.wait(30)
        return real(*args)

    monkeypatch.setattr(engine, "_advance", advance)
    services = {engine.ENQUEUE_SERVICE: enqueue}
    cut = run_context(
        fake, factory, keyring, agents.EXTRACT, services=services, final_hook_timeout_s=0.2
    )

    async def scenario() -> None:
        assert await runner.execute(cut, first) is AgentStatus.done  # the hook was given up
        assert held.is_set()
        assert not release.is_set()
        assert queue == []
        await engine.tick(factory, enqueue, now, fake)  # the cron, before the thread
        release.set()

    asyncio.run(scenario())  # returns once the hook's thread has ended
    assert len(calls) == 2
    assert eh.statuses(factory, run_id) == ["done", "running"]
    second = eh.running(factory, run_id).agent_run_id
    assert queue == [second]
    (one, other) = eh.agent_runs_of(factory, alice)
    assert {one.id, other.id} == {first, second}
    assert eh.run_of(factory, run_id).tokens_spent == sum(
        (row.input_tokens or 0) + (row.output_tokens or 0) for row in (one, other)
    )  # the first step counted once

    context = run_context(fake, factory, keyring, agents.EXTRACT, services=services)
    assert execute(context, queue.pop(0)) is AgentStatus.done
    run = eh.run_of(factory, run_id)
    assert (run.status, run.partial) == (RunStatus.done, False)
    assert eh.statuses(factory, run_id) == ["done", "done"]
    assert len(eh.agent_runs_of(factory, alice)) == 2


# --- the overshoot, measured and read through the route ---------------------------------------


def test_the_overshoot_of_a_step_cut_at_the_ceiling_is_within_what_the_run_announces(
    factory: sessionmaker[Session], settings: Settings, keyring: Keyring, api: Api, carol: str
) -> None:
    """A step on what remains under the ceiling, on the real runner, with a backend that moves
    at its own pace as Claude Code does (tests/agents/push_backend.py) and calls a tool the gate
    never hears of: the worst case G6 measured. The requests that left after the one that
    reached the budget are counted and compared with what `GET /api/ingestion/runs/{id}` says
    a run can pass its ceiling by; the tokens it reports are those of the answered requests.
    The request after the reaching one lasts 5 s (`lasting`) until the run cuts it, as a real
    request lasts seconds: with a 5 ms timer it was a race against the interrupt on a loaded
    machine and could be answered."""
    user = user_id(factory, CAROL)
    save_credential(factory, keyring, user)
    run_id = eh.start(factory, user, eh.documents(factory, user, 2))
    ceiling = eh.run_of(factory, run_id).token_ceiling // 4  # the high estimate of one step
    eh.set_run(factory, run_id, token_ceiling=ceiling)
    queue: list[uuid.UUID] = []

    async def enqueue(agent_run_id: uuid.UUID) -> None:
        queue.append(agent_run_id)

    asyncio.run(engine.drive(factory, enqueue, run_id, now, settings))
    (first,) = queue
    assert eh.running(factory, run_id).token_budget == ceiling  # the remainder, not the backstop
    reaching = -(-ceiling // COUNTED)  # the request whose count reaches the budget
    backend = PushBackend(
        looping("Bash", reaching + 6), request_s=0.005, lasting={reaching + 1: 5.0}
    )
    context = run_context(
        settings,
        factory,
        keyring,
        agents.EXTRACT,
        backends=backend,
        services={engine.ENQUEUE_SERVICE: enqueue},
    )
    assert execute(context, first) is AgentStatus.failed
    assert backend.answered == reaching
    measured = len(backend.sent) - reaching  # requests that left after the reaching one

    response = api.get(f"/api/ingestion/runs/{run_id}", token=carol)
    assert response.status_code == 200, response.text
    body = response.json()
    tokens = body["tokens"]
    assert (body["status"], body["paused_reason"]) == ("paused", engine.TOKEN_CEILING)
    assert tokens["ceiling"] == ceiling
    assert tokens["overshoot_requests"] == engine.OVERSHOOT_REQUESTS
    assert 1 == measured <= tokens["overshoot_requests"]
    # As recorded: every answered request in full, nothing for the one that was cut. The run is
    # over its ceiling and says so, it does not clamp the figure.
    assert tokens["spent"] == reaching * (INPUT_SIDE + OUTPUT) > ceiling
    assert queue == [first]  # paused: no further step was created
