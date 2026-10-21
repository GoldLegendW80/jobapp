"""The token budget of a run (ingestion G6, points 6 and 7; decision 10 of the owner relies on
it), on the fake backend and a real Postgres. Absent: no limit. Zero, or anything that is not a
whole number above zero: no call at all. Otherwise the run is stopped once what it spent has
reached the budget and the turn goes on: interrupted as soon as a tool call is seen, and stopped
by the gate at a tool call the gate is asked about. A stopped run ends failed with TOKEN_LIMIT,
a constant, and a run with a budget always records what it used.

What this file can and cannot show. The fake backend is pulled by the runner and, by default,
asks the gate about every tool: here the model calls the agent's own tool, the gate is asked,
and no request leaves after the one that reached the budget, for every budget. What the real
CLI adds (requests at their own pace, tools it does not offer answered without the gate) is in
test_g6_budget_push.py, on a backend that behaves like it, and in test_g6_real_cli.py.

One request of the recorded streams costs PER_REQUEST tokens (g6_helpers.USAGE)."""

import asyncio
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions, failures, fake_backend, runner, runs
from app.agents.backend import BackendRequest
from app.agents.crypto import Keyring
from app.agents.models import RunStatus
from app.config import Settings
from tests.agents.g6_helpers import (
    ALL_TOOLS,
    MARKER,
    PER_REQUEST,
    USAGE,
    WRITTEN,
    Streams,
    agent,
    assistant,
    call,
    everything_stored,
    g6_tools,
    init,
    loop,
    raw_call,
    result,
    run_context,
    streams,
    text,
)
from tests.agents.live_helpers import RecordingStore
from tests.agents.run_helpers import (
    Backends,
    add_user,
    events,
    execute,
    kinds,
    new_run,
    now,
    run_row,
    save_credential,
)
from tests.conftest import make_settings

__all__ = ["g6_tools", "streams"]

BUDGETED = agent("g6_budget", tools=ALL_TOOLS)
TURNS = 5  # the loop of these tests: five requests that call a tool, a sixth that answers
INPUT_SIDE = USAGE["input_tokens"] + 100 + 100
OUTPUT = USAGE["output_tokens"]
assert INPUT_SIDE + OUTPUT == PER_REQUEST


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


def run_loop(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    user: uuid.UUID,
    data: dict[str, Any],
    found: definitions.AgentDefinition = BUDGETED,
    **changes: Any,
) -> tuple[uuid.UUID, Any, Any]:
    backends = Backends()
    run_id = new_run(factory, user, data, agent=found.name)
    rc = run_context(settings, factory, keyring, found, backends=backends, **changes)
    status = execute(rc, run_id)
    return run_id, status, backends.made[0] if backends.made else None


# --- the budget a run is given --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "budget"),
    [
        ({}, None),
        ({"other": 1}, None),
        ({"token_budget": 1}, 1),
        ({"token_budget": 250_000}, 250_000),
        ({"token_budget": 27_109_890}, 27_109_890),  # the largest ceiling the plan computes
        ({"token_budget": 0}, 0),
        ({"token_budget": -1}, 0),
        ({"token_budget": -(10**9)}, 0),
        ({"token_budget": None}, 0),
        ({"token_budget": "1000"}, 0),
        ({"token_budget": 1000.0}, 0),
        ({"token_budget": 0.5}, 0),
        ({"token_budget": True}, 0),
        ({"token_budget": [1000]}, 0),
        ({"token_budget": {"limit": 1000}}, 0),
    ],
)
def test_reading_the_budget(data: dict[str, Any], budget: int | None) -> None:
    """Only an absent key means no limit. Whatever else stands there and is not a whole number
    above zero is read as zero: a budget computed wrongly never becomes an unlimited run."""
    assert runner.token_budget(data) == budget
    assert runner.BUDGET_KEY == "token_budget"


