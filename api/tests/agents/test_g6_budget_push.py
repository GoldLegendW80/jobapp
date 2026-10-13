"""The token budget against a backend that behaves like the real CLI where the fake does not
(review of PR #95; tests/agents/push_backend.py): its requests leave at their own pace, whether
or not the runner has written its events, and the gate is only asked about the tools the run
offers. With such a backend the first version of the budget let a run go on for every request
of its loop: the count sat behind the event writes, and a call to Bash or to an invented tool
never reached the gate. The control at the end runs that first loop again and shows this
backend sees it.

A request is counted 901 by the runner while the run goes on (the input side, and 1 of output);
a budget of 2,500 is reached by the third request of a loop of nine."""

import logging
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runner
from app.agents.crypto import Keyring
from app.agents.models import RunStatus
from app.config import Settings
from tests.agents.g6_helpers import (
    ALL_TOOLS,
    MARKER,
    WRITTEN,
    agent,
    everything_stored,
    g6_tools,
    run_context,
)
from tests.agents.push_backend import COUNTED, INPUT_SIDE, OUTPUT, PushBackend, Turn, looping
from tests.agents.run_helpers import add_user, events, execute, new_run, run_row, save_credential
from tests.conftest import make_settings

__all__ = ["g6_tools"]

OWN = "mcp__app__g6_write"
BUILT_IN = "Bash"
INVENTED = "mcp__app__made_up"
TURNS = 8  # eight requests that call a tool, a ninth that answers
BUDGET = 2500
# How long the request that leaves right after the one that reached the budget stays in flight.
# A real request lasts seconds, the runner's interrupt lands in milliseconds: the request is cut.
# With a timer of a few milliseconds this was a race against the machine (see the first test).
IN_FLIGHT_S = 5.0
REACHING = 3
assert (REACHING - 1) * COUNTED < BUDGET <= REACHING * COUNTED

STEP = agent("g6_push", tools=ALL_TOOLS)


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


