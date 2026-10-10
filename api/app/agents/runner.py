"""One agent run, in the worker (SPEC-agents.md, Runtime; SDK facts in tasks/agents/todo.md).

`execute(context, run_id)`:
1. claims the run (queued -> running; a run cancelled while queued is skipped);
2. reads the user's model credential and decrypts it in memory only;
3. makes a scratch directory for the run (mode 0700, under AGENTS_SCRATCH_DIR or the system
   temporary directory, a tmpfs in the worker container) holding Claude Code's HOME, config
   directory and temporary files, and the run's working directory;
4. builds the SDK options: no built-in tool at all (`tools=[]`), the agent's app tools only
   (`allowed_tools`, an in-process MCP server bound to the run's user), every built-in tool named
   again in `disallowed_tools`, `permission_mode="dontAsk"` (anything not pre-approved is denied,
   never prompted), no settings file, no other MCP server, and a PreToolUse hook that denies any
   tool outside the allow list in code (the gate below);
5. streams the run through the backend (the SDK, or the fake in tests), appending each step to
   agent_events: text, tool calls (name and a short argument summary), tool results (truncated),
   denied tools, usage and cost. While a text block is being written, its text is published
   live next to the log (app/agents/live.py, issue #33): Claude Code is asked for partial
   messages, their text deltas feed a per-block buffer in Redis that the run stream relays, and
   the block's `text` event, written as before when the block is complete, replaces it. Thinking
   and tool input deltas are never read;
6. stops it when the user cancels (POST /api/agents/runs/{id}/cancel sets cancel_requested_at,
   polled every CANCEL_POLL_S) or when the wall time limit passes: the backend is interrupted
   (`ClaudeSDKClient.interrupt()`), given INTERRUPT_GRACE_S to end, then cancelled;
7. records the outcome (done, failed or cancelled, tokens, cost, the final text) and deletes the
   scratch directory and the live text, on every path: success, failure, cancel, worker
   shutdown. A run that does not end `done` stores no answer: text that was only live is gone
   (the log keeps the `text` event of a cut block when Claude Code reports one).

The credential goes to Claude Code only through the environment of that one process
(`options.env`: ANTHROPIC_API_KEY for an API key, CLAUDE_CODE_OAUTH_TOKEN for a subscription
token). The SDK merges `options.env` over the worker's own environment, so every inherited
variable outside ENV_KEEP is overridden with an empty value. The SDK's other process, its
`claude -v` version probe, is started without env= and would inherit the worker's environment
as is: the worker turns it off (CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK, in the image and in
app/worker.py) and drops DATABASE_URL, CREDENTIALS_KEY, SECRET_KEY and the other secrets from
its own environment once its settings are read. So no Claude Code process sees them.
The credential is never
written to disk, never logged, and scrubbed from every event and error stored. The first run
that succeeds with a subscription token marks it verified (credentials.set_verified_if_unchanged:
only if it was not replaced meanwhile).

Logs carry ids, statuses, counts, tokens and cost, never a prompt, a tool argument or a result.
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLIConnectionError,
    CLINotFoundError,
    HookMatcher,
    ProcessError,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from sqlalchemy.orm import Session, sessionmaker

from app.agents import chat, credentials, definitions, live, runs
from app.agents import tools as app_tools
from app.agents.backend import Backend, BackendFactory, BackendRequest
from app.agents.crypto import DecryptError, Keyring
from app.agents.models import CredentialKind, RunStatus
from app.config import Settings

logger = logging.getLogger("app.agents.runner")

CANCEL_POLL_S = 0.5
INTERRUPT_GRACE_S = 10.0
TEXT_MAX = 20_000
TOOL_RESULT_MAX = 2_000
ARG_VALUE_MAX = 200
ARGS_MAX = 1_000
REDACTED = "[redacted]"

# Built-in Claude Code tools, denied by name on top of `tools=[]` (which already leaves the model
# none) and of the PreToolUse gate (which denies anything outside the allow list): shell, files,
# web, subagents and tasks, schedules, worktrees, skills, planning and user prompts. Best effort,
# defense in depth only: CLI releases add built-ins, and a name the CLI does not know is ignored
# (the real CLI 2.1.286 ran with this list).
FORBIDDEN_TOOLS = (
    "Agent",
    "AskUserQuestion",
    "Bash",
    "BashOutput",
    "CronCreate",
    "CronDelete",
    "CronList",
    "Edit",
    "EnterPlanMode",
    "EnterWorktree",
    "ExitPlanMode",
    "ExitWorktree",
    "Glob",
    "Grep",
    "KillShell",
    "LS",
    "LSP",
    "Monitor",
    "MultiEdit",
    "NotebookEdit",
    "NotebookRead",
    "Read",
    "SendMessage",
    "Skill",
    "SlashCommand",
    "Task",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
    "TodoWrite",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
    "Write",
)

# Inherited variables the CLI process keeps; every other one is overridden with "".
ENV_KEEP = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "https_proxy",
        "http_proxy",
        "no_proxy",
    }
)
# Argument names whose values are never stored in a tool call event.
_SECRET_ARG = ("secret", "token", "password", "api_key", "apikey", "authorization", "cookie")

# Messages stored on the run (they reach the user).
NO_CREDENTIAL = "No model credential is saved. Add one on the Account page."
UNREADABLE = "The saved credential cannot be read with this server's key. Paste it again."
UNKNOWN_AGENT = "This agent no longer exists."
THREAD_GONE = "This conversation no longer exists."
TIME_LIMIT = "The run took longer than its limit of {minutes} and was stopped."
TURN_LIMIT = "The run reached its limit of {turns} turns."
NO_RESULT = "The agent stopped without a result."
REFUSED = "Anthropic refused the credential: it is wrong, revoked or has no access."
BUSY = "Anthropic is busy or rate limiting this credential. Try again later."
API_ERROR = "The model API failed (HTTP {status})."
FAILED = "The agent failed ({reason})."
NO_CLI = "Claude Code is not installed in the worker."
NO_START = "Claude Code could not start."
PROCESS = "The Claude Code process failed (exit code {code})."
INTERNAL = "The run failed on an internal error."


@dataclass
class RunContext:
    settings: Settings
    sessions: sessionmaker[Session]
    clock: Callable[[], datetime]
    keyring: Keyring | None
    backend_factory: BackendFactory
    cancel_poll_s: float = CANCEL_POLL_S
    interrupt_grace_s: float = INTERRUPT_GRACE_S
    # Tests replace an agent's definition (shorter limits); production uses definitions.AGENTS.
    agents: dict[str, definitions.AgentDefinition] | None = None
    # Where the answer being written is published (app/agents/live.py); None: no live text, and
    # Claude Code is not asked for partial messages.
    live_store: live.LiveStore | None = None
    live_flush_s: float = live.FLUSH_S

    def definition(self, name: str) -> definitions.AgentDefinition:
        if self.agents is not None and name in self.agents:
            return self.agents[name]
        return definitions.get(name, self.settings.app_env)


# --- pure helpers -------------------------------------------------------------------------------


def scrub(value: Any, secret: str | None) -> Any:
    """`value` with every occurrence of the secret replaced, recursively: strings, the items of
    lists and tuples, and both the keys and the values of dicts.

    What this guarantees, and what it does not. The credential is replaced wherever it stands
    whole, character for character, inside one string. Each string is looked at on its own: a
    credential cut in two by the model (the end of one text block and the beginning of the
    next, two tool arguments, two events) or rewritten by it (spaces, another case, an
    encoding) is not recognised, and nothing joins stored values to look again. That is why a
    value is always scrubbed whole and only then shortened (`shorten`): shortening first can
    cut a credential in two, and the part that is kept would be stored as it is."""
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, REDACTED)
    if isinstance(value, list | tuple):
        return [scrub(item, secret) for item in value]
    if isinstance(value, dict):
        return {
            (scrub(key, secret) if isinstance(key, str) else key): scrub(item, secret)
            for key, item in value.items()
        }
    return value


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} more]"


def shorten(text: str, limit: int, secret: str | None) -> str:
    """`text` as it may be stored when only its first `limit` characters are kept: the
    credential is replaced in the whole text first, and only then is the text cut (`scrub`
    says why the order matters). The one way a text derived from the model or from a tool is
    shortened in this module."""
    return _clip(str(scrub(text, secret)), limit)


def summarize_args(args: Any, secret: str | None = None) -> dict[str, Any]:
    """A short, secret-free view of a tool call's arguments for the timeline. The credential is
    replaced in the arguments as the model gave them (names and values, at any depth) before
    anything is encoded or shortened, and again in each encoded value before it is cut."""
    args = scrub(args, secret)
    if not isinstance(args, dict):
        return {"value": shorten(json.dumps(args, default=str), ARG_VALUE_MAX, secret)}
    out: dict[str, Any] = {}
    used = 0
    for key, value in args.items():
        name = str(key)
        if any(marker in name.lower() for marker in _SECRET_ARG):
            out[name] = REDACTED
            continue
        shown = value if isinstance(value, str) else json.dumps(value, default=str)
        shown = shorten(shown, ARG_VALUE_MAX, secret)
        used += len(shown)
        if used > ARGS_MAX:
            out["..."] = "more arguments not shown"
            break
        out[name] = shown
    return out


def result_text(content: Any) -> str:
    """The text of a tool result's content (a string, or a list of content blocks)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, dict):
                parts.append(f"[{block.get('type', 'block')}]")
        return "\n".join(parts)
    return str(content)


