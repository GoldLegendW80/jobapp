"""The letter writer, `documents_letter` (task D8; SPEC-documents.md, "The writers", "Limits of one
run"): D7's writer for a posting's cover letter.

It is the CV writer's definition with a letter in place of the CV (writer_cv.py documents what a
run asks of the runner): `internal`, bound to one generation (`bind_for` with the letter kind), a
`private_io` run without live text, `model_by_credential`, the turns and wall time of the
settings and the generation's `token_budget`. The tools are `get_job`, `read_profile_section`,
`get_base_cv`, `get_document("cv")` (the CV of the same posting, so the two tell one story; none
yet is answered, not an error) and `submit_letter`, which goes through D7's `submit.handle`
(structure, the dash fix, the fact check with the posting's title and company allowed in a
letter, the one correction pass, then the removal by code).

A letter is never shortened by a second pass: the render fits the font (D3) and a letter that
still overflows is kept and shown. The input holds ids, the document's language, the person's
tone (a code), the template's soft limits (numbers) and the fake backend's `script`: never a text
of the person or the posting.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents import definitions
from app.agents.models import AgentRun, CredentialKind
from app.documents import writer_cv
from app.documents.models import Kind

if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools (the api image has no SDK, as writer_cv.py).
    from app.documents import writer_tools as _tools  # noqa: F401

NAME = "documents_letter"
PROMPT_PATH = Path(__file__).parent / "prompts" / "letter.md"
TOOLS = ("get_job", "read_profile_section", "get_base_cv", "get_document", "submit_letter")


class LetterInput(BaseModel):
    """The input of one letter writer run. Ids, codes and numbers only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    generation_id: uuid.UUID = Field(strict=False)
    mode: Literal["posting"] = "posting"
    language: Literal["en", "fr"] = "en"
    tone: Literal["plain", "formal", "warm"] = "plain"
    # The template's soft limits, in characters (rules.SoftLimits); null: no counter.
    limits: dict[str, int | None] = Field(default_factory=dict, max_length=12)
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


def bind_generation(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """`writer_cv.bind_for` the letter writer: a posting's letter. The binding also names the
    base CV the generation reads (`base_document_id`: a letter has none of its own)."""
    return writer_cv.bind_for(db, run, Kind.letter)


@dataclass(frozen=True)
class LetterWriter(definitions.AgentDefinition):
    """The definition, with a first message built from the run's input."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        language = writer_cv.LANGUAGES.get(str(data.get("language")), "English")
        limits = writer_cv.limits_line(data.get("limits") or {})
        text = (
            "Write the cover letter for this posting, from the person's profile and base CV, "
            "and from the CV already written for it when there is one. "
            f"Language: {language}. {writer_cv.tone_line(data)} {limits}"
        )
        if job_id is not None:
            text += f"\n\nJob id: {job_id}"
        return text.strip()


def _definition() -> LetterWriter:
    return LetterWriter(
        name=NAME,
        description="Writes the cover letter of one posting from the profile and the CVs.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=TOOLS,
        input_model=LetterInput,
        model=writer_cv.MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: writer_cv.MODEL_API_KEY},
        max_turns=writer_cv.setting("documents_generation_max_turns"),
        wall_time_s=float(writer_cv.setting("documents_generation_wall_s")),
        effort="medium",
        internal=True,
        bind_job=True,
        live_text=False,
        bind=bind_generation,
        private_io=True,
        on_final=writer_cv.run_final_hooks,
    )


LETTER_WRITER = definitions.register(_definition())
