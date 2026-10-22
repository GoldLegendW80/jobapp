"""The answer agent of an application on the agents runtime, `apply_answer` (A5, second slice;
SPEC-apply, "Answers to standard questions", source 5, and "Cost per application").

`agent.py` decides what is asked and what is kept; this file is the one model call between the
two. It asks of the runner (app/agents/runner.py, "What a definition can ask of a run"):

- `internal`: started by the apply run only, through `RuntimeAnswerAgent`; POST /api/agents/runs
  refuses it and GET /api/agents does not list it;
- `private_io`, `live_text=False`: the run's events hold the tool's name and sizes, never an
  answer, its output holds counts, and nothing of it is live;
- `model_by_credential`: Opus on a subscription, Sonnet on an API key (the writers' choice,
  documents D7), the turns and the wall time below;
- `token_budget`: `agent.TOKEN_BUDGET` (34,000, twice the spec's estimate of one call), written
  in the run's input by `RuntimeAnswerAgent`; a run that reaches it is stopped by the runner and
  its questions stay left;
- one tool, `submit_answers` (app/apply/answer_tools.py): one entry per question, its value or
  null ("leave") and the source the model declares. What it stores is read back here once the
  run has ended (`take`), then judged by `agent.judge`; the tool decides nothing.

The run happens inside the fill (the `jobapp:apply` job, which holds the person's browser lease):
`RuntimeAnswerAgent` creates the run and executes it in this process (`runner.execute`), never
through the agents queue. So the run's input holds no text of the person, the posting or the
form: only `request`, a random key of the `agent.Request` held in this process while the run
lasts, the token budget and the fake backend's `script`. The prompt is built from that request
(`prompt_for`): the questions (as `q1`, `q2`: the model never sees a field's key), the profile's
allowlist (`agent.profile_for`: facts, experience, skills, and of the answers only the
motivation, the stated years and the highest degree; never identity, an EEO answer, pay, work
authorization or a practicality), the posting and the letter, each a JSON document between tags,
with every angle bracket and its lookalikes escaped, so no text of a page can close its block.
The system prompt (prompts/answer.md) says that what is inside is data, never instructions.

Without a credential no run is created (`no_credential`); over the person's active runs
(`runs.ACTIVE_PER_USER`) none either (`busy`): in both cases every question stays left, as for a
run with no answer agent. A run that failed or was stopped says so in `Reply.stopped`, and what
it submitted before is still judged. A fill cancelled after the run was created but before the
runner claimed it fails the run (`runs.fail_queued`, shielded), so it never holds one of the
person's run slots until the stale reaper finds it.

The run executes in the fill's process, outside the agents queue and so outside its
`MAX_AGENT_RUNS` (app/worker.py): with the fill's one at a time queue, the worker holds at most
three Claude Code processes.

Importing this module registers the agent, and its tool where the Claude Agent SDK is installed
(the worker). It is a registration module (`agents`, app/worker.py `REGISTRATION_MODULES`), so
the worker imports it by itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import secrets
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials, definitions, runs
from app.agents.failures import TOKEN_LIMIT
from app.agents.models import AgentRun, CredentialKind, RunStatus
from app.apply import agent

if TYPE_CHECKING:
    from app.agents.runner import RunContext

NAME = "apply_answer"
SUBMIT = "submit_answers"
TOOLS = (SUBMIT,)
PROMPT_PATH = Path(__file__).parent / "prompts" / "answer.md"
MODEL = "claude-opus-5-5"
MODEL_API_KEY = "claude-sonnet-5-5"
# One submit, one correction if the tool refuses the first, one closing line.
MAX_TURNS = 3
# Inside the fill's lease: well under the browser's idle limit (BROWSER_IDLE_S, 300 s by default).
WALL_TIME_S = 180.0
MAX_QUESTIONS = 60
# What the prompt carries at most of each block, in characters.
POSTING_MAX = 12_000
LETTER_MAX = 6_000
PROFILE_MAX = 24_000
SUBMITTED_ONCE = "Your answers are already stored. Stop and answer with one short line."
STORED = "Stored. Stop and answer with one short line."


class AnswerInput(BaseModel):
    """The input of one run: a key of this process, the budget and the fake stream. No text."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: str = Field(pattern=r"^[0-9a-f]{32}$")
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


class SubmittedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    value: str | None = Field(default=None, max_length=2 * agent.VALUE_MAX)
    source: str = Field(default="", max_length=40)


class Submitted(BaseModel):
    """The arguments of `submit_answers`."""

    model_config = ConfigDict(extra="forbid")

    answers: list[SubmittedAnswer] = Field(max_length=MAX_QUESTIONS)


SUBMIT_SCHEMA = Submitted.model_json_schema()

# What lives in this process only while a run lasts: the request a run answers (by its key), and
# what the run submitted (by run id), taken once the run has ended.
_PENDING: dict[str, agent.Request] = {}
_SUBMITTED: dict[uuid.UUID, tuple[SubmittedAnswer, ...]] = {}
_LOCK = threading.Lock()


def question_ids(request: agent.Request) -> dict[str, str]:
    """`q1`, `q2`, ... in the order asked, to the field's key."""
    return {f"q{n}": q.key for n, q in enumerate(request.questions[:MAX_QUESTIONS], start=1)}


# The angle brackets and their lookalikes (fullwidth, small form), each as a JSON escape.
_BRACKETS = str.maketrans({c: f"\\u{ord(c):04x}" for c in "<>\uff1c\uff1e\ufe64\ufe65"})


def _block(tag: str, value: Any) -> str:
    """A JSON document between tags, every angle bracket and its lookalikes escaped (still valid
    JSON): nothing inside can close the block, open another, or look like a tag."""
    text = json.dumps(value, ensure_ascii=False, indent=1).translate(_BRACKETS)
    return f"<{tag}>\n{text}\n</{tag}>"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " [cut]"


def prompt_for(request: agent.Request) -> str:
    """The first message of a run: what to do in one line, then the four data blocks."""
    questions = [
        {
            "id": f"q{n}",
            "label": q.label,
            "kind": "choice" if q.options else "text",
            **({"options": list(q.options)} if q.options else {}),
            "required": q.required,
        }
        for n, q in enumerate(request.questions[:MAX_QUESTIONS], start=1)
    ]
    profile: Any = json.loads(json.dumps(dict(request.profile), ensure_ascii=False, default=str))
    flat = json.dumps(profile, ensure_ascii=False)
    if len(flat) > PROFILE_MAX:
        profile = _clip(flat, PROFILE_MAX)
    return "\n\n".join(
        (
            "Answer these questions of the application form, or leave them, then call "
            "submit_answers once.",
            _block("questions", questions),
            _block("profile", profile),
            _block(
                "posting",
                {
                    "title": request.posting_title,
                    "company": request.posting_company,
                    "text": _clip(request.posting_text, POSTING_MAX),
                },
            ),
            _block("letter", _clip(request.letter_text, LETTER_MAX)),
        )
    )


@dataclass(frozen=True)
class AnswerDefinition(definitions.AgentDefinition):
    """The definition, with its first message built from the request held in this process."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        with _LOCK:
            request = _PENDING.get(str(data.get("request")))
        if request is None:
            # A run nobody waits for (started otherwise, or its fill gone): no model call.
            raise LookupError("no pending answer request")
        return prompt_for(request)


def submit(run_id: uuid.UUID | None, raw: object) -> tuple[str, bool]:
    """What `submit_answers` does: the arguments checked for shape and kept for the run, once.
    Answers the tool's text and whether it is an error."""
    if run_id is None:
        return "This run cannot submit answers.", True
    try:
        parsed = Submitted.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "answers"
        return f"Not stored: {where}: {first.get('msg', 'invalid')}. Fix it and submit again.", True
    with _LOCK:
        if run_id in _SUBMITTED:
            return SUBMITTED_ONCE, True
        _SUBMITTED[run_id] = tuple(parsed.answers)
    return STORED, False


def take(run_id: uuid.UUID) -> tuple[SubmittedAnswer, ...]:
    """What the run submitted, forgotten here; nothing for a run that never submitted."""
    with _LOCK:
        return _SUBMITTED.pop(run_id, ())


def _definition() -> AnswerDefinition:
    return AnswerDefinition(
        name=NAME,
        description="Answers the open questions of one application form from the profile.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=TOOLS,
        input_model=AnswerInput,
        model=MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: MODEL_API_KEY},
        max_turns=MAX_TURNS,
        wall_time_s=WALL_TIME_S,
        effort="medium",
        internal=True,
        live_text=False,
        private_io=True,
    )


