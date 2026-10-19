"""The chat of a document (task D17; SPEC-documents.md, "The writers", "Being careful, and the
automatic correction", "API", "Pages", "Privacy", "Limits").

A message of the person in a document's chat is a row of `document_messages` and a generation
of mode `chat` (trigger `chat`, `message_id` the message), started at once with one run of the
chat writer of the document's kind (`post`):

- `documents_chat_cv`, `documents_chat_letter`, `documents_chat_base`: the writer of the kind
  (the base CV writer for a base CV), its prompt followed by `prompts/chat.md`, its own tools,
  and the chat's three: `get_draft`, `get_conversation` and `set_template(slug)`
  (`chat_tools.py`, worker only). Every tool reads the run's binding (`bind`), decided in code:
  the generation of this run, running, of this person, on this document, and for a posting's
  document the run's job is its posting. No tool takes an id, so the person's message, which is
  untrusted input to a model with tools, can point the run at nothing else.
- `internal`, `private_io`, `live_text=False`: what the tools read and answer is never stored,
  so the chat's answer is not the model's text: code writes it (`answer`), from what was stored.
  A private run cannot be a `chat` agent of the agents module, so the page polls the generation.
- The input holds ids, codes and numbers (the generation, the kind, the language, the soft
  limits, the token budget, the fake backend's `script`): never the message. The model reads
  the conversation through `get_conversation`: the latest messages up to this one, within
  `conversation_chars` (`estimate_for`: at most CONVERSATION_SHARE of the run's high estimate,
  over the turns that send it again), the newest always.

**Before a run starts:** the consent of the current text and a model credential (the rule of
every model call of the module, `writer_base.wait_reason`), else nothing is stored and the
route answers 409 with what to settle: a waiting chat would hold the document's one open
generation, and nothing would start it. One open generation per document (409 while one runs);
the person's most active runs (429). The message is at most DOCUMENTS_CHAT_MAX_CHARS characters
once its control characters are dropped (`clean`); the document keeps its latest
DOCUMENTS_MESSAGES_KEPT messages (`store.add_message` prunes the oldest).

**The result** (`settle`, from the final hook, in one transaction under the document's lock):
the submit goes through D7's pipeline (`submit.handle`: the schema, the dash fix, the fact
check, one correction pass, then code removes what remains; a chat message is never a source).
The draft is replaced with a version of origin `chat` when nobody wrote it since the message,
else the result is kept as a version beside the person's draft. A base CV keeps its own fixed
block: what the writer sent as `fixed` is replaced by the draft's. A template asked through
`set_template` is applied with the result (the version carries it), or alone as a version of
origin `template` with the same content (`render_job.switch`). A run that ends any other way
changes nothing (`failed` with a code of `ERRORS`, or `cancelled`). Every end writes the
assistant's message: what changed, what the fact check corrected (and the profile, when a claim
it does not state went), the version number, or why nothing changed; an unknown template
asked lists the gallery. A run whose final hook was missed is settled the same way, as failed,
by the next read of its generation or its chat (`recover`).

Logs carry ids, sizes, counts and codes, never a message or a content.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import definitions, failures
from app.agents import runs as agent_runs
from app.agents.models import AgentRun, CredentialKind, RunStatus
from app.config import Settings, get_settings
from app.documents import content as c
from app.documents import corrections as fixes
from app.documents import estimate, generate, render_job, store, submit, templates, writer_base
from app.documents.corrections import Correction
from app.documents.models import (
    Author,
    Channel,
    Document,
    DocumentGeneration,
    DocumentMessage,
    GenerationMode,
    GenerationOutcome,
    GenerationStatus,
    GenerationTrigger,
    Kind,
    Origin,
    Role,
    Scope,
)
from app.documents.writer_cv import LANGUAGES, limits_line, setting
from app.ingestion.estimate import CHARS_PER_TOKEN
from app.profile.models import Profile

logger = logging.getLogger("app.documents.chat")

ChatKind = Literal["cv", "letter", "base"]
KINDS: tuple[ChatKind, ...] = ("cv", "letter", "base")
NAMES: dict[str, str] = {kind: f"documents_chat_{kind}" for kind in KINDS}
MODEL = "claude-opus-5-5"
MODEL_API_KEY = "claude-sonnet-5-5"
PROMPTS = Path(__file__).parent / "prompts"
GET_DRAFT = "get_draft"
GET_CONVERSATION = "get_conversation"
SET_TEMPLATE = "set_template"
CHAT_TOOLS = (GET_DRAFT, GET_CONVERSATION, SET_TEMPLATE)
TOOLS: dict[str, tuple[str, ...]] = {
    "cv": ("get_job", "read_profile_section", "get_base_cv", *CHAT_TOOLS, "submit_cv"),
    "letter": (
        "get_job",
        "read_profile_section",
        "get_base_cv",
        "get_document",
        *CHAT_TOOLS,
        "submit_letter",
    ),
    "base": ("read_profile_section", *CHAT_TOOLS, "submit_base"),
}
SUBMITS = {"cv": "submit_cv", "letter": "submit_letter", "base": "submit_base"}
WHAT = {"cv": "CV", "letter": "cover letter", "base": "base CV"}
DEFAULT_SCRIPTS = {kind: f"documents_chat_{kind}_valid" for kind in KINDS}
# The conversation adds at most this share to the run's high estimate.
CONVERSATION_SHARE = Fraction(1, 10)
ERRORS = ("invalid_output", "token_limit", "binding_gone", "run_failed")
FINAL = (RunStatus.done, RunStatus.failed, RunStatus.cancelled)
HOOK_GRACE_S = 120  # as the tick's: a final hook ends within the runner's own limit
QUOTE_MAX = 80
CORRECTIONS_LISTED = 5

NO_CONSENT = "Confirm what is sent to the model first, then send your message again."
NO_CREDENTIAL = "Add a model credential on the Account page first, then send your message again."
SUGGEST_PROFILE = "If it is true, add it to your profile, then ask again."
FAILED = {
    "invalid_output": "The assistant could not write a valid document. Nothing changed.",
    "token_limit": "This change stopped at its limit. Nothing changed. Try a smaller request.",
    "binding_gone": "This document changed under the assistant. Nothing changed. Try again.",
    "run_failed": "The assistant could not answer. Nothing changed. Try again.",
}
CANCELLED = "Stopped before any change."
NOTHING = "Nothing in the document changed."


class ChatInput(BaseModel):
    """The input of one chat writer run. Ids, codes and numbers only: never the message."""

    model_config = ConfigDict(extra="forbid", strict=True)

    generation_id: uuid.UUID = Field(strict=False)
    mode: Literal["chat"] = "chat"
    kind: ChatKind
    language: Literal["en", "fr"] = "en"
    limits: dict[str, int | None] = Field(default_factory=dict, max_length=12)
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


def kind_of(document: Document) -> ChatKind:
    return "base" if document.scope == Scope.base else document.kind.value  # type: ignore[return-value]


# --- the binding --------------------------------------------------------------------------------


def bind_for(db: Session, run: AgentRun, kind: ChatKind) -> Mapping[str, Any] | None:
    """What a chat run is bound to: `{generation_id, document_id, posting_id, base_document_id,
    mode, message_id}`. None (the run fails before any model call) unless the generation is this
    run's own, running, of mode chat and of this person, on one of their documents of `kind`; a
    posting's document needs the run's job to be its posting, a base CV a run with no job."""
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
            DocumentGeneration.mode == GenerationMode.chat,
            Document.user_id == run.user_id,
        )
    ).one_or_none()
    if row is None:
        return None
    generation, document = row
    if kind_of(document) != kind:
        return None
    if document.scope == Scope.posting:
        if run.job_id is None or run.job_id != document.posting_id:
            return None
    elif run.job_id is not None:
        return None
    base = generation.base_document_id or document.base_document_id
    return {
        "generation_id": str(generation.id),
        "document_id": str(document.id),
        "posting_id": str(document.posting_id) if document.posting_id is not None else None,
        "base_document_id": str(base) if base is not None else None,
        "mode": GenerationMode.chat.value,
        "message_id": generation.message_id,
    }


def _bind(kind: ChatKind) -> definitions.Bind:
    def bind(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
        return bind_for(db, run, kind)

    return bind


# --- what the run asked, kept in memory for the final hook (submit.py's pattern) ---------------


@dataclass
class Asked:
    template: str | None = None  # a slug the gallery serves
    unknown: bool = False  # a slug asked that it does not


RUNS_KEPT = 64
_ASKED: dict[uuid.UUID, Asked] = {}
_LOCK = threading.Lock()


def _asked(run_id: uuid.UUID) -> Asked:
    with _LOCK:
        found = _ASKED.pop(run_id, None) or Asked()
        _ASKED[run_id] = found
        while len(_ASKED) > RUNS_KEPT:
            del _ASKED[next(iter(_ASKED))]
        return found


def take(run_id: uuid.UUID) -> Asked:
    with _LOCK:
        return _ASKED.pop(run_id, None) or Asked()


# --- the tools' work, SDK free (chat_tools.py wraps it) -----------------------------------------


def gallery(db: Session, user_id: uuid.UUID) -> list[tuple[str, str]]:
    """`(slug, name)` of every template the person may use: their own first, then the bundled."""
    own = [(row.slug, row.name) for row in store.user_templates(db, user_id)]
    bundled = [(slug, t.name.get("en") or slug) for slug, t in templates.registry().items()]
    return own + bundled


def ask_template(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    run_id: uuid.UUID | None,
    binding: Mapping[str, Any] | None,
    slug: object,
) -> tuple[str, bool]:
    """`set_template(slug)`: remembered for the end of the run, never applied by the tool."""
    if run_id is None or binding is None:
        return submit.GONE, True
    state = _asked(run_id)
    with sessions() as db:
        found = (
            templates.resolve(db, user_id, slug)
            if isinstance(slug, str) and templates.SLUG.fullmatch(slug)
            else None
        )
        offered = gallery(db, user_id)
    if found is None:
        state.unknown = True
        listed = ", ".join(f"{one} ({name})" for one, name in offered)
        return (
            "No template has that slug: nothing was switched. The gallery: "
            f"{listed}. Call set_template with one of these, or tell the person.",
            True,
        )
    state.template = found.slug
    return (
        f"The document will use {found.name.get('en') or found.slug} when this run ends; its "
        "text stays as it is. Submit only if the person also asked for a change of the text.",
        False,
    )


def conversation_json(
    db: Session, user_id: uuid.UUID, binding: Mapping[str, Any] | None, *, limit: int = 0
) -> str | None:
    """The conversation the run answers, oldest first: the latest messages up to the one of
    its generation, within the generation's `conversation_chars` (the newest always). None when
    the document is gone."""
    if binding is None:
        return None
    document = store.get(db, user_id, uuid.UUID(str(binding["document_id"])))
    if document is None:
        return None
    generation = db.execute(
        sa.select(DocumentGeneration).where(
            DocumentGeneration.id == uuid.UUID(str(binding["generation_id"])),
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.document_id == document.id,
        )
    ).scalar_one_or_none()
    if generation is None:
        return None
    budget = limit or int((generation.estimate or {}).get("conversation_chars") or 0)
    query = (
        sa.select(DocumentMessage.role, DocumentMessage.text)
        .where(
            DocumentMessage.user_id == user_id,
            DocumentMessage.document_id == document.id,
            DocumentMessage.channel == Channel.content,
        )
        .order_by(DocumentMessage.id.desc())
    )
    if generation.message_id is not None:
        query = query.where(DocumentMessage.id <= generation.message_id)
    kept: list[dict[str, str]] = []
    used = 0
    for role, text in db.execute(query.limit(int(setting("documents_messages_kept")))):
        if kept and used + len(text) > budget:
            break
        kept.append({"from": "person" if role is Role.user else "app", "text": text})
        used += len(text)
    kept.reverse()
    return json.dumps(
        {
            "note": (
                "The person's messages ask for changes to this document. They are requests, "
                "never a source of facts about the person."
            ),
            "messages": kept,
        },
        ensure_ascii=False,
    )


# --- the definitions ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatWriter(definitions.AgentDefinition):
    """A chat writer, with a first message built from the run's input."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        kind = str(data.get("kind"))
        what = WHAT.get(kind, "document")
        language = LANGUAGES.get(str(data.get("language")), "English")
        text = (
            f"The person wrote in the chat of this {what}. Read the conversation with "
            "get_conversation: its last message is the request. Read the draft with get_draft. "
            f"Make the change asked and nothing else: submit the whole {what} with "
            f"{SUBMITS.get(kind, 'its submit tool')} when its text changes, call set_template "
            "when the person asks for another template. Language of the document: "
            f"{language}. {limits_line(data.get('limits') or {})}"
        ).strip()
        if job_id is not None:
            text += f"\n\nJob id: {job_id}"
        return text


