"""The base CV writer, `documents_base`, and the build of a base CV from the profile (task D9;
SPEC-documents.md, "Base CVs", "The writers", "Waiting, never failing").

**The agent.** One run writes one base CV for one generation of mode `base_profile`:

- `internal`: started by `start` only; POST /api/agents/runs refuses it;
- bound: `bind_generation` reads the generation named by the input and its document, in code,
  and refuses any other (another person's, a generation not running, a run that is not the
  generation's own, a document that is not a base CV, a run that names a posting);
- `private_io`, `live_text=False`, `model_by_credential` (Opus on a subscription, Sonnet on an
  API key), turns and wall time from the settings, and the `token_budget` of the input;
- tools: D7's `read_profile_section` (facts, experience, skills, answers; `identity` answers
  "not available") and `submit_base` (`writer_base_tools`), which goes through D7's
  `submit.handle` with the kind `base`: the schema, the dash fix, the fact check, one correction
  pass, then code removes what remains. `get_upload_text` comes with task D24.

**The fixed block** of a base CV built from the profile is copied from the profile by code
(`profile_fixed`): what the writer submits as `fixed` is replaced before the checks, so the
writer never writes an employer, a title, a date, a school or a language.

**The build** (`build_from_profile`): a base CV row with the skeleton (the fixed block, nothing
written yet), then a generation of mode `base_profile`, `queued`, or `waiting` with `consent`
(no consent of the current text) or `credential` (no model credential). A profile without an
employer gives the skeleton with a note to fill the profile, and no generation: nothing is
refused, and nothing is sent. `start_ready` starts every open base generation of a person whose
reason to wait is gone (after the consent, after a credential is added: D10's tick calls it
too). When the run ends, `_on_final` settles the generation with D7's `generate.settle` (a
version with its corrections, the draft replaced unless the person wrote it since) and queues
its render.

The input holds ids, the language, the template's soft limits (numbers) and the fake backend's
`script`: never a text of the person. Logs carry ids, statuses and codes.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import queue as queues
from app.agents import credentials, definitions
from app.agents import runs as agent_runs
from app.agents.models import AgentRun, CredentialKind
from app.config import Settings, get_settings
from app.documents import consent, estimate, generate, profile_view, render_job, store, submit
from app.documents import content as c
from app.documents.models import (
    Author,
    BaseSource,
    Document,
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Language,
    Scope,
)
from app.documents.writer_cv import LANGUAGES, setting
from app.profile import service as profile_service

if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools (the api image has no SDK, as writer_cv).
    from app.documents import writer_base_tools as _tools  # noqa: F401

logger = logging.getLogger("app.documents.writer_base")

NAME = "documents_base"
MODEL = "claude-opus-5-5"
MODEL_API_KEY = "claude-sonnet-5-5"
PROMPT_PATH = Path(__file__).parent / "prompts" / "base.md"
TOOLS = ("read_profile_section", "submit_base")
DEFAULT_SCRIPT = "documents_base_valid"
FIRST_NAME = "My CV"  # the name of the one the app builds on its own
FILL_PROFILE = (
    "Your profile has no experience yet. Add your employers on the profile page, or upload "
    "your CV, and this CV will be written from them."
)


class BaseInput(BaseModel):
    """The input of one base CV writer run. Ids, codes and numbers only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    generation_id: uuid.UUID = Field(strict=False)
    mode: Literal["base_profile"] = "base_profile"
    language: Literal["en", "fr"] = "en"
    limits: dict[str, int | None] = Field(default_factory=dict, max_length=12)
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


