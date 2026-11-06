"""The agent that reads a part of the "About you" document, `profile_interpret` (SPEC-profile,
"About you"; what it reads and how its answer is checked: about.py and about_text.py).

It asks of the runner what the job titles agent asks (app/profile/agents.py):
- `internal`: started by this module only (`about.start`, from Done); POST /api/agents/runs
  refuses it and GET /api/agents does not list it;
- `private_io`, `live_text=False`: the events hold the tool's name and sizes, never the text;
- `model_by_credential`: the documents writers' model for each kind of credential,
  MAX_TURNS turns, WALL_TIME_S, and the ceiling of `about.estimate` for the run's first message
  in the run's input;
- one tool, `submit_interpretation` (about_tools.py): the reading, checked for shape and kept
  for the run (`submit`), read back here once the run has ended (`take`) and judged by
  `about_text.interpret`.

The run is executed in the worker by `execute`, the job JOB of the agents queue: the request is
built from the stored document and profile in a thread, held in this process under the random
key of the run's input while the run lasts, and the input holds that key and the budget only. A
run started any other way finds no request and fails before Claude Code is started. Importing
this module registers the agent (app/profile/agents.py imports it, a registration module), and
its tool where the Claude Agent SDK is installed.
"""

from __future__ import annotations

import asyncio
import importlib.util
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
from app.profile import about
from app.profile.about_text import Interpretation

if TYPE_CHECKING:
    from app.agents.runner import RunContext

JOB = "profile_interpret"
SUBMIT = "submit_interpretation"
TOOLS = (SUBMIT,)
STORED = "Stored. Stop and answer with one short line."
SUBMITTED_ONCE = "Your reading is already stored. Stop and answer with one short line."
NO_RUN = "This run cannot submit a reading."


def job_id(run_id: object) -> str:
    return f"profile-interpret:{run_id}"


class InterpretInput(BaseModel):
    """The input of one run: a key of the worker's memory, the budget and the fake stream."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: str = Field(pattern=r"^[0-9a-f]{32}$")
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


SUBMIT_SCHEMA = Interpretation.model_json_schema()

_PENDING: dict[str, about.Request] = {}
_SUBMITTED: dict[uuid.UUID, Interpretation] = {}
_LOCK = threading.Lock()


@dataclass(frozen=True)
class InterpretDefinition(definitions.AgentDefinition):
    """The definition, with its first message built from the request held in this process."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        with _LOCK:
            request = _PENDING.get(str(data.get("request")))
        if request is None:
            raise LookupError("no pending reading")  # nobody waits: no model call
        return about.prompt_for(request)


def submit(run_id: uuid.UUID | None, raw: object) -> tuple[str, bool]:
    """What `submit_interpretation` does: the reading checked for shape and kept, once."""
    if run_id is None:
        return NO_RUN, True
    try:
        parsed = Interpretation.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "reading"
        return f"Not stored: {where}: {first.get('msg', 'invalid')}. Fix it and submit again.", True
    with _LOCK:
        if run_id in _SUBMITTED:
            return SUBMITTED_ONCE, True
        _SUBMITTED[run_id] = parsed
    return STORED, False


def take(run_id: uuid.UUID) -> Interpretation | None:
    with _LOCK:
        return _SUBMITTED.pop(run_id, None)


PROFILE_INTERPRET = definitions.register(
    InterpretDefinition(
        name=about.NAME,
        description="Reads a part of your About you document into your profile's fields.",
        system_prompt=about.PROMPT_PATH.read_text(encoding="utf-8"),
        tools=TOOLS,
        input_model=InterpretInput,
        model=about.MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: about.MODEL_API_KEY},
        max_turns=about.MAX_TURNS,
        wall_time_s=about.WALL_TIME_S,
        effort="low",
        internal=True,
        live_text=False,
        private_io=True,
    )
)


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
    """The job JOB: run one queued reading in this process and record its end."""
    from app.agents import runner  # the SDK: the worker's image only
    from app.apply.agents import _release

    now = context.clock()
    found = await asyncio.to_thread(
        _in_db, context.sessions, lambda db: about.run_request(db, run_id, now)
    )
    if found is None:
        await _release(context, run_id)
        await asyncio.to_thread(
            _in_db, context.sessions, lambda db: about.finish(db, run_id, None, None, now)
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
        lambda db: about.finish(db, run_id, request, submitted, context.clock()),
    )


async def enqueue(redis: Any, run_id: uuid.UUID) -> None:
    await redis.enqueue_job(JOB, str(run_id), _job_id=job_id(run_id), _queue_name=queues.AGENTS)


if importlib.util.find_spec("claude_agent_sdk") is not None:
    from app.profile import about_tools as _tools  # noqa: F401