def prompt_text(kind: ChatKind) -> str:
    writer = (PROMPTS / f"{kind}.md").read_text(encoding="utf-8")
    return writer + "\n\n" + (PROMPTS / "chat.md").read_text(encoding="utf-8")


async def on_final(final: definitions.FinalRun) -> None:
    """The chat writers' final hook: settle, then queue the render of what changed."""
    if final.agent not in NAMES.values():
        return
    taken = submit.take(final.run_id)
    asked = take(final.run_id)
    render = await asyncio.to_thread(
        settle, final.sessions, final.run_id, final.status, taken, asked, final.clock()
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
        logger.warning("documents.chat.render_not_queued", extra={"run_id": str(final.run_id)})
    finally:
        if own:
            await queue.close()


def _definition(kind: ChatKind) -> ChatWriter:
    return ChatWriter(
        name=NAMES[kind],
        description=f"Changes one {WHAT[kind]} as the person asks in its chat.",
        system_prompt=prompt_text(kind),
        tools=TOOLS[kind],
        input_model=ChatInput,
        model=MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: MODEL_API_KEY},
        max_turns=setting("documents_generation_max_turns"),
        wall_time_s=float(setting("documents_generation_wall_s")),
        effort="medium",
        internal=True,
        bind_job=kind != "base",
        live_text=False,
        bind=_bind(kind),
        private_io=True,
        on_final=on_final,
    )


