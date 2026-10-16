"""Helpers of the tests of the extraction step (ingestion G9), for the tasks that follow as well
(G10 the chain, G12 the merge): a document, an ingestion run with one running extraction step and
its agent run, created directly; the step's tools, built as the runner builds them; and the
builders of fake streams. Every document here is made up: no real person, no real address."""

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions, runner, runs
from app.agents import tools as app_tools
from app.agents.crypto import Keyring
from app.agents.models import AgentRun
from app.config import Settings
from app.ingestion import agents, plan
from app.ingestion.models import IngestionDocument, TextSource, TextStatus
from app.ingestion.runs_models import (
    IngestionItem,
    IngestionRun,
    IngestionRunStep,
    StepStatus,
    text_sha256,
)
from app.ingestion.tools import of_stored_text
from tests.agents.conftest import new_key
from tests.agents.run_helpers import now
from tests.conftest import make_settings

FIXTURES = Path(__file__).parent / "fixtures"
# What stands for a private document: found nowhere but in the document, the items drawn from it
# and the tests themselves.
MARKER = "PRIVATE-DOCUMENT-MARKER-9c41"
USAGE = {
    "input_tokens": 700,
    "output_tokens": 40,
    "cache_creation_input_tokens": 100,
    "cache_read_input_tokens": 100,
}


def fixture(name: str) -> str:
    """The text of a fixture document, as an upload of it would be stored."""
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


@pytest.fixture
def key() -> str:
    return new_key()


@pytest.fixture
def keyring(key: str) -> Keyring:
    return Keyring.from_text(key)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(scratch))


@dataclass(frozen=True)
class Step:
    user_id: uuid.UUID
    document_id: uuid.UUID
    ingestion_run_id: uuid.UUID
    step_id: uuid.UUID
    agent_run_id: uuid.UUID