def build_env(
    kind: CredentialKind, secret: str, scratch: Path, base_url: str | None = None
) -> dict[str, str]:
    """The environment of the run's Claude Code process (merged by the SDK over os.environ)."""
    env = {name: "" for name in os.environ if name not in ENV_KEEP}
    env.update(
        {
            "HOME": str(scratch / "home"),
            # Settings, sessions and plugins of Claude Code live here (env-vars reference).
            "CLAUDE_CONFIG_DIR": str(scratch / "config"),
            "CLAUDE_CODE_TMPDIR": str(scratch / "tmp"),
            "TMPDIR": str(scratch / "tmp"),
            # No transcript or prompt history written (Python has no persistSession option;
            # the sessions guide names this variable instead).
            "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
            # No auto update, telemetry, error reporting or other nonessential traffic.
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_AGENT_SDK_CLIENT_APP": "jobapp/0.1",
        }
    )
    if kind is CredentialKind.anthropic_api_key:
        env["ANTHROPIC_API_KEY"] = secret
    else:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = secret
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    return env


Gate = Callable[[dict[str, Any], str | None, Any], Awaitable[dict[str, Any]]]


def make_gate(allowed: frozenset[str], on_denied: Callable[[str], Awaitable[None]]) -> Gate:
    """The PreToolUse hook: any tool outside the allow list is denied by this code, whatever
    the options or the model say (hooks run first, and a hook deny holds in every mode)."""

    async def gate(
        input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        name = str(input_data.get("tool_name", ""))
        if name in allowed:
            return {}
        await on_denied(name)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"The tool {name} is not available to this agent.",
            }
        }

    return gate


