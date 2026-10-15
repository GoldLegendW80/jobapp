"""The one tool of the answer agent, `submit_answers` (app/apply/agents.py), registered with
`app.agents.tools.register` (worker only: this module imports the Claude Agent SDK).

It takes no id of a person, a form or an application: what it stores belongs to the run it is
bound to (`Scope.run_id`), and `agents.RuntimeAnswerAgent` reads it back when that run has ended.
The arguments go through the run's `redact` (the scrub of the credential) before they are
checked. One submit per run; a malformed one answers what to fix and stores nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.apply import agents


def _submit_answers(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        agents.SUBMIT,
        "Submit one entry per question, once: its id, your answer or null to leave it to the "
        "person, and the source of the answer (motivation, facts, experience, skills, answers "
        "or letter). The answer says whether it was stored.",
        agents.SUBMIT_SCHEMA,
    )
    async def submit_answers(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(agents.submit, scope.run_id, scope.redact(args))
        return app_tools.text_result(text, error=error)

    return submit_answers


app_tools.register(agents.SUBMIT, _submit_answers)
