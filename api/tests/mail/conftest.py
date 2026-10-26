"""The scaffolding of the mail module's tests (mail M1). A later task never edits this file:
it adds helpers of its own in `api/tests/mail/m<n>_support.py`.

What a test gets by naming a fixture:

- `fake`: the fake Google in process (`fake_gmail.FakeGoogle`), seeded with the two mailboxes
  of fixtures/mailboxes: `alice` (user A) and `bob` (user B, the same companies). `fake.seed(
  "bulk")` adds the generated mailbox of about 300 messages.
- `mail_settings`: settings of APP_ENV=test whose three Google addresses are the fake's.
- `clock`, `sleeps`: a clock that moves only when told (`clock.advance(s)`), and the list of
  the waits the client asked for (each one moves the clock: no test ever sleeps).
- `google`: `app.mail.google.GoogleClient` on the fake, with that clock and no jitter.
- `account_a`, `account_b`: a connected account each, on the Google side (`Connected`: the user
  id, the address, the `Grant` to hand to the client). The row of `mail_accounts` comes with M4.
- `redis_url`: a Redis for the canary (TEST_REDIS_URL or REDIS_URL, else a throwaway container,
  else skip).

Run for every test of the folder: the mail variables are removed from the environment, and
`no_real_google` fails a test that tried to resolve a real Google host.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from app.config import Settings
from app.gmailmcp.google import GmailGoogle
from app.mail.google import Grant
from app.mail.models import TOKEN_KIND
from tests.mail import canary
from tests.mail.fake_gmail import ALL_SCOPES, FakeGoogle


class GoogleClient(GmailGoogle):
    async def connection_profile(self, account: Any, keyring: Any) -> Any:
        opened = keyring.decrypt(
            account.refresh_token_encrypted, account.key_version, account.user_id, TOKEN_KIND
        )
        return await self.get_profile(Grant(str(account.user_id), opened.plaintext))


FAKE_ORIGIN = "http://fake-google.test"
ALICE = "e2e-alice@example.org"
BOB = "e2e-bob@example.org"
BULK = "e2e-bulk@example.org"
MAIL_ENV = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_AUTH_URL",
    "GOOGLE_TOKEN_BASE_URL",
    "GOOGLE_API_BASE_URL",
    "MAIL_AUTO_APPLY_CONFIDENCE",
    "MAIL_SEND_DAILY_LIMIT",
    "MAIL_SEND_COMPANY_DAILY_LIMIT",
    "MAIL_AUTO_RUNS_PER_DAY",
    "MAIL_STORAGE_MAX_MESSAGES",
    "MAIL_STORAGE_MAX_BYTES",
    "MAIL_SENDS_KEEP_DAYS",
)
# Never connected to: settings need a database address to exist.
NO_DATABASE = "postgresql+psycopg://nobody:nothing@db.invalid:5432/none"


def run[T](scenario: Coroutine[Any, Any, T]) -> T:
    """Run one async scenario to its end in a loop of its own."""
    return asyncio.run(scenario)


class Clock:
    """Time that moves only when a test says so."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass(frozen=True)
class Connected:
    user_id: str
    email: str
    grant: Grant


def connect(fake: FakeGoogle, email: str, scopes: tuple[str, ...] = ALL_SCOPES) -> Connected:
    """A user of the app whose Gmail `email` is connected, on the Google side."""
    user_id = str(uuid.uuid4())
    return Connected(user_id, email, Grant(user_id, fake.grant(email, scopes)))


def settings_for(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": NO_DATABASE,
        "app_env": "test",
        "google_auth_url": f"{FAKE_ORIGIN}/o/oauth2/v2/auth",
        "google_token_base_url": FAKE_ORIGIN,
        "google_api_base_url": FAKE_ORIGIN,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def clean_mail_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in MAIL_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session", autouse=True)
def _google_blocked() -> Iterator[None]:
    restore = canary.block_google()
    yield
    restore()


@pytest.fixture(autouse=True)
def no_real_google(_google_blocked: None) -> Iterator[None]:
    """Fails the test that asked for a real Google host, whatever became of the exception."""
    before = len(canary.reached)
    yield
    asked = canary.reached[before:]
    del canary.reached[before:]
    if asked:
        pytest.fail(f"this test tried to reach real Google hosts: {sorted(set(asked))}")


@pytest.fixture
def fake() -> FakeGoogle:
    google = FakeGoogle()
    google.seed("alice")
    google.seed("bob")
    return google


@pytest.fixture
def mail_settings(fake: FakeGoogle) -> Settings:
    return settings_for(
        google_oauth_client_id=fake.client_id, google_oauth_client_secret=fake.client_secret
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def sleeps(clock: Clock) -> list[float]:
    return []


@pytest.fixture
def google(
    fake: FakeGoogle, mail_settings: Settings, clock: Clock, sleeps: list[float]
) -> GoogleClient:
    return make_client(fake, mail_settings, clock, sleeps)


def make_client(
    fake: FakeGoogle,
    settings: Settings,
    clock: Clock,
    sleeps: list[float],
    jitter: Callable[[], float] = lambda: 0.0,
    transport: Any = None,
) -> GoogleClient:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    return GoogleClient(
        settings,
        transport=transport or fake.transport(),
        clock=clock,
        sleep=sleep,
        jitter=jitter,
    )


@pytest.fixture
def account_a(fake: FakeGoogle) -> Connected:
    return connect(fake, ALICE)


@pytest.fixture
def account_b(fake: FakeGoogle) -> Connected:
    return connect(fake, BOB)


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    # The gate (and CI) give a Redis in REDIS_URL; elsewhere a throwaway container.
    external = os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")
    if external:
        yield external
        return
    from tests.pg import docker_available
    from tests.redis_server import start_redis

    if not docker_available():
        pytest.skip("Docker is not available and TEST_REDIS_URL is not set")
    server = start_redis()
    yield server.url
    server.remove()
