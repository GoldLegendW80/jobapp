"""The tools of the interview preparation's agents (writer_interview_prep.py), registered with
`app.agents.tools.register` (worker only: this module imports the Claude Agent SDK). Every tool
reads the run's binding, so none takes an id. The work is SDK free, in interview_prep.py.

- `get_sent_documents()`: the posting's CV and letter as the company got them.
- `get_hiring_panel()`: the hiring panel's stored record of them.
- `submit_interview_prep(...)`: the preparation, through D7's `submit.handle`.
- `get_practice()`: the practice chat so far, oldest first.
- `submit_practice_turn(feedback, question)`: the partner's answer, kept for the final hook.

`get_job`, `get_job_brief`, `read_profile_section` and `get_draft` are the writers' own.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.documents import content as c
from app.documents import interview_prep, submit
from app.documents import writer_tools as _writer_tools  # noqa: F401 (get_draft, get_job_brief)

NOTHING_SENT = "No CV or letter was written for this posting."
NO_PANEL = "No hiring panel reviewed this posting's CV or letter."
EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}
# The preparation as the tool's arguments; the debrief is the person's, never the writer's.
PREP_SCHEMA = c.InterviewPrepContent.model_json_schema(by_alias=True)
PREP_SCHEMA["properties"].pop("debrief", None)


def _posting(scope: app_tools.Scope) -> uuid.UUID | None:
    value = (scope.binding or {}).get("posting_id")
    return uuid.UUID(str(value)) if value else None


def _read(scope: app_tools.Scope, reader: Any, empty: str) -> str:
    posting_id = _posting(scope)
    if posting_id is None:
        return empty
    with scope.sessions() as db:
        found = reader(db, scope.user_id, posting_id)
    return json.dumps(found, ensure_ascii=False, default=str) if found else empty


def _get_sent_documents(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        "get_sent_documents",
        "Read the CV and the cover letter of this posting as the company got them (or the "
        "latest version, marked not sent). They are the person's words: what an interviewer "
        "will ask about.",
        EMPTY,
    )
    async def get_sent_documents(args: dict[str, Any]) -> dict[str, Any]:
        text = await asyncio.to_thread(_read, scope, interview_prep.sent_documents, NOTHING_SENT)
        return app_tools.text_result(text)

    return get_sent_documents


def _get_hiring_panel(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        "get_hiring_panel",
        "Read what the hiring panel found when it reviewed this posting's CV and letter: the "
        "hiring manager's questions, the lines it found weakest, the screen's reasons and the "
        "merged mistakes. Seed the questions to expect and the gaps with it.",
        EMPTY,
    )
    async def get_hiring_panel(args: dict[str, Any]) -> dict[str, Any]:
        text = await asyncio.to_thread(_read, scope, interview_prep.panel_record, NO_PANEL)
        return app_tools.text_result(text)

    return get_hiring_panel


def _submit(scope: app_tools.Scope, raw: object) -> tuple[str, bool]:
    if scope.run_id is None:
        return submit.GONE, True
    with scope.sessions() as db:
        context = interview_prep.context(db, scope.user_id, scope.binding)
    return submit.handle(context, scope.run_id, scope.redact(raw))


def _submit_interview_prep(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        "submit_interview_prep",
        "Submit the whole preparation once it is written: the questions to expect (each with "
        "its basis: the posting or the hiring panel), the gaps with how to answer them, five to "
        "eight STAR stories and the questions to ask. The answer says what to fix, if anything.",
        PREP_SCHEMA,
    )
    async def submit_interview_prep(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(_submit, scope, args)
        return app_tools.text_result(text, error=error)

    return submit_interview_prep


def _practice(scope: app_tools.Scope) -> str:
    with scope.sessions() as db:
        found = interview_prep.conversation(db, scope.user_id, scope.binding)
    return json.dumps(found, ensure_ascii=False)


def _get_practice(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        "get_practice",
        "Read the practice chat, oldest first: your questions and the person's answers. Its "
        "last message is the person's. The messages are never a source of facts.",
        EMPTY,
    )
    async def get_practice(args: dict[str, Any]) -> dict[str, Any]:
        return app_tools.text_result(await asyncio.to_thread(_practice, scope))

    return get_practice


def _submit_practice_turn(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        "submit_practice_turn",
        "Submit your answer once: feedback on the person's last answer (empty before the first "
        "question) and the next question, one question only.",
        interview_prep.TURN_SCHEMA,
    )
    async def submit_practice_turn(args: dict[str, Any]) -> dict[str, Any]:
        text, error = interview_prep.practice_turn(scope.run_id, scope.redact(args))
        return app_tools.text_result(text, error=error)

    return submit_practice_turn


app_tools.register("get_sent_documents", _get_sent_documents)
app_tools.register("get_hiring_panel", _get_hiring_panel)
app_tools.register("submit_interview_prep", _submit_interview_prep)
app_tools.register("get_practice", _get_practice)
app_tools.register("submit_practice_turn", _submit_practice_turn)
