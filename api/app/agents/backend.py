"""The seam between a run and the model: `SdkBackend` drives the real Claude Agent SDK
(`ClaudeSDKClient`, which spawns the bundled Claude Code CLI), `FakeBackend`
(app/agents/fake_backend.py) replays recorded message streams for tests and scratch stacks.
Both yield the SDK's own message objects (AssistantMessage, UserMessage, SystemMessage,
ResultMessage, ...), so the runner handles one shape. Worker only (imports the SDK).

Which one runs is decided by `for_settings`: the fake only when AGENTS_FAKE_BACKEND is set,
which the settings refuse in production, and this function refuses again when APP_ENV is
production, so no configuration mistake can put recorded answers in front of a real user.
"""

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from app.agents.tools import AppTools
from app.config import Settings

Gate = Callable[[dict[str, Any], str | None, Any], Awaitable[dict[str, Any]]]


class FakeInProductionError(RuntimeError):
    """The fake backend was asked for in production."""


@dataclass
class BackendRequest:
    prompt: str
    options: ClaudeAgentOptions = field(repr=False)  # holds the credential in options.env
    tools: AppTools = field(repr=False)
    gate: Gate = field(repr=False)
    # Fake backend only: the recorded stream to replay and the values put into it.
    script: str | None = None
    variables: dict[str, str] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:  # never print the prompt or the options
        return f"BackendRequest(script={self.script!r})"


class Backend(Protocol):
    def stream(self, request: BackendRequest) -> AsyncIterator[Any]:
        """The run's messages, ending after the ResultMessage."""
        ...

    async def interrupt(self) -> None:
        """Ask the running turn to stop; the stream then ends soon after."""
        ...


class SdkBackend:
    """The real model, through the Claude Agent SDK. One instance per run."""

    def __init__(self) -> None:
        self._client: ClaudeSDKClient | None = None

    async def stream(self, request: BackendRequest) -> AsyncIterator[Any]:
        client = ClaudeSDKClient(options=request.options)
        self._client = client
        try:
            await client.connect()
            await client.query(request.prompt)
            # receive_response() ends after the ResultMessage (SDK docs). Never `break` out of
            # it: the SDK warns that breaking early can leave its tasks half cleaned up.
            async for message in client.receive_response():
                yield message
        finally:
            self._client = None
            # disconnect() terminates the CLI process (SubprocessCLITransport.close).
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def interrupt(self) -> None:
        client = self._client
        if client is not None:
            await client.interrupt()


BackendFactory = Callable[[], Backend]


def for_settings(settings: Settings) -> BackendFactory:
    if not settings.agents_fake_backend:
        return SdkBackend
    if settings.app_env == "production":
        raise FakeInProductionError("the fake agent backend is refused in production")
    from app.agents.fake_backend import FakeBackend

    return FakeBackend
