"""Fixtures of the ingestion tests: the identity fixtures (real Postgres at head, emptied after
each test; the app with a frozen clock; the cookie-explicit client) and two signed-in users.
Every document in these tests is made up: no real person, no real credential."""

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion import documents
from tests.identity import conftest as identity
from tests.identity.api_helpers import Api, make_user

# The identity fixtures, made visible to the tests of this folder.
migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory
db = identity.db
clock = identity.clock
app = identity.app
api = identity.api

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
