"""The form agent of the generic strategy, `apply_form` (apply A8; SPEC-apply, "Per ATS strategies",
the last row, and "Cost per application"): it reads a form no adapter knows, fills it from the
profile, moves through its steps and names its submit element. It asks of the runner
(app/agents/runner.py):

- `internal`: started by the apply run only, through `RuntimeFormAgent`; never listed or started
  through the api;
- `private_io`, `live_text=False`: the run's events hold tool names and sizes, nothing it read;
- at most `MAX_TURNS` (30) turns, and `token_budget` (`TOKEN_BUDGET`, twice the spec's estimate
  of 410,000 tokens) written in the run's input; a run that reaches either is stopped by the
  runner, and what it filled before stays, with no designation;
- five tools (app/apply/form_tools.py), bound to the run (`Scope.run_id`) and through it to the
  fill's lease, held here in `_SESSIONS` while the run lasts, and browser B12b's
  `browser_request_signin` (app/browser/signin_tool.py), lent the same lease. No tool takes a
  value: the model names elements by ref and code does the rest.
  * `read_form`: the fields (ref, label, kind, options, required, filled or not, never a value),
    the buttons (ref, name) and the page's text, as data;
  * `resolve_answer(ref)`: `answers.resolve` on that field: whether the profile answers it and from
    which source, never the value;
  * `fill_field(ref)`: that field filled by code with the resolver's answer (`Adapter.fill`); a
    field the resolver leaves stays for the person;
  * `next_step(ref)`: a button named as a step (`generic.NEXT_NAMES`) clicked through the tool
    layer, which refuses a submit; the step and its fields recorded for the refill;
  * `designate_submit(ref)`: the designation, accepted only when `generic.check_designation` and
    the detector (`generic.marked`) agree.
  * `browser_request_signin(site, reason)`: only after a read saw a sign in wall; the person
    signs in in the live view and the run reads the form again, or, nobody watching, a Cancel
    or the deadline, the fill stops there as `needs_person: signin` without reading the page.
    The run's wall time holds that wait (`SIGNIN_WAIT_S`).
  There is no click on anything else, no typing of the model's text, no navigation and no mail:
  a page that asks the assistant to do something else gets nothing done, and a page that names
  an AI reader is reported (`generic.addressed`).

What the run filled, its steps and its designation are read back by code once it has ended
(`finish`): the last step's fields as the page shows them, the designated element checked again
there. No model clicks Submit: the gate (app/apply/gate.py) does, after the person's approval.
Nothing of a value, a page or a token goes into a log or the run's events.

Importing this module registers the agent (app/apply/agents.py imports it), and its tools where
the Claude Agent SDK is installed (the worker).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import secrets
import threading
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents import credentials, definitions, runs
from app.agents.failures import TOKEN_LIMIT, TURN_LIMIT
from app.agents.models import CredentialKind, RunStatus
from app.apply import agents as answer_agents
from app.apply import answers
from app.apply.ats import base as ats_base
from app.apply.ats import generic
from app.config import Settings

if TYPE_CHECKING:
    from app.agents.runner import RunContext

NAME = "apply_form"
SIGNIN = "browser_request_signin"  # app/browser/signin_tool.py's TOOL
TOOLS = ("read_form", "resolve_answer", "fill_field", "next_step", "designate_submit", SIGNIN)
PROMPT_PATH = Path(__file__).parent / "prompts" / "form.md"
MODEL = answer_agents.MODEL
MODEL_API_KEY = answer_agents.MODEL_API_KEY
MAX_TURNS = 30
TOKEN_BUDGET = 820_000
# Inside the fill's lease, whose tool calls are paced (browser B5): a form of 30 fields and 3 steps.
WALL_TIME_S = 900.0
# A sign in hand off's wait, which the lease does not count as active time (browser B12a).
SIGNIN_WAIT_S = float(Settings.model_fields["browser_session_max_s"].default)
# What the run keeps after a hand off's wait, to read the form again or end its turn.
SIGNIN_MARGIN_S = 60.0
WALL = (
    "A sign in or sign up wall is shown. Call browser_request_signin with the site's name and "
    "why, then read the form again."
)
SIGNIN_OVER = "The person was not signed in. Stop now."
TEXT_MAX = 4_000
BUTTONS_MAX = 40
FIRST_MESSAGE = (
    "Fill the application form open in the browser from the profile, step by step, then "
    "designate its submit element. Start with read_form."
)
_TURN_LIMIT_HEAD = TURN_LIMIT.split("{", 1)[0]


class FormInput(BaseModel):
    """The input of one run: a key of this process, the budget and the fake stream. No text."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: str = Field(pattern=r"^[0-9a-f]{32}$")
    token_budget: int = Field(ge=0)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


