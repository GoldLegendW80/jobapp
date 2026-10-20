"""The sign in hand off as an agent's tool (browser B12b; SPEC-browser, "The sign in hand off"):
`browser_request_signin(site, reason)`, for every agent that drives a browser lease. Worker only:
this module imports the Claude Agent SDK.

The tool takes a site name and a reason, never a credential, and no lease: an agent module that
drives a lease for its runs says how to find the run's one with `hold(find)`, where `find(scope)`
answers a `Hold` for a run of that module and None for any other. Today the form agent of apply
A8 holds one (app/apply/form_tools.py); a later agent with browser tools adds its own `hold` and
names the tool in its definition.

The call goes through app.browser.handoff.request_signin (B12a), so the answer is browserd's:
`nobody_watching` at once on a sweep or full_auto lease or with no live view of the person open,
and nothing started; otherwise the wait for the person, polled until "I'm signed in", Cancel, the
deadline or the run's own budget (`Hold.budget`). The model reads `Outcome.text()`; the agent's
module learns the outcome through `Hold.ended` (the work then waits as `needs_person: signin`
unless the person signed in). `Hold.wall` lets a module ask only when its own read of the page
saw a sign in wall, so a page that asks the assistant to call the tool gets nothing asked.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.browser.client import BrowserError
from app.browser.handoff import Outcome, plain, request_signin
from app.browser.tools import SIGNIN_TOOL, LeaseTools

TOOL = SIGNIN_TOOL
SITE_MAX = 80
REASON_MAX = 300
DESCRIPTION = (
    "Ask the person to sign in or sign up on this site themselves, when the page shows a login "
    "or sign up wall. Give the site's name and one sentence of why. Never ask for, guess or type "
    "a password. Wait for the answer: when it says the person signed in, read the page again; "
    "otherwise stop."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "site": {"type": "string", "minLength": 1, "maxLength": SITE_MAX},
        "reason": {"type": "string", "minLength": 1, "maxLength": REASON_MAX},
    },
    "required": ["site", "reason"],
    "additionalProperties": False,
}
NO_LEASE = "This run has no browser. Stop now."
NO_WALL = "No sign in wall was read on this page: nothing was asked. Read the page again."
BAD_ARGS = "Give the site's name and the reason, as short plain text."


@dataclass(frozen=True)
class Hold:
    """One run's lease, as its agent module lends it to the tool."""

    tools: LeaseTools
    # The seconds the run can still wait, None when it outlives any wait.
    budget: Callable[[], float | None] = lambda: None
    # Whether the module's last read of the page saw a sign in wall.
    wall: Callable[[], bool] = lambda: True
    ended: Callable[[Outcome], None] = lambda outcome: None


Find = Callable[[app_tools.Scope], Hold | None]
_FINDERS: list[Find] = []


def hold(find: Find) -> None:
    """Adds an agent module's way to find its run's lease; the same function twice is kept once."""
    if find not in _FINDERS:
        _FINDERS.append(find)


def _held(scope: app_tools.Scope) -> Hold | None:
    for find in _FINDERS:
        found = find(scope)
        if found is not None:
            return found
    return None


async def ask(held: Hold, args: dict[str, Any]) -> Outcome | str:
    """The call: an Outcome, or the sentence of a refusal before anything reached browserd."""
    site = plain(args.get("site"), SITE_MAX)
    reason = plain(args.get("reason"), REASON_MAX)
    if not site or not reason:
        return BAD_ARGS
    if not held.wall():
        return NO_WALL
    try:
        outcome = await request_signin(held.tools, site, reason, budget_s=held.budget())
    except BrowserError:
        outcome = Outcome("ended")
    held.ended(outcome)
    return outcome


def _signin(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(TOOL, DESCRIPTION, SCHEMA)
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        held = _held(scope)
        if held is None:
            return app_tools.text_result(NO_LEASE, error=True)
        answer = await ask(held, scope.redact(args))
        if isinstance(answer, str):
            return app_tools.text_result(answer, error=True)
        return app_tools.text_result(answer.text(), error=answer.code != "signed_in")

    return handler


app_tools.register(TOOL, _signin)
