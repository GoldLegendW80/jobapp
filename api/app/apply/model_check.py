"""The check by model of a Full auto item, `apply_check` (apply A13c; SPEC-apply, "Guardrails" 6
and 7, "Cost per application"). Mail's M20 is paused, so apply makes its own call (owner,
2026-10-19).

What it judges: the free text the answer agent wrote on the form (an answer of source `agent`
and kind text or textarea, `FREE_TEXT`), against the profile and the posting, for the claims of
`CODES`: a fact the profile does not hold, a figure rounded up, a title or degree relabelled, the
tone, and the person's rules (the topics the person keeps, the dash rule). One run per item and
answers hash, before its decision.

The order. The submit job of a `ready` Full auto item (`submit._decide`) calls `before_decision`
first, outside any transaction:
1. `request_for` reads the item's own free texts, the profile's allowlist (`agent.profile_for`:
   facts, experience, skills, and of the answers only the motivation, the stated years and the
   highest degree) and the item's posting, all of the item's own person. It asks for no call
   when the item has no free text by the agent, or when the decision would refuse it anyway
   (`full_auto.checks` answers anything but `model_check_not_run`, or the documents need a look),
   which also covers a result already stored for the current hash: one call per item and hash.
2. The run: `RuntimeCheck`, the agents runtime in this process (the pattern of
   `agents.RuntimeAnswerAgent`): `internal`, `private_io`, `live_text=False`,
   `model_by_credential`, `TOKEN_BUDGET` (twice the estimate of 6,500 tokens), one tool,
   `report_check` (app/apply/check_tools.py), and no other. The run's input holds a random key of
   the request held in this process, the budget and the fake stream's name: no text.
3. `judge` (pure) turns the reply into a verdict, and `record` writes it as the application
   event `model_check` (the hash it read, the verdict, the reason, the codes and their spans as
   offsets in the answer, never a word of it) and its tokens on the application's `cost`. The
   run itself is an agent run of the person: its tokens and cost count in the person's spend.

Fail closed: only a report of `passed` with no claim passes. A run that errs, is cancelled, times
out or reaches its budget, a run with no report or a report that is not `passed`, and a person
without a credential are not `passed`. A person whose run slots are all busy is asked again at
the next tick (no event; the Like's own validity bounds the wait).

The gate reads it (`code`, from `full_auto.checks`, in place of A13a's rule that sent every free
text by the agent to the review queue): a stored `passed` for the current answers hash lets the
decision go on; a flag, a failure or no result for that hash (`model_check_not_run`: never run,
or the answers changed since) sends the item to the review queue with its reason. The same reading
runs again before the click (`full_auto.recheck`). Submit services without a checker never call
the model, so their items with free text by the agent still go to the queue.

Nothing of an answer, the profile or the posting goes into a log, an event or the run's record:
codes, counts, offsets and sizes.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import secrets
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials, definitions, runs
from app.agents.models import CredentialKind
from app.apply import agent, agents
from app.apply.models import AnswerSource, Application, ApplicationAnswer, ApplicationEvent, Mode
from app.apply.models import State as AppState
from app.jobs.models import Job
from app.profile import service as profile_service

if TYPE_CHECKING:
    from app.agents.runner import RunContext

logger = logging.getLogger(__name__)

NAME = "apply_check"
REPORT = "report_check"
TOOLS = (REPORT,)
PROMPT_PATH = Path(__file__).parent / "prompts" / "check.md"
MODEL = agents.MODEL
MODEL_API_KEY = agents.MODEL_API_KEY
# One report, one correction if the tool refuses the first, one closing line.
MAX_TURNS = 3
WALL_TIME_S = 120.0
# SPEC-apply, "Cost per application": about 6,000 tokens in and 500 out per item.
ESTIMATE_TOKENS = 6_500
TOKEN_BUDGET = 2 * ESTIMATE_TOKENS
FREE_TEXT = frozenset({"text", "textarea"})
EVENT = "model_check"
CODES = ("not_in_profile", "rounded_up", "relabelled", "tone", "person_rule")
MAX_ANSWERS = 20
MAX_CLAIMS = 40
# What the prompt carries at most of each block, in characters (about 3 per token).
ANSWER_MAX = 2 * agent.VALUE_MAX
POSTING_MAX = 6_000
PROFILE_MAX = 9_000
REPORTED_ONCE = "Your report is already stored. Stop and answer with one short line."
STORED = "Stored. Stop and answer with one short line."
NOT_RUN_CODE = "model_check_not_run"


class Verdict(StrEnum):
    passed = "passed"
    flagged = "flagged"
    failed = "failed"
    not_run = "not_run"


# The review queue's reason for each verdict that is not `passed` (gate.SENTENCES through
# full_auto.SENTENCES).
CODE_OF = {
    Verdict.flagged: "model_check_flagged",
    Verdict.failed: "model_check_failed",
    Verdict.not_run: NOT_RUN_CODE,
}
SENTENCES = {
    "model_check_flagged": (
        "The check of the app's free text answers found claims to look at, so you check them first."
    ),
    "model_check_failed": (
        "The app could not finish checking its free text answers, so you check them first."
    ),
    NOT_RUN_CODE: (
        "The app's free text answers were not checked against your profile, so you check them "
        "first."
    ),
}


# --- what the model receives and gives back -----------------------------------------------------


@dataclass(frozen=True)
class Text:
    """One free text answer the agent wrote: its field's key, the question and the value."""

    key: str
    label: str
    value: str


