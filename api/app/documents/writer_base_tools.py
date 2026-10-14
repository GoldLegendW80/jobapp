"""The app tool of the base CV writer (task D9), registered with `app.agents.tools.register`
(worker only: this module imports the Claude Agent SDK, as `writer_tools` does).

- `submit_base(...)`: the base CV, as D2's content JSON with its `fixed` block. For a base CV
  built from the profile (`base_profile`), the `fixed` block the writer sends is replaced by the
  profile's own (`writer_base.profile_fixed`) before D7's `submit.handle` checks and corrects
  it: the writer never writes a fixed fact. The tool takes no id: it reads the run's binding.

The writer reads the profile with D7's `read_profile_section` (`writer_tools`).
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.documents import content as c
from app.documents import submit, writer_base
from app.documents import writer_tools as _cv_tools  # noqa: F401 (read_profile_section)
from app.documents.models import GenerationMode

SUBMIT_BASE = "submit_base"
GONE = "This document no longer exists."

# The base CV as the tool's arguments: D2's schema, unknown fields refused.
BASE_SCHEMA = c.BaseCvContent.model_json_schema(by_alias=True)


def _submit(scope: app_tools.Scope, raw: object) -> tuple[str, bool]:
    if scope.run_id is None or scope.binding is None:
        return GONE, True
    data = scope.redact(raw)
    with scope.sessions() as db:
        context = submit.context_for(db, scope.user_id, scope.binding, "base")
        if isinstance(data, dict) and scope.binding.get("mode") == GenerationMode.base_profile:
            view = writer_base.view_of(db, scope.user_id, scope.clock())
            data = {**data, "fixed": c.dump(writer_base.profile_fixed(view))}
    return submit.handle(context, scope.run_id, data)


def _submit_base(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        SUBMIT_BASE,
        "Submit the whole base CV once it is written. Leave fixed empty: the app copies the "
        "fixed facts (employers' organisations, titles and dates, schools, languages) from the "
        "profile. An experience entry names its employer by employer_key only. The answer says "
        "what to fix, if anything.",
        BASE_SCHEMA,
    )
    async def submit_base(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(_submit, scope, args)
        return app_tools.text_result(text, error=error)

    return submit_base


app_tools.register(SUBMIT_BASE, _submit_base)