WRITERS = {kind: definitions.register(_definition(kind)) for kind in KINDS}


# --- the message --------------------------------------------------------------------------------

# C0 and C1 controls but the line feed and the tab, and lone surrogates (Postgres refuses both).
_DROPPED = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")


def clean(text: str) -> str:
    """The message as stored: line ends made `\\n`, control characters dropped, outer blanks
    trimmed. Pure and linear."""
    return _DROPPED.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


class ChatRefusedError(Exception):
    """The chat cannot start a run now: `reason` is `consent` or `credential`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Posted:
    message: DocumentMessage
    generation: DocumentGeneration
    run_id: uuid.UUID


def _reads(db: Session, document: Document) -> int:
    """The characters a chat run reads besides the conversation."""
    user_id = document.user_id
    total = estimate.profile_chars(db, user_id) + len(json.dumps(document.draft))
    if document.scope == Scope.posting and document.posting_id is not None:
        total += estimate.posting_chars(db, user_id, document.posting_id)
        base = document.base_document_id
        if document.kind == Kind.letter:
            cv = next(
                (
                    d
                    for d in store.posting_documents(db, user_id, document.posting_id)
                    if d.kind == Kind.cv
                ),
                None,
            )
            base = cv.base_document_id if cv is not None else None
            total += len(json.dumps(cv.draft)) if cv is not None else 0
        total += estimate.base_chars(db, user_id, base) if base is not None else 0
    return total


def estimate_for(db: Session, document: Document) -> tuple[Any, int]:
    """`(estimate, conversation_chars)` of one chat run on `document` (module docstring)."""
    kind = kind_of(document)
    prompt = estimate.prompt_chars(kind) + estimate.prompt_chars("chat")
    reads = _reads(db, document)
    found = estimate.limits()
    plain = estimate.writer(prompt, reads, **found)
    resent = max(found["turns"] - 1, 1)
    chars = int(plain.high * CONVERSATION_SHARE) // resent * CHARS_PER_TOKEN
    return estimate.writer(prompt, reads + chars, **found), chars


def _base_of_letter(db: Session, document: Document) -> uuid.UUID | None:
    if document.kind != Kind.letter or document.posting_id is None:
        return document.base_document_id
    cv = next(
        (
            d
            for d in store.posting_documents(db, document.user_id, document.posting_id)
            if d.kind == Kind.cv
        ),
        None,
    )
    return cv.base_document_id if cv is not None else None


def _script(settings: Settings, kind: ChatKind, script: str | None) -> str | None:
    if not settings.agents_fake_backend or settings.app_env == "production":
        return None
    return f"documents_chat_{kind}_{script}" if script else DEFAULT_SCRIPTS[kind]


def start(
    db: Session,
    document: Document,
    generation: DocumentGeneration,
    *,
    at: datetime,
    script: str | None = None,
    settings: Settings | None = None,
) -> AgentRun:
    """The chat run of a generation, `queued`, and the generation `running`. Raises
    agent_runs.ActiveRunsError at the person's most active runs."""
    settings = settings or get_settings()
    kind = kind_of(document)
    template = render_job.template_of(db, document.user_id, document.template_slug)
    template_kind = "letter" if document.kind == Kind.letter else "cv"
    data: dict[str, Any] = {
        "generation_id": str(generation.id),
        "mode": GenerationMode.chat.value,
        "kind": kind,
        "language": document.language.value,
        "limits": asdict(template.kinds[template_kind].soft_limits),
        "token_budget": generation.token_ceiling,
    }
    chosen = _script(settings, kind, script)
    if chosen is not None:
        data["script"] = chosen
    definition = WRITERS[kind]
    run = agent_runs.create_run(
        db,
        document.user_id,
        definition.name,
        document.posting_id,
        definition.validate_input(data),
        definition.model,
        at,
    )
    generation.run_id = run.id
    generation.status = GenerationStatus.running
    generation.started_at = at
    db.flush()
    return run