@dataclass(frozen=True)
class Request:
    user_id: str
    texts: tuple[Text, ...]
    profile: Mapping[str, Any]  # `agent.profile_for`
    posting_title: str
    posting_company: str
    posting_text: str  # data, never instructions
    token_budget: int = TOKEN_BUDGET


class ReportedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(pattern=r"^a[1-9][0-9]{0,2}$")
    code: Literal["not_in_profile", "rounded_up", "relabelled", "tone", "person_rule"]
    quote: str = Field(default="", max_length=ANSWER_MAX)


class Reported(BaseModel):
    """The arguments of `report_check`."""

    model_config = ConfigDict(extra="forbid")

    passed: bool = Field(strict=True)
    claims: list[ReportedClaim] = Field(default_factory=list, max_length=MAX_CLAIMS)


REPORT_SCHEMA = Reported.model_json_schema()


@dataclass(frozen=True)
class Reply:
    report: Reported | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    # token_budget, cancelled, error; no_credential and busy: no run was made
    stopped: str = ""


class Checker(Protocol):
    async def __call__(self, request: Request) -> Reply: ...


@dataclass(frozen=True)
class Claim:
    """A flagged claim as stored: the field's key (None for an id that was not asked), the code
    and where the quote stands in the answer (None when the quote is not found in it)."""

    key: str | None
    code: str
    start: int | None
    end: int | None


@dataclass(frozen=True)
class Outcome:
    verdict: Verdict
    reason: str
    claims: tuple[Claim, ...] = ()


def answer_ids(request: Request) -> dict[str, Text]:
    """`a1`, `a2`, ... in the order sent, to the answer."""
    return {f"a{n}": text for n, text in enumerate(request.texts[:MAX_ANSWERS], start=1)}


def judge(request: Request, reply: Reply) -> Outcome:
    """The verdict of one reply, failing closed: only a report of `passed` with no claim, from a
    run that ended well, passes. A claim makes it `flagged` whatever `passed` says."""
    if reply.stopped == "no_credential":
        return Outcome(Verdict.not_run, "no_credential")
    if reply.stopped:
        return Outcome(Verdict.failed, reply.stopped)
    if reply.report is None:
        return Outcome(Verdict.failed, "no_report")
    asked = answer_ids(request)
    claims = tuple(_claim(asked.get(one.answer), one) for one in reply.report.claims)
    if claims:
        return Outcome(Verdict.flagged, "claims", claims)
    if not reply.report.passed:
        return Outcome(Verdict.failed, "not_passed")
    return Outcome(Verdict.passed, "passed")


def _claim(text: Text | None, reported: ReportedClaim) -> Claim:
    if text is None:
        return Claim(None, reported.code, None, None)
    start = text.value.find(reported.quote) if reported.quote else -1
    if start < 0:
        return Claim(text.key, reported.code, None, None)
    return Claim(text.key, reported.code, start, start + len(reported.quote))


def prompt_for(request: Request) -> str:
    """The first message of a run: what to do in one line, then the three data blocks."""
    answers = [
        {"id": f"a{n}", "question": text.label, "text": agents._clip(text.value, ANSWER_MAX)}
        for n, text in enumerate(request.texts[:MAX_ANSWERS], start=1)
    ]
    profile: Any = json.loads(json.dumps(dict(request.profile), ensure_ascii=False, default=str))
    flat = json.dumps(profile, ensure_ascii=False)
    if len(flat) > PROFILE_MAX:
        profile = agents._clip(flat, PROFILE_MAX)
    return "\n\n".join(
        (
            "Check these answers against the profile, then call report_check once.",
            agents._block("answers", answers),
            agents._block("profile", profile),
            agents._block(
                "posting",
                {
                    "title": request.posting_title,
                    "company": request.posting_company,
                    "text": agents._clip(request.posting_text, POSTING_MAX),
                },
            ),
        )
    )


