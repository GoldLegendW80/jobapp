"""A test backend that moves at its own pace, as Claude Code does (review of PR #95).

The product's fake backend (app/agents/fake_backend.py) replays a recorded stream when the
runner pulls it, and asks the runner's gate about every tool call. The real CLI does neither,
and a token budget is exactly where that matters. This backend behaves like the pinned CLI on
those two points:

1. **It is pushed, not pulled.** A task of its own makes the requests of the plan one after the
   other, each taking `request_s`, and puts what Claude Code would print on a queue. It never
   waits for the runner: when the runner is slow to write its events, the requests go on.
   `sent` holds the moment each request left, `answered` how many were answered in full.
2. **The gate is asked only about the tools the run offers** (the agent's own app tools). A call
   to any other name, a built-in one like Bash or one the model made up, is answered by the
   backend itself with the CLI's error, at once, and the next request leaves without a pause:
   the hook never hears of it. `gate_asked` lists the calls the gate was asked about.

The rest follows what was recorded from the pinned CLI (tasks/evidence/ingestion-g6/): one
assistant message per content block, each with the id and the usage of its API message, the
input side in full and `output_tokens` at 1; a result message whose usage is cumulative, with
the output in full, over the requests that were answered; an interrupt ends the turn at once (a
request in flight is cut and never answered, a pending hook is cancelled); a hook that answers
`continue_: False` ends the turn with a `success` result whose terminal reason is
`hook_stopped`.

Options for the cases the real CLI is not known to produce but the runner must survive:
`hook_id` asks the gate under another call id than the one the message carries (the gate's
wait then ends without its message), `hook_first` asks the gate before the message of the call
is printed (the gate must wait for it).
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk._internal.message_parser import parse_message

from app.agents.backend import BackendRequest

# What one request of this backend costs: the input side, and the output in full.
INPUT = {"input_tokens": 700, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 100}
INPUT_SIDE = 900
OUTPUT = 40
COUNTED = INPUT_SIDE + 1  # what the runner can count of a request while the run goes on

_END = object()
_INTERRUPTED = object()


@dataclass
class Turn:
    """One answer of the model: an optional text, then tool calls (name as the model writes
    it, arguments). No call: the answer ends the run, unless `goes_on` says the turn continues
    by itself with a new request (an answer the API cut or paused)."""

    text: str | None = None
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    goes_on: bool = False


def looping(tool: str, turns: int, **args: Any) -> list[Turn]:
    """`turns` requests that each say something and call `tool`, then one that answers."""
    plan = [
        Turn(f"Step {n}.", [(tool, args or {"text": f"item {n}"})]) for n in range(1, turns + 1)
    ]
    return [*plan, Turn("Finished.")]


class PushBackend:
    def __init__(
        self,
        turns: list[Turn],
        *,
        request_s: float = 0.005,
        hook_id: str | None = None,
        hook_first: bool = False,
    ) -> None:
        self.turns = turns
        self.request_s = request_s
        self.hook_id = hook_id
        self.hook_first = hook_first
        self.sent: list[float] = []
        self.answered = 0
        self.gate_asked: list[str] = []
        self.tools_run = 0
        self.interrupts = 0
        self.seen_options: Any = None
        self._interrupted = asyncio.Event()
        self._session = str(uuid.uuid4())

    def __call__(self) -> "PushBackend":
        """A backend factory that hands out this very backend (one run per test)."""
        return self

    async def interrupt(self) -> None:
        self.interrupts += 1
        self._interrupted.set()

    async def stream(self, request: BackendRequest) -> AsyncIterator[Any]:
        self.seen_options = request.options
        queue: asyncio.Queue[Any] = asyncio.Queue()
        producer = asyncio.create_task(self._produce(request, queue))
        try:
            while (item := await queue.get()) is not _END:
                yield item
            await producer
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    # --- what Claude Code does, at its own pace -------------------------------------------------

    async def _produce(self, request: BackendRequest, queue: asyncio.Queue[Any]) -> None:
        def put(raw: dict[str, Any]) -> None:
            queue.put_nowait(parse_message(raw))

        try:
            put(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": self._session,
                    "uuid": str(uuid.uuid4()),
                    "model": request.options.model,
                    "tools": sorted(request.tools.by_name),
                }
            )
            for index, turn in enumerate(self.turns, 1):
                # The request leaves now, whatever the runner is doing.
                self.sent.append(time.monotonic())
                if await self._cut(self.request_s):
                    put(self._result("error_during_execution", True, "aborted_streaming"))
                    return
                self.answered += 1
                message_id = f"msg_{index:02d}"
                blocks: list[dict[str, Any]] = []
                if turn.text is not None:
                    blocks.append({"type": "text", "text": turn.text})
                calls = [
                    {
                        "type": "tool_use",
                        "id": f"toolu_{index:02d}_{n}",
                        "name": name,
                        "input": args,
                    }
                    for n, (name, args) in enumerate(turn.calls)
                ]
                printed = [self._assistant(message_id, block) for block in [*blocks, *calls]]
                if not (self.hook_first and calls):
                    for raw in printed:
                        put(raw)
                    printed = []
                if not calls:
                    if turn.goes_on:
                        continue  # the next request leaves at once, with no tool in between
                    put(self._result("success", False, "completed", turn.text))
                    return
                answers: list[dict[str, Any]] = []
                for call in calls:
                    answer = await self._answer(request, call, printed, put)
                    if answer is _INTERRUPTED:
                        put(self._user([self._error(call, "The tool use was rejected.")]))
                        put(self._result("error_during_execution", True, "aborted_tools"))
                        return
                    stops = answer.pop("stops", False)
                    answers.append(answer)
                    if stops:  # the hook answered `continue_: False`
                        put(self._user(answers))
                        put(self._result("success", False, "hook_stopped", ""))
                        return
                put(self._user(answers))
                # ... and the next request leaves at once.
            put(self._result("success", False, "completed", "Finished."))  # pragma: no cover
        finally:
            queue.put_nowait(_END)

    async def _answer(
        self,
        request: BackendRequest,
        call: dict[str, Any],
        printed: list[dict[str, Any]],
        put: Any,
    ) -> Any:
        name = call["name"]
        tool = request.tools.by_name.get(name)
        if tool is None:
            # Not a tool Claude Code offers: answered by the CLI itself, the hook is not asked.
            for raw in printed:
                put(raw)
            printed.clear()
            text = f"<tool_use_error>Error: No such tool available: {name}</tool_use_error>"
            return self._error(call, text)
        self.gate_asked.append(name)
        asking = asyncio.ensure_future(
            request.gate(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": self._session,
                    "tool_name": name,
                    "tool_input": call["input"],
                    "tool_use_id": self.hook_id or call["id"],
                },
                self.hook_id or call["id"],
                {"signal": None},
            )
        )
        if printed:
            # `hook_first`: the hook was asked before the message of its call was printed.
            await asyncio.sleep(0.05)
            for raw in printed:
                put(raw)
            printed.clear()
        verdict = await self._unless_interrupted(asking)
        if verdict is _INTERRUPTED:
            return _INTERRUPTED
        decision = verdict.get("hookSpecificOutput", {})
        if verdict.get("continue_") is False:
            reason = decision.get("permissionDecisionReason") or verdict.get("stopReason")
            return {**self._error(call, f"PreToolUse:{name} hook error: {reason}"), "stops": True}
        if decision.get("permissionDecision") == "deny":
            return self._error(call, str(decision.get("permissionDecisionReason")))
        self.tools_run += 1
        made = await self._unless_interrupted(asyncio.ensure_future(tool.handler(call["input"])))
        if made is _INTERRUPTED:
            return _INTERRUPTED
        return {
            "type": "tool_result",
            "tool_use_id": call["id"],
            "content": made.get("content", []),
            "is_error": bool(made.get("is_error", False)),
        }

    async def _cut(self, seconds: float) -> bool:
        """Wait while a request is in flight; True when the run was interrupted meanwhile."""
        try:
            await asyncio.wait_for(self._interrupted.wait(), timeout=max(seconds, 0.001))
        except TimeoutError:
            return False
        return True

    async def _unless_interrupted(self, pending: "asyncio.Future[Any] | Awaitable[Any]") -> Any:
        task = asyncio.ensure_future(pending)
        stop = asyncio.ensure_future(self._interrupted.wait())
        try:
            done, _ = await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                return task.result()
            return _INTERRUPTED
        finally:
            for waiting in (task, stop):
                if not waiting.done():
                    waiting.cancel()
            await asyncio.gather(task, stop, return_exceptions=True)

    # --- the lines Claude Code prints -----------------------------------------------------------

    def _assistant(self, message_id: str, block: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "assistant",
            "session_id": self._session,
            "uuid": str(uuid.uuid4()),
            "parent_tool_use_id": None,
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": self.seen_options.model,
                "content": [block],
                "stop_reason": None,
                "usage": {**INPUT, "output_tokens": 1},
            },
        }

    def _user(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "type": "user",
            "message": {"role": "user", "content": results},
            "session_id": self._session,
            "uuid": str(uuid.uuid4()),
        }

    def _error(self, call: dict[str, Any], text: str) -> dict[str, Any]:
        return {"type": "tool_result", "tool_use_id": call["id"], "content": text, "is_error": True}

    def _result(
        self, subtype: str, is_error: bool, terminal: str, answer: str | None = None
    ) -> dict[str, Any]:
        raw: dict[str, Any] = {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "duration_ms": 1,
            "duration_api_ms": 1,
            "num_turns": self.answered,
            "session_id": self._session,
            "total_cost_usd": 0.001 * self.answered,
            "terminal_reason": terminal,
            # Cumulative, the output in full, over the requests that were answered.
            "usage": {
                **{name: value * self.answered for name, value in INPUT.items()},
                "output_tokens": OUTPUT * self.answered,
            },
        }
        if answer is not None:
            raw["result"] = answer
        return raw