def post(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    text: str,
    *,
    at: datetime,
    script: str | None = None,
    settings: Settings | None = None,
) -> Posted:
    """Store the person's message and start its run, in the caller's transaction (it commits,
    then enqueues the run). Raises store.NotFoundError, store.MessageInvalidError,
    ChatRefusedError, store.GenerationOpenError, agent_runs.ActiveRunsError and
    store.DocumentBusyError; the caller rolls back and nothing is kept."""
    settings = settings or get_settings()
    stored = clean(text)
    if not stored or len(stored) > settings.documents_chat_max_chars:
        raise store.MessageInvalidError("text")
    document = store.lock_document(db, user_id, document_id)
    reason = writer_base.wait_reason(db, user_id)
    if reason is not None:
        raise ChatRefusedError(reason.value)
    recover(db, document, at)
    current = store.open_generation(db, user_id, document.id)
    if current is not None:
        raise store.GenerationOpenError(current)
    message = store.add_message(
        db,
        user_id,
        document.id,
        Role.user,
        stored,
        at=at,
        max_chars=settings.documents_chat_max_chars,
        keep=settings.documents_messages_kept,
    )
    figures, chars = estimate_for(db, document)
    generation = store.create_generation(
        db,
        user_id,
        document.id,
        GenerationMode.chat,
        GenerationTrigger.chat,
        at=at,
        estimate={**estimate.as_json(figures), "conversation_chars": chars},
        token_ceiling=figures.ceiling,
        base_document_id=_base_of_letter(db, document),
        message_id=message.id,
    )
    message.generation_id = generation.id
    run = start(db, document, generation, at=at, script=script, settings=settings)
    logger.info(
        "documents.chat.message",
        extra={
            "document_id": str(document.id),
            "generation_id": str(generation.id),
            "run_id": str(run.id),
            "chars": len(stored),
        },
    )
    return Posted(message, generation, run.id)


