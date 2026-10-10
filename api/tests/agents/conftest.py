"""Fixtures of the agents tests: the identity fixtures (real Postgres at head, emptied after each
test, frozen clock, cookie-explicit client), an app holding a CREDENTIALS_KEY, and a fake
Anthropic API behind the credential check (no request ever leaves the process)."""

import base64
import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.agents.verify import Verifier
from app.main import create_app
from tests.conftest import make_settings
from tests.identity import conftest as identity
from tests.identity.api_helpers import Api, Clock, make_user

migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory
db = identity.db
clock = identity.clock

ALICE = "alice@example.org"
BOB = "bob@example.org"
# Made up values of the right shape: never a real credential.
API_KEY = "sk-ant-api03-" + "Ab1_" * 20 + "WXyz"
OTHER_KEY = "sk-ant-api03-" + "Zz9-" * 20 + "Last"
OAUTH = "sk-ant-oat01-" + "Oa7_" * 20 + "Tok3"


def new_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


@pytest.fixture
def key() -> str:
    return new_key()


Handler = Callable[[httpx2.Request], httpx2.Response]


@dataclass
class FakeAnthropic:
    """The Anthropic API as the credential check sees it: answers with `handler` (200 with an
    empty model list by default) and records every request."""

    requests: list[httpx2.Request] = field(default_factory=list)
    handler: Handler | None = None

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.handler is not None:
            return self.handler(request)
        return httpx2.Response(
            200, json={"data": [], "has_more": False, "first_id": None, "last_id": None}
        )

    def answer(self, status: int, headers: dict[str, str] | None = None) -> None:
        body = {"type": "error", "error": {"type": "error", "message": "refused"}}
        self.handler = lambda _: httpx2.Response(status, json=body, headers=headers)

    def fail(self, exc: Exception) -> None:
        def raise_(_: httpx2.Request) -> httpx2.Response:
            raise exc

        self.handler = raise_


@pytest.fixture
def anthropic_api() -> FakeAnthropic:
    return FakeAnthropic()


def build_app(
    url: str, clock: Clock, fake: FakeAnthropic, key: str | None, previous: str | None = None
) -> FastAPI:
    app = create_app(
        make_settings(url, credentials_key=key, credentials_key_previous=previous), clock=clock
    )
    app.state.model_verifier = Verifier(transport=httpx2.MockTransport(fake))
    return app


@pytest.fixture
def app(migrated_url: str, clock: Clock, anthropic_api: FakeAnthropic, key: str) -> FastAPI:
    return build_app(migrated_url, clock, anthropic_api, key)


def client_for(app: FastAPI) -> Iterator[Api]:
    with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="*")) as client:
        yield Api(client)


@pytest.fixture
def api(app: FastAPI) -> Iterator[Api]:
    yield from client_for(app)


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> str:
    """Alice's session token."""
    make_user(factory, email=ALICE, name="Alice")
    return api.login_token(ALICE)


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> str:
    make_user(factory, email=BOB, name="Bob")
    return api.login_token(BOB)
