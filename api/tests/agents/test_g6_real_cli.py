"""The token budget through the real Claude Agent SDK and its bundled Claude Code CLI (ingestion
G6: assumption A15 and the measured overshoot), opt-in only: set JOBAPP_REAL_CLI_STUB=1. Skipped
otherwise, and never in CI (it starts the 215 MB CLI once per run).

No model is called and no credential is used: the CLI is pointed (MODEL_API_BASE_URL) at a stub
of the Messages API in this process, which answers every request with a text block and a tool
call, as a loop that would never end, and counts the requests it receives. The runner is the
product's (`runner.execute` with `SdkBackend`), on a real Postgres.

What it holds, on the pinned SDK and CLI:
- every assistant message carries the usage of its API message and its id (A15): the runner's
  own count of the input side equals what the stub reported, request for request;
- a run stopped on its budget made exactly the requests up to the one whose usage reached the
  budget, and not one more, in every one of TRIALS runs; the tool of that request did not run;
- the run ends failed with TOKEN_LIMIT and records the result message's figures, output
  included;
- an agent that names a model per kind of credential asks the API for that model;
- a run cut in the middle of a turn by a stopping worker sends no further request, because
  the turn is interrupted before the client is closed (without that interrupt, the control,
  the CLI goes on by itself and the stub receives every request the loop still held).

    cd api && JOBAPP_REAL_CLI_STUB=1 TEST_DATABASE_URL=... uv run --native-tls pytest \
        tests/agents/test_g6_real_cli.py -p no:cacheprovider -s
"""

import contextlib
import json
import os
import select
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.agents import runner
from app.agents.backend import SdkBackend
from app.agents.crypto import Keyring
from app.agents.models import RunStatus
from app.config import Settings
from tests.agents.g6_helpers import (
    ALL_TOOLS,
    MARKER,
    SLOW_STARTED,
    WRITTEN,
    agent,
    g6_tools,
    run_context,
)
from tests.agents.run_helpers import add_user, events, execute, new_run, run_row, save_credential
from tests.conftest import make_settings

__all__ = ["g6_tools"]

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBAPP_REAL_CLI_STUB") != "1",
    reason="set JOBAPP_REAL_CLI_STUB=1 to run the real Claude Code CLI against a local stub",
)

TRIALS = 10
LOOP = 8  # the stub asks for a tool this many times before it would answer
INPUT = {"input_tokens": 1000, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 300}
INPUT_SIDE = 1500
OUTPUT = 50
TOOL = "mcp__app__g6_write"


