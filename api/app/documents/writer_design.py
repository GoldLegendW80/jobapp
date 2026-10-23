"""The design writer, `documents_design` (task D25b; SPEC-documents.md, "Design capture of an
upload", steps 3 to 5): one run writes the template of one `template_capture` generation.

- `internal`: started by `start` only (design_capture.request and start_ready); POST
  /api/agents/runs refuses it;
- bound: `bind_generation` reads the generation, in code, and refuses any other (another
  person's, a generation not running, a run that is not the generation's own, a document that
  is not a base CV converted from an upload, a run that names a posting);
- `private_io`, `live_text=False`, `model_by_credential` (Opus on a subscription, Sonnet on an
  API key, both with vision), turns and wall time from the settings, the `token_budget` of the
  input;
- tools (`writer_design_tools`, worker only): `read_design` (the scrubbed layout extraction, the
  tokens and fonts code chose, the structure of the base CV), `view_page` (a page of the upload
  as an image, as ingestion's `view_image` shows one: the runtime's message builder is not
  changed), `view_render` (page 1 of the base CV printed with the accepted template) and
  `submit_template` (the three files, checked by `design_capture.validate`).

The input holds the generation's id, the file's kind, the budget and the fake backend's
`script`: never a text of the person. The final hook is `design_capture.on_final`.
"""

from __future__ import annotations

import importlib.util
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents import definitions
from app.agents import runs as agent_runs
from app.agents.models import AgentRun, CredentialKind
from app.config import Settings, get_settings
from app.documents import design_capture, store
from app.documents.models import Document, DocumentGeneration, GenerationMode, GenerationStatus
from app.documents.models import Scope as DocumentScope
from app.documents.writer_cv import setting
from app.ingestion.documents import DOCX_MIME
from app.ingestion.models import IngestionDocument

if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools (the api image has no SDK, as writer_cv).
    from app.documents import writer_design_tools as _tools  # noqa: F401

logger = logging.getLogger("app.documents.writer_design")

NAME = "documents_design"
MODEL = "claude-opus-5-5"
MODEL_API_KEY = "claude-sonnet-5-5"
PROMPT_PATH = Path(__file__).parent / "prompts" / "design.md"
TOOLS = ("read_design", "view_page", "view_render", "submit_template")
DEFAULT_SCRIPT = "documents_design_valid"


class DesignInput(BaseModel):
    """The input of one design run. Ids, codes and numbers only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    generation_id: uuid.UUID = Field(strict=False)
    source: Literal["pdf", "docx"] = "pdf"
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


def bind_generation(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """`{generation_id, document_id, upload_id}`, ids as text; None (the run fails before any
    model call) unless the generation is this run's own and running, a capture of one of the
    person's base CVs whose upload is still linked, and the run names no posting."""
    try:
        generation_id = uuid.UUID(str(run.input.get("generation_id")))
    except ValueError:
        return None
    if run.job_id is not None:
        return None
    row = db.execute(
        sa.select(DocumentGeneration.id, Document.id, Document.source_document_id)
        .join(Document, Document.id == DocumentGeneration.document_id)
        .where(
            DocumentGeneration.id == generation_id,
            DocumentGeneration.run_id == run.id,
            DocumentGeneration.user_id == run.user_id,
            DocumentGeneration.status == GenerationStatus.running,
            DocumentGeneration.mode == GenerationMode.template_capture,
            Document.user_id == run.user_id,
            Document.scope == DocumentScope.base,
        )
    ).one_or_none()
    if row is None or row[2] is None:
        return None
    return {"generation_id": str(row[0]), "document_id": str(row[1]), "upload_id": str(row[2])}


@dataclass(frozen=True)
class DesignWriter(definitions.AgentDefinition):
    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        if data.get("source") == "docx":
            return (
                "Capture the design of the person's uploaded CV, a Word document: there is no "
                "page image, read its styles with read_design. Then write the template."
            )
        return (
            "Capture the design of the person's uploaded CV, a PDF: read the layout with "
            "read_design, look at its pages with view_page, then write the template."
        )


def _definition() -> DesignWriter:
    return DesignWriter(
        name=NAME,
        description="Writes a template that looks like the person's uploaded CV.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=TOOLS,
        input_model=DesignInput,
        model=MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: MODEL_API_KEY},
        max_turns=setting("documents_generation_max_turns"),
        wall_time_s=float(setting("documents_generation_wall_s")),
        effort="medium",
        internal=True,
        live_text=False,
        bind=bind_generation,
        private_io=True,
        on_final=design_capture.on_final,
    )


DESIGN_WRITER = definitions.register(_definition())


def _script(settings: Settings, script: str | None) -> str | None:
    if not settings.agents_fake_backend or settings.app_env == "production":
        return None
    return script or DEFAULT_SCRIPT


def start(
    db: Session,
    generation: DocumentGeneration,
    *,
    at: Any,
    script: str | None = None,
    settings: Settings | None = None,
) -> AgentRun:
    """The design run of a capture generation, `queued`, and the generation `running`. The
    caller commits, then `generate.enqueue_run`. Raises agent_runs.ActiveRunsError when the
    person has their most runs active, ValueError when the generation has its run."""
    settings = settings or get_settings()
    if generation.mode is not GenerationMode.template_capture:
        raise ValueError(f"not a capture: {generation.mode.value}")
    document = store.lock_document(db, generation.user_id, generation.document_id)
    db.refresh(generation)
    if generation.status not in (GenerationStatus.queued, GenerationStatus.waiting):
        raise ValueError(f"the generation is {generation.status.value}: it has its run")
    mime = db.scalar(
        sa.select(IngestionDocument.mime).where(
            IngestionDocument.id == document.source_document_id,
            IngestionDocument.user_id == generation.user_id,
        )
    )
    data: dict[str, Any] = {
        "generation_id": str(generation.id),
        "source": "docx" if mime == DOCX_MIME else "pdf",
        "token_budget": generation.token_ceiling,
    }
    chosen = _script(settings, script)
    if chosen is not None:
        data["script"] = chosen
    run = agent_runs.create_run(
        db, generation.user_id, NAME, None, DESIGN_WRITER.validate_input(data), MODEL, at
    )
    generation.run_id = run.id
    generation.status = GenerationStatus.running
    generation.wait_reason = None
    generation.started_at = at
    db.flush()
    logger.info(
        "documents.design.started",
        extra={"generation_id": str(generation.id), "run_id": str(run.id)},
    )
    return run