# --- the agent on the runtime --------------------------------------------------------------------


class CheckInput(BaseModel):
    """The input of one run: a key of this process, the budget and the fake stream. No text."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: str = Field(pattern=r"^[0-9a-f]{32}$")
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


# What lives in this process only while a run lasts (the pattern of agents.py).
_PENDING: dict[str, Request] = {}
_REPORTED: dict[uuid.UUID, Reported] = {}
_LOCK = threading.Lock()


@dataclass(frozen=True)
class CheckDefinition(definitions.AgentDefinition):
    """The definition, with its first message built from the request held in this process."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        with _LOCK:
            request = _PENDING.get(str(data.get("request")))
        if request is None:
            # A run nobody waits for (started otherwise, or its job gone): no model call.
            raise LookupError("no pending check request")
        return prompt_for(request)


def report(run_id: uuid.UUID | None, raw: object) -> tuple[str, bool]:
    """What `report_check` does: the arguments checked for shape and kept for the run, once.
    Answers the tool's text and whether it is an error."""
    if run_id is None:
        return "This run cannot report.", True
    try:
        parsed = Reported.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "report"
        return f"Not stored: {where}: {first.get('msg', 'invalid')}. Fix it and report again.", True
    with _LOCK:
        if run_id in _REPORTED:
            return REPORTED_ONCE, True
        _REPORTED[run_id] = parsed
    return STORED, False


def take(run_id: uuid.UUID) -> Reported | None:
    """What the run reported, forgotten here; None for a run that never reported."""
    with _LOCK:
        return _REPORTED.pop(run_id, None)


def _definition() -> CheckDefinition:
    return CheckDefinition(
        name=NAME,
        description="Checks the free text answers of one application form against the profile.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=TOOLS,
        input_model=CheckInput,
        model=MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: MODEL_API_KEY},
        max_turns=MAX_TURNS,
        wall_time_s=WALL_TIME_S,
        effort="medium",
        internal=True,
        live_text=False,
        private_io=True,
    )


APPLY_CHECK = definitions.register(_definition())


@dataclass
class RuntimeCheck:
    """`Checker` on the agents runtime: one `apply_check` run per call, executed in this process.
    `context` builds the runner's context (app/worker.py `agent_context`); `script` names the
    fake backend's stream (tests; the default stream is the agent's name)."""

    context: Callable[[], RunContext]
    script: str | None = None

    async def __call__(self, request: Request) -> Reply:
        from app.agents import runner  # the SDK: the worker's image only

        context = self.context()
        key = secrets.token_hex(16)
        user_id = uuid.UUID(request.user_id)
        data = CheckInput(
            request=key, token_budget=request.token_budget, script=self.script
        ).model_dump(exclude_none=True)

        def create(db: Session) -> uuid.UUID | str:
            if credentials.get(db, user_id) is None:
                return "no_credential"
            try:
                made = runs.create_run(db, user_id, NAME, None, data, MODEL, context.clock())
            except runs.ActiveRunsError:
                return "busy"
            return made.id

        creating = asyncio.ensure_future(asyncio.to_thread(agents._in_db, context.sessions, create))
        try:
            created = await asyncio.shield(creating)
        except asyncio.CancelledError:
            # The row may be written while the job is cancelled: wait for it, then fail it.
            with contextlib.suppress(Exception):
                made = await asyncio.shield(creating)
                if isinstance(made, uuid.UUID):
                    await agents._release(context, made)
            raise
        if isinstance(created, str):
            return Reply(stopped=created)
        with _LOCK:
            _PENDING[key] = request
        try:
            await runner.execute(context, created)
        except asyncio.CancelledError:
            await agents._release(context, created)
            raise
        finally:
            with _LOCK:
                _PENDING.pop(key, None)
            reported = take(created)
        ended = await asyncio.to_thread(
            agents._in_db, context.sessions, lambda db: agents._ended(db, created)
        )
        return Reply(
            report=reported,
            input_tokens=ended.input_tokens,
            output_tokens=ended.output_tokens,
            stopped=agents._stopped(ended),
        )


# --- the store -----------------------------------------------------------------------------------


def _texts(db: Session, application: Application) -> list[Text]:
    rows = db.execute(
        sa.select(ApplicationAnswer.field_key, ApplicationAnswer.label, ApplicationAnswer.value)
        .where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.user_id == application.user_id,
            ApplicationAnswer.source == AnswerSource.agent,
            ApplicationAnswer.kind.in_(FREE_TEXT),
        )
        .order_by(ApplicationAnswer.field_key)
    ).all()
    return [Text(key, label, value) for key, label, value in rows if isinstance(value, str)]


