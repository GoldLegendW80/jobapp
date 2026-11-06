"""The two tools of the hiring panel (task D29, panel.py), registered with
`app.agents.tools.register` (worker only: this module imports the Claude Agent SDK).

`report_review` (the seven reviewers) and `report_mistakes` (the merge) take no id of a person or
a document: what they store belongs to the run they are bound to (`Scope.run_id`), and
`panel.RuntimeCall` reads it back once that run has ended. The arguments go through the run's
`redact` before they are checked; one report per run, a malformed one answers what to fix.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.documents import panel


def _report_review(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        panel.REVIEW_TOOL,
        "Report your review once: the verdict word of your role, then what your role asks for "
        "(the quoted lines, each with why it hurts). The answer says whether it was stored.",
        panel.REVIEW_SCHEMA,
    )
    async def report_review(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(
            panel.report, scope.run_id, scope.redact(args), merge=False
        )
        return app_tools.text_result(text, error=error)

    return report_review


def _report_mistakes(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        panel.MERGE_TOOL,
        "Report the merged list once: the mistakes worst first (each with the quoted line, why "
        "it gets rejected, the keys of the reviewers who flagged it and the fix), the questions "
        "for facts the profile lacks, and the reviewer closest to rejecting it.",
        panel.MERGE_SCHEMA,
    )
    async def report_mistakes(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(
            panel.report, scope.run_id, scope.redact(args), merge=True
        )
        return app_tools.text_result(text, error=error)

    return report_mistakes


app_tools.register(panel.REVIEW_TOOL, _report_review)
app_tools.register(panel.MERGE_TOOL, _report_mistakes)
