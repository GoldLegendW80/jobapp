"""Interview preparation of one posting (SPEC-documents.md, "Interview preparation"; owner
2026-11-07, "build all" of the career-ops analysis). SDK free: the tools that call it live in
`interview_prep_tools` (worker only), the agents in `writer_interview_prep`.

A document of kind `interview_prep`, one per posting, text only like a proposal: its draft, its
versions, the hand edits and restores of router.py. Nothing writes it without the person's click
(D29) and nothing here searches the web:

- **Prepare** (`start`): a `posting` generation of trigger `click`, started at once with one run
  of `documents_interview_prep`, which reads the posting, the CV and letter as they were sent
  (`sent_documents`), the profile, and the hiring panel's record of them (`panel_record`), and
  submits through D7's `submit.handle`: the stories and the answers to the gaps are claims, so
  the fact check corrects any figure the profile does not state and records it. Without the
  consent or a credential, with a writing open, or with the person's runs all busy, nothing is
  stored (`PrepRefusedError`). A new preparation keeps the debrief the person wrote (`on_final`).
- **Practice** (`post_practice`): the person's message in the channel `practice` and a `chat`
  generation answered by one run of `documents_interview_practice`, which asks one question at
  a time and gives feedback on the last answer through a strict tool (`practice_turn`); code
  writes the answer from what that tool stored (`settle_practice`). A turn whose hook was lost
  is settled by the next read (`recover_practice`).
- **Debrief**: the person appends what was asked to the draft by hand (router.py's save).
- **Keep stories** (`keep_stories`): the stories the person picks become one pending proposal of
  the profile's facts block "Interview stories" (`item_proposals`), written only when accepted;
  a second keep before that adds to the pending one, and the page of a posting shows only a
  proposal that holds its stories (`pending_stories`).

Logs carry ids and codes, never a text.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runs as agent_runs
from app.agents.definitions import FinalRun
from app.agents.models import FINAL_STATUSES, AgentRun, Proposal, ProposalStatus, RunStatus
from app.config import Settings, get_settings
from app.documents import content as c
from app.documents import corrections, estimate, generate, panel, rules, store, submit, writer_base
from app.documents.models import (
    Channel,
    Document,
    DocumentGeneration,
    DocumentMessage,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    Kind,
    Role,
)
from app.profile import service as profile_service

logger = logging.getLogger("app.documents.interview_prep")

KIND = "interview_prep"
STORIES_BLOCK = "Interview stories"
# What a stories proposal records beside its item: the stories it adds and their postings.
PROPOSAL_STORIES, PROPOSAL_POSTINGS = "prep_stories", "prep_postings"
PRACTICE_KEPT = 40  # the messages a practice run reads
HOOK_GRACE_S = 120  # as the tick's
PRACTICE_FAILED = "The practice partner could not answer. Send your message again."
STORED = "Stored. Your work is done: answer with one short line."


class PrepRefusedError(Exception):
    """Nothing was started: `code` is `consent`, `credential`, `busy` or `runs_busy`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- reads ---------------------------------------------------------------------------------------


