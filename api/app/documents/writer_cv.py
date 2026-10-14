"""The CV writer, `documents_cv` (task D7; SPEC-documents.md, "The writers", "Limits of one run").

One run writes one posting's CV for one generation (`generate.start` creates it). The definition
asks the runner for what a run over private text needs (app/agents/runner.py, "What a definition
can ask of a run"):

- `internal`: started by `generate.start` only; POST /api/agents/runs refuses it;
- bound: `bind_generation` reads the generation named by the input, its document and its posting,
  in code, and refuses any other (another person's, a generation not running, a run that is not
  the generation's own, a document that is not a posting's CV or whose posting is not the run's
  job). The tools read the binding; `get_job` reads the run's own posting only (`bind_job`);
- `private_io`, `live_text=False`: the events hold tool names and sizes, nothing is live;
- `model_by_credential`: Opus on a subscription, Sonnet on an API key, as ingestion and mail;
- turns and wall time from the settings (DOCUMENTS_GENERATION_MAX_TURNS, _WALL_S), and the
  `token_budget` of the input (the generation's ceiling).

The input holds ids, the document's language, the template's soft limits (numbers), the mode,
the fields a `shorten` pass must bring under their limits (paths), and the fake backend's
`script`: never a text of the person or of the posting. The first message is built from it.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents import definitions
from app.agents.models import AgentRun, CredentialKind
from app.config import ConfigError, Settings, get_settings
from app.documents.models import (
    Document,
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    Kind,
    Scope,
)

if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools (the api image has no SDK, as app/ingestion/agents.py).
    from app.documents import writer_tools as _tools  # noqa: F401

NAME = "documents_cv"
MODEL = "claude-opus-5-5"
MODEL_API_KEY = "claude-sonnet-5-5"
PROMPT_PATH = Path(__file__).parent / "prompts" / "cv.md"
TOOLS = ("get_job", "read_profile_section", "get_base_cv", "get_draft", "submit_cv")
MODES = (GenerationMode.posting.value, GenerationMode.shorten.value)
LANGUAGES = {"en": "English", "fr": "French"}
FIELD_PATH = r"^[a-z_]+(\.[0-9]+)?(\.[a-z_]+)?(\.[0-9]+)?$"


class CvInput(BaseModel):
    """The input of one CV writer run. Ids, codes and numbers only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    generation_id: uuid.UUID = Field(strict=False)
    mode: Literal["posting", "shorten"] = "posting"
    language: Literal["en", "fr"] = "en"
    # The template's soft limits, in characters (rules.SoftLimits); null: no counter.
    limits: dict[str, int | None] = Field(default_factory=dict, max_length=12)
    # `shorten` only: the fields over their soft limits, longest first.
    long_fields: list[str] = Field(default_factory=list, max_length=40)
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


def bind_for(db: Session, run: AgentRun, kind: Kind) -> Mapping[str, Any] | None:
    """What a writer run is bound to: `{generation_id, document_id, posting_id,
    base_document_id, mode}`, ids as text. None (the run fails before any model call) unless
    the generation is this run's own and running, of this person, for a posting's document of
    `kind` whose posting is the run's job. Reads only, takes no lock. The letter writer binds
    through it too (writer_letter.py)."""
    try:
        generation_id = uuid.UUID(str(run.input.get("generation_id")))
    except ValueError:
        return None
    row = db.execute(
        sa.select(DocumentGeneration, Document)
        .join(Document, Document.id == DocumentGeneration.document_id)
        .where(
            DocumentGeneration.id == generation_id,
            DocumentGeneration.run_id == run.id,
            DocumentGeneration.user_id == run.user_id,
            DocumentGeneration.status == GenerationStatus.running,
            Document.user_id == run.user_id,
            Document.scope == Scope.posting,
            Document.kind == kind,
        )
    ).one_or_none()
    if row is None:
        return None
    generation, document = row
    if run.job_id is None or document.posting_id != run.job_id:
        return None
    base = generation.base_document_id or document.base_document_id
    return {
        "generation_id": str(generation.id),
        "document_id": str(document.id),
        "posting_id": str(document.posting_id),
        "base_document_id": str(base) if base is not None else None,
        "mode": generation.mode.value,
    }


def bind_generation(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """`bind_for` the CV writer: a posting's CV."""
    return bind_for(db, run, Kind.cv)


def limits_line(limits: Mapping[str, Any]) -> str:
    shown = [f"{name} {value}" for name, value in limits.items() if value]
    if not shown:
        return ""
    return "Soft lengths of the template, in characters: " + ", ".join(shown) + "."


@dataclass(frozen=True)
class CvWriter(definitions.AgentDefinition):
    """The definition, with a first message built from the run's input."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        language = LANGUAGES.get(str(data.get("language")), "English")
        limits = limits_line(data.get("limits") or {})
        if data.get("mode") == GenerationMode.shorten.value:
            fields = ", ".join(str(path) for path in data.get("long_fields") or ()) or "the longest"
            text = (
                "The CV of this posting overflows one page. Read the draft with get_draft, then "
                "shorten these fields under their soft lengths, longest first: "
                f"{fields}. Cut words, never facts you would have to invent; change nothing "
                f"else. Submit the whole CV with submit_cv. Language: {language}. {limits}"
            )
        else:
            text = (
                "Write the CV of this posting for the person, from their profile and base CV. "
                f"Language: {language}. {limits}"
            )
        if job_id is not None:
            text += f"\n\nJob id: {job_id}"
        return text.strip()


# Called once a run of a writer (the CV's or the letter's) has reached its final status:
# `generate.on_final`, registered when generate.py is imported (it imports this module, so this
# one cannot import it).
_final_hooks: list[definitions.OnFinal] = []


def register_final_hook(hook: definitions.OnFinal) -> None:
    if hook not in _final_hooks:
        _final_hooks.append(hook)


async def run_final_hooks(final: definitions.FinalRun) -> None:
    for hook in list(_final_hooks):
        await hook(final)


def setting(name: str) -> Any:
    """A setting of the module, its default where this process has no configuration (a check, a
    test that imports the module without a database)."""
    try:
        return getattr(get_settings(), name)
    except ConfigError:
        return Settings.model_fields[name].default


def _definition() -> CvWriter:
    return CvWriter(
        name=NAME,
        description="Writes the CV of one posting from the profile and a base CV.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=TOOLS,
        input_model=CvInput,
        model=MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: MODEL_API_KEY},
        max_turns=setting("documents_generation_max_turns"),
        wall_time_s=float(setting("documents_generation_wall_s")),
        effort="medium",
        internal=True,
        bind_job=True,
        live_text=False,
        bind=bind_generation,
        private_io=True,
        on_final=run_final_hooks,
    )


CV_WRITER = definitions.register(_definition())
