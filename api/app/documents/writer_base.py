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
  pass, then code removes what remains; `get_upload_text` (an upload's text, D24).

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

**An upload** (task D24, mode `base_upload`): `build_from_upload` makes the base CV of an
ingestion document of the person (`source_document_id`, `base_source = uploaded`, empty until
its writer ran, in the language of the file, `upload.language_of`) and its generation, waiting
or queued as above. Only a file whose text ingestion read (`done`), that is a PDF or a DOCX, and
whose secret warning the person confirmed when it has one, is taken (`readable_upload`, else
`UploadRefusedError` with its code). The run is bound to that file (`upload_id` in the binding):
`get_upload_text` (`writer_base_tools`) answers its text, at most UPLOAD_MAX_CHARS characters
(`upload_text`). Its submit is checked against the file: the fixed block keeps what the text
states (`upload.apply`), the experience entries may name the fixed block's employers only, and
the fact check takes the profile, the fixed block and the text as sources (`upload_context`).
The corrections of the fixed block join the version's (`keep_fixed`, then `_on_final`). When a
conversion is done, `_on_final` calls the design capture's hook (`design_capture.enqueue`). A
file deleted in ingestion before its run starts fails the generation (`binding_gone`); the base
CV stays, as it does when the file is deleted after.

The input holds ids, the language, the template's soft limits (numbers) and the fake backend's
`script`: never a text of the person. Logs carry ids, statuses and codes.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import threading
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
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
from app.documents import (
    consent,
    design_capture,
    estimate,
    generate,
    profile_view,
    render_job,
    store,
    submit,
    upload,
)
from app.documents import content as c
from app.documents.corrections import Correction
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
from app.documents.sources import sources_for
from app.documents.writer_cv import LANGUAGES, setting
from app.ingestion.documents import DOCX_MIME
from app.ingestion.models import IngestionDocument, TextStatus
from app.profile import service as profile_service

if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools (the api image has no SDK, as writer_cv).
    from app.documents import writer_base_tools as _tools  # noqa: F401

logger = logging.getLogger("app.documents.writer_base")

NAME = "documents_base"
MODEL = "claude-opus-5-5"
MODEL_API_KEY = "claude-sonnet-5-5"
PROMPT_PATH = Path(__file__).parent / "prompts" / "base.md"
TOOLS = ("read_profile_section", "get_upload_text", "submit_base")
MODES = (GenerationMode.base_profile, GenerationMode.base_upload)
DEFAULT_SCRIPTS = {
    GenerationMode.base_profile: "documents_base_valid",
    GenerationMode.base_upload: "documents_base_upload_valid",
}
FIRST_NAME = "My CV"  # the name of the one the app builds on its own
UPLOADED_NAME = "My uploaded CV"  # an upload whose file name leaves nothing
# What the reader gets of an upload, and what sources its fact check: one tool result stays
# inline up to these sizes (ingestion's READ_MAX_CHARS and READ_MAX_BYTES).
UPLOAD_MAX_CHARS = 30_000
UPLOAD_MAX_BYTES = 40_000
UPLOAD_MIMES = frozenset(("application/pdf", DOCX_MIME))
FILL_PROFILE = (
    "Your profile has no experience yet. Add your employers on the profile page, or upload "
    "your CV, and this CV will be written from them."
)


class BaseInput(BaseModel):
    """The input of one base CV writer run. Ids, codes and numbers only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    generation_id: uuid.UUID = Field(strict=False)
    mode: Literal["base_profile", "base_upload"] = "base_profile"
    language: Literal["en", "fr"] = "en"
    limits: dict[str, int | None] = Field(default_factory=dict, max_length=12)
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


def bind_generation(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """What a base writer run is bound to: `{generation_id, document_id, mode, upload_id}`, ids
    as text (`upload_id` None for a build from the profile). None (the run fails before any model
    call) unless the generation is this run's own and running, of this person, on one of their
    base CVs, the run names no posting, and an upload's file is still the base CV's."""
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
            DocumentGeneration.mode.in_(MODES),
            Document.user_id == run.user_id,
            Document.scope == Scope.base,
        )
    ).one_or_none()
    if row is None:
        return None
    generation, document = row
    upload_id = None
    if generation.mode is GenerationMode.base_upload:
        if document.source_document_id is None:
            return None  # the file was deleted in ingestion
        upload_id = str(document.source_document_id)
    return {
        "generation_id": str(generation.id),
        "document_id": str(document.id),
        "base_document_id": None,  # a base CV is not written from another one
        "mode": generation.mode.value,
        "upload_id": upload_id,
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
        if data.get("mode") == GenerationMode.base_upload.value:
            return (
                "Convert the person's uploaded CV into their base CV: read it with "
                f"get_upload_text and follow the upload mode. Language: {language}. {limits}"
            ).strip()
        return (
            f"Write the person's base CV from their profile. Language: {language}. {limits}".strip()
        )


# The corrections of an upload's fixed block, by run, from its accepted submit to its final hook.
# A run whose hook never takes them is forgotten past FIXED_KEPT, the oldest first.
FIXED_KEPT = 64
_FIXED: dict[uuid.UUID, tuple[Correction, ...]] = {}
_FIXED_LOCK = threading.Lock()


def keep_fixed(run_id: uuid.UUID, records: tuple[Correction, ...]) -> None:
    with _FIXED_LOCK:
        _FIXED.pop(run_id, None)
        _FIXED[run_id] = records
        while len(_FIXED) > FIXED_KEPT:
            del _FIXED[next(iter(_FIXED))]


def take_fixed(run_id: uuid.UUID) -> tuple[Correction, ...]:
    with _FIXED_LOCK:
        return _FIXED.pop(run_id, ())


def _with_fixed(taken: submit.Taken, records: tuple[Correction, ...]) -> submit.Taken:
    if not records or taken.accepted is None:
        return taken
    accepted = taken.accepted
    return replace(taken, accepted=replace(accepted, corrections=(*records, *accepted.corrections)))


def _converted(sessions: Any, run_id: uuid.UUID) -> uuid.UUID | None:
    """The base CV an upload's conversion run has just written, None for anything else."""
    with sessions() as db:
        found: uuid.UUID | None = db.scalar(
            sa.select(DocumentGeneration.document_id).where(
                DocumentGeneration.run_id == run_id,
                DocumentGeneration.mode == GenerationMode.base_upload,
                DocumentGeneration.status == GenerationStatus.done,
            )
        )
        return found


async def _on_final(final: definitions.FinalRun) -> None:
    """Settle the generation (D7's `generate.settle`), queue the render of its version, then
    hand a converted upload to the design capture."""
    if final.agent != NAME:
        return
    taken = _with_fixed(submit.take(final.run_id), take_fixed(final.run_id))
    render = await asyncio.to_thread(
        generate.settle, final.sessions, final.run_id, final.status, taken, final.clock()
    )
    if render is not None:
        await _queue_render(final, render)
    converted = await asyncio.to_thread(_converted, final.sessions, final.run_id)
    if converted is not None:
        await design_capture.enqueue(final, converted)


async def _queue_render(final: definitions.FinalRun, render: tuple[uuid.UUID, int]) -> None:
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


# --- an upload ----------------------------------------------------------------------------------


class UploadRefusedError(Exception):
    """An ingestion document a base CV cannot be converted from. `code`: `not_found` (not the
    person's), `failed` (its text could not be read: `reason` is ingestion's sentence),
    `reading` (not read yet), `pictures` (pages of pictures waiting for a transcription),
    `secret` (a secret warning not confirmed), `not_a_cv` (neither a PDF nor a DOCX)."""

    def __init__(self, code: str, reason: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class Upload:
    id: uuid.UUID
    name: str
    characters: int  # of the text the reader gets


def readable_upload(db: Session, user_id: uuid.UUID, upload_id: uuid.UUID) -> Upload:
    """The person's ingestion document a base CV may be converted from, taken `FOR KEY SHARE`
    until the caller's commit: a deletion in ingestion (`FOR UPDATE`) waits for the base CV that
    points at it, `store.delete_base` (lock 1b, `FOR NO KEY UPDATE`) does not. Raises
    UploadRefusedError."""
    row = db.execute(
        sa.select(
            IngestionDocument.name,
            IngestionDocument.mime,
            IngestionDocument.text_status,
            IngestionDocument.failed_reason,
            IngestionDocument.secret_warning,
            IngestionDocument.secret_confirmed_at,
            sa.func.char_length(IngestionDocument.text),
        )
        .where(IngestionDocument.id == upload_id, IngestionDocument.user_id == user_id)
        .with_for_update(key_share=True, read=True)
    ).one_or_none()
    if row is None:
        raise UploadRefusedError("not_found")
    name, mime, status, reason, warning, confirmed, characters = row
    if status is TextStatus.failed:
        raise UploadRefusedError("failed", reason)
    if status is TextStatus.pending:
        raise UploadRefusedError("reading")
    if status is TextStatus.awaiting_transcription:
        raise UploadRefusedError("pictures")
    if mime not in UPLOAD_MIMES:
        raise UploadRefusedError("not_a_cv")
    if warning and confirmed is None:
        raise UploadRefusedError("secret")
    return Upload(upload_id, name, min(characters or 0, UPLOAD_MAX_CHARS))


def _clip(text: str) -> str:
    text = text[:UPLOAD_MAX_CHARS]
    data = text.encode("utf-8", "surrogatepass")
    if len(data) <= UPLOAD_MAX_BYTES:
        return text
    return data[:UPLOAD_MAX_BYTES].decode("utf-8", "ignore")


def upload_text(db: Session, user_id: uuid.UUID, upload_id: uuid.UUID | None) -> str | None:
    """What the reader of an upload gets and its fact check reads: the head of the document's
    text, only while `readable_upload` would take it; None otherwise."""
    if upload_id is None:
        return None
    try:
        readable_upload(db, user_id, upload_id)
    except UploadRefusedError:
        return None
    text = db.scalar(
        sa.select(IngestionDocument.text).where(
            IngestionDocument.id == upload_id, IngestionDocument.user_id == user_id
        )
    )
    return _clip(text) if text else None


def _uploaded_name(file_name: str) -> str:
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    return " ".join(stem.split())[: store.NAME_MAX] or UPLOADED_NAME


def build_from_upload(
    db: Session,
    user_id: uuid.UUID,
    upload_id: uuid.UUID,
    *,
    at: datetime,
    name: str | None = None,
    language: Language | None = None,
) -> Built:
    """A base CV converted from an ingestion document: the empty row, in the file's language
    unless one is given, and its `base_upload` generation, `queued` or `waiting` (module
    docstring). Raises UploadRefusedError, store.BaseLimitError. The caller commits, then
    `start_ready` and `generate.enqueue_run`."""
    # The store's lock order: the person (1), then the upload (1b), as `delete_base` takes them.
    store.lock_person(db, user_id)
    store.lock_source(db, user_id, upload_id)
    found = readable_upload(db, user_id, upload_id)
    if language is None:
        language = upload.language_of(upload_text(db, user_id, upload_id) or "")
    document = store.create_base(
        db,
        user_id,
        name or _uploaded_name(found.name),
        BaseSource.uploaded,
        at=at,
        content=c.BaseCvContent(),
        language=language,
        written_by=Author.upload,
        source_document_id=upload_id,
    )
    figures = estimate.writer(
        estimate.prompt_chars("base"),
        estimate.profile_chars(db, user_id) + found.characters,
        **estimate.limits(),
    )
    reason = wait_reason(db, user_id)
    generation = generate.create(
        db,
        document,
        GenerationMode.base_upload,
        GenerationTrigger.click,
        None,
        estimate.as_json(figures),
        figures.ceiling,
        at=at,
        status=GenerationStatus.waiting if reason is not None else GenerationStatus.queued,
        wait_reason=reason,
    )
    return Built(document, generation)


def upload_context(
    db: Session,
    user_id: uuid.UUID,
    context: submit.WriterContext,
    fixed: c.Fixed,
    text: str,
    at: datetime,
) -> submit.WriterContext:
    """The checks of an upload's submit: the experience names the employers of the fixed block
    the text states, and the profile, that block and the text are the sources. A name the text
    writes sources an organisation or a technology ("with FastAPI and PostgreSQL") as a skills
    term of the profile does: the text is one more entry of `known_terms`."""
    base = c.BaseCvContent(fixed=fixed)
    profile = profile_service.get_profile(db, user_id, at)
    view = profile_view.build(profile_service.sections_view(profile), profile.revision, base)
    sources = sources_for(view, base, text, None, kind="base")
    return replace(
        context,
        sources=replace(sources, known_terms=(*sources.known_terms, text)),
        employer_keys=tuple(employer.key for employer in fixed.employers),
    )


def _script(settings: Settings, mode: GenerationMode, script: str | None) -> str | None:
    if not settings.agents_fake_backend or settings.app_env == "production":
        return None
    return script or DEFAULT_SCRIPTS[mode]


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
    if generation.mode not in MODES:
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
    chosen = _script(settings, generation.mode, script)
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


def _file_gone(db: Session, generation: DocumentGeneration) -> bool:
    """An upload's conversion whose file was deleted in ingestion: nothing is left to read."""
    if generation.mode is not GenerationMode.base_upload:
        return False
    source = db.scalar(
        sa.select(Document.source_document_id).where(
            Document.id == generation.document_id, Document.user_id == generation.user_id
        )
    )
    return source is None


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
            DocumentGeneration.mode.in_(MODES),
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
        if _file_gone(db, generation):
            generation.status = GenerationStatus.failed
            generation.wait_reason = None
            generation.error = "binding_gone"
            generation.finished_at = at
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
