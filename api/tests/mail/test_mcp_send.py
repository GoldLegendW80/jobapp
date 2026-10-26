"""Synthetic mailbox through the real MCP2.3 transport and one-use provider boundary."""

import asyncio
import base64
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.agents.crypto import Keyring
from app.gmailmcp.server import Service, build_app
from app.mail import send_key
from app.mail.mcp_client import GmailMcp
from app.mail.models import TOKEN_KIND, MailAccount
from tests.mail.conftest import ALICE, BOB, make_client, settings_for

NOW = datetime(2026, 10, 22, tzinfo=UTC)
SECRET = "synthetic-mail-send-key-" * 3


class Keys:
    def __init__(self) -> None:
        self.used: set[str] = set()

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        assert nx
        assert ex == 600
        assert value == "1"
        if key in self.used:
            return False
        self.used.add(key)
        return True


def test_mcp_transport_send_and_replay(fake, clock, sleeps) -> None:
    async def scenario() -> None:
        settings = settings_for(
            google_oauth_client_id=fake.client_id,
            google_oauth_client_secret=fake.client_secret,
            gmail_mcp_url="http://gmailmcp.test/mcp",
            mail_send_key=SECRET,
        )
        oauth = make_client(fake, settings, clock, sleeps)
        ring = Keyring.from_text(base64.b64encode(b"x" * 32).decode())
        user = uuid4()
        sealed = ring.encrypt(fake.grant(ALICE), user, TOKEN_KIND)
        account = MailAccount(
            user_id=user,
            email=ALICE,
            refresh_token_encrypted=sealed.ciphertext,
            key_version=sealed.key_version,
        )
        provider = Service(settings, Keys(), transport=fake.transport(), now=lambda: NOW)
        app = build_app(provider)
        client = GmailMcp(settings, oauth, ring, transport=httpx.ASGITransport(app=app))
        async with app.router.lifespan_context(app):
            profile = await client.get_profile(account)
            assert profile["emailAddress"] == ALICE
            assert len(fake.calls_of("getProfile")) == 1
            raw = b"To: e2e-recipient@example.org\r\nSubject: Application\r\n\r\nHello"
            key = send_key.mint(SECRET, uuid4(), user, ALICE, uuid4(), None, "hash", raw, NOW)
            args = {"send_key": key, "raw": base64.urlsafe_b64encode(raw).decode()}
            # This lower boundary test is the sole test-only bypass of mail.gate.send.
            answer = await client.call(account, "send_message", args)
            assert answer["result"] == "sent", answer
            assert (await client.call(account, "send_message", args))["result"] == "unknown"
            assert len(fake.sent) == 1
            account.email = BOB
            assert (await client.get_profile(account))["error"] == "not_connected"
            account.email = ALICE
            provider.budget.limit = provider.budget.used(str(user))
            limited = send_key.mint(SECRET, uuid4(), user, ALICE, uuid4(), None, "hash", raw, NOW)
            refused = await client.call(account, "send_message", {**args, "send_key": limited})
            assert refused == {"result": "not_sent", "code": "gmail_quota"}
            assert len(fake.sent) == 1

    asyncio.run(scenario())


class DownKeys(Keys):
    """Redis refusing the connection: the `SET NX` raises."""

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        raise ConnectionError("synthetic: redis is down")


async def _send_twice(fake, clock, sleeps, keys: Keys, key_of, *, secret: str | None = SECRET):
    """Mint (or forge) a key, send it twice through the real server, answer both replies."""
    settings = settings_for(
        google_oauth_client_id=fake.client_id,
        google_oauth_client_secret=fake.client_secret,
        gmail_mcp_url="http://gmailmcp.test/mcp",
        mail_send_key=secret,
    )
    oauth = make_client(fake, settings, clock, sleeps)
    ring = Keyring.from_text(base64.b64encode(b"x" * 32).decode())
    user = uuid4()
    sealed = ring.encrypt(fake.grant(ALICE), user, TOKEN_KIND)
    account = MailAccount(
        user_id=user,
        email=ALICE,
        refresh_token_encrypted=sealed.ciphertext,
        key_version=sealed.key_version,
    )
    provider = Service(settings, keys, transport=fake.transport(), now=lambda: NOW)
    app = build_app(provider)
    client = GmailMcp(settings, oauth, ring, transport=httpx.ASGITransport(app=app))
    raw = b"To: e2e-recipient@example.org\r\nSubject: Application\r\n\r\nHello"
    args = {"send_key": key_of(user, raw), "raw": base64.urlsafe_b64encode(raw).decode()}
    async with app.router.lifespan_context(app):
        # This lower boundary test is the sole test-only bypass of mail.gate.send.
        first = await client.call(account, "send_message", args)
        second = await client.call(account, "send_message", args)
    return first, second


def _minted(user, raw):
    return send_key.mint(SECRET, uuid4(), user, ALICE, uuid4(), None, "hash", raw, NOW)


def test_redis_down_sends_nothing_and_marks_nothing(fake, clock, sleeps) -> None:
    keys = DownKeys()
    first, second = asyncio.run(_send_twice(fake, clock, sleeps, keys, _minted))
    assert first == {"result": "not_sent", "code": "mail_server_down"}
    assert second == first
    assert fake.calls_of("messages.send") == []
    assert fake.sent == []
    assert keys.used == set()


@pytest.mark.parametrize("kind", ["500", "503", "timeout"])
def test_gmail_5xx_or_timeout_is_unknown_and_burns_the_key(fake, clock, sleeps, kind) -> None:
    fake.fail("messages.send", kind)
    keys = Keys()
    first, second = asyncio.run(_send_twice(fake, clock, sleeps, keys, _minted))
    assert first == {"result": "unknown"}
    # The key was marked before the one call: the same key never reaches Gmail again.
    assert second == {"result": "unknown", "code": "key_used"}
    assert len(fake.calls_of("messages.send")) == 1
    assert fake.sent == []
    assert len(keys.used) == 1
    assert next(iter(keys.used)).startswith("mailmcp:send:")


def _forged_other_secret(user, raw):
    return send_key.mint(
        "another-synthetic-key-" * 3, uuid4(), user, ALICE, uuid4(), None, "h", raw, NOW
    )


def _forged_tampered(user, raw):
    key = _minted(user, raw)
    return key[:-2] + ("AA" if not key.endswith("AA") else "BB")


def _forged_other_user(user, raw):
    return send_key.mint(SECRET, uuid4(), uuid4(), ALICE, uuid4(), None, "hash", raw, NOW)


@pytest.mark.parametrize(
    ("key_of", "secret"),
    [
        (_minted, None),
        (lambda user, raw: "", SECRET),
        (lambda user, raw: "not-a-key", SECRET),
        (_forged_other_secret, SECRET),
        (_forged_tampered, SECRET),
        (_forged_other_user, SECRET),
    ],
    ids=["no-key-file", "empty", "garbage", "other-secret", "tampered", "other-user"],
)
def test_missing_or_forged_key_is_refused_before_redis_and_gmail(
    fake, clock, sleeps, key_of, secret
) -> None:
    keys = Keys()
    first, second = asyncio.run(_send_twice(fake, clock, sleeps, keys, key_of, secret=secret))
    assert first == {"result": "not_sent", "code": "not_authorised"}
    assert second == first
    assert fake.calls_of("messages.send") == []
    assert fake.sent == []
    assert keys.used == set()
