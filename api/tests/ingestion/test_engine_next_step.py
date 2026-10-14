"""One function decides the step a Continue pays for (issue #158, the third review of PR #148).

`engine.next_step` names the step that would run next and the budget it needs.
`engine.continue_adds` (the figure the page shows and the click adds) and `engine._next` (the
step that starts) both read it, so the three cannot disagree, whatever happened to the documents
during the pause. The reviewer's constructed states come first, then every state a seeded walk
reaches. Each test fails on the engine as merged with PR #148."""

import asyncio
import random
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import failures
from app.agents.crypto import Keyring
from app.agents.models import AgentRun
from app.agents.models import RunStatus as AgentStatus
from app.config import Settings
from app.ingestion import documents, engine
from app.ingestion.models import IngestionDocument, TextStatus
from app.ingestion.runs_models import IngestionRun, IngestionRunStep, RunStatus, StepStatus
from tests.agents.run_helpers import add_user, now, save_credential
from tests.ingestion import engine_helpers as eh
from tests.ingestion.extract_helpers import Step, add_document, key, keyring, settings
from tests.ingestion.transcribe_helpers import add_scan, document_of, images_of, tools_for
from tests.ingestion.transcribe_helpers import transcribe as view_then_store

_FIXTURES = (key, keyring, settings)


@pytest.fixture
def alice(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)  # an API key
    return user


LONG = "word " * 20_000  # 100,000 characters: its low estimate is above the `H` of two small ones


def started(factory: sessionmaker[Session], user: uuid.UUID, settings: Settings, count: int) -> Any:
    run_id = eh.start(factory, user, eh.documents(factory, user, count))
    assert eh.advance(factory, run_id, settings).enqueue is not None
    return run_id


def replace_text(factory: sessionmaker[Session], document_id: uuid.UUID, text: str) -> None:
    """A text replaced as a parser of a later version will replace it."""
    with factory() as db, db.begin():
        db.execute(
            sa.update(IngestionDocument)
            .where(IngestionDocument.id == document_id)
            .values(text=text)
        )


def delete_by_sql(factory: sessionmaker[Session], document_id: uuid.UUID) -> None:
    """A document deleted as the deletion task will allow, whatever its steps."""
    with factory() as db, db.begin():
        db.execute(sa.delete(IngestionDocument).where(IngestionDocument.id == document_id))