@dataclass
class FormReport(ats_base.Report):
    """A fill by the form agent: the base report, the designation and the steps."""

    designated: str = ""  # the submit's name as the page shows it
    designated_ref: str = ""  # its ref on the last read
    steps: list[str] = field(default_factory=list)  # the Next names clicked, in order
    refused: dict[str, int] = field(default_factory=dict)  # codes of what code or the tools refused
    addressed: bool = False  # the page named an AI reader


@dataclass
class FormSession:
    """One fill's form, as the tools of one run see it."""

    adapter: generic.Generic
    tools: Any
    context: answers.Context
    user_id: str
    token_budget: int = TOKEN_BUDGET
    outcomes: dict[str, ats_base.Outcome] = field(default_factory=dict)
    closed: list[ats_base.Outcome] = field(default_factory=list)  # fields of earlier steps
    skipped: list[ats_base.Skipped] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    designated: str = ""
    refused: Counter[str] = field(default_factory=Counter)
    stopped: str = ""
    pending: str = ""
    addressed: bool = False
    signin_over: bool = False  # a hand off that ended without the person signed in
    deadline: float | None = None  # monotonic: the end of the run's wall time

    async def _read(self) -> tuple[ats_base.Discovery | None, str]:
        if self.signin_over:
            return None, SIGNIN_OVER
        found, code = await self.adapter.read(self.tools)
        if found is None:
            return None, f"The page could not be read ({code})."
        if found.pending:
            self.pending = found.pending
            if found.pending == "signin":
                return None, WALL
            return None, "A step only the person can take is shown. Stop now."
        if found.root is not None and generic.addressed(" ".join(found.root.words())):
            self.addressed = True
        return found, ""

    def _refuse(self, code: str, text: str) -> tuple[str, bool]:
        self.refused[code] += 1
        return text, True

    async def read_form(self) -> tuple[str, bool]:
        found, problem = await self._read()
        if found is None or found.root is None:
            return problem or "The page could not be read.", True
        fields = [
            {
                "ref": f.ref,
                "label": f.label,
                "kind": f.kind,
                "required": f.required,
                **({"options": list(f.options)} if f.options else {}),
                "filled": bool(f.value or f.checked),
            }
            for f in found.fields
        ]
        buttons = [
            {"ref": n.ref, "name": n.name}
            for n in found.root.walk()
            if n.role == "button" and n.ref and n.name
        ][:BUTTONS_MAX]
        text = [
            n.name or n.text
            for n in found.root.walk()
            if n.role in ("heading", "paragraph", "text", "alert", "link") and (n.name or n.text)
        ]
        view = {
            "step": self.adapter.step + 1,
            "fields": fields,
            "buttons": buttons,
            "untrusted_page_text": " | ".join(text)[:TEXT_MAX],
        }
        return json.dumps(view, ensure_ascii=False), False

    async def _field(self, ref: str) -> tuple[ats_base.Field | None, str]:
        found, problem = await self._read()
        if found is None:
            return None, problem
        item = next((f for f in found.fields if f.ref == ref), None)
        return item, "" if item is not None else "No field has this ref. Read the form again."

    async def resolve_answer(self, ref: str) -> tuple[str, bool]:
        item, problem = await self._field(ref)
        if item is None:
            return problem, True
        answer = answers.resolve(item.form_field(), self.context)
        if isinstance(answer, answers.Left):
            return json.dumps(
                {"label": item.label, "answer": "left", "reason": answer.reason}
            ), False
        return json.dumps(
            {"label": item.label, "answer": "profile", "source": answer.source}
        ), False

    async def fill_field(self, ref: str) -> tuple[str, bool]:
        if self.stopped:
            return "The fill has stopped. Stop now.", True
        item, problem = await self._field(ref)
        if item is None:
            return problem, True
        answer = answers.resolve(item.form_field(), self.context)
        filled = await self.adapter.fill(self.tools, item, answer)
        self.outcomes[item.key] = ats_base.Outcome(item, answer, filled)
        if filled.status == "failed" and filled.code in ats_base.PENDING_STEPS:
            self.pending = filled.code
            return "A step only the person can take is shown. Stop now.", True
        if filled.status == "failed" and filled.code in ats_base.STOPPING:
            self.stopped = filled.code
            self.refused[filled.code] += 1
            return f"Stopped: {filled.code}. Stop now.", True
        return json.dumps(
            {"label": item.label, "status": filled.status, "code": filled.code}
        ), False

    async def next_step(self, ref: str) -> tuple[str, bool]:
        found, problem = await self._read()
        if found is None or found.root is None:
            return problem or "The page could not be read.", True
        node = next((n for n in found.root.walk() if n.ref == ref), None)
        if node is None or node.role != "button":
            return self._refuse("not_a_button", "Not a button. Read the form again.")
        name = generic.button_name(node.name)
        if name in generic.SUBMIT_NAMES:
            return self._refuse(
                "submit", "This is the submit: never click it. Designate it with designate_submit."
            )
        if name not in generic.NEXT_NAMES:
            return self._refuse("not_a_step", "Only a Next or Continue button can be clicked.")
        if len(self.steps) >= generic.STEPS_MAX:
            return self._refuse("too_many_steps", "Too many steps. Stop now.")
        clicked = await self.tools.click(ref, node.name)
        if not clicked.get("ok"):
            code = str(clicked.get("error") or "failed")
            return self._refuse(code, f"Refused by the browser: {code}. Stop clicking it.")
        self.closed += [self._outcome(f) for f in found.fields]
        self.skipped += found.skipped
        self.steps.append(node.name)
        self.adapter.step += 1
        return "The next step is shown. Read the form again.", False

    def _outcome(self, item: ats_base.Field) -> ats_base.Outcome:
        done = self.outcomes.get(item.key)
        if done is not None:
            return ats_base.Outcome(item, done.answer, done.filled)
        left = answers.Left("not_filled", item.options, item.required)
        return ats_base.Outcome(item, left, ats_base.Filled("left", "not_filled"))

    async def designate_submit(self, ref: str) -> tuple[str, bool]:
        found, problem = await self._read()
        if found is None or found.root is None:
            return problem or "The page could not be read.", True
        self.designated = ""
        code = generic.check_designation(found.root, ref)
        node = next((n for n in found.root.walk() if n.ref == ref), None)
        if not code and node is not None and not await generic.marked(self.tools, ref, node.name):
            code = "not_marked"
        if code or node is None:
            return self._refuse(f"designation_{code}", f"Refused: {code}. The person will do it.")
        self.designated = node.name
        return "Designated. The person approves it before anything is sent. Stop now.", False

    def signin_wall(self) -> bool:
        return self.pending == "signin" and not self.signin_over

    def signin_budget(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic() - SIGNIN_MARGIN_S)

    def signin_ended(self, outcome: Any) -> None:
        """The end of a hand off (app.browser.handoff.Outcome): signed in, the form is read
        again; else the lease is gone or still waits for the person, and nothing reads it."""
        if outcome.code == "signed_in":
            self.pending = ""
        elif outcome.needs_person or outcome.code == "ended":
            self.signin_over = True

    async def finish(self) -> FormReport:
        """What the run left on the page, read by code: the outcomes of every step, the last
        step's fields as shown now, the designation checked again on this read."""
        report = FormReport(
            steps=list(self.steps), refused=dict(self.refused), addressed=self.addressed
        )
        if self.signin_over:
            report.outcomes, report.skipped = list(self.closed), list(self.skipped)
            report.stopped, report.needs_person = "needs_person", "signin"
            return report
        found, code = await self.adapter.read(self.tools)
        report.outcomes = list(self.closed)
        report.skipped = list(self.skipped)
        if found is None:
            report.stopped = code
            return report
        report.outcomes += [self._outcome(f) for f in found.fields]
        report.skipped += found.skipped
        if self.pending or found.pending:
            report.stopped, report.needs_person = "needs_person", self.pending or found.pending
            return report
        if self.stopped:
            report.stopped = self.stopped
            return report
        if self.designated and found.root is not None:
            wanted = ats_base.fold(self.designated)
            refs = [
                n.ref
                for n in found.root.walk()
                if n.role == "button" and ats_base.fold(n.name) == wanted
            ]
            if len(refs) == 1 and not generic.check_designation(found.root, refs[0]):
                report.designated, report.designated_ref = self.designated, refs[0]
            else:
                report.refused["designation_changed"] = 1
        return report