# --- the end of a run ---------------------------------------------------------------------------


def _error(run: AgentRun | None, status: RunStatus, exhausted: bool) -> str:
    if exhausted:
        return "invalid_output"
    error = run.error if run is not None else None
    if error == failures.TOKEN_LIMIT:
        return "token_limit"
    if error == failures.BINDING_GONE:
        return "binding_gone"
    return "run_failed"


@dataclass(frozen=True)
class Outcome:
    """What `apply` did, for the answer and the render."""

    version: int | None = None
    outcome: GenerationOutcome | None = None
    before: Any = None  # the content the writer changed, None when it changed none
    after: Any = None
    records: tuple[Correction, ...] = ()
    template: str | None = None  # the name of a template switched to
    render: int | None = None  # the draft revision whose render was marked
    unknown: bool = False
    orgs: Mapping[str, str] = field(default_factory=dict)


def _profile_revision(db: Session, user_id: uuid.UUID) -> int | None:
    return db.scalar(sa.select(Profile.revision).where(Profile.user_id == user_id))


def _orgs(db: Session, document: Document, at: datetime) -> dict[str, str]:
    view = render_job.read_context(db, document, at).view
    return {employer.key: employer.organisation for employer in view.employers}


def apply(
    db: Session,
    document: Document,
    generation: DocumentGeneration,
    accepted: submit.Accepted | None,
    asked: Asked,
    *,
    at: datetime,
) -> Outcome:
    """Store a done run's result (module docstring): the document is locked by the caller."""
    user_id = document.user_id
    kind = render_job.content_kind(document)
    moved = document.draft_revision != generation.base_draft_revision
    switch_to = (
        asked.template
        if asked.template is not None and asked.template != document.template_slug
        else None
    )
    template = render_job.template_of(db, user_id, switch_to or document.template_slug)
    name = (template.name.get("en") or template.slug) if switch_to is not None else None
    orgs = _orgs(db, document, at)
    if accepted is None:
        if switch_to is None:
            return Outcome(unknown=asked.unknown, orgs=orgs)
        made = render_job.switch(db, user_id, document.id, at=at, template_slug=switch_to)
        document, fresh = render_job.mark_edited(db, user_id, document.id)
        return Outcome(
            version=made.number if made is not None else None,
            template=name,
            render=document.draft_revision if fresh else None,
            unknown=asked.unknown,
            orgs=orgs,
        )
    content = accepted.content
    if isinstance(content, c.BaseCvContent):
        own = c.parse("base", document.draft)
        content = content.model_copy(update={"fixed": own.fixed})  # type: ignore[union-attr]
    records = tuple(accepted.corrections)
    before = c.parse(kind, document.draft)
    render: int | None = None
    if moved:
        if switch_to is not None:
            render_job.switch(db, user_id, document.id, at=at, template_slug=switch_to)
            document, fresh = render_job.mark_edited(db, user_id, document.id)
            render = document.draft_revision if fresh else None
        version, _ = store.snapshot(
            db,
            user_id,
            document.id,
            Origin.chat,
            at=at,
            generation_id=generation.id,
            corrections=records,
            template_version=template.version,
            profile_revision=_profile_revision(db, user_id),
            content=c.dump(content),
        )
        outcome = GenerationOutcome.kept_as_version
    else:
        if switch_to is not None:
            document.template_slug = switch_to
            document.updated_at = at
            db.flush()
        store.save_draft(
            db, user_id, document.id, c.dump(content), document.draft_revision, Author.chat, at=at
        )
        version, _ = store.snapshot(
            db,
            user_id,
            document.id,
            Origin.chat,
            at=at,
            generation_id=generation.id,
            corrections=records,
            template_version=template.version,
            profile_revision=_profile_revision(db, user_id),
        )
        outcome = GenerationOutcome.draft_replaced
        document, fresh = render_job.mark_queued(
            db, user_id, document.id, revision=document.draft_revision, bounded=False
        )
        render = document.draft_revision if fresh else None
    return Outcome(
        version=version.number,
        outcome=outcome,
        before=before,
        after=content,
        records=records,
        template=name,
        render=render,
        unknown=asked.unknown,
        orgs=orgs,
    )


