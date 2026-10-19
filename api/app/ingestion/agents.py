"""The agents of the ingestion module: `profile_extract`, the extraction step (task G9), and
`document_transcribe`, the transcription step (task G14, at the end of this file).

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

Importing this module registers the agent, and its tools where the Claude Agent SDK is installed
(the worker; the api's image has none and runs no tool). The worker and the api both import it
(task G10 adds those imports with its engine and its router); until then no process knows the
agent, and no route starts it.
"""

import importlib.util
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
from app.ingestion import plan, tool_names
from app.ingestion.models import IngestionDocument, IngestionImage, TextStatus
from app.ingestion.runs_models import (
    IngestionRun,
    IngestionRunStep,
    RunStatus,
    StepStatus,
    may_be_read,
    stored_text_sha256,
)

logger = logging.getLogger("app.ingestion.agents")

if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools: importing them registers them with the runner. The api
    # image holds no Claude Agent SDK (api/Dockerfile, the `runtime` target): it registers the
    # definition below, which names the tools, and never builds one.
    from app.ingestion import tools as _tools  # noqa: F401
    from app.ingestion import transcribe as _transcribe  # noqa: F401

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
    another agent run or another user), its ingestion run is not `running`, or its document is
    gone, has no text, or stores another text than the one the step was planned on
    (`text_sha256`: a replaced text keeps the row and the hash of its file, so only the hash of
    the text tells). None as well when the document may not be read now (`may_be_read`: its text
    not `done`, or a secret warning not confirmed), and for a step without a range unless it is
    a document read whole: part 0 of a text no longer than one part (`plan.PART_MAX_CHARS`), as
    `plan.cut` would leave it. A step that carries no text hash is never bound. Reads only,
    takes no lock."""
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
            IngestionRun.status == RunStatus.running,
        )
    ).scalar_one_or_none()
    if step is None or step.text_sha256 is None:
        return None
    total = db.execute(
        sa.select(sa.func.char_length(IngestionDocument.text)).where(
            IngestionDocument.id == step.document_id,
            IngestionDocument.user_id == run.user_id,
            IngestionDocument.sha256 == step.sha256,
            stored_text_sha256() == bytes(step.text_sha256),
            may_be_read(),
        )
    ).scalar_one_or_none()
    if total is None:  # no such document for this user, no text, not that text, not readable now
        return None
    if step.range_start is None or step.range_end is None:
        if step.part != 0 or total > plan.PART_MAX_CHARS:
            return None
        start, end = 0, total
    else:
        start, end = min(step.range_start, total), min(step.range_end, total)
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
        tools=(tool_names.READ_DOCUMENT, tool_names.PROPOSE),
        input_model=ExtractInput,
        model=EXTRACT_MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: EXTRACT_MODEL_API_KEY},
        max_turns=EXTRACT_MAX_TURNS,
        wall_time_s=EXTRACT_WALL_TIME_S,
        effort="medium",
        default_message=FIRST_MESSAGE,
        internal=True,
        bind=bind_step,
        private_io=True,
        on_final=_on_final,
    )
)


# --- document_transcribe (G14) --------------------------------------------------------------------

# The name the engine starts a transcription step under (`engine.TRANSCRIBE_NAME`, the same
# text: the engine imports this module, so the name is written twice and a test compares them).
TRANSCRIBE_NAME = "document_transcribe"
TRANSCRIBE_MAX_TURNS = 40
TRANSCRIBE_WALL_TIME_S = 900.0  # 15 minutes
TRANSCRIBE_PROMPT_PATH = Path(__file__).parent / "prompts" / "transcribe.md"
TRANSCRIBE_FIRST_MESSAGE = (
    "Call view_image with position 0. Store the text of each image you are shown, follow "
    "next_position until it is null, then answer with one line."
)


def bind_transcription(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """What a transcription run is bound to: `{ingestion_run_id, step_id, document_id,
    positions}`, where `positions` are the images of the step's group that still wait for a
    text, in ascending order. None when there is nothing to transcribe, and the run then fails
    before any model call: the step is not this run's (not a transcription step, not `running`,
    of another agent run or another user), its ingestion run is not `running`, its document is
    gone, is another file or no longer waits for a transcription, or every image of the group
    already holds its text (a second attempt of a step that had stored them all: no image is
    sent twice). Reads only, takes no lock."""
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
            IngestionRunStep.kind == plan.StepKind.transcribe,
            IngestionRunStep.status == StepStatus.running,
            IngestionRun.user_id == run.user_id,
            IngestionRun.status == RunStatus.running,
        )
    ).scalar_one_or_none()
    if step is None or not step.image_positions:
        return None
    waiting = (
        db.execute(
            sa.select(IngestionImage.position)
            .join(
                IngestionDocument,
                sa.and_(
                    IngestionDocument.id == IngestionImage.document_id,
                    IngestionDocument.user_id == IngestionImage.user_id,
                ),
            )
            .where(
                IngestionImage.document_id == step.document_id,
                IngestionImage.user_id == run.user_id,
                IngestionImage.position.in_(list(step.image_positions)),
                IngestionImage.text.is_(None),
                IngestionDocument.sha256 == step.sha256,
                IngestionDocument.text_status == TextStatus.awaiting_transcription,
            )
            .order_by(IngestionImage.position)
        )
        .scalars()
        .all()
    )
    if not waiting:
        return None
    return {
        "ingestion_run_id": str(step.ingestion_run_id),
        "step_id": str(step.id),
        "document_id": str(step.document_id),
        "positions": [int(position) for position in waiting],
    }


TRANSCRIBE = definitions.register(
    definitions.AgentDefinition(
        name=TRANSCRIBE_NAME,
        description="Reads the page images of one uploaded document and stores their text.",
        system_prompt=TRANSCRIBE_PROMPT_PATH.read_text(encoding="utf-8"),
        tools=(tool_names.VIEW_IMAGE, tool_names.STORE_TRANSCRIPTION),
        input_model=ExtractInput,
        model=EXTRACT_MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: EXTRACT_MODEL_API_KEY},
        max_turns=TRANSCRIBE_MAX_TURNS,
        wall_time_s=TRANSCRIBE_WALL_TIME_S,
        effort="medium",
        default_message=TRANSCRIBE_FIRST_MESSAGE,
        internal=True,
        bind=bind_transcription,
        private_io=True,
        # The same hook as the extraction step: the engine's `advance` moves the chain, and plans
        # the document's extraction from the text the last image completed.
        on_final=_on_final,
    )
)
