"""Bodies of /api/me/model-credential (A1) and /api/agents (A2). The secret comes in as a
SecretStr (its repr and any validation error never show it) and never goes out: answers carry
kind, last4 and dates only."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.agents.models import CredentialKind, RunStatus

# Generous bound for the body itself; the format check (credentials.clean_secret) is stricter.
SECRET_BODY_MAX = 4096


class CredentialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CredentialKind
    secret: SecretStr = Field(max_length=SECRET_BODY_MAX)


class CredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: CredentialKind
    last4: str
    verified_at: datetime | None
    updated_at: datetime


class VerifyOut(BaseModel):
    # verified: the API accepted the key just now. unverified: a subscription token, which the
    # server cannot check without running Claude Code; the first agent run checks it.
    result: Literal["verified", "unverified"]
    message: str
    credential: CredentialOut


# --- A2: agent runs ---------------------------------------------------------------------------


class RunIn(BaseModel):
    """POST /api/agents/runs. The agent's own input model checks `input` further."""

    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1, max_length=64)
    job_id: uuid.UUID | None = None
    input: dict[str, Any] = Field(default_factory=dict)


class RunOut(BaseModel):
    id: uuid.UUID
    agent: str
    job_id: uuid.UUID | None
    status: RunStatus
    input: dict[str, Any]
    output: dict[str, Any] | None
    error: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None


class RunList(BaseModel):
    items: list[RunOut]


class EventOut(BaseModel):
    seq: int
    at: datetime
    kind: str
    data: dict[str, Any]


class RunDetail(RunOut):
    events: list[EventOut]
    # True when the run has more events than one answer carries (the stream replays them all).
    events_truncated: bool


class AgentOut(BaseModel):
    name: str
    description: str
    model: str
    max_turns: int
    wall_time_s: float
    test_only: bool


class AgentList(BaseModel):
    items: list[AgentOut]