def _answer_message(
    db: Session, document: Document, generation: DocumentGeneration, text: str, at: datetime
) -> None:
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


def conclude(
    db: Session,
    document: Document,
    generation: DocumentGeneration,
    status: RunStatus,
    run: AgentRun | None,
    accepted: submit.Accepted | None,
    exhausted: bool,
    asked: Asked,
    at: datetime,
) -> int | None:
    """The generation of an ended run, its answer, and the revision of a render to queue."""
    render: int | None = None
    if status is RunStatus.done and not exhausted:
        done = apply(db, document, generation, accepted, asked, at=at)
        generation.status = GenerationStatus.done
        generation.outcome = done.outcome
        generation.version_number = done.version
        generation.corrections = len(done.records)
        generation.error = None
        render = done.render
        text = answer(done, gallery(db, document.user_id))
    elif status is RunStatus.cancelled:
        generation.status = GenerationStatus.cancelled
        text = CANCELLED
    else:
        generation.status = GenerationStatus.failed
        generation.error = _error(run, status, exhausted)
        text = FAILED[generation.error]
    generation.finished_at = at
    db.flush()
    _answer_message(db, document, generation, text, at)
    logger.info(
        "documents.chat.settled",
        extra={
            "generation_id": str(generation.id),
            "status": generation.status.value,
            "code": generation.error,
            "version": generation.version_number,
            "corrections": generation.corrections,
        },
    )
    return render