def rows_of(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every step of the run as stored, to see that a read wrote nothing."""
    return [
        (one.id, one.position, one.status, one.text_sha256, one.agent_run_id, one.cut_budget)
        for one in eh.steps_of(factory, run_id)
    ]


def shown(factory: sessionmaker[Session], run_id: uuid.UUID, at: datetime | None = None) -> int:
    """`tokens.continue_adds` as the read route computes it: without the run's lock, and without
    one write. The session holds nothing to flush afterwards and the rows are as they were."""
    before = rows_of(factory, run_id), eh.run_of(factory, run_id).token_ceiling
    with factory() as db:
        run = db.get_one(IngestionRun, run_id)
        steps = list(
            db.scalars(
                sa.select(IngestionRunStep)
                .where(IngestionRunStep.ingestion_run_id == run_id)
                .order_by(IngestionRunStep.position)
            )
        )
        figure = engine.continue_adds(db, run, steps, at or now())
        assert (
            engine.continue_adds(db, run, steps, at or now()) == figure
        )  # asked twice, one answer
        assert (list(db.new), list(db.dirty), list(db.deleted)) == ([], [], [])
        db.commit()  # even committed, the read route's session would have written nothing
    assert (rows_of(factory, run_id), eh.run_of(factory, run_id).token_ceiling) == before
    return figure


def click(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    run_id: uuid.UUID,
    adds: int,
    at: datetime | None = None,
) -> None:
    with factory() as db, db.begin():
        engine.continue_(db, user, run_id, at or now(), adds)


def remaining(factory: sessionmaker[Session], run_id: uuid.UUID) -> int:
    run = eh.run_of(factory, run_id)
    return run.token_ceiling - run.tokens_spent


def test_a_cut_step_whose_document_is_gone_is_not_the_step_a_continue_pays_for(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """The reviewer's probe Q3e: three small documents, the second step cut on 228,660. The page
    showed 457,325 (twice that budget, plus the 5 of overshoot); with the document of the cut
    step deleted, the step that runs is the third, and one `H` is what it needs."""
    run_id = started(factory, alice, settings, count=3)
    run = eh.run_of(factory, run_id)
    high = run.estimate_high
    eh.finish_step(factory, run_id, settings, tokens=run.token_ceiling - 228_660)
    cut = eh.running(factory, run_id)
    assert (cut.position, cut.token_budget) == (1, 228_660)
    eh.finish_step(factory, run_id, settings, AgentStatus.failed, failures.TOKEN_LIMIT, 228_665)
    assert eh.run_of(factory, run_id).paused_reason == engine.TOKEN_CEILING
    assert eh.statuses(factory, run_id) == ["done", "planned", "planned"]
    assert shown(factory, run_id) == 457_325 > high  # the cut step is the one that would run

    delete_by_sql(factory, cut.document_id)
    figure = shown(factory, run_id)
    assert figure == high < 457_325  # sized for the third step, which is the one that runs
    agents_before = len(eh.agent_runs_of(factory, alice))
    ceiling = eh.run_of(factory, run_id).token_ceiling
    click(factory, alice, run_id, figure)
    assert eh.advance(factory, run_id, settings).enqueue is not None
    after = eh.run_of(factory, run_id)
    assert (after.status, after.token_ceiling) == (RunStatus.running, ceiling + figure)
    assert [(one["from"], one["to"]) for one in after.ceiling_history] == [
        (ceiling, ceiling + figure)
    ]
    assert eh.statuses(factory, run_id) == ["done", "failed", "running"]
    assert eh.running(factory, run_id).token_budget == figure - 5
    assert len(eh.agent_runs_of(factory, alice)) == agents_before + 1  # one click, one step


def test_a_text_replaced_during_a_pause_is_paid_for_in_one_click(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """The reviewer's probe Q3c: two small documents, 12,440 remain after the first step and the
    run pauses. The second text becomes 100,000 characters. The page showed one `H`, which left
    the new part under its low estimate: the run paused again at once and a second click
    followed. Sized for the part that runs, one click is enough."""
    selected = eh.documents(factory, alice, 2)
    run_id = eh.start(factory, alice, selected)
    eh.advance(factory, run_id, settings)
    run = eh.run_of(factory, run_id)
    high = run.estimate_high
    eh.finish_step(factory, run_id, settings, tokens=run.token_ceiling - 12_440)
    assert eh.run_of(factory, run_id).paused_reason == engine.TOKEN_CEILING
    assert shown(factory, run_id) == high

    replace_text(factory, selected[1], LONG)
    figure = shown(factory, run_id)
    assert figure > high
    agents_before = len(eh.agent_runs_of(factory, alice))
    click(factory, alice, run_id, figure)
    assert eh.advance(factory, run_id, settings).enqueue is not None  # no second pause
    after = eh.run_of(factory, run_id)
    assert (after.status, after.paused_reason) == (RunStatus.running, None)
    assert len(after.ceiling_history) == 1  # one raise, and it bought a step
    step = eh.running(factory, run_id)
    assert (step.range_start, step.range_end) == (0, len(LONG))
    # Exactly what the new part needs: it starts on its low estimate, with nothing to spare.
    assert step.token_budget == 12_440 + figure == engine._floor(step)
    assert len(eh.agent_runs_of(factory, alice)) == agents_before + 1


def test_the_figure_of_before_a_replacement_is_refused_and_buys_nothing(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """The same state, from a page that still shows one `H`: the click is refused with the
    figure of now, where it used to be accepted and to pause the run again with no token."""
    selected = eh.documents(factory, alice, 2)
    run_id = eh.start(factory, alice, selected)
    eh.advance(factory, run_id, settings)
    run = eh.run_of(factory, run_id)
    eh.finish_step(factory, run_id, settings, tokens=run.token_ceiling - 12_440)
    stale = shown(factory, run_id)
    replace_text(factory, selected[1], LONG)
    with factory() as db, db.begin(), pytest.raises(engine.ContinueChangedError) as refused:
        engine.continue_(db, alice, run_id, now(), stale)
    assert refused.value.adds == shown(factory, run_id) > stale
    assert eh.run_of(factory, run_id).ceiling_history == []


def test_the_last_planned_document_deleted_during_a_pause_closes_the_run_without_a_raise(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """The reviewer's probe Q3b, reachable through the real delete with its guard: the run
    pauses before its last step and the user deletes that document. Nothing would run, so the
    run asks for no raise and closes at its next `advance`, on the ceiling it had."""
    selected = eh.documents(factory, alice, 2)
    run_id = eh.start(factory, alice, selected)
    eh.advance(factory, run_id, settings)
    run = eh.run_of(factory, run_id)
    eh.finish_step(factory, run_id, settings, tokens=run.token_ceiling - 1_000)
    paused = eh.run_of(factory, run_id)
    assert (paused.status, paused.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
    assert shown(factory, run_id) == paused.estimate_high
    with factory() as db, db.begin():
        assert documents.delete_document(db, alice, selected[1])

    assert shown(factory, run_id) == 0  # no raise is asked
    agents_before = len(eh.agent_runs_of(factory, alice))
    assert asyncio.run(engine.tick(factory, None, now, settings)) == 1  # the cron, no click
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.partial) == (RunStatus.done, True)
    assert (closed.token_ceiling, closed.ceiling_history) == (paused.token_ceiling, [])
    assert closed.tokens_spent == paused.tokens_spent
    assert eh.statuses(factory, run_id) == ["done", "failed"]
    assert len(eh.agent_runs_of(factory, alice)) == agents_before


def test_a_continue_with_nothing_left_to_run_adds_nothing_and_the_run_closes(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """The same state, and a click from a page that still shows one `H` before the cron came:
    it used to add that `H` only for the run to close. It adds nothing, and it is refused with
    the figure of now, zero (the route answers 409 `continue_changed` and closes the run)."""
    selected = eh.documents(factory, alice, 2)
    run_id = eh.start(factory, alice, selected)
    eh.advance(factory, run_id, settings)
    run = eh.run_of(factory, run_id)
    eh.finish_step(factory, run_id, settings, tokens=run.token_ceiling - 1_000)
    paused = eh.run_of(factory, run_id)
    stale = shown(factory, run_id)
    delete_by_sql(factory, selected[1])
    for adds in (stale, 0, 1):
        with pytest.raises(engine.ContinueChangedError) as refused:
            click(factory, alice, run_id, adds)  # never answered as accepted: the figure is 0
        assert refused.value.adds == 0
        same = eh.run_of(factory, run_id)
        assert (same.status, same.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
        assert (same.token_ceiling, same.ceiling_history) == (paused.token_ceiling, [])
    eh.advance(factory, run_id, settings)  # what the route does right after the click
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.partial) == (RunStatus.done, True)
    assert (closed.token_ceiling, closed.ceiling_history) == (paused.token_ceiling, [])
    assert closed.tokens_spent == paused.tokens_spent


# --- the deadline (B1 of the review of PR #165) --------------------------------------------------


def test_a_cut_recorded_after_the_deadline_pauses_nothing_and_asks_no_raise(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """The reviewer's probe: two small documents, the first step cut on its budget of the whole
    ceiling, its end recorded 5 seconds after `deadline_at`. It paused the run on the ceiling, the
    page showed 500,550, the click was accepted and raised the ceiling, and the next `advance`
    closed the run on the deadline with nothing started. A run out of time asks for no raise: the
    cut ends the run as the deadline rule says, with the ceiling and its history as they were."""
    run_id = started(factory, alice, settings, count=2)
    run = eh.run_of(factory, run_id)
    step = eh.running(factory, run_id)
    assert step.token_budget == run.token_ceiling  # cut by the ceiling, not by its backstop
    agents_before = len(eh.agent_runs_of(factory, alice))
    late = run.deadline_at + timedelta(seconds=5)
    eh.finish_step(
        factory,
        run_id,
        settings,
        AgentStatus.failed,
        failures.TOKEN_LIMIT,
        run.token_ceiling,
        at=late,
    )
    closed = eh.run_of(factory, run_id)
    assert closed.status not in engine.ACTIVE
    assert (closed.paused_at, closed.paused_reason) == (None, None)  # it never asked for a raise
    assert (closed.token_ceiling, closed.ceiling_history) == (run.token_ceiling, [])
    assert closed.tokens_spent == run.token_ceiling
    assert len(eh.agent_runs_of(factory, alice)) == agents_before
    with pytest.raises(engine.WrongStateError):
        click(factory, alice, run_id, 500_550, late)  # a click meanwhile adds nothing
    assert eh.run_of(factory, run_id).token_ceiling == run.token_ceiling


def test_a_run_paused_on_the_ceiling_at_or_after_its_deadline_shows_no_figure_and_closes(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """A row paused that way before this fix (the state of the probe above): the figure is 0
    whatever the clock, a click is refused with that figure and adds nothing, and the next
    `advance` closes the run on the ceiling it has."""
    run_id = started(factory, alice, settings, count=2)
    run = eh.run_of(factory, run_id)
    eh.finish_step(factory, run_id, settings, tokens=run.token_ceiling - 1_000)
    paused = eh.run_of(factory, run_id)
    assert paused.paused_at is not None
    assert shown(factory, run_id) == paused.estimate_high
    eh.set_run(factory, run_id, deadline_at=paused.paused_at - timedelta(seconds=5))
    for at in (paused.paused_at, paused.paused_at + timedelta(hours=2)):
        assert shown(factory, run_id, at) == 0
        with pytest.raises(engine.ContinueChangedError) as refused:
            click(factory, alice, run_id, paused.estimate_high, at)
        assert refused.value.adds == 0
    agents_before = len(eh.agent_runs_of(factory, alice))
    eh.advance(factory, run_id, settings, at=paused.paused_at + timedelta(minutes=1))
    closed = eh.run_of(factory, run_id)
    assert closed.status not in engine.ACTIVE
    assert (closed.token_ceiling, closed.ceiling_history) == (paused.token_ceiling, [])
    assert len(eh.agent_runs_of(factory, alice)) == agents_before


def test_a_run_paused_before_its_deadline_is_continued_after_the_deadline_passed(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """A pause stops the clock: the Continue moves the deadline by the length of the pause, so a
    run that paused with time left still has that time left when it resumes, hours later. The
    figure is judged as the engine will judge it right after the click, by the same function
    as `_next`, and is shown; the click starts a step and the deadline moved by the pause."""
    run_id = started(factory, alice, settings, count=2)
    run = eh.run_of(factory, run_id)
    eh.finish_step(factory, run_id, settings, tokens=run.token_ceiling - 1_000)
    paused = eh.run_of(factory, run_id)
    assert paused.paused_at is not None
    later = run.deadline_at + timedelta(hours=3)  # the deadline passed while the run was paused
    assert shown(factory, run_id, later) == paused.estimate_high
    click(factory, alice, run_id, paused.estimate_high, later)
    moved = eh.advance(factory, run_id, settings, at=later)
    after = eh.run_of(factory, run_id)
    assert moved.enqueue is not None
    assert after.status is RunStatus.running
    assert after.deadline_at == run.deadline_at + (later - paused.paused_at)
    assert after.token_ceiling == paused.token_ceiling + paused.estimate_high


# --- every state a seeded walk reaches --------------------------------------------------------


def _unread(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[uuid.UUID]:
    """The documents no step has finished reading and that still have a planned step."""
    steps = eh.steps_of(factory, run_id)
    read = {one.document_id for one in steps if one.status in (StepStatus.done, StepStatus.reused)}
    planned = [one.document_id for one in steps if one.status is StepStatus.planned]
    return [one for one in dict.fromkeys(planned) if one not in read]


def _disturb(factory: sessionmaker[Session], run_id: uuid.UUID, rng: random.Random) -> list[str]:
    """What the next tasks let a user do during a pause: delete a document, or see its text
    replaced by a shorter or a longer one (up to several parts)."""
    done: list[str] = []
    for document_id in _unread(factory, run_id):
        action = rng.choice(["keep", "keep", "delete", "shorter", "longer", "longest"])
        if action == "delete":
            delete_by_sql(factory, document_id)
        elif action != "keep":
            words = {"shorter": 40, "longer": 20_000, "longest": 70_000}[action]
            replace_text(factory, document_id, f"{rng.random()} " + "word " * words)
        done.append(action)
    return done


def _end_running_step(
    factory: sessionmaker[Session],
    run_id: uuid.UUID,
    settings: Settings,
    rng: random.Random,
    at: datetime,
) -> None:
    budget = eh.running(factory, run_id).token_budget or 0
    how = rng.choice(["cheap", "to the ceiling", "cut", "cut"])
    if how == "cheap":
        eh.finish_step(factory, run_id, settings, tokens=1_000, at=at)
    elif how == "to the ceiling":
        left = remaining(factory, run_id)
        eh.finish_step(
            factory, run_id, settings, tokens=max(left - rng.randrange(20_000), 1), at=at
        )
    else:
        eh.finish_step(
            factory,
            run_id,
            settings,
            AgentStatus.failed,
            failures.TOKEN_LIMIT,
            budget + rng.randrange(50),
            at,
        )


# How long a step takes and how long a pause lasts in the walk, in minutes. The run has 60: a
# deadline is reached while a step works, and while the run is paused (a pause stops the clock).
_WORK = (0, 1, 5, 20, 45)
_PAUSES = (0, 5, 70, 180)


@pytest.mark.parametrize("seed", range(40))
def test_at_every_pause_one_continue_is_exactly_what_starts_the_next_step(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings, seed: int
) -> None:
    """A property over small random plans: steps end cheap, at the ceiling or cut, and during
    every pause on the ceiling documents are deleted or their texts replaced at random. In every
    such state, the figure shown is zero and the run closes with no raise, or it is the amount
    after which the next step starts without a second pause: one `H`, or to the token what that
    step's floor needs when one `H` is not enough. The clock moves: a step ends minutes later, a
    pause lasts up to hours, so deadlines are reached while a step works (a cut recorded out of
    time pauses nothing) and while the run is paused (a pause stops the clock, and the run
    continues). Whatever the time, a figure shown is a figure that starts a step."""
    rng = random.Random(seed)  # noqa: S311 (test data, not a secret)
    texts = [
        eh.text_of(number, rng.choice([200, 600, 4_000])) for number in range(rng.randint(2, 4))
    ]
    clock = now()  # moves: steps take minutes, pauses last up to hours
    run_id = eh.start(
        factory, alice, [add_document(factory, alice, text) for text in texts], at=clock
    )
    eh.advance(factory, run_id, settings, at=clock)
    pauses = 0
    for _ in range(80):
        run = eh.run_of(factory, run_id)
        if run.status not in engine.ACTIVE:
            break
        if run.status is RunStatus.running:
            clock += timedelta(minutes=rng.choice(_WORK))
            _end_running_step(factory, run_id, settings, rng, clock)
            if clock >= run.deadline_at:  # the step ended out of time: no raise is asked
                assert eh.run_of(factory, run_id).status not in engine.ACTIVE, (seed, clock)
            continue
        assert run.paused_reason == engine.TOKEN_CEILING
        assert run.paused_at is not None, seed
        assert run.paused_at < run.deadline_at, seed
        pauses += 1
        clock += timedelta(minutes=rng.choice(_PAUSES))  # the deadline may pass during the pause
        state = (seed, pauses, clock, _disturb(factory, run_id, rng))
        left = remaining(factory, run_id)
        agents_before = len(eh.agent_runs_of(factory, alice))
        figure = shown(factory, run_id, clock)
        if figure == 0:
            eh.advance(factory, run_id, settings, at=clock)
            closed = eh.run_of(factory, run_id)
            assert closed.status not in engine.ACTIVE, state
            assert closed.token_ceiling == run.token_ceiling, state
            assert closed.ceiling_history == run.ceiling_history, state
            assert len(eh.agent_runs_of(factory, alice)) == agents_before, state
            break
        click(factory, alice, run_id, figure, clock)
        moved = eh.advance(factory, run_id, settings, at=clock)
        after = eh.run_of(factory, run_id)
        assert moved.enqueue is not None, state
        assert after.status is RunStatus.running, state
        assert after.token_ceiling == run.token_ceiling + figure, state
        assert len(after.ceiling_history) == len(run.ceiling_history) + 1, state
        assert len(eh.agent_runs_of(factory, alice)) == agents_before + 1, state
        step = eh.running(factory, run_id)
        floor = engine._floor(step)
        assert figure == max(run.estimate_high, floor - left), state
        assert (step.token_budget or 0) >= floor, state
    else:
        pytest.fail(f"seed {seed}: the run never ended")
    assert eh.run_of(factory, run_id).status not in engine.ACTIVE


# --- the close of a stuck run -----------------------------------------------------------------


def test_a_stuck_run_closes_even_when_recording_its_running_step_fails(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Note 3 of the third review of PR #148: at the fifth failure `_record_late` ran bare, so a
    read that failed there took the close down with it, and with a failed statement the whole
    transaction. In a savepoint the run closes; the step's tokens are recorded by the next
    `advance` that can read them."""
    run_id = started(factory, alice, settings, count=2)
    eh.end_agent(factory, eh.running(factory, run_id).agent_run_id, tokens=1_000)

    def broken(*args: object, **kwargs: object) -> engine.Moved:
        raise RuntimeError

    def failing_read(db: Session, agent_run_id: uuid.UUID | None) -> None:
        db.execute(sa.text("select 1 / 0"))  # the statement fails: Postgres aborts what holds it

    with monkeypatch.context() as patched:
        patched.setattr(engine, "advance", broken)
        patched.setattr(engine, "_agent", failing_read)
        for number in range(1, engine.ADVANCE_FAILURES + 1):
            clock = lambda number=number: now() + timedelta(minutes=number)  # noqa: E731
            asyncio.run(engine.tick(factory, None, clock, settings))
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.closed_reason) == (RunStatus.failed, engine.STUCK)
    assert (closed.advance_failures, closed.tokens_spent) == (engine.ADVANCE_FAILURES, 0)
    assert eh.statuses(factory, run_id) == ["running", "cancelled"]
    with factory() as db:
        assert engine.active_run_id(db, alice) is None  # a new run can start
        assert db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) == 1

    assert asyncio.run(engine.tick(factory, None, now, settings)) == 1  # reads work again
    late = eh.run_of(factory, run_id)
    assert (late.status, late.tokens_spent) == (RunStatus.failed, 1_000)
    assert eh.statuses(factory, run_id) == ["cancelled", "cancelled"]


