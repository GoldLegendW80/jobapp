"""The job titles agent on the agents runtime, `profile_titles` (SPEC-profile, "Job title
suggestions"; what it reads and how its answer is checked: titles.py).

It asks of the runner (app/agents/runner.py, "What a definition can ask of a run"):
- `internal`: started by this module only (`titles.start`, from the "More ideas" route or the
  tick); POST /api/agents/runs refuses it and GET /api/agents does not list it;
- `private_io`, `live_text=False`: the events hold the tool's name and sizes, never a title;
- `model_by_credential`: the documents writers' model for each kind of credential,
  MAX_TURNS turns, WALL_TIME_S, and `titles.TOKEN_BUDGET` in the run's input;
- one tool, `submit_titles` (titles_tools.py): the list, checked for shape and kept for the run
  (`submit`), read back here once the run has ended (`take`) and judged by `titles.check`.

The prompt holds the person's profile summary, so the run is executed in the worker by
`execute`, the job JOB of the agents queue (one of its MAX_AGENT_RUNS slots), never through
`run_agent`: the request is built from the stored profile in a thread, held in this process
under the random key of the run's input while the run lasts (the pattern of apply's answer
agent), and the run's input holds that key and the budget only. A run started any other way
finds no request and fails before Claude Code is started.

`tick` (a cron of the agents queue, every minute) starts the automatic runs (`titles.due`).
Importing this module registers the agent, and its tool where the Claude Agent SDK is installed
(the worker). It is a registration module (`agents`, app/worker.py `REGISTRATION_MODULES`).
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import definitions
from app.agents.models import CredentialKind
from app.profile import titles

if TYPE_CHECKING:
    from datetime import datetime

    from app.agents.runner import RunContext

logger = logging.getLogger(__name__)

JOB = "profile_titles"
SUBMIT = "submit_titles"
TOOLS = (SUBMIT,)
STORED = "Stored. Stop and answer with one short line."
SUBMITTED_ONCE = "Your titles are already stored. Stop and answer with one short line."
NO_RUN = "This run cannot submit titles."


def job_id(run_id: object) -> str:
    return f"profile-titles:{run_id}"


class TitlesInput(BaseModel):
    """The input of one run: a key of the worker's memory, the budget and the fake stream."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: str = Field(pattern=r"^[0-9a-f]{32}$")
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


SUBMIT_SCHEMA = titles.SubmittedTitles.model_json_schema()

# What lives in this process only while a run lasts: the request of a run (by its key) and what
# the run submitted (by run id), taken once the run has ended.
_PENDING: dict[str, titles.Request] = {}
_SUBMITTED: dict[uuid.UUID, tuple[titles.Submitted, ...]] = {}
_LOCK = threading.Lock()


@dataclass(frozen=True)
class TitlesDefinition(definitions.AgentDefinition):
    """The definition, with its first message built from the request held in this process."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        with _LOCK:
            request = _PENDING.get(str(data.get("request")))
        if request is None:
            raise LookupError("no pending titles request")  # nobody waits: no model call
        return titles.prompt_for(request)


def submit(run_id: uuid.UUID | None, raw: object) -> tuple[str, bool]:
    """What `submit_titles` does: the list checked for shape and kept for the run, once. Answers
    the tool's text and whether it is an error."""
    if run_id is None:
        return NO_RUN, True
    try:
        parsed = titles.SubmittedTitles.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "titles"
        return f"Not stored: {where}: {first.get('msg', 'invalid')}. Fix it and submit again.", True
    with _LOCK:
        if run_id in _SUBMITTED:
            return SUBMITTED_ONCE, True
        _SUBMITTED[run_id] = tuple(parsed.titles)
    return STORED, False


def take(run_id: uuid.UUID) -> tuple[titles.Submitted, ...]:
    with _LOCK:
        return _SUBMITTED.pop(run_id, ())


def _definition() -> TitlesDefinition:
    return TitlesDefinition(
        name=titles.NAME,
        description="Suggests job titles that fit your profile.",
        system_prompt=titles.PROMPT_PATH.read_text(encoding="utf-8"),
        tools=TOOLS,
        input_model=TitlesInput,
        model=titles.MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: titles.MODEL_API_KEY},
        max_turns=titles.MAX_TURNS,
        wall_time_s=titles.WALL_TIME_S,
        effort="low",
        internal=True,
        live_text=False,
        private_io=True,
    )


PROFILE_TITLES = definitions.register(_definition())


def _in_db[T](sessions: sessionmaker[Session], step: Callable[[Session], T]) -> T:
    with sessions() as db:
        try:
            out = step(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return out


async def execute(context: RunContext, run_id: uuid.UUID) -> None:
    """The job JOB: run one queued titles run in this process and record its end."""
    from app.agents import runner  # the SDK: the worker's image only
    from app.apply.agents import _release

    found = await asyncio.to_thread(
        _in_db, context.sessions, lambda db: titles.run_request(db, run_id)
    )
    if found is None:
        # Nothing waits for it, or its person's search can no longer be read: no model call.
        await _release(context, run_id)
        await asyncio.to_thread(
            _in_db, context.sessions, lambda db: titles.finish(db, run_id, None, (), clock(context))
        )
        return
    key, request = found
    with _LOCK:
        _PENDING[key] = request
    try:
        await runner.execute(context, run_id)
    except asyncio.CancelledError:
        await _release(context, run_id)
        raise
    finally:
        with _LOCK:
            _PENDING.pop(key, None)
        submitted = take(run_id)
    await asyncio.to_thread(
        _in_db,
        context.sessions,
        lambda db: titles.finish(db, run_id, request, submitted, clock(context)),
    )


def clock(context: RunContext) -> datetime:
    return context.clock()


async def enqueue(redis: Any, run_id: uuid.UUID) -> None:
    """Queue the job of a committed run (an ArqRedis pool, the worker's)."""
    await redis.enqueue_job(JOB, str(run_id), _job_id=job_id(run_id), _queue_name=queues.AGENTS)


async def tick(sessions: sessionmaker[Session], now: datetime, redis: Any) -> int:
    """Start the automatic run of everyone `titles.due` names; answers how many started."""
    people = await asyncio.to_thread(_in_db, sessions, lambda db: titles.due(db, now))
    started = 0
    for person in people:

        def begin(db: Session, person: uuid.UUID = person) -> uuid.UUID | None:
            try:
                made = titles.start(db, person, now, auto=True)
            except titles.RefusedError as refused:
                if refused.code in titles.SETTLED:
                    titles.seen(db, person, now)
                return None
            titles.seen(db, person, now)
            return made

        run_id = await asyncio.to_thread(_in_db, sessions, begin)
        if run_id is None:
            continue
        try:
            await enqueue(redis, run_id)
        except Exception as error:  # Redis gone between the commit and the queue
            logger.warning("profile titles run not queued: error=%s", type(error).__name__)
            await asyncio.to_thread(
                _in_db, sessions, lambda db, r=run_id, p=person: titles.not_queued(db, p, r, now)
            )
            continue
        started += 1
    return started


if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tool: importing it registers it with the runner.
    from app.profile import titles_tools as _tools  # noqa: F401