class Api:
    """What the stub was asked, in order, and how it answers."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        # The tool every answer asks for; None: the answers hold a text only.
        self.tool: str | None = TOOL
        # How long a request takes before its answer starts (a model takes seconds).
        self.delay_s = 0.0
        # The stop reason of an answer without a tool call, while the loop lasts.
        self.stop_reason = "end_turn"

    @property
    def answered(self) -> int:
        """Requests the stub answered: the client was still there when the answer was due."""
        return sum(1 for request in self.requests if request["answered"])


def handler_for(api: Api) -> type[BaseHTTPRequestHandler]:
    class Stub(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: int, body: object) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._json(404, {"type": "error", "error": {"type": "not_found_error"}})

        def do_HEAD(self) -> None:
            self.send_response(404)
            self.end_headers()

        def _gone(self) -> bool:
            """Whether the client closed the connection while the request was in flight."""
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            try:
                return self.connection.recv(1, socket.MSG_PEEK) == b""
            except OSError:
                return True

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path.split("?")[0] != "/v1/messages":
                self._json(404, {"type": "error", "error": {"type": "not_found_error"}})
                return
            answered = sum(
                1
                for message in body.get("messages") or []
                if isinstance(message.get("content"), list)
                for block in message["content"]
                if isinstance(block, dict) and block.get("type") == "tool_result"
            )
            entry = {"n": 0, "model": body.get("model"), "results": answered, "answered": False}
            with api.lock:
                index = entry["n"] = len(api.requests) + 1
                api.requests.append(entry)
            if api.delay_s:
                time.sleep(api.delay_s)
            if self._gone():
                return  # the CLI cut this request while it was in flight: never answered
            entry["answered"] = True
            blocks: list[dict[str, Any]] = [{"type": "text", "text": f"Step {answered + 1}."}]
            again = api.tool is not None and answered < LOOP
            if again:
                call = {"type": "tool_use", "id": f"toolu_{index:02d}_{uuid.uuid4().hex[:6]}"}
                blocks.append({**call, "name": api.tool, "input": {"text": f"item {index}"}})
            said = sum(1 for m in body.get("messages") or [] if m.get("role") == "assistant")
            ending = "tool_use" if again else api.stop_reason if said < LOOP else "end_turn"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def event(name: str, data: dict[str, Any]) -> None:
                self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()

            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                start = {
                    "id": f"msg_{index:02d}_{uuid.uuid4().hex[:6]}",
                    "type": "message",
                    "role": "assistant",
                    "model": body.get("model"),
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {**INPUT, "output_tokens": 1},
                }
                event("message_start", {"type": "message_start", "message": start})
                for position, block in enumerate(blocks):
                    if block["type"] == "text":
                        opened: dict[str, Any] = {"type": "text", "text": ""}
                        change = {"type": "text_delta", "text": block["text"]}
                    else:
                        opened = {**block, "input": {}}
                        change = {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(block["input"]),
                        }
                    event(
                        "content_block_start",
                        {"type": "content_block_start", "index": position, "content_block": opened},
                    )
                    event(
                        "content_block_delta",
                        {"type": "content_block_delta", "index": position, "delta": change},
                    )
                    event("content_block_stop", {"type": "content_block_stop", "index": position})
                stop = {"stop_reason": ending, "stop_sequence": None}
                event(
                    "message_delta",
                    {"type": "message_delta", "delta": stop, "usage": {"output_tokens": OUTPUT}},
                )
                event("message_stop", {"type": "message_stop"})

    return Stub


@pytest.fixture
def api() -> Iterator[tuple[Api, str]]:
    found = Api()
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(found))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield found, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path, api: tuple[Api, str]) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    worker.skip_sdk_version_probe()
    return make_settings(
        migrated_url,
        credentials_key=key,
        agents_scratch_dir=str(scratch),
        model_api_base_url=api[1],
    )


@pytest.fixture
def alice_id(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)  # a made up key of the right shape
    return user


LOOPING = agent("g6_real", tools=ALL_TOOLS, max_turns=40)


def test_usage_arrives_on_every_assistant_message_a15(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    api: tuple[Api, str],
    g6_tools: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run with a budget it never reaches: the runner's own count of the input side is what
    the stub reported for every request, and the output is counted at 1 per message (the value
    at the start of the answer) until the result message reports it in full."""
    counted: list[runner.Budget] = []
    made = runner.Budget

    def budget(limit: int, wait_s: float = runner.GATE_WAIT_S) -> runner.Budget:
        counted.append(made(limit, wait_s))
        return counted[-1]

    monkeypatch.setattr(runner, "Budget", budget)
    run_id = new_run(factory, alice_id, {"token_budget": 10**9}, agent="g6_real")
    rc = run_context(settings, factory, keyring, LOOPING, backends=SdkBackend)
    assert execute(rc, run_id) is RunStatus.done
    requests = len(api[0].requests)
    assert requests == LOOP + 1
    assert counted[0].requests == requests  # one count per API message id
    assert counted[0].input_tokens == requests * INPUT_SIDE
    assert counted[0].output_tokens == requests * 1
    row = run_row(factory, run_id)
    # The result message's figures are the ones recorded: the output in full.
    assert (row.input_tokens, row.output_tokens) == (requests * INPUT_SIDE, requests * OUTPUT)
    assert len(WRITTEN) == LOOP
    print(
        f"\nA15: {requests} requests, runner counted input {counted[0].input_tokens} "
        f"and output {counted[0].output_tokens}; result recorded "
        f"{row.input_tokens} and {row.output_tokens}"
    )