def build_options(
    definition: definitions.AgentDefinition,
    tools: app_tools.AppTools,
    env: dict[str, str],
    workdir: Path,
    gate: Gate,
    stderr: Callable[[str], None] | None = None,
    partial_messages: bool = False,
) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=definition.system_prompt,
        tools=[],
        allowed_tools=tools.allowed_names,
        disallowed_tools=list(FORBIDDEN_TOOLS),
        permission_mode="dontAsk",
        mcp_servers={app_tools.SERVER: tools.server},
        strict_mcp_config=True,
        setting_sources=[],
        model=definition.model,
        max_turns=definition.max_turns,
        effort=definition.effort,  # type: ignore[arg-type]
        thinking={"type": "adaptive"},
        cwd=str(workdir),
        env=env,
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[gate])]},  # type: ignore[list-item]
        stderr=stderr,
        # StreamEvent messages next to the complete ones (the text deltas of the live answer).
        include_partial_messages=partial_messages,
    )


def make_scratch(settings: Settings, run_id: uuid.UUID) -> Path:
    base = settings.agents_scratch_dir or None
    scratch = Path(tempfile.mkdtemp(prefix=f"agent-run-{run_id.hex[:12]}-", dir=base))
    for name in ("home", "config", "tmp", "work"):
        (scratch / name).mkdir(mode=0o700)
    return scratch


