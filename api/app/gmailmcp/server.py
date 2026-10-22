"""Account-bound Gmail tools. Only the send gate can mint their one-use send capability."""

import base64
import contextvars
import hashlib
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from app.config import Settings
from app.gmailmcp.google import GmailGoogle
from app.mail import log, send_key
from app.mail.errors import GoogleError
from app.mail.google import JSON_MAX_BYTES, MESSAGE_MAX_BYTES, TIMEOUT, Budget, Grant

_headers: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("gmailmcp_headers")
MAX_RAW_BYTES = 12 * 1024 * 1024


class AccessGoogle(GmailGoogle):
    """A per-call client: the supplied access token is never cached or refreshed here."""

    access: str = ""
    refresh_on_401 = False

    async def _token(self, grant: Grant) -> str:
        return self.access


class Service:
    def __init__(
        self,
        settings: Settings,
        redis: Any,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings, self.redis, self.transport = settings, redis, transport
        self.clock, self.now = clock, now
        self.budget, self.agent_budget = Budget(clock, 3000), Budget(clock, 1000)
        self.checked: dict[str, tuple[str, float]] = {}

    async def client(self, headers: dict[str, str]) -> tuple[AccessGoogle, Grant]:
        uid, address = headers.get("x-jobapp-user", ""), headers.get("x-jobapp-address", "")
        token = headers.get("authorization", "").removeprefix("Bearer ")
        try:
            uuid.UUID(uid)
        except ValueError:
            raise PermissionError("not_connected") from None
        if not address or not token or "@" not in address:
            raise PermissionError("not_connected")
        client = AccessGoogle(self.settings, transport=self.transport, clock=self.clock)
        client.access, client.budget = token, self.budget
        grant = Grant(uid, "")
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        cached = self.checked.get(fingerprint)
        if cached is None or self.clock() >= cached[1]:
            with log.hide(token, address):
                profile = await client.get_profile(grant)
            self.checked[fingerprint] = profile.email.casefold(), self.clock() + 60
            cached = self.checked[fingerprint]
        if cached[0] != address.casefold():
            raise PermissionError("not_connected")
        # Expired entries contain no token and are pruned on every call.
        self.checked = {k: v for k, v in self.checked.items() if v[1] > self.clock()}
        return client, grant

    async def read(
        self, name: str, args: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        try:
            client, grant = await self.client(headers)
            calls = {
                "get_profile": ("getProfile", "/profile", []),
                "list_messages": (
                    "messages.list",
                    "/messages",
                    [
                        ("q", args.get("q", "")),
                        ("maxResults", str(min(500, max(1, args.get("max_results", 100))))),
                        *(([("pageToken", args["page_token"])]) if args.get("page_token") else []),
                    ],
                ),
                "get_message": (
                    "messages.get",
                    f"/messages/{_id(args.get('id', ''))}",
                    [
                        ("format", args.get("format", "full")),
                        *[("metadataHeaders", h) for h in args.get("metadata_headers", [])],
                    ],
                ),
                "get_thread": (
                    "threads.get",
                    f"/threads/{_id(args.get('id', ''))}",
                    [
                        ("format", args.get("format", "full")),
                    ],
                ),
                "list_history": (
                    "history.list",
                    "/history",
                    [
                        ("startHistoryId", args.get("start_history_id", "")),
                        *[("historyTypes", h) for h in args.get("history_types", [])],
                        *(([("pageToken", args["page_token"])]) if args.get("page_token") else []),
                    ],
                ),
            }
            call, path, params = calls[name]
            if args.get("format", "full") not in {"full", "metadata"}:
                return {"error": "invalid_request"}
            if headers.get("x-jobapp-agent") == "1":
                from app.mail.google import UNITS

                self.agent_budget.spend(grant.user_id, UNITS[call])
            with log.hide(client.access, headers.get("x-jobapp-address")), log.quiet():
                return await client._gmail(
                    grant,
                    call,
                    path,
                    params,
                    cap=MESSAGE_MAX_BYTES if call == "messages.get" else JSON_MAX_BYTES,
                )
        except (PermissionError, ValueError, KeyError):
            return {"error": "not_connected"}
        except GoogleError as exc:
            return {"error": "token_expired" if exc.status == 401 else exc.code}

    async def send(
        self, key: str, raw: str, thread_id: str | None, headers: dict[str, str]
    ) -> dict[str, Any]:
        try:
            if len(raw) > MAX_RAW_BYTES * 2:
                return {"result": "not_sent", "code": "message_too_large"}
            data = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
            if len(data) > MAX_RAW_BYTES:
                return {"result": "not_sent", "code": "message_too_large"}
            secret = self.settings.mail_send_key
            payload = send_key.verify(
                secret.get_secret_value() if secret else None,
                key,
                headers.get("x-jobapp-user", ""),
                headers.get("x-jobapp-address", ""),
                thread_id,
                data,
                self.now(),
            )
            client, grant = await self.client(headers)
            # Atomic and durable across provider restarts, BEFORE the irreversible call.
            accepted = await self.redis.set(
                f"mailmcp:send:{payload['send_id']}", "1", nx=True, ex=600
            )
            if not accepted:
                return {"result": "unknown", "code": "key_used"}
        except GoogleError as exc:
            if exc.status == 401:
                return {"error": "token_expired"}
            return {
                "result": "not_sent",
                "code": "gmail_quota" if exc.code == "quota" else "gmail_error",
            }
        except (PermissionError, ValueError):
            return {"result": "not_sent", "code": "not_authorised"}
        except Exception:
            return {"result": "not_sent", "code": "mail_server_down"}
        try:
            self.budget.spend(grant.user_id, 100)
            body: dict[str, Any] = {"raw": raw}
            if thread_id:
                body["threadId"] = thread_id
            with log.hide(client.access, key, raw), log.quiet():
                async with client._http() as http:
                    answer = await http.post(
                        f"{client.gmail_url}/messages/send",
                        json=body,
                        headers={"Authorization": f"Bearer {client.access}"},
                        timeout=TIMEOUT,
                    )
            if 400 <= answer.status_code < 500:
                return {"result": "gmail_4xx", "status": answer.status_code}
            if answer.status_code != 200:
                return {"result": "unknown"}
            found = answer.json()
            if not isinstance(found.get("id"), str) or not found["id"]:
                return {"result": "unknown"}
            return {"result": "sent", "id": found["id"], "threadId": found.get("threadId")}
        except Exception:
            return {"result": "unknown"}


def _id(value: str) -> str:
    if value and not all(c.isalnum() or c in "_-" for c in value):
        raise ValueError("invalid_request")
    return value


def build_app(service: Service) -> Any:
    server = MCPServer("jobapp Gmail", log_level="WARNING")

    @server.tool()
    async def get_profile() -> dict[str, Any]:
        return await service.read("get_profile", {}, _headers.get())

    @server.tool()
    async def list_messages(
        q: str = "", page_token: str | None = None, max_results: int = 100
    ) -> dict[str, Any]:
        return await service.read("list_messages", locals(), _headers.get())

    @server.tool()
    async def get_message(
        id: str, format: str = "full", metadata_headers: list[str] | None = None
    ) -> dict[str, Any]:
        return await service.read(
            "get_message",
            {"id": id, "format": format, "metadata_headers": metadata_headers or []},
            _headers.get(),
        )

    @server.tool()
    async def get_thread(id: str, format: str = "full") -> dict[str, Any]:
        return await service.read("get_thread", locals(), _headers.get())

    @server.tool()
    async def list_history(
        start_history_id: str, history_types: list[str] | None = None, page_token: str | None = None
    ) -> dict[str, Any]:
        return await service.read(
            "list_history",
            {
                "start_history_id": start_history_id,
                "history_types": history_types or [],
                "page_token": page_token,
            },
            _headers.get(),
        )

    @server.tool()
    async def send_message(send_key: str, raw: str, thread_id: str | None = None) -> dict[str, Any]:
        return await service.send(send_key, raw, thread_id, _headers.get())

    @server.custom_route("/healthz", methods=["GET"])
    async def health(request: Request) -> Any:
        return JSONResponse({"status": "ok"})

    @server.custom_route("/attachments/{message_id}/{attachment_id}", methods=["GET"])
    async def attachment(request: Request) -> Any:
        try:
            client, grant = await service.client(dict(request.headers))
        except (PermissionError, GoogleError):
            return JSONResponse({"error": "not_connected"}, status_code=403)
        return StreamingResponse(
            client.download_attachment(
                grant,
                _id(request.path_params["message_id"]),
                _id(request.path_params["attachment_id"]),
            ),
            media_type="application/octet-stream",
        )

    application = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=20 * 1024 * 1024,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[
                "gmailmcp:9400",
                "gmailmcp",
                *(
                    [urlsplit(service.settings.gmail_mcp_url).netloc]
                    if service.settings.app_env == "test"
                    else []
                ),
            ],
            allowed_origins=[],
        ),
    )

    class Headers:
        def __init__(self, app: Any) -> None:
            self.app = app

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            context = _headers.set(headers)
            try:
                with log.hide(headers.get("authorization"), headers.get("x-jobapp-address")):
                    await self.app(scope, receive, send)
            finally:
                _headers.reset(context)

    wrapped = Headers(application)
    wrapped.state = application.state
    wrapped.router = application.router
    return wrapped