def of_posting(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> Document | None:
    return next(
        (
            found
            for found in store.posting_documents(db, user_id, posting_id)
            if found.kind == Kind.interview_prep
        ),
        None,
    )


def own(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    """The person's preparation, else store.NotFoundError (another kind answers the same)."""
    found = store.get(db, user_id, document_id)
    if found is None or found.kind != Kind.interview_prep or found.posting_id is None:
        raise store.NotFoundError(document_id)
    return found


def sent_documents(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> dict[str, Any]:
    """The posting's CV and letter as the company got them: the latest frozen version, else the
    latest version, marked as not sent; a kind never written is absent."""
    out: dict[str, Any] = {}
    for document in store.posting_documents(db, user_id, posting_id):
        if document.kind not in (Kind.cv, Kind.letter):
            continue
        found = store.versions(db, user_id, document.id)
        if not found:
            continue
        frozen = [v for v in found if v.frozen_at is not None]
        picked = frozen[0] if frozen else found[0]
        out[document.kind.value] = {
            "sent": picked.frozen_at.date().isoformat() if picked.frozen_at else None,
            "content": picked.content,
        }
    return out


def panel_record(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> dict[str, Any]:
    """What the hiring panel stored about the posting's CV and letter (D29): the hiring
    manager's questions and the lines it found weakest, the screen's verdict and reasons, the
    merged mistakes and the reviewer closest to rejecting. Nothing is run again."""
    out: dict[str, Any] = {}
    for document in store.posting_documents(db, user_id, posting_id):
        if document.kind not in (Kind.cv, Kind.letter):
            continue
        row = panel.latest(db, user_id, document.id)
        if row is None:
            continue
        reviews = row.reviews or {}
        manager = reviews.get("manager") or {}
        screen = reviews.get("screen") or {}
        out[document.kind.value] = {
            "manager_verdict": manager.get("verdict"),
            "manager_questions": list(manager.get("questions") or []),
            "weakest_lines": list(manager.get("problems") or []),
            "screen_verdict": screen.get("verdict"),
            "screen_reasons": list(screen.get("problems") or []),
            "mistakes": [
                {"quote": m.get("quote"), "why": m.get("why")} for m in (row.mistakes or [])
            ],
            "closest": row.closest,
        }
    return out


# --- Prepare ------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Started:
    document: Document
    generation: DocumentGeneration
    run_id: uuid.UUID  # to queue after the commit (`generate.enqueue_run`)


def _ready(db: Session, user_id: uuid.UUID) -> None:
    reason = writer_base.wait_reason(db, user_id)
    if reason is not None:
        raise PrepRefusedError(reason.value)


def start(
    db: Session,
    user_id: uuid.UUID,
    posting_id: uuid.UUID,
    *,
    at: datetime,
    settings: Settings | None = None,
    script: str | None = None,
) -> Started:
    """Prepare the posting's interview (module docstring). Raises store.NotFoundError for a
    posting that is not the person's, PrepRefusedError when nothing can start; the caller rolls
    back on a refusal, commits otherwise, then queues the run."""
    from app.documents import writer_interview_prep as writer

    settings = settings or get_settings()
    if store.lock_posting(db, user_id, posting_id) is None:
        raise store.NotFoundError(posting_id)
    _ready(db, user_id)
    document = store.get_or_create(db, user_id, posting_id, Kind.interview_prep, at=at)
    default = store.default_base(db, user_id)
    base_id = default.id if default is not None else None
    figures = estimate.for_posting(db, user_id, posting_id, base_id)
    try:
        generation = generate.create(
            db,
            document,
            GenerationMode.posting,
            GenerationTrigger.click,
            base_id,
            dict(estimate.as_json(figures)),
            figures.ceiling,
            at=at,
        )
    except store.GenerationOpenError:
        raise PrepRefusedError("busy") from None
    data: dict[str, Any] = {
        "generation_id": str(generation.id),
        "language": document.language.value,
        "token_budget": generation.token_ceiling,
    }
    if settings.agents_fake_backend and settings.app_env != "production":
        data["script"] = script or writer.PREP_SCRIPT
    try:
        run = agent_runs.create_run(
            db,
            user_id,
            writer.PREP_NAME,
            posting_id,
            writer.PREP_WRITER.validate_input(data),
            writer.PREP_WRITER.model,
            at,
            flow=agent_runs.WorkflowRef("interview_prep", trigger="prepare"),
        )
    except agent_runs.ActiveRunsError:
        raise PrepRefusedError("runs_busy") from None
    generation.run_id = run.id
    generation.status = GenerationStatus.running
    generation.started_at = at
    db.flush()
    logger.info(
        "documents.interview_prep.started",
        extra={"generation_id": str(generation.id), "run_id": str(run.id)},
    )
    return Started(document, generation, run.id)


def context(
    db: Session, user_id: uuid.UUID, binding: Mapping[str, Any] | None
) -> submit.WriterContext | None:
    """What a submit of the preparation is checked against: the profile, the base CV the
    generation names and the posting's title and company (a preparation may name the role and
    the company, as a letter does), with the dash rule on whatever the person's documents use:
    the preparation is the app's text to the person. None when the bound document is gone."""
    if binding is None:
        return None
    generation = db.execute(
        sa.select(DocumentGeneration).where(
            DocumentGeneration.id == uuid.UUID(str(binding["generation_id"])),
            DocumentGeneration.user_id == user_id,
        )
    ).scalar_one_or_none()
    document = store.get(db, user_id, uuid.UUID(str(binding["document_id"])))
    if (
        generation is None
        or document is None
        or generation.document_id != document.id
        or document.kind != Kind.interview_prep
    ):
        return None
    started = generation.started_at or generation.created_at
    reading = submit.read_proposal(db, document, generation.base_document_id, started)
    return submit.WriterContext(
        kind=KIND,
        sources=submit.proposal_sources(reading),
        settings=store.settings(db, user_id).model_copy(update={"no_dash_separators": True}),
        employer_keys=tuple(employer.key for employer in reading.view.employers),
        soft_limits=submit.NO_SOFT_LIMITS,
        fallbacks=corrections.Fallbacks(),
        today=started.date(),
    )


def _with_debrief(sessions: sessionmaker[Session], run_id: uuid.UUID, accepted: Any) -> Any:
    """The accepted preparation with the debrief the person wrote in the draft, never the
    writer's: a new preparation does not drop what was asked."""
    with sessions() as db:
        draft = db.execute(
            sa.select(Document.draft)
            .join(DocumentGeneration, DocumentGeneration.document_id == Document.id)
            .where(DocumentGeneration.run_id == run_id)
        ).scalar_one_or_none()
    kept = c.parse(KIND, draft).debrief if draft else ()  # type: ignore[union-attr]
    content = accepted.content.model_copy(update={"debrief": list(kept)})
    return dataclasses.replace(accepted, content=content)


async def on_final(final: FinalRun) -> None:
    """The writer's final hook: settle the generation as every writer's is (`generate.settle`:
    a version, the draft replaced when nobody wrote it since), nothing rendered."""
    import asyncio

    taken = submit.take(final.run_id)
    if taken.accepted is not None:
        accepted = await asyncio.to_thread(
            _with_debrief, final.sessions, final.run_id, taken.accepted
        )
        taken = dataclasses.replace(taken, accepted=accepted)
    await asyncio.to_thread(
        generate.settle, final.sessions, final.run_id, final.status, taken, final.clock()
    )


# --- Practice -----------------------------------------------------------------------------------


FEEDBACK_MAX, QUESTION_MAX = 1_500, 600
# The longest answer a turn makes (`practice_text`), whatever the chat's own message limit.
PRACTICE_ANSWER_MAX = FEEDBACK_MAX + 2 + QUESTION_MAX


class Turn(BaseModel):
    """What the practice partner submits once per run: feedback on the person's last answer
    (empty before the first question) and the next question."""

    model_config = ConfigDict(extra="forbid", strict=True)

    feedback: str = Field(default="", max_length=FEEDBACK_MAX)
    question: str = Field(default="", max_length=QUESTION_MAX)


TURN_SCHEMA = Turn.model_json_schema()
TURNS_KEPT = 64
_TURNS: dict[uuid.UUID, Turn] = {}
_TURNS_LOCK = threading.Lock()


def _undashed(text: str, limit: int) -> str:
    """The dash rule on a turn: the compact replacement when the plain one would pass `limit`."""
    found = rules.separators(text)
    fixed = rules.apply_separators(text, found)
    return fixed if len(fixed) <= limit else rules.apply_separators(text, found, compact=True)


def practice_turn(run_id: uuid.UUID | None, raw: object) -> tuple[str, bool]:
    """One submit of a practice run: `(answer, is_error)`, its dashes used as separators
    replaced. The first valid one is kept for the final hook; a second is answered as already
    stored."""
    if run_id is None:
        return PRACTICE_FAILED, True
    try:
        turn = Turn.model_validate(raw)
    except ValidationError as exc:
        paths = sorted({".".join(str(p) for p in e["loc"]) or "(the turn)" for e in exc.errors()})
        return "Not stored: fix " + ", ".join(paths) + " and submit again.", True
    turn = Turn(
        feedback=_undashed(turn.feedback, FEEDBACK_MAX),
        question=_undashed(turn.question, QUESTION_MAX),
    )
    if not (turn.feedback.strip() or turn.question.strip()):
        return "Not stored: give feedback, a question, or both.", True
    with _TURNS_LOCK:
        if run_id in _TURNS:
            return submit.ALREADY, False
        _TURNS[run_id] = turn
        while len(_TURNS) > TURNS_KEPT:
            del _TURNS[next(iter(_TURNS))]
    return STORED, False


def take_turn(run_id: uuid.UUID) -> Turn | None:
    with _TURNS_LOCK:
        return _TURNS.pop(run_id, None)


def practice_text(turn: Turn) -> str:
    parts = [turn.feedback.strip(), turn.question.strip()]
    return "\n\n".join(part for part in parts if part)


@dataclasses.dataclass(frozen=True)
class Posted:
    message: DocumentMessage
    generation: DocumentGeneration
    run_id: uuid.UUID


def post_practice(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    text: str,
    *,
    at: datetime,
    settings: Settings | None = None,
    script: str | None = None,
) -> Posted:
    """The person's practice message and the run that answers it (module docstring). Raises
    store.NotFoundError, store.MessageInvalidError for an empty or long text, and
    PrepRefusedError."""
    from app.documents import writer_interview_prep as writer

    settings = settings or get_settings()
    document = own(db, user_id, document_id)
    store.lock_posting(db, user_id, document.posting_id)  # type: ignore[arg-type]
    recover_practice(db, user_id, document.id, at)
    _ready(db, user_id)
    message = store.add_message(
        db, user_id, document.id, Role.user, text.strip(), at=at, channel=Channel.practice
    )
    figures = estimate.for_posting(db, user_id, document.posting_id)  # type: ignore[arg-type]
    try:
        generation = store.create_generation(
            db,
            user_id,
            document.id,
            GenerationMode.chat,
            GenerationTrigger.chat,
            at=at,
            estimate=dict(estimate.as_json(figures)),
            token_ceiling=figures.ceiling,
            message_id=message.id,
        )
    except store.GenerationOpenError:
        raise PrepRefusedError("busy") from None
    data: dict[str, Any] = {
        "generation_id": str(generation.id),
        "language": document.language.value,
        "token_budget": generation.token_ceiling,
    }
    if settings.agents_fake_backend and settings.app_env != "production":
        data["script"] = script or writer.PRACTICE_SCRIPT
    try:
        run = agent_runs.create_run(
            db,
            user_id,
            writer.PRACTICE_NAME,
            document.posting_id,
            writer.PRACTICE_PARTNER.validate_input(data),
            writer.PRACTICE_PARTNER.model,
            at,
            flow=agent_runs.WorkflowRef("interview_prep", trigger="practice"),
        )
    except agent_runs.ActiveRunsError:
        raise PrepRefusedError("runs_busy") from None
    generation.run_id = run.id
    generation.status = GenerationStatus.running
    generation.started_at = at
    message.generation_id = generation.id
    db.flush()
    return Posted(message, generation, run.id)


def _close(
    db: Session, generation: DocumentGeneration, status: GenerationStatus, text: str, at: datetime
) -> None:
    store.add_message(
        db,
        generation.user_id,
        generation.document_id,
        Role.assistant,
        text,
        at=at,
        channel=Channel.practice,
        generation_id=generation.id,
        max_chars=PRACTICE_ANSWER_MAX,
    )
    generation.status = status
    generation.error = None if status is GenerationStatus.done else "run_failed"
    generation.finished_at = at
    db.flush()


def settle_practice(
    sessions: sessionmaker[Session],
    run_id: uuid.UUID,
    status: RunStatus,
    turn: Turn | None,
    at: datetime,
) -> None:
    """The practice generation of a finished run, closed with its answer, in one transaction."""
    with sessions() as db:
        found = db.execute(
            sa.select(DocumentGeneration.user_id, DocumentGeneration.document_id).where(
                DocumentGeneration.run_id == run_id
            )
        ).one_or_none()
        if found is None:
            return
        store.lock_document(db, found.user_id, found.document_id)
        generation = db.execute(
            sa.select(DocumentGeneration)
            .where(DocumentGeneration.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        if generation.status is not GenerationStatus.running:
            return
        if status is RunStatus.done and turn is not None:
            _close(db, generation, GenerationStatus.done, practice_text(turn), at)
        else:
            _close(db, generation, GenerationStatus.failed, PRACTICE_FAILED, at)
        store.commit(db)


async def on_practice_final(final: FinalRun) -> None:
    import asyncio

    turn = take_turn(final.run_id)
    await asyncio.to_thread(
        settle_practice, final.sessions, final.run_id, final.status, turn, final.clock()
    )


def recover_practice(db: Session, user_id: uuid.UUID, document_id: uuid.UUID, at: datetime) -> int:
    """A practice turn whose run ended over HOOK_GRACE_S ago without its hook is closed as
    failed, so the next message can start. Returns how many."""
    ended = sa.func.coalesce(AgentRun.finished_at, AgentRun.created_at)
    found = db.scalars(
        sa.select(DocumentGeneration)
        .join(AgentRun, AgentRun.id == DocumentGeneration.run_id)
        .where(
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.document_id == document_id,
            DocumentGeneration.mode == GenerationMode.chat,
            DocumentGeneration.status == GenerationStatus.running,
            AgentRun.status.in_(FINAL_STATUSES),
            ended < at - timedelta(seconds=HOOK_GRACE_S),
        )
    ).all()
    for generation in found:
        _close(db, generation, GenerationStatus.failed, PRACTICE_FAILED, at)
    return len(found)


def practice_messages(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID
) -> list[DocumentMessage]:
    return store.messages(db, user_id, document_id, Channel.practice)


def conversation(db: Session, user_id: uuid.UUID, binding: Mapping[str, Any] | None) -> list[Any]:
    """The practice so far, oldest first, its last message the person's (what a run reads)."""
    if binding is None:
        return []
    found = store.messages(db, user_id, uuid.UUID(str(binding["document_id"])), Channel.practice)
    return [{"role": m.role.value, "text": m.text} for m in found[-PRACTICE_KEPT:]]


# --- Keep stories -------------------------------------------------------------------------------


def story_text(story: c.Story) -> str:
    lines = [story.title.strip()]
    for label, text in (
        ("Situation", story.situation),
        ("Task", story.task),
        ("Action", story.action),
        ("Result", story.result),
    ):
        if text.strip():
            lines.append(f"{label}: {text.strip()}")
    return "\n".join(line for line in lines if line)


def _appended(current: str, texts: Sequence[str]) -> str:
    body = current.strip()
    for text in texts:
        if text and text not in body:
            body = f"{body}\n\n{text}".strip()
    return body


def stories_body(current: str, stories: Sequence[c.Story]) -> str:
    """The block's body with each story not already in it appended."""
    return _appended(current, [story_text(story) for story in stories])


def _current_block(db: Session, user_id: uuid.UUID, at: datetime) -> str:
    """The block STORIES_BLOCK of the profile, which stays locked: two keeps run one at a time."""
    profile = profile_service.get_profile(db, user_id, at, lock=True)
    facts = profile_service.sections_view(profile).get("facts") or {}
    for block in facts.get("blocks") or ():
        if str(block.get("title", "")).casefold() == STORIES_BLOCK.casefold():
            return str(block.get("body") or "")
    return ""


def _listed(proposal: Proposal | None, key: str) -> list[str]:
    found = (proposal.payload.get(key) if proposal is not None else None) or ()
    return [str(item) for item in found]


def keep_stories(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, indexes: Sequence[int], at: datetime
) -> Proposal | None:
    """One pending proposal of the facts block STORIES_BLOCK with the chosen stories appended,
    None when the profile holds them all already. Raises store.NotFoundError and ValueError (no
    story chosen). Nothing changes in the profile until the person accepts it.

    A proposal still pending (one stands at a time: the new one supersedes it) keeps its stories
    in the new one, and the postings they came from (PROPOSAL_POSTINGS), where the page shows it."""
    from app.agents import proposals

    document = own(db, user_id, document_id)
    content = c.parse(KIND, document.draft)
    stories = getattr(content, "stories", [])
    picked = [stories[i] for i in sorted(set(indexes)) if 0 <= i < len(stories)]
    if not picked:
        raise ValueError("no story chosen")
    current = _current_block(db, user_id, at)
    texts = [story_text(story) for story in picked]
    if _appended(current, texts) == current.strip():
        return None
    pending = pending_stories(db, user_id)
    carried = _listed(pending, PROPOSAL_STORIES)
    added = [text for text in dict.fromkeys([*carried, *texts]) if text and text not in current]
    postings = _listed(pending, PROPOSAL_POSTINGS) if carried else []
    payload = {
        "section": "facts",
        "key": "blocks",
        "action": "add",
        "value": {"title": STORIES_BLOCK, "body": _appended(current, added)},
        "citations": [],
    }
    try:
        made = proposals.create(
            db,
            user_id,
            target=proposals.TARGET_PROFILE,
            target_id=None,
            kind=proposals.KIND_PROFILE_ITEM,
            payload=payload,
            run_id=None,
            now=at,
        )
    except proposals.NotStoredError:
        return None
    made.payload = {
        **made.payload,
        PROPOSAL_STORIES: added,
        PROPOSAL_POSTINGS: sorted({*postings, str(document.posting_id)}),
    }
    db.flush()
    logger.info("documents.interview_prep.stories", extra={"document_id": str(document_id)})
    return made


def pending_stories(
    db: Session, user_id: uuid.UUID, posting_id: uuid.UUID | None = None
) -> Proposal | None:
    """The pending proposal of the stories block, if any; with `posting_id`, only one that holds
    stories of that posting's preparation."""
    held = [
        Proposal.user_id == user_id,
        Proposal.target == "profile",
        Proposal.status == ProposalStatus.pending,
        Proposal.payload["section"].astext == "facts",
        Proposal.payload["value"]["title"].astext == STORIES_BLOCK,
    ]
    if posting_id is not None:
        held.append(Proposal.payload[PROPOSAL_POSTINGS].contains([str(posting_id)]))
    return db.scalars(
        sa.select(Proposal).where(*held).order_by(Proposal.created_at.desc()).limit(1)
    ).first()


def latest_generation(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, mode: GenerationMode
) -> DocumentGeneration | None:
    return db.scalars(
        sa.select(DocumentGeneration)
        .where(
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.document_id == document_id,
            DocumentGeneration.mode == mode,
        )
        .order_by(DocumentGeneration.created_at.desc(), DocumentGeneration.id.desc())
        .limit(1)
    ).first()