# --- a transcription that did not finish, and one with nothing left (G14) -----------------------

ITS_OWN = "The agent ended without a result."  # neither the provider's failure nor the box's


@pytest.mark.parametrize(
    "pages",
    [[None, None], ["A page read as text, of a made-up letter.", None, "Its third page."]],
    ids=["a pure scan", "a mixed PDF"],
)
def test_a_document_whose_transcription_failed_is_not_extracted_in_that_run(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    settings: Settings,
    pages: list[str | None],
) -> None:
    """Its text is not whole (an empty slot per page that waits), so no model reads it: the
    predicted extraction fails without an agent run, and the other document is read."""
    scan = add_scan(factory, alice, pages)
    other = add_document(factory, alice, eh.text_of(1))
    run_id = eh.start(factory, alice, [scan, other])
    first = eh.advance(factory, run_id, settings)
    assert eh.running(factory, run_id).kind == "transcribe"
    eh.end_agent(factory, first.enqueue, AgentStatus.failed, ITS_OWN, tokens=700)
    before = rows_of(factory, run_id)
    figure = shown(factory, run_id)
    assert rows_of(factory, run_id) == before  # the prediction wrote nothing
    second = eh.advance(factory, run_id, settings)
    # The step that starts is the other document's: nothing ran for the scan's extraction.
    reading = eh.running(factory, run_id)
    assert (reading.kind, reading.document_id, reading.agent_run_id) == (
        "extract",
        other,
        second.enqueue,
    )
    of_the_scan = [one for one in eh.steps_of(factory, run_id) if one.document_id == scan]
    assert [(one.kind, one.status) for one in of_the_scan] == [
        ("transcribe", StepStatus.failed),
        ("extract", StepStatus.failed),
    ]
    assert of_the_scan[1].agent_run_id is None
    assert [one.agent for one in eh.agent_runs_of(factory, alice)] == [
        engine.TRANSCRIBE_NAME,
        engine.agents.EXTRACT_NAME,
    ]
    assert eh.run_of(factory, run_id).tokens_spent == 700  # nothing charged for an extraction
    assert figure >= 0
    eh.end_agent(factory, second.enqueue, tokens=300)
    assert eh.advance(factory, run_id, settings).enqueue is None
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.partial, closed.tokens_spent) == (RunStatus.done, True, 1000)
    assert len(eh.agent_runs_of(factory, alice)) == 2
    # The pages still wait, for a later run.
    assert document_of(factory, scan).text_status is TextStatus.awaiting_transcription
    assert None in images_of(factory, scan).values()


