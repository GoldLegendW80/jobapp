"""The saved overrides of an agent's definition (agents A6, SPEC-agents.md "The Agents tab").

An admin changes what makes an agent from the Agents tab of Admin; each save is a new version
in `agent_overrides` (models.py), never an edit of an older one, so the history stays whole.
"Revert to the code's version" saves a version with no change. A version holds only the
editable fields that differ from the code when it is saved (`check`):

  description, system_prompt, default_message, model, model_by_credential, effort, max_turns,
  wall_time_s, tools (the code's tools switched off: never one the code did not give the agent).

A wall time may be lowered or raised (A7, owner 2026-11-04), from WALL_TIME_MIN_S up to the
agent's own ceiling (`ceiling_s`, catalog.ceiling_of): the bound of the job that runs it, the
longest the agents queue lets a run take for an agent run as its own run_agent job, the other
module's job for one that runs inside it (a sweep, a fill, a Full auto decision, a profile
import). The worker's bounds are per run: the job of a raised run ends at its wall time plus a
margin (worker.run_job_timeout_s), the reaper waits as long (runs.reap_stale), and a run nobody
raised keeps the queue's bounds.

Everything else stays the code's: the input model, the flags (internal, private_io, bind, chat,
needs_job, bind_job, test_only, live_text), the tools' code and schemas, and what the runner
sets for every agent (sdk_options.py: the denied built-ins, the permission mode, no settings
sources). So do the guards enforced in code whatever a prompt says: browserd's policy, the apply
gate and its approval, no mail sent by itself, the cybersecurity rule. A saved prompt is checked
for its size only, never for what it says.

The default message is editable only for an agent whose first message can be it
(`AgentDefinition.uses_default_message`); the others refuse it, so nothing is saved that no run
reads.

Each version also keeps what the code said when it was saved: a hash of the code's value of each
field it overrides (`code_hashes`), and the code's prompt itself when it overrides the prompt
(`code_prompt`). The page and the list then say when the code changed a field since the version
that runs was saved (`code_changed`): a saved prompt would otherwise hide every later change of
the code's prompt.

The runner reads the latest version when a run starts (`for_run`: one read, no cache) and
records the version on the run (`agent_runs.agent_version`). When that read fails, the run goes
on with the code's definition and a warning names the error's type.
"""

import dataclasses
import hashlib
import json
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session, sessionmaker

from app.agents import catalog, definitions
from app.agents.models import AgentOverride, AgentRun, CredentialKind

logger = logging.getLogger("app.agents.overrides")

PROMPT_MAX = 100_000
DESCRIPTION_MAX = 300
MESSAGE_MAX = 4_000
NOTE_MAX = 500
TURNS_MAX = 10_000
# A run gets at least this long, and never more than its agent's ceiling (`ceiling_s`).
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
    wall_time_s: float | None = Field(default=None, ge=WALL_TIME_MIN_S, allow_inf_nan=False)
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


def ceiling_s(definition: definitions.AgentDefinition | None = None) -> float:
    """The longest wall time a saved version may give a run of `definition` (catalog.ceiling_of).
    None: the agents queue's, the longest it lets any run take (catalog.longest_wall_time_s;
    worker.RUN_AGENT_MAX_S gives arq room for it)."""
    if definition is None:
        return catalog.longest_wall_time_s()
    return catalog.ceiling_of(definition).seconds


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
    if (
        "wall_time_s" in given
        and given["wall_time_s"] > (ceiling := catalog.ceiling_of(definition)).seconds
    ):
        raise OverrideError(f"wall_time_s: at most {ceiling.seconds:g} seconds. {ceiling.where}")
    if "default_message" in given and not definition.uses_default_message:
        raise OverrideError("default_message: this agent builds its first message in code")
    return {name: value for name, value in given.items() if value != code[name]}


def code_hash(value: Any) -> str:
    """The hash of a code's value as `code_values` gives it, keys sorted."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def code_hashes(
    definition: definitions.AgentDefinition, changes: Mapping[str, Any]
) -> dict[str, str]:
    """What a version keeps of the code it was saved against: the hash of the code's value of
    each field it changes."""
    code = code_values(definition)
    return {name: code_hash(code[name]) for name in sorted(changes) if name in code}


def code_changed(definition: definitions.AgentDefinition, row: AgentOverride | None) -> list[str]:
    """The fields of this version whose code value is no longer the one it was saved against.
    A field saved with no hash is not named: nothing says what the code was."""
    if row is None:
        return []
    code = code_values(definition)
    kept = dict(row.code_hashes or {})
    return [
        name
        for name in EDITABLE
        if name in (row.changes or {}) and name in kept and kept[name] != code_hash(code[name])
    ]


def apply(
    definition: definitions.AgentDefinition, changes: Mapping[str, Any]
) -> definitions.AgentDefinition:
    """The definition a run uses: the code's, with the saved changes on top. A version saved
    against an older code is read the same way, field by field: a field the agent cannot take
    any more (a tool the code removed, a default message its first message no longer reads)
    falls back to the code's value, a wall time above the agent's ceiling of now is that ceiling,
    and the others still apply."""
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
        if name == "default_message" and not definition.uses_default_message:
            continue
        if name == "tools":
            value = tuple(tool for tool in definition.tools if tool in set(value))
        elif name == "model_by_credential":
            value = {str(kind.value): model for kind, model in value.items()}
        elif name == "wall_time_s":
            value = min(value, ceiling_s(definition))
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
        code_hashes=code_hashes(definition, changes),
        code_prompt=definition.system_prompt if "system_prompt" in changes else None,
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


def run_wall_time_s(sessions: sessionmaker[Session], run_id: uuid.UUID) -> float | None:
    """The wall time a run would get if it started now (A7, the bound of its job in the worker):
    its agent's code definition with the latest saved version on top. None when the run or its
    agent is not known here, or the read fails (a warning names the error's type only)."""
    try:
        with sessions() as db:
            agent = db.scalar(sa.select(AgentRun.agent).where(AgentRun.id == run_id))
            found = latest(db, agent) if agent is not None else None
            changes = dict(found.changes or {}) if found is not None else {}
            db.rollback()
    except Exception as exc:
        logger.warning(
            "agent run's wall time not read, the queue's job timeout is used",
            extra={"run_id": str(run_id), "error": type(exc).__name__},
        )
        return None
    definition = definitions.AGENTS.get(agent) if agent is not None else None
    if definition is None:
        return None
    return apply(definition, changes).wall_time_s


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
