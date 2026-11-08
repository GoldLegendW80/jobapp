"""The follow up writer on the agents runtime, `apply_followup` (SPEC-apply.md, "Follow ups"):
one short mail to the company of a posting the person applied to, written on the person's click
only (D29, `followup_service.start_draft`), run by the agents queue's `run_agent` job.

It asks of the runner (app/agents/runner.py):
- `internal`: started by this module only; POST /api/agents/runs refuses it;
- bound (`bind`): the run works on the posting's open draft that names it, of its person, for
  its job; anything else and the run fails before any model call;
- `private_io`, `live_text=False`: what its tools read and write is never stored in the run;
- `model_by_credential`, MAX_TURNS, WALL_TIME_S, and TOKEN_BUDGET in its input;
- two tools (followup_tools.py, worker only): `read_follow_up`, the data it writes from
  (`context`), and `submit_follow_up` (`submit`): the dash rule by code, the shape (a subject,
  at most followups.WORDS_MAX words), then mail M9's fact check (`followup_service.issues`). Its
  first findings go back to the agent once to fix; a text that still has some is stored with
  them, shown to the person, never sent by anyone but the person;
- `on_final`: the run's end on the draft (`finish`), why it gave no text when it gave none.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions
from app.agents.failures import TOKEN_LIMIT
from app.agents.models import AgentRun, CredentialKind, RunStatus
from app.apply import followups
from app.apply.followup_models import FollowUp
from app.profile import service as profile_service

NAME = "apply_followup"
MODEL = "claude-opus-5-5"  # on a subscription, as the documents writers
READ = "read_follow_up"
SUBMIT = "submit_follow_up"
TOOLS = (READ, SUBMIT)
PROMPT_PATH = Path(__file__).parent / "prompts" / "followup.md"
MODEL_API_KEY = "claude-sonnet-5-5"
MAX_TURNS = 6  # one read, a submit, a fixed submit, closing lines
WALL_TIME_S = 300.0
POSTING_MAX = 8000
LETTER_MAX = 6000
FIRST_MESSAGE = "Write the follow up of this application: read it with read_follow_up first."

STORED = "Stored. Stop and answer with one short line."
NO_RUN = "This run cannot store a follow up."
GONE = "This draft is no longer waiting for you. Stop and answer with one short line."
# What the person reads when the last click gave no text.
ERRORS = {
    "failed": "The draft could not be written. Try again in a minute.",
    "no_answer": "The assistant gave no draft this time. Try again.",
    "token_budget": "The draft stopped at its token limit. Try again.",
    "queue_down": "The assistant is unavailable. Try again in a minute.",
}
# The profile the writer reads: no contact detail, no search settings.
SECTIONS = ("identity", "experience", "facts", "skills", "answers")
IDENTITY = ("full_name", "headline", "city", "country", "languages")


class FollowUpInput(BaseModel):
    """The input of one run: ids and numbers only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    follow_up_id: uuid.UUID = Field(strict=False)
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


class Submitted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(max_length=2000)
    body: str = Field(max_length=20_000)


SUBMIT_SCHEMA = Submitted.model_json_schema()

# The runs whose fact check findings went back to the agent once, and the runs that stored a
# text: this process's, while their run lasts (the tools and the hook run in the same job).
_WARNED: set[uuid.UUID] = set()
_STORED: set[uuid.UUID] = set()
_LOCK = threading.Lock()


def store() -> Any:
    # Imported when a run needs it: the store reaches apply modules that import this registry.
    from app.apply import followup_service

    return followup_service


