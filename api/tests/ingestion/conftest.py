"""Fixtures of the ingestion tests: the identity fixtures (real Postgres at head, emptied after
each test; the app with a frozen clock; the cookie-explicit client) and two signed-in users.
Every document in these tests is made up: no real person, no real credential.

The app of these tests has a queue that records what it is given (`jobs_queue`) in place of the
one that talks to Redis: an upload of a PDF queues its reading, and most tests have no Redis
and no worker. The tests of the queue itself (test_jobs_worker.py) run the real one."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.ingestion import documents
from app.main import create_app
from tests.conftest import make_settings
from tests.identity import conftest as identity
from tests.identity.api_helpers import Api, Clock, make_user

# The identity fixtures, made visible to the tests of this folder.
migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory
db = identity.db
clock = identity.clock
api = identity.api


@dataclass
class RecordingQueue:
    """Stands for app.queue.Queue: remembers the jobs it was given, refuses an id it already
    holds as Redis does, and is unreachable when `down`."""

    jobs: list[dict[str, Any]] = field(default_factory=list)
    down: bool = False

    async def enqueue(self, function: str, *args: Any, queue: str, job_id: str) -> bool:
        if self.down:
            raise queues.QueueUnavailableError("down")
        if any(job["job_id"] == job_id for job in self.jobs):
            return False
        self.jobs.append({"function": function, "args": args, "queue": queue, "job_id": job_id})
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
def jobs_queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def app(migrated_url: str, clock: Clock, jobs_queue: RecordingQueue) -> FastAPI:
    made = create_app(make_settings(migrated_url), clock=clock)
    made.state.queue = jobs_queue
    return made


ALICE = "alice@example.org"
BOB = "bob@example.org"
CAROL = "carol@example.org"


def signed_in(factory: sessionmaker[Session], api: Api, email: str) -> str:
    make_user(factory, email=email, name=email.split("@")[0])
    return api.login_token(email)


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> str:
    """Session token of a signed-in user."""
    return signed_in(factory, api, ALICE)


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> str:
    return signed_in(factory, api, BOB)


@pytest.fixture
def carol(factory: sessionmaker[Session], api: Api) -> str:
    return signed_in(factory, api, CAROL)


@pytest.fixture(autouse=True)
def no_delete_guard_left() -> Iterator[None]:
    """A guard registered by one test never refuses the deletions of the next."""
    before = list(documents._delete_guards)
    yield
    documents._delete_guards[:] = before
