"""/api/admin/agents: the Agents tab of Admin (agents A6, SPEC-agents.md "The Agents tab").

Every registered agent (catalog.py), as the running code defines it plus what an admin saved
(overrides.py): who starts it, its flags, its input, its system prompt and first message, its
tools as the model sees them, its model and limits, the options the runner hands Claude Code,
its recent runs and the history of its versions. A save is a new version, used from the next
run on.

Every route runs `require_admin` (router dependency), which answers 404 to a member, as the
other admin routes. The pages carry no secret and no environment value. A run shows its numbers
only (status, model, version, turns, tokens, when it was queued, started and finished), never its
input, output or error, a private run's included; of the person it ran for, only the name and
e-mail the Users page of Admin shows (agents A7); and the link to its page only to the admin who
ran it.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents import catalog, definitions, overrides, sdk_options
from app.agents.models import AgentEvent, AgentRun, CredentialKind
from app.db import get_session, read_snapshot
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut

router = APIRouter(
    prefix="/api/admin/agents", tags=["admin"], dependencies=[Depends(deps.require_admin)]
)

NO_AGENT = "No agent has this name."
STALE = "Another version was saved since this page was read. Reload it, then save again."
RUNS_SHOWN = 10
GUARDS = (
    "Whatever a saved prompt says, the code keeps its guards: browserd's line (B20: no apply or "
    "submit, no message or connection request, no sign out, no form but a search), the apply "
    "gate and the person's approval before anything is submitted, no mail ever sent by itself, "
    "no application to a cybersecurity role, the privacy of private runs, the denied built-in "
    "tools, the permission mode and the empty setting sources."
)
SKILLS = (
    "None. Claude Code finds skills, CLAUDE.md files and settings through its setting sources, "
    "and the runner passes none (setting_sources is empty); the Skill and SlashCommand tools "
    "are denied by name as well."
)
GATE = (
    "A PreToolUse hook in the runner's code answers every tool call first: any tool outside "
    "allowed_tools is denied, whatever the options or the model say, and a run with a token "
    "budget is stopped there once the budget is used up."
)

Db = Annotated[Session, Depends(get_session)]
Admin = Annotated[User, Depends(deps.require_admin)]
_404 = {404: {"model": ErrorOut, "description": "Not an admin, or no such agent"}}


class InputField(BaseModel):
    name: str
    type: str
    required: bool
    default: Any = None
    limits: str
    description: str


class ToolOut(BaseModel):
    name: str
    full_name: str
    module: str | None
    description: str | None
    input_schema: dict[str, Any] | None
    described_at: datetime | None
    on: bool


class VersionOut(BaseModel):
    version: int
    fields: list[str]
    saved_by: str | None
    saved_at: datetime
    note: str | None


class RunRow(BaseModel):
    id: uuid.UUID
    status: str
    model: str | None
    version: int | None
    turns: int | None
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime  # queued
    started_at: datetime | None  # None: not started yet
    finished_at: datetime | None
    # The person the run is for, as the Users page shows them (A7): nothing else of the account.
    user_name: str
    user_email: str
    mine: bool


class AgentSummary(BaseModel):
    name: str
    description: str
    module: str
    model: str
    test_only: bool
    internal: bool
    private_io: bool
    version: int | None
    overridden: bool
    # The fields of that version whose code value changed since it was saved.
    code_changed: list[str]
    last_run_at: datetime | None


class AgentList(BaseModel):
    items: list[AgentSummary]


class AgentPage(BaseModel):
    name: str
    module: str
    starter: str
    flags: list[str]
    test_only: bool
    internal: bool
    private_io: bool
    code: dict[str, Any]
    saved: dict[str, Any]
    effective: dict[str, Any]
    input_fields: list[InputField]
    input_schema: dict[str, Any]
    first_message: str
    # Whether a run's first message can be the default message: the field is editable only then.
    uses_default_message: bool
    tools: list[ToolOut]
    turn_budgeted: bool
    ends: list[str]
    claude_code: dict[str, Any]
    environment: str
    gate: str
    skills: str
    guards: str
    version: int | None
    # The fields of the version that runs whose code value changed since it was saved, and the
    # code's prompt of then when the prompt is one of them.
    code_changed: list[str]
    code_prompt_then: str | None
    versions: list[VersionOut]
    runs: list[RunRow]
    choices: dict[str, Any]


class VersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: dict[str, Any] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=overrides.NOTE_MAX)
    # The latest version the page showed (None: it showed none).
    base_version: int | None = None


def _agents(request: Request) -> dict[str, definitions.AgentDefinition]:
    catalog.load()
    return definitions.available(request.app.state.settings.app_env)


def _agent(request: Request, name: str) -> definitions.AgentDefinition:
    found = _agents(request).get(name)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_AGENT)
    return found


def _flags(agent: definitions.AgentDefinition) -> list[str]:
    flags = []
    if agent.needs_job:
        flags.append("Needs a job: every run names the job it works on.")
    if agent.chat:
        flags.append("Chat: its runs start from a message in a thread.")
    if agent.bind_job:
        flags.append("Bound to its job: get_job reads the run's own job only.")
    if agent.bind is not None:
        flags.append("Bound in code: what a run works on is decided before any tool exists.")
    if agent.private_io:
        flags.append(
            "Private: its inputs and outputs are never stored, only their sizes and counts."
        )
    flags.append(
        "Live text: the answer shows while it is written."
        if agent.live
        else "No live text: the answer shows once it is complete."
    )
    if agent.on_final is not None:
        flags.append("Followed: its module acts once a run has ended.")
    if agent.test_only:
        flags.append("Test only: never runs in production.")
    if agent.internal:
        flags.append("Internal: started by its own module, never from a run button.")
    return flags


_LIMIT_KEYS = {
    "minLength": "at least {} characters",
    "maxLength": "at most {} characters",
    "minimum": "at least {}",
    "maximum": "at most {}",
    "exclusiveMinimum": "above {}",
    "exclusiveMaximum": "below {}",
    "minItems": "at least {} items",
    "maxItems": "at most {} items",
    "pattern": "matches {}",
}


def _type_of(prop: dict[str, Any]) -> str:
    if "anyOf" in prop:
        return " or ".join(_type_of(one) for one in prop["anyOf"])
    if "$ref" in prop:
        return str(prop["$ref"]).rsplit("/", 1)[-1]
    if "enum" in prop:
        return "one of " + ", ".join(str(value) for value in prop["enum"])
    kind = str(prop.get("type", "any"))
    if kind == "array" and isinstance(prop.get("items"), dict):
        return f"list of {_type_of(prop['items'])}"
    return kind


def _limits(prop: dict[str, Any]) -> str:
    found = [text.format(prop[key]) for key, text in _LIMIT_KEYS.items() if key in prop]
    for one in prop.get("anyOf", ()):
        found += [text.format(one[key]) for key, text in _LIMIT_KEYS.items() if key in one]
    return ", ".join(found)


def _input(agent: definitions.AgentDefinition) -> tuple[list[InputField], dict[str, Any]]:
    schema = agent.input_model.model_json_schema()
    required = set(schema.get("required", ()))
    rows = [
        InputField(
            name=name,
            type=_type_of(prop),
            required=name in required,
            default=prop.get("default"),
            limits=_limits(prop),
            description=str(prop.get("description") or ""),
        )
        for name, prop in (schema.get("properties") or {}).items()
    ]
    return rows, schema


def _seconds(value: float) -> str:
    if value >= 3600:
        return f"{value / 3600:g} hours"
    if value >= 60:
        return f"{value / 60:g} minutes"
    return f"{value:g} seconds"


def _ends(agent: definitions.AgentDefinition) -> list[str]:
    turns = f"It reaches {agent.max_turns} turns (max_turns)"
    if agent.name in sdk_options.TURN_BUDGETED:
        turns += ", or the lower turn budget its input sets"
    ends = [
        "The model gives its final answer.",
        turns + ".",
        f"It has run for {_seconds(agent.wall_time_s)} (wall_time_s): the runner stops it.",
        "The person cancels it, or the worker stops.",
    ]
    if "token_budget" in agent.input_model.model_fields:
        ends.append("Its input's token budget is used up (token_budget).")
    return ends


def _options(agent: definitions.AgentDefinition) -> dict[str, Any]:
    """The Claude Agent SDK options of a run of this agent, as runner.build_options builds
    them (the live objects named, never a value of the environment)."""
    return {
        **sdk_options.fixed(),
        "system_prompt": "the system prompt above",
        "allowed_tools": sorted(sdk_options.full_name(name) for name in agent.tools),
        "mcp_servers": {
            sdk_options.SERVER: "in-process MCP server holding the tools above, bound to "
            "the run's user"
        },
        "model": agent.model,
        "model_by_credential": dict(agent.model_by_credential or {}),
        "max_turns": agent.max_turns,
        "effort": agent.effort,
        "cwd": "the run's own scratch folder, deleted when the run ends",
        "env": "names only, below",
        "hooks": {"PreToolUse": "the gate, below"},
        "include_partial_messages": agent.live,
    }


def _environment() -> str:
    names = sdk_options.CREDENTIAL_ENV
    kept = ", ".join(sorted(sdk_options.ENV_KEEP))
    return (
        f"The credential of the run's user in {names['anthropic_api_key']} (an API key) or "
        f"{names['claude_subscription']} (a subscription); HOME, the Claude Code configuration "
        "and its temporary folders in the run's scratch folder; prompt history, memory, "
        "CLAUDE.md files, telemetry, error reports and auto updates off. Every other variable "
        f"of the worker is blanked, except {kept}. Values are never shown."
    )


def _versions(db: Session, agent: str) -> list[VersionOut]:
    rows = overrides.history(db, agent)
    ids = {row.saved_by for row in rows if row.saved_by is not None}
    names: dict[uuid.UUID, str] = {}
    if ids:
        names = {
            row.id: row.name
            for row in db.execute(sa.select(User.id, User.name).where(User.id.in_(ids)))
        }
    return [
        VersionOut(
            version=row.version,
            fields=sorted(row.changes or {}),
            saved_by=names.get(row.saved_by) if row.saved_by is not None else None,
            saved_at=row.saved_at,
            note=row.note,
        )
        for row in rows
    ]


def _runs(db: Session, agent: str, admin_id: uuid.UUID) -> list[RunRow]:
    # Numbers only: the turns of the run's usage event, or of a private run's counts.
    usage_turns = (
        sa.select(AgentEvent.data["num_turns"])
        .where(AgentEvent.run_id == AgentRun.id, AgentEvent.kind == "usage")
        .order_by(AgentEvent.seq.desc())
        .limit(1)
        .scalar_subquery()
    )
    rows = db.execute(
        sa.select(
            AgentRun.id,
            AgentRun.user_id,
            AgentRun.status,
            AgentRun.model,
            AgentRun.agent_version,
            AgentRun.input_tokens,
            AgentRun.output_tokens,
            AgentRun.created_at,
            AgentRun.started_at,
            AgentRun.finished_at,
            AgentRun.output["turns"].label("counted_turns"),
            usage_turns.label("usage_turns"),
            User.name.label("user_name"),
            User.email.label("user_email"),
        )
        .join(User, User.id == AgentRun.user_id)
        .where(AgentRun.agent == agent)
        .order_by(AgentRun.created_at.desc())
        .limit(RUNS_SHOWN)
    ).all()

    def turns(*values: Any) -> int | None:
        for value in values:
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    return [
        RunRow(
            id=row.id,
            status=row.status.value,
            model=row.model,
            version=row.agent_version,
            turns=turns(row.counted_turns, row.usage_turns),
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            user_name=row.user_name,
            user_email=row.user_email,
            mine=row.user_id == admin_id,
        )
        for row in rows
    ]


def _choices(agent: definitions.AgentDefinition) -> dict[str, Any]:
    return {
        "models": list(definitions.MODELS),
        "efforts": list(definitions.EFFORTS),
        "credential_kinds": [kind.value for kind in CredentialKind],
        "turns_max": overrides.TURNS_MAX,
        "wall_time_min_s": overrides.WALL_TIME_MIN_S,
        # The agent's own ceiling, the bound of the job that runs it, and where that is (A7).
        "wall_time_max_s": (ceiling := catalog.ceiling_of(agent)).seconds,
        "wall_time_where": ceiling.where,
        "prompt_max": overrides.PROMPT_MAX,
        "description_max": overrides.DESCRIPTION_MAX,
        "message_max": overrides.MESSAGE_MAX,
        "note_max": overrides.NOTE_MAX,
    }


def _page(db: Session, agent: definitions.AgentDefinition, admin_id: uuid.UUID) -> AgentPage:
    read_snapshot(db)
    current = overrides.latest(db, agent.name)
    saved = dict(current.changes or {}) if current is not None else {}
    effective = overrides.apply(agent, saved)
    described = catalog.tools_described(db, agent.tools)
    fields, schema = _input(agent)
    changed = overrides.code_changed(agent, current)
    tools = [
        ToolOut(
            name=name,
            full_name=sdk_options.full_name(name),
            module=described[name].module if name in described else None,
            description=described[name].description if name in described else None,
            input_schema=described[name].input_schema if name in described else None,
            described_at=described[name].published_at if name in described else None,
            on=name in effective.tools,
        )
        for name in agent.tools
    ]
    return AgentPage(
        name=agent.name,
        module=definitions.module_of(agent.name),
        starter=catalog.starter(agent),
        flags=_flags(agent),
        test_only=agent.test_only,
        internal=agent.internal,
        private_io=agent.private_io,
        code=overrides.code_values(agent),
        saved=saved,
        effective=overrides.code_values(effective),
        input_fields=fields,
        input_schema=schema,
        first_message=catalog.first_message(agent),
        uses_default_message=agent.uses_default_message,
        tools=tools,
        turn_budgeted=agent.name in sdk_options.TURN_BUDGETED,
        ends=_ends(effective),
        claude_code=_options(effective),
        environment=_environment(),
        gate=GATE,
        skills=SKILLS,
        guards=GUARDS,
        version=current.version if current is not None else None,
        code_changed=changed,
        code_prompt_then=(
            current.code_prompt if current is not None and "system_prompt" in changed else None
        ),
        versions=_versions(db, agent.name),
        runs=_runs(db, agent.name, admin_id),
        choices=_choices(agent),
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("", response_model=AgentList)
def list_agents(request: Request, response: Response, db: Db) -> AgentList:
    """Every agent registered here, test ones included outside production."""
    agents = _agents(request)
    read_snapshot(db)
    latest = overrides.latest_of_all(db)
    last = {
        row.agent: row.last
        for row in db.execute(
            sa.select(AgentRun.agent, sa.func.max(AgentRun.created_at).label("last")).group_by(
                AgentRun.agent
            )
        )
    }
    _no_store(response)
    items = []
    for name in sorted(agents):
        found = latest.get(name)
        saved = dict(found.changes or {}) if found is not None else {}
        agent = overrides.apply(agents[name], saved)
        items.append(
            AgentSummary(
                name=name,
                description=agent.description,
                module=definitions.module_of(name),
                model=agent.model,
                test_only=agent.test_only,
                internal=agent.internal,
                private_io=agent.private_io,
                version=found.version if found is not None else None,
                overridden=bool(saved),
                code_changed=overrides.code_changed(agents[name], found),
                last_run_at=last.get(name),
            )
        )
    return AgentList(items=items)


@router.get("/{name}", response_model=AgentPage, responses=_404)
def read_agent(name: str, request: Request, response: Response, db: Db, admin: Admin) -> AgentPage:
    agent = _agent(request, name)
    page = _page(db, agent, admin.id)
    _no_store(response)
    return page


@router.post(
    "/{name}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=AgentPage,
    responses={
        **_404,
        409: {"model": ErrorOut, "description": "Another version was saved meanwhile"},
        422: {"model": ErrorOut, "description": "A change the agent cannot take"},
    },
)
def save_version(
    name: str, body: VersionIn, request: Request, response: Response, db: Db, admin: Admin
) -> AgentPage:
    """A new version of the agent's overrides, used from its next run on. `changes` holds the
    editable fields to change; an empty one goes back to the code's definition."""
    agent = _agent(request, name)
    try:
        overrides.save(
            db,
            agent,
            body.changes,
            base_version=body.base_version,
            note=body.note,
            user_id=admin.id,
            now=deps.clock(request),
        )
    except overrides.OverrideError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None
    except overrides.StaleVersionError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=STALE) from None
    db.commit()
    page = _page(db, agent, admin.id)
    _no_store(response)
    return page