def settle(
    sessions: sessionmaker[Session],
    run_id: uuid.UUID,
    status: RunStatus,
    taken: submit.Taken,
    asked: Asked,
    at: datetime,
) -> tuple[uuid.UUID, int] | None:
    """The chat generation of a finished run, in one transaction. Returns `(document_id,
    revision)` of a render to queue after the commit."""
    with sessions() as db:
        found = db.execute(
            sa.select(DocumentGeneration.user_id, DocumentGeneration.document_id).where(
                DocumentGeneration.run_id == run_id
            )
        ).one_or_none()
        if found is None:
            return None
        try:
            document = store.lock_document(db, found.user_id, found.document_id)
        except store.NotFoundError:
            return None
        generation = db.execute(
            sa.select(DocumentGeneration)
            .where(DocumentGeneration.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        if generation.status is not GenerationStatus.running:
            return None
        revision = conclude(
            db,
            document,
            generation,
            status,
            db.get(AgentRun, run_id),
            taken.accepted,
            taken.exhausted,
            asked,
            at,
        )
        store.commit(db)
        return (document.id, revision) if revision is not None else None


def recover(db: Session, document: Document, at: datetime) -> bool:
    """A chat generation of `document` still `running` whose run ended over HOOK_GRACE_S ago
    without its final hook: failed (or cancelled), with its answer. The document is locked by
    the caller. Returns whether one was settled."""
    generation = db.execute(
        sa.select(DocumentGeneration)
        .where(
            DocumentGeneration.document_id == document.id,
            DocumentGeneration.user_id == document.user_id,
            DocumentGeneration.mode == GenerationMode.chat,
            DocumentGeneration.status == GenerationStatus.running,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if generation is None or generation.run_id is None:
        return False
    run = db.get(AgentRun, generation.run_id, populate_existing=True)
    if run is None or run.status not in FINAL:
        return False
    ended = run.finished_at or run.created_at
    if ended > at - timedelta(seconds=HOOK_GRACE_S):
        return False
    status = RunStatus.cancelled if run.status is RunStatus.cancelled else RunStatus.failed
    conclude(db, document, generation, status, run, None, False, Asked(), at)
    return True


# --- the answer ---------------------------------------------------------------------------------

LABELS = {
    "headline": "the headline",
    "summary": "the summary",
    "highlights": "the highlights",
    "competencies": "the competencies",
    "skills": "the skills",
    "recipient": "the recipient",
    "subject": "the subject",
    "salutation": "the salutation",
    "closing": "the closing",
}
SUGGESTED = frozenset({fixes.NOT_IN_PROFILE, fixes.NUMBER_NOT_STATED})


def _groups(content: Any) -> dict[str, Any]:
    """The parts of a content the answer names, in reading order."""
    if isinstance(content, c.LetterContent):
        out: dict[str, Any] = {
            "headline": content.headline,
            "recipient": content.recipient,
            "subject": content.subject,
            "salutation": content.salutation,
        }
        out.update({f"paragraphs.{i}": text for i, text in enumerate(content.paragraphs)})
        out["closing"] = content.closing
        return out
    out = {
        "headline": content.headline,
        "summary": content.summary,
        "highlights": tuple(content.highlights),
        "competencies": tuple(content.competencies),
    }
    out.update({f"experience:{r.employer_key}": tuple(r.bullets) for r in content.experience})
    out["skills"] = tuple((g.label, tuple(g.items)) for g in content.skills)
    return out


def _label(part: str, orgs: Mapping[str, str]) -> str:
    if part.startswith("experience:"):
        key = part.split(":", 1)[1]
        return f"your experience at {orgs[key]}" if key in orgs else "an experience entry"
    if part.startswith("paragraphs."):
        return f"paragraph {int(part.split('.')[1]) + 1}"
    return LABELS.get(part, "the document")


def _field_label(path: str, content: Any, orgs: Mapping[str, str]) -> str:
    parts = path.split(".")
    if parts[0] == "experience" and len(parts) > 1 and parts[1].isdigit():
        index = int(parts[1])
        roles = getattr(content, "experience", [])
        if index < len(roles):
            return _label(f"experience:{roles[index].employer_key}", orgs)
        return "an experience entry"
    if parts[0] == "paragraphs" and len(parts) > 1 and parts[1].isdigit():
        return _label(f"paragraphs.{parts[1]}", orgs)
    return LABELS.get(parts[0], "the document")


def changed(before: Any, after: Any, orgs: Mapping[str, str]) -> list[str]:
    """The labels of the parts that differ, in the order of the new content."""
    old, new = _groups(before), _groups(after)
    order = list(new) + [part for part in old if part not in new]
    return [_label(part, orgs) for part in order if old.get(part) != new.get(part)]


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _quote(text: str) -> str:
    flat = " ".join(text.split())
    return f'"{flat if len(flat) <= QUOTE_MAX else flat[: QUOTE_MAX - 3].rstrip() + "..."}"'


def _correction(record: Correction, content: Any, orgs: Mapping[str, str]) -> str:
    where = _field_label(record.field_path, content, orgs)
    reason = record.reason[:1].lower() + record.reason[1:]
    if record.reason == fixes.KEPT:
        return f"{_quote(record.before)} in {where} was kept as written, for you to check."
    if not record.after:
        return f"{_quote(record.before)} was removed from {where}: {reason}."
    return f"{_quote(record.before)} became {_quote(record.after)} in {where}: {reason}."


def _gallery_line(offered: Iterable[tuple[str, str]]) -> str:
    names = [name for _, name in offered]
    return "No template has that name. The gallery has " + _join(names) + "."


def answer(done: Outcome, offered: Iterable[tuple[str, str]] = ()) -> str:
    """The assistant's message for a done run: pure, from what was stored."""
    lines: list[str] = []
    if done.unknown:
        lines.append(_gallery_line(offered))
    if done.template is not None:
        lines.append(f"Switched the template to {done.template}.")
    if done.outcome is GenerationOutcome.kept_as_version:
        lines.append(
            "You were editing while I wrote, so your draft stays as you left it. The change is "
            f"kept as version {done.version}: open it in See to restore it."
        )
    elif done.after is not None:
        parts = changed(done.before, done.after, done.orgs)
        lines.append(f"Changed {_join(parts)}." if parts else "The text is the same as before.")
    if done.records:
        count = len(done.records)
        lines.append(f"The fact check corrected {count} claim{'s' if count > 1 else ''}:")
        shown = done.records[:CORRECTIONS_LISTED]
        lines += [_correction(record, done.after, done.orgs) for record in shown]
        if count > len(shown):
            lines.append(f"And {count - len(shown)} more, listed with the version.")
        if any(record.reason in SUGGESTED for record in done.records):
            lines.append(SUGGEST_PROFILE)
    if done.version is not None and done.outcome is not GenerationOutcome.kept_as_version:
        lines.append(f"This is version {done.version}.")
    if done.template is None and done.after is None:
        lines.append(NOTHING)
    text = "\n".join(line for line in lines if line)
    limit = int(setting("documents_chat_max_chars"))
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools (the api image has no SDK); last, since they import this.
    from app.documents import chat_tools as _tools  # noqa: F401