def bind(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """The open draft that names this run, of its person, for the run's job; else None."""
    try:
        wanted = uuid.UUID(str(run.input.get("follow_up_id")))
    except ValueError:
        return None
    row = db.get(FollowUp, wanted)
    if (
        row is None
        or row.user_id != run.user_id
        or row.run_id != run.id
        or row.sent_on is not None
        or run.job_id != row.posting_id
    ):
        return None
    return {"follow_up_id": str(row.id), "posting_id": str(row.posting_id)}


def _profile(db: Session, user_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    sections = profile_service.sections_view(profile_service.get_profile(db, user_id, now))
    shown = {name: sections.get(name) for name in SECTIONS if sections.get(name)}
    if isinstance(shown.get("identity"), Mapping):
        shown["identity"] = {k: v for k, v in shown["identity"].items() if k in IDENTITY}
    return shown


def context(db: Session, user_id: uuid.UUID, binding: Mapping[str, Any], now: datetime) -> str:
    """What read_follow_up answers, as JSON: the task, the dates, the earlier follow ups, the
    posting, the letter that was sent and the profile."""
    service = store()
    posting_id = uuid.UUID(str(binding["posting_id"]))
    found = service.entry(db, user_id, posting_id, now)
    posting = found.posting
    earlier = db.scalars(
        sa.select(FollowUp)
        .where(
            FollowUp.user_id == user_id,
            FollowUp.posting_id == posting_id,
            FollowUp.sent_on.is_not(None),
        )
        .order_by(FollowUp.sent_on)
    )
    data = {
        "task": found.cadence.kind,
        "number": found.cadence.count + 1,
        "applied_on": found.applied_on.isoformat(),
        "follow_ups_sent": [
            {"sent_on": row.sent_on.isoformat() if row.sent_on else None, "text": row.body}
            for row in earlier
        ],
        "posting": {
            "title": posting.title,
            "company": posting.company,
            "location": posting.location,
            "text": (posting.posting_text or posting.description or "")[:POSTING_MAX],
        },
        "cover_letter_sent": service.letter_text(db, user_id, posting_id)[:LETTER_MAX],
        "person_profile": _profile(db, user_id, now),
    }
    return json.dumps(data, ensure_ascii=False, default=str)


def _problems(code: str) -> str:
    return {
        "subject": f"Not stored: the subject must be one line of at most {followups.SUBJECT_MAX} "
        "characters. Fix it and submit again.",
        "empty": "Not stored: the body is empty. Write it and submit again.",
        "too_long": f"Not stored: the body has more than {followups.WORDS_MAX} words. Shorten it "
        "and submit again.",
    }[code]


def submit(
    db: Session,
    user_id: uuid.UUID,
    run_id: uuid.UUID | None,
    binding: Mapping[str, Any] | None,
    raw: object,
    now: datetime,
) -> tuple[str, bool]:
    """What submit_follow_up does; answers the tool's text and whether it is an error. The
    caller commits."""
    if run_id is None or binding is None:
        return NO_RUN, True
    service = store()
    try:
        parsed = Submitted.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "input"
        return f"Not stored: {where}: {first.get('msg', 'invalid')}. Fix it and submit again.", True
    subject = followups.undash(parsed.subject.strip())
    body = followups.undash(parsed.body.strip())
    code = followups.check_text(subject, body)
    if code is not None:
        return _problems(code), True
    row = db.scalar(
        sa.select(FollowUp)
        .where(
            FollowUp.id == uuid.UUID(str(binding["follow_up_id"])),
            FollowUp.user_id == user_id,
            FollowUp.run_id == run_id,
            FollowUp.sent_on.is_(None),
        )
        .with_for_update()
    )
    if row is None:
        return GONE, True
    try:
        found = service.entry(db, user_id, row.posting_id, now)
    except service.RefusedError:
        return GONE, True  # the posting left applied and interview meanwhile
    issues = service.issues(db, user_id, found, body, now)
    with _LOCK:
        warn = bool(issues) and run_id not in _WARNED
        if warn:
            _WARNED.add(run_id)
    if warn:
        quoted = "; ".join(f'"{body[i["start"] : i["end"]]}" ({i["code"]})' for i in issues)
        return (
            "Not stored: no source states these claims: "
            f"{quoted}. Remove or fix exactly those and submit again.",
            True,
        )
    row.subject, row.body, row.issues = subject, body, issues
    row.written_by, row.error, row.updated_at = "agent", None, now
    with _LOCK:
        _STORED.add(run_id)
    return STORED, False


def finish(db: Session, run_id: uuid.UUID, status: RunStatus, error: str | None) -> None:
    """The end of a run on the draft that waited for it: why it gave no text, when it gave
    none. A draft that asks for another run since is left alone. The caller commits."""
    with _LOCK:
        stored = run_id in _STORED
        _STORED.discard(run_id)
        _WARNED.discard(run_id)
    row = db.scalar(sa.select(FollowUp).where(FollowUp.run_id == run_id).with_for_update())
    if row is None:
        return
    row.run_id = None
    if stored:
        return
    if status is RunStatus.done:
        row.error = "no_answer"
    elif error == TOKEN_LIMIT:
        row.error = "token_budget"
    else:
        row.error = "failed"


def _in_db[T](sessions: sessionmaker[Session], step: Callable[[Session], T]) -> T:
    with sessions() as db:
        try:
            out = step(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return out


async def on_final(final: definitions.FinalRun) -> None:
    def step(db: Session) -> None:
        error = db.scalar(sa.select(AgentRun.error).where(AgentRun.id == final.run_id))
        finish(db, final.run_id, final.status, error)

    await asyncio.to_thread(_in_db, final.sessions, step)


@dataclass(frozen=True)
class FollowUpDefinition(definitions.AgentDefinition):
    """The definition, with its fixed first message and the run's job id."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        text = FIRST_MESSAGE
        if job_id is not None:
            text += f"\n\nJob id: {job_id}"
        return text


APPLY_FOLLOWUP = definitions.register(
    FollowUpDefinition(
        name=NAME,
        description="Writes a short follow up mail for an application, which the person sends.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=TOOLS,
        input_model=FollowUpInput,
        model=MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: MODEL_API_KEY},
        max_turns=MAX_TURNS,
        wall_time_s=WALL_TIME_S,
        effort="low",
        internal=True,
        bind_job=True,
        live_text=False,
        bind=bind,
        private_io=True,
        on_final=on_final,
    )
)

if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools: importing them registers them with the runner.
    from app.apply import followup_tools as _tools  # noqa: F401