# --- events -------------------------------------------------------------------------------------


@dataclass
class Recorder:
    """Appends a run's events in order (one writer per run, so seq is a plain counter)."""

    context: RunContext
    run_id: uuid.UUID
    seq: int
    secret: str | None = field(default=None, repr=False)
    # Called in the transaction that records a successful end (A4: the chat answer is stored
    # with the run's final status, so a client that sees "done" finds the answer).
    on_done: Callable[[Session, runs.Outcome], None] | None = field(default=None, repr=False)
    # Called first in that transaction, before the run row is updated, to take the locks that
    # come before the run in the lock order (A4, issue #27: the chat thread, see chat.py).
    lock_first: Callable[[Session], None] | None = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def last_seq(self) -> int:
        """The seq of the newest event of this run (written, or being written)."""
        return self.seq - 1

    def _write(self, seq: int, kind: str, data: dict[str, Any]) -> None:
        with self.context.sessions() as db:
            runs.append_event(db, self.run_id, seq, kind, data, self.context.clock())
            db.commit()

    async def emit(self, kind: str, data: dict[str, Any]) -> None:
        clean = scrub(data, self.secret)
        async with self._lock:
            # Taken before the write: a cancelled await leaves the write running in its thread,
            # and the next event must not reuse its seq.
            seq, self.seq = self.seq, self.seq + 1
            await asyncio.to_thread(self._write, seq, kind, clean)

    def _finish(self, outcome: runs.Outcome) -> bool:
        with self.context.sessions() as db:
            if self.lock_first is not None and outcome.status is RunStatus.done:
                # Lock order: whatever on_done will lock, before runs.finish locks the run.
                self.lock_first(db)
            if not runs.finish(db, self.run_id, outcome, self.context.clock()):
                db.rollback()
                return False
            data: dict[str, Any] = {"status": outcome.status.value}
            if outcome.error:
                data["error"] = outcome.error
            runs.append_event(db, self.run_id, self.seq, "status", data, self.context.clock())
            if self.on_done is not None and outcome.status is RunStatus.done:
                self.on_done(db, outcome)
            db.commit()
            return True

    async def finish(self, outcome: runs.Outcome) -> bool:
        # The answer is scrubbed whole, then shortened (never the other way round, `scrub`).
        output = scrub(outcome.output, self.secret)
        if isinstance(output, dict) and isinstance(output.get("text"), str):
            output = {**output, "text": _clip(output["text"], TEXT_MAX)}
        clean = runs.Outcome(
            status=outcome.status,
            error=scrub(outcome.error, self.secret),
            output=output,
            model=scrub(outcome.model, self.secret),
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            cost_usd=outcome.cost_usd,
        )
        async with self._lock:
            done = await asyncio.to_thread(self._finish, clean)
            if done:
                self.seq += 1
            return done


# --- the run ------------------------------------------------------------------------------------


@dataclass
class _State:
    model: str | None = None
    result: ResultMessage | None = None
    last_text: str = ""
    denied: int = 0
    live_text: live.LiveText | None = None


