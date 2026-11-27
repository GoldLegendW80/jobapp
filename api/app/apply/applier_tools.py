"""The tools One click Apply's agent has beside B20's browser set (A19, app/apply/applier.py),
registered with `app.agents.tools.register` (worker only: this module imports the Claude Agent
SDK): the upload of the documents placed on its lease, `apply_done`, `apply_ready` (A20),
`apply_ask_person` and `apply_type_code` (A25). The browser set is
`app/browser/finder_tools.py`'s, shared through `tool_sessions` and imported by the run itself
(`applier.RuntimeApplier`), never at the worker's start: it imports the finder, whose sweep
agents the agents queue's timeout must not count (agents/catalog.py SWEEP_AGENTS).
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.apply import applier

NO_APPLICATION = "This run applies to nothing. Stop now."
REF = {"type": "string", "pattern": "^e[0-9]+$"}  # as REF


def _object(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


# A20: the answers the agent chose for the person (a required field the profile does not answer).
CHOSEN = {
    "type": "array",
    "maxItems": applier.CHOSEN_MAX,
    "items": _object(
        {
            "field": {"type": "string", "maxLength": applier.FIELD_MAX},
            "value": {"type": "string", "maxLength": 300},
            "why": {"type": "string", "maxLength": applier.WHY_MAX},
        },
        ("field", "why"),
    ),
}
SCHEMAS: dict[str, dict[str, Any]] = {
    "browser_file_upload": _object(
        {
            "ref": REF,
            "paths": {
                "type": "array",
                "items": {"type": "string", "enum": ["cv.pdf", "letter.pdf"]},
                "minItems": 1,
                "maxItems": 2,
            },
        },
        ("paths",),
    ),
    "apply_done": _object(
        {
            "outcome": {"type": "string", "enum": ["submitted", "failed", "posting_closed"]},
            "summary": {"type": "string", "maxLength": applier.SUMMARY_MAX},
            "confirmation_url": {"type": "string", "maxLength": 2000},
            "chosen": CHOSEN,
            # A25: with failed or posting_closed, whether a control that sends it was clicked.
            "sent": {"type": "string", "enum": list(applier.SENT_WORDS)},
        },
        ("outcome", "summary"),
    ),
    # A20: Review first names the control that sends the form; the gate clicks it on Confirm.
    "apply_ready": _object({"submit_ref": REF, "chosen": CHOSEN}, ("submit_ref",)),
    # A25: what only the person can do or know; the run waits for them.
    "apply_ask_person": _object(
        {
            "kind": {"type": "string", "enum": list(applier.ASK_KINDS)},
            "sentence": {"type": "string", "maxLength": applier.SENTENCE_MAX},
        },
        ("kind", "sentence"),
    ),
    # A25: the code the person gave in the app, typed by browserd into the box named.
    "apply_type_code": _object({"ref": REF}, ("ref",)),
}
DESCRIPTIONS = {
    "browser_file_upload": "Upload documents placed on this browser (cv.pdf, letter.pdf) to the "
    "file input or the attach button of the last snapshot's ref.",
    "apply_done": "End the application once: submitted after the site showed its confirmation "
    "(confirmation_url: that page's address, read with browser_snapshot), posting_closed when "
    "the site says the posting is no longer online, or failed; with failed or posting_closed, "
    "sent: whether a control that sends the application was clicked (yes, no, unsure); "
    "summary: what you filled and sent, or why it failed; chosen: each answer you chose for a "
    "required field the profile does not answer (field label, value, one line why).",
    "apply_ready": "Review first only: on the last step, every field filled, name the control "
    "that sends the application by its ref in your last snapshot instead of clicking it, with "
    "chosen as for apply_done. The person checks the list and the app sends it.",
    "apply_ask_person": "Ask the person for what only they can do or know (kind signin, "
    "account, code, captcha, confirmation_link or other), in one or two plain sentences; never "
    "for an ordinary field. Waits up to ask_waits_minutes of your first message: answers with "
    "their words, or that they used "
    "the browser (read the page again before any act), or that nobody came (stop then).",
    "apply_type_code": "Once the person gave a code in the app (apply_ask_person's answer says "
    "so), name the site's one time code box by its ref in your last snapshot: the app types the "
    "code there once. You never see it, and only that box of the site that asked takes it.",
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
            elif name == "apply_ready":
                text, error = session.ready_at(args.get("submit_ref"), args.get("chosen"))
            elif name == "apply_ask_person":
                text, error = await session.ask_person(args.get("kind"), args.get("sentence"))
            elif name == "apply_type_code":
                text, error = await session.type_code(args.get("ref"))
            else:
                text, error = session.finish(
                    args.get("outcome"),
                    args.get("summary"),
                    args.get("confirmation_url"),
                    args.get("chosen"),
                    args.get("sent"),
                )
            return app_tools.text_result(text, error=error)

        return handler

    return make


for _name in SCHEMAS:
    app_tools.register(_name, _factory(_name))
