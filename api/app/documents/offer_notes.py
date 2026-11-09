"""Reading an offer with the person (owner 2026-11-07; SPEC-documents.md, "Reading an offer").

The person uploads the offer letter they received through ingestion's upload, so its parsing,
its secret check and its deletion are ingestion's. Their click (`read`) writes the posting's
document of kind `offer_notes` from it with one run of `documents_offer_read`, under ingestion's
consent (`refusal`): each clause in plain words beside its own words, what differs from the
posting's stated pay and from the person's `search.compensation` (`pay_context`), and questions
for a lawyer, in the letter's own language. No field holds a verdict or a score, the prompt
forbids both and any web search, and a submit that tells the person to sign is refused
(`VERDICT`). The dash rule applies to every field but the quotes, which stay word for word.

A message in the chat of the notes reaches `post_message` through `chat.post`: the documents
chat's routes, messages and Stop, with the agent `documents_chat_offer_notes`.

Both agents are `internal`, bound to one running generation of the person's notes on the run's
own posting (`bind`), `private_io`, with the writers' turns and wall time and the generation's
ceiling as their token budget. Logs carry ids, statuses and codes, never a text.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials, definitions
from app.agents import runs as agent_runs
from app.agents.models import AgentRun, CredentialKind, RunStatus
from app.config import Settings, get_settings
from app.documents import chat, corrections, estimate, generate, store, submit, upload, writer_base
from app.documents import content as c
from app.documents.models import (
    Author,
    Document,
    DocumentGeneration,
    GenerationMode,
    GenerationOutcome,
    GenerationStatus,
    GenerationTrigger,
    Kind,
    Origin,
    Role,
    Scope,
)
from app.documents.settings import DocumentSettings
from app.documents.writer_cv import LANGUAGES, setting
from app.ingestion.estimate import CHARS_PER_TOKEN
from app.ingestion.estimate_router import has_consented
from app.ingestion.models import IngestionDocument
from app.jobs.models import Job
from app.opportunities.pay import pay_text
from app.profile.models import Profile
from app.profile.schemas import Compensation

logger = logging.getLogger("app.documents.offer_notes")

READ = "documents_offer_read"
CHAT = "documents_chat_offer_notes"
MODEL = "claude-opus-5-5"
MODEL_API_KEY = "claude-sonnet-5-5"
PROMPTS = Path(__file__).parent / "prompts"
PROMPT, CHAT_PROMPT = "offer_notes", "offer_notes_chat"
GET_OFFER_TEXT, GET_PAY_CONTEXT, SUBMIT = "get_offer_text", "get_pay_context", "submit_offer_notes"
READ_TOOLS = (GET_OFFER_TEXT, GET_PAY_CONTEXT, SUBMIT)
CHAT_TOOLS = (GET_OFFER_TEXT, GET_PAY_CONTEXT, "get_draft", chat.GET_CONVERSATION, SUBMIT)
# What the pay context adds to a run's reads, in characters, over the letter's text.
CONTEXT_CHARS = 1_000
CHANGED = "The notes changed: version {number}."
DASH_RULE = DocumentSettings(no_dash_separators=True)
# Telling the person to sign, or that signing is safe, in English or French: refused at submit.
# Quotes are the letter's own words and are not read.
VERDICT = re.compile(
    r"\b(?:safe|ok|okay|fine|good)\s+(?:for\s+you\s+)?to\s+sign\b"
    r"|\byou\s+should\s+(?:not\s+)?sign\b"
    r"|\brecommend\s+(?:that\s+you\s+)?(?:not\s+)?sign(?:ing)?\b"
    r"|\bgo\s+ahead\s+and\s+sign\b"
    r"|\bsigner\s+(?:\w+\s+)?sans\s+(?:crainte|risque|danger|inqui[ée]tude)\b"
    r"|\bvous\s+devriez\s+(?:ne\s+pas\s+)?(?:la\s+|le\s+|les\s+)?signer\b"
    r"|\brecommande\s+de\s+(?:ne\s+pas\s+)?(?:la\s+|le\s+|les\s+)?signer\b",
    re.IGNORECASE,
)


class OfferRefusedError(Exception):
    """`reason`: `consent` or `credential`, missing before any model call."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def refusal(db: Session, user_id: uuid.UUID) -> str | None:
    """What the person must settle before a run reads their letter, None when nothing."""
    if not has_consented(db, user_id):
        return "consent"
    if credentials.get(db, user_id) is None:
        return "credential"
    return None


