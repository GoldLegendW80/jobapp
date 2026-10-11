"""Probe of task G6 (ingestion plan: assumption A15 and the overshoot of a token budget).
Throwaway, not product code: it asks the real bundled Claude Code CLI what the runner can
rely on. The product's own path is tested by api/tests/agents/test_g6_real_cli.py.

Questions:
1. Does every assistant message carry `usage`, and what is in it (A15)? Do the messages
   of one API call share an id?
2. In which order do the assistant message of a tool call and the PreToolUse hook of that
   call reach the SDK's user, and is the hook's `tool_use_id` the block's id?
3. When a run must stop because its token budget is used up, how many requests still
   leave after the one whose usage reached the budget? Compared for the ways of stopping:
   - `stop`: the PreToolUse hook answers `continue: false` (what the runner does);
   - `interrupt`: `ClaudeSDKClient.interrupt()` when the reaching message arrives;
   - `hold`: that interrupt, and the hook of the call never answers;
   - `close`: the SDK client is closed while the turn is going on, with no interrupt
     first (what happens to a run when the worker stops);
   - `interrupt_close`: an interrupt, then the close;
   - `hold_close`: the hook never answers, nothing interrupts, the client is closed a
     few seconds later (a hook that only holds, met by a kill).
4. What does the result message of a stopped run carry (subtype, terminal reason, usage)?

It starts the real CLI through the pinned SDK with the options of app/agents/runner.py
(`tools=[]`, `dontAsk`, an in-process MCP server, no settings, a scratch HOME, the
PreToolUse gate) against a stub of the Messages API in this process (the event sequence
of tests/anthropic_stub.py, with a text block and a tool call per answer and distinct
usage figures). No model is called and no credential is used: the key is a made up
string.

    cd <repo root>
    CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK=1 api/.venv/bin/python \
        tasks/evidence/ingestion-g6/budget_probe.py [out.json] [trials] [mode ...]
"""

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

KEY = "probe-key-not-a-credential"
TOOL = "mcp__app__ping"
ENV_KEEP = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
# What the stub reports for every request (distinct figures, to tell the fields apart).
USAGE = {
    "input_tokens": 1000,
    "cache_creation_input_tokens": 200,
    "cache_read_input_tokens": 300,
}
OUTPUT_TOKENS = 50
# The loop would go on for this many tool calls if nothing stopped it.
LOOP = 8
# Reached by the third request when counting what an assistant message reports (1,501).
BUDGET = 4000
MODES = ("stop", "interrupt", "hold", "close", "interrupt_close", "hold_close")

STARTED = time.monotonic()
REQUESTS: list[dict[str, Any]] = []
OTHER: list[str] = []
LOCK = threading.Lock()


class Close(Exception):
    """Leave the message loop and close the client, the turn still going on."""


def now() -> float:
    return round(time.monotonic() - STARTED, 4)


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
        with LOCK:
            OTHER.append(f"GET {self.path.split('?')[0]}")
        self._json(404, {"type": "error", "error": {"type": "not_found_error"}})

    def do_HEAD(self) -> None:
        with LOCK:
            OTHER.append(f"HEAD {self.path.split('?')[0]}")
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        path = self.path.split("?")[0]
        if path != "/v1/messages":
            with LOCK:
                OTHER.append(f"POST {path}")
            self._json(404, {"type": "error", "error": {"type": "not_found_error"}})
            return
        results = sum(
            1
            for m in body.get("messages") or []
            if isinstance(m.get("content"), list)
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"
        )
        with LOCK:
            index = len(REQUESTS) + 1
            REQUESTS.append({"n": index, "at": now(), "tool_results": results})
        message_id = f"msg_probe_{index:02d}"
        again = results < LOOP
        blocks: list[dict[str, Any]] = [
            {"type": "text", "text": f"Step {results + 1}."}
        ]
        if again:
            call = f"toolu_probe_{index:02d}"
            blocks.append(
                {"type": "tool_use", "id": call, "name": TOOL, "input": {"n": index}}
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def event(name: str, data: dict[str, Any]) -> None:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            start = {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": body.get("model", "claude-opus-5-5"),
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {**USAGE, "output_tokens": 1},
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
                    {
                        "type": "content_block_start",
                        "index": position,
                        "content_block": opened,
                    },
                )
                event(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": position, "delta": change},
                )
                event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": position},
                )
            stop = "tool_use" if again else "end_turn"
            event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop, "stop_sequence": None},
                    "usage": {"output_tokens": OUTPUT_TOKENS},
                },
            )
            event("message_stop", {"type": "message_stop"})