def test_no_request_leaves_after_the_one_that_reached_the_budget(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    api: tuple[Api, str],
    g6_tools: None,
) -> None:
    """The overshoot on the real CLI where the gate is asked (the model calls a tool of the
    agent), TRIALS runs in a row. A request is counted 1,501 by the runner; a budget of 4,000 is
    reached by the third. In each run the stub received three requests and never a fourth, two
    tools ran, and the run failed with TOKEN_LIMIT. The call of the third request is stopped
    twice over: the gate answers it with a stop, and the run is interrupted when the call
    arrives; whichever the CLI acts on first words the last tool result."""
    rc = run_context(settings, factory, keyring, LOOPING, backends=SdkBackend)
    seen = []
    stopped_by = []
    for _ in range(TRIALS):
        before, written = len(api[0].requests), len(WRITTEN)
        run_id = new_run(factory, alice_id, {"token_budget": 4000}, agent="g6_real")
        assert execute(rc, run_id) is RunStatus.failed
        made = len(api[0].requests) - before
        row = run_row(factory, run_id)
        seen.append((made, len(WRITTEN) - written, row.error, row.input_tokens, row.output_tokens))
        last = [data for kind, data in events(factory, run_id) if kind == "tool_result"][-1]
        assert last["is_error"] is True
        stopped_by.append("gate" if runner.BUDGET_STOP in last["content"] else "interrupt")
    print(f"\nrequests, tools run, error, input, output per run: {seen}")
    print(f"the call of the reaching request was answered by: {stopped_by}")
    # Three requests, two tools, and the result's cumulative figures (output in full).
    assert seen == [(3, 2, runner.TOKEN_LIMIT, 3 * INPUT_SIDE, 3 * OUTPUT)] * TRIALS