async def _handle(message: Any, state: _State, recorder: Recorder) -> None:
    if isinstance(message, StreamEvent):
        # Partial messages: only the text deltas of a text block are read (live.LiveText.feed).
        if state.live_text is not None:
            state.live_text.feed(message.event)
    elif isinstance(message, AssistantMessage):
        state.model = message.model or state.model
        for block in message.content:
            if isinstance(block, TextBlock):
                if block.text:
                    state.last_text = block.text
                    stored = shorten(block.text, TEXT_MAX, recorder.secret)
                    await recorder.emit("text", {"text": stored})
                if state.live_text is not None:
                    # The complete text is stored: it replaces the live one.
                    await state.live_text.close_block()
            elif isinstance(block, ThinkingBlock):
                await recorder.emit("thinking", {})
            elif isinstance(block, ToolUseBlock):
                await recorder.emit(
                    "tool_call",
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": summarize_args(block.input, recorder.secret),
                    },
                )
        if message.error:
            await recorder.emit("api_error", {"error": str(message.error)})
    elif isinstance(message, UserMessage):
        if isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    await recorder.emit(
                        "tool_result",
                        {
                            "id": block.tool_use_id,
                            "is_error": bool(block.is_error),
                            "content": shorten(
                                result_text(block.content), TOOL_RESULT_MAX, recorder.secret
                            ),
                        },
                    )
    elif isinstance(message, ResultMessage):
        state.result = message
        await recorder.emit("usage", usage_of(message))
    elif isinstance(message, SystemMessage):
        if message.subtype == "init":
            data = message.data
            state.model = str(data.get("model") or "") or state.model
            await recorder.emit(
                "init",
                {"model": data.get("model"), "tools": list(data.get("tools") or [])},
            )
    elif isinstance(message, RateLimitEvent):
        await recorder.emit("rate_limit", {"status": message.rate_limit_info.status})


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def usage_of(result: ResultMessage) -> dict[str, Any]:
    usage = result.usage or {}
    return {
        "input_tokens": _int(usage.get("input_tokens")),
        "output_tokens": _int(usage.get("output_tokens")),
        "cache_creation_input_tokens": _int(usage.get("cache_creation_input_tokens")),
        "cache_read_input_tokens": _int(usage.get("cache_read_input_tokens")),
        "cost_usd": result.total_cost_usd,
        "num_turns": result.num_turns,
        "duration_ms": result.duration_ms,
    }