def build_env(scratch: Path, base_url: str) -> dict[str, str]:
    env = {name: "" for name in os.environ if name not in ENV_KEEP}
    env.update(
        {
            "HOME": str(scratch / "home"),
            "CLAUDE_CONFIG_DIR": str(scratch / "config"),
            "CLAUDE_CODE_TMPDIR": str(scratch / "tmp"),
            "TMPDIR": str(scratch / "tmp"),
            "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "ANTHROPIC_API_KEY": KEY,
            "ANTHROPIC_BASE_URL": base_url,
        }
    )
    return env


def counted(usage: dict[str, Any] | None) -> int:
    usage = usage or {}
    return sum(
        int(usage.get(name) or 0)
        for name in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        )
    )


async def trial(mode: str, base_url: str) -> dict[str, Any]:
    """One run. `mode` is observe (no budget) or one of MODES."""
    first = len(REQUESTS)
    other_first = len(OTHER)
    scratch = Path(tempfile.mkdtemp(prefix="g6-probe-"))
    for sub in ("home", "config", "tmp", "work"):
        (scratch / sub).mkdir()
    log: list[dict[str, Any]] = []
    per_id: dict[str, int] = {}
    seen_calls: set[str] = set()
    state: dict[str, Any] = {
        "used_up": False,
        "reaching_request": None,
        "tools_run": 0,
        "gate_cancelled": 0,
        "gate_before_message": 0,
    }
    budget = None if mode == "observe" else BUDGET

    @tool("ping", "Probe tool: answers pong.", {"type": "object", "properties": {}})
    async def ping(args: dict[str, Any]) -> dict[str, Any]:
        state["tools_run"] += 1
        log.append({"at": now(), "what": "tool runs", "n": args.get("n")})
        return {"content": [{"type": "text", "text": "pong"}]}

    async def gate(
        input_data: dict[str, Any], tool_use_id: str | None, _: Any
    ) -> dict[str, Any]:
        known = tool_use_id in seen_calls
        log.append(
            {
                "at": now(),
                "what": "gate asked",
                "tool_use_id": tool_use_id,
                "its_message_already_handled": known,
            }
        )
        if not known:
            state["gate_before_message"] += 1
        if state["used_up"] and mode in ("hold", "hold_close"):
            log.append({"at": now(), "what": "gate holds"})
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                state["gate_cancelled"] += 1
                log.append({"at": now(), "what": "gate cancelled by the SDK"})
                raise
        if state["used_up"] and mode == "stop":
            log.append({"at": now(), "what": "gate answers continue false"})
            return {
                "continue_": False,
                "stopReason": "The run reached its token budget.",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "The run reached its token budget.",
                },
            }
        return {}

    server = create_sdk_mcp_server(name="app", version="1.0.0", tools=[ping])
    options = ClaudeAgentOptions(
        system_prompt="Probe. Call the tool when asked.",
        tools=[],
        allowed_tools=[TOOL],
        permission_mode="dontAsk",
        mcp_servers={"app": server},
        strict_mcp_config=True,
        setting_sources=[],
        model="claude-opus-5-5",
        max_turns=40,
        cwd=str(scratch / "work"),
        env=build_env(scratch, base_url),
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[gate])]},  # type: ignore[list-item]
    )
    result: dict[str, Any] = {}
    error = None
    stopped_at = None
    ended_at = None
    started = time.monotonic()
    client = ClaudeSDKClient(options)
    limit = 6 if mode == "hold_close" else 60
    try:
        async with asyncio.timeout(limit):
            await client.connect()
            await client.query("Loop.")
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    key = message.message_id or f"none-{len(per_id)}"
                    per_id[key] = max(per_id.get(key, 0), counted(message.usage))
                    calls = [
                        b.id for b in message.content if isinstance(b, ToolUseBlock)
                    ]
                    seen_calls.update(calls)
                    spent = sum(per_id.values())
                    log.append(
                        {
                            "at": now(),
                            "what": "assistant message",
                            "message_id": message.message_id,
                            "blocks": [type(b).__name__ for b in message.content],
                            "tool_use_ids": calls,
                            "usage": message.usage,
                            "stop_reason": message.stop_reason,
                            "spent_as_counted": spent,
                        }
                    )
                    if budget is not None and spent >= budget and not state["used_up"]:
                        state["used_up"] = True
                        state["reaching_request"] = len(REQUESTS) - first
                        log.append({"at": now(), "what": "budget reached"})
                    if state["used_up"] and calls and stopped_at is None:
                        if mode in ("interrupt", "hold", "interrupt_close"):
                            stopped_at = now()
                            log.append({"at": stopped_at, "what": "interrupt sent"})
                            await client.interrupt()
                            log.append({"at": now(), "what": "interrupt acknowledged"})
                        if mode in ("close", "interrupt_close"):
                            stopped_at = stopped_at or now()
                            raise Close
                elif isinstance(message, UserMessage) and isinstance(
                    message.content, list
                ):
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            log.append(
                                {
                                    "at": now(),
                                    "what": "tool result",
                                    "is_error": bool(block.is_error),
                                    "text": str(block.content)[:120],
                                }
                            )
                elif isinstance(message, ResultMessage):
                    ended_at = now()
                    result = {
                        "subtype": message.subtype,
                        "is_error": message.is_error,
                        "num_turns": message.num_turns,
                        "terminal_reason": message.terminal_reason,
                        "stop_reason": message.stop_reason,
                        "usage": {
                            name: (message.usage or {}).get(name)
                            for name in (*USAGE, "output_tokens")
                        },
                        "total_cost_usd": message.total_cost_usd,
                        "result": (message.result or "")[:80],
                    }
                    log.append({"at": ended_at, "what": "result", **result})
    except Close:
        log.append({"at": now(), "what": "client closed, the turn still going on"})
    except Exception as exc:  # noqa: BLE001, every failure is a result of the probe
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        closing = time.monotonic()
        with contextlib.suppress(Exception):
            await client.disconnect()
        close_seconds = round(time.monotonic() - closing, 2)
        shutil.rmtree(scratch, ignore_errors=True)
    await asyncio.sleep(0.5)  # a request sent just before the end would be in by now
    mine = REQUESTS[first:]
    reaching = state["reaching_request"]
    return {
        "mode": mode,
        "seconds": round(time.monotonic() - started, 2),
        "error": error,
        "requests_to_the_api": len(mine),
        "request_whose_usage_reached_the_budget": reaching,
        "requests_after_it": (len(mine) - reaching) if reaching else None,
        "tools_run": state["tools_run"],
        "gate_asked_before_its_message_was_handled": state["gate_before_message"],
        "gate_cancelled_by_the_sdk": state["gate_cancelled"],
        "seconds_from_interrupt_to_result": round(ended_at - stopped_at, 3)
        if ended_at is not None and stopped_at is not None
        else None,
        "seconds_to_close_the_client": close_seconds,
        "result": result,
        "other_http_calls": OTHER[other_first:],
        "request_times": [r["at"] for r in mine],
        "log": log,
    }


