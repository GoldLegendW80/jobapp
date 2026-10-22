"""Synthetic mailbox through the real MCP2.3 transport and one-use provider boundary."""

import asyncio
import base64
from datetime import UTC, datetime
from uuid import uuid4

import httpx

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

    asyncio.run(scenario())
