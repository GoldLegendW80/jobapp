"""The saved overrides of an agent's definition (agents A6, SPEC-agents.md "The Agents tab").

An admin changes what makes an agent from the Agents tab of Admin; each save is a new version
in `agent_overrides` (models.py), never an edit of an older one, so the history stays whole.
"Revert to the code's version" saves a version with no change. A version holds only the
editable fields that differ from the code when it is saved (`check`):

  description, system_prompt, default_message, model, model_by_credential, effort, max_turns,
  wall_time_s, tools (the code's tools switched off: never one the code did not give the agent).

Everything else stays the code's: the input model, the flags (internal, private_io, bind, chat,
needs_job, bind_job, test_only, live_text), the tools' code and schemas, and what the runner
sets for every agent (sdk_options.py: the denied built-ins, the permission mode, no settings
sources). So do the guards enforced in code whatever a prompt says: browserd's policy, the apply
gate and its approval, no mail sent by itself, the cybersecurity rule. A saved prompt is checked
for its size only, never for what it says.

The runner reads the latest version when a run starts (`for_run`: one read, no cache) and
records the version on the run (`agent_runs.agent_version`). When that read fails, the run goes
on with the code's definition and a warning names the error's type.
"""

import dataclasses
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions
from app.agents.models import AgentOverride, AgentRun, CredentialKind

logger = logging.getLogger("app.agents.overrides")

PROMPT_MAX = 100_000
DESCRIPTION_MAX = 300
MESSAGE_MAX = 4_000
NOTE_MAX = 500
TURNS_MAX = 10_000
# A run gets at least this long; never more than the code's own wall time (`check`): the
# queue's job timeout is computed from the code's limits when the worker starts.
WALL_TIME_MIN_S = 10.0
EDITABLE = (
    "description",
    "system_prompt",
    "default_message",
    "model",
    "model_by_credential",
    "effort",
    "max_turns",
    "wall_time_s",
    "tools",
)


class OverrideError(ValueError):
    """A change the agent cannot take. The message says which field and why."""


class StaleVersionError(RuntimeError):
    """Another version was saved since the page was read."""


