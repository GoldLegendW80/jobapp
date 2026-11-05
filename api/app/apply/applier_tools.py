"""The two tools One click Apply's agent has beside B20's browser set (A19, app/apply/applier.py),
registered with `app.agents.tools.register` (worker only: this module imports the Claude Agent
SDK): the upload of the documents placed on its lease, and `apply_done`. The browser set is
`app/browser/finder_tools.py`'s, shared through `tool_sessions`.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.apply import applier
from app.browser import finder_tools

NO_APPLICATION = "This run applies to nothing. Stop now."
SCHEMAS: dict[str, dict[str, Any]] = {
    "browser_file_upload": finder_tools._object(
        {
            "ref": finder_tools.REF,
            "paths": {
                "type": "array",
                "items": {"type": "string", "enum": ["cv.pdf", "letter.pdf"]},
                "minItems": 1,
                "maxItems": 2,
            },
        },
        ("paths",),
    ),
    "apply_done": finder_tools._object(
        {
            "outcome": {"type": "string", "enum": ["submitted", "failed"]},
            "summary": {"type": "string", "maxLength": applier.SUMMARY_MAX},
            "confirmation_url": {"type": "string", "maxLength": 2000},
        },
        ("outcome", "summary"),
    ),
}
DESCRIPTIONS = {
    "browser_file_upload": "Upload documents placed on this browser (cv.pdf, letter.pdf) to the "
    "file input or the attach button of the last snapshot's ref.",
    "apply_done": "End the application once: submitted after the site showed its confirmation "
    "(confirmation_url: that page's address, read with browser_snapshot), or failed; summary: "
    "what you filled and sent, or why it failed.",
}


def _factory(name: str) -> app_tools.Factory:
    def make(scope: app_tools.Scope) -> SdkMcpTool[Any]:
        @tool(name, DESCRIPTIONS[name], SCHEMAS[name])
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            session = applier.session_of(scope.run_id)
            if session is None:
                return app_tools.text_result(NO_APPLICATION, error=True)
            args = scope.redact(args) if isinstance(args, dict) else {}
            if name == "browser_file_upload":
                text, error = await session.upload(args)
            else:
                text, error = session.finish(
                    args.get("outcome"), args.get("summary"), args.get("confirmation_url")
                )
            return app_tools.text_result(text, error=error)

        return handler

    return make


for _name in SCHEMAS:
    app_tools.register(_name, _factory(_name))
