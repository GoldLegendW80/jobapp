"""A stand-in for Claude Code that replays recorded message streams: CI and scratch stacks run
agents end to end without a credential, a model call or a cent spent (SPEC-agents.md, Testing).
Never used in production (app/agents/backend.py `for_settings`).

A stream (app/agents/fake_streams/<name>.json) is a list of steps:
- {"sleep": seconds}: wait (scaled by `speed`), interruptible;
- {"message": {...}}: one message exactly as the Claude Code CLI prints it in stream-json mode,
  turned into the SDK's message object by the SDK's own parser (`parse_message`), so the runner
  sees what the real SDK would give it;
- {"raise": "process" | "cli_not_found" | "connection"}: fail like the SDK does when the CLI
  dies, is missing or cannot start.

The messages follow the documented types (Python reference, Message types): `system` (subtype
`init`), `assistant` (content blocks text, thinking, tool_use; usage), `user` (tool_result
blocks), `result` (subtype, is_error, num_turns, duration_ms, total_cost_usd, usage). Strings may
hold {placeholders} (`variables`: model, message, job_id, session_id), filled in before parsing.

What Claude Code itself does between messages is simulated: for every tool_use block of an
assistant message, the PreToolUse gate runs first (the runner's own hook, as the CLI calls it),
then the in-process app tool, and the tool_result comes back as a user message. A tool that is
neither allowed by the gate nor an app tool is answered with an error result, as Claude Code
answers a call to a tool it does not offer. Each assistant message with tool calls is one turn;
past `options.max_turns` the replay ends with an `error_max_turns` result, as the CLI does.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError

# The SDK's parser of the CLI's stream-json lines: private in 0.2.163 but the very code the real
# backend runs, so the replay cannot drift from what the SDK produces.
from claude_agent_sdk._internal.message_parser import parse_message

from app.agents.backend import BackendRequest

STREAMS = Path(__file__).parent / "fake_streams"
NAME_PATTERN = r"^[a-z][a-z0-9_]{0,40}$"
DEFAULT_SCRIPT = "echo"


class UnknownScriptError(LookupError):
    pass


def load_script(name: str) -> list[dict[str, Any]]:
    path = STREAMS / f"{name}.json"
    if not name.replace("_", "").isalnum() or not path.is_file():
        raise UnknownScriptError(name)
    steps: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["steps"]
    return steps


def scripts() -> list[str]:
    return sorted(path.stem for path in STREAMS.glob("*.json"))


def _fill(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        for key, replacement in variables.items():
            value = value.replace("{" + key + "}", replacement)
        return value
    if isinstance(value, list):
        return [_fill(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _fill(item, variables) for key, item in value.items()}
    return value


class FakeBackend:
    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed
        self._interrupted = asyncio.Event()
        # What the runner handed over, kept for the tests' assertions (never logged).
        self.seen_env: dict[str, str] | None = None
        self.seen_cwd: str | None = None
        self.seen_prompt: str | None = None
        self.seen_options: Any = None

    async def interrupt(self) -> None:
        self._interrupted.set()

    async def _sleep(self, seconds: float) -> bool:
        """Wait; True when interrupted meanwhile."""
        try:
            await asyncio.wait_for(self._interrupted.wait(), timeout=max(0.0, seconds * self.speed))
        except TimeoutError:
            return False
        return True

    async def stream(self, request: BackendRequest) -> AsyncIterator[Any]:
        options = request.options
        self.seen_env = dict(options.env)
        self.seen_cwd = str(options.cwd) if options.cwd is not None else None
        self.seen_prompt = request.prompt
        self.seen_options = options
        variables = {
            "model": options.model or "",
            "session_id": str(uuid.uuid4()),
            **request.variables,
        }
        steps = load_script(request.script or DEFAULT_SCRIPT)
        started = time.monotonic()
        turns = 0
        for step in steps:
            if self._interrupted.is_set():
                yield self._result(variables, started, turns, "error_during_execution", True)
                return
            if "sleep" in step:
                if await self._sleep(float(step["sleep"])):
                    yield self._result(variables, started, turns, "error_during_execution", True)
                    return
                continue
            if "raise" in step:
                kind = step["raise"]
                if kind == "cli_not_found":
                    raise CLINotFoundError("Claude Code not found", cli_path="/nowhere/claude")
                if kind == "connection":
                    raise CLIConnectionError("Failed to start Claude Code")
                raise ProcessError("Command failed", exit_code=1, stderr="fake failure")
            raw = _fill(step["message"], variables)
            message = parse_message(raw)
            if message is None:
                continue
            yield message
            if raw.get("type") == "result":
                return
            calls = [
                block
                for block in raw.get("message", {}).get("content", [])
                if raw.get("type") == "assistant" and block.get("type") == "tool_use"
            ]
            if not calls:
                continue
            turns += 1
            if options.max_turns is not None and turns > options.max_turns:
                yield self._result(variables, started, turns, "error_max_turns", True)
                return
            results = [await self._call(request, block) for block in calls]
            yield parse_message(
                {
                    "type": "user",
                    "message": {"role": "user", "content": results},
                    "session_id": variables["session_id"],
                    "uuid": str(uuid.uuid4()),
                }
            )
        yield self._result(variables, started, turns, "success", False)

    async def _call(self, request: BackendRequest, block: dict[str, Any]) -> dict[str, Any]:
        name, args, call_id = block["name"], block.get("input") or {}, block["id"]
        verdict = await request.gate(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "fake",
                "transcript_path": "",
                "cwd": str(request.options.cwd or ""),
                "tool_name": name,
                "tool_input": args,
                "tool_use_id": call_id,
            },
            call_id,
            {"signal": None},
        )
        decision = verdict.get("hookSpecificOutput", {})
        if decision.get("permissionDecision") == "deny":
            text = str(decision.get("permissionDecisionReason", "Denied by a hook."))
            return {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": text,
                "is_error": True,
            }
        handler = request.tools.by_name.get(name)
        if handler is None:
            text = f"Error: No such tool available: {name}"
            return {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": text,
                "is_error": True,
            }
        try:
            result = await handler.handler(args)
        except Exception as exc:  # the SDK's MCP server reports a raising handler as an error
            return {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": str(exc),
                "is_error": True,
            }
        return {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": result.get("content", []),
            "is_error": bool(result.get("is_error", False)),
        }

    def _result(
        self,
        variables: dict[str, str],
        started: float,
        turns: int,
        subtype: str,
        is_error: bool,
    ) -> Any:
        return parse_message(
            {
                "type": "result",
                "subtype": subtype,
                "is_error": is_error,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "duration_api_ms": 0,
                "num_turns": turns + 1,
                "session_id": variables["session_id"],
                "total_cost_usd": 0.0,
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "terminal_reason": "aborted_streaming"
                if subtype == "error_during_execution"
                else ("max_turns" if subtype == "error_max_turns" else "completed"),
            }
        )
