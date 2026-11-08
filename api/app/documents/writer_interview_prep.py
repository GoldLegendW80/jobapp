"""The two agents of an interview preparation (interview_prep.py): `documents_interview_prep`,
which writes it, and `documents_interview_practice`, the Practice chat's partner.

Both are writer runs like the proposal's (writer_cv.py documents what a run asks of the runner):
`internal`, bound to one generation of a posting's preparation (`writer_cv.bind_for` with the
kind), `private_io` without live text, `model_by_credential`, the documents' turns and wall
time, and the generation's `token_budget`. Neither has a web tool: what the posting says is data,
never instructions. The input holds ids, the document's language and the fake backend's
`script`: never a text of the person or the posting.

The writer reads the posting (`get_job`, `get_job_brief`), the profile (`read_profile_section`),
the CV and letter as sent (`get_sent_documents`) and the hiring panel's record
(`get_hiring_panel`), and submits through `submit_interview_prep` (D7's fact check). The partner
reads the preparation (`get_draft`), the practice so far (`get_practice`) and the profile, and
answers through `submit_practice_turn`.
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
from app.documents import interview_prep, writer_cv
from app.documents.models import Kind

if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools (the api image has no SDK, as writer_cv.py).
    from app.documents import interview_prep_tools as _tools  # noqa: F401

PREP_NAME = "documents_interview_prep"
PRACTICE_NAME = "documents_interview_practice"
PROMPTS = Path(__file__).parent / "prompts"
PREP_TOOLS = (
    "get_job",
    "get_job_brief",
    "read_profile_section",
    "get_sent_documents",
    "get_hiring_panel",
    "submit_interview_prep",
)
PRACTICE_TOOLS = (
    "get_job",
    "read_profile_section",
    "get_draft",
    "get_practice",
    "submit_practice_turn",
)
PREP_SCRIPT = "documents_interview_prep_valid"
PRACTICE_SCRIPT = "documents_interview_practice_valid"


class PrepInput(BaseModel):
    """The input of one run of either agent. Ids, codes and numbers only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    generation_id: uuid.UUID = Field(strict=False)
    language: Literal["en", "fr"] = "en"
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


def bind_generation(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """`writer_cv.bind_for` an interview preparation."""
    return writer_cv.bind_for(db, run, Kind.interview_prep)


@dataclass(frozen=True)
class PrepWriter(definitions.AgentDefinition):
    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        language = writer_cv.LANGUAGES.get(str(data.get("language")), "English")
        text = (
            "Prepare the person for the interviews of this posting, from the posting, their "
            "profile, the CV and letter they sent and the hiring panel's record. "
            f"Language: {language}."
        )
        if job_id is not None:
            text += f"\n\nJob id: {job_id}"
        return text


@dataclass(frozen=True)
class PracticePartner(definitions.AgentDefinition):
    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        language = writer_cv.LANGUAGES.get(str(data.get("language")), "English")
        text = (
            "The person wrote in the practice chat of their interview preparation: read the "
            "practice so far and the preparation, give feedback on their last answer and ask "
            f"the next question. Language: {language}."
        )
        if job_id is not None:
            text += f"\n\nJob id: {job_id}"
        return text


def _common(name: str) -> dict[str, Any]:
    return {
        "input_model": PrepInput,
        "model": writer_cv.MODEL,
        "model_by_credential": {CredentialKind.anthropic_api_key.value: writer_cv.MODEL_API_KEY},
        "max_turns": writer_cv.setting("documents_generation_max_turns"),
        "wall_time_s": float(writer_cv.setting("documents_generation_wall_s")),
        "effort": "medium",
        "internal": True,
        "bind_job": True,
        "live_text": False,
        "bind": bind_generation,
        "private_io": True,
        "system_prompt": (PROMPTS / f"{name}.md").read_text(encoding="utf-8"),
    }


PREP_WRITER = definitions.register(
    PrepWriter(
        name=PREP_NAME,
        description=(
            "Prepares the person for the interviews of one posting: the questions to expect, "
            "the gaps, STAR stories from the profile and questions to ask."
        ),
        tools=PREP_TOOLS,
        on_final=interview_prep.on_final,
        **_common("interview_prep"),
    )
)
PRACTICE_PARTNER = definitions.register(
    PracticePartner(
        name=PRACTICE_NAME,
        description=(
            "Rehearses an interview with the person: one question at a time, with feedback on "
            "each answer."
        ),
        tools=PRACTICE_TOOLS,
        on_final=interview_prep.on_practice_final,
        **_common("interview_practice"),
    )
)
