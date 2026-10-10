"""The app tools an agent can call, as an in-process MCP server of the Claude Agent SDK
(`create_sdk_mcp_server` with `@tool` handlers, SDK facts in tasks/agents/todo.md). Worker only:
this module imports the SDK, which the api image does not hold.

User scoping is server side: `build(sessions, user_id)` makes a fresh server per run whose
handlers close over that run's user id. No tool takes a user id argument, so the model cannot
choose whose data it reads; every read goes through the same service functions as the api,
which filter on the user (app/jobs/service.py `jobs_of`). Another user's job, an unknown id and a
malformed id all answer the same "not found".

Tool names as Claude Code sees them: `mcp__<SERVER>__<tool>` (SERVER below). `allowed_names`
is what the runner passes as `allowed_tools`; the PreToolUse gate (app/agents/runner.py) denies
every other name.

A2 tools: get_job (read only). A3 adds get_profile_section, propose_note, propose_status.
"""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import service as jobs_service

SERVER = "app"
DESCRIPTION_MAX = 6000
NOT_FOUND = "Job not found."


def full_name(name: str) -> str:
    return f"mcp__{SERVER}__{name}"


def text_result(text: str, *, error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if error:
        result["is_error"] = True
    return result


def _job_payload(sessions: sessionmaker[Session], user_id: uuid.UUID, raw: object) -> str | None:
    try:
        job_id = uuid.UUID(str(raw))
    except ValueError:
        return None
    with sessions() as db:
        job = jobs_service.get_job(db, user_id, job_id)
        if job is None:
            return None
        description = job.description or ""
        payload = {
            "id": str(job.id),
            "company": job.company,
            "title": job.title,
            "location": job.location,
            "contract_type": job.contract_type.value,
            "status": job.status.value,
            "fit": job.fit,
            "competencies": list(job.competencies),
            "gaps": list(job.gaps),
            "url": job.url,
            "published_on": job.published_on.isoformat() if job.published_on else None,
            "found_on": job.found_on.isoformat(),
            "notes": job.notes,
            "description": description[:DESCRIPTION_MAX],
            "description_truncated": len(description) > DESCRIPTION_MAX,
        }
    return json.dumps(payload, ensure_ascii=False)


Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _get_job(sessions: sessionmaker[Session], user_id: uuid.UUID) -> SdkMcpTool[Any]:
    @tool(
        "get_job",
        "Read one job posting of the user by its id: company, title, location, contract type, "
        "status, fit score, competencies, gaps, url, dates, notes and the description.",
        {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "The job's id (a UUID)"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
    )
    async def get_job(args: dict[str, Any]) -> dict[str, Any]:
        found = await asyncio.to_thread(_job_payload, sessions, user_id, args.get("job_id"))
        if found is None:
            return text_result(NOT_FOUND, error=True)
        return text_result(found)

    return get_job


FACTORIES: dict[str, Callable[[sessionmaker[Session], uuid.UUID], SdkMcpTool[Any]]] = {
    "get_job": _get_job,
}


@dataclass(frozen=True)
class AppTools:
    """One run's tools: the MCP server for the SDK and the same tools by full name (the fake
    backend calls them directly, as Claude Code would through the server)."""

    server: McpSdkServerConfig
    by_name: dict[str, SdkMcpTool[Any]] = field(default_factory=dict)

    @property
    def allowed_names(self) -> list[str]:
        return sorted(self.by_name)


def build(sessions: sessionmaker[Session], user_id: uuid.UUID, names: tuple[str, ...]) -> AppTools:
    """A server holding exactly `names`, bound to `user_id`. Unknown names fail loudly (a
    definition naming a tool that does not exist is a bug)."""
    unknown = [name for name in names if name not in FACTORIES]
    if unknown:
        raise ValueError(f"unknown app tools: {', '.join(unknown)}")
    tools = [FACTORIES[name](sessions, user_id) for name in names]
    server = create_sdk_mcp_server(name=SERVER, version="1.0.0", tools=tools)
    return AppTools(server=server, by_name={full_name(t.name): t for t in tools})
