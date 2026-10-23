"""The one tool of the check by model, `report_check` (app/apply/model_check.py), registered with
`app.agents.tools.register` (worker only: this module imports the Claude Agent SDK).

It takes no id of a person, a form or an application: what it stores belongs to the run it is
bound to (`Scope.run_id`), and `model_check.RuntimeCheck` reads it back when that run has ended.
The arguments go through the run's `redact` (the scrub of the credential) before they are
checked. One report per run; a malformed one answers what to fix and stores nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.apply import model_check


def _report_check(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        model_check.REPORT,
        "Report the check once: passed (true only with no claim) and the claims, each with the "
        "answer's id, its code (not_in_profile, rounded_up, relabelled, tone or person_rule) "
        "and the exact words of the answer that carry it. The answer says whether it was stored.",
        model_check.REPORT_SCHEMA,
    )
    async def report_check(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(model_check.report, scope.run_id, scope.redact(args))
        return app_tools.text_result(text, error=error)

    return report_check


app_tools.register(model_check.REPORT, _report_check)