def slow_writes(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    """Every event write of the runner takes `seconds` longer, as on a database that waits for
    a lock or a disk."""
    if not seconds:
        return
    write = runner.Recorder._write

    def slow(self: runner.Recorder, seq: int, kind: str, data: dict[str, Any]) -> None:
        time.sleep(seconds)
        write(self, seq, kind, data)

    monkeypatch.setattr(runner.Recorder, "_write", slow)


# name of the case: the tool the model calls, seconds per request, seconds added per event write
CASES = {
    "the agent's own tool, fast requests": (OWN, 0.005, 0.0),
    "the agent's own tool, slow event writes": (OWN, 0.03, 0.12),
    "a built in tool name, fast requests": (BUILT_IN, 0.005, 0.0),
    "a built in tool name, slow event writes": (BUILT_IN, 0.03, 0.12),
    "an invented tool name, fast requests": (INVENTED, 0.005, 0.0),
    "an invented tool name, slow event writes": (INVENTED, 0.03, 0.12),
}


@pytest.mark.parametrize("case", list(CASES))
def test_the_run_is_stopped_at_the_request_that_reached_its_budget_or_the_next(
    case: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    g6_tools: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever the model calls and however slow the event writes are. A tool of the agent: the
    gate is asked and stops the turn, no request follows the one that reached the budget. Any
    other name: the backend answers the call itself, as the CLI does, and its next request has
    left when the interrupt lands, which cuts it; never a third.

    What the runner promises is that it interrupts as soon as it has counted the reaching
    message, without waiting on anything (module docstring of app/agents/runner.py): at most
    the one request that had already left is out when it does. Whether that request is cut or
    answered is a race between the interrupt and the request's own duration, which nothing
    orders. The real CLI's request lasts seconds, so it is cut; with a timer of 5 ms, as the
    backend first did, a loaded machine made the timer run out before the interrupt's few
    event loop turns (the count, the wake of the watcher, the interrupt call) and a fourth
    request was answered (`assert 4 == 3`, full check of 2026-10-13), the runner having done
    all it promises. So the request that leaves after the reaching one stays in flight until
    cut (`lasting`), and what is asserted no longer depends on the machine's speed: it is cut,
    never answered, and none leaves after it. The requests before it stay fast, the budget is
    reached by the third."""
    tool, request_s, write_s = CASES[case]
    slow_writes(monkeypatch, write_s)
    backend = PushBackend(
        looping(tool, TURNS), request_s=request_s, lasting={REACHING + 1: IN_FLIGHT_S}
    )
    run_id = new_run(factory, alice_id, {"token_budget": BUDGET}, agent="g6_push")
    rc = run_context(settings, factory, keyring, STEP, backends=backend)
    assert execute(rc, run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == runner.TOKEN_LIMIT
    assert backend.answered == REACHING  # no request after the reaching one was ever answered
    if tool == OWN:
        assert len(backend.sent) == REACHING  # and none even left
        assert backend.gate_asked == [OWN] * REACHING
        assert backend.tools_run == REACHING - 1  # the call of the reaching request did not run
        assert len(WRITTEN) == REACHING - 1
    else:
        assert len(backend.sent) == REACHING + 1  # one more left, and was cut
        assert backend.gate_asked == []  # the hook never heard of these calls
        assert [kind for kind, _ in events(factory, run_id) if kind == "tool_denied"] == []
        assert backend.interrupts >= 1
    # What was used is what is recorded: the result message's figures, the output in full.
    assert (row.input_tokens, row.output_tokens) == (REACHING * INPUT_SIDE, REACHING * OUTPUT)


def test_a_gate_that_waits_less_than_an_event_write_takes(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    g6_tools: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate's wait for the message of its call is 50 ms here and an event write 120 ms. When
    the count sat behind the writes, such a gate decided on a count that was several requests
    old and let the call through. The count no longer waits on the database: the gate finds
    its message counted whatever the writes do, and stops the reaching call."""
    slow_writes(monkeypatch, 0.12)
    backend = PushBackend(looping(OWN, TURNS), request_s=0.03)
    run_id = new_run(factory, alice_id, {"token_budget": BUDGET}, agent="g6_push")
    rc = run_context(settings, factory, keyring, STEP, backends=backend, gate_wait_s=0.05)
    assert execute(rc, run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == runner.TOKEN_LIMIT  # the budget, not a gate that gave up
    assert (len(backend.sent), backend.tools_run) == (REACHING, REACHING - 1)
    assert (row.input_tokens, row.output_tokens) == (REACHING * INPUT_SIDE, REACHING * OUTPUT)


def test_a_run_cut_while_its_events_are_still_being_written_records_what_it_used(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    g6_tools: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner gives the event writes a grace, then cuts them. The count and the result
    message were taken when they came off the stream, so the figures on the run are whole even
    though its log is not."""
    slow_writes(monkeypatch, 0.3)
    backend = PushBackend(
        looping(BUILT_IN, TURNS), request_s=0.01, lasting={REACHING + 1: IN_FLIGHT_S}
    )
    run_id = new_run(factory, alice_id, {"token_budget": BUDGET}, agent="g6_push")
    rc = run_context(settings, factory, keyring, STEP, backends=backend, interrupt_grace_s=0.2)
    assert execute(rc, run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == runner.TOKEN_LIMIT
    assert len(backend.sent) == REACHING + 1
    assert (row.input_tokens, row.output_tokens) == (REACHING * INPUT_SIDE, REACHING * OUTPUT)
    kinds = [kind for kind, _ in events(factory, run_id)]
    assert "usage" not in kinds  # the log was cut before the result's own event
    assert kinds[-1] == "status"


def test_a_budget_reached_by_the_request_that_ends_the_run(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    g6_tools: None,
) -> None:
    backend = PushBackend(looping(OWN, 2))  # three requests, the third only answers
    run_id = new_run(factory, alice_id, {"token_budget": BUDGET}, agent="g6_push")
    rc = run_context(settings, factory, keyring, STEP, backends=backend)
    assert execute(rc, run_id) is RunStatus.done
    assert (len(backend.sent), backend.interrupts) == (3, 0)  # nothing to stop
    row = run_row(factory, run_id)
    assert (row.input_tokens, row.output_tokens) == (3 * INPUT_SIDE, 3 * OUTPUT)


def test_a_turn_that_goes_on_without_any_tool_call(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    g6_tools: None,
) -> None:
    """An answer the API cut or paused, after which Claude Code would send a new request by
    itself. Nothing in the reaching message says the turn will go on: only the next request
    does, when its first message arrives. So that request is answered, and the one after it,
    which this backend sends at once, is cut by the interrupt: the worst this path can do. The
    fifth request stays in flight until cut, as in the first test of this file: its being cut
    is the real CLI's behaviour (a request lasts seconds), not a race with a 5 ms timer."""
    parts = [Turn(f"Part {n}.", goes_on=True) for n in range(1, TURNS + 1)]
    backend = PushBackend([*parts, Turn("The end.")], lasting={REACHING + 2: IN_FLIGHT_S})
    run_id = new_run(factory, alice_id, {"token_budget": BUDGET}, agent="g6_push")
    rc = run_context(settings, factory, keyring, STEP, backends=backend)
    assert execute(rc, run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == runner.TOKEN_LIMIT
    assert backend.answered == REACHING + 1  # the request that gave the turn away
    assert len(backend.sent) == REACHING + 2  # and one that had left, cut
    assert (row.input_tokens, row.output_tokens) == (
        (REACHING + 1) * INPUT_SIDE,
        (REACHING + 1) * OUTPUT,
    )


def test_without_a_budget_the_same_loop_runs_to_its_end(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    g6_tools: None,
) -> None:
    """A run without a budget keeps the one loop it always had: nothing is counted, nothing is
    stopped, and a call to a tool the agent does not have is simply answered with an error."""
    backend = PushBackend(looping(BUILT_IN, TURNS))
    run_id = new_run(factory, alice_id, {}, agent="g6_push")
    rc = run_context(settings, factory, keyring, STEP, backends=backend)
    assert execute(rc, run_id) is RunStatus.done
    assert (len(backend.sent), backend.answered, backend.interrupts) == (TURNS + 1, TURNS + 1, 0)
    results = [data for kind, data in events(factory, run_id) if kind == "tool_result"]
    assert len(results) == TURNS
    assert all(
        data["is_error"] and "No such tool available: Bash" in data["content"] for data in results
    )


# --- the gate and the message of its call ---------------------------------------------------------


def test_the_gate_asked_before_the_message_of_its_call_waits_for_it(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    g6_tools: None,
) -> None:
    """The hook of a call is asked 50 ms before the message that made the call is printed (the
    SDK may hand over a hook request while the message is still in its buffer). The gate does
    not decide on a count that misses that message: it waits, and stops the reaching call."""
    backend = PushBackend(looping(OWN, TURNS), hook_first=True)
    run_id = new_run(factory, alice_id, {"token_budget": BUDGET}, agent="g6_push")
    rc = run_context(settings, factory, keyring, STEP, backends=backend)
    assert execute(rc, run_id) is RunStatus.failed
    assert run_row(factory, run_id).error == runner.TOKEN_LIMIT
    assert (len(backend.sent), backend.tools_run) == (REACHING, REACHING - 1)


def test_the_gate_fails_closed_when_the_message_of_its_call_never_comes(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The hook names a call no message carries. The gate cannot tell whether the count is
    complete: it stops the run, on a budget that is far from used up, and the run fails as an
    internal error, not as a budget reached."""
    backend = PushBackend(looping(OWN, TURNS), hook_id="toolu_of_no_message")
    run_id = new_run(factory, alice_id, {"token_budget": 10**9}, agent="g6_push")
    rc = run_context(settings, factory, keyring, STEP, backends=backend, gate_wait_s=0.2)
    with caplog.at_level(logging.INFO):
        assert execute(rc, run_id) is RunStatus.failed
    row = run_row(factory, run_id)
    assert row.error == runner.INTERNAL
    assert (len(backend.sent), backend.tools_run, WRITTEN) == (1, 0, [])  # the call never ran
    last = [data for kind, data in events(factory, run_id) if kind == "tool_result"][-1]
    assert last["is_error"] is True
    assert "token count could not be confirmed" in last["content"]
    logged = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert logged == ["agent run stopped: its token count could not be confirmed"]
    assert (row.input_tokens, row.output_tokens) == (INPUT_SIDE, OUTPUT)


# --- a private run and a tool name the model made up ----------------------------------------------


def test_what_the_cli_says_about_a_made_up_tool_is_not_stored_for_a_private_run(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    g6_tools: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The path of the real CLI for a tool it does not offer: no hook, no `tool_denied`, and an
    error result written by the CLI that repeats the name the model made up. A private run
    stores the size of that result and `[not stored]` for the name."""
    private = agent("g6_push", tools=ALL_TOOLS, private_io=True)
    made_up = f"mcp__app__{MARKER}"
    backend = PushBackend([Turn("Trying.", [(made_up, {"text": MARKER})]), Turn("Done.")])
    run_id = new_run(factory, alice_id, {}, agent="g6_push")
    rc = run_context(settings, factory, keyring, private, backends=backend)
    with caplog.at_level(logging.DEBUG):
        assert execute(rc, run_id) is RunStatus.done
    evs = events(factory, run_id)
    assert [data for kind, data in evs if kind == "tool_call"] == [
        {"id": "toolu_01_0", "name": runner.NOT_STORED, "input_size": len(MARKER) + 11}
    ]
    error = f"<tool_use_error>Error: No such tool available: {made_up}</tool_use_error>"
    assert [data for kind, data in evs if kind == "tool_result"] == [
        {"id": "toolu_01_0", "is_error": True, "size": len(error), "blocks": 0}
    ]
    assert [kind for kind, _ in evs if kind == "tool_denied"] == []
    assert MARKER not in everything_stored(factory, run_id)
    assert MARKER not in caplog.text


# --- the control: the first version of the loop, seen by this backend -----------------------------


@pytest.mark.parametrize("tool", [BUILT_IN, INVENTED])
def test_counting_behind_the_event_writes_lets_every_request_leave(
    tool: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    g6_tools: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loop as it was first built for a run with a budget: one loop that counts a message
    when it gets to it, behind the event writes. On this backend, with writes slower than
    requests, all nine requests leave before the count has reached the budget, and the run
    records fewer tokens than it used: what the review measured on the real CLI. It is why the
    count has a reader of its own, and it shows the tests above could have failed."""

    async def behind(messages: Any, budget: runner.Budget, state: Any, recorder: Any) -> None:
        async for message in messages:
            if isinstance(message, AssistantMessage):
                budget.count(message)
            await runner._handle(message, state, recorder)

    monkeypatch.setattr(runner, "_drive_counting", behind)
    slow_writes(monkeypatch, 0.12)
    backend = PushBackend(looping(tool, TURNS), request_s=0.03)
    run_id = new_run(factory, alice_id, {"token_budget": BUDGET}, agent="g6_push")
    rc = run_context(settings, factory, keyring, STEP, backends=backend)
    execute(rc, run_id)
    assert len(backend.sent) == TURNS + 1  # every request of the loop, six past the budget
    assert backend.answered == TURNS + 1
    # And the run says it used less than it did: its own count was behind as well.
    row = run_row(factory, run_id)
    assert (row.input_tokens or 0) + (row.output_tokens or 0) < (TURNS + 1) * INPUT_SIDE