def _cost(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        cost = Decimal(str(value))
    except InvalidOperation:
        return None
    return cost.quantize(Decimal("0.000001")) if cost.is_finite() and cost >= 0 else None


def _tokens(result: ResultMessage | None) -> tuple[int | None, int | None]:
    if result is None or not result.usage:
        return None, None
    usage = usage_of(result)
    total_in = (
        usage["input_tokens"]
        + usage["cache_creation_input_tokens"]
        + usage["cache_read_input_tokens"]
    )
    return total_in, usage["output_tokens"]


def _minutes(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:g} seconds"
    minutes = seconds / 60
    return f"{minutes:g} minute" + ("" if minutes == 1 else "s")


def outcome_of(
    state: _State, definition: definitions.AgentDefinition, stopped: str | None
) -> runs.Outcome:
    result = state.result
    input_tokens, output_tokens = _tokens(result)
    usage = {
        "model": state.model or definition.model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _cost(result.total_cost_usd) if result is not None else None,
    }
    if stopped == "cancelled":
        return runs.Outcome(status=RunStatus.cancelled, **usage)
    if stopped == "timeout":
        message = TIME_LIMIT.format(minutes=_minutes(definition.wall_time_s))
        return runs.Outcome(status=RunStatus.failed, error=message, **usage)
    if result is None:
        return runs.Outcome(status=RunStatus.failed, error=NO_RESULT, **usage)
    if result.subtype == "error_max_turns":
        message = TURN_LIMIT.format(turns=definition.max_turns)
        return runs.Outcome(status=RunStatus.failed, error=message, **usage)
    if result.is_error:
        status = result.api_error_status
        if status in (401, 403):
            message = REFUSED
        elif status in (429, 503, 529):
            message = BUSY
        elif status is not None:
            message = API_ERROR.format(status=status)
        else:
            message = FAILED.format(reason=result.subtype)
        return runs.Outcome(status=RunStatus.failed, error=message, **usage)
    text = result.result if result.result is not None else state.last_text
    # Whole: Recorder.finish replaces the credential in it, then shortens it to TEXT_MAX.
    return runs.Outcome(status=RunStatus.done, output={"text": text}, **usage)


def error_of(exc: BaseException) -> str:
    if isinstance(exc, CLINotFoundError):
        return NO_CLI
    if isinstance(exc, ProcessError):
        return PROCESS.format(code=exc.exit_code if exc.exit_code is not None else "unknown")
    if isinstance(exc, CLIConnectionError):
        return NO_START
    if isinstance(exc, ClaudeSDKError):
        return FAILED.format(reason=type(exc).__name__)
    return INTERNAL


async def _drive(
    backend: Backend, request: BackendRequest, state: _State, recorder: Recorder
) -> None:
    # aclosing: when this task is cancelled mid step, the stream's own `finally` (which stops the
    # Claude Code process) runs here and now, before the scratch directory is removed, not later
    # from the async generator finalizer.
    async with contextlib.aclosing(backend.stream(request)) as messages:
        async for message in messages:
            await _handle(message, state, recorder)


async def _watch_cancel(context: RunContext, run_id: uuid.UUID) -> None:
    """Returns once the user asked to cancel the run. A failed read (the database restarting)
    is retried at the next poll: it never stops the run."""

    def requested() -> bool:
        with context.sessions() as db:
            return runs.cancel_requested(db, run_id)

    while True:
        try:
            if await asyncio.to_thread(requested):
                return
        except Exception as exc:
            logger.warning(
                "agent cancel check failed",
                extra={"run_id": str(run_id), "error": type(exc).__name__},
            )
        await asyncio.sleep(context.cancel_poll_s)


async def _stop(task: asyncio.Task[None], grace_s: float) -> None:
    """Let an interrupted stream end on its own for `grace_s`, then cancel it."""
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(asyncio.shield(task), timeout=grace_s)
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _run_stream(
    context: RunContext,
    run_id: uuid.UUID,
    definition: definitions.AgentDefinition,
    backend: Backend,
    request: BackendRequest,
    state: _State,
    recorder: Recorder,
) -> str | None:
    """Run the stream to its end, or stop it. Returns None (ran to its end), "cancelled" or
    "timeout". Exceptions of the stream propagate."""
    consumer = asyncio.create_task(_drive(backend, request, state, recorder))
    watcher = asyncio.create_task(_watch_cancel(context, run_id))
    try:
        done, _ = await asyncio.wait(
            {consumer, watcher},
            timeout=definition.wall_time_s,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if consumer in done:
            consumer.result()  # raises what the stream raised
            return None
        stopped = "cancelled" if watcher in done else "timeout"
        with contextlib.suppress(Exception):
            await asyncio.wait_for(backend.interrupt(), timeout=5)
        await _stop(consumer, context.interrupt_grace_s)
        return stopped
    finally:
        for task in (watcher, consumer):
            if not task.done():
                task.cancel()
        await asyncio.gather(watcher, consumer, return_exceptions=True)


def _claim(context: RunContext, run_id: uuid.UUID) -> tuple[Any, int] | None:
    with context.sessions() as db:
        run = runs.claim(db, run_id, context.clock())
        if run is None:
            db.rollback()
            return None
        seq = runs.next_seq(db, run_id)
        runs.append_event(db, run_id, seq, "status", {"status": "running"}, context.clock())
        db.commit()
        return run, seq + 1


@dataclass(frozen=True)
class _Secret:
    kind: CredentialKind
    value: str = field(repr=False)
    checked_at: datetime
    verified: bool


def _chat_prompt(context: RunContext, run: Any) -> chat.ChatPrompt | None:
    with context.sessions() as db:
        return chat.prompt_for_run(db, run)


def _chat_reply(context: RunContext, run_id: uuid.UUID) -> Callable[[Session, runs.Outcome], None]:
    def record(db: Session, outcome: runs.Outcome) -> None:
        text = str((outcome.output or {}).get("text", ""))
        chat.record_reply(db, run_id, text, context.clock())

    return record


def _chat_lock(run_id: uuid.UUID) -> Callable[[Session], None]:
    def lock(db: Session) -> None:
        chat.lock_thread_of_run(db, run_id)

    return lock


def _reveal(context: RunContext, user_id: uuid.UUID, run_id: uuid.UUID) -> _Secret | str:
    """The decrypted credential, or the error message to fail the run with. The kind of the
    credential (not the secret) is written on the run, in the same transaction: the run keeps
    what it used even if the user replaces the credential later."""
    if context.keyring is None:
        return UNREADABLE
    with context.sessions() as db:
        try:
            revealed = credentials.reveal(db, context.keyring, user_id)
        except DecryptError:
            db.rollback()
            return UNREADABLE
        if revealed is None:
            db.rollback()
            return NO_CREDENTIAL
        row = revealed.row
        runs.record_credential_kind(db, run_id, row.kind)
        db.commit()  # also keeps a re-encryption with the current key
        return _Secret(row.kind, revealed.secret, row.updated_at, row.verified_at is not None)


def _mark_verified(context: RunContext, user_id: uuid.UUID, secret: _Secret) -> None:
    with context.sessions() as db:
        if credentials.set_verified_if_unchanged(
            db, user_id, secret.kind, secret.checked_at, context.clock()
        ):
            db.commit()
            logger.info("subscription token verified by a run", extra={"user_id": str(user_id)})
        else:
            db.rollback()


async def execute(context: RunContext, run_id: uuid.UUID) -> RunStatus | None:
    """Run one queued run to its end (module docstring). None when it was not queued."""
    claimed = await asyncio.to_thread(_claim, context, run_id)
    if claimed is None:
        logger.info("agent run skipped: not queued", extra={"run_id": str(run_id)})
        return None
    run, seq = claimed
    recorder = Recorder(context, run_id, seq)
    started = time.monotonic()
    state = _State()
    log = {"run_id": str(run_id), "user_id": str(run.user_id), "agent": run.agent}
    scratch: Path | None = None
    outcome: runs.Outcome | None = None
    secret: _Secret | None = None
    pump: asyncio.Task[None] | None = None
    try:
        try:
            definition = context.definition(run.agent)
        except definitions.UnknownAgentError:
            outcome = runs.Outcome(status=RunStatus.failed, error=UNKNOWN_AGENT)
            return RunStatus.failed
        data = dict(run.input or {})
        variables = {"message": str(data.get("message", "")), "job_id": str(run.job_id or "")}
        if definition.chat:
            # A4: the prompt is rebuilt from the thread's messages; no SDK session is resumed.
            built = await asyncio.to_thread(_chat_prompt, context, run)
            if built is None:
                outcome = runs.Outcome(
                    status=RunStatus.failed, error=THREAD_GONE, model=definition.model
                )
                return RunStatus.failed
            prompt = built.text
            variables.update(
                message=built.current, history=str(built.included), omitted=str(built.omitted)
            )
            recorder.lock_first = _chat_lock(run_id)
            recorder.on_done = _chat_reply(context, run_id)
        else:
            prompt = definition.prompt(data, run.job_id)
        found = await asyncio.to_thread(_reveal, context, run.user_id, run_id)
        if isinstance(found, str):
            outcome = runs.Outcome(status=RunStatus.failed, error=found, model=definition.model)
            return RunStatus.failed
        secret = found
        recorder.secret = secret.value
        scratch = await asyncio.to_thread(make_scratch, context.settings, run_id)
        logger.info("agent run started", extra={**log, "model": definition.model})

        async def denied(name: str) -> None:
            state.denied += 1
            # The name is the model's: scrubbed, then shortened, like everything stored.
            shown = shorten(name, ARG_VALUE_MAX, recorder.secret)
            logger.warning("agent tool denied", extra={**log, "tool": shown})
            await recorder.emit("tool_denied", {"name": name})

        tools = app_tools.build(
            context.sessions,
            run.user_id,
            definition.tools,
            run_id=run_id,
            job_id=run.job_id,
            clock=context.clock,
            job_only=definition.bind_job,
            # What the model writes into a proposal is stored too: same scrub as the events.
            redact=lambda value: scrub(value, recorder.secret),
        )
        stderr_lines: deque[str] = deque(maxlen=50)
        options = build_options(
            definition,
            tools,
            build_env(secret.kind, secret.value, scratch, context.settings.model_api_base_url),
            scratch / "work",
            make_gate(frozenset(tools.allowed_names), denied),
            stderr=stderr_lines.append,
            partial_messages=context.live_store is not None,
        )
        if context.live_store is not None:
            state.live_text = live.LiveText(
                context.live_store,
                run_id,
                recorder.last_seq,
                secret=secret.value,
                redacted=REDACTED,
                cap=TEXT_MAX,
                flush_s=context.live_flush_s,
            )
            pump = asyncio.create_task(state.live_text.pump())
        request = BackendRequest(
            prompt=prompt,
            options=options,
            tools=tools,
            gate=options.hooks["PreToolUse"][0].hooks[0],  # type: ignore[index]
            # Fake backend only: the input's recorded stream, else the one named after the agent.
            script=data.get("script") or definition.name,
            variables=variables,
        )
        backend = context.backend_factory()
        try:
            stopped = await _run_stream(
                context, run_id, definition, backend, request, state, recorder
            )
        except Exception as exc:
            logger.warning(
                "agent run error",
                extra={**log, "error": type(exc).__name__, "stderr_lines": len(stderr_lines)},
            )
            outcome = runs.Outcome(
                status=RunStatus.failed,
                error=error_of(exc),
                model=state.model or definition.model,
                input_tokens=_tokens(state.result)[0],
                output_tokens=_tokens(state.result)[1],
                cost_usd=_cost(state.result.total_cost_usd) if state.result else None,
            )
            return RunStatus.failed
        finally:
            del options, request, prompt
        outcome = outcome_of(state, definition, stopped)
        if (
            outcome.status is RunStatus.done
            and secret.kind is CredentialKind.claude_subscription
            and not secret.verified
        ):
            await asyncio.to_thread(_mark_verified, context, run.user_id, secret)
        return outcome.status
    except asyncio.CancelledError:
        # The worker is stopping (SIGTERM): the run cannot finish, say so, then let go.
        outcome = runs.Outcome(status=RunStatus.failed, error=runs.STOPPED, model=state.model)
        raise
    except Exception:
        logger.exception("agent run crashed", extra=log)
        outcome = runs.Outcome(status=RunStatus.failed, error=INTERNAL, model=state.model)
        return RunStatus.failed
    finally:
        if pump is not None:
            pump.cancel()
        if scratch is not None:
            await asyncio.to_thread(shutil.rmtree, scratch, True)
        if outcome is not None:
            try:
                recorded = await asyncio.shield(recorder.finish(outcome))
            except Exception:
                recorded = False
                logger.exception("agent run end not recorded", extra=log)
            logger.info(
                "agent run finished",
                extra={
                    **log,
                    "status": outcome.status.value,
                    "recorded": recorded,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "input_tokens": outcome.input_tokens,
                    "output_tokens": outcome.output_tokens,
                    "cost_usd": str(outcome.cost_usd) if outcome.cost_usd is not None else None,
                    "denied_tools": state.denied,
                    "live_deltas": state.live_text.deltas if state.live_text else None,
                    "live_dropped": state.live_text.dropped if state.live_text else None,
                    "live_failures": state.live_text.failures if state.live_text else None,
                },
            )
        if state.live_text is not None:
            # Nothing is live once the run ends, whatever the end. After the end is recorded:
            # the stream stops reading the buffer of a run that has ended, and nothing here may
            # keep the final status from being written.
            with contextlib.suppress(Exception):
                await asyncio.shield(state.live_text.aclose())
        secret = None