def add_document(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    text: str | None,
    *,
    digest: bytes | None = None,
    document_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """A stored document whose text is `text` (None: it has no text yet)."""
    data = (text or "-").encode("utf-8")
    with factory() as db, db.begin():
        document = IngestionDocument(
            id=document_id or uuid.uuid4(),
            user_id=user_id,
            name="document.txt",
            mime="text/plain",
            size=len(data),
            sha256=digest or hashlib.sha256(data + user_id.bytes).digest(),
            text=text,
            text_source=TextSource.parsed,
            text_status=TextStatus.done if text is not None else TextStatus.pending,
            parser_version=1,
        )
        db.add(document)
        db.flush()
        return document.id


def add_step(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    start: int | None = None,
    end: int | None = None,
    script: str | None = None,
    budget: int = 1_000_000,
    after: Step | None = None,
    part: int | None = None,
) -> Step:
    """An ingestion run with one extraction step that is running, and the step's agent run, as
    the engine of G10 will create them. `after`: one more step of that step's ingestion run.
    `part`: by default its position, and 0 for a step without a range (a document read whole)."""
    with factory() as db, db.begin():
        sha256, planned_on = db.execute(
            sa.select(IngestionDocument.sha256, IngestionDocument.text).where(
                IngestionDocument.id == document_id
            )
        ).one()
        if after is None:
            run = IngestionRun(
                user_id=user_id,
                document_ids=[document_id],
                base_revision=0,
                deadline_at=now() + timedelta(hours=1),
                estimate_low=1_000,
                estimate_high=2_000,
                token_ceiling=4_000,
            )
            db.add(run)
            db.flush()
            ingestion_run_id, position = run.id, 0
        else:
            ingestion_run_id = after.ingestion_run_id
            position = 1 + int(
                db.execute(
                    sa.select(sa.func.max(IngestionRunStep.position)).where(
                        IngestionRunStep.ingestion_run_id == ingestion_run_id
                    )
                ).scalar_one()
            )
        step = IngestionRunStep(
            ingestion_run_id=ingestion_run_id,
            position=position,
            kind=plan.StepKind.extract,
            document_id=document_id,
            sha256=sha256,
            part=part if part is not None else (0 if start is None else position),
            range_start=start,
            range_end=end,
            identity_hash="a" * 64,
            text_sha256=None if planned_on is None else text_sha256(planned_on),
            status=StepStatus.running,
            token_budget=budget,
        )
        db.add(step)
        db.flush()
        raw: dict[str, Any] = {
            "ingestion_run_id": str(ingestion_run_id),
            "step_id": str(step.id),
            "token_budget": budget,
        }
        if script is not None:
            raw["script"] = script
        agent_run = runs.create_run(
            db,
            user_id,
            agents.EXTRACT_NAME,
            None,
            agents.EXTRACT.validate_input(raw),
            definitions.DEFAULT_MODEL,
            now(),
        )
        step.agent_run_id = agent_run.id
        return Step(user_id, document_id, ingestion_run_id, step.id, agent_run.id)


def binding_of(factory: sessionmaker[Session], step: Step) -> dict[str, Any] | None:
    """What the agent's `bind` hook answers for the step's agent run, now."""
    with factory() as db:
        found = agents.bind_step(db, db.get_one(AgentRun, step.agent_run_id))
        return None if found is None else dict(found)


def tools_for(
    factory: sessionmaker[Session],
    step: Step,
    *,
    user_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    binding: dict[str, Any] | None = None,
    names: tuple[str, ...] | None = None,
) -> app_tools.AppTools:
    """The step's tools as the runner builds them: bound, the binding frozen. The overrides are
    for the tests that hand a tool a scope it must refuse."""
    bound = binding if binding is not None else binding_of(factory, step)
    assert bound is not None
    return app_tools.build(
        factory,
        user_id or step.user_id,
        names or agents.EXTRACT.tools,
        run_id=run_id or step.agent_run_id,
        binding=runner.frozen(bound),
    )


def call(tools: app_tools.AppTools, name: str, **args: Any) -> tuple[Any, bool]:
    """Call a tool as Claude Code would. Returns (the answer, parsed when it is JSON; is_error)."""
    result = asyncio.run(tools.by_name[app_tools.full_name(name)].handler(args))
    text = result["content"][0]["text"]
    try:
        parsed: Any = json.loads(text)
    except ValueError:
        parsed = text
    return parsed, bool(result.get("is_error"))


def raw_answer(tools: app_tools.AppTools, name: str, **args: Any) -> str:
    """The answer of a tool exactly as the model receives it."""
    result = asyncio.run(tools.by_name[app_tools.full_name(name)].handler(args))
    return str(result["content"][0]["text"])


def stored_items(factory: sessionmaker[Session], step: Step) -> list[IngestionItem]:
    with factory() as db:
        return list(
            db.execute(
                sa.select(IngestionItem)
                .where(IngestionItem.step_id == step.step_id)
                .order_by(IngestionItem.id)
            ).scalars()
        )


def items_of_stored_text(factory: sessionmaker[Session], step: Step) -> list[IngestionItem]:
    """The step's items that the merge may read: those of the text its document stores now."""
    with factory() as db:
        return list(
            db.execute(
                sa.select(IngestionItem)
                .where(IngestionItem.step_id == step.step_id, of_stored_text())
                .order_by(IngestionItem.id)
            ).scalars()
        )


def all_items(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return int(db.execute(sa.select(sa.func.count()).select_from(IngestionItem)).scalar_one())


def counters(factory: sessionmaker[Session], step: Step) -> tuple[int, int, int]:
    """(items_count, refused_count, two_digit_year_count) of the step."""
    with factory() as db:
        row = db.get_one(IngestionRunStep, step.step_id)
        return row.items_count, row.refused_count, row.two_digit_year_count


def set_step(factory: sessionmaker[Session], step: Step, **changes: Any) -> None:
    with factory() as db, db.begin():
        db.execute(
            sa.update(IngestionRunStep).where(IngestionRunStep.id == step.step_id).values(**changes)
        )


def delete_document(factory: sessionmaker[Session], document_id: uuid.UUID) -> None:
    with factory() as db, db.begin():
        db.execute(sa.delete(IngestionDocument).where(IngestionDocument.id == document_id))


# --- fake streams ---------------------------------------------------------------------------------


def item(key: str, value: Any, *excerpts: str, action: str = "add") -> dict[str, Any]:
    """One proposed item, as the model writes it."""
    made: dict[str, Any] = {"key": key, "action": action}
    if value is not None:
        made["value"] = value
    made["citations"] = [{"excerpt": excerpt} for excerpt in excerpts]
    return made


def init() -> dict[str, Any]:
    return {
        "message": {
            "type": "system",
            "subtype": "init",
            "session_id": "{session_id}",
            "uuid": "00000000-0000-4000-8000-000000000001",
            "cwd": "/tmp/run",  # noqa: S108, what the CLI prints, not a path this code opens
            "model": "{model}",
            "permissionMode": "dontAsk",
            "tools": [
                "mcp__app__read_document",
                "mcp__app__propose_profile_changes",
            ],
            "mcp_servers": [{"name": "app", "status": "connected"}],
            "apiKeySource": "ANTHROPIC_API_KEY",
            "claude_code_version": "2.1.286",
        }
    }


def assistant(number: int, *blocks: dict[str, Any]) -> dict[str, Any]:
    """The assistant message of request `number` (1 based)."""
    return {
        "message": {
            "type": "assistant",
            "session_id": "{session_id}",
            "uuid": f"00000000-0000-4000-8000-{0x100 + number:012x}",
            "parent_tool_use_id": None,
            "message": {
                "id": f"msg_extract_{number:03d}",
                "type": "message",
                "role": "assistant",
                "model": "{model}",
                "content": list(blocks),
                "stop_reason": "tool_use"
                if any(block["type"] == "tool_use" for block in blocks)
                else "end_turn",
                "usage": dict(USAGE),
            },
        }
    }


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def read(number: int, offset: Any = 0, **extra: Any) -> dict[str, Any]:
    """A call of read_document. `extra`: arguments the tool does not have, as a hostile document
    would have the model add them."""
    return {
        "type": "tool_use",
        "id": f"toolu_extract_{number:03d}",
        "name": "mcp__app__read_document",
        "input": {"offset": offset, **extra},
    }


def propose(number: int, section: str, *proposed: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": f"toolu_extract_{number:03d}",
        "name": "mcp__app__propose_profile_changes",
        "input": {"section": section, "items": list(proposed), **extra},
    }


def other_tool(number: int, name: str, **args: Any) -> dict[str, Any]:
    """A call of a tool the agent does not have (a built-in one, or one of a test)."""
    return {"type": "tool_use", "id": f"toolu_extract_{number:03d}", "name": name, "input": args}


def final(answer: str, turns: int) -> dict[str, Any]:
    requests = turns
    return {
        "message": {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 1800,
            "duration_api_ms": 1500,
            "num_turns": turns,
            "session_id": "{session_id}",
            "uuid": "00000000-0000-4000-8000-0000000009ff",
            "result": answer,
            "stop_reason": "end_turn",
            "total_cost_usd": 0.02,
            "usage": {name: count * requests for name, count in USAGE.items()},
            "permission_denials": [],
            "terminal_reason": "completed",
        }
    }


def stream(description: str, *messages: tuple[dict[str, Any], ...] | str) -> dict[str, Any]:
    """A whole stream: one tuple of blocks per request that calls tools, then the one line of
    the final answer."""
    steps = [init()]
    number = 0
    answer = ""
    for blocks in messages:
        number += 1
        if isinstance(blocks, str):
            answer = blocks
            steps.append(assistant(number, text(blocks)))
        else:
            steps.append(assistant(number, *blocks))
    steps.append(final(answer, number))
    return {"description": description, "steps": steps}