# --- what the agents read -----------------------------------------------------------------------


def _sought(raw: object) -> list[str]:
    try:
        found = Compensation.model_validate(raw or {})
    except ValidationError:
        return []
    pairs = ((found.salary, "year"), (found.daily_rate, "day"))
    return [pay_text(r.min, r.max, r.currency, period) for r, period in pairs if r is not None]


def pay_context(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> dict[str, Any] | None:
    """What the notes compare the letter with: the posting as it was stated, and the pay the
    person seeks. None for a posting that is not the person's."""
    job = db.execute(
        sa.select(Job).where(Job.id == posting_id, Job.user_id == user_id)
    ).scalar_one_or_none()
    if job is None:
        return None
    sections = db.scalar(sa.select(Profile.sections).where(Profile.user_id == user_id)) or {}
    stated = None
    if job.pay_min is not None and job.pay_max is not None and job.pay_period is not None:
        stated = pay_text(job.pay_min, job.pay_max, str(job.pay_currency), job.pay_period.value)
    return {
        "posting": {
            "title": job.title,
            "company": job.company,
            "contract": job.contract_type.value,
            "location": job.location or None,
            "work_mode": job.work_mode.value if job.work_mode is not None else None,
            "pay": stated,
        },
        "pay_sought": _sought((sections.get("search") or {}).get("compensation")),
    }


@dataclass(frozen=True, slots=True)
class Letter:
    """The letter's text as the agents read it (at most writer_base.UPLOAD_MAX_CHARS
    characters), and the characters of the whole letter."""

    text: str
    total: int

    @property
    def truncated(self) -> bool:
        return len(self.text) < self.total


def letter_read(db: Session, user_id: uuid.UUID, upload_id: uuid.UUID) -> Letter | None:
    """The person's letter as the agents read it, None once ingestion no longer holds it."""
    text = writer_base.upload_text(db, user_id, upload_id)
    if text is None:
        return None
    total = db.scalar(
        sa.select(sa.func.char_length(IngestionDocument.text)).where(
            IngestionDocument.id == upload_id, IngestionDocument.user_id == user_id
        )
    )
    return Letter(text, max(total or 0, len(text)))


def offer_text(db: Session, user_id: uuid.UUID, binding: Mapping[str, Any] | None) -> Letter | None:
    """The bound letter as the agents read it, None once ingestion no longer holds it."""
    upload_id = (binding or {}).get("upload_id")
    if not upload_id:
        return None
    return letter_read(db, user_id, uuid.UUID(str(upload_id)))


def _read_chars(db: Session, user_id: uuid.UUID, upload_id: uuid.UUID) -> int | None:
    """The characters the agents read of a letter longer than they read, None otherwise. The
    text itself is loaded only for such a letter."""
    text = IngestionDocument.text
    found = db.execute(
        sa.select(
            sa.func.char_length(text),
            sa.func.octet_length(sa.func.left(text, writer_base.UPLOAD_MAX_CHARS)),
        ).where(IngestionDocument.id == upload_id, IngestionDocument.user_id == user_id)
    ).one_or_none()
    if found is None or found[0] is None:
        return None
    chars, head = found
    if chars <= writer_base.UPLOAD_MAX_CHARS and head <= writer_base.UPLOAD_MAX_BYTES:
        return None
    read = letter_read(db, user_id, upload_id)
    return len(read.text) if read is not None and read.truncated else None


# --- the agents ---------------------------------------------------------------------------------


class OfferInput(BaseModel):
    """The input of one run. Ids, codes and numbers only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    generation_id: uuid.UUID = Field(strict=False)
    mode: Literal["posting", "chat"]
    language: Literal["en", "fr"] = "en"
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,60}$")


def bind_for(db: Session, run: AgentRun, mode: GenerationMode) -> Mapping[str, Any] | None:
    """`{generation_id, document_id, posting_id, upload_id}`; None (the run fails before any
    model call) unless the generation is this run's own, running, of `mode`, on the person's
    offer notes of the run's own posting."""
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
            DocumentGeneration.mode == mode,
            Document.user_id == run.user_id,
            Document.scope == Scope.posting,
            Document.kind == Kind.offer_notes,
        )
    ).one_or_none()
    if row is None:
        return None
    generation, document = row
    if run.job_id is None or document.posting_id != run.job_id:
        return None
    source = document.source_document_id
    return {
        "generation_id": str(generation.id),
        "document_id": str(document.id),
        "posting_id": str(document.posting_id),
        "upload_id": str(source) if source is not None else None,
    }


