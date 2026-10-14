"""The one client of browserd's control API (browser B3), for the api and the worker.

browserd (browser/browserd/app.py) runs in the `browser` container on the internal `browsing`
network, behind BROWSER_CONTROL_SECRET. Nothing else of the application speaks to it:

    start(...)  POST /leases            the lease's id, its token and browserd's view of it
    end(id)     DELETE /leases/{id}     True, or False when it is no longer the current lease
    current()   GET /leases/current     {lease: {...} | None, last_end: {...} | None}
    live_input(id, viewer)  POST /leases/{id}/live/input   who holds the live view's input (B4)

Every failure is a BrowserError whose `code` is one of CODES, never browserd's text:

    unavailable   no secret configured, nobody answers within TIMEOUT_S, the connection broke,
                  browserd answered 401 (a secret that differs) or 5xx (Chromium did not start)
    busy          browserd holds another lease (409); `holder_purpose` says its purpose, and no
                  id or person of it ever comes back
    refused       browserd refused the request as malformed (400, 404 on a start, 413)

Timeouts are TIMEOUT_S (5 s) for every call but the start, which waits for Chromium to answer on
CDP: browserd gives it START_S (30 s, browser/browserd/config.py) and the client a few seconds
more, so a slow start (a stale profile lock) is not mistaken for a dead service. The lease token
is returned to the caller and goes nowhere else: it is not logged, and app/browser/leases.py does
not store it. One log line per call: the route, the status and the duration.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("app.browser.client")

TIMEOUT_S = 5.0
START_TIMEOUT_S = 35.0  # browserd's START_S of 30 s, plus a margin
CODES = ("unavailable", "busy", "refused")
LEASE_IN_PATH = re.compile(r"/leases/[0-9a-f-]{36}")


class BrowserError(Exception):
    """A call of the control API that did not succeed. The message is the code."""

    def __init__(self, code: str, holder_purpose: str | None = None) -> None:
        if code not in CODES:
            raise ValueError(code)
        super().__init__(code)
        self.code = code
        self.holder_purpose = holder_purpose


@dataclass(frozen=True)
class Started:
    lease_id: str
    token: str
    lease: dict[str, Any]

    def __repr__(self) -> str:  # never the token, in a log or a traceback
        return f"Started(lease_id={self.lease_id!r})"


class BrowserdClient:
    def __init__(
        self,
        base_url: str,
        secret: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._secret = secret or ""
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self._secret)

    async def start(
        self,
        *,
        user_id: str,
        profile_id: str,
        uid: int,
        purpose: str,
        automation: bool,
    ) -> Started:
        body = {
            "user_id": user_id,
            "profile_id": profile_id,
            "uid": uid,
            "purpose": purpose,
            "automation": automation,
        }
        status, payload = await self._call("POST", "/leases", START_TIMEOUT_S, body)
        if status == 201 and isinstance(payload, dict):
            lease_id, token = payload.get("lease_id"), payload.get("lease_token")
            if isinstance(lease_id, str) and isinstance(token, str) and token:
                public = {k: v for k, v in payload.items() if k != "lease_token"}
                return Started(lease_id=lease_id, token=token, lease=public)
        raise self._failure(status, payload)

    async def end(self, lease_id: str) -> bool:
        status, payload = await self._call("DELETE", f"/leases/{lease_id}", TIMEOUT_S)
        if status == 204:
            return True
        if status == 404:
            return False
        raise self._failure(status, payload)

    async def current(self) -> dict[str, Any]:
        status, payload = await self._call("GET", "/leases/current", TIMEOUT_S)
        if status == 200 and isinstance(payload, dict) and "lease" in payload:
            lease = payload.get("lease")
            return {
                "lease": lease if isinstance(lease, dict) else None,
                "last_end": payload.get("last_end")
                if isinstance(payload.get("last_end"), dict)
                else None,
            }
        raise self._failure(status, payload)

    async def live_input(self, lease_id: str, viewer: str | None) -> str:
        """The viewer `viewer` takes the keyboard and the mouse of the lease's live view, or
        (None) the agent has them back. Answers who holds them: "person" or "agent"."""
        path = f"/leases/{lease_id}/live/input"
        status, payload = await self._call("POST", path, TIMEOUT_S, {"viewer": viewer})
        if (
            status == 200
            and isinstance(payload, dict)
            and payload.get("input") in ("person", "agent")
        ):
            return str(payload["input"])
        raise self._failure(status, payload)

    @staticmethod
    def _failure(status: int, payload: Any) -> BrowserError:
        if status == 409:
            holder = payload.get("holder_purpose") if isinstance(payload, dict) else None
            return BrowserError("busy", holder if isinstance(holder, str) else None)
        if status in (400, 404, 413):
            return BrowserError("refused")
        return BrowserError("unavailable")

    async def _call(
        self, method: str, path: str, timeout_s: float, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        if not self._secret:
            raise BrowserError("unavailable")
        route = LEASE_IN_PATH.sub("/leases/{id}", path)
        began = time.monotonic()
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(timeout_s, connect=TIMEOUT_S),
                transport=self._transport,
                follow_redirects=False,
            ) as http:
                answer = await http.request(
                    method,
                    path,
                    json=body,
                    headers={"Authorization": f"Bearer {self._secret}"},
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "browserd %s %s failed: %s after %.2f s",
                method,
                route,
                type(exc).__name__,
                time.monotonic() - began,
            )
            raise BrowserError("unavailable") from None
        logger.info(
            "browserd %s %s %d in %.2f s",
            method,
            route,
            answer.status_code,
            time.monotonic() - began,
        )
        try:
            payload = answer.json() if answer.content else None
        except ValueError:
            payload = None
        return answer.status_code, payload