@dataclass(frozen=True)
class Ran:
    """How a run of the form agent ended, and what it cost."""

    input_tokens: int = 0
    output_tokens: int = 0
    stopped: str = ""  # "", no_credential, busy, token_budget, turn_limit, cancelled, error


FormAgent = Callable[[FormSession], Awaitable[Ran]]

# The sessions of the runs that live in this process, by run id, and the keys of their inputs.
_SESSIONS: dict[uuid.UUID, FormSession] = {}
_KEYS: set[str] = set()
_LOCK = threading.Lock()


def session_of(run_id: uuid.UUID | None) -> FormSession | None:
    with _LOCK:
        return _SESSIONS.get(run_id) if run_id is not None else None


@dataclass(frozen=True)
class FormDefinition(definitions.AgentDefinition):
    """The definition; its first message names no person, posting or page."""

    def prompt(self, data: dict[str, Any], job_id: uuid.UUID | None) -> str:
        with _LOCK:
            pending = str(data.get("request")) in _KEYS
        if not pending:
            raise LookupError("no pending form")  # a run nobody waits for: no model call
        return FIRST_MESSAGE


def _definition() -> FormDefinition:
    return FormDefinition(
        name=NAME,
        description="Fills one application form no adapter knows and names its submit element.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=TOOLS,
        input_model=FormInput,
        model=MODEL,
        model_by_credential={CredentialKind.anthropic_api_key.value: MODEL_API_KEY},
        max_turns=MAX_TURNS,
        wall_time_s=WALL_TIME_S + SIGNIN_WAIT_S,
        effort="medium",
        internal=True,
        live_text=False,
        private_io=True,
    )


