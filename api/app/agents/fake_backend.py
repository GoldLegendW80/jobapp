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

A gate that answers a tool call with a stop (`continue_: False`, how the runner ends a run whose
token budget is used up, ingestion G6) ends the replay the way the pinned CLI ends the turn
(recorded against a stub API, tasks/evidence/ingestion-g6/): the tool does not run, its result
is the error "PreToolUse:<tool> hook error: <reason>", and the result message says `success`
with the terminal reason `hook_stopped` and an empty result. No further step is replayed: no
further request leaves. `seen_requests` lists the ids of the API messages replayed, one per
request the real CLI would have made.

Where this replay is not Claude Code (review of PR #95). A test written on it proves nothing
about these three points; `tests/agents/push_backend.py` behaves like the CLI on the first two,
and `tests/agents/test_g6_real_cli.py` (opt-in) runs the CLI itself.
1. **It is pulled, the CLI pushes.** A step is replayed when the runner asks for the next
   message, and a request takes no time unless the stream says so (`sleep`). The real CLI makes
   its next request as soon as it can, whether or not the runner has dealt with the last
   message: it can be several requests ahead of a runner that writes slowly.
2. **By default the gate is asked about every tool name.** The real CLI asks its hooks only
   about the tools it offers, which with `tools=[]` are the run's own app tools. A call to any
   other name, a built-in one like Bash or one the model made up, it answers by itself
   ("No such tool available"): the hook never hears of it, so the run stores no `tool_denied`
   event for it and no gate can stop on it. The default here replays something else, a tool
   that exists and is not allowed (the gate's defence in depth, which the product's recorded
   streams and their tests rely on). A stream that says `"gate": "offered"` at its top level is
   replayed as the CLI behaves: the gate is asked about the run's own tools only, and any other
   call is answered at once with the CLI's error.
3. **A result the replay makes up reports no usage** (interrupt, turn limit, hook stop, a
   stream without its own result step). The real CLI's result is cumulative over the run.

Partial messages (issue #33; facts and the recording in tasks/agents/todo.md). When the options
ask for them (`include_partial_messages`), the replay also yields the `stream_event` messages
the CLI prints, in the order recorded from the pinned CLI (2.1.286) against a stub API:

    stream_event message_start
    per content block:  stream_event content_block_start
                        stream_event content_block_delta ...  (text_delta, thinking_delta then
                                                               signature_delta, input_json_delta)
                        assistant message holding that one block
                        stream_event content_block_stop
    user message with the tool results (when the message called tools)
    stream_event message_delta, stream_event message_stop

A stream that holds `stream_event` steps itself (`partial_recorded`, the recording) is replayed
as it is. For every other stream they are built from each assistant message: its blocks one by
one, the text cut into pieces of `partial.chars` characters (cut anywhere, also inside a
grapheme) `partial.sleep` seconds apart (top level `"partial"` of the stream, defaults
PARTIAL_CHARS and PARTIAL_SLEEP_S). An interrupt in the middle of a text block ends it the way
the pinned CLI does: an assistant message with the text so far, no stop events, then the
`error_during_execution` result. Without the option no `stream_event` is ever yielded and each
assistant message arrives whole, as before.
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
PARTIAL_CHARS = 24
PARTIAL_SLEEP_S = 0.02
# How the pinned CLI answers, by itself, a call to a tool it does not offer.
NO_SUCH_TOOL = "<tool_use_error>Error: No such tool available: {name}</tool_use_error>"


class UnknownScriptError(LookupError):
    pass


def load(name: str) -> dict[str, Any]:
    """The whole stream file: its steps and how its partial messages are paced."""
    path = STREAMS / f"{name}.json"
    if not name.replace("_", "").isalnum() or not path.is_file():
        raise UnknownScriptError(name)
    script: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return script


def load_script(name: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = load(name)["steps"]
    return steps


def _is_stream_event(step: dict[str, Any]) -> bool:
    return bool(step.get("message", {}).get("type") == "stream_event")


def pieces(text: str, size: int) -> list[str]:
    """`text` cut every `size` characters (code points: a cut may fall inside a grapheme)."""
    size = max(1, size)
    return [text[i : i + size] for i in range(0, len(text), size)]


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
        # The API messages replayed, by id: one per request Claude Code would have made.
        self.seen_requests: list[str] = []
        self._hook_stopped = False
        # Whether the gate is asked about every tool name (the default) or, as the CLI does,
        # about the run's own tools only (`"gate": "offered"` in the stream).
        self._gate_all = True

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
        script = load(request.script or DEFAULT_SCRIPT)
        steps: list[dict[str, Any]] = script["steps"]
        asked = script.get("gate", "all")
        if asked not in ("all", "offered"):
            raise ValueError(f'a stream\'s "gate" is "all" or "offered", not {asked!r}')
        self._gate_all = asked == "all"
        partial = bool(getattr(options, "include_partial_messages", False))
        recorded = any(_is_stream_event(step) for step in steps)
        pacing = script.get("partial") or {}
        started = time.monotonic()
        turns = 0
        replayed: set[int] = set()  # recorded steps already yielded ahead of their turn
        for position, step in enumerate(steps):
            if position in replayed:
                continue
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
            if _is_stream_event(step) and not partial:
                continue  # the CLI prints these only when asked
            raw = _fill(step["message"], variables)
            if raw.get("type") == "assistant":
                # Several lines with one id are the content blocks of one API message.
                made = str(raw.get("message", {}).get("id", ""))
                if not made or self.seen_requests[-1:] != [made]:
                    self.seen_requests.append(made)
            built = (
                partial and not recorded and raw.get("type") == "assistant" and "error" not in raw
            )
            if built:
                cut = [False]
                async for item in self._partial(raw, variables, pacing, cut):
                    yield item
                if cut[0]:
                    yield self._result(variables, started, turns, "error_during_execution", True)
                    return
            else:
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
                for event in self._closing(variables, raw) if built else ():
                    yield event
                continue
            # A recording holds the block's content_block_stop after its assistant message: the
            # CLI prints it before the tool runs.
            following = position + 1
            if (
                partial
                and following < len(steps)
                and _is_stream_event(steps[following])
                and steps[following]["message"]["event"].get("type") == "content_block_stop"
            ):
                replayed.add(following)
                yield parse_message(_fill(steps[following]["message"], variables))
            turns += 1
            if options.max_turns is not None and turns > options.max_turns:
                yield self._result(variables, started, turns, "error_max_turns", True)
                return
            results = []
            for block in calls:
                results.append(await self._call(request, block))
                if self._hook_stopped:
                    break  # the gate stopped the run: no other call of the message is made
            yield parse_message(
                {
                    "type": "user",
                    "message": {"role": "user", "content": results},
                    "session_id": variables["session_id"],
                    "uuid": str(uuid.uuid4()),
                }
            )
            if self._hook_stopped:
                yield self._result(variables, started, turns, "success", False, "hook_stopped")
                return
            for event in self._closing(variables, raw) if built else ():
                yield event
        yield self._result(variables, started, turns, "success", False)

    def _event(self, variables: dict[str, str], event: dict[str, Any]) -> Any:
        return parse_message(
            {
                "type": "stream_event",
                "uuid": str(uuid.uuid4()),
                "session_id": variables["session_id"],
                "parent_tool_use_id": None,
                "event": event,
            }
        )

    def _closing(self, variables: dict[str, str], raw: dict[str, Any]) -> list[Any]:
        """The two events that end a message, printed after its tool results."""
        body = raw.get("message", {})
        change = {"stop_reason": body.get("stop_reason"), "stop_sequence": None}
        usage = {"output_tokens": (body.get("usage") or {}).get("output_tokens", 0)}
        return [
            self._event(variables, {"type": "message_delta", "delta": change, "usage": usage}),
            self._event(variables, {"type": "message_stop"}),
        ]

    async def _partial(
        self,
        raw: dict[str, Any],
        variables: dict[str, str],
        pacing: dict[str, Any],
        cut: list[bool],
    ) -> AsyncIterator[Any]:
        """One assistant message as the CLI prints it with partial messages (module docstring),
        up to its last content block. `cut[0]` is set when an interrupt ended it."""
        body = raw.get("message", {})
        chars = int(pacing.get("chars", PARTIAL_CHARS))
        sleep = float(pacing.get("sleep", PARTIAL_SLEEP_S))

        def one(block: dict[str, Any]) -> Any:
            return parse_message({**raw, "message": {**body, "content": [block]}})

        def delta(index: int, change: dict[str, Any]) -> Any:
            return self._event(
                variables, {"type": "content_block_delta", "index": index, "delta": change}
            )

        start = {**body, "content": [], "stop_reason": None}
        yield self._event(variables, {"type": "message_start", "message": start})
        for index, block in enumerate(body.get("content", [])):
            kind = block.get("type")
            if kind == "text":
                opened: dict[str, Any] = {"type": "text", "text": ""}
            elif kind == "thinking":
                opened = {"type": "thinking", "thinking": "", "signature": ""}
            elif kind == "tool_use":
                opened = {**block, "input": {}}
            else:
                opened = dict(block)
            yield self._event(
                variables,
                {"type": "content_block_start", "index": index, "content_block": opened},
            )
            if kind == "text":
                written = ""
                for piece in pieces(str(block.get("text", "")), chars):
                    if await self._sleep(sleep):
                        # Interrupted mid block: the CLI reports the text so far and stops.
                        if written:
                            yield one({**block, "text": written})
                        cut[0] = True
                        return
                    written += piece
                    yield delta(index, {"type": "text_delta", "text": piece})
            elif kind == "thinking":
                thought = str(block.get("thinking", ""))
                yield delta(index, {"type": "thinking_delta", "thinking": thought})
                signature = str(block.get("signature", ""))
                yield delta(index, {"type": "signature_delta", "signature": signature})
            elif kind == "tool_use":
                encoded = json.dumps(block.get("input") or {})
                for piece in pieces(encoded, max(1, len(encoded) // 2 + 1)):
                    yield delta(index, {"type": "input_json_delta", "partial_json": piece})
            yield one(block)
            yield self._event(variables, {"type": "content_block_stop", "index": index})

    async def _call(self, request: BackendRequest, block: dict[str, Any]) -> dict[str, Any]:
        name, args, call_id = block["name"], block.get("input") or {}, block["id"]
        if not self._gate_all and name not in request.tools.by_name:
            # As the CLI: a tool it does not offer is answered by itself, no hook is asked.
            return {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": NO_SUCH_TOOL.format(name=name),
                "is_error": True,
            }
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
        if verdict.get("continue_") is False:
            # The hook stops the run (module docstring): the tool does not run.
            self._hook_stopped = True
            reason = str(decision.get("permissionDecisionReason") or verdict.get("stopReason"))
            return {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": f"PreToolUse:{name} hook error: {reason}",
                "is_error": True,
            }
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
        terminal: str | None = None,
    ) -> Any:
        raw: dict[str, Any] = {
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
        if terminal is not None:
            # A run stopped by a hook, as the pinned CLI reports it: the turn ended on the
            # tool call, with no answer.
            raw.update(terminal_reason=terminal, stop_reason="tool_use", result="")
        return parse_message(raw)
