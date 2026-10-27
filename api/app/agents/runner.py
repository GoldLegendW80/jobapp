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
   shutdown. An agent whose definition turns live text off (`live_text=False`) is never asked
   for partial messages and publishes nothing. A run that does not end `done` stores no
   answer: text that was only live is gone (the log keeps the `text` event of a cut block
   when Claude Code reports one).

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

What a definition can ask of a run (ingestion G6; none of it applies to an agent that does not
ask for it, and the agents of definitions.py ask for none):

- **A bound run** (`AgentDefinition.bind`). Before the credential is read and before any tool
  exists, the hook says what the run works on, in a transaction of its own. Its answer is the
  tools' `Scope.binding`, read only: no tool takes an id of that thing, so the model cannot
  point the run elsewhere. When the hook answers None the thing is gone, and the run fails with
  BINDING_GONE without a model call.
- **A private run** (`private_io`). A `tool_call` event holds the tool's name and the size of
  its arguments, a `tool_result` event its error state and the size of the answer, never a
  value; a tool name the model made up is not stored either (only the agent's own tools and
  Claude Code's built-in names are names this code knows). The run's `output` holds counts
  (`_State.counts`), never the answer. Nothing of the run is live, whatever `live_text` and
  AGENTS_LIVE_TEXT say: no partial message is asked for and Redis is never written. What a
  failure of such a run raised is logged by its type alone. The `text` events are stored as
  for any run (the model's own words, scrubbed and shortened the same way): whoever owns the
  documents deletes them with the document.
- **A final hook** (`on_final`). Called once this runner has committed the run's final status
  and let go of everything of the run (no transaction open, no lock held, the scratch
  directory and the credential gone), so the hook can take any lock from the top of its own
  lock order. Called for done, cancelled and every failure recorded here; not for a run ended
  elsewhere (cancelled while queued, failed by a reaper or at the enqueue) and not when the
  run's job is cancelled under it: the worker is stopping, or the queue's job timeout has
  passed (arq ends such a job the same way, and the two cannot be told apart here). A missed
  hook is never called again: whoever chains runs also looks on a timer, and that timer is
  the guarantee. The hook has FINAL_HOOK_TIMEOUT_S and holds one of the worker's agent slots
  meanwhile; what it raises is logged and changes nothing of the run.
- **A model per kind of credential** (`model_by_credential`). Chosen when the credential is
  revealed and written on the run in that same transaction, next to `credential_kind`.
- **A token budget** (`token_budget` in the run's input, read for every agent; the input models
  of the agents of definitions.py refuse the key, so only a module that creates its runs
  itself can set one). Absent: no limit. A whole number above zero: the run may spend that many
  tokens (input, cache writes, cache reads and output together). Anything else written there
  (zero, a negative number, null, text) allows no call: the run fails with TOKEN_LIMIT before
  Claude Code is started.

  The count. What each assistant message reports, once per API message id (`Budget`), taken
  the moment the message comes off the stream, by a reader of its own (`_drive_counting`):
  never behind the event writes of the messages before it, because Claude Code does not wait
  for those and can be several requests ahead of a runner that writes slowly. A run without a
  budget keeps the one loop it always had.

  The stop, once the count has reached the budget. It does not depend on any hook:
  - a message that holds a tool call, whatever the tool, means another request: the run is
    interrupted at once (`Budget.stop`), like a cancelled one;
  - where Claude Code asks the gate (the tools it offers, which are the agent's own), the gate
    answers the call with a stop as well (`continue: false`): the tool does not run and the
    CLI ends the turn itself, in order;
  - a request that left with no tool call before it (the pinned CLI goes on by itself after
    an answer the API ended with `max_tokens`) is seen when its first message arrives, and the
    run is interrupted there;
  - a run whose last message used the budget up and asked for nothing more is simply done:
    nothing was cut, and its caller sees what it spent.
  The gate fails closed: when it cannot tell that the message of a call is counted, it stops
  the run, which then fails with INTERNAL, not with TOKEN_LIMIT.

  What it holds (tasks/ingestion/todo.md, Measurements, G6; measured on the pinned CLI). A run
  passes its budget by one request, or two. One, the request whose usage reached the budget,
  which cannot be prevented (its usage is known when its first message arrives), when the
  model then calls a tool of the agent: the CLI waits for the gate, so no request follows.
  Two in two cases. The model calls a tool the CLI does not offer: only the interrupt holds
  the next request back, nothing orders the two, and in 3 of 80 runs against an instant stub
  that request had left. It is cut at once and the CLI drops it, so the run's record does
  not hold it. Or the turn goes on with no tool call: the second request is answered and
  recorded (a third that had left when the interrupt lands was never seen, and is not
  excluded). On top comes the output of the run so far: an assistant message reports its
  input in full and its output as of the start of the answer, so output is only counted when
  the result message arrives.

  What is recorded. A stopped run ends failed with TOKEN_LIMIT, a constant its caller can
  compare. A run with a budget always says what it used: the result message's figures when it
  reports any (kept the moment it arrives, even if the run is cut while its events are still
  being written), else the runner's own count. While it runs, that count is written on the run
  with each of its events, so a run no runner ends (the worker killed, a reaper) keeps it.
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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
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

# The messages stored on a run (they reach the user) and what a caller may conclude from one
# live in a module the api can import too; they are this module's names as before.
from app.agents.failures import (
    ANSWER_NOT_STORED,
    API_ERROR,
    BINDING_GONE,
    BUSY,
    FAILED,
    INTERNAL,
    NO_CLI,
    NO_CREDENTIAL,
    NO_CREDIT,
    NO_RESULT,
    NO_START,
    PROCESS,
    REFUSED,
    THREAD_GONE,
    TIME_LIMIT,
    TOKEN_LIMIT,
    TURN_LIMIT,
    UNKNOWN_AGENT,
    UNREADABLE,
    UNUSABLE,
)
from app.agents.failures import is_box_failure as is_box_failure
from app.agents.failures import is_provider_failure as is_provider_failure
from app.agents.models import CredentialKind, RunStatus
from app.config import Settings
from app.logging_config import hidden as hidden_from_logs

logger = logging.getLogger("app.agents.runner")

CANCEL_POLL_S = 0.5
INTERRUPT_GRACE_S = 10.0
# How long a definition's on_final hook may take before the runner moves on.
FINAL_HOOK_TIMEOUT_S = 30.0
# How long the gate waits for the assistant message of a tool call to be counted against the
# run's token budget before it decides with what is counted so far (`Budget.counted`).
GATE_WAIT_S = 5.0
# How long the end of a chat run waits for its thread's row (seconds, Postgres `lock_timeout`).
# The row is held for a moment by a send or a delete of the same thread; a session that holds
# it for longer than this is stuck, and the worker must not be stuck with it (issue #37).
THREAD_LOCK_WAIT_S = 10.0
# How long the end of a run waits for its cancelled live text pump (seconds). The pump ends at
# once when it is cancelled; the bound is for a store call that does not let itself be stopped,
# which must not keep the run's final status from being written (issue #90).
PUMP_STOP_S = 2.0
TEXT_MAX = 20_000
TOOL_RESULT_MAX = 2_000
ARG_VALUE_MAX = 200
ARGS_MAX = 1_000
REDACTED = "[redacted]"
# In place of a tool name the model made up, in the events and the log of a private run.
NOT_STORED = "[not stored]"
# The key of a run's input that holds its token budget, and what Claude Code is told when the
# gate stops a run on it, or because it could not tell whether the budget was reached.
BUDGET_KEY = "token_budget"
BUDGET_STOP = "The run reached its token budget."
BUDGET_UNSURE = "The run's token count could not be confirmed."
# The agents whose input may lower the run's max_turns (`turn_budget`, validated by their input
# model, never above the definition's): a form fill's profile (apply A18) and the native search
# of a job website, whose searches share its turns (app/browser/finder.py).
TURN_BUDGETED = frozenset({"apply_form", "browser_discovery"})

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
    # How long one attempt on the live store may take, and how long it is left alone after a
    # failed one (app/agents/live.py: a Redis that does not answer must not slow the run).
    live_timeout_s: float = live.STORE_TIMEOUT_S
    live_backoff_s: float = live.WRITE_BACKOFF_S
    # Handed to a definition's on_final hook as `FinalRun.services`: what the worker has that a
    # hook may need and this runner does not (its queue). Empty until the worker fills it.
    services: Mapping[str, Any] = field(default_factory=dict)
    final_hook_timeout_s: float = FINAL_HOOK_TIMEOUT_S
    gate_wait_s: float = GATE_WAIT_S
    thread_lock_wait_s: float = THREAD_LOCK_WAIT_S
    pump_stop_s: float = PUMP_STOP_S

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
    cut a credential in two, and the part that is kept would be stored as it is.

    Two copies that overlap: a credential whose end repeats its own beginning can stand twice
    in a text with the two copies sharing those characters. The first copy is replaced and the
    rest of the second stays, a part of the credential without its beginning. This cannot
    happen with a real credential: after their fixed prefix, which never comes back inside,
    tokens and keys are random characters. It is said here so that nobody reads "every
    occurrence" as more than it is.

    The secret is never a short one: `_reveal` refuses a credential under
    `credentials.MIN_LENGTH`, because replacing a few characters everywhere would mangle
    everything a run stores."""
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


def size_of(args: Any) -> int:
    """How large a tool call's arguments are: the characters of their compact JSON (of their
    text, for something JSON cannot write: a size is never a reason to fail a run). What a
    private run stores of them."""
    try:
        return len(json.dumps(args, ensure_ascii=False, default=str, separators=(",", ":")))
    except (TypeError, ValueError):
        return len(str(args))


def result_size(content: Any) -> tuple[int, int]:
    """How large a tool result is: the characters of its text, and how many of its content
    blocks are something else (an image). What a private run stores of it."""
    if content is None:
        return 0, 0
    if isinstance(content, str):
        return len(content), 0
    if isinstance(content, list):
        chars = other = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                chars += len(str(block.get("text", "")))
            else:
                other += 1
        return chars, other
    return len(str(content)), 0


def token_budget(data: Mapping[str, Any]) -> int | None:
    """The token budget a run's input gives it. None when the input names none: no limit. A
    whole number above zero is the budget. Anything else written under the key (zero, a
    negative number, null, a fraction, text, true) is read as zero, and a budget of zero allows
    no call at all: whoever computes budgets by subtraction must never get an unlimited run out
    of a wrong one."""
    if BUDGET_KEY not in data:
        return None
    value = data[BUDGET_KEY]
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


class Budget:
    """What a run has used of its token budget, counted from the usage Claude Code reports on
    each assistant message, the moment the message comes off the stream (`_drive`): never
    behind the event writes of the messages before it (module docstring, A token budget).

    One count per API message: Claude Code prints one assistant message per content block, each
    with the usage of the whole API message and its id, so the figures of an id are counted
    once (the largest seen of each side). A message without an id cannot be recognised when it
    comes again, so each one counts on its own: too much rather than too little.

    `stop` is set, and the run then interrupted, as soon as the count has reached the budget
    and the turn is seen to go on: the message holds a tool call, whatever the tool (Claude
    Code answers a call to a tool it does not offer by itself, without asking the gate, and
    sends its next request at once), or a new API message arrives when nothing was left to
    spend. `refused` is set when the gate stopped a tool call on a used up budget, `unsure`
    when it stopped one because it could not tell that the call's message was counted."""

    def __init__(self, limit: int, wait_s: float = GATE_WAIT_S) -> None:
        self.limit = limit
        self.stop = asyncio.Event()
        self.refused = False
        self.unsure = False
        self._wait_s = wait_s
        self._by_message: dict[str, tuple[int, int]] = {}
        self._calls: set[str] = set()
        self._arrived = asyncio.Event()

    @property
    def requests(self) -> int:
        """API messages counted so far."""
        return len(self._by_message)

    @property
    def input_tokens(self) -> int:
        return sum(side for side, _ in self._by_message.values())

    @property
    def output_tokens(self) -> int:
        return sum(out for _, out in self._by_message.values())

    @property
    def spent(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def used_up(self) -> bool:
        """No further call is allowed: what was spent has reached the budget."""
        return self.spent >= self.limit

    def count(self, message: AssistantMessage) -> None:
        """Count an assistant message, and stop the run when nothing is left and the turn goes
        on. Never waits, on the database or on anything else: it is called as the message
        comes off the stream, and the gate may be asked about a tool call of this message at
        any moment."""
        key = message.message_id
        if key is None or key not in self._by_message:
            if self.used_up:
                # A request left although nothing was left to spend: stop the run now.
                self.stop.set()
            if key is None:
                key = f"no id {len(self._by_message)}"
        usage = message.usage or {}
        side = (
            _int(usage.get("input_tokens"))
            + _int(usage.get("cache_creation_input_tokens"))
            + _int(usage.get("cache_read_input_tokens"))
        )
        out = _int(usage.get("output_tokens"))
        before = self._by_message.get(key, (0, 0))
        self._by_message[key] = (max(before[0], side), max(before[1], out))
        calls = [block.id for block in message.content if isinstance(block, ToolUseBlock)]
        self._calls.update(calls)
        if calls and self.used_up:
            # A tool call means another request, and nothing is left to pay for it. Not left to
            # the gate alone: the gate is only asked about the tools Claude Code offers.
            self.stop.set()
        self._arrived.set()

    async def counted(self, tool_use_id: str | None) -> bool:
        """Whether the assistant message that made this tool call has been counted, waiting
        for it up to `wait_s`. The SDK hands a hook request to a task of its own as soon as it
        has read it, while the message printed just before it may still sit in the SDK's
        buffer: without the wait the gate could decide before the usage that uses the budget
        up is counted. The message is counted when it comes off the stream, so the wait covers
        that buffer alone, never the database. False when the wait ends without the message,
        or when the hook names no call at all: the gate then stops the run (never seen in the
        measurements, where the message always came first and the hook always named its
        call)."""
        if tool_use_id is None:
            return False
        try:
            async with asyncio.timeout(self._wait_s):
                while tool_use_id not in self._calls:
                    self._arrived.clear()
                    await self._arrived.wait()
        except TimeoutError:
            return False
        return True


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
            # A sign in hand off waits in one tool call: BROWSER_SESSION_MAX_S (at most 7200 s).
            "MCP_TOOL_TIMEOUT": str((7200 + 600) * 1000),
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


def make_gate(
    allowed: frozenset[str],
    on_denied: Callable[[str], Awaitable[None]],
    budget: Budget | None = None,
) -> Gate:
    """The PreToolUse hook: any tool outside the allow list is denied by this code, whatever
    the options or the model say (hooks run first, and a hook deny holds in every mode).

    For a run with a token budget it is also one of the two places where the run stops (the
    other is `Budget.count`, which interrupts the run and needs no hook). Once the budget is
    used up, a tool call the gate is asked about is answered with a stop (`continue: false`, a
    documented hook answer). The tool does not run and Claude Code ends the turn itself, in
    order, with no further request (measured on the pinned CLI: the result's terminal reason is
    `hook_stopped`). Claude Code only asks about the tools it offers, which here are the
    agent's own: a call to any other name it answers by itself. A hook that merely never
    answered would not hold: when the wait for it ends without an answer (the client is
    closed), Claude Code goes on as if nothing had been asked (measured too).

    The gate fails closed: when it cannot tell that the message of the call is counted (the
    wait of `Budget.counted` ended, or the hook named no call), it stops the run as well,
    instead of letting the call through on a count that may be behind."""

    def stop(reason: str) -> dict[str, Any]:
        return {
            "continue_": False,
            "stopReason": reason,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }

    async def gate(
        input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        name = str(input_data.get("tool_name", ""))
        if budget is not None:
            if not await budget.counted(tool_use_id):
                budget.unsure = True
                budget.stop.set()
                return stop(BUDGET_UNSURE)
            if budget.used_up:
                budget.refused = True
                return stop(BUDGET_STOP)
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
    model: str | None = None,
    turn_budget: int | None = None,
) -> ClaudeAgentOptions:
    """`model` is the one chosen for the run's credential (AgentDefinition.model_for); the
    definition's own when none is given."""
    if turn_budget is not None and (type(turn_budget) is not int or turn_budget < 1):
        raise ValueError("turn_budget must be a positive integer")
    turns = definition.max_turns
    if turn_budget is not None:
        turns = min(turns, turn_budget) if turns is not None else turn_budget
    return ClaudeAgentOptions(
        system_prompt=definition.system_prompt,
        tools=[],
        allowed_tools=tools.allowed_names,
        disallowed_tools=list(FORBIDDEN_TOOLS),
        permission_mode="dontAsk",
        mcp_servers={app_tools.SERVER: tools.server},
        strict_mcp_config=True,
        setting_sources=[],
        model=model or definition.model,
        max_turns=turns,
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
    # A private run (AgentDefinition.private_io): tool events hold names and sizes, never a
    # value, and only the tool names this code knows are stored.
    private: bool = False
    known_tools: frozenset[str] = frozenset(FORBIDDEN_TOOLS)
    # A run with a token budget: what it has counted so far goes on the run row with each of
    # its events (runs.record_tokens), so that a run which never records its own end (the
    # worker is killed, a reaper fails the run) still says what it had used by then.
    budget: Budget | None = field(default=None, repr=False)
    _noted: tuple[int, int] = (0, 0)
    _owed: tuple[int, int] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def last_seq(self) -> int:
        """The seq of the newest event of this run (written, or being written)."""
        return self.seq - 1

    def tool_name(self, name: str) -> str:
        """A tool's name as it may be stored and logged. The name is the model's: in a private
        run only a name this code knows is kept (the agent's own tools, Claude Code's built-in
        ones); any other could be a piece of what the run read."""
        return name if not self.private or name in self.known_tools else NOT_STORED

    def tool_call(self, block: ToolUseBlock) -> dict[str, Any]:
        """The `tool_call` event of a tool use block."""
        if self.private:
            return {
                "id": block.id,
                "name": self.tool_name(block.name),
                "input_size": size_of(block.input),
            }
        return {
            "id": block.id,
            "name": block.name,
            "input": summarize_args(block.input, self.secret),
        }

    def tool_result(self, block: ToolResultBlock) -> dict[str, Any]:
        """The `tool_result` event of a tool result block."""
        data: dict[str, Any] = {"id": block.tool_use_id, "is_error": bool(block.is_error)}
        if self.private:
            data["size"], data["blocks"] = result_size(block.content)
        else:
            data["content"] = shorten(result_text(block.content), TOOL_RESULT_MAX, self.secret)
        return data

    def _write(self, seq: int, kind: str, data: dict[str, Any]) -> None:
        with self.context.sessions() as db:
            runs.append_event(db, self.run_id, seq, kind, data, self.context.clock())
            owed = self._owed
            if owed is not None:
                runs.record_tokens(db, self.run_id, *owed)
            db.commit()

    async def emit(self, kind: str, data: dict[str, Any]) -> None:
        clean = scrub(data, self.secret)
        async with self._lock:
            # Taken before the write: a cancelled await leaves the write running in its thread,
            # and the next event must not reuse its seq.
            seq, self.seq = self.seq, self.seq + 1
            if self.budget is not None:
                # Read here, in the loop, where the count moves; written with the event. It
                # is behind the count by the events still to write, never ahead of it.
                counted = (self.budget.input_tokens, self.budget.output_tokens)
                self._owed = counted if counted != self._noted else None
            await asyncio.to_thread(self._write, seq, kind, clean)
            if self._owed is not None:
                self._noted, self._owed = self._owed, None

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
    # An assistant message came marked `billing_error`: the credential has no credit left.
    no_credit: bool = False
    live_text: live.LiveText | None = None
    # The kind of credential the run uses (never the secret), for the `usage` event.
    credential_kind: str | None = None
    # The run's token budget and what it has used of it; None: no limit, nothing counted.
    budget: Budget | None = None
    # What the run did, in numbers: the output of a private run.
    text_blocks: int = 0
    text_chars: int = 0
    tool_calls: int = 0
    tool_errors: int = 0

    def counts(self) -> dict[str, int]:
        """The run in numbers only: what a private run stores as its output."""
        return {
            "turns": _int(self.result.num_turns) if self.result is not None else 0,
            "text_blocks": self.text_blocks,
            "text_chars": self.text_chars,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "tools_denied": self.denied,
        }


async def _handle(message: Any, state: _State, recorder: Recorder) -> None:
    if isinstance(message, StreamEvent):
        # Partial messages: only the text deltas of a text block are read (live.LiveText.feed),
        # and only those of the run's own session: an event of a sub agent (it names the tool
        # call it runs under) is never published. The SDK documents the field as always None,
        # and no agent here may start a sub agent: this holds if either ever changes.
        if state.live_text is not None and message.parent_tool_use_id is None:
            state.live_text.feed(message.event)
    elif isinstance(message, AssistantMessage):
        # (A run with a token budget has counted this message already, when it came off the
        # stream: `_drive`. Nothing is counted here, behind the event writes.)
        state.model = message.model or state.model
        for block in message.content:
            if isinstance(block, TextBlock):
                if block.text:
                    state.last_text = block.text
                    state.text_blocks += 1
                    state.text_chars += len(block.text)
                    stored = shorten(block.text, TEXT_MAX, recorder.secret)
                    await recorder.emit("text", {"text": stored})
                if state.live_text is not None:
                    # The complete text is stored: it replaces the live one.
                    await state.live_text.close_block()
            elif isinstance(block, ThinkingBlock):
                await recorder.emit("thinking", {})
            elif isinstance(block, ToolUseBlock):
                state.tool_calls += 1
                await recorder.emit("tool_call", recorder.tool_call(block))
        if message.error:
            if message.error == "billing_error":
                state.no_credit = True
            await recorder.emit("api_error", {"error": str(message.error)})
    elif isinstance(message, UserMessage):
        if isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    state.tool_errors += bool(block.is_error)
                    await recorder.emit("tool_result", recorder.tool_result(block))
    elif isinstance(message, ResultMessage):
        state.result = message
        # The kind goes with the amount: the browser words the amount by kind (a cost, or an
        # estimate on a subscription) and has it in the same step, without waiting for the
        # run's detail to be read again (issue #80: the label flickered until then).
        await recorder.emit(
            "usage", {**usage_of(message), "credential_kind": state.credential_kind}
        )
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
    state: _State,
    definition: definitions.AgentDefinition,
    stopped: str | None,
    model: str | None = None,
) -> runs.Outcome:
    """How the run ended. `stopped` is what `_run_stream` returned; `model` the one chosen for
    the run's credential (the definition's own when none is given)."""
    result = state.result
    input_tokens, output_tokens = _tokens(result)
    usage = {
        "model": state.model or model or definition.model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _cost(result.total_cost_usd) if result is not None else None,
    }
    if stopped == "cancelled":
        return runs.Outcome(status=RunStatus.cancelled, **usage)
    if stopped == "timeout":
        message = TIME_LIMIT.format(minutes=_minutes(definition.wall_time_s))
        return runs.Outcome(status=RunStatus.failed, error=message, **usage)
    budget = state.budget
    if budget is not None and budget.unsure:
        # The gate stopped a call because it could not tell that the count was complete. Not
        # the budget's end (nothing says it was reached): the runner's own failure.
        return runs.Outcome(status=RunStatus.failed, error=INTERNAL, **usage)
    if budget is not None and (
        # The gate stopped a tool call: Claude Code then ends the turn with a result that says
        # "success" (terminal reason hook_stopped), and the run is not finished for all that.
        budget.refused
        # Interrupted because nothing was left and the turn went on. When the request that
        # followed turned out to be the run's last (a clean result came before the interrupt
        # landed), nothing was cut.
        or (stopped == "budget" and (result is None or result.is_error))
    ):
        return runs.Outcome(status=RunStatus.failed, error=TOKEN_LIMIT, **usage)
    if result is None:
        return runs.Outcome(status=RunStatus.failed, error=NO_RESULT, **usage)
    if result.subtype == "error_max_turns":
        message = TURN_LIMIT.format(turns=definition.max_turns)
        return runs.Outcome(status=RunStatus.failed, error=message, **usage)
    if result.is_error:
        status = result.api_error_status
        if state.no_credit:
            # Before the status: a balance that is too low comes as HTTP 400, which read alone
            # is the run's own content, and a chain would fail every remaining step on it.
            message = NO_CREDIT
        elif status in (401, 403):
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
        if state.budget is None:
            async for message in messages:
                await _handle(message, state, recorder)
        else:
            await _drive_counting(messages, state.budget, state, recorder)


async def _drive_counting(messages: Any, budget: Budget, state: _State, recorder: Recorder) -> None:
    """The loop of a run with a token budget: the count ahead, the events behind.

    Claude Code does not wait for this runner. It prints a message and goes on, and it makes
    its next request as soon as it has the answers it needs, which for a call to a tool it does
    not offer is at once. A loop that counts a message when it gets to it, after the event
    writes of the messages before (one commit each), is behind by as many requests as the
    database is slow, and so is everything that reads the count: the gate, the stop, the
    figures recorded on a run that is cut (review of PR #95, measured on the pinned CLI).

    So a reader of its own takes each message off the stream the moment it is there, counts an
    assistant message against the budget (`Budget.count`, which stops the run when nothing is
    left and never waits), keeps the result message, and hands the messages on in order; the
    events are written behind it, at the pace of the database. A run without a budget keeps the
    one loop it always had."""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    end = object()

    async def read() -> None:
        try:
            async for message in messages:
                if isinstance(message, AssistantMessage):
                    budget.count(message)
                elif isinstance(message, ResultMessage):
                    # What the run used in all: kept even when the run is cut while its
                    # events are still being written.
                    state.result = message
                queue.put_nowait(message)
        finally:
            queue.put_nowait(end)

    reader = asyncio.create_task(read())
    try:
        while (message := await queue.get()) is not end:
            await _handle(message, state, recorder)
        await reader  # raises what the stream raised
    finally:
        if not reader.done():
            reader.cancel()
        # Awaited inside the caller's `aclosing`: the stream's `finally` runs in the reader.
        await asyncio.gather(reader, return_exceptions=True)


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
    """Run the stream to its end, or stop it. Returns None (ran to its end), "cancelled",
    "budget" (the run's token budget is used up and the turn was going on: `Budget.stop`) or
    "timeout". Exceptions of the stream propagate."""
    consumer = asyncio.create_task(_drive(backend, request, state, recorder))
    watcher = asyncio.create_task(_watch_cancel(context, run_id))
    tasks: list[asyncio.Task[Any]] = [watcher, consumer]
    limiter: asyncio.Task[Any] | None = None
    if state.budget is not None:
        limiter = asyncio.create_task(state.budget.stop.wait())
        tasks.append(limiter)
    try:
        done, _ = await asyncio.wait(
            tasks, timeout=definition.wall_time_s, return_when=asyncio.FIRST_COMPLETED
        )
        if consumer in done:
            consumer.result()  # raises what the stream raised
            return None
        # The user's cancel first, then the budget, then the clock.
        stopped = "cancelled" if watcher in done else "budget" if limiter in done else "timeout"
        with contextlib.suppress(Exception):
            await asyncio.wait_for(backend.interrupt(), timeout=5)
        await _stop(consumer, context.interrupt_grace_s)
        return stopped
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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


async def _record_end(
    recorder: Recorder, outcome: runs.Outcome, log: dict[str, Any]
) -> tuple[runs.Outcome, bool]:
    """Write the run's end: the outcome written, and whether it was (False: the run was no
    longer running). A chat answer whose thread stays locked past its bound (`_chat_lock`) is
    not stored: the run is failed at once instead, with ANSWER_NOT_STORED and what it used, in
    a transaction of its own that writes the run's row and its last event and locks no thread
    (the lock order of chat.py holds). The thread takes the next message as soon as its holder
    lets go, instead of after the stale reaper."""
    try:
        return outcome, await asyncio.shield(recorder.finish(outcome))
    except chat.ThreadLockedError:
        failed = replace(outcome, status=RunStatus.failed, error=ANSWER_NOT_STORED, output=None)
    logger.warning("agent run answer not stored: its thread stayed locked", extra=log)
    return failed, await asyncio.shield(recorder.finish(failed))


def _chat_lock(run_id: uuid.UUID, wait_s: float) -> Callable[[Session], None]:
    """Lock the thread of a chat run for the transaction that ends the run, waiting `wait_s`
    at most. The bound is the transaction's own (`SET LOCAL`): it also holds for the run's row
    that the same transaction locks next, and is gone with the transaction. When it passes on
    the thread, Postgres refuses the lock and chat.ThreadLockedError is raised, the transaction
    rolled back: `_record_end` then fails the run at once without the thread, and the worker is
    free again."""

    def lock(db: Session) -> None:
        with chat.bounded_lock_waits(db, wait_s):
            chat.lock_thread_of_run(db, run_id)

    return lock


def _reveal(
    context: RunContext,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    definition: definitions.AgentDefinition,
) -> _Secret | str:
    """The decrypted credential, or the error message to fail the run with. The kind of the
    credential (not the secret) is written on the run, in the same transaction: the run keeps
    what it used even if the user replaces the credential later. For an agent that names a
    model per kind of credential, the model of that kind is written with it: the run was
    created with the model of whatever credential the user had then, or of none."""
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
        if not credentials.has_shape(revealed.secret):
            # Checked when it was saved (credentials.clean_secret), and again here, where the
            # scrub starts to rely on it: a row written any other way never reaches a run.
            db.rollback()
            return UNUSABLE
        row = revealed.row
        chosen = definition.model_for(row.kind) if definition.model_by_credential else None
        runs.record_credential_kind(db, run_id, row.kind, chosen)
        db.commit()  # also keeps a re-encryption with the current key
        return _Secret(row.kind, revealed.secret, row.updated_at, row.verified_at is not None)


def frozen(value: Any) -> Any:
    """A read only copy of `value`, at every depth: a mapping becomes a read only view of a
    copy, a list or a tuple a tuple, a set a frozenset, each with its items frozen the same
    way. Anything else is kept as it is: text, numbers, ids, dates, None, which nothing can
    change in place (a hook that answers with another kind of object answers for it)."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: frozen(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(frozen(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(frozen(item) for item in value)
    return value


def _bind(context: RunContext, bind: definitions.Bind, run: Any) -> Mapping[str, Any] | None:
    """What the run is bound to, as its definition's hook reads it now, in a transaction of
    its own that ends here (the hook reads; nothing it changed would be kept). A read only
    copy, at every depth (`frozen`): the tools share it and none can change what another
    reads, a list of positions inside it no more than the mapping itself."""
    with context.sessions() as db:
        found = bind(db, run)
        if found is None:
            return None
        made: Mapping[str, Any] = frozen(dict(found))
        return made


# Log lines of a private run that wait until the failure they are about is no longer being
# handled: (level, message, extra).
_Later = list[tuple[int, str, dict[str, Any]]]


def _log_failure(what: str, log: dict[str, Any], exc: BaseException, later: _Later | None) -> None:
    """An unexpected failure. For a public run (`later` is None): logged here and now, with
    its traceback. For a private run: its type alone, since an exception's message can quote
    what the run read, and not from here but through `_log_later`, once the exception is no
    longer being handled. While it is, anything that goes wrong inside the logging itself (a
    handler that cannot write) is reported by Python together with the exception being
    handled, message included: seen in the test suite, where it printed a test's document."""
    if later is None:
        logger.error(what, extra=log, exc_info=exc)
    else:
        later.append((logging.ERROR, what, {**log, "error": type(exc).__name__}))


def _log_later(later: _Later) -> None:
    """Write the lines `_log_failure` kept back. Called outside every `except` block."""
    while later:
        level, what, extra = later.pop(0)
        logger.log(level, what, extra=extra)


async def _final(
    context: RunContext,
    hook: definitions.OnFinal,
    private: bool,
    run: Any,
    status: RunStatus,
    log: dict[str, Any],
) -> None:
    """Call a definition's on_final hook (module docstring, A final hook). The run's end is
    committed and nothing of the run is held; whatever the hook does or raises, the run stays
    as it was recorded."""
    final = definitions.FinalRun(
        run_id=run.id,
        user_id=run.user_id,
        agent=run.agent,
        status=status,
        sessions=context.sessions,
        clock=context.clock,
        settings=context.settings,
        services=context.services,
    )
    later: _Later = []
    try:
        async with asyncio.timeout(context.final_hook_timeout_s):
            await hook(final)
    except Exception as exc:
        _log_failure("agent final hook failed", log, exc, later if private else None)
    _log_later(later)


def _complete(
    outcome: runs.Outcome, state: _State, definition: definitions.AgentDefinition | None
) -> runs.Outcome:
    """The outcome as it is recorded, whatever path made it. A run with a token budget always
    says what it used: the result message's figures when it reports any, else the runner's own
    count (a run cut before its result, or whose result carries none). A private run's output
    is its counts, never its answer."""
    budget = state.budget
    if budget is not None and not (outcome.input_tokens or 0) + (outcome.output_tokens or 0):
        outcome = replace(
            outcome, input_tokens=budget.input_tokens, output_tokens=budget.output_tokens
        )
    if definition is not None and definition.private_io:
        outcome = replace(outcome, output=state.counts())
    return outcome


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
    """Run one queued run to its end (module docstring). None when it was not queued.

    From the moment the credential is decrypted to the end of this call, it is replaced in
    every log record of the process, whoever logs (app/logging_config.py): the SDK, the HTTP
    client and the queue log on their own, outside the scrub of what the run stores."""
    with contextlib.ExitStack() as held:
        return await _execute(context, run_id, held)


async def _execute(
    context: RunContext, run_id: uuid.UUID, held: contextlib.ExitStack
) -> RunStatus | None:
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
    definition: definitions.AgentDefinition | None = None
    recorded = False
    stopping = False
    # What a private run has to say about its failures, written once they are handled.
    later: _Later = []
    try:
        try:
            definition = context.definition(run.agent)
        except definitions.UnknownAgentError:
            outcome = runs.Outcome(status=RunStatus.failed, error=UNKNOWN_AGENT)
            return RunStatus.failed
        recorder.private = definition.private_io
        # The definition's model until the credential says which one the run uses.
        model = definition.model
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
            recorder.lock_first = _chat_lock(run_id, context.thread_lock_wait_s)
            recorder.on_done = _chat_reply(context, run_id)
        else:
            prompt = definition.prompt(data, run.job_id)
        binding: Mapping[str, Any] | None = None
        if definition.bind is not None:
            # What the run works on, decided here, in code, before a tool or a model call.
            binding = await asyncio.to_thread(_bind, context, definition.bind, run)
            if binding is None:
                outcome = runs.Outcome(status=RunStatus.failed, error=BINDING_GONE, model=model)
                return RunStatus.failed
        found = await asyncio.to_thread(_reveal, context, run.user_id, run_id, definition)
        if isinstance(found, str):
            outcome = runs.Outcome(status=RunStatus.failed, error=found, model=model)
            return RunStatus.failed
        secret = found
        held.enter_context(hidden_from_logs(secret.value))
        recorder.secret = secret.value
        state.credential_kind = secret.kind.value
        model = definition.model_for(secret.kind)
        limit = token_budget(data)
        if limit is not None:
            if limit <= 0:
                # No call is allowed: the run ends here, before Claude Code is started.
                outcome = runs.Outcome(status=RunStatus.failed, error=TOKEN_LIMIT, model=model)
                return RunStatus.failed
            state.budget = recorder.budget = Budget(limit, context.gate_wait_s)
        scratch = await asyncio.to_thread(make_scratch, context.settings, run_id)
        logger.info("agent run started", extra={**log, "model": model})

        async def denied(name: str) -> None:
            state.denied += 1
            # The name is the model's: scrubbed, then shortened, like everything stored (and
            # replaced in a private run, where it could quote what the run read).
            name = recorder.tool_name(name)
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
            binding=binding,
        )
        recorder.known_tools = frozenset(tools.allowed_names) | frozenset(FORBIDDEN_TOOLS)
        stderr_lines: deque[str] = deque(maxlen=50)
        # Live text needs a store (AGENTS_LIVE_TEXT, Redis) and an agent that allows it: never
        # one that reads private documents.
        live_store = context.live_store if definition.live else None
        options = build_options(
            definition,
            tools,
            build_env(secret.kind, secret.value, scratch, context.settings.model_api_base_url),
            scratch / "work",
            make_gate(frozenset(tools.allowed_names), denied, state.budget),
            stderr=stderr_lines.append,
            partial_messages=live_store is not None,
            model=model,
            turn_budget=data.get("turn_budget") if definition.name in TURN_BUDGETED else None,
        )
        if live_store is not None:
            state.live_text = live.LiveText(
                live_store,
                run_id,
                recorder.last_seq,
                secret=secret.value,
                redacted=REDACTED,
                cap=TEXT_MAX,
                flush_s=context.live_flush_s,
                timeout_s=context.live_timeout_s,
                backoff_s=context.live_backoff_s,
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
            what = {**log, "error": type(exc).__name__, "stderr_lines": len(stderr_lines)}
            if recorder.private:
                later.append((logging.WARNING, "agent run error", what))
            else:
                logger.warning("agent run error", extra=what)
            outcome = runs.Outcome(
                status=RunStatus.failed,
                error=error_of(exc),
                model=state.model or model,
                input_tokens=_tokens(state.result)[0],
                output_tokens=_tokens(state.result)[1],
                cost_usd=_cost(state.result.total_cost_usd) if state.result else None,
            )
            return RunStatus.failed
        finally:
            del options, request, prompt
        outcome = outcome_of(state, definition, stopped, model)
        if state.budget is not None and state.budget.unsure:
            logger.error("agent run stopped: its token count could not be confirmed", extra=log)
        if (
            outcome.status is RunStatus.done
            and secret.kind is CredentialKind.claude_subscription
            and not secret.verified
        ):
            await asyncio.to_thread(_mark_verified, context, run.user_id, secret)
        return outcome.status
    except asyncio.CancelledError:
        # The worker is stopping (SIGTERM): the run cannot finish, say so, then let go.
        stopping = True
        outcome = runs.Outcome(status=RunStatus.failed, error=runs.STOPPED, model=state.model)
        raise
    except Exception as exc:
        _log_failure("agent run crashed", log, exc, later if recorder.private else None)
        outcome = runs.Outcome(status=RunStatus.failed, error=INTERNAL, model=state.model)
        return RunStatus.failed
    finally:
        _log_later(later)
        if pump is not None:
            # Cancelled and awaited: the pump may be in the middle of a write, and the clear
            # below must come after it, not next to it. Bounded: a store call that ignores
            # the cancellation must not hold the end of the run (the clear is bounded too).
            pump.cancel()
            stopped, _ = await asyncio.wait({pump}, timeout=context.pump_stop_s)
            if not stopped:
                # Left running: once closed below, it starts no write and clears behind its call.
                logger.warning("live text pump did not stop", extra=log)
            elif not pump.cancelled():
                pump.exception()  # looked at, so that asyncio does not report it at exit
        if scratch is not None:
            await asyncio.to_thread(shutil.rmtree, scratch, True)
        if outcome is not None:
            # What a run with a budget used, and the counts of a private run, on every path.
            outcome = _complete(outcome, state, definition)
            try:
                outcome, recorded = await _record_end(recorder, outcome, log)
            except Exception as exc:
                recorded = False
                _log_failure(
                    "agent run end not recorded", log, exc, later if recorder.private else None
                )
            _log_later(later)
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
                    "live_slow": state.live_text.slow if state.live_text else None,
                },
            )
        if state.live_text is not None:
            # Nothing is live once the run ends, whatever the end. After the end is recorded:
            # the stream stops reading the buffer of a run that has ended, and nothing here may
            # keep the final status from being written.
            with contextlib.suppress(Exception):
                await asyncio.shield(state.live_text.aclose())
        secret = None
        if (
            outcome is not None
            and recorded
            and not stopping
            and definition is not None
            and definition.on_final is not None
        ):
            # Last: the end is committed and nothing of the run is held any more. Not for an
            # end someone else recorded, and not while the worker is stopping.
            await _final(
                context, definition.on_final, definition.private_io, run, outcome.status, log
            )
