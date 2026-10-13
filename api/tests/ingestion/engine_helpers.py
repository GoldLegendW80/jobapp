"""Helpers of the tests of the ingestion engine (G10): made-up documents, a run started as the
route starts it, and the end of a step's agent run written as the runner or a reaper would."""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.models import AgentEvent, AgentRun
from app.agents.models import RunStatus as AgentStatus
from app.config import Settings
from app.ingestion import engine, estimate_router
from app.ingestion.runs_models import IngestionItem, IngestionRun, IngestionRunStep, StepStatus
from tests.agents.run_helpers import now
from tests.ingestion.extract_helpers import add_document


def text_of(number: int, words: int = 600) -> str:
    """A small made-up text, different for each number."""
    return f"Document {number} of a made-up person.\n\n" + "word " * words


def documents(
    factory: sessionmaker[Session], user: uuid.UUID, count: int, first: int = 0
) -> list[uuid.UUID]:
    return [add_document(factory, user, text_of(first + number)) for number in range(count)]


def start(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    selected: list[uuid.UUID],
    reread: list[uuid.UUID] | None = None,
    at: datetime | None = None,
) -> uuid.UUID:
    """A run created as `POST /api/ingestion/runs` creates it, not yet advanced."""
    with factory() as db, db.begin():
        quote = estimate_router.quote(
            db, user, [str(one) for one in selected], [str(one) for one in reread or []]
        )
        return engine.create(db, user, quote, at or now()).id


def advance(
    factory: sessionmaker[Session],
    run_id: uuid.UUID,
    settings: Settings,
    at: datetime | None = None,
) -> engine.Moved:
    with factory() as db, db.begin():
        return engine.advance(db, run_id, at or now(), settings)


def run_of(factory: sessionmaker[Session], run_id: uuid.UUID) -> IngestionRun:
    with factory() as db:
        return db.get_one(IngestionRun, run_id)


def steps_of(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[IngestionRunStep]:
    with factory() as db:
        return list(
            db.scalars(
                sa.select(IngestionRunStep)
                .where(IngestionRunStep.ingestion_run_id == run_id)
                .order_by(IngestionRunStep.position)
            )
        )


def statuses(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[str]:
    return [step.status.value for step in steps_of(factory, run_id)]


def running(factory: sessionmaker[Session], run_id: uuid.UUID) -> IngestionRunStep:
    """The one running step of the run."""
    (found,) = [step for step in steps_of(factory, run_id) if step.status is StepStatus.running]
    assert found.agent_run_id is not None
    return found


def agent_runs_of(factory: sessionmaker[Session], user: uuid.UUID) -> list[AgentRun]:
    with factory() as db:
        return list(
            db.scalars(
                sa.select(AgentRun).where(AgentRun.user_id == user).order_by(AgentRun.created_at)
            )
        )


def end_agent(
    factory: sessionmaker[Session],
    agent_run_id: uuid.UUID | None,
    status: AgentStatus = AgentStatus.done,
    error: str | None = None,
    tokens: int = 0,
    at: datetime | None = None,
) -> None:
    """The end of an agent run, as the runner (or a reaper) records it."""
    assert agent_run_id is not None
    with factory() as db, db.begin():
        db.execute(
            sa.update(AgentRun)
            .where(AgentRun.id == agent_run_id)
            .values(
                status=status,
                error=error,
                input_tokens=tokens,
                output_tokens=0,
                finished_at=at or now(),
            )
        )


def set_agent(
    factory: sessionmaker[Session], agent_run_id: uuid.UUID | None, **values: Any
) -> None:
    with factory() as db, db.begin():
        db.execute(sa.update(AgentRun).where(AgentRun.id == agent_run_id).values(**values))


def set_run(factory: sessionmaker[Session], run_id: uuid.UUID, **values: Any) -> None:
    with factory() as db, db.begin():
        db.execute(sa.update(IngestionRun).where(IngestionRun.id == run_id).values(**values))


def finish_step(
    factory: sessionmaker[Session],
    run_id: uuid.UUID,
    settings: Settings,
    status: AgentStatus = AgentStatus.done,
    error: str | None = None,
    tokens: int = 0,
) -> engine.Moved:
    """End the running step's agent run, then advance."""
    end_agent(factory, running(factory, run_id).agent_run_id, status, error, tokens)
    return advance(factory, run_id, settings)


def run_to_the_end(
    factory: sessionmaker[Session], run_id: uuid.UUID, settings: Settings
) -> IngestionRun:
    """Every step ends `done`, one after the other."""
    advance(factory, run_id, settings)
    while run_of(factory, run_id).status.value == "running":
        finish_step(factory, run_id, settings)
    return run_of(factory, run_id)


def add_item(
    factory: sessionmaker[Session], user: uuid.UUID, step: IngestionRunStep, key: str = "blocks:a"
) -> None:
    """One stored item of a step, as the write tool stores it."""
    with factory() as db, db.begin():
        db.add(
            IngestionItem(
                user_id=user,
                document_id=step.document_id,
                step_id=step.id,
                section="facts",
                key=key,
                action="add",
                value={"title": "a", "text": "Document"},
                value_hash="0" * 64,
                citations=[{"start": 0, "end": 8, "excerpt": "Document"}],
            )
        )
        db.execute(
            sa.update(IngestionRunStep)
            .where(IngestionRunStep.id == step.id)
            .values(items_count=IngestionRunStep.items_count + 1)
        )


def items_of(factory: sessionmaker[Session], run_id: uuid.UUID) -> int:
    with factory() as db:
        return int(
            db.scalar(
                sa.select(sa.func.count())
                .select_from(IngestionItem)
                .join(IngestionRunStep, IngestionRunStep.id == IngestionItem.step_id)
                .where(IngestionRunStep.ingestion_run_id == run_id)
            )
            or 0
        )


def events_of(factory: sessionmaker[Session], agent_run_id: uuid.UUID | None) -> int:
    with factory() as db:
        return int(
            db.scalar(
                sa.select(sa.func.count())
                .select_from(AgentEvent)
                .where(AgentEvent.run_id == agent_run_id)
            )
            or 0
        )