APPLY_ANSWER = definitions.register(_definition())


def _in_db[T](sessions: sessionmaker[Session], step: Callable[[Session], T]) -> T:
    with sessions() as db:
        try:
            out = step(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return out


@dataclass(frozen=True)
class _Ended:
    status: RunStatus | None
    error: str | None
    input_tokens: int
    output_tokens: int


@dataclass
class RuntimeAnswerAgent:
    """`agent.AnswerAgent` on the agents runtime: one `apply_answer` run per call, executed in
    this process. `context` builds the runner's context (app/worker.py `agent_context`); `script`
    names the fake backend's stream (tests; the default stream is the agent's name)."""

    context: Callable[[], RunContext]
    script: str | None = None

    async def __call__(self, request: agent.Request) -> agent.Reply:
        from app.agents import runner  # the SDK: the worker's image only

        context = self.context()
        key = secrets.token_hex(16)
        user_id = uuid.UUID(request.user_id)
        data = AnswerInput(
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

        creating = asyncio.ensure_future(asyncio.to_thread(_in_db, context.sessions, create))
        try:
            created = await asyncio.shield(creating)
        except asyncio.CancelledError:
            # The row may be written while the fill is cancelled: wait for it, then fail it.
            with contextlib.suppress(Exception):
                made = await asyncio.shield(creating)
                if isinstance(made, uuid.UUID):
                    await _release(context, made)
            raise
        if isinstance(created, str):
            return agent.Reply(stopped=created)
        with _LOCK:
            _PENDING[key] = request
        try:
            await runner.execute(context, created)
        except asyncio.CancelledError:
            # Cancelled before the runner claimed the run: it would stay queued, holding one of
            # the person's run slots for AGENT_STALE_QUEUED. Once claimed, the runner ends it.
            await _release(context, created)
            raise
        finally:
            with _LOCK:
                _PENDING.pop(key, None)
            submitted = take(created)
        ended = await asyncio.to_thread(_in_db, context.sessions, lambda db: _ended(db, created))
        return agent.Reply(
            proposals=_proposals(request, submitted),
            input_tokens=ended.input_tokens,
            output_tokens=ended.output_tokens,
            stopped=_stopped(ended),
        )


async def _release(context: RunContext, run_id: uuid.UUID) -> None:
    """A run still queued when its fill is cancelled: failed, shielded from the cancellation (the
    `sweep.run_sweep` pattern). A run the runner claimed is left to the runner."""

    def fail(db: Session) -> bool:
        return runs.fail_queued(db, run_id, runs.STOPPED, context.clock())

    await asyncio.shield(asyncio.to_thread(_in_db, context.sessions, fail))


def _ended(db: Session, run_id: uuid.UUID) -> _Ended:
    row = db.execute(
        sa.select(
            AgentRun.status, AgentRun.error, AgentRun.input_tokens, AgentRun.output_tokens
        ).where(AgentRun.id == run_id)
    ).one_or_none()
    if row is None:
        return _Ended(None, None, 0, 0)
    return _Ended(row.status, row.error, row.input_tokens or 0, row.output_tokens or 0)


def _stopped(ended: _Ended) -> str:
    if ended.status is RunStatus.done:
        return ""
    if ended.error == TOKEN_LIMIT:
        return "token_budget"
    return "cancelled" if ended.status is RunStatus.cancelled else "error"


def _proposals(
    request: agent.Request, submitted: tuple[SubmittedAnswer, ...]
) -> tuple[agent.Proposal, ...]:
    """The submitted entries on the fields' keys; an id that was not asked is dropped."""
    keys: Mapping[str, str] = question_ids(request)
    return tuple(
        agent.Proposal(keys[one.question], one.value, one.source)
        for one in submitted
        if one.question in keys
    )


if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tool: importing it registers it with the runner. The api image
    # holds no Claude Agent SDK and never runs this agent.
    from app.apply import answer_tools as _tools  # noqa: F401

from app.apply import form_agent as _form_agent  # noqa: E402, F401  (A8's agent)
from app.apply import model_check as _model_check  # noqa: E402, F401  (A13c's agent)
