"""The hiring panel of every CV and letter build, and of an uploaded CV's conversion (task D29,
Part C; SPEC-documents.md, "The hiring panel"). Adapted from hiring-panel-skill by Alex Chen
(MIT, NOTICE): seven reviewers try to reject the document, their answers are merged into one
list of mistakes, the writer rebuilds it with the same facts, and the screen runs again.

**When.** A writer's first accepted version of a posting's CV or letter (`generate.on_final`,
mode `posting`), or of an upload's conversion (`writer_base._on_final`, mode `base_upload`),
opens a panel (`begin`, a row of `document_panels`, `running`) and queues the job
`documents_panel` on the agents queue (`enqueue`). The letter of a CV follows the CV's panel:
the job starts it at its end, whatever the outcome (`_finish`). Nothing else opens one.

**The job** (`run`), outside any transaction but its own short ones:
1. The document's text in reading order (`document_text`: the content's printed fields, the
   fixed facts the template prints under each employer, whether contact details are printed,
   never their values), the template's name and its columns, and the job brief (brief.py: the
   posting, or for an upload the person's target roles).
2. The seven reviews (`ROLES`), at most `AT_ONCE` (3) runs at once, so the person's fourth run
   slot (`runs.ACTIVE_PER_USER` is 4) stays free for a click or a chat. Each reviewer is its own
   agent (`documents_panel_<role>`), tool free but for one report tool with a strict schema
   (`Review`, checked per role by `check_review`), on the person's own credential, Sonnet by
   default. Each gets ONLY the document, the brief and its own role in escaped data blocks: the
   five readers never see each other's answers, the recruiter gets the top third only. A
   reviewer that fails, times out, finds the person's runs busy or never reports is recorded
   missing with its code, and the panel goes on with the others.
3. The merge (`documents_panel_merge`, the skill's section 5): one numbered list of mistakes,
   worst first, each with the quoted line, why it is rejected, who flagged it and the fix, and
   the questions for facts the profile lacks. Code then merges duplicates the model left (the
   same quoted line, case and spaces folded), keeps only reviewers that answered, and numbers
   the list from 1 (`merged_mistakes`).
4. The rebuild: a generation of mode `panel` of the same document, written by the document's
   own writer with the mistakes as its instructions (held in process, `panel_hold`), through
   `submit.handle` like every write: the fact check corrects any unsupported claim, and an
   `[ADD: ...]` never prints (`corrections.drop_placeholders`). Its version is the document's
   new version (origin `panel`), the first draft stays in the history.
5. The screen again on the rebuilt version: the before and after verdicts.

**Correct, never block** (Decision 4). Every step that fails leaves the writer's first version
as the document and says in `failed` what failed: no review at all (`reviews`), the merge
(`merge`), the rebuild (`rebuild`, `rebuild_busy`), the screen after (`screen_after`), the job
itself (`panel_error`). A panel nobody closes (its job lost) is closed by the documents tick
after `STALE_S` (`recover`). The apply module waits for a running panel like for a writing
(freeze.py `current`), within its own grace.

**Privacy.** The document, the brief and the reviews live in this process while the job runs
and in the panel's row (the person's own data, shown to them); logs and events carry ids,
counts and codes only.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials, definitions, runs
from app.agents.models import AgentRun, CredentialKind, RunStatus
from app.documents import brief as briefs
from app.documents import content as c
from app.documents import estimate, panel_hold, store
from app.documents.models import (
    Document,
    DocumentGeneration,
    DocumentPanel,
    DocumentVersion,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    Kind,
    PanelStatus,
    Scope,
)
from app.profile.models import Profile

logger = logging.getLogger("app.documents.panel")

FUNCTION = "documents_panel"  # the arq job on the agents queue (app/worker.py)
REVIEW_TOOL = "report_review"
MERGE_TOOL = "report_mistakes"
MODEL = "claude-sonnet-5-5"  # Sonnet by default, on a subscription as on an API key
MAX_TURNS = 3
WALL_TIME_S = 150.0
AT_ONCE = 3
REVIEW_CHARS = 1_500  # what one review adds to the merge's reads, at most
JOB_TIMEOUT_S = 1_500  # 3 waves of reviews, the merge, the rebuild and the screen, with room
STALE_S = 1_800  # over JOB_TIMEOUT_S: a panel still running then lost its job
NOTE_WORDS = 120
DOCUMENT_MAX = 20_000
BRIEF_TEXT_MAX = 12_000
STORED = "Stored. Stop and answer with one short line."
REPORTED_AGAIN = "Already stored: this run reports once. Stop."
PROMPTS = Path(__file__).parent / "prompts"
WHAT = {"cv": "CV", "letter": "cover letter", "base": "CV"}


@dataclass(frozen=True)
class Role:
    key: str
    label: str
    verdicts: tuple[str, ...]
    good: str  # the verdict that would let the person through
    problems_max: int

    @property
    def agent(self) -> str:
        return f"documents_panel_{self.key}"


ROLES: tuple[Role, ...] = (
    Role("screen", "The automated screen", ("PASS", "REJECT"), "PASS", 5),
    Role("manager", "The hiring manager", ("INTERVIEW", "NO"), "INTERVIEW", 3),
    Role("reader_manager", "Reader: the hiring manager", ("HIRE", "MAYBE", "PASS"), "HIRE", 2),
    Role("reader_rival", "Reader: another applicant", ("HIRE", "MAYBE", "PASS"), "HIRE", 2),
    Role("reader_ceo", "Reader: the CEO", ("HIRE", "MAYBE", "PASS"), "HIRE", 2),
    Role("reader_recruiter", "Reader: the recruiter", ("HIRE", "MAYBE", "PASS"), "HIRE", 2),
    Role("reader_teammate", "Reader: the teammate", ("HIRE", "MAYBE", "PASS"), "HIRE", 2),
)
ROLE_OF = {role.key: role for role in ROLES}
READERS = tuple(role for role in ROLES if role.key.startswith("reader_"))
MERGE_AGENT = "documents_panel_merge"
AGENT_NAMES = (*(role.agent for role in ROLES), MERGE_AGENT)
# Why a reviewer's answer is missing.
MISSING = ("no_credential", "busy", "token_budget", "cancelled", "error", "no_report")


def you_are(role: Role, kind: str, title: str, company: str) -> str:
    """The role a reviewer plays, at the company and for the posting (D29, item 5)."""
    what = WHAT[kind]
    return {
        "screen": f"the automated screening system of {company} for {title}, which decides "
        f"whether a real person should even see this {what}",
        "manager": f"the hiring manager of {title} at {company}, who decides whether this "
        "person gets an interview",
        "reader_manager": f"the hiring manager of {title} at {company}, reading for one thing: "
        "can this person do the job on day one?",
        "reader_rival": f"another applicant for {title} at {company}, reading as a rival: where "
        f"is this {what} weaker than mine?",
        "reader_ceo": f"the CEO of {company}, reading for impact: what did this person change, "
        "and by how much?",
        "reader_recruiter": f"the recruiter of {company} for {title}, with 30 seconds and 100 "
        f"applications today, reading the top third of the {what} only",
        "reader_teammate": f"a future teammate at {company}, working next to the {title}, "
        "reading for how this person works with others",
    }[role.key]


# --- the reports -----------------------------------------------------------------------------


class Quoted(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    quote: str = Field(min_length=1, max_length=500)
    why: str = Field(max_length=500)


class Review(BaseModel):
    """What one reviewer reports. Which fields its role fills is said in its prompt and checked
    by `check_review`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    verdict: str = Field(max_length=12)
    decided_by: str = Field(default="", max_length=500)  # the manager's first ten seconds
    hurt_most: Quoted | None = None  # a reader's line that hurt most
    problems: list[Quoted] = Field(default_factory=list, max_length=5)
    questions: list[str] = Field(default_factory=list, max_length=3)  # the manager asks them
    missing_terms: list[str] = Field(default_factory=list, max_length=20)  # the screen's
    note: str = Field(default="", max_length=900)


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(min_length=1, max_length=300)
    section: Literal["facts", "experience", "skills", "answers"] = "facts"


