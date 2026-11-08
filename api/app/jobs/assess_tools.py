"""The submit tool of "Assess this posting", `submit_assessment` (app/jobs/assess.py), registered
with `app.agents.tools.register` (worker only: this module imports the Claude Agent SDK).

It takes no id: what it stores belongs to the run's user and the run's job (`Scope`). The
arguments go through the run's `redact` before they are checked. One submit per run; a
malformed one answers what to fix and stores nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.jobs import assess


def _submit_assessment(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        assess.SUBMIT,
        "Submit your assessment of this posting, once: at most twelve requirements, the "
        "culture screen against the user's own criteria, the signals the text shows, and a one "
        "sentence verdict with its confidence. The answer says whether it was stored, or what "
        "to fix.",
        assess.SUBMIT_SCHEMA,
    )
    async def submit_assessment(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(
            assess.submit,
            scope.sessions,
            scope.user_id,
            scope.run_id,
            scope.job_id,
            scope.redact(args),
            scope.clock(),
        )
        return app_tools.text_result(text, error=error)

    return submit_assessment


app_tools.register(assess.SUBMIT, _submit_assessment)
