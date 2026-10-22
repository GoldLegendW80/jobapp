"""The app's account-row-bound client. No caller supplies identity headers independently."""

import json
from typing import Any

import httpx

from app.agents.crypto import Keyring
from app.config import Settings
from app.mail import log
from app.mail.google import GoogleClient, Grant
from app.mail.models import TOKEN_KIND, MailAccount


class MailServerDownError(RuntimeError):
    pass


class MailRequestUnknownError(RuntimeError):
    pass


class GmailMcp:
    def __init__(
        self,
        settings: Settings,
        oauth: GoogleClient,
        keyring: Keyring,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings, self.oauth, self.keyring, self.transport = (
            settings,
            oauth,
            keyring,
            transport,
        )

    async def call(
        self, account: MailAccount, name: str, arguments: dict[str, Any], *, agent: bool = False
    ) -> dict[str, Any]:
        token = self.keyring.decrypt(
            account.refresh_token_encrypted, account.key_version, account.user_id, TOKEN_KIND
        )
        token = token.plaintext
        grant = Grant(str(account.user_id), token)
        for attempt in range(2):
            access = await self.oauth.access_token(grant)
            headers = {
                "Authorization": f"Bearer {access}",
                "X-Jobapp-User": str(account.user_id),
                "X-Jobapp-Address": account.email,
                "X-Jobapp-Agent": "1" if agent else "0",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            }
            # Stateless Streamable HTTP retains the 2025 handshake. Never follows redirects.
            with log.hide(access, token, account.email), log.quiet():
                async with httpx.AsyncClient(
                    transport=self.transport, timeout=30, follow_redirects=False
                ) as http:
                    try:
                        initial = await http.post(
                            self.settings.gmail_mcp_url,
                            headers=headers,
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "initialize",
                                "params": {
                                    "protocolVersion": "2025-06-18",
                                    "capabilities": {},
                                    "clientInfo": {"name": "jobapp", "version": "1"},
                                },
                            },
                        )
                        initial.raise_for_status()
                        answer = await http.post(
                            self.settings.gmail_mcp_url,
                            headers=headers,
                            json={
                                "jsonrpc": "2.0",
                                "id": 2,
                                "method": "tools/call",
                                "params": {"name": name, "arguments": arguments},
                            },
                        )
                        answer.raise_for_status()
                        found = answer.json()["result"]
                        result = found.get("structuredContent")
                        if result is None:
                            result = json.loads(found["content"][0]["text"])
                        if not isinstance(result, dict):
                            raise ValueError("invalid_answer")
                    except httpx.ConnectError:
                        raise MailServerDownError("mail_server_down") from None
                    except (httpx.HTTPError, ValueError, KeyError, IndexError):
                        raise MailRequestUnknownError("unknown") from None
            if result.get("error") != "token_expired" or attempt:
                return result
            self.oauth.forget(str(account.user_id))
        return {"error": "needs_reconnect"}

    async def get_profile(self, account: MailAccount) -> dict[str, Any]:
        return await self.call(account, "get_profile", {})

    async def list_messages(
        self,
        account: MailAccount,
        *,
        q: str = "",
        page_token: str | None = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        return await self.call(
            account,
            "list_messages",
            {
                "q": q,
                "page_token": page_token,
                "max_results": max_results,
            },
        )

    async def get_message(
        self,
        account: MailAccount,
        id: str,
        *,
        format: str = "full",
        metadata_headers: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self.call(
            account,
            "get_message",
            {
                "id": id,
                "format": format,
                "metadata_headers": metadata_headers or [],
            },
        )

    async def get_thread(
        self, account: MailAccount, id: str, *, format: str = "full"
    ) -> dict[str, Any]:
        return await self.call(account, "get_thread", {"id": id, "format": format})

    async def list_history(
        self,
        account: MailAccount,
        start_history_id: str,
        *,
        history_types: list[str] | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        return await self.call(
            account,
            "list_history",
            {
                "start_history_id": start_history_id,
                "history_types": history_types or [],
                "page_token": page_token,
            },
        )
