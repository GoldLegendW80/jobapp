"""The agents of the ingestion module (task G9): `profile_extract`, the extraction step.

One run of this agent is one step of an ingestion run: it reads one part of one document with
`read_document` and stores what it finds through `propose_profile_changes` (app/ingestion/
tools.py). The definition asks the runner for everything a run over a private document needs
(app/agents/runner.py, "What a definition can ask of a run"):

- `internal`: started by the ingestion engine only, through `runs.create_run`; refused by
  POST /api/agents/runs and absent from GET /api/agents.
- `private_io`: the events hold tool names and sizes, never a value, the output holds counts, and
  nothing is live.
- `model_by_credential`: Opus on a subscription, Sonnet on an API key, chosen by the runner when
  it has read the credential.
- `bind`: `bind_step` below says, in code, which document part the run reads. No tool takes an
  id, so the model cannot point the run elsewhere.

The run's input holds exactly four keys (`ExtractInput`): `ingestion_run_id`, `step_id`,
`token_budget` (read by the runner for every agent) and `script` (the fake backend's stream,
written only when the fake backend is on).

Importing this module registers the agent and its tools. The worker and the api both import it
(task G10 adds those imports with its engine and its router); until then no process knows the
agent, and no route starts it.
"""

import logging
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents import definitions
from app.agents.models import AgentRun, CredentialKind
from app.ingestion import plan, tools
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import IngestionRun, IngestionRunStep, StepStatus

logger = logging.getLogger("app.ingestion.agents")

EXTRACT_NAME = "profile_extract"
EXTRACT_MODEL = "claude-opus-5-5"
EXTRACT_MODEL_API_KEY = "claude-sonnet-5-5"
EXTRACT_MAX_TURNS = 40
EXTRACT_WALL_TIME_S = 900.0  # 15 minutes
PROMPT_PATH = Path(__file__).parent / "prompts" / "extract.md"
FIRST_MESSAGE = (
    "Read your document part from offset 0 to its end, propose what it states about the user, "
    "then answer with one line."
)


class ExtractInput(BaseModel):
    """The input of one extraction step. Ids only: never a text, never a name."""

    model_config = ConfigDict(extra="forbid", strict=True)

    ingestion_run_id: uuid.UUID = Field(strict=False)
    step_id: uuid.UUID = Field(strict=False)
    # What the step may spend. Zero allows no call at all (the runner fails the run first).
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


def bind_step(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """What an extraction run is bound to: `{ingestion_run_id, step_id, document_id, part, start,
    end}`, plain text and numbers, where `start` and `end` are the character offsets of the part
    in the document's stored text. None when there is nothing to read, and the run then fails
    before any model call: the step is not this run's (not an extraction step, not `running`, of
    another agent run or another user), or its document is gone, was replaced, or has no text.
    Reads only, takes no lock."""
    try:
        step_id = uuid.UUID(str(run.input.get("step_id")))
    except ValueError:
        return None
    step = db.execute(
        sa.select(IngestionRunStep)
        .join(IngestionRun, IngestionRun.id == IngestionRunStep.ingestion_run_id)
        .where(
            IngestionRunStep.id == step_id,
            IngestionRunStep.agent_run_id == run.id,
            IngestionRunStep.kind == plan.StepKind.extract,
            IngestionRunStep.status == StepStatus.running,
            IngestionRun.user_id == run.user_id,
        )
    ).scalar_one_or_none()
    if step is None:
        return None
    total = db.execute(
        sa.select(sa.func.char_length(IngestionDocument.text)).where(
            IngestionDocument.id == step.document_id,
            IngestionDocument.user_id == run.user_id,
            IngestionDocument.sha256 == step.sha256,
        )
    ).scalar_one_or_none()
    if total is None:  # no such document for this user, or no text
        return None
    start = min(step.range_start or 0, total)
    end = total if step.range_end is None else min(step.range_end, total)
    return {
        "ingestion_run_id": str(step.ingestion_run_id),
        "step_id": str(step.id),
        "document_id": str(step.document_id),
        "part": int(step.part or 0),
        "start": start,
        "end": max(end, start),
    }


# Called when a run of the agent has reached its final status (definitions.OnFinal). The engine
# of G10 registers `advance` here; nothing is registered until then.
_final_hooks: list[definitions.OnFinal] = []


def register_final_hook(hook: definitions.OnFinal) -> None:
    """Called once, at import, by the module that moves the chain (G10's engine)."""
    if hook not in _final_hooks:
        _final_hooks.append(hook)


async def _on_final(final: definitions.FinalRun) -> None:
    for hook in list(_final_hooks):
        await hook(final)


EXTRACT = definitions.register(
    definitions.AgentDefinition(
        name=EXTRACT_NAME,
        description="Reads one part of one uploaded document and stores checked profile items.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=(tools.READ_DOCUMENT, tools.PROPOSE),
        input_model=ExtractInput,
        model=EXTRACT_MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: EXTRACT_MODEL_API_KEY},
        max_turns=EXTRACT_MAX_TURNS,
        wall_time_s=EXTRACT_WALL_TIME_S,
        effort="medium",
        default_message=FIRST_MESSAGE,
        live_text=False,
        internal=True,
        bind=bind_step,
        private_io=True,
        on_final=_on_final,
    )
)
