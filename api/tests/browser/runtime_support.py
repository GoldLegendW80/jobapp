"""The agents runtime of the finder tests: real Postgres for the runs and the credential (the
jobs fixtures, re-exported for the importers), the fake backend's recorded streams in place of the
model (app/agents/fake_streams/browser_finder*.json and the website streams)."""

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, CredentialKind
from app.agents.runner import RunContext
from tests.agents.conftest import API_KEY, new_key
from tests.agents.run_helpers import Backends
from tests.conftest import make_settings
from tests.jobs.conftest import (  # noqa: F401  (fixtures)
    alice,
    bob,
    clock,
    db,
    engine,
    factory,
    migrated_url,
    one_connection,
)


class Runtime:
    def __init__(self, factory_: sessionmaker[Session], url: str) -> None:
        key = new_key()
        self.factory = factory_
        self.settings = make_settings(url, credentials_key=key, agents_fake_backend=True)
        self.keyring = Keyring.from_text(key)

    def give_key(self, user_id: uuid.UUID) -> None:
        with self.factory() as session:
            credentials.save(
                session,
                self.keyring,
                user_id,
                CredentialKind.anthropic_api_key,
                API_KEY,
                datetime.now(UTC),
            )
            session.commit()

    def context(self) -> RunContext:
        return RunContext(
            settings=self.settings,
            sessions=self.factory,
            clock=lambda: datetime.now(UTC),
            keyring=self.keyring,
            backend_factory=Backends(speed=0.0),
            cancel_poll_s=0.02,
            interrupt_grace_s=1.0,
        )


@pytest.fixture
def runtime(factory: sessionmaker[Session], migrated_url: str) -> Runtime:  # noqa: F811
    return Runtime(factory, migrated_url)


def runs_of(factory_: sessionmaker[Session], user: uuid.UUID) -> list[AgentRun]:
    with factory_() as session:
        return list(session.scalars(sa.select(AgentRun).where(AgentRun.user_id == user)))
