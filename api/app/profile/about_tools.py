"""The one tool of the agent that reads a part of the "About you" document,
`submit_interpretation` (app/profile/about_agent.py), registered with `app.agents.tools.register`
(worker only: this module imports the Claude Agent SDK).

It takes no id of a person: what it stores belongs to the run it is bound to (`Scope.run_id`).
The arguments go through the run's `redact` before they are checked. One submit per run; a
malformed one answers what to fix and stores nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.profile import about_agent


def _submit_interpretation(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        about_agent.SUBMIT,
        "Submit what this part of the document states, once: the fields of its kind, each value "
        "as the text writes it. The answer says whether the reading was stored.",
        about_agent.SUBMIT_SCHEMA,
    )
    async def submit_interpretation(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(about_agent.submit, scope.run_id, scope.redact(args))
        return app_tools.text_result(text, error=error)

    return submit_interpretation


app_tools.register(about_agent.SUBMIT, _submit_interpretation)
