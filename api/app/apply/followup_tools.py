"""The two tools of the follow up writer (app/apply/followup_agent.py), registered with
`app.agents.tools.register` (worker only: this module imports the Claude Agent SDK).

Neither takes an id: both work on the open draft the run is bound to (`Scope.binding`), of the
run's person. The submit's arguments go through the run's `redact` before they are checked.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.apply import followup_agent as agent


def _read(scope: app_tools.Scope) -> str | None:
    if scope.binding is None:
        return None
    with scope.sessions() as db:
        try:
            return agent.context(db, scope.user_id, scope.binding, scope.clock())
        except agent.store().RefusedError:
            return None


def _read_follow_up(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        agent.READ,
        "Read what to write and what to write it from: the task, the day of the application, "
        "the follow ups already sent, the posting, the cover letter that was sent and the "
        "person's profile.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    async def read_follow_up(args: dict[str, Any]) -> dict[str, Any]:
        found = await asyncio.to_thread(_read, scope)
        if found is None:
            return app_tools.text_result(agent.GONE, error=True)
        return app_tools.text_result(found)

    return read_follow_up


def _submit(scope: app_tools.Scope, raw: object) -> tuple[str, bool]:
    with scope.sessions() as db:
        out = agent.submit(db, scope.user_id, scope.run_id, scope.binding, raw, scope.clock())
        db.commit()
        return out


def _submit_follow_up(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        agent.SUBMIT,
        "Store the follow up: its subject (one line) and its body (at most 150 words). The "
        "answer says whether it was stored, or what to fix.",
        agent.SUBMIT_SCHEMA,
    )
    async def submit_follow_up(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(_submit, scope, scope.redact(args))
        return app_tools.text_result(text, error=error)

    return submit_follow_up


app_tools.register(agent.READ, _read_follow_up)
app_tools.register(agent.SUBMIT, _submit_follow_up)
