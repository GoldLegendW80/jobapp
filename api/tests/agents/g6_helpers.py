"""Helpers of the tests of ingestion task G6 (runner changes for bound, private, internal
steps): test only agents and tools, registered the way another module registers its own
(`definitions.register`, `tools.register`), and recorded streams written by the tests into a
copy of the fake backend's folder. No agent of the product is touched."""

import asyncio
import json
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import SdkMcpTool, tool
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions, fake_backend
from app.agents import tools as app_tools
from app.agents.crypto import Keyring
from app.agents.runner import RunContext
from app.config import Settings
from tests.agents.run_helpers import Backends, now

# What stands for a private document in these tests: it must never be found anywhere but in the
# `text` events of the run that read it (the model's own words) and in the tests themselves.
MARKER = "PRIVATE-DOCUMENT-MARKER-7f3a"
USAGE = {
    "input_tokens": 700,
    "output_tokens": 40,
    "cache_creation_input_tokens": 100,
    "cache_read_input_tokens": 100,
}
PER_REQUEST = 940  # what one request of USAGE costs


class G6Input(BaseModel):
    model_config = ConfigDict(extra="allow")


def agent(name: str = "g6_agent", **changes: Any) -> definitions.AgentDefinition:
    """A test agent as a module of its own would define it. Nothing private unless asked."""
    fields: dict[str, Any] = {
        "name": name,
        "description": "Test agent of task G6.",
        "system_prompt": "Test agent. Call your tools.",
        "tools": ("g6_read", "g6_write"),
        "input_model": G6Input,
        "max_turns": 40,
        "effort": "low",
        "default_message": "Go.",
    }
    fields.update(changes)
    return definitions.AgentDefinition(**fields)


# --- tools ----------------------------------------------------------------------------------------

# What the write tool was given, with the scope it ran in: (user id, binding, arguments).
WRITTEN: list[tuple[uuid.UUID, Any, dict[str, Any]]] = []
# Set when the slow tool was entered (a threading event: it is read from other event loops).
SLOW_STARTED = threading.Event()

NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
TEXT_ARG: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def _read(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool("g6_read", "Read what this run is bound to. Takes no id.", NO_ARGS)
    async def g6_read(args: dict[str, Any]) -> dict[str, Any]:
        bound = scope.binding
        if bound is None:
            return app_tools.text_result("Nothing is bound to this run.", error=True)
        return app_tools.text_result(json.dumps({"untrusted_text": bound.get("text")}))

    return g6_read


def _write(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool("g6_write", "Store a text for what this run is bound to. Takes no id.", TEXT_ARG)
    async def g6_write(args: dict[str, Any]) -> dict[str, Any]:
        WRITTEN.append((scope.user_id, scope.binding, dict(args)))
        return app_tools.text_result("stored")

    return g6_write


def _fail(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool("g6_fail", "Always fails, and says what it was given.", TEXT_ARG)
    async def g6_fail(args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"cannot store {args.get('text')}")

    return g6_fail


def _slow(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool("g6_slow", "Never answers: a tool call in flight.", TEXT_ARG)
    async def g6_slow(args: dict[str, Any]) -> dict[str, Any]:
        SLOW_STARTED.set()
        await asyncio.sleep(3600)
        return app_tools.text_result("never")  # pragma: no cover

    return g6_slow


def _image(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool("g6_image", "Returns a text and an image block.", NO_ARGS)
    async def g6_image(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {"type": "text", "text": "page 1"},
                {"type": "image", "data": "aGVsbG8=", "mimeType": "image/jpeg"},
            ]
        }

    return g6_image


TOOLS = {
    "g6_read": _read,
    "g6_write": _write,
    "g6_fail": _fail,
    "g6_slow": _slow,
    "g6_image": _image,
}
ALL_TOOLS = tuple(TOOLS)


@pytest.fixture
def g6_tools() -> Iterator[None]:
    """The test tools, registered as another module registers its own, and taken out again."""
    WRITTEN.clear()
    SLOW_STARTED.clear()
    for name, factory in TOOLS.items():
        app_tools.register(name, factory)
    yield
    for name in TOOLS:
        app_tools.FACTORIES.pop(name, None)
    WRITTEN.clear()


# --- recorded streams -----------------------------------------------------------------------------


def init() -> dict[str, Any]:
    return {
        "message": {
            "type": "system",
            "subtype": "init",
            "session_id": "{session_id}",
            "uuid": str(uuid.uuid4()),
            "model": "{model}",
            "tools": [],
        }
    }


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def call(call_id: str, name: str, **args: Any) -> dict[str, Any]:
    """A tool call. A bare name is one of the app's tools; a name that already says where it
    comes from (`mcp__...`) or starts with a capital (Bash) is taken as it is."""
    full = name if name.startswith("mcp__") or name[:1].isupper() else f"mcp__app__{name}"
    return {"type": "tool_use", "id": call_id, "name": full, "input": args}


def raw_call(call_id: str, name: str, **args: Any) -> dict[str, Any]:
    """A tool call under exactly this name, whatever it is (a name the model made up)."""
    return {"type": "tool_use", "id": call_id, "name": name, "input": args}


def assistant(
    message_id: str | None, *blocks: dict[str, Any], usage: dict[str, int] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "message",
        "role": "assistant",
        "model": "{model}",
        "content": list(blocks),
        "stop_reason": None,
        "usage": dict(USAGE if usage is None else usage),
    }
    if message_id is not None:
        body["id"] = message_id
    return {
        "message": {
            "type": "assistant",
            "session_id": "{session_id}",
            "uuid": str(uuid.uuid4()),
            "parent_tool_use_id": None,
            "message": body,
        }
    }


def result(answer: str, turns: int = 1, **usage: int) -> dict[str, Any]:
    return {
        "message": {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 10,
            "duration_api_ms": 5,
            "num_turns": turns,
            "session_id": "{session_id}",
            "result": answer,
            "total_cost_usd": 0.02,
            "usage": usage or {"input_tokens": 0, "output_tokens": 0},
            "terminal_reason": "completed",
        }
    }


def loop(turns: int, tool_name: str = "g6_write", **args: Any) -> list[dict[str, Any]]:
    """`turns` requests that each call a tool, then a last one that answers: turns + 1 requests
    of PER_REQUEST tokens each, and no result message of its own (the fake backend ends the
    replay with one that reports no usage)."""
    steps = [init()]
    for index in range(1, turns + 1):
        steps.append(
            assistant(
                f"msg_{index:02d}", call(f"toolu_{index:02d}", tool_name, **(args or {"text": "x"}))
            )
        )
    steps.append(assistant(f"msg_{turns + 1:02d}", text("Finished.")))
    return steps


@pytest.fixture
def streams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "Streams":
    """A copy of the fake backend's recorded streams that the test adds its own to."""
    folder = tmp_path / "streams"
    folder.mkdir()
    for path in fake_backend.STREAMS.glob("*.json"):
        (folder / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(fake_backend, "STREAMS", folder)
    return Streams(folder)


class Streams:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def add(self, name: str, steps: list[dict[str, Any]], **options: Any) -> str:
        body = {"description": "Written by a test of task G6.", "steps": steps, **options}
        (self.folder / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
        return name


# --- the runner -----------------------------------------------------------------------------------


def run_context(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring | None,
    *agents: definitions.AgentDefinition,
    backends: Any = None,
    **changes: Any,
) -> RunContext:
    """A runner context on the fake backend that knows `agents` (as RunContext.agents: what a
    test replaces, the worker's registry untouched)."""
    values: dict[str, Any] = {
        "settings": settings,
        "sessions": factory,
        "clock": now,
        "keyring": keyring,
        "backend_factory": backends or Backends(),
        "cancel_poll_s": 0.02,
        "interrupt_grace_s": 1.0,
        "agents": {found.name: found for found in agents},
    }
    values.update(changes)
    return RunContext(**values)


def everything_stored(factory: sessionmaker[Session], run_id: uuid.UUID) -> str:
    """The run's row and every one of its events, as text: what a marker must not be in."""
    import sqlalchemy as sa

    with factory() as db:
        row = db.execute(
            sa.text("SELECT to_jsonb(r)::text FROM agent_runs r WHERE id = :id"), {"id": run_id}
        ).scalar_one()
        events = db.execute(
            sa.text("SELECT to_jsonb(e)::text FROM agent_events e WHERE run_id = :id ORDER BY seq"),
            {"id": run_id},
        ).scalars()
        return row + "\n" + "\n".join(events)
