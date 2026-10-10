"""Agent definitions: what each agent is told, which app tools it may call, its model and its
limits (SPEC-agents.md, Runtime). Plain data, no SDK import: the api validates a new run against
these, and the worker (app/agents/runner.py) turns one into Claude Agent SDK options.

Every agent runs on `claude-opus-5-5` unless its definition says otherwise. Limits are technical
only (no spending cap, SPEC-agents.md Decisions): `max_turns` is passed to the SDK, `wall_time_s`
is enforced by the runner, which stops the run when it is exceeded.

A2 ships one agent, `echo`, for tests and the dev run panel only (`test_only`: refused when
APP_ENV is production). A3 adds the job triage agent.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MAX_TURNS = 8
DEFAULT_WALL_TIME_S = 600.0  # 10 minutes


class UnknownAgentError(LookupError):
    """No agent of that name is available here."""


class InvalidInputError(ValueError):
    """The run input does not match the agent's input model. The message names the field."""


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    system_prompt: str
    # Names of app tools (app/agents/tools.py) this agent may call; nothing else is reachable.
    tools: tuple[str, ...]
    input_model: type[BaseModel]
    model: str = DEFAULT_MODEL
    max_turns: int = DEFAULT_MAX_TURNS
    wall_time_s: float = DEFAULT_WALL_TIME_S
    effort: str | None = None
    # Only outside production: test agents and the dev run panel.
    test_only: bool = False

    def validate_input(self, raw: dict[str, Any]) -> dict[str, Any]:
        try:
            parsed = self.input_model.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first.get("loc", ())) or "input"
            raise InvalidInputError(f"input.{where}: {first.get('msg', 'invalid')}") from None
        return parsed.model_dump(mode="json", exclude_none=True)

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        """The first user message of the run. Built from the run's own input, never logged."""
        text = str(data.get("message", ""))
        if job_id is not None:
            text += f"\n\nJob id: {job_id}"
        return text


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)
    # Fake backend only (tests, scratch stacks): the recorded stream to replay
    # (app/agents/fake_streams/). Ignored by the real SDK backend.
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


ECHO = AgentDefinition(
    name="echo",
    description="Test agent: repeats the message back; with a job, reads it with get_job.",
    system_prompt=(
        "You are the echo test agent of a job search app. Answer in one short sentence that "
        "repeats the user's message. When the message gives a job id, call the get_job tool "
        "once with that id and also name the job's company and title. Never call any other "
        "tool."
    ),
    tools=("get_job",),
    input_model=EchoInput,
    max_turns=4,
    effort="low",
    test_only=True,
)

AGENTS: dict[str, AgentDefinition] = {ECHO.name: ECHO}


def available(app_env: str) -> dict[str, AgentDefinition]:
    """The agents a user may start here: every one outside production, else the real ones."""
    return {
        name: agent
        for name, agent in AGENTS.items()
        if not (agent.test_only and app_env == "production")
    }


def get(name: str, app_env: str) -> AgentDefinition:
    found = available(app_env).get(name)
    if found is None:
        raise UnknownAgentError(name)
    return found
