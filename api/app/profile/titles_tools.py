"""The one tool of the job titles agent, `submit_titles` (app/profile/agents.py), registered
with `app.agents.tools.register` (worker only: this module imports the Claude Agent SDK).

It takes no id of a person: what it stores belongs to the run it is bound to (`Scope.run_id`),
and `agents.execute` reads it back when that run has ended. The arguments go through the run's
`redact` (the scrub of the credential) before they are checked. One submit per run; a malformed
one answers what to fix and stores nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.profile import agents


def _submit_titles(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        agents.SUBMIT,
        "Submit your job title suggestions, once: at most eight entries, each a title, its one "
        "line reason and the two letter code of the title's language. The answer says whether "
        "they were stored.",
        agents.SUBMIT_SCHEMA,
    )
    async def submit_titles(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(agents.submit, scope.run_id, scope.redact(args))
        return app_tools.text_result(text, error=error)

    return submit_titles


app_tools.register(agents.SUBMIT, _submit_titles)