def test_a_pure_scan_alone_whose_transcription_failed_ends_the_run_with_one_agent_run(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    scan = add_scan(factory, alice, [None, None])
    run_id = eh.start(factory, alice, [scan])
    first = eh.advance(factory, run_id, settings)
    eh.end_agent(factory, first.enqueue, AgentStatus.failed, ITS_OWN, tokens=700)
    assert shown(factory, run_id) == 0  # nothing would run: no raise is asked
    assert eh.advance(factory, run_id, settings).enqueue is None
    assert eh.statuses(factory, run_id) == ["failed", "failed"]
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.tokens_spent) == (RunStatus.failed, 700)
    assert [one.agent for one in eh.agent_runs_of(factory, alice)] == [engine.TRANSCRIBE_NAME]


def test_a_transcription_step_with_nothing_left_is_done_without_an_agent_run(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """A first attempt stored every page, then the worker stopped under it. The step is planned
    again and has nothing to do: it is done, no second agent run is made for it, and the text
    is read. The run counts no failed step."""
    scan = add_scan(factory, alice, [None])
    run_id = eh.start(factory, alice, [scan])
    first = eh.advance(factory, run_id, settings)
    step = eh.running(factory, run_id)
    attempt = Step(alice, scan, run_id, step.id, first.enqueue)  # type: ignore[arg-type]
    view_then_store(tools_for(factory, attempt), 0, "The whole page of a made-up letter.")
    eh.end_agent(factory, first.enqueue, AgentStatus.failed, engine.agent_runs.STOPPED, tokens=500)
    second = eh.advance(factory, run_id, settings)
    kinds = [(one.kind, one.status) for one in eh.steps_of(factory, run_id)]
    assert kinds == [("transcribe", StepStatus.done), ("extract", StepStatus.running)]
    reading = eh.running(factory, run_id)
    assert reading.agent_run_id == second.enqueue
    assert (reading.range_start, reading.range_end) == (
        0,
        len("The whole page of a made-up letter."),
    )
    assert [one.agent for one in eh.agent_runs_of(factory, alice)] == [
        engine.TRANSCRIBE_NAME,
        engine.agents.EXTRACT_NAME,
    ]
    assert eh.run_of(factory, run_id).tokens_spent == 500  # the first attempt, counted once
    eh.end_agent(factory, second.enqueue, tokens=300)
    eh.advance(factory, run_id, settings)
    closed = eh.run_of(factory, run_id)
    assert (closed.status, closed.partial, closed.tokens_spent) == (RunStatus.done, False, 800)
    assert "failed" not in eh.statuses(factory, run_id)


def test_a_group_with_nothing_left_is_done_and_the_next_group_is_what_a_continue_pays_for(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """Nine pages are two groups. The first stored its eight pages before the worker stopped:
    it is done, and the step a run paused on its ceiling asks a raise for is the second group,
    in the figure shown and in what starts."""
    scan = add_scan(factory, alice, [None] * 9)
    run_id = eh.start(factory, alice, [scan])
    first = eh.advance(factory, run_id, settings)
    step = eh.running(factory, run_id)
    attempt = Step(alice, scan, run_id, step.id, first.enqueue)  # type: ignore[arg-type]
    tools = tools_for(factory, attempt)
    for position in range(8):
        view_then_store(tools, position, f"Page {position + 1} of a made-up scan.")
    eh.end_agent(factory, first.enqueue, AgentStatus.failed, engine.agent_runs.STOPPED, tokens=500)
    eh.set_run(factory, run_id, token_ceiling=501)
    assert eh.advance(factory, run_id, settings).enqueue is None
    paused = eh.run_of(factory, run_id)
    assert (paused.status, paused.paused_reason) == (RunStatus.paused, engine.TOKEN_CEILING)
    with factory() as db:
        steps = [db.merge(one) for one in eh.steps_of(factory, run_id)]
        chosen = engine.next_step(db, eh.run_of(factory, run_id), steps, now())
    assert chosen.step is not None
    assert (chosen.step.kind, list(chosen.step.image_positions or ())) == ("transcribe", [8])
    # The pause wrote what `next_step` listed: the first group is done, the rest still waits.
    assert eh.statuses(factory, run_id) == ["done", "planned", "planned"]
    assert shown(factory, run_id) >= chosen.needs - 1
    assert len(eh.agent_runs_of(factory, alice)) == 1  # no agent run for the group that was done