def _not_blank(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise ValueError("must not be empty")
    return value


class Changes(BaseModel):
    """The editable fields as a save sends them; a field left out keeps the code's value."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, min_length=1, max_length=DESCRIPTION_MAX)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=PROMPT_MAX)
    default_message: str | None = Field(default=None, min_length=1, max_length=MESSAGE_MAX)
    model: str | None = None
    model_by_credential: dict[CredentialKind, str] | None = None
    effort: str | None = None
    max_turns: int | None = Field(default=None, ge=1, le=TURNS_MAX)
    wall_time_s: float | None = Field(default=None, ge=WALL_TIME_MIN_S)
    tools: list[str] | None = Field(default=None, max_length=64)

    _blank = field_validator("description", "system_prompt", "default_message")(_not_blank)

    @field_validator("model")
    @classmethod
    def _model(cls, value: str | None) -> str | None:
        if value is not None and value not in definitions.MODELS:
            raise ValueError(f"must be one of {', '.join(definitions.MODELS)}")
        return value

    @field_validator("model_by_credential")
    @classmethod
    def _models(cls, value: dict[CredentialKind, str] | None) -> dict[CredentialKind, str] | None:
        for model in (value or {}).values():
            if model not in definitions.MODELS:
                raise ValueError(f"must be one of {', '.join(definitions.MODELS)}")
        return value

    @field_validator("effort")
    @classmethod
    def _effort(cls, value: str | None) -> str | None:
        if value is not None and value not in definitions.EFFORTS:
            raise ValueError(f"must be one of {', '.join(definitions.EFFORTS)}")
        return value


def code_values(definition: definitions.AgentDefinition) -> dict[str, Any]:
    """The editable fields as the code defines them, in the form a version stores."""
    return {
        "description": definition.description,
        "system_prompt": definition.system_prompt,
        "default_message": definition.default_message,
        "model": definition.model,
        "model_by_credential": dict(definition.model_by_credential or {}),
        "effort": definition.effort,
        "max_turns": definition.max_turns,
        "wall_time_s": definition.wall_time_s,
        "tools": list(definition.tools),
    }


def check(definition: definitions.AgentDefinition, raw: Mapping[str, Any]) -> dict[str, Any]:
    """What a version stores for these changes: each field checked, then only the fields that
    differ from the code. OverrideError names the first field the agent cannot take."""
    code = code_values(definition)
    # A field sent as the code has it is no change (an empty default message included).
    sent = {name: value for name, value in raw.items() if name not in code or value != code[name]}
    try:
        parsed = Changes.model_validate(sent)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "changes"
        raise OverrideError(f"{where}: {first.get('msg', 'invalid')}") from None
    given = parsed.model_dump(mode="json", exclude_none=True)
    if "tools" in given:
        unknown = [name for name in given["tools"] if name not in definition.tools]
        if unknown:
            raise OverrideError(f"tools: not a tool of this agent: {', '.join(unknown)}")
        # Kept in the code's order, each once.
        given["tools"] = [name for name in definition.tools if name in set(given["tools"])]
    if "wall_time_s" in given and given["wall_time_s"] > definition.wall_time_s:
        raise OverrideError(f"wall_time_s: at most the code's {definition.wall_time_s:g} seconds")
    return {name: value for name, value in given.items() if value != code[name]}


def apply(
    definition: definitions.AgentDefinition, changes: Mapping[str, Any]
) -> definitions.AgentDefinition:
    """The definition a run uses: the code's, with the saved changes on top. A version saved
    against an older code is read the same way, field by field: a field the agent cannot take
    any more (a tool the code removed, a wall time above the code's new one) falls back to the
    code's value, and the others still apply."""
    fields: dict[str, Any] = {}
    for name in EDITABLE:
        if name not in changes:
            continue
        try:
            parsed = Changes.model_validate({name: changes[name]})
        except ValidationError:
            continue
        value = getattr(parsed, name)
        if value is None:
            continue
        if name == "tools":
            value = tuple(tool for tool in definition.tools if tool in set(value))
        elif name == "model_by_credential":
            value = {str(kind.value): model for kind, model in value.items()}
        elif name == "wall_time_s":
            value = min(value, definition.wall_time_s)
        fields[name] = value
    return dataclasses.replace(definition, **fields) if fields else definition


def latest(db: Session, agent: str) -> AgentOverride | None:
    return db.scalars(
        sa.select(AgentOverride)
        .where(AgentOverride.agent == agent)
        .order_by(AgentOverride.version.desc())
        .limit(1)
    ).first()


def latest_of_all(db: Session) -> dict[str, AgentOverride]:
    """The latest version of every agent that has one."""
    newest = (
        sa.select(AgentOverride.agent, sa.func.max(AgentOverride.version).label("version"))
        .group_by(AgentOverride.agent)
        .subquery()
    )
    rows = db.scalars(
        sa.select(AgentOverride).join(
            newest,
            sa.and_(
                AgentOverride.agent == newest.c.agent, AgentOverride.version == newest.c.version
            ),
        )
    )
    return {row.agent: row for row in rows}


def history(db: Session, agent: str, limit: int = 50) -> list[AgentOverride]:
    return list(
        db.scalars(
            sa.select(AgentOverride)
            .where(AgentOverride.agent == agent)
            .order_by(AgentOverride.version.desc())
            .limit(limit)
        )
    )


def save(
    db: Session,
    definition: definitions.AgentDefinition,
    raw: Mapping[str, Any],
    *,
    base_version: int | None,
    note: str | None,
    user_id: uuid.UUID,
    now: datetime,
) -> AgentOverride:
    """A new version of the agent's overrides, in the caller's transaction. Two saves of one
    agent wait for each other (a transaction lock on its name); the latest version is read
    again under it and must be the one the page showed (`base_version`, None when it showed
    none), else StaleVersionError and nothing is written."""
    changes = check(definition, raw)
    if note is not None and (len(note) > NOTE_MAX):
        raise OverrideError(f"note: at most {NOTE_MAX} characters")
    db.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"agent_overrides:{definition.name}"},
    )
    current = latest(db, definition.name)
    if (current.version if current is not None else None) != base_version:
        raise StaleVersionError(definition.name)
    row = AgentOverride(
        agent=definition.name,
        version=(current.version if current is not None else 0) + 1,
        changes=changes,
        saved_by=user_id,
        saved_at=now,
        note=(note or "").strip() or None,
    )
    db.add(row)
    db.flush()
    return row


def _read_and_record(
    sessions: sessionmaker[Session], run_id: uuid.UUID, agent: str
) -> tuple[int, dict[str, Any]] | None:
    with sessions() as db:
        found = latest(db, agent)
        if found is None:
            db.rollback()
            return None
        db.execute(
            sa.update(AgentRun).where(AgentRun.id == run_id).values(agent_version=found.version)
        )
        db.commit()
        return found.version, dict(found.changes or {})


def for_run(
    sessions: sessionmaker[Session],
    run_id: uuid.UUID,
    definition: definitions.AgentDefinition,
) -> tuple[definitions.AgentDefinition, int | None]:
    """The definition a starting run uses and the version it records: the latest saved version
    read once, in a transaction of its own that also writes the version on the run. No version
    saved: the code's definition, and the run records none. A failed read or write: the code's
    definition too, with a warning that names the error's type only."""
    try:
        found = _read_and_record(sessions, run_id, definition.name)
    except Exception as exc:
        logger.warning(
            "agent overrides not read, the code's definition is used",
            extra={"run_id": str(run_id), "agent": definition.name, "error": type(exc).__name__},
        )
        return definition, None
    if found is None:
        return definition, None
    version, changes = found
    return apply(definition, changes), version


def effective(
    sessions: sessionmaker[Session], agents: list[definitions.AgentDefinition]
) -> list[definitions.AgentDefinition]:
    """The agents as their next run would use them, for a page that lists them. A failed read
    lists the code's definitions, with a warning that names the error's type."""
    try:
        with sessions() as db:
            saved = {name: dict(row.changes or {}) for name, row in latest_of_all(db).items()}
            db.rollback()
    except Exception as exc:
        logger.warning("agent overrides not read", extra={"error": type(exc).__name__})
        return agents
    return [apply(agent, saved.get(agent.name, {})) for agent in agents]
