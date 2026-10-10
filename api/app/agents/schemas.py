"""Bodies of /api/me/model-credential (A1), /api/agents (A2) and /api/proposals (A3). The secret
comes in as a SecretStr (its repr and any validation error never show it) and never goes out:
answers carry kind, last4 and dates only."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.agents.models import CredentialKind, ProposalDecider, ProposalStatus, RunStatus

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


class RunJobOut(BaseModel):
    """The job a run worked on, for the run lists' link (A3)."""

    id: uuid.UUID
    company: str
    title: str


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
    # Null when the run has no job or its job was deleted.
    job: RunJobOut | None = None


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
    # The agent works on one job: a run of it must name job_id.
    needs_job: bool = False


class AgentList(BaseModel):
    items: list[AgentOut]


# --- A3: proposals ----------------------------------------------------------------------------


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID | None
    target: str
    target_id: uuid.UUID | None
    kind: str
    payload: dict[str, Any]
    status: ProposalStatus
    decided_by: ProposalDecider | None
    decided_at: datetime | None
    edited_value: Any | None
    reason: str | None
    created_at: datetime


class ProposalList(BaseModel):
    items: list[ProposalOut]


class AcceptIn(BaseModel):
    """POST /api/proposals/{id}/accept. `edited_value`, when sent, is applied instead of the
    proposal's own value (a note's text, a status) and validated like it. `updated_at`, when
    sent, is the version of the target job the user saw: a job changed since is a 409."""

    model_config = ConfigDict(extra="forbid")

    edited_value: Any = None
    updated_at: datetime | None = None


class ProposalConflictOut(BaseModel):
    """409 of accept and reject. reason: not_pending (decided already: `status` says how),
    target_gone (the job was deleted: the proposal is now invalid), stale (the job changed since
    the user loaded it: `updated_at` is its current version)."""

    detail: str
    reason: Literal["not_pending", "target_gone", "stale"]
    status: ProposalStatus | None = None
    updated_at: datetime | None = None


# --- A4: chat threads -----------------------------------------------------------------------------

MESSAGE_MAX = 8000


class ThreadIn(BaseModel):
    """POST /api/agents/threads: the caller's thread on this job (created when there is none)."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID


class ThreadOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID | None
    title: str
    created_at: datetime
    updated_at: datetime


class ThreadList(BaseModel):
    items: list[ThreadOut]


class MessageIn(BaseModel):
    """POST /api/agents/threads/{id}/messages."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MESSAGE_MAX)


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    text: str
    run_id: uuid.UUID | None
    # The state of the run this message started (user messages) or came from (answers): a user
    # message whose run failed or was cancelled has no answer, and `error` says why.
    run_status: RunStatus | None = None
    error: str | None = None
    at: datetime


class ThreadDetail(ThreadOut):
    messages: list[MessageOut]
    # Older messages exist than the ones listed (the list holds the newest).
    messages_truncated: bool
    # The run answering the newest message, while it is queued or running.
    active_run_id: uuid.UUID | None


class SentOut(BaseModel):
    """202 of POST /api/agents/threads/{id}/messages: the stored message and the queued run."""

    message: MessageOut
    run: RunOut


class ThreadBusyOut(BaseModel):
    """409 of POST /api/agents/threads/{id}/messages while the previous answer is pending."""

    detail: str
    active_run_id: uuid.UUID | None = None
