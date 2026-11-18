"""What the runner hands Claude Code for every agent, whatever its definition, in a module the api
can import (the api image holds no Claude Agent SDK, api/Dockerfile): `runner.build_options`
builds the options of a run from `fixed()`, and the Agents tab of Admin (admin_router.py) shows
the same values, so what the page says is what a run gets.

None of these is editable from the tab (agents A6): the overrides of a definition change its
prompt, model, effort, limits, tool switches, first message and description only.
"""

from typing import Any

# The in-process MCP server that serves an agent's app tools (app/agents/tools.py).
SERVER = "app"
# No permission prompt: anything not allowed is denied (the PreToolUse gate denies it first).
PERMISSION_MODE = "dontAsk"

# The agents whose input may lower the run's max_turns (`turn_budget`, validated by their input
# model, never above the definition's): a form fill's profile (apply A18). The agent of a job
# website has no turn cap (jobs J18, app/browser/finder.py).
TURN_BUDGETED = frozenset({"apply_form"})

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

# Claude Code's built in web search (browser B28, owner 2026-11-17): the one built-in tool an
# agent may get, and only an agent whose definition asks for it (`AgentDefinition.web_search`,
# the website search's alone); its results are untrusted text, as a page's. At most
# WEB_SEARCHES_MAX a run: the runner's gate refuses the next ones, and Claude Code's own cap is
# set to the same number (WEB_SEARCHES_ENV). The credential kinds it runs on: an API key, and a
# Claude subscription (the docs do not settle whether a subscription's token may search; a
# search that fails answers the model an error and the run goes on). A kind left out of this
# set gets no web search at all, and the prompt says the tool may be missing.
WEB_SEARCH = "WebSearch"
WEB_SEARCHES_MAX = 20
WEB_SEARCHES_ENV = "CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION"
WEB_SEARCH_KINDS = frozenset({"anthropic_api_key", "claude_subscription"})

# The longest line Claude Code may write to the SDK, one message that may echo a tool's answer:
# the SDK's 1 MiB ended a search on a screenshot (CLIJSONDecodeError, browser B23); still a bound.
MAX_BUFFER_SIZE = 8 * 1024 * 1024

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
        # Claude Code's own extra trust, set only behind a TLS inspecting proxy
        # (compose.extra-ca-run.yml); a path, never a secret.
        "NODE_EXTRA_CA_CERTS",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "https_proxy",
        "http_proxy",
        "no_proxy",
    }
)
# Where the credential goes in the run's Claude Code process, by kind: the name only.
CREDENTIAL_ENV = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "claude_subscription": "CLAUDE_CODE_OAUTH_TOKEN",
}


def full_name(name: str) -> str:
    """An app tool's name as Claude Code sees it."""
    return f"mcp__{SERVER}__{name}"


def fixed(web_search: bool = False) -> dict[str, Any]:
    """The options of every run that no definition changes: no built-in tool (`tools=[]`, and
    each one denied by name), no permission prompt, no settings, CLAUDE.md or skills
    (`setting_sources=[]`), only the MCP servers the runner names, adaptive thinking, and a
    line of at most MAX_BUFFER_SIZE bytes from Claude Code. With `web_search` (B28), WebSearch
    alone is offered and not denied."""
    builtin = [WEB_SEARCH] if web_search else []
    return {
        "max_buffer_size": MAX_BUFFER_SIZE,
        "tools": builtin,
        "disallowed_tools": [name for name in FORBIDDEN_TOOLS if name not in builtin],
        "permission_mode": PERMISSION_MODE,
        "strict_mcp_config": True,
        "setting_sources": [],
        "thinking": {"type": "adaptive"},
    }