async def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("budget_probe.json")
    trials = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    only = set(sys.argv[3:])
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    from claude_agent_sdk._cli_version import __cli_version__

    report: dict[str, Any] = {
        "sdk": claude_agent_sdk.__version__,
        "bundled_cli": __cli_version__,
        "platform": f"{sys.platform} {os.uname().machine}",
        "python": sys.version.split()[0],
        "stub_usage_per_request": {**USAGE, "output_tokens": OUTPUT_TOKENS},
        "budget": BUDGET,
        "summary": {},
        "trials": [],
    }
    plan = [("observe", 1)]
    plan += [(mode, trials) for mode in ("stop", "interrupt", "hold")]
    plan += [(mode, 3) for mode in ("close", "interrupt_close", "hold_close")]
    for mode, count in plan:
        if only and mode not in only:
            continue
        for _ in range(count):
            entry = await trial(mode, base_url)
            report["trials"].append(entry)
            brief = {k: v for k, v in entry.items() if k not in ("log", "result")}
            print(json.dumps(brief), flush=True)
            print("   result:", json.dumps(entry["result"]), flush=True)
        mine = [t for t in report["trials"] if t["mode"] == mode]
        report["summary"][mode] = {
            "trials": len(mine),
            "requests_after_the_reaching_one": sorted(
                {t["requests_after_it"] for t in mine}, key=str
            ),
            "tools_run": sorted({t["tools_run"] for t in mine}),
            "result": sorted(
                {
                    f"{t['result'].get('subtype')}/{t['result'].get('terminal_reason')}"
                    for t in mine
                }
            ),
            "gate_asked_before_its_message_was_handled": sum(
                t["gate_asked_before_its_message_was_handled"] for t in mine
            ),
        }
        out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report["summary"], indent=1))
    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    asyncio.run(main())