def slow_writes(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    if not seconds:
        return
    write = runner.Recorder._write

    def slow(self: runner.Recorder, seq: int, kind: str, data: dict[str, Any]) -> None:
        time.sleep(seconds)
        write(self, seq, kind, data)

    monkeypatch.setattr(runner.Recorder, "_write", slow)


# The cases of the review of PR #95: the tool the model calls, seconds a request takes, seconds
# added to every event write, the gate's wait. Budget 4,000, reached by the third request of a
# loop of nine. As first built the runs marked "all nine" made every request of the loop.
REVIEW_CASES = {
    "own tool, instant requests": (TOOL, 0.0, 0.0, 5.0),
    "own tool, requests 0.3 s, writes 0.4 s, gate wait 0.2 s": (TOOL, 0.3, 0.4, 0.2),
    "Bash, requests 1 s": ("Bash", 1.0, 0.0, 5.0),
    "invented tool, requests 1 s": ("mcp__app__made_up", 1.0, 0.0, 5.0),
    "Bash, requests 0.3 s, writes 0.4 s (all nine)": ("Bash", 0.3, 0.4, 5.0),
    "Bash, instant requests (all nine)": ("Bash", 0.0, 0.0, 5.0),
    "invented tool, instant requests (all nine)": ("mcp__app__made_up", 0.0, 0.0, 5.0),
}
REPEATS = 5


@pytest.mark.parametrize("case", list(REVIEW_CASES))
def test_the_cases_of_the_review(
    case: str,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    api: tuple[Api, str],
    g6_tools: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a run does past its budget on the real CLI, whatever the model calls and however
    slow the event writes are, REPEATS runs per case. A tool of the agent: three requests, the
    reaching one is the last. A tool the CLI does not offer (a built-in name, an invented one):
    the CLI answers the call itself and would send its fourth request, which only the interrupt
    holds back. Measured, the interrupt came first in every run (three requests); nothing
    orders the two, so the test allows the fourth, cut before any answer when a request takes
    time, and never a fifth."""
    tool, request_s, write_s, gate_wait_s = REVIEW_CASES[case]
    api[0].tool = tool
    api[0].delay_s = request_s
    slow_writes(monkeypatch, write_s)
    rc = run_context(
        settings, factory, keyring, LOOPING, backends=SdkBackend, gate_wait_s=gate_wait_s
    )
    seen = []
    for _ in range(REPEATS):
        before = len(api[0].requests)
        answered = api[0].answered
        run_id = new_run(factory, alice_id, {"token_budget": 4000}, agent="g6_real")
        status = execute(rc, run_id)
        time.sleep(0.3)  # a request sent just before the end would have arrived by now
        row = run_row(factory, run_id)
        seen.append(
            (
                len(api[0].requests) - before,
                api[0].answered - answered,
                status.value if status else None,
                row.error,
                row.input_tokens,
                row.output_tokens,
            )
        )
    print(f"\n{case}: requests sent, answered, status, error, input, output: {seen}")
    for sent, answered_now, status_now, error, tokens_in, tokens_out in seen:
        assert (status_now, error) == ("failed", runner.TOKEN_LIMIT)
        if tool == TOOL:
            assert (sent, answered_now) == (3, 3)
            assert (tokens_in, tokens_out) == (3 * INPUT_SIDE, 3 * OUTPUT)
        else:
            assert sent <= 4  # the reaching request, and at most one more
            if request_s:
                assert answered_now == 3  # which was cut before any answer
                assert (tokens_in, tokens_out) == (3 * INPUT_SIDE, 3 * OUTPUT)
        # The run records what the answered requests used, whatever cut it.
        assert tokens_in == answered_now * INPUT_SIDE


@pytest.mark.parametrize("request_s", [0.0, 0.3], ids=["instant requests", "requests 0.3 s"])
def test_a_turn_that_goes_on_with_no_tool_call_passes_its_budget_by_two_requests(
    request_s: float,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    api: tuple[Api, str],
    g6_tools: None,
) -> None:
    """The pinned CLI does go on by itself: after an answer the API ended with `max_tokens` it
    sends a new request, with no tool call in between, up to four requests in a row (the
    first run below has no budget and counts them). Nothing in the message that reached the
    budget says so, and no gate is asked. The runner sees the turn go on when the next
    request's first message arrives, and interrupts there. With a budget the first request
    reaches: that request and one more, the second, which had its answer; a third may have
    left, and is cut when a request takes time."""
    api[0].tool = None
    api[0].stop_reason = "max_tokens"
    api[0].delay_s = request_s
    rc = run_context(settings, factory, keyring, LOOPING, backends=SdkBackend)
    if not request_s:
        free = new_run(factory, alice_id, {}, agent="g6_real")
        execute(rc, free)
        made = len(api[0].requests)
        print(f"\nwithout a budget, answers ended with max_tokens: {made} requests by itself")
        assert made == 4  # the CLI went on with no tool call, three times
    seen = []
    for _ in range(REPEATS):
        before, answered = len(api[0].requests), api[0].answered
        run_id = new_run(factory, alice_id, {"token_budget": 1500}, agent="g6_real")
        status = execute(rc, run_id)
        time.sleep(0.3)
        row = run_row(factory, run_id)
        seen.append(
            (
                len(api[0].requests) - before,
                api[0].answered - answered,
                status.value if status else None,
                row.error,
                row.input_tokens,
                row.output_tokens,
            )
        )
    print(f"\nafter max_tokens: requests sent, answered, status, error, input, output: {seen}")
    for sent, answered_now, status_now, error, tokens_in, _ in seen:
        assert (status_now, error) == ("failed", runner.TOKEN_LIMIT)
        # The reaching request (the first) and one more; with instant requests the one after
        # may be answered before the interrupt lands. Never the four the CLI makes by itself.
        assert 2 <= answered_now <= (2 if request_s else 3)
        assert sent <= 3
        assert tokens_in == answered_now * INPUT_SIDE


@pytest.mark.parametrize("interrupted", [True, False], ids=["as built", "the control"])
def test_a_worker_stop_in_the_middle_of_a_turn_sends_no_further_request(
    interrupted: bool,
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    api: tuple[Api, str],
    g6_tools: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first request asked for a tool that is still running when the worker stops. As
    built, the turn is interrupted before the client is closed, and the stub never hears from
    the CLI again. The control takes the interrupt away: the CLI, its input closed, answers
    the tool call itself with an error and goes on, request after request, to the loop's end."""
    import asyncio

    from claude_agent_sdk import ClaudeSDKClient

    from app.agents import runs

    if not interrupted:

        async def no_interrupt(self: Any) -> None:
            raise RuntimeError("the interrupt is taken away for the control")

        monkeypatch.setattr(ClaudeSDKClient, "interrupt", no_interrupt)
    api[0].tool = "mcp__app__g6_slow"
    run_id = new_run(factory, alice_id, {"token_budget": 10**9}, agent="g6_real")
    rc = run_context(settings, factory, keyring, LOOPING, backends=SdkBackend)

    async def go() -> int:
        task = asyncio.create_task(runner.execute(rc, run_id))
        while not SLOW_STARTED.is_set():  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        before = len(api[0].requests)
        task.cancel()  # the worker stops
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(1)  # the CLI process is gone by now: nothing more can come
        return len(api[0].requests) - before

    after = asyncio.run(go())
    row = run_row(factory, run_id)
    assert (row.status, row.error) == (RunStatus.failed, runs.STOPPED)
    # What the run had used when it was cut is on the run: one request, counted by the runner.
    assert (row.input_tokens, row.output_tokens) == (INPUT_SIDE, 1)
    which = "as built" if interrupted else "the control"
    print(f"\nrequests after the worker stopped ({which}): {after}")
    if interrupted:
        assert after == 0
    else:
        assert after == LOOP  # every request the loop still held


def test_a_budget_of_zero_sends_nothing(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    api: tuple[Api, str],
    g6_tools: None,
) -> None:
    run_id = new_run(factory, alice_id, {"token_budget": 0}, agent="g6_real")
    rc = run_context(settings, factory, keyring, LOOPING, backends=SdkBackend)
    assert execute(rc, run_id) is RunStatus.failed
    assert run_row(factory, run_id).error == runner.TOKEN_LIMIT
    assert api[0].requests == []


def test_a_private_run_with_a_model_per_credential_through_the_real_cli(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    alice_id: uuid.UUID,
    api: tuple[Api, str],
    g6_tools: None,
) -> None:
    """The API is asked for the model of the credential's kind, and the tool events of a
    private run hold names and sizes through the real CLI too."""
    step = agent(
        "g6_real",
        tools=ALL_TOOLS,
        private_io=True,
        bind=lambda db, run: {"text": f"the part with {MARKER}"},
        model_by_credential={"anthropic_api_key": "claude-sonnet-5-5"},
    )
    run_id = new_run(factory, alice_id, {"token_budget": 2000}, agent="g6_real")
    rc = run_context(settings, factory, keyring, step, backends=SdkBackend)
    assert execute(rc, run_id) is RunStatus.failed
    assert {request["model"] for request in api[0].requests} == {"claude-sonnet-5-5"}
    assert len(api[0].requests) == 2
    row = run_row(factory, run_id)
    assert (row.model, row.error) == ("claude-sonnet-5-5", runner.TOKEN_LIMIT)
    evs = events(factory, run_id)
    calls = [data for kind, data in evs if kind == "tool_call"]
    assert all(set(data) == {"id", "name", "input_size"} for data in calls)
    assert [data["name"] for data in calls] == [TOOL, TOOL]
    results = [data for kind, data in evs if kind == "tool_result"]
    assert all(set(data) == {"id", "is_error", "size", "blocks"} for data in results)
    assert row.output is not None
    assert all(type(value) is int for value in row.output.values())