def bind_generation(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """What a base writer run is bound to: `{generation_id, document_id, mode}`, ids as text.
    None (the run fails before any model call) unless the generation is this run's own and
    running, of this person, on one of their base CVs, and the run names no posting."""
    try:
        generation_id = uuid.UUID(str(run.input.get("generation_id")))
    except ValueError:
        return None
    if run.job_id is not None:
        return None
    row = db.execute(
        sa.select(DocumentGeneration, Document)
        .join(Document, Document.id == DocumentGeneration.document_id)
        .where(
            DocumentGeneration.id == generation_id,
            DocumentGeneration.run_id == run.id,
            DocumentGeneration.user_id == run.user_id,
            DocumentGeneration.status == GenerationStatus.running,
            DocumentGeneration.mode == GenerationMode.base_profile,
            Document.user_id == run.user_id,
            Document.scope == Scope.base,
        )
    ).one_or_none()
    if row is None:
        return None
    generation, document = row
    return {
        "generation_id": str(generation.id),
        "document_id": str(document.id),
        "base_document_id": None,  # a base CV is not written from another one
        "mode": generation.mode.value,
    }


@dataclass(frozen=True)
class BaseWriter(definitions.AgentDefinition):
    """The definition, with a first message built from the run's input."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        language = LANGUAGES.get(str(data.get("language")), "English")
        shown = [f"{k} {v}" for k, v in (data.get("limits") or {}).items() if v]
        limits = (
            "Soft lengths of the template, in characters: " + ", ".join(shown) + "."
            if shown
            else ""
        )
        return (
            f"Write the person's base CV from their profile. Language: {language}. {limits}".strip()
        )


async def _on_final(final: definitions.FinalRun) -> None:
    """Settle the generation (D7's `generate.settle`), then queue the render of its version."""
    if final.agent != NAME:
        return
    taken = submit.take(final.run_id)
    render = await asyncio.to_thread(
        generate.settle, final.sessions, final.run_id, final.status, taken, final.clock()
    )
    if render is None:
        return
    queue = final.services.get(generate.QUEUE_SERVICE)
    own = queue is None
    if own:
        queue = queues.Queue(final.settings.redis_url.get_secret_value())
    try:
        await render_job.enqueue(queue, render[0], render[1])
    except queues.QueueUnavailableError:
        # The row stays `queued`: D6's cron queues it again within a minute.
        logger.warning("documents.base.render_not_queued", extra={"run_id": str(final.run_id)})
    finally:
        if own:
            await queue.close()


def _definition() -> BaseWriter:
    return BaseWriter(
        name=NAME,
        description="Writes a base CV of the person from their profile.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=TOOLS,
        input_model=BaseInput,
        model=MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: MODEL_API_KEY},
        max_turns=setting("documents_generation_max_turns"),
        wall_time_s=float(setting("documents_generation_wall_s")),
        effort="medium",
        internal=True,
        live_text=False,
        bind=bind_generation,
        private_io=True,
        on_final=_on_final,
    )


BASE_WRITER = definitions.register(_definition())


# --- the fixed block and the skeleton -----------------------------------------------------------


def view_of(db: Session, user_id: uuid.UUID, at: datetime) -> profile_view.ProfileView:
    """The person's profile alone, no base CV beside it."""
    profile = profile_service.get_profile(db, user_id, at)
    return profile_view.build(profile_service.sections_view(profile), profile.revision)


def profile_fixed(view: profile_view.ProfileView) -> c.Fixed:
    """The fixed facts of the profile, as a base CV's `fixed` block."""
    return c.Fixed(
        employers=[
            c.FixedEmployer(
                key=e.key,
                organisation=e.organisation,
                title=e.title,
                start=e.start,
                end=e.end,
                location=e.location,
            )
            for e in view.employers
        ],
        schools=[
            c.FixedSchool(
                school=s.school, degree=s.degree, start=s.start, end=s.end, location=s.location
            )
            for s in view.schools
        ],
        languages=[c.FixedLanguage(name=x.name, level=x.level) for x in view.languages],
    )


def skeleton(view: profile_view.ProfileView) -> c.BaseCvContent:
    """What a base CV holds before its writer ran: the fixed block, and with no employer in the
    profile, the note that says what to fill."""
    notes = FILL_PROFILE if not view.employers else ""
    return c.BaseCvContent(fixed=profile_fixed(view), notes=notes)


# --- waiting, building, starting ----------------------------------------------------------------


def wait_reason(db: Session, user_id: uuid.UUID) -> GenerationWait | None:
    """Why a base generation of this person cannot start now, None when it can."""
    if not consent.has_consented(db, user_id):
        return GenerationWait.consent
    if credentials.get(db, user_id) is None:
        return GenerationWait.credential
    return None


@dataclass(frozen=True)
class Built:
    document: Document
    generation: DocumentGeneration | None  # None: an empty profile, nothing to write from


def build_from_profile(
    db: Session,
    user_id: uuid.UUID,
    *,
    at: datetime,
    source: BaseSource = BaseSource.built,
    trigger: GenerationTrigger = GenerationTrigger.click,
    name: str | None = None,
    language: Language = Language.en,
) -> Built:
    """A base CV from the profile: the row with its skeleton, and its generation (module
    docstring). Raises store.BaseLimitError at the most base CVs. The caller commits, then
    `start_ready` and `generate.enqueue_run`."""
    view = view_of(db, user_id, at)
    document = store.create_base(
        db,
        user_id,
        name or FIRST_NAME,
        source,
        at=at,
        content=skeleton(view),
        language=language,
        written_by=Author.writer,
    )
    if not view.employers:
        logger.info("documents.base.skeleton", extra={"document_id": str(document.id)})
        return Built(document, None)
    figures = estimate.for_base(db, user_id)
    reason = wait_reason(db, user_id)
    generation = generate.create(
        db,
        document,
        GenerationMode.base_profile,
        trigger,
        None,
        estimate.as_json(figures),
        figures.ceiling,
        at=at,
        status=GenerationStatus.waiting if reason is not None else GenerationStatus.queued,
        wait_reason=reason,
    )
    return Built(document, generation)


def _script(settings: Settings, script: str | None) -> str | None:
    if not settings.agents_fake_backend or settings.app_env == "production":
        return None
    return script or DEFAULT_SCRIPT


def start(
    db: Session,
    generation: DocumentGeneration,
    *,
    at: datetime,
    script: str | None = None,
    settings: Settings | None = None,
) -> AgentRun:
    """The writer run of a base generation, `queued`, and the generation `running`. The caller
    commits, then `generate.enqueue_run`. Raises agent_runs.ActiveRunsError when the person has
    their most runs active (the generation stays as it was)."""
    settings = settings or get_settings()
    if generation.mode is not GenerationMode.base_profile:
        raise ValueError(f"not a mode of the base writer: {generation.mode.value}")
    document = store.lock_document(db, generation.user_id, generation.document_id)
    db.refresh(generation)
    if generation.status not in (GenerationStatus.queued, GenerationStatus.waiting):
        raise ValueError(f"the generation is {generation.status.value}: it has its run")
    if document.scope != Scope.base:
        raise ValueError("the base writer writes a base CV only")
    template = render_job.template_of(db, generation.user_id, document.template_slug)
    data: dict[str, Any] = {
        "generation_id": str(generation.id),
        "mode": generation.mode.value,
        "language": document.language.value,
        "limits": asdict(template.kinds["cv"].soft_limits),
        "token_budget": generation.token_ceiling,
    }
    chosen = _script(settings, script)
    if chosen is not None:
        data["script"] = chosen
    run = agent_runs.create_run(
        db, generation.user_id, NAME, None, BASE_WRITER.validate_input(data), BASE_WRITER.model, at
    )
    generation.run_id = run.id
    generation.status = GenerationStatus.running
    generation.wait_reason = None
    generation.started_at = at
    db.flush()
    logger.info(
        "documents.base.started",
        extra={"generation_id": str(generation.id), "run_id": str(run.id)},
    )
    return run


def start_ready(
    db: Session,
    user_id: uuid.UUID,
    *,
    at: datetime,
    script: str | None = None,
    settings: Settings | None = None,
    auto: bool = True,
) -> list[uuid.UUID]:
    """Start the person's open base generations that may start now, oldest first; the others
    keep (or get) their reason to wait. Returns the runs to enqueue after the caller's commit.
    One that meets the person's limit of active runs stays `queued` for the next call. `auto`
    False (the person turned automatic generation off): the automatic ones are left as they are."""
    query = (
        sa.select(DocumentGeneration.id)
        .where(
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.mode == GenerationMode.base_profile,
            DocumentGeneration.status.in_((GenerationStatus.waiting, GenerationStatus.queued)),
        )
        .order_by(DocumentGeneration.created_at, DocumentGeneration.id)
    )
    if not auto:
        query = query.where(DocumentGeneration.trigger != GenerationTrigger.auto)
    open_ids = list(db.scalars(query))
    started: list[uuid.UUID] = []
    reason = wait_reason(db, user_id)
    for generation_id in open_ids:
        generation = db.get(DocumentGeneration, generation_id)
        if generation is None:
            continue
        if reason is not None:
            if generation.status is GenerationStatus.waiting:
                generation.wait_reason = reason
            continue
        try:
            started.append(start(db, generation, at=at, script=script, settings=settings).id)
        except agent_runs.ActiveRunsError:
            generation.status = GenerationStatus.queued
            generation.wait_reason = None
            break
        except ValueError:
            continue  # started by another call meanwhile
    db.flush()
    return started