def _stored(db: Session, application: Application) -> dict[str, Any] | None:
    found: dict[str, Any] | None = db.scalar(
        sa.select(ApplicationEvent.data)
        .where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.user_id == application.user_id,
            ApplicationEvent.kind == EVENT,
        )
        .order_by(ApplicationEvent.id.desc())
        .limit(1)
    )
    return found


def code(db: Session, application: Application, rows: list[Any]) -> str | None:
    """The gate's reading (module docstring): None when the item has no free text by the agent
    or its check passed for the current answers hash, else the review queue's code. `rows` are
    the item's answers as (kind, source)."""
    from app.apply import gate  # gate imports full_auto, which imports this module

    if not any(source is AnswerSource.agent and kind in FREE_TEXT for kind, source in rows):
        return None
    stored = _stored(db, application)
    if stored is None or stored.get("hash") != gate.current_hash(db, application).hex():
        return NOT_RUN_CODE
    try:
        verdict = Verdict(stored.get("verdict"))
    except ValueError:
        return CODE_OF[Verdict.failed]
    return CODE_OF.get(verdict)


def request_for(db: Session, application_id: uuid.UUID, now: datetime) -> tuple[Request, str] | str:
    """What one run reads, with the answers hash it covers, or why no run is needed."""
    from app.apply import full_auto, gate  # both import this module

    application = db.get(Application, application_id)
    if application is None:
        return "gone"
    if application.state is not AppState.ready or application.mode is not Mode.full_auto:
        return "superseded"
    posting = db.execute(
        sa.select(Job).where(Job.id == application.posting_id, Job.user_id == application.user_id)
    ).scalar_one_or_none()
    if posting is None:
        return "gone"
    if gate.documents_need_review(application.document_versions):
        return "refused_anyway"
    if full_auto.checks(db, application, posting) != NOT_RUN_CODE:
        return "not_needed"
    texts = _texts(db, application)
    if not texts:
        return "no_text"
    profile = profile_service.get_profile(db, application.user_id, now)
    sections = {k: v for k, v in profile_service.sections_view(profile).items() if v}
    request = Request(
        user_id=str(application.user_id),
        texts=tuple(texts),
        profile=agent.profile_for(sections),
        posting_title=posting.title,
        posting_company=posting.company,
        posting_text=posting.posting_text or "",
    )
    return request, gate.current_hash(db, application).hex()


def record(
    db: Session,
    application_id: uuid.UUID,
    digest: str,
    outcome: Outcome,
    reply: Reply,
    now: datetime,
) -> str:
    """The verdict as the event `model_check` and the tokens on the application's `cost`, under
    the application's row lock. The caller commits."""
    application = db.execute(
        sa.select(Application).where(Application.id == application_id).with_for_update()
    ).scalar_one_or_none()
    if application is None:
        return "gone"
    spent = {
        "verdict": outcome.verdict.value,
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
    }
    application.cost = {**(application.cost or {}), "check": spent}
    db.add(
        ApplicationEvent(
            application_id=application.id,
            user_id=application.user_id,
            at=now,
            kind=EVENT,
            data={
                "hash": digest,
                "verdict": outcome.verdict.value,
                "reason": outcome.reason,
                "claims": [
                    {"field": c.key, "code": c.code, "start": c.start, "end": c.end}
                    for c in outcome.claims
                ],
                "estimate_tokens": ESTIMATE_TOKENS,
                "input_tokens": reply.input_tokens,
                "output_tokens": reply.output_tokens,
            },
        )
    )
    return outcome.verdict.value


async def before_decision(
    checker: Checker,
    sessions: sessionmaker[Session],
    clock: Callable[[], datetime],
    application_id: uuid.UUID,
) -> str | None:
    """Steps 1 to 3 of the module docstring. Returns `check_busy` when the decision must wait for
    a free run slot, else None (the decision goes on and reads what was stored)."""
    found = await asyncio.to_thread(
        agents._in_db, sessions, lambda db: request_for(db, application_id, clock())
    )
    if isinstance(found, str):
        return None
    request, digest = found
    try:
        reply = await checker(request)
    except Exception as exc:  # the model failing is a failed check, never a pass
        logger.warning("apply.check_failed", extra={"error": type(exc).__name__})
        reply = Reply(stopped="error")
    if reply.stopped == "busy":
        return "check_busy"
    outcome = judge(request, reply)
    verdict = await asyncio.to_thread(
        agents._in_db,
        sessions,
        lambda db: record(db, application_id, digest, outcome, reply, clock()),
    )
    logger.info(
        "apply.check",
        extra={"verdict": verdict, "reason": outcome.reason, "claims": len(outcome.claims)},
    )
    return None


if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tool: importing it registers it with the runner.
    from app.apply import check_tools as _tools  # noqa: F401