def _bind(mode: GenerationMode) -> definitions.Bind:
    def bind(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
        return bind_for(db, run, mode)

    return bind


@dataclass(frozen=True)
class OfferReader(definitions.AgentDefinition):
    """Both agents of the notes, with a first message built from the run's input."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        language = LANGUAGES.get(str(data.get("language")), "English")
        if data.get("mode") == "chat":
            text = (
                "The person wrote in the chat of their notes on an offer letter. Read the "
                "conversation with get_conversation: its last message is the request. Read the "
                "notes with get_draft, and the letter with get_offer_text when the change needs "
                "it. Make the change asked and nothing else: submit the whole notes with "
                "submit_offer_notes when they change."
            )
        else:
            text = (
                "Read the offer letter the person received for this posting, and write your "
                "notes on it with submit_offer_notes: each clause in plain words, what differs "
                "from the posting and from the pay the person seeks, and questions for a lawyer."
            )
        return f"{text} Language of the letter and of the notes: {language}."


async def _on_final_read(final: definitions.FinalRun) -> None:
    taken = take(final.run_id)
    await asyncio.to_thread(
        generate.settle, final.sessions, final.run_id, final.status, taken, final.clock()
    )


async def _on_final_chat(final: definitions.FinalRun) -> None:
    taken = take(final.run_id)
    await asyncio.to_thread(
        settle_chat, final.sessions, final.run_id, final.status, taken, final.clock()
    )


def _definition(name: str, mode: GenerationMode) -> OfferReader:
    reading = mode is GenerationMode.posting
    prompt = (PROMPTS / f"{PROMPT}.md").read_text(encoding="utf-8")
    if not reading:
        prompt += "\n\n" + (PROMPTS / f"{CHAT_PROMPT}.md").read_text(encoding="utf-8")
    return OfferReader(
        name=name,
        description=(
            "Reads an offer letter the person uploaded and writes notes on it: each clause in "
            "plain words, what differs from the posting and the pay sought, questions for a "
            "lawyer."
            if reading
            else "Changes the notes on an offer letter as the person asks in their chat."
        ),
        system_prompt=prompt,
        tools=READ_TOOLS if reading else CHAT_TOOLS,
        input_model=OfferInput,
        model=MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: MODEL_API_KEY},
        max_turns=setting("documents_generation_max_turns"),
        wall_time_s=float(setting("documents_generation_wall_s")),
        effort="medium",
        internal=True,
        bind_job=True,
        live_text=False,
        bind=_bind(mode),
        private_io=True,
        on_final=_on_final_read if reading else _on_final_chat,
    )


READER = definitions.register(_definition(READ, GenerationMode.posting))
CHAT_WRITER = definitions.register(_definition(CHAT, GenerationMode.chat))


# --- the submit ---------------------------------------------------------------------------------


@dataclass
class _Run:
    submits: int = 0
    accepted: submit.Accepted | None = None
    exhausted: bool = False


RUNS_KEPT = 64
_RUNS: dict[uuid.UUID, _Run] = {}
_LOCK = threading.Lock()


def _run(run_id: uuid.UUID) -> _Run:
    with _LOCK:
        found = _RUNS.pop(run_id, None) or _Run()
        _RUNS[run_id] = found
        while len(_RUNS) > RUNS_KEPT:
            del _RUNS[next(iter(_RUNS))]
        return found


def take(run_id: uuid.UUID) -> submit.Taken:
    """What the run submitted, forgotten here."""
    with _LOCK:
        found = _RUNS.pop(run_id, None) or _Run()
    return submit.Taken(found.accepted, found.submits, found.exhausted)


def _refused(state: _Run, limit: int, lines: list[str]) -> tuple[str, bool]:
    left = limit - state.submits
    state.exhausted = left <= 0
    tail = (
        f"Fix each one and submit again ({left} submit(s) left)."
        if left > 0
        else "No submit is left in this run."
    )
    return "Not stored: the notes break these rules.\n" + "\n".join(lines) + "\n" + tail, True


def handle(run_id: uuid.UUID | None, raw: object, *, gone: bool = False) -> tuple[str, bool]:
    """One `submit_offer_notes` of a run: `(answer, is_error)`. The schema, then the verdict
    rule, then the dash fix, recorded; at most DOCUMENTS_MAX_SUBMITS per run."""
    if run_id is None or gone:
        return submit.GONE, True
    state = _run(run_id)
    if state.accepted is not None:
        return submit.ALREADY, False
    limit = submit.max_submits()
    if state.submits >= limit:
        return submit.NO_MORE, True
    state.submits += 1
    try:
        content = c.parse("offer_notes", raw)
    except c.ContentInvalidError as error:
        lines = [f"- {e.path or '(the whole content)'}: {e.code}" for e in error.errors]
        return _refused(state, limit, lines)
    verdicts = [path for path, text in c.text_fields(content) if VERDICT.search(text)]
    if verdicts:
        lines = [
            f"- {path}: tells the person to sign or calls signing safe; say what the clause "
            "does and what to ask a lawyer instead"
            for path in verdicts
        ]
        return _refused(state, limit, lines)
    fixed, records = corrections.fix_separators("offer_notes", content, DASH_RULE)
    state.accepted = submit.Accepted(fixed, tuple(records))
    return submit.STORED, False


# --- starting a run -----------------------------------------------------------------------------


def _letter_chars(db: Session, document: Document) -> int:
    if document.source_document_id is None:
        return 0
    found = db.scalar(
        sa.select(sa.func.char_length(IngestionDocument.text)).where(
            IngestionDocument.id == document.source_document_id,
            IngestionDocument.user_id == document.user_id,
        )
    )
    return min(found or 0, writer_base.UPLOAD_MAX_CHARS)


def _estimate(db: Session, document: Document, mode: GenerationMode) -> tuple[Any, int]:
    """`(estimate, conversation_chars)` of one run on `document`, as `chat.estimate_for`."""
    prompt = estimate.prompt_chars(PROMPT)
    reads = _letter_chars(db, document) + CONTEXT_CHARS
    found = estimate.limits()
    if mode is GenerationMode.posting:
        return estimate.writer(prompt, reads, **found), 0
    prompt += estimate.prompt_chars(CHAT_PROMPT)
    reads += len(json.dumps(document.draft))
    plain = estimate.writer(prompt, reads, **found)
    chars = int(plain.high * chat.CONVERSATION_SHARE) // max(found["turns"] - 1, 1)
    chars *= CHARS_PER_TOKEN
    return estimate.writer(prompt, reads + chars, **found), chars


def _script(settings: Settings, name: str, script: str | None) -> str | None:
    if not settings.agents_fake_backend or settings.app_env == "production":
        return None
    return f"{name}_{script}" if script else f"{name}_valid"


def _start(
    db: Session,
    document: Document,
    generation: DocumentGeneration,
    definition: definitions.AgentDefinition,
    *,
    at: datetime,
    script: str | None,
    settings: Settings,
) -> AgentRun:
    data: dict[str, Any] = {
        "generation_id": str(generation.id),
        "mode": generation.mode.value,
        "language": document.language.value,
        "token_budget": generation.token_ceiling,
    }
    chosen = _script(settings, definition.name, script)
    if chosen is not None:
        data["script"] = chosen
    run = agent_runs.create_run(
        db,
        document.user_id,
        definition.name,
        document.posting_id,
        definition.validate_input(data),
        definition.model,
        at,
        flow=agent_runs.WorkflowRef(
            "offer_letter", trigger="notes_chat" if definition is CHAT_WRITER else "offer"
        ),
    )
    generation.run_id = run.id
    generation.status = GenerationStatus.running
    generation.started_at = at
    db.flush()
    logger.info(
        "documents.offer_notes.started",
        extra={"generation_id": str(generation.id), "run_id": str(run.id)},
    )
    return run


@dataclass(frozen=True)
class Started:
    document: Document
    generation: DocumentGeneration
    run_id: uuid.UUID  # to queue after the commit (`generate.enqueue_run`)


def read(
    db: Session,
    user_id: uuid.UUID,
    posting_id: uuid.UUID,
    upload_id: uuid.UUID,
    *,
    at: datetime,
    script: str | None = None,
    settings: Settings | None = None,
) -> Started:
    """The posting's notes written from the person's upload `upload_id`, in its language: a
    reading generation and its run, in the caller's transaction. Raises
    writer_base.UploadRefusedError, OfferRefusedError, store.NotFoundError (the posting),
    store.GenerationOpenError, store.DocumentBusyError and agent_runs.ActiveRunsError; the
    caller rolls back and nothing is kept."""
    settings = settings or get_settings()
    # The lock order of store.py: the upload (1b), the posting (2), then the notes (3).
    store.lock_source(db, user_id, upload_id)
    found = writer_base.readable_upload(db, user_id, upload_id)
    reason = refusal(db, user_id)
    if reason is not None:
        raise OfferRefusedError(reason)
    if store.lock_posting(db, user_id, posting_id) is None:
        raise store.NotFoundError(posting_id)
    document = store.get_or_create(db, user_id, posting_id, Kind.offer_notes, at=at)
    document = store.lock_document(db, user_id, document.id)
    document.source_document_id = found.id
    document.language = upload.language_of(writer_base.upload_text(db, user_id, found.id) or "")
    document.updated_at = at
    db.flush()
    figures, _ = _estimate(db, document, GenerationMode.posting)
    generation = store.create_generation(
        db,
        user_id,
        document.id,
        GenerationMode.posting,
        GenerationTrigger.click,
        at=at,
        estimate=estimate.as_json(figures),
        token_ceiling=figures.ceiling,
    )
    run = _start(db, document, generation, READER, at=at, script=script, settings=settings)
    return Started(document, generation, run.id)


def post_message(
    db: Session,
    document: Document,
    text: str,
    *,
    at: datetime,
    script: str | None,
    settings: Settings,
) -> chat.Posted:
    """`chat.post` for the notes, the document locked by it: the message and its run, under
    the same consent as the reading."""
    user_id = document.user_id
    reason = refusal(db, user_id)
    if reason is not None:
        raise chat.ChatRefusedError(reason)
    chat.recover(db, document, at)
    current = store.open_generation(db, user_id, document.id)
    if current is not None:
        raise store.GenerationOpenError(current)
    message = store.add_message(
        db,
        user_id,
        document.id,
        Role.user,
        text,
        at=at,
        max_chars=settings.documents_chat_max_chars,
        keep=settings.documents_messages_kept,
    )
    figures, chars = _estimate(db, document, GenerationMode.chat)
    generation = store.create_generation(
        db,
        user_id,
        document.id,
        GenerationMode.chat,
        GenerationTrigger.chat,
        at=at,
        estimate={**estimate.as_json(figures), "conversation_chars": chars},
        token_ceiling=figures.ceiling,
        message_id=message.id,
    )
    message.generation_id = generation.id
    run = _start(db, document, generation, CHAT_WRITER, at=at, script=script, settings=settings)
    return chat.Posted(message, generation, run.id)


# --- the end of a chat run ----------------------------------------------------------------------


def _keep(
    db: Session,
    document: Document,
    generation: DocumentGeneration,
    accepted: submit.Accepted,
    at: datetime,
) -> None:
    """The chat's notes: the draft replaced when nobody wrote it since the message, else a
    version beside it."""
    user_id = document.user_id
    records = list(accepted.corrections)
    if document.draft_revision == generation.base_draft_revision:
        store.save_draft(
            db,
            user_id,
            document.id,
            c.dump(accepted.content),
            document.draft_revision,
            Author.chat,
            at=at,
        )
        version, _ = store.snapshot(
            db,
            user_id,
            document.id,
            Origin.chat,
            at=at,
            generation_id=generation.id,
            corrections=records,
        )
        generation.outcome = GenerationOutcome.draft_replaced
    else:
        version, _ = store.snapshot(
            db,
            user_id,
            document.id,
            Origin.chat,
            at=at,
            generation_id=generation.id,
            corrections=records,
            content=c.dump(accepted.content),
        )
        generation.outcome = GenerationOutcome.kept_as_version
    generation.version_number = version.number
    generation.corrections = len(records)


def settle_chat(
    sessions: sessionmaker[Session],
    run_id: uuid.UUID,
    status: RunStatus,
    taken: submit.Taken,
    at: datetime,
) -> None:
    """The chat generation of a finished run and its answer, in one transaction."""
    with sessions() as db:
        found = db.execute(
            sa.select(DocumentGeneration.user_id, DocumentGeneration.document_id).where(
                DocumentGeneration.run_id == run_id
            )
        ).one_or_none()
        if found is None:
            return
        try:
            document = store.lock_document(db, found.user_id, found.document_id)
        except store.NotFoundError:
            return
        generation = db.execute(
            sa.select(DocumentGeneration)
            .where(DocumentGeneration.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        if generation.status is not GenerationStatus.running:
            return
        done = status is RunStatus.done and not taken.exhausted
        if done and (taken.accepted is not None or taken.submits == 0):
            text = chat.NOTHING
            if taken.accepted is not None:
                _keep(db, document, generation, taken.accepted, at)
                text = CHANGED.format(number=generation.version_number)
            generation.status = GenerationStatus.done
            generation.finished_at = at
            db.flush()
            store.add_message(
                db,
                document.user_id,
                document.id,
                Role.assistant,
                text,
                at=at,
                generation_id=generation.id,
                version_number=generation.version_number,
            )
        else:
            chat.conclude(
                db,
                document,
                generation,
                status,
                db.get(AgentRun, run_id),
                None,
                taken.exhausted,
                chat.Asked(),
                at,
                taken.submits,
            )
        logger.info(
            "documents.offer_notes.chat",
            extra={"generation_id": str(generation.id), "status": generation.status.value},
        )
        store.commit(db)


# --- what the posting page reads ----------------------------------------------------------------


@dataclass(frozen=True)
class View:
    document: Document | None
    latest: int | None  # the newest version's number
    reading: DocumentGeneration | None  # the newest reading, whatever its status
    busy: bool  # a reading or a chat change is open
    letter: tuple[uuid.UUID, str] | None  # the file read and its name, None once deleted
    pay: dict[str, Any]
    read_chars: int | None = None  # what was read of a letter longer than that, else None


def _settle_stale(db: Session, user_id: uuid.UUID, document_id: uuid.UUID, at: datetime) -> None:
    """A chat change whose run ended without its final hook, settled by `chat.recover` and
    committed, as the documents chat's own read does; a locked row is left for the next read."""
    found = store.open_generation(db, user_id, document_id)
    if found is None or found.mode is not GenerationMode.chat:
        return
    try:
        document = store.lock_document(db, user_id, document_id)
        if chat.recover(db, document, at):
            store.commit(db)
        else:
            db.rollback()
    except (store.DocumentBusyError, store.NotFoundError):
        db.rollback()


def view(
    db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, at: datetime | None = None
) -> View | None:
    """The posting's notes as its page shows them; None for a posting that is not the
    person's. With `at`, a chat change whose hook was lost is settled first (`_settle_stale`),
    so the notes do not stay busy."""
    context = pay_context(db, user_id, posting_id)
    if context is None:
        return None
    document = next(
        (
            found
            for found in store.posting_documents(db, user_id, posting_id)
            if found.kind == Kind.offer_notes
        ),
        None,
    )
    if document is None:
        return View(None, None, None, False, None, context)
    if at is not None:
        _settle_stale(db, user_id, document.id, at)
    versions = store.versions(db, user_id, document.id)
    reading = db.execute(
        sa.select(DocumentGeneration)
        .where(
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.document_id == document.id,
            DocumentGeneration.mode == GenerationMode.posting,
        )
        .order_by(DocumentGeneration.created_at.desc(), DocumentGeneration.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    name = None
    if document.source_document_id is not None:
        name = db.scalar(
            sa.select(IngestionDocument.name).where(
                IngestionDocument.id == document.source_document_id,
                IngestionDocument.user_id == user_id,
            )
        )
    letter = (document.source_document_id, name) if document.source_document_id and name else None
    return View(
        document,
        max((v.number for v in versions), default=None),
        reading,
        store.open_generation(db, user_id, document.id) is not None,
        letter,
        context,
        _read_chars(db, user_id, letter[0]) if letter is not None else None,
    )


if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools (the api image has no SDK); last, as they read this
    # module's names when they register.
    from app.documents import offer_tools as _tools  # noqa: F401
