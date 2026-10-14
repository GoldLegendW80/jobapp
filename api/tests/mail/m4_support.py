"""What the tests of mail M4 share: the app on the in process fake Google, two signed in
users, and the browser's side of the OAuth round trip. conftest.py (M1) is never edited: a test
file imports the fixtures it needs from here.

Every account is `e2e-*@example.org`; every key, secret and token is made up by the test or by
the fake. No test sleeps: the Google client's waits move a clock, and a lock wait is bounded
by `locks.LOCK_TIMEOUT_MS`, which a test sets to a few milliseconds.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.agents.crypto import Keyring
from app.config import Settings
from app.main import create_app
from tests.conftest import make_settings
from tests.identity import conftest as identity
from tests.identity.api_helpers import Api, make_user
from tests.identity.api_helpers import Clock as AppClock
from tests.mail.conftest import ALICE, BOB, FAKE_ORIGIN, Clock, make_client
from tests.mail.fake_gmail import FakeGoogle

# The identity fixtures (real Postgres at head, emptied after each test), made visible here.
migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory

USER_A = "e2e-mail-a@example.org"
USER_B = "e2e-mail-b@example.org"
REDIRECT = "http://localhost/api/mail/oauth/callback"
FROZEN = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
READS = ("messages.list", "messages.get", "history.list", "threads.get", "attachments.get")
MAIL_TABLES = (
    "mail_accounts",
    "mail_threads",
    "mail_messages",
    "mail_classifications",
    "mail_confirmed_domains",
    "mail_assist_batches",
)

__all__ = ["ALICE", "BOB", "FROZEN", "MAIL_TABLES", "READS", "REDIRECT", "USER_A", "USER_B"]


class MemoryVerifiers:
    """Stands for oauth.RedisVerifiers: read once, and unreachable when `down`."""

    def __init__(self) -> None:
        self.held: dict[str, str] = {}
        self.down = False

    async def put(self, nonce: str, verifier: str) -> None:
        from app.mail.oauth import StoreUnavailableError

        if self.down:
            raise StoreUnavailableError
        self.held[nonce] = verifier

    async def take(self, nonce: str) -> str | None:
        from app.mail.oauth import StoreUnavailableError

        if self.down:
            raise StoreUnavailableError
        return self.held.pop(nonce, None)


class Lines(logging.Handler):
    """Every log record of the process, as text with all its fields."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.getMessage()} {record.__dict__!r}")

    @property
    def text(self) -> str:
        return "\n".join(self.records)


def settings_of(database_url: str, fake: FakeGoogle, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "secret_key": "e2e-only-not-a-secret-" + "0" * 24,
        "google_oauth_client_id": fake.client_id,
        "google_oauth_client_secret": fake.client_secret,
        "google_auth_url": f"{FAKE_ORIGIN}/o/oauth2/v2/auth",
        "google_token_base_url": FAKE_ORIGIN,
        "google_api_base_url": FAKE_ORIGIN,
        "google_oauth_redirect_uri": REDIRECT,
    }
    values.update(overrides)
    return make_settings(database_url, **values)


@pytest.fixture
def app_clock() -> AppClock:
    return AppClock(FROZEN)


@pytest.fixture
def keyring() -> Keyring:
    return Keyring(os.urandom(32))


@pytest.fixture
def verifiers() -> MemoryVerifiers:
    return MemoryVerifiers()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def app(
    migrated_url: str,
    fake: FakeGoogle,
    app_clock: AppClock,
    keyring: Keyring,
    verifiers: MemoryVerifiers,
    sleeps: list[float],
) -> FastAPI:
    settings = settings_of(migrated_url, fake)
    made = create_app(settings, clock=app_clock)
    made.state.mail_google = make_client(fake, settings, Clock(), sleeps)
    made.state.mail_verifiers = verifiers
    made.state.credentials_keyring = keyring
    return made


@pytest.fixture
def api(app: FastAPI) -> Iterator[Api]:
    with TestClient(app, follow_redirects=False) as client:
        yield Api(client)


@pytest.fixture
def lines(app: FastAPI) -> Iterator[Lines]:
    """After the app is made: its logging setup replaces the handlers of the root logger."""
    handler = Lines()
    root = logging.getLogger()
    before = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield handler
    root.removeHandler(handler)
    root.setLevel(before)


def sign_in(factory: sessionmaker[Session], api: Api, email: str) -> tuple[uuid.UUID, str]:
    user = make_user(factory, email=email, name=email.split("@")[0])
    return user.id, api.login_token(email)


@pytest.fixture
def user_a(factory: sessionmaker[Session], api: Api) -> tuple[uuid.UUID, str]:
    return sign_in(factory, api, USER_A)


@pytest.fixture
def user_b(factory: sessionmaker[Session], api: Api) -> tuple[uuid.UUID, str]:
    return sign_in(factory, api, USER_B)


# --- the browser's side of the round trip ---------------------------------------------------


def start(api: Api, token: str) -> str:
    """GET /oauth/start: the consent address the browser is sent to."""
    answer = api.get("/api/mail/oauth/start", token=token)
    assert answer.status_code == 303, answer.text
    assert answer.headers["cache-control"] == "no-store"
    return str(answer.headers["location"])


def decide(
    fake: FakeGoogle,
    consent: str,
    account: str = ALICE,
    *,
    allow: bool = True,
    untick: tuple[str, ...] = (),
) -> str:
    """What the person does on the fake's consent page: the callback address Google answers."""
    asked = {name: values[-1] for name, values in parse_qs(urlsplit(consent).query).items()}
    form = [
        (name, asked.get(name, ""))
        for name in ("client_id", "redirect_uri", "state", "code_challenge", "scope")
    ]
    form += [("account", account), ("decision", "allow" if allow else "deny")]
    form += [
        ("granted", scope)
        for scope in asked["scope"].split()
        if scope != "openid" and scope not in untick
    ]
    reply = fake.handle("POST", "/o/oauth2/v2/auth/decision", {}, urlencode(form).encode())
    assert reply.status == 302, reply.body
    back = urlsplit(reply.headers["Location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
    return f"{back.path}?{back.query}"


def connect(api: Api, fake: FakeGoogle, token: str, account: str = ALICE, **how: Any) -> str:
    """The whole round trip; the value of `/account?gmail=` it ends on."""
    return outcome(api.get(decide(fake, start(api, token), account, **how), token=token))


def outcome(answer: httpx.Response) -> str:
    assert answer.status_code == 303, answer.text
    assert answer.headers["cache-control"] == "no-store"
    location = str(answer.headers["location"])
    assert location.startswith("/account?gmail="), location
    return location.removeprefix("/account?gmail=")


def count(factory: sessionmaker[Session], table: str, user_id: uuid.UUID | None = None) -> int:
    assert table in MAIL_TABLES or table in ("proposals", "job_events", "jobs")
    where = " WHERE user_id = :u" if user_id is not None else ""
    with factory() as db:
        return int(
            db.execute(sa.text(f"SELECT count(*) FROM {table}{where}"), {"u": user_id}).scalar_one()
        )


def endpoints(fake: FakeGoogle) -> list[str]:
    return [call["endpoint"] for call in fake.calls_of()]