class Mistake(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    quote: str = Field(min_length=1, max_length=500)
    why: str = Field(max_length=500)
    flagged_by: list[str] = Field(min_length=1, max_length=7)
    fix: str = Field(max_length=500)


class Merged(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mistakes: list[Mistake] = Field(default_factory=list, max_length=15)
    questions: list[Question] = Field(default_factory=list, max_length=8)
    closest: str | None = Field(default=None, max_length=40)  # the role closest to rejecting
    closest_why: str = Field(default="", max_length=500)


REVIEW_SCHEMA = Review.model_json_schema()
MERGE_SCHEMA = Merged.model_json_schema()


def check_review(role: Role, review: Review) -> str | None:
    """What the role's answer breaks (the skill's limits), None when it is whole."""
    if review.verdict not in role.verdicts:
        return f"verdict: one of {', '.join(role.verdicts)}"
    if len(review.problems) > role.problems_max:
        return f"problems: at most {role.problems_max}"
    if role in READERS:
        if review.hurt_most is None:
            return "hurt_most: the one line that hurt most, quoted"
        if len(review.note.split()) >= NOTE_WORDS:
            return f"note: under {NOTE_WORDS} words"
    return None


# What the runs of this process reported, and for which role (the tool reads the run id only).
_REPORTED: dict[uuid.UUID, BaseModel] = {}
_EXPECTS: dict[uuid.UUID, Role | None] = {}  # None: the merge


def _answer(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first.get("loc", ())) or "report"
    return f"Not stored: {where}: {first.get('msg', 'invalid')}. Fix it and report again."


def report(run_id: uuid.UUID | None, raw: object, *, merge: bool) -> tuple[str, bool]:
    """What the report tools do (panel_tools.py): the arguments checked for shape and kept for
    the run, once. Answers the tool's text and whether it is an error."""
    if run_id is None or run_id not in _EXPECTS:
        return "This run cannot report.", True
    role = _EXPECTS[run_id]
    if merge != (role is None):
        return "This run reports with its own tool.", True
    if run_id in _REPORTED:
        return REPORTED_AGAIN, False
    try:
        parsed: BaseModel = Merged.model_validate(raw) if merge else Review.model_validate(raw)
    except ValidationError as exc:
        return _answer(exc), True
    if role is not None and isinstance(parsed, Review):
        broken = check_review(role, parsed)
        if broken is not None:
            return f"Not stored: {broken}. Fix it and report again.", True
    _REPORTED[run_id] = parsed
    return STORED, False


# --- the agents ------------------------------------------------------------------------------


class PanelInput(BaseModel):
    """The input of one reviewer or merge run: a key of this process, the budget and the fake
    stream. No text."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: str = Field(pattern=panel_hold.KEY_PATTERN)
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


@dataclass(frozen=True)
class PanelDefinition(definitions.AgentDefinition):
    """A reviewer or the merge: its first message is the one held in this process."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        return panel_hold.read(data.get("request"))


def _prompt_file(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def _definition(name: str, description: str, prompt: str, tool: str) -> PanelDefinition:
    return PanelDefinition(
        name=name,
        description=description,
        system_prompt=_prompt_file(prompt),
        tools=(tool,),
        input_model=PanelInput,
        model=MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: MODEL},
        max_turns=MAX_TURNS,
        wall_time_s=WALL_TIME_S,
        effort="medium",
        internal=True,
        live_text=False,
        private_io=True,
    )


DEFINITIONS = {
    role.key: definitions.register(
        _definition(
            role.agent,
            f"Hiring panel: {role.label.lower()} tries to reject a CV or a letter.",
            f"panel_{role.key}",
            REVIEW_TOOL,
        )
    )
    for role in ROLES
}
MERGE = definitions.register(
    _definition(
        MERGE_AGENT,
        "Hiring panel: merges the seven reviews into one numbered list of mistakes.",
        "panel_merge",
        MERGE_TOOL,
    )
)


# --- the estimate ----------------------------------------------------------------------------


def review_estimate(name: str, reads: int) -> Any:
    return estimate.writer(estimate.prompt_chars(name), reads, turns=MAX_TURNS, submits=2)


def cost(document_chars: int, brief_chars: int, **limits: int) -> Any:
    """One panel: the seven reviews, the merge, the rebuild (one more write) and the screen
    again. Part of a posting's estimate (estimate.posting)."""
    reads = document_chars + brief_chars
    reviews = [review_estimate(f"panel_{role.key}", reads) for role in ROLES]
    merge = review_estimate("panel_merge", document_chars + len(ROLES) * REVIEW_CHARS)
    rebuild = estimate.writer(estimate.prompt_chars("cv"), reads + document_chars, **limits)
    again = review_estimate("panel_screen", reads)
    return estimate.combine([*reviews, merge, rebuild, again])


def review_budget(name: str, reads: int) -> int:
    return int(review_estimate(name, reads).ceiling)


# --- the document's text and the first messages -----------------------------------------------


def _period(value: Any) -> str:
    return str(value) if value else ""


def document_text(kind: str, content: dict[str, Any], fixed: dict[str, Any] | None) -> str:
    """The document as a reader meets it: its printed fields in order, and above each employer's
    bullets the line the template prints from the fixed facts (title, organisation, place,
    dates); the schools and languages of the fixed facts at the end."""
    parsed_as: Any = {"letter": Kind.letter, "base": "base"}.get(kind, Kind.cv)
    model = c.parse(parsed_as, content)
    employers = {e.get("key"): e for e in (fixed or {}).get("employers") or []}
    lines: list[str] = []
    shown: set[str] = set()
    for path, text in c.text_fields(model):
        parts = path.split(".")
        if parts[0] == "experience" and len(parts) > 1 and parts[1] not in shown:
            shown.add(parts[1])
            role = content["experience"][int(parts[1])] if content.get("experience") else {}
            found = employers.get(role.get("employer_key"), {})
            dates = " to ".join(
                p for p in (_period(found.get("start")), _period(found.get("end"))) if p
            )
            head = ", ".join(
                p
                for p in (
                    found.get("title"),
                    found.get("organisation"),
                    found.get("location"),
                    dates,
                )
                if p
            )
            if head:
                lines.append(head)
        lines.append(text)
    for school in (fixed or {}).get("schools") or []:
        lines.append(", ".join(str(v) for v in (school.get("degree"), school.get("school")) if v))
    languages = [
        " ".join(str(v) for v in (lang.get("name"), lang.get("level")) if v)
        for lang in (fixed or {}).get("languages") or []
    ]
    if languages:
        lines.append("Languages: " + ", ".join(languages))
    return "\n".join(line for line in lines if line)[:DOCUMENT_MAX]


def top_third(text: str) -> str:
    lines = text.splitlines()
    return "\n".join(lines[: max(1, -(-len(lines) // 3))])


def _block(tag: str, value: Any) -> str:
    """A JSON document between tags, every angle bracket escaped: nothing inside can close the
    block or open another (the pattern of apply/agents.py)."""
    text = json.dumps(value, ensure_ascii=False, indent=1)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e")
    text = text.replace("\uff1c", "\\uff1c").replace("\uff1e", "\\uff1e")
    return f"<{tag}>\n{text}\n</{tag}>"


@dataclass(frozen=True)
class Subject:
    """What the reviewers read: held in process for the job's length."""

    kind: str
    text: str
    template: str
    columns: int
    contact_present: bool
    brief: dict[str, Any]
    cv_beside: str | None = None  # a letter's CV, read beside it


def _brief_for_prompt(brief: dict[str, Any]) -> dict[str, Any]:
    out = dict(brief)
    if isinstance(out.get("text"), str) and len(out["text"]) > BRIEF_TEXT_MAX:
        out["text"] = out["text"][:BRIEF_TEXT_MAX] + " [cut]"
    return out


def review_prompt(role: Role, subject: Subject, text: str | None = None) -> str:
    """The first message of one reviewer: one line, then the document, the job, its role, and
    nothing of any other reviewer."""
    title, company = briefs.role_of(subject.brief)
    shown = text if text is not None else subject.text
    if role.key == "reader_recruiter":
        shown = top_third(shown)
    what = WHAT[subject.kind]
    parts = [
        f"Review this {what} as the role below says, then call {REVIEW_TOOL} once.",
        _block(
            "document",
            {
                "kind": what,
                "text": shown,
                "template": subject.template,
                "columns": subject.columns,
                "contact_details_printed": subject.contact_present,
            },
        ),
        _block("job", _brief_for_prompt(subject.brief)),
    ]
    if subject.cv_beside is not None and role.key != "screen":
        parts.append(_block("cv_beside", subject.cv_beside))
    parts.append(
        _block(
            "role",
            {
                "you_are": you_are(role, subject.kind, title, company),
                "verdict_words": list(role.verdicts),
                "at_most_problems": role.problems_max,
            },
        )
    )
    return "\n\n".join(parts)


def merge_prompt(subject: Subject, reviews: dict[str, Any]) -> str:
    answered = {ROLE_OF[k].label: v for k, v in reviews.items() if "missing" not in v}
    return "\n\n".join(
        (
            f"Merge these reviews into one numbered list of mistakes, worst first, then call "
            f"{MERGE_TOOL} once. Name each reviewer by its key: "
            + ", ".join(k for k in reviews if "missing" not in reviews[k])
            + ".",
            _block("document", {"kind": WHAT[subject.kind], "text": subject.text}),
            _block(
                "reviews",
                {
                    "by_key": {k: v for k, v in reviews.items() if "missing" not in v},
                    "labels": list(answered),
                },
            ),
        )
    )


def rebuild_instructions(kind: str, number: int, mistakes: Sequence[dict[str, Any]]) -> str:
    """The rebuild's first message (held in process; the writer's `prompt` reads it)."""
    what = WHAT[kind]
    tool = {"cv": "submit_cv", "letter": "submit_letter", "base": "submit_base"}[kind]
    reads = "get_draft and get_job_brief" if kind != "base" else "get_draft"
    return "\n\n".join(
        (
            f"A hiring panel read version {number} of this {what} and listed the mistakes "
            f"below. Read the current {what} with {reads}, then rewrite it with every mistake "
            "fixed and the same facts: add nothing that is not in the profile, the base CV or "
            "the current draft. Where a fix needs a fact you do not have, leave that part as it "
            "is: the person is asked for the fact elsewhere. Never write a placeholder such as "
            f"[ADD]. Submit the whole {what} with {tool}.",
            _block("mistakes", list(mistakes)),
        )
    )


# --- one model call --------------------------------------------------------------------------


@dataclass(frozen=True)
class Reply:
    report: BaseModel | None
    run_id: uuid.UUID | None = None
    stopped: str = ""  # a code of MISSING, empty when the run ended done


Call = Callable[[str, uuid.UUID, str, int], Awaitable[Reply]]


def _in_db[T](sessions: sessionmaker[Session], step: Callable[[Session], T]) -> T:
    with sessions() as db:
        try:
            out = step(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return out


def _stopped(db: Session, run_id: uuid.UUID) -> str:
    row = db.execute(sa.select(AgentRun.status, AgentRun.error).where(AgentRun.id == run_id)).one()
    if row.status is RunStatus.done:
        return ""
    if row.error == "token_limit":
        return "token_budget"
    return "cancelled" if row.status is RunStatus.cancelled else "error"


@dataclass
class RuntimeCall:
    """`Call` on the agents runtime: one run of the named agent, executed in this process (the
    pattern of apply/model_check.py `RuntimeCheck`). `scripts` names the fake backend's stream
    per agent, or a list of them for its successive calls (tests; the default stream is the
    agent's name)."""

    context: Any
    scripts: dict[str, str | list[str]] = field(default_factory=dict)
    _calls: dict[str, int] = field(default_factory=dict)

    def _script(self, agent: str) -> str | None:
        found = self.scripts.get(agent)
        if not isinstance(found, list):
            return found
        count = self._calls.get(agent, 0)
        self._calls[agent] = count + 1
        return found[min(count, len(found) - 1)] if found else None

    async def __call__(self, agent: str, user_id: uuid.UUID, prompt: str, budget: int) -> Reply:
        from app.agents import runner  # the SDK: the worker's image only

        context = self.context
        key = panel_hold.hold(prompt)
        data = PanelInput(request=key, token_budget=budget, script=self._script(agent))
        expects = None if agent == MERGE_AGENT else ROLE_OF[agent.removeprefix("documents_panel_")]

        def create(db: Session) -> uuid.UUID | str:
            if credentials.get(db, user_id) is None:
                return "no_credential"
            try:
                made = runs.create_run(
                    db,
                    user_id,
                    agent,
                    None,
                    data.model_dump(exclude_none=True),
                    MODEL,
                    context.clock(),
                )
            except runs.ActiveRunsError:
                return "busy"
            return made.id

        try:
            created = await asyncio.to_thread(_in_db, context.sessions, create)
            if isinstance(created, str):
                return Reply(None, None, created)
            _EXPECTS[created] = expects
            try:
                await runner.execute(context, created)
            finally:
                reported = _REPORTED.pop(created, None)
                _EXPECTS.pop(created, None)
            stopped = await asyncio.to_thread(
                _in_db, context.sessions, lambda db: _stopped(db, created)
            )
        finally:
            panel_hold.drop(key)
        if reported is None:
            return Reply(None, created, stopped or "no_report")
        return Reply(reported, created, stopped)


# --- the record ------------------------------------------------------------------------------


def _version(db: Session, document_id: uuid.UUID, number: int) -> DocumentVersion | None:
    return db.scalar(
        sa.select(DocumentVersion).where(
            DocumentVersion.document_id == document_id, DocumentVersion.number == number
        )
    )


def begin(
    db: Session, generation: DocumentGeneration, *, at: datetime, enabled: bool = True
) -> DocumentPanel | None:
    """Open the panel of a write that just stored its version (module docstring, "When"), in
    the caller's transaction. None when there is nothing to review: the panel off, a mode or a
    document it does not review, no version, or no credential (nothing would run)."""
    if not enabled or generation.status is not GenerationStatus.done:
        return None
    if generation.version_number is None:
        return None
    document = db.get(Document, generation.document_id)
    if document is None:
        return None
    if generation.mode is GenerationMode.posting and document.scope == Scope.posting:
        if document.kind not in (Kind.cv, Kind.letter):
            return None
        kind = document.kind.value
    elif generation.mode is GenerationMode.base_upload and document.scope == Scope.base:
        kind = "base"
    else:
        return None
    if credentials.get(db, generation.user_id) is None:
        return None
    made = DocumentPanel(
        id=uuid.uuid4(),
        user_id=generation.user_id,
        document_id=document.id,
        kind=kind,
        status=PanelStatus.running.value,
        source_run_id=generation.run_id,
        reviewed_version=generation.version_number,
        created_at=at,
    )
    db.add(made)
    db.flush()
    logger.info(
        "documents.panel.begun",
        extra={"panel_id": str(made.id), "document_id": str(document.id), "kind": kind},
    )
    return made


async def enqueue(queue: Any, panel_id: uuid.UUID) -> bool:
    from app import queue as queues

    return bool(
        await queue.enqueue(
            FUNCTION, str(panel_id), queue=queues.AGENTS, job_id=f"documents-panel-{panel_id}"
        )
    )


def recover(db: Session, user_id: uuid.UUID, at: datetime) -> list[uuid.UUID]:
    """The person's panels still running `STALE_S` after they began (their job lost): failed
    with `panel_error`, the first version kept. Returns the source runs of the CVs among them,
    whose letter the caller starts after its commit."""
    found = db.scalars(
        sa.select(DocumentPanel)
        .where(
            DocumentPanel.user_id == user_id,
            DocumentPanel.status == PanelStatus.running.value,
            DocumentPanel.created_at < at - timedelta(seconds=STALE_S),
        )
        .with_for_update(skip_locked=True)
    ).all()
    letters: list[uuid.UUID] = []
    for row in found:
        row.status = PanelStatus.failed.value
        row.failed = [*row.failed, "panel_error"]
        row.finished_at = at
        if row.kind == "cv" and row.source_run_id is not None:
            letters.append(row.source_run_id)
        logger.info("documents.panel.recovered", extra={"panel_id": str(row.id)})
    return letters


def _contact_present(db: Session, user_id: uuid.UUID) -> bool:
    sections: Any = db.scalar(sa.select(Profile.sections).where(Profile.user_id == user_id)) or {}
    identity = sections.get("identity") or {}
    return bool(isinstance(identity, dict) and (identity.get("email") or identity.get("phone")))


def _columns(slug: str) -> int:
    return 2 if "columns" in slug else 1


def _fixed_of(db: Session, user_id: uuid.UUID, document: Document) -> dict[str, Any] | None:
    if document.scope == Scope.base:
        return dict(document.draft or {}).get("fixed")
    base_id = document.base_document_id
    if document.kind == Kind.letter and document.posting_id is not None:
        return None
    base = store.get(db, user_id, base_id) if base_id is not None else None
    return dict(base.draft or {}).get("fixed") if base is not None else None


@dataclass(frozen=True)
class Loaded:
    panel_id: uuid.UUID
    user_id: uuid.UUID
    document_id: uuid.UUID
    posting_id: uuid.UUID | None
    kind: str
    reviewed: int
    subject: Subject
    fixed: dict[str, Any] | None
    source_run_id: uuid.UUID | None


def load(db: Session, panel_id: uuid.UUID) -> Loaded | None:
    """What the job reads before any model call; None for a panel no longer running."""
    row = db.get(DocumentPanel, panel_id)
    if row is None or row.status != PanelStatus.running.value:
        return None
    document = store.get(db, row.user_id, row.document_id)
    version = _version(db, row.document_id, row.reviewed_version)
    if document is None or version is None:
        return None
    fixed = _fixed_of(db, row.user_id, document)
    cv_beside = None
    if row.kind == "letter" and document.posting_id is not None:
        cv = next(
            (
                d
                for d in store.posting_documents(db, row.user_id, document.posting_id)
                if d.kind == Kind.cv
            ),
            None,
        )
        if cv is not None:
            cv_beside = document_text("cv", dict(cv.draft or {}), _fixed_of(db, row.user_id, cv))
    if document.posting_id is not None:
        brief = briefs.for_posting(db, row.user_id, document.posting_id) or {}
    else:
        brief = briefs.for_targets(db, row.user_id)
    subject = Subject(
        kind=row.kind,
        text=document_text(row.kind, dict(version.content or {}), fixed),
        template=version.template_slug,
        columns=_columns(version.template_slug),
        contact_present=_contact_present(db, row.user_id),
        brief=brief,
        cv_beside=cv_beside,
    )
    return Loaded(
        row.id,
        row.user_id,
        row.document_id,
        document.posting_id,
        row.kind,
        row.reviewed_version,
        subject,
        fixed,
        row.source_run_id,
    )


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def merged_mistakes(merged: Merged, answered: Sequence[str]) -> list[dict[str, Any]]:
    """The merge's list as stored: its order kept (worst first), the duplicates it left merged
    into the first (the same quoted line, case and spaces folded; the reviewers joined), only
    reviewers that answered named, numbered from 1."""
    known = set(answered)
    out: list[dict[str, Any]] = []
    by_quote: dict[str, dict[str, Any]] = {}
    for mistake in merged.mistakes:
        flagged = [k for k in dict.fromkeys(mistake.flagged_by) if k in known]
        key = _norm(mistake.quote)
        if key in by_quote:
            kept = by_quote[key]
            kept["flagged_by"] = list(dict.fromkeys([*kept["flagged_by"], *flagged]))
            continue
        item = {
            "quote": mistake.quote,
            "why": mistake.why,
            "flagged_by": flagged,
            "fix": mistake.fix,
        }
        by_quote[key] = item
        out.append(item)
    for number, item in enumerate(out, start=1):
        item["number"] = number
    return out


def _questions(merged: Merged) -> list[dict[str, str]]:
    seen: set[str] = set()
    out = []
    for question in merged.questions:
        key = _norm(question.question)
        if key not in seen:
            seen.add(key)
            out.append({"question": question.question, "section": question.section})
    return out


def _review_record(role: Role, reply: Reply) -> dict[str, Any]:
    if reply.report is None or not isinstance(reply.report, Review):
        return {"missing": reply.stopped or "no_report"}
    return reply.report.model_dump()


def _save(sessions: sessionmaker[Session], panel_id: uuid.UUID, **values: Any) -> None:
    def step(db: Session) -> None:
        row = db.get(DocumentPanel, panel_id, with_for_update=True)
        if row is None:
            return
        for name, value in values.items():
            if name in ("failed", "run_ids"):
                value = [*getattr(row, name), *value]
            setattr(row, name, value)

    _in_db(sessions, step)


# --- the rebuild -----------------------------------------------------------------------------

Execute = Callable[[uuid.UUID], Awaitable[None]]


def _start_rebuild(
    db: Session, loaded: Loaded, key: str, at: datetime, settings: Any, script: str | None
) -> uuid.UUID | str:
    """The rebuild's generation (mode `panel`) and its writer run, started; a code when it
    cannot start (`rebuild_busy`: a generation of the document is open, or the person's runs are
    all busy). The caller commits."""
    from app.documents import generate, writer_base  # both import this module's callers

    if loaded.posting_id is not None:
        store.lock_posting(db, loaded.user_id, loaded.posting_id)  # lock 2 before lock 3
    document = store.lock_document(db, loaded.user_id, loaded.document_id)
    if document.scope == Scope.base:
        figures = estimate.writer(estimate.prompt_chars("base"), 2 * len(loaded.subject.text))
    else:
        figures = estimate.writer(
            estimate.prompt_chars(loaded.kind),
            estimate.profile_chars(db, loaded.user_id) + 2 * len(loaded.subject.text),
            **estimate.limits(),
        )
    try:
        generation = generate.create(
            db,
            document,
            GenerationMode.panel,
            GenerationTrigger.click,
            document.base_document_id if document.kind == Kind.cv else None,
            estimate.as_json(figures),
            figures.ceiling,
            at=at,
        )
    except store.GenerationOpenError:
        return "rebuild_busy"
    try:
        if document.scope == Scope.base:
            run = writer_base.start(
                db, generation, at=at, script=script, settings=settings, panel=key
            )
        else:
            run = generate.start(db, generation, at=at, script=script, settings=settings, panel=key)
    except runs.ActiveRunsError:
        generation.status = GenerationStatus.cancelled
        generation.finished_at = at
        return "rebuild_busy"
    return run.id


def _rebuilt(db: Session, run_id: uuid.UUID) -> tuple[int, dict[str, Any]] | None:
    generation = db.scalar(sa.select(DocumentGeneration).where(DocumentGeneration.run_id == run_id))
    if (
        generation is None
        or generation.status is not GenerationStatus.done
        or generation.version_number is None
    ):
        return None
    version = _version(db, generation.document_id, generation.version_number)
    return (generation.version_number, dict(version.content or {})) if version else None


# --- the job ---------------------------------------------------------------------------------


@dataclass
class Steps:
    """How the job calls the models and runs the rebuild: the runtime in the worker, fakes in
    tests."""

    call: Call
    execute: Execute
    sessions: sessionmaker[Session]
    clock: Callable[[], datetime]
    settings: Any
    rebuild_script: str | None = None
    after: Callable[[uuid.UUID | None, str], Awaitable[None]] | None = None


async def _reviews(steps: Steps, loaded: Loaded) -> tuple[dict[str, Any], list[uuid.UUID]]:
    gate = asyncio.Semaphore(AT_ONCE)
    reads = len(loaded.subject.text) + len(json.dumps(loaded.subject.brief, ensure_ascii=False))

    async def one(role: Role) -> tuple[Role, Reply]:
        async with gate:
            try:
                reply = await steps.call(
                    role.agent,
                    loaded.user_id,
                    review_prompt(role, loaded.subject),
                    review_budget(f"panel_{role.key}", reads),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "documents.panel.review_failed",
                    extra={
                        "panel_id": str(loaded.panel_id),
                        "role": role.key,
                        "error": type(exc).__name__,
                    },
                )
                reply = Reply(None, None, "error")
            return role, reply

    done = await asyncio.gather(*(one(role) for role in ROLES))
    reviews = {role.key: _review_record(role, reply) for role, reply in done}
    spent = [reply.run_id for _, reply in done if reply.run_id is not None]
    return reviews, spent


async def run(steps: Steps, panel_id: uuid.UUID) -> str:
    """The whole panel (module docstring). Returns its final status. Never raises for a step
    that fails: the panel says what failed, and the letter of a CV still follows."""
    try:
        loaded = await asyncio.to_thread(_in_db, steps.sessions, lambda db: load(db, panel_id))
    except Exception as exc:
        # A version that no longer reads: the panel fails, the first version stays.
        logger.error(
            "documents.panel.failed",
            extra={"panel_id": str(panel_id), "error": type(exc).__name__},
        )
        await asyncio.to_thread(
            _save,
            steps.sessions,
            panel_id,
            status=PanelStatus.failed.value,
            failed=["panel_error"],
            finished_at=steps.clock(),
        )
        if steps.after is not None:
            row = await asyncio.to_thread(
                _in_db, steps.sessions, lambda db: db.get(DocumentPanel, panel_id)
            )
            cv_run = row.source_run_id if row is not None and row.kind == "cv" else None
            await steps.after(cv_run, PanelStatus.failed.value)  # the letter still follows
        return PanelStatus.failed.value
    if loaded is None:
        return "gone"
    status = PanelStatus.failed.value
    try:
        status = await _run(steps, loaded)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                _save,
                steps.sessions,
                panel_id,
                status=PanelStatus.failed.value,
                failed=["panel_error"],
                finished_at=steps.clock(),
            )
        raise
    except Exception as exc:
        logger.error(
            "documents.panel.failed",
            extra={"panel_id": str(panel_id), "error": type(exc).__name__},
        )
        await asyncio.to_thread(
            _save,
            steps.sessions,
            panel_id,
            status=PanelStatus.failed.value,
            failed=["panel_error"],
            finished_at=steps.clock(),
        )
    if steps.after is not None:
        await steps.after(loaded.source_run_id if loaded.kind == "cv" else None, status)
    return status


async def _run(steps: Steps, loaded: Loaded) -> str:
    panel_id = loaded.panel_id
    reviews, spent = await _reviews(steps, loaded)
    answered = [key for key, value in reviews.items() if "missing" not in value]
    screen = reviews["screen"].get("verdict")
    await asyncio.to_thread(
        _save, steps.sessions, panel_id, reviews=reviews, run_ids=spent, screen_before=screen
    )
    logger.info(
        "documents.panel.reviewed",
        extra={
            "panel_id": str(panel_id),
            "answered": len(answered),
            "missing": len(ROLES) - len(answered),
        },
    )
    if not answered:
        return await _close(steps, panel_id, ["reviews"])
    try:
        merged_reply = await steps.call(
            MERGE_AGENT,
            loaded.user_id,
            merge_prompt(loaded.subject, reviews),
            review_budget("panel_merge", len(loaded.subject.text) + len(ROLES) * REVIEW_CHARS),
        )
    except Exception as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        merged_reply = Reply(None, None, "error")
    spent = [merged_reply.run_id] if merged_reply.run_id is not None else []
    if not isinstance(merged_reply.report, Merged):
        await asyncio.to_thread(_save, steps.sessions, panel_id, run_ids=spent)
        return await _close(steps, panel_id, ["merge"])
    merged = merged_reply.report
    mistakes = merged_mistakes(merged, answered)
    closest = (
        {"role": merged.closest, "why": merged.closest_why}
        if merged.closest in ROLE_OF and merged.closest in answered
        else None
    )
    await asyncio.to_thread(
        _save,
        steps.sessions,
        panel_id,
        run_ids=spent,
        mistakes=mistakes,
        questions=_questions(merged),
        closest=closest,
    )
    if not mistakes:
        await asyncio.to_thread(_save, steps.sessions, panel_id, screen_after=screen)
        return await _close(steps, panel_id, [])
    # The rebuild, then the screen again on its version.
    key = panel_hold.hold(rebuild_instructions(loaded.kind, loaded.reviewed, mistakes))
    try:
        started = await asyncio.to_thread(
            _in_db,
            steps.sessions,
            lambda db: _start_rebuild(
                db, loaded, key, steps.clock(), steps.settings, steps.rebuild_script
            ),
        )
        if isinstance(started, str):
            return await _close(steps, panel_id, [started])
        await steps.execute(started)
    finally:
        panel_hold.drop(key)
    rebuilt = await asyncio.to_thread(_in_db, steps.sessions, lambda db: _rebuilt(db, started))
    await asyncio.to_thread(_save, steps.sessions, panel_id, run_ids=[started])
    if rebuilt is None:
        return await _close(steps, panel_id, ["rebuild"])
    number, content = rebuilt
    await asyncio.to_thread(_save, steps.sessions, panel_id, rebuilt_version=number)
    again_subject = Subject(
        loaded.kind,
        document_text(loaded.kind, content, loaded.fixed),
        loaded.subject.template,
        loaded.subject.columns,
        loaded.subject.contact_present,
        loaded.subject.brief,
    )
    screen_role = ROLE_OF["screen"]
    try:
        again = await steps.call(
            screen_role.agent,
            loaded.user_id,
            review_prompt(screen_role, again_subject),
            review_budget("panel_screen", len(again_subject.text)),
        )
    except Exception as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        again = Reply(None, None, "error")
    after = again.report.verdict if isinstance(again.report, Review) else None
    await asyncio.to_thread(
        _save,
        steps.sessions,
        panel_id,
        screen_after=after,
        run_ids=[again.run_id] if again.run_id is not None else [],
    )
    return await _close(steps, panel_id, [] if after is not None else ["screen_after"])


async def _close(steps: Steps, panel_id: uuid.UUID, failed: list[str]) -> str:
    whole = not failed or failed == ["screen_after"]
    status = PanelStatus.done.value if whole else PanelStatus.failed.value
    await asyncio.to_thread(
        _save, steps.sessions, panel_id, status=status, failed=failed, finished_at=steps.clock()
    )
    logger.info(
        "documents.panel.closed",
        extra={"panel_id": str(panel_id), "status": status, "failed": ",".join(failed)},
    )
    return status


# --- what the person sees --------------------------------------------------------------------


def view(row: DocumentPanel) -> dict[str, Any]:
    """The panel as the document page and the job page show it (panel_router.py)."""
    reviews = []
    for role in ROLES:
        found = (row.reviews or {}).get(role.key) or {"missing": "not_run"}
        hurt = found.get("hurt_most") or (found.get("problems") or [None])[0]
        reviews.append(
            {
                "role": role.key,
                "label": role.label,
                "verdict": found.get("verdict"),
                "missing": found.get("missing"),
                "line": (hurt or {}).get("quote") if isinstance(hurt, dict) else None,
                "why": (hurt or {}).get("why") if isinstance(hurt, dict) else None,
            }
        )
    readers = [r for r in reviews if r["role"].startswith("reader_") and r["verdict"]]
    hires = sum(1 for r in readers if r["verdict"] == "HIRE")
    closest = row.closest or {}
    return {
        "id": str(row.id),
        "status": row.status,
        "kind": row.kind,
        "reviewed_version": row.reviewed_version,
        "rebuilt_version": row.rebuilt_version,
        "reviews": reviews,
        "mistakes": list(row.mistakes or []),
        "questions": list(row.questions or []),
        "screen_before": row.screen_before,
        "screen_after": row.screen_after,
        "readers_hire": hires,
        "readers_answered": len(readers),
        "strong": row.status == PanelStatus.done.value and not row.mistakes,
        "closest": (
            {
                "role": closest.get("role"),
                "label": ROLE_OF[closest["role"]].label,
                "why": closest.get("why") or "",
            }
            if closest.get("role") in ROLE_OF
            else None
        ),
        "failed": list(row.failed or []),
        "created_at": row.created_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def latest(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> DocumentPanel | None:
    return db.scalar(
        sa.select(DocumentPanel)
        .where(DocumentPanel.user_id == user_id, DocumentPanel.document_id == document_id)
        .order_by(DocumentPanel.created_at.desc(), DocumentPanel.id.desc())
        .limit(1)
    )


def is_running(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(DocumentPanel)
            .where(
                DocumentPanel.user_id == user_id,
                DocumentPanel.document_id == document_id,
                DocumentPanel.status == PanelStatus.running.value,
            )
        )
        or 0
    ) > 0


def tokens(db: Session, panel: DocumentPanel) -> tuple[int, int]:
    """The input and output tokens of the panel's runs (the spend counts them)."""
    if not panel.run_ids:
        return 0, 0
    row = db.execute(
        sa.select(
            sa.func.coalesce(sa.func.sum(AgentRun.input_tokens), 0),
            sa.func.coalesce(sa.func.sum(AgentRun.output_tokens), 0),
        ).where(AgentRun.id.in_(list(panel.run_ids)), AgentRun.user_id == panel.user_id)
    ).one()
    return int(row[0]), int(row[1])


def close_unsent(sessions: sessionmaker[Session], panel_id: uuid.UUID, at: datetime) -> None:
    """A panel whose job could not be queued: failed with `not_queued`, the first version kept."""
    _save(
        sessions, panel_id, status=PanelStatus.failed.value, failed=["not_queued"], finished_at=at
    )


def _one(found: str | list[str] | None) -> str | None:
    if isinstance(found, list):
        return found[0] if found else None
    return found


async def job(
    context: Any, panel_id: uuid.UUID, *, scripts: dict[str, str | list[str]] | None = None
) -> str:
    """The arq job `documents_panel` (app/worker.py): the panel on the agents runtime of this
    process, then the letter of a CV, whatever the panel's outcome."""
    from app.agents import runner
    from app.documents import generate

    async def execute(run_id: uuid.UUID) -> None:
        await runner.execute(context, run_id)

    async def after(cv_run: uuid.UUID | None, status: str) -> None:
        if cv_run is not None:
            await generate.start_letter_after(context, cv_run)

    steps = Steps(
        call=RuntimeCall(context, dict(scripts or {})),
        execute=execute,
        sessions=context.sessions,
        clock=context.clock,
        settings=context.settings,
        rebuild_script=_one((scripts or {}).get("rebuild")),
        after=after,
    )
    return await run(steps, panel_id)


if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools (the api image has no SDK, as writer_cv).
    from app.documents import panel_tools as _tools  # noqa: F401