def test_without_a_budget_there_is_no_limit(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    streams.add("g6_budget", loop(TURNS))
    run_id, status, backend = run_loop(settings, factory, keyring, alice_id, {})
    assert status is RunStatus.done
    assert len(backend.seen_requests) == TURNS + 1
    assert len(WRITTEN) == TURNS
    row = run_row(factory, run_id)
    # Nothing is counted for a run without a budget: its tokens are the result's, as before.
    assert (row.input_tokens, row.output_tokens) == (0, 0)


@pytest.mark.parametrize("budget", [0, -1, -5000, None, "100000", 2.5, True])
def test_a_budget_of_zero_allows_no_call(
    budget: Any,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """Zero, and whatever is read as zero: the run fails before Claude Code is started."""
    streams.add("g6_budget", loop(TURNS))
    run_id, status, backend = run_loop(
        settings, factory, keyring, alice_id, {"token_budget": budget}
    )
    assert status is RunStatus.failed
    assert backend is None  # no backend was even made: no request can have left
    row = run_row(factory, run_id)
    assert row.error == runner.TOKEN_LIMIT == "The run reached its token budget and was stopped."
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (None, None, None)
    assert row.credential_kind is not None  # the credential was read, and nothing more
    assert kinds(factory, run_id) == ["status", "status", "status"]
    assert events(factory, run_id)[-1] == (
        "status",
        {"status": "failed", "error": runner.TOKEN_LIMIT},
    )
    assert WRITTEN == []
    assert list(Path(settings.agents_scratch_dir or "").iterdir()) == []


# --- the overshoot --------------------------------------------------------------------------------


def test_the_budget_is_passed_inside_a_tool_loop(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """A budget of 2,000 with requests of 940: the third request passes it. Its tool call is
    stopped at the gate, the tool does not run, and no fourth request leaves."""
    streams.add("g6_budget", loop(TURNS))
    run_id, status, backend = run_loop(settings, factory, keyring, alice_id, {"token_budget": 2000})
    assert status is RunStatus.failed
    assert backend.seen_requests == ["msg_01", "msg_02", "msg_03"]
    assert len(WRITTEN) == 2  # the tools of the first two requests ran, the third did not
    row = run_row(factory, run_id)
    assert row.error == runner.TOKEN_LIMIT
    assert runner.is_provider_failure(row.error) is False
    # The result reports no usage here: the runner's own count is recorded.
    assert (row.input_tokens, row.output_tokens) == (3 * INPUT_SIDE, 3 * OUTPUT)
    assert row.output is None
    evs = events(factory, run_id)
    stopped = [data for kind, data in evs if kind == "tool_result"][-1]
    assert stopped == {
        "id": "toolu_03",
        "is_error": True,
        "content": "PreToolUse:mcp__app__g6_write hook error: The run reached its token budget.",
    }
    assert [data for kind, data in evs if kind == "tool_denied"] == []  # a stop, not a refusal
    assert evs[-1] == ("status", {"status": "failed", "error": runner.TOKEN_LIMIT})


LAST = (TURNS + 1) * PER_REQUEST


@pytest.mark.parametrize(
    "budget",
    [
        1,
        PER_REQUEST - 1,
        PER_REQUEST,  # exactly reached by the first request
        PER_REQUEST + 1,
        2 * PER_REQUEST - 1,
        2 * PER_REQUEST,  # exactly reached by the second
        2 * PER_REQUEST + 1,
        3000,
        TURNS * PER_REQUEST - 1,
        TURNS * PER_REQUEST,  # exactly reached by the last request that calls a tool
        TURNS * PER_REQUEST + 1,  # passed by the request that only answers
        LAST - 1,
        LAST,
        LAST + 1,
        10**7,
    ],
)
def test_no_request_leaves_after_the_one_that_reached_the_budget(
    budget: int,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """The overshoot where the gate is asked (the model calls a tool of the agent), for every
    budget. The request whose usage reaches the budget cannot be prevented (its usage is known
    when it arrives); no request follows it. So the run passes its budget by less than one
    request, and a budget reached exactly is not passed at all."""
    streams.add("g6_budget", loop(TURNS))
    run_id, status, backend = run_loop(
        settings, factory, keyring, alice_id, {"token_budget": budget}
    )
    reaching = -(-budget // PER_REQUEST)  # the request whose usage reaches the budget
    row = run_row(factory, run_id)
    if reaching <= TURNS:
        assert status is RunStatus.failed
        assert row.error == runner.TOKEN_LIMIT
        requests = len(backend.seen_requests)
        assert requests == reaching
        assert requests - reaching == 0  # requests after the one that reached the budget
        assert len(WRITTEN) == reaching - 1  # its tool call did not run
        spent = (row.input_tokens or 0) + (row.output_tokens or 0)
        assert spent == reaching * PER_REQUEST
        assert 0 <= spent - budget < PER_REQUEST
    else:
        # The budget is reached by the request that ends the run, or never: nothing is cut.
        assert status is RunStatus.done
        assert len(backend.seen_requests) == TURNS + 1
        assert len(WRITTEN) == TURNS
        assert (row.input_tokens or 0) + (row.output_tokens or 0) == LAST


def test_the_last_message_uses_the_budget_up_and_the_run_is_done(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """One request, far over its budget, that asks for nothing more: there is nothing to stop.
    The run is done and says what it spent, so its caller can act on it."""
    streams.add("g6_budget", [init(), assistant("m1", text("All in one answer."))])
    run_id, status, _ = run_loop(settings, factory, keyring, alice_id, {"token_budget": 10})
    assert status is RunStatus.done
    row = run_row(factory, run_id)
    assert row.output == {"text": "All in one answer."}
    assert (row.input_tokens, row.output_tokens, row.error) == (INPUT_SIDE, OUTPUT, None)


def test_a_turn_that_goes_on_without_a_tool_call_is_interrupted_at_its_next_request(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """A turn that goes on by itself, with no tool call to give it away: it is seen when the
    next request's first message arrives, and the run is interrupted there. On this backend,
    which waits to be pulled, that is one request more (test_g6_budget_push.py has the same
    turn at the CLI's own pace)."""
    streams.add(
        "g6_budget",
        [
            init(),
            assistant("m1", text("First.")),
            assistant("m2", text("Still going.")),
            {"sleep": 5},
            assistant("m3", text("Never asked for.")),
            assistant("m4", text("Nor this.")),
        ],
    )
    run_id, status, backend = run_loop(settings, factory, keyring, alice_id, {"token_budget": 500})
    assert status is RunStatus.failed
    assert backend.seen_requests == ["m1", "m2"]
    row = run_row(factory, run_id)
    assert row.error == runner.TOKEN_LIMIT
    assert (row.input_tokens, row.output_tokens) == (2 * INPUT_SIDE, 2 * OUTPUT)
    assert [data["text"] for kind, data in events(factory, run_id) if kind == "text"] == [
        "First.",
        "Still going.",
    ]


@pytest.mark.parametrize("gate", ["offered", "all"])
@pytest.mark.parametrize("name", ["Bash", "mcp__app__made_up"])
def test_the_model_calls_a_tool_the_agent_does_not_have(
    name: str,
    gate: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """The stop does not depend on the gate being asked. With `"gate": "offered"` the fake
    backend answers such a call itself, as the real CLI does: the gate hears nothing, no
    `tool_denied` is stored, and the run is interrupted when the call of the reaching request
    arrives. With the fake's default the gate is asked about every name, which the CLI never
    does: the two earlier calls are stored as denied and the third is stopped at the gate. The
    run ends the same either way; only the first is what the CLI does."""
    steps = [init()]
    for index in range(1, TURNS + 1):
        steps += [
            {"sleep": 2},  # a request takes time: the fake's own are instant
            assistant(f"m{index}", raw_call(f"t{index}", name, text="x")),
        ]
    streams.add("g6_budget", [*steps, assistant("end", text("Finished."))], gate=gate)
    # The budget's stop and the gate's answer to the third call race: the run is interrupted
    # and its consumer given the grace to store what is already on the stream. The helpers'
    # one second grace was too short under the gate's load (the third result was cut off), so
    # this test gives the consumer the product's own grace.
    run_id, status, backend = run_loop(
        settings,
        factory,
        keyring,
        alice_id,
        {"token_budget": 2000},
        interrupt_grace_s=runner.INTERRUPT_GRACE_S,
    )
    assert status is RunStatus.failed
    assert run_row(factory, run_id).error == runner.TOKEN_LIMIT
    assert backend.seen_requests == ["m1", "m2", "m3"]
    evs = events(factory, run_id)
    denied = [data["name"] for kind, data in evs if kind == "tool_denied"]
    results = [data["content"] for kind, data in evs if kind == "tool_result"]
    if gate == "offered":
        assert denied == []
        assert results == [
            f"<tool_use_error>Error: No such tool available: {name}</tool_use_error>"
        ] * len(results)
    else:
        assert denied == [name, name]
        assert results[-1] == f"PreToolUse:{name} hook error: The run reached its token budget."


def test_a_stream_cannot_misspell_its_gate(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    streams.add("g6_budget", loop(1), gate="offerd")
    _, status, _ = run_loop(settings, factory, keyring, alice_id, {})
    assert status is RunStatus.failed  # the replay refuses the stream, loudly


# --- one count per API message --------------------------------------------------------------------


def test_usage_repeated_on_the_messages_of_one_api_call_is_counted_once(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """Claude Code prints one assistant message per content block, each with the usage of the
    whole API message and its id (recorded on the pinned CLI). Three blocks are one request."""
    thinking = {"type": "thinking", "thinking": "hm", "signature": "s"}
    streams.add(
        "g6_budget",
        [
            init(),
            assistant("m1", thinking),
            assistant("m1", text("Let me store it.")),
            assistant("m1", call("t1", "g6_write", text="one")),
            assistant("m2", text("And again.")),
            assistant("m2", call("t2", "g6_write", text="two")),
            assistant("m3", text("Done.")),
        ],
    )
    # Counted three times the first request would be 2,820 and the run would stop at once.
    run_id, status, backend = run_loop(
        settings, factory, keyring, alice_id, {"token_budget": PER_REQUEST + 1}
    )
    assert status is RunStatus.failed
    assert backend.seen_requests == ["m1", "m2"]  # five printed messages, two requests
    assert [args for _, _, args in WRITTEN] == [{"text": "one"}]
    row = run_row(factory, run_id)
    assert (row.input_tokens, row.output_tokens) == (2 * INPUT_SIDE, 2 * OUTPUT)


def test_a_budget_on_a_run_whose_answer_is_live(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """A public agent with partial messages on: the fake backend then prints each content
    block as a message of its own, as the CLI does, between the stream events. The count is
    the same as without them, and the live text is cleared when the gate stops the run."""
    steps = [
        init(),
        assistant("m1", text("Storing the first one."), call("t1", "g6_write", text="one")),
        assistant("m2", text("Storing the second one."), call("t2", "g6_write", text="two")),
        assistant("m3", text("Done.")),
    ]
    streams.add("g6_budget", steps, partial={"chars": 5, "sleep": 0.01})
    store = RecordingStore()
    run_id, status, backend = run_loop(
        settings,
        factory,
        keyring,
        alice_id,
        {"token_budget": PER_REQUEST + 1},
        live_store=store,
        live_flush_s=0.01,
    )
    assert backend.seen_options.include_partial_messages is True
    assert status is RunStatus.failed
    assert run_row(factory, run_id).error == runner.TOKEN_LIMIT
    assert backend.seen_requests == ["m1", "m2"]
    assert [args for _, _, args in WRITTEN] == [{"text": "one"}]
    row = run_row(factory, run_id)
    assert (row.input_tokens, row.output_tokens) == (2 * INPUT_SIDE, 2 * OUTPUT)
    assert [op[1] for op in store.ops if op[0] == "open"]  # the answer was live meanwhile
    assert store.meta == {}  # and nothing is left of it


def test_the_largest_figures_seen_for_an_id_are_the_ones_counted() -> None:
    budget = runner.Budget(10**6)

    def said(message_id: str | None, **usage: Any) -> AssistantMessage:
        return AssistantMessage(
            content=[TextBlock(text="x")], model="m", usage=usage or None, message_id=message_id
        )

    budget.count(said("a", input_tokens=100, output_tokens=1))
    budget.count(said("a", input_tokens=100, output_tokens=40))  # the output grew
    budget.count(said("a", input_tokens=100, output_tokens=1))  # an older figure again
    assert (budget.input_tokens, budget.output_tokens, budget.requests) == (100, 40, 1)
    budget.count(said("b", input_tokens=10, cache_creation_input_tokens=20, output_tokens=5))
    budget.count(said("b", cache_read_input_tokens=300))
    assert (budget.input_tokens, budget.output_tokens, budget.requests) == (400, 45, 2)
    # Figures that are not whole numbers above zero count for nothing, and nothing raises.
    budget.count(said("c", input_tokens="many", output_tokens=-5, cache_read_input_tokens=True))
    budget.count(said("d"))
    assert (budget.spent, budget.requests) == (445, 4)


def test_a_message_without_an_id_counts_on_its_own() -> None:
    """It cannot be recognised when it comes again: too much is counted rather than too
    little. (The pinned CLI always gives the id.)"""
    budget = runner.Budget(1000)
    message = AssistantMessage(
        content=[TextBlock(text="x")], model="m", usage={"input_tokens": 400}, message_id=None
    )
    budget.count(message)
    budget.count(message)
    assert (budget.spent, budget.requests, budget.used_up) == (800, 2, False)
    budget.count(message)
    assert (budget.spent, budget.used_up) == (1200, True)
    assert not budget.stop.is_set()
    budget.count(message)  # a request that left with nothing left to spend
    assert budget.stop.is_set()


def test_the_count_stops_the_run_when_nothing_is_left_and_the_turn_goes_on() -> None:
    """Decided when the message is counted, from the message alone, never from the gate: a
    tool call means another request, whatever tool is called and whoever answers it."""

    def message(message_id: str, tokens: int, *names: str) -> AssistantMessage:
        blocks: list[Any] = [TextBlock(text="x")]
        blocks += [ToolUseBlock(id=f"{message_id}-{name}", name=name, input={}) for name in names]
        return AssistantMessage(
            content=blocks, model="m", usage={"input_tokens": tokens}, message_id=message_id
        )

    # Something is left: a tool call stops nothing.
    budget = runner.Budget(1000)
    budget.count(message("a", 999, "Bash"))
    assert not budget.stop.is_set()
    # Nothing is left and the message asks for nothing more: the run is ending by itself.
    budget.count(message("b", 1))
    assert budget.used_up
    assert not budget.stop.is_set()
    # The same API message, its next content block, a tool call: the turn goes on. Stop.
    budget.count(message("b", 1, "mcp__app__made_up"))
    assert budget.stop.is_set()
    # Whatever the tool: the agent's own, a built-in name, one nobody offers.
    for name in ("mcp__app__g6_write", "Bash", "anything at all"):
        budget = runner.Budget(1000)
        budget.count(message("a", 1000, name))
        assert budget.stop.is_set(), name
    # The gate has stopped nothing in any of this.
    assert (budget.refused, budget.unsure) == (False, False)


# --- what a run with a budget records -------------------------------------------------------------


def test_the_result_messages_figures_are_recorded_when_it_reports_any(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """The result message counts the whole run, output included (an assistant message reports
    its output as of the start of the answer): when it says something, that is what is kept."""
    steps = [
        *loop(2),
        result(
            "Finished.",
            turns=3,
            input_tokens=5000,
            output_tokens=700,
            cache_creation_input_tokens=1000,
            cache_read_input_tokens=2000,
        ),
    ]
    streams.add("g6_budget", steps)
    run_id, status, _ = run_loop(settings, factory, keyring, alice_id, {"token_budget": 10**6})
    assert status is RunStatus.done
    row = run_row(factory, run_id)
    assert (row.input_tokens, row.output_tokens) == (8000, 700)
    assert float(row.cost_usd or 0) == pytest.approx(0.02)


SLOW_LOOP = [
    init(),
    assistant("m1", call("t1", "g6_write", text="one")),
    assistant("m2", call("t2", "g6_write", text="two")),
    {"sleep": 30},
    assistant("m3", text("Never.")),
]


@pytest.mark.parametrize("budget", [10**6, None], ids=["with a budget", "without"])
@pytest.mark.parametrize("ending", ["cancelled", "the worker stops", "time limit", "it dies"])
def test_a_run_with_a_budget_says_what_it_used_however_it_ends(
    ending: str,
    budget: int | None,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """Two requests were made when the run is cut. With a budget their tokens are on the run
    whatever cut it, so whoever adds runs up loses none. Without one nothing changes: the run
    records what the result message said, or nothing."""
    from dataclasses import replace

    steps = [*SLOW_LOOP[:3], {"raise": "process"}] if ending == "it dies" else SLOW_LOOP
    streams.add("g6_budget", steps)
    found = replace(BUDGETED, wall_time_s=2.0) if ending == "time limit" else BUDGETED
    data = {} if budget is None else {"token_budget": budget}
    run_id = new_run(factory, alice_id, data, agent="g6_budget")
    rc = run_context(settings, factory, keyring, found, backends=Backends(speed=1.0))

    async def go() -> Any:
        task = asyncio.create_task(runner.execute(rc, run_id))
        if ending in ("cancelled", "the worker stops"):
            while len(WRITTEN) < 2 or kinds(factory, run_id).count("tool_result") < 2:  # noqa: ASYNC110
                await asyncio.sleep(0.02)
        if ending == "cancelled":
            with factory() as db:
                runs.request_cancel(db, alice_id, run_id, now())
                db.commit()
        if ending == "the worker stops":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return None
        return await task

    asyncio.run(go())
    row = run_row(factory, run_id)
    expected = {
        "cancelled": (RunStatus.cancelled, None),
        "the worker stops": (RunStatus.failed, runs.STOPPED),
        "time limit": (
            RunStatus.failed,
            "The run took longer than its limit of 2 seconds and was stopped.",
        ),
        "it dies": (RunStatus.failed, "The Claude Code process failed (exit code 1)."),
    }[ending]
    assert (row.status, row.error) == expected
    recorded = (row.input_tokens, row.output_tokens)
    if budget is not None:
        assert recorded == (2 * INPUT_SIDE, 2 * OUTPUT)
    elif ending in ("the worker stops", "it dies"):
        assert recorded == (None, None)  # as before this task
    else:
        assert recorded == (0, 0)  # the interrupted result's own figures, as before


@pytest.mark.parametrize("budget", [10**6, None], ids=["with a budget", "without"])
def test_a_run_a_reaper_failed_keeps_what_it_had_counted(
    budget: int | None,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """The one end no runner records: the worker is killed in the middle of a run and a reaper
    fails it later. A run with a budget writes what it has counted on its row with each of its
    events, so the row holds the two requests it had made when it was reaped, and whoever adds
    up a chain of runs sees them. A run without a budget has no figure while it runs, as
    before."""
    streams.add("g6_budget", SLOW_LOOP)
    data = {} if budget is None else {"token_budget": budget}
    run_id = new_run(factory, alice_id, data, agent="g6_budget")
    rc = run_context(settings, factory, keyring, BUDGETED, backends=Backends(speed=1.0))
    while_running: list[tuple[Any, Any, Any]] = []

    async def go() -> None:
        task = asyncio.create_task(runner.execute(rc, run_id))
        while kinds(factory, run_id).count("tool_result") < 2:  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        row = run_row(factory, run_id)
        while_running.append((row.status, row.input_tokens, row.output_tokens))
        with factory() as db:
            assert runs.reap_orphans(db, now()) == 1
            db.commit()
        task.cancel()  # the process that was running it is gone
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.failed, runs.INTERRUPTED)  # the reaper's end
    counted = (2 * INPUT_SIDE, 2 * OUTPUT) if budget is not None else (None, None)
    assert while_running == [(RunStatus.running, *counted)]
    assert (row.input_tokens, row.output_tokens) == counted


def test_record_tokens_only_touches_a_running_run(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    run_id = new_run(factory, alice_id, {}, agent="g6_budget")
    with factory() as db, db.begin():
        runs.record_tokens(db, run_id, 10, 2)  # still queued: nothing is written
    assert (run_row(factory, run_id).input_tokens, run_row(factory, run_id).output_tokens) == (
        None,
        None,
    )
    with factory() as db, db.begin():
        runs.claim(db, run_id, now())
        runs.record_tokens(db, run_id, 10, 2)
    assert (run_row(factory, run_id).input_tokens, run_row(factory, run_id).output_tokens) == (
        10,
        2,
    )
    with factory() as db, db.begin():
        done = runs.Outcome(status=RunStatus.done, input_tokens=900, output_tokens=40)
        assert runs.finish(db, run_id, done, now())
        runs.record_tokens(db, run_id, 1, 1)  # the run has ended: its final figures stay
    assert (run_row(factory, run_id).input_tokens, run_row(factory, run_id).output_tokens) == (
        900,
        40,
    )


def test_complete_keeps_figures_that_are_there_and_fills_in_the_ones_that_are_not() -> None:
    state = runner._State()
    plain = runs.Outcome(status=RunStatus.failed, error="x")
    assert runner._complete(plain, state, definitions.ECHO) is plain  # no budget, no change
    state.budget = runner.Budget(100)
    state.budget.count(
        AssistantMessage(
            content=[], model="m", usage={"input_tokens": 70, "output_tokens": 5}, message_id="a"
        )
    )
    assert runner._complete(plain, state, None).input_tokens == 70
    assert runner._complete(plain, state, None).output_tokens == 5
    zeros = runs.Outcome(status=RunStatus.failed, input_tokens=0, output_tokens=0)
    assert runner._complete(zeros, state, definitions.ECHO).input_tokens == 70
    reported = runs.Outcome(status=RunStatus.done, input_tokens=900, output_tokens=30)
    assert runner._complete(reported, state, definitions.ECHO) is reported
    only_output = runs.Outcome(status=RunStatus.done, input_tokens=0, output_tokens=30)
    assert runner._complete(only_output, state, definitions.ECHO) is only_output
    # A private run's output is its counts, on any path.
    private = agent("g6_private", private_io=True)
    answered = runs.Outcome(status=RunStatus.done, output={"text": "the answer"})
    assert runner._complete(answered, runner._State(), private).output == {
        "turns": 0,
        "text_blocks": 0,
        "text_chars": 0,
        "tool_calls": 0,
        "tool_errors": 0,
        "tools_denied": 0,
    }


# --- how a run ends -------------------------------------------------------------------------------


def finished(**changes: Any) -> ResultMessage:
    base: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 3,
        "session_id": "s",
        "result": "the answer",
    }
    return ResultMessage(**{**base, **changes})


def test_outcome_of_a_run_with_a_budget() -> None:
    state = runner._State(budget=runner.Budget(100))
    # Nothing stopped it: an ordinary end.
    state.result = finished()
    assert runner.outcome_of(state, BUDGETED, None).status is RunStatus.done
    # The gate stopped a tool call: Claude Code says "success" (hook_stopped). It is not.
    state.budget.refused = True
    state.result = finished(terminal_reason="hook_stopped", result="", stop_reason="tool_use")
    stopped = runner.outcome_of(state, BUDGETED, None, "claude-sonnet-5-5")
    assert (stopped.status, stopped.error, stopped.output) == (
        RunStatus.failed,
        runner.TOKEN_LIMIT,
        None,
    )
    assert stopped.model == "claude-sonnet-5-5"
    # The user's cancel and the clock come first: they say what happened to the run.
    assert runner.outcome_of(state, BUDGETED, "cancelled").status is RunStatus.cancelled
    assert "took longer" in (runner.outcome_of(state, BUDGETED, "timeout").error or "")
    # Interrupted for a request that left with nothing left to spend.
    state = runner._State(budget=runner.Budget(100))
    assert runner.outcome_of(state, BUDGETED, "budget").error == runner.TOKEN_LIMIT
    state.result = finished(subtype="error_during_execution", is_error=True)
    assert runner.outcome_of(state, BUDGETED, "budget").error == runner.TOKEN_LIMIT
    # ... unless that request ended the run cleanly before the interrupt landed: nothing was cut.
    state.result = finished()
    done = runner.outcome_of(state, BUDGETED, "budget")
    assert (done.status, done.output) == (RunStatus.done, {"text": "the answer"})


# --- the gate -------------------------------------------------------------------------------------


def asked(gate: Any, name: str, tool_use_id: str | None = "t1") -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        found: dict[str, Any] = await gate({"tool_name": name, "tool_input": {}}, tool_use_id, None)
        return found

    return asyncio.run(go())


def calling(call_id: str, tokens: int, message_id: str = "m") -> AssistantMessage:
    return AssistantMessage(
        content=[ToolUseBlock(id=call_id, name="mcp__app__g6_write", input={})],
        model="m",
        usage={"input_tokens": tokens},
        message_id=message_id,
    )


def test_the_gate_stops_the_run_once_the_budget_is_used_up() -> None:
    denied: list[str] = []

    async def on_denied(name: str) -> None:
        denied.append(name)

    budget = runner.Budget(1000)
    gate = runner.make_gate(frozenset({"mcp__app__g6_write"}), on_denied, budget)
    budget.count(calling("t1", 999))
    assert asked(gate, "mcp__app__g6_write") == {}  # one token left: the call goes through
    assert asked(gate, "Bash")["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert denied == ["Bash"]
    assert budget.refused is False
    budget.count(calling("t2", 1, "m2"))  # exactly reached
    answer = asked(gate, "mcp__app__g6_write", "t2")
    assert answer == {
        "continue_": False,
        "stopReason": "The run reached its token budget.",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "The run reached its token budget.",
        },
    }
    assert budget.refused is True
    # Any tool, also one the agent does not have: the run is over, nothing is reported denied.
    assert asked(gate, "Bash", "t2")["continue_"] is False
    assert denied == ["Bash"]


def test_the_gate_waits_for_the_message_of_its_call_to_be_counted() -> None:
    """The SDK can hand the hook of a tool call to its own task before the runner has handled
    the assistant message that made the call. The gate then waits for it: it never decides on
    a count that misses the request it is asked about."""

    async def never(name: str) -> None:  # pragma: no cover
        raise AssertionError(name)

    async def go() -> tuple[bool, dict[str, Any]]:
        budget = runner.Budget(1000, wait_s=5)
        gate = runner.make_gate(frozenset({"mcp__app__g6_write"}), never, budget)
        pending = asyncio.create_task(
            gate({"tool_name": "mcp__app__g6_write", "tool_input": {}}, "t9", None)
        )
        await asyncio.sleep(0.05)
        undecided = not pending.done()
        budget.count(calling("t8", 10, "m8"))  # another message: still waiting
        await asyncio.sleep(0.05)
        undecided = undecided and not pending.done()
        budget.count(calling("t9", 5000, "m9"))  # the one that made the call, and passes
        return undecided, await asyncio.wait_for(pending, 2)

    undecided, answer = asyncio.run(go())
    assert undecided
    assert answer["continue_"] is False


@pytest.mark.parametrize("tool_use_id", ["never printed", None], ids=["unknown call", "no call"])
def test_the_gate_fails_closed_when_it_cannot_tell_that_its_call_is_counted(
    tool_use_id: str | None,
) -> None:
    """The wait ends without the message of the call, or the hook names no call at all. The
    count may be behind: the gate stops the run instead of letting the call through on it,
    although nothing says the budget is reached. (The first version let the call through.)"""

    async def never(name: str) -> None:  # pragma: no cover
        raise AssertionError(name)

    async def go() -> tuple[dict[str, Any], runner.Budget]:
        budget = runner.Budget(10**9, wait_s=0.05)
        gate = runner.make_gate(frozenset({"mcp__app__g6_write"}), never, budget)
        budget.count(calling("t1", 10))  # another call's message: not the one asked about
        call_of = {"tool_name": "mcp__app__g6_write", "tool_input": {}}
        return await gate(call_of, tool_use_id, None), budget

    answer, budget = asyncio.run(go())
    assert answer == {
        "continue_": False,
        "stopReason": "The run's token count could not be confirmed.",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "The run's token count could not be confirmed.",
        },
    }
    assert (budget.unsure, budget.refused, budget.used_up) == (True, False, False)
    assert budget.stop.is_set()  # and the run is interrupted as well
    # Such a run did not reach its budget: it failed on the runner's side.
    state = runner._State(budget=budget)
    state.result = finished(terminal_reason="hook_stopped", result="")
    assert runner.outcome_of(state, BUDGETED, "budget").error == runner.INTERNAL
    assert runner.outcome_of(state, BUDGETED, None).error == runner.INTERNAL
    assert runner.outcome_of(state, BUDGETED, "cancelled").status is RunStatus.cancelled
    assert runner.GATE_WAIT_S == 5.0


def test_a_gate_without_a_budget_is_the_gate_of_before() -> None:
    async def on_denied(name: str) -> None:
        return None

    gate = runner.make_gate(frozenset({"mcp__app__get_job"}), on_denied)
    assert asked(gate, "mcp__app__get_job", "never counted") == {}
    assert asked(gate, "Bash")["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- the fake backend's hook stop -----------------------------------------------------------------


def test_the_fake_backend_ends_the_replay_on_a_hook_stop_as_the_cli_ends_the_turn(
    streams: Streams, g6_tools: None
) -> None:
    """Recorded on the pinned CLI (tasks/evidence/ingestion-g6/): the tool does not run, its
    result is the hook's error, and the result message says success with the terminal reason
    hook_stopped. No other call of the message is made and no further message is replayed."""
    from app.agents import tools as app_tools

    streams.add(
        "g6_stop",
        [
            init(),
            assistant("m1", call("t1", "g6_write", text="a"), call("t2", "g6_write", text="b")),
            assistant("m2", text("Never.")),
        ],
    )
    tools = app_tools.build(None, uuid.uuid4(), ("g6_write",))  # type: ignore[arg-type]

    async def stop(*_: Any) -> dict[str, Any]:
        return {"continue_": False, "stopReason": "enough"}

    async def go() -> list[Any]:
        backend = fake_backend.FakeBackend(speed=0)
        options = runner.build_options(BUDGETED, tools, {}, Path("."), stop)
        request = BackendRequest(
            prompt="x", options=options, tools=tools, gate=stop, script="g6_stop"
        )
        found = [message async for message in backend.stream(request)]
        assert backend.seen_requests == ["m1"]
        return found

    messages = asyncio.run(go())
    assert WRITTEN == []
    answered = messages[-2].content
    assert [(block.tool_use_id, block.is_error) for block in answered] == [("t1", True)]
    assert answered[0].content == "PreToolUse:mcp__app__g6_write hook error: enough"
    last = messages[-1]
    assert isinstance(last, ResultMessage)
    assert (last.subtype, last.is_error, last.terminal_reason, last.result, last.stop_reason) == (
        "success",
        False,
        "hook_stopped",
        "",
        "tool_use",
    )


# --- everything at once ---------------------------------------------------------------------------


def test_a_bound_private_hooked_run_stopped_on_its_budget(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    streams: Streams,
    g6_tools: None,
) -> None:
    """A step as the ingestion engine will run it: every point of the task in one run."""
    ended: list[definitions.FinalRun] = []

    async def on_final(final: definitions.FinalRun) -> None:
        ended.append(final)

    step = agent(
        "g6_step",
        tools=ALL_TOOLS,
        internal=True,
        private_io=True,
        bind=lambda db, run: {"text": f"the part with {MARKER}"},
        on_final=on_final,
        model_by_credential={"anthropic_api_key": "claude-sonnet-5-5"},
    )
    streams.add("g6_step", [init(), *loop(TURNS, text=f"item from {MARKER}")[1:]])
    store = RecordingStore()
    run_id, status, backend = run_loop(
        settings,
        factory,
        keyring,
        alice_id,
        {"ingestion_run_id": "r", "step_id": "s", "token_budget": 2 * PER_REQUEST},
        step,
        live_store=store,
    )
    assert status is RunStatus.failed
    row = run_row(factory, run_id)
    assert (row.error, row.model) == (runner.TOKEN_LIMIT, "claude-sonnet-5-5")
    assert (row.input_tokens, row.output_tokens) == (2 * INPUT_SIDE, 2 * OUTPUT)
    assert row.output == {
        "turns": 3,
        "text_blocks": 0,
        "text_chars": 0,
        "tool_calls": 2,
        "tool_errors": 1,  # the call the gate stopped
        "tools_denied": 0,
    }
    assert backend.seen_requests == ["msg_01", "msg_02"]
    assert len(WRITTEN) == 1  # the item of the first request is kept by its tool
    assert [final.status for final in ended] == [RunStatus.failed]
    assert store.ops == []
    assert MARKER not in everything_stored(factory, run_id)
    assert row.input == {"ingestion_run_id": "r", "step_id": "s", "token_budget": 2 * PER_REQUEST}


# --- what a caller may conclude from a failure ----------------------------------------------------

# Whose failure each message of the runner is: the provider's (the user fixes the credential and
# resumes), the box's (this worker could not run the agent: a chain pauses after the first one,
# decided by the orchestrator on 2026-10-11) or the run's own (the chain goes on).
KINDS = {
    "NO_CREDENTIAL": "provider",
    "UNREADABLE": "provider",
    "UNUSABLE": "provider",
    "REFUSED": "provider",
    "BUSY": "provider",
    "NO_CREDIT": "provider",
    "NO_CLI": "box",
    "NO_START": "box",
    "PROCESS": "box",
    "INTERNAL": "box",
    "UNKNOWN_AGENT": "box",
    "THREAD_GONE": "own",
    "ANSWER_NOT_STORED": "own",
    "BINDING_GONE": "own",
    "TIME_LIMIT": "own",
    "TURN_LIMIT": "own",
    "TOKEN_LIMIT": "own",
    "NO_RESULT": "own",
    "FAILED": "own",
}


def test_whose_failure_every_message_of_the_runner_is() -> None:
    constants = {
        name: value
        for name, value in vars(failures).items()
        if name.isupper() and isinstance(value, str)
    }
    # A message added to the module needs its kind here.
    assert set(constants) == set(KINDS) | {"API_ERROR"}
    filled = {"minutes": "15 minutes", "turns": 40, "reason": "error_during_execution", "code": 1}
    for name, kind in KINDS.items():
        message = constants[name].format(**filled)
        assert runner.is_provider_failure(message) is (kind == "provider"), name
        assert failures.is_box_failure(message) is (kind == "box"), name
        assert getattr(runner, name) is constants[name]  # the runner's names are these objects
    assert runner.is_provider_failure is failures.is_provider_failure


def test_is_box_failure() -> None:
    """The worker could not run the agent, whatever the run was about."""
    for code in (1, 137, -9, "unknown"):
        assert failures.is_box_failure(runner.PROCESS.format(code=code)) is True
    for message in (
        None,
        "",
        runner.PROCESS.format(code="1 and more"),
        runner.PROCESS + " ",
        "x" + runner.NO_CLI,
        runs.QUEUE_DOWN,  # the provider's side of a chain: the user waits and resumes
        runs.INTERRUPTED,  # a worker restart: the chain runs the step once more by its own rule
        runs.STOPPED,
        runs.LOST,
        runs.STALE,
    ):
        assert failures.is_box_failure(message) is False
    # No status of the API is the box's, and nothing is both the box's and the provider's.
    for status in (400, 401, 413, 422, 429, 500, 529):
        message = runner.API_ERROR.format(status=status)
        assert failures.is_box_failure(message) is False
    for message in (*failures._BOX, runner.PROCESS.format(code=1)):
        assert failures.is_provider_failure(message) is False


@pytest.mark.parametrize(
    ("status", "provider"),
    [
        *[(400, False), (413, False), (422, False), (429, True), (500, True), (529, True)],
        *[(401, True), (403, True), (404, True), (408, True), (502, True), (503, True)],
    ],
)
def test_is_provider_failure_on_the_api_errors(status: int, provider: bool) -> None:
    """A request the API refuses for what it holds fails again whatever the credential: the
    run's own failure. Any other status is the provider's."""
    assert runner.is_provider_failure(runner.API_ERROR.format(status=status)) is provider


def test_is_provider_failure_on_what_the_runner_did_not_write() -> None:
    assert runner.is_provider_failure(runs.QUEUE_DOWN) is True
    for message in (runs.INTERRUPTED, runs.STOPPED, runs.LOST, runs.STALE):
        assert runner.is_provider_failure(message) is False
    for message in (
        None,
        "",
        "anything",
        "The model API failed (HTTP 500)",  # not the message, a look-alike without its full stop
        "x The model API failed (HTTP 500).",
        "The model API failed (HTTP 5000).",
        "The model API failed (HTTP abc).",
        runner.NO_CREDENTIAL + " ",
    ):
        assert runner.is_provider_failure(message) is False


def test_the_api_reads_the_messages_without_the_sdk() -> None:
    """The api image holds no Claude Agent SDK. What a module that chains runs needs there
    (the messages, the verdict, the definitions and their registry) imports without it."""
    code = (
        "import sys\n"
        "from app.agents import definitions, failures, runs\n"
        "assert failures.is_provider_failure(failures.BUSY)\n"
        "assert failures.is_box_failure(failures.NO_CLI)\n"
        "assert definitions.FinalRun and definitions.register and definitions.startable\n"
        "assert 'claude_agent_sdk' not in sys.modules, 'the SDK was imported'\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert done.returncode == 0, done.stderr


def test_the_api_starts_without_the_sdk() -> None:
    """The api registers the ingestion agents and chains their runs (ingestion G10), and its
    image holds no Claude Agent SDK: the whole app imports with the SDK absent, knows the
    extraction agent by its definition and never loads a module that builds a tool."""
    code = (
        "import sys\n"
        "sys.modules['claude_agent_sdk'] = None  # as in the api image: the import fails\n"
        "import app.main\n"
        "from app.agents import definitions\n"
        "from app.ingestion import agents, engine, tool_names\n"
        "assert definitions.AGENTS[agents.EXTRACT_NAME].tools == "
        "(tool_names.READ_DOCUMENT, tool_names.PROPOSE)\n"
        "assert engine.on_final\n"
        "loaded = [name for name in ('app.ingestion.tools', 'app.agents.tools', "
        "'app.agents.runner') if name in sys.modules]\n"
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
