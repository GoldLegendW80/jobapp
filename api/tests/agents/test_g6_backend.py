"""The real backend closing a turn that is still going on (ingestion G6, found while measuring
the overshoot): closing the SDK client alone lets the CLI go on for seconds, a request per
failed tool; the turn is interrupted first. Here with a stand-in for `ClaudeSDKClient` that
records what it is asked, in order; the real CLI is in test_g6_real_cli.py (opt-in) and in
tasks/evidence/ingestion-g6/."""

import asyncio
import contextlib
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, CLIConnectionError, ResultMessage, TextBlock

from app.agents import backend
from app.agents.backend import BackendRequest, SdkBackend

SAID = AssistantMessage(content=[TextBlock(text="one")], model="m")
RESULT = ResultMessage(
    subtype="success",
    duration_ms=1,
    duration_api_ms=1,
    is_error=False,
    num_turns=1,
    session_id="s",
    result="one",
)


class Client:
    """What the backend asks of the SDK client, in order."""

    calls: list[str]
    messages: list[Any]
    connect_error: BaseException | None = None
    interrupt_error: BaseException | None = None
    interrupt_hangs = False

    def __init__(self, options: Any = None) -> None:
        self.options = options

    async def connect(self) -> None:
        self.calls.append("connect")
        if self.connect_error is not None:
            raise self.connect_error

    async def query(self, prompt: str) -> None:
        self.calls.append("query")

    async def receive_response(self) -> Any:
        for message in self.messages:
            if isinstance(message, BaseException):
                raise message
            yield message
        await asyncio.sleep(3600)  # the turn goes on

    async def interrupt(self) -> None:
        self.calls.append("interrupt")
        if self.interrupt_hangs:
            await asyncio.sleep(3600)
        if self.interrupt_error is not None:
            raise self.interrupt_error

    async def disconnect(self) -> None:
        self.calls.append("disconnect")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> type[Client]:
    class Fresh(Client):
        calls: list[str] = []  # noqa: RUF012, one list per test
        messages: list[Any] = [SAID, RESULT]  # noqa: RUF012

    monkeypatch.setattr(backend, "ClaudeSDKClient", Fresh)
    return Fresh


REQUEST = BackendRequest(prompt="x", options=None, tools=None, gate=None)  # type: ignore[arg-type]


def test_a_stream_read_to_its_result_is_closed_without_an_interrupt(client: type[Client]) -> None:
    async def go() -> list[Any]:
        found = []
        async for message in SdkBackend().stream(REQUEST):
            found.append(message)
            if isinstance(message, ResultMessage):
                break
        return found

    assert asyncio.run(go()) == [SAID, RESULT]
    assert client.calls == ["connect", "query", "disconnect"]


def test_a_stream_closed_before_its_result_is_interrupted_first(client: type[Client]) -> None:
    client.messages = [SAID]

    async def go() -> None:
        stream = SdkBackend().stream(REQUEST)
        async with contextlib.aclosing(stream) as messages:  # type: ignore[type-var]
            async for _ in messages:
                break  # the runner stops reading: the turn is still going on

    asyncio.run(go())
    assert client.calls == ["connect", "query", "interrupt", "disconnect"]


def test_a_reader_cancelled_in_the_middle_of_a_turn(client: type[Client]) -> None:
    """The worker stopping: the task that reads the stream is cancelled while it waits for
    the next message. The turn is interrupted, then the client closed, before the task ends."""
    client.messages = [SAID]
    made = SdkBackend()

    async def read() -> None:
        async with contextlib.aclosing(made.stream(REQUEST)) as messages:  # type: ignore[type-var]
            async for _ in messages:
                pass

    async def go() -> None:
        task = asyncio.create_task(read())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert client.calls == ["connect", "query", "interrupt", "disconnect"]
    assert made._client is None


def test_claude_code_that_never_started_is_closed_all_the_same(client: type[Client]) -> None:
    client.connect_error = CLIConnectionError("no process")
    client.interrupt_error = CLIConnectionError("Not connected")

    async def go() -> None:
        async for _ in SdkBackend().stream(REQUEST):
            pass  # pragma: no cover

    with pytest.raises(CLIConnectionError, match="no process"):
        asyncio.run(go())  # the failure of the start is what the runner sees
    assert client.calls == ["connect", "interrupt", "disconnect"]


def test_a_stream_that_raises_is_closed_all_the_same(client: type[Client]) -> None:
    client.messages = [SAID, RuntimeError("the process died")]
    client.interrupt_error = CLIConnectionError("gone")

    async def go() -> None:
        async for _ in SdkBackend().stream(REQUEST):
            pass

    with pytest.raises(RuntimeError, match="the process died"):
        asyncio.run(go())
    assert client.calls == ["connect", "query", "interrupt", "disconnect"]


def test_an_interrupt_that_never_answers_does_not_keep_the_client_open(
    client: type[Client], monkeypatch: pytest.MonkeyPatch
) -> None:
    client.messages = [SAID]
    client.interrupt_hangs = True
    monkeypatch.setattr(backend, "INTERRUPT_BEFORE_CLOSE_S", 0.1)

    async def go() -> None:
        async with contextlib.aclosing(SdkBackend().stream(REQUEST)) as messages:  # type: ignore[type-var]
            async for _ in messages:
                break

    asyncio.run(asyncio.wait_for(go(), 5))
    assert client.calls == ["connect", "query", "interrupt", "disconnect"]
    assert backend.INTERRUPT_BEFORE_CLOSE_S == 0.1


def test_cancelled_again_during_the_interrupt_the_client_is_still_closed(
    client: type[Client],
) -> None:
    client.messages = [SAID]
    client.interrupt_hangs = True

    async def read() -> None:
        async with contextlib.aclosing(SdkBackend().stream(REQUEST)) as messages:  # type: ignore[type-var]
            async for _ in messages:
                pass

    async def go() -> None:
        task = asyncio.create_task(read())
        await asyncio.sleep(0.05)
        task.cancel()  # the worker stops
        await asyncio.sleep(0.05)
        assert client.calls[-1] == "interrupt"
        task.cancel()  # and is told to stop again while the interrupt waits
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert client.calls == ["connect", "query", "interrupt", "disconnect"]


def test_the_runners_own_interrupt_reaches_the_client_only_while_it_streams(
    client: type[Client],
) -> None:
    client.messages = [SAID]

    async def go() -> None:
        made = SdkBackend()
        await made.interrupt()  # nothing is running: nothing to ask
        assert client.calls == []
        stream = made.stream(REQUEST)
        await stream.__anext__()
        await made.interrupt()
        assert client.calls == ["connect", "query", "interrupt"]
        await stream.aclose()

    asyncio.run(go())
    assert client.calls == ["connect", "query", "interrupt", "interrupt", "disconnect"]