APPLY_FORM = definitions.register(_definition())


def _stopped(ended: Any) -> str:
    if ended.status is RunStatus.done:
        return ""
    if ended.error == TOKEN_LIMIT:
        return "token_budget"
    if (ended.error or "").startswith(_TURN_LIMIT_HEAD):
        return "turn_limit"
    return "cancelled" if ended.status is RunStatus.cancelled else "error"


@dataclass
class RuntimeFormAgent:
    """`FormAgent` on the agents runtime: one `apply_form` run per fill, executed in this process
    (the pattern of `agents.RuntimeAnswerAgent`). `script` names the fake backend's stream."""

    context: Callable[[], RunContext]
    script: str | None = None

    async def __call__(self, session: FormSession) -> Ran:
        from app.agents import runner  # the SDK: the worker's image only

        context = self.context()
        key = secrets.token_hex(16)
        user_id = uuid.UUID(session.user_id)
        data = FormInput(
            request=key, token_budget=session.token_budget, script=self.script
        ).model_dump(exclude_none=True)

        def create(db: Session) -> uuid.UUID | str:
            if credentials.get(db, user_id) is None:
                return "no_credential"
            try:
                made = runs.create_run(db, user_id, NAME, None, data, MODEL, context.clock())
            except runs.ActiveRunsError:
                return "busy"
            return made.id

        creating = asyncio.ensure_future(
            asyncio.to_thread(answer_agents._in_db, context.sessions, create)
        )
        try:
            created = await asyncio.shield(creating)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                made = await asyncio.shield(creating)
                if isinstance(made, uuid.UUID):
                    await answer_agents._release(context, made)
            raise
        if isinstance(created, str):
            return Ran(stopped=created)
        session.deadline = time.monotonic() + APPLY_FORM.wall_time_s
        with _LOCK:
            _KEYS.add(key)
            _SESSIONS[created] = session
        try:
            await runner.execute(context, created)
        except asyncio.CancelledError:
            await answer_agents._release(context, created)
            raise
        finally:
            with _LOCK:
                _KEYS.discard(key)
                _SESSIONS.pop(created, None)
        ended = await asyncio.to_thread(
            answer_agents._in_db, context.sessions, lambda db: answer_agents._ended(db, created)
        )
        return Ran(ended.input_tokens, ended.output_tokens, _stopped(ended))


async def fill(agent: FormAgent, session: FormSession) -> tuple[FormReport, dict[str, Any]]:
    """The agent's run over the session, then the report read by code, and the cost."""
    try:
        ran = await agent(session)
    except Exception:  # the model failing leaves what was filled, with no designation
        ran = Ran(stopped="error")
        session.designated = ""
    if ran.stopped:
        session.designated = ""
    report = await session.finish()
    cost: dict[str, Any] = {
        "form_agent": "stopped" if ran.stopped else "ran",
        **({"stopped": ran.stopped} if ran.stopped else {}),
        "input_tokens": ran.input_tokens,
        "output_tokens": ran.output_tokens,
        "steps": len(report.steps),
        "refused": report.refused,
        "addressed": report.addressed,
    }
    return report, cost


if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools: importing them registers them with the runner.
    from app.apply import form_tools as _tools  # noqa: F401
