"""The proposal writer, `documents_proposal` (issue #485, the dependency of opportunities O9a):
the writer of a short outreach proposal for a posting whose only way to apply is an e-mail
address. A subject and a body of at most about 150 words, text only: nothing renders it, and the
app never sends it (the person does, from their own mail program).

It is the letter writer's definition with a proposal in place of the letter (writer_cv.py
documents what a run asks of the runner): `internal`, bound to one generation (`bind_for` with
the proposal kind), a `private_io` run without live text, `model_by_credential`, the turns and
wall time of the settings and the generation's `token_budget`. The tools are the letter's
(`get_job`, `read_profile_section`, `get_base_cv`, `get_document("cv")`), `get_draft`, which
shows the address the proposal goes to (`to`, set by the app from the posting), and
`submit_proposal`, which goes through D7's `submit.handle` (structure, the dash fix, the fact
check with the posting's title and company allowed as in a letter, the one correction pass,
then the removal by code). A posting with no text gets a short generic proposal from its title
and company: the prompt says so, and the fallbacks of `corrections.proposal_fallbacks` say no
more than they do.

The input holds ids, the document's language, the person's tone (a code) and the fake backend's
`script`: never a text of the person or the posting, and never the address.

Preferences (issue #473): the language is the document's, which `store.get_or_create` sets, and
the tone is the input's `tone`, which `generate.start` fills for every writer and
`writer_cv.tone_line` reads.
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
from app.documents import content as c
from app.documents import writer_cv
from app.documents.models import Kind

if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools (the api image has no SDK, as writer_cv.py).
    from app.documents import writer_tools as _tools  # noqa: F401

NAME = "documents_proposal"
PROMPT_PATH = Path(__file__).parent / "prompts" / "proposal.md"
TOOLS = (
    "get_job",
    "read_profile_section",
    "get_base_cv",
    "get_document",
    "get_draft",
    "submit_proposal",
)


class ProposalInput(BaseModel):
    """The input of one proposal writer run. Ids, codes and numbers only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    generation_id: uuid.UUID = Field(strict=False)
    mode: Literal["posting"] = "posting"
    language: Literal["en", "fr"] = "en"
    tone: Literal["plain", "formal", "warm"] = "plain"
    # Read by no one: `generate.start` passes every writer the template's soft limits, and a
    # proposal has no template (an empty map).
    limits: dict[str, int | None] = Field(default_factory=dict, max_length=12)
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


def bind_generation(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """`writer_cv.bind_for` the proposal writer: a posting's proposal. The binding names the
    base CV the generation reads (`base_document_id`: a proposal has none of its own)."""
    return writer_cv.bind_for(db, run, Kind.proposal)


@dataclass(frozen=True)
class ProposalWriter(definitions.AgentDefinition):
    """The definition, with a first message built from the run's input."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        language = writer_cv.LANGUAGES.get(str(data.get("language")), "English")
        text = (
            "Write a short outreach proposal for this posting, from the person's profile and "
            "base CV, and from the CV already written for it when there is one: a subject and "
            f"a body of at most {c.PROPOSAL_WORDS} words. "
            f"Language: {language}. {writer_cv.tone_line(data)}"
        )
        if job_id is not None:
            text += f"\n\nJob id: {job_id}"
        return text.strip()


def _definition() -> ProposalWriter:
    return ProposalWriter(
        name=NAME,
        description="Writes a short outreach proposal for one posting from the profile.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=TOOLS,
        input_model=ProposalInput,
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


PROPOSAL_WRITER = definitions.register(_definition())
