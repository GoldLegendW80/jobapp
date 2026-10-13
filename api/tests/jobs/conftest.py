"""Fixtures of the jobs tests: the identity fixtures (real Postgres at head, users emptied after
each test, which cascades to every jobs table; the app with a frozen clock and the
cookie-explicit client) and two users."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tests.identity import conftest as identity
from tests.identity.api_helpers import make_user

# The identity fixtures, made visible to the tests of this folder.
migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory
db = identity.db
clock = identity.clock
app = identity.app
api = identity.api
one_connection = identity.one_connection

NOW = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)


def new_user(factory: sessionmaker[Session], email: str) -> uuid.UUID:
    # A fixed hash: these tests never sign in, and argon2 would only slow them down.
    return make_user(factory, email=email, name=email.split("@")[0], password_hash="h").id


@pytest.fixture
def alice(factory: sessionmaker[Session]) -> uuid.UUID:
    return new_user(factory, "alice@example.org")


@pytest.fixture
def bob(factory: sessionmaker[Session]) -> uuid.UUID:
    return new_user(factory, "bob@example.org")
