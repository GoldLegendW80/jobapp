"""Helpers of the agent run tests: users, jobs, credentials and runs straight in the database,
and a runner context on the fake backend (no model, no Redis)."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials, definitions, runs
from app.agents.crypto import Keyring
from app.agents.fake_backend import FakeBackend
from app.agents.models import AgentEvent, AgentRun, CredentialKind, ModelCredential
from app.agents.runner import RunContext
from app.config import Settings
from app.identity.models import Role
from app.jobs import service as jobs_service
from tests.agents.conftest import API_KEY
from tests.identity.api_helpers import make_user


def now() -> datetime:
    return datetime.now(UTC)


def add_user(factory: sessionmaker[Session], email: str) -> uuid.UUID:
    return make_user(factory, email=email, name=email.split("@")[0], role=Role.member).id


def add_job(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    company: str = "Acme",
    title: str = "GenAI Engineer",
    description: str = "Build agents.",
) -> uuid.UUID:
    with factory() as db, db.begin():
        job = jobs_service.create_job(
            db,
            user_id,
            jobs_service.NewJob(company=company, title=title, description=description),
            now(),
        )
        return job.id


def save_credential(
    factory: sessionmaker[Session],
    keyring: Keyring,
    user_id: uuid.UUID,
    secret: str = API_KEY,
    kind: CredentialKind = CredentialKind.anthropic_api_key,
) -> None:
    with factory() as db, db.begin():
        credentials.save(db, keyring, user_id, kind, secret, now())


def credential_row(factory: sessionmaker[Session], user_id: uuid.UUID) -> ModelCredential | None:
    with factory() as db:
        return db.get(ModelCredential, user_id)


def new_run(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    data: dict[str, Any] | None = None,
    job_id: uuid.UUID | None = None,
    agent: str = "echo",
) -> uuid.UUID:
    with factory() as db, db.begin():
        run = runs.create_run(
            db,
            user_id,
            agent,
            job_id,
            data if data is not None else {"message": "hello"},
            definitions.DEFAULT_MODEL,
            now(),
        )
        return run.id


def run_row(factory: sessionmaker[Session], run_id: uuid.UUID) -> AgentRun:
    with factory() as db:
        row = db.get(AgentRun, run_id)
        assert row is not None
        return row


def events(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    with factory() as db:
        rows = db.scalars(
            sa.select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.seq)
        ).all()
        seqs = [row.seq for row in rows]
        assert seqs == list(range(1, len(rows) + 1)), f"seq has gaps: {seqs}"
        return [(row.kind, row.data) for row in rows]


def kinds(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[str]:
    return [kind for kind, _ in events(factory, run_id)]


@dataclass
class Backends:
    """A backend factory that keeps every backend it made, for assertions."""

    speed: float = 0.01
    made: list[FakeBackend] = field(default_factory=list)

    def __call__(self) -> FakeBackend:
        backend = FakeBackend(speed=self.speed)
        self.made.append(backend)
        return backend


def context(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring | None,
    backends: Callable[[], Any] | None = None,
    **agent_changes: Any,
) -> RunContext:
    agents = None
    if agent_changes:
        agents = {"echo": replace(definitions.ECHO, **agent_changes)}
    return RunContext(
        settings=settings,
        sessions=factory,
        clock=now,
        keyring=keyring,
        backend_factory=backends or Backends(),
        cancel_poll_s=0.02,
        interrupt_grace_s=1.0,
        agents=agents,
    )


def execute(rc: RunContext, run_id: uuid.UUID) -> Any:
    from app.agents import runner

    return asyncio.run(runner.execute(rc, run_id))
