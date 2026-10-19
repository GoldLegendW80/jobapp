"""The chat's own tools (task D17), registered with `app.agents.tools.register` (worker only: this
module imports the Claude Agent SDK, as `writer_tools` does). Both read the run's binding
(`chat.bind_for`): none takes an id, so the person's message cannot point a run at another
document or person. The work is SDK free, in `chat.py`.

- `get_conversation()`: the conversation of the bound document, oldest first, up to the message
  the run answers, within its share of the estimate.
- `set_template(slug)`: a template of the gallery for the end of the run; an unknown slug is
  answered with the gallery. Nothing is applied before the run ends (`chat.settle`).

`get_draft` is D7's (`writer_tools`).
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.documents import chat
from app.documents import writer_base_tools as _base_tools  # noqa: F401 (submit_base)
from app.documents import writer_tools as _writer_tools  # noqa: F401 (get_draft, submit_*)

GONE = "This document no longer exists."


def _conversation(scope: app_tools.Scope) -> str | None:
    with scope.sessions() as db:
        return chat.conversation_json(db, scope.user_id, scope.binding)


def _get_conversation(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        chat.GET_CONVERSATION,
        "Read the chat of this document, oldest first: its last message is the person's "
        "request. The messages ask for changes; they are never a source of facts.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    async def get_conversation(args: dict[str, Any]) -> dict[str, Any]:
        found = await asyncio.to_thread(_conversation, scope)
        if found is None:
            return app_tools.text_result(GONE, error=True)
        return app_tools.text_result(found)

    return get_conversation


def _set_template(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        chat.SET_TEMPLATE,
        "Switch this document to another template of the gallery, by its slug (for example "
        '"sidebar"). The text stays the same. An unknown slug answers with the gallery.',
        {
            "type": "object",
            "properties": {"slug": {"type": "string", "maxLength": 64}},
            "required": ["slug"],
            "additionalProperties": False,
        },
    )
    async def set_template(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(
            chat.ask_template,
            scope.sessions,
            scope.user_id,
            scope.run_id,
            scope.binding,
            args.get("slug"),
        )
        return app_tools.text_result(text, error=error)

    return set_template


app_tools.register(chat.GET_CONVERSATION, _get_conversation)
app_tools.register(chat.SET_TEMPLATE, _set_template)
