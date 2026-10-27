"""The one client of browserd's control API (browser B3), for the api and the worker.

browserd (browser/browserd/app.py) runs in the `browser` container on the internal `browsing`
network, behind BROWSER_CONTROL_SECRET. Nothing else of the application speaks to it:

    start(...)  POST /leases            the lease's id, its token and browserd's view of it
    end(id)     DELETE /leases/{id}     Ended (its reason, the profile's size), or None when
                                        it is no longer the current lease
    remove_profile(id, uid)  DELETE /profiles/{id}   the profile's directory removed (B7)
    site_session(id, uid, host)  POST /profiles/{id}/site-session   a website's login saved (B17)
    current()   GET /leases/current     {lease: {...} | None, last_end: {...} | None}
    live_input(id, viewer)  POST /leases/{id}/live/input   who holds the live view's input (B4)
    tool(...)   POST /leases/{id}/tools/{name}   a tool's result (browser B5)
    put_file()  PUT /leases/{id}/files/{name}    a PDF placed on the lease for browser_file_upload
    gate_click(...)  browser_click with the gate's capability: the apply gate's code only
    handoff(id, action)  POST /leases/{id}/handoff/{done|cancel}   the sign in hand off (B12a)

A tool's refusal (a submit element, a host outside the list) is a result with `ok` false, never
an error. The gate's capability comes back from `start` for the gate's purposes (`apply_submit`,
`profile_edit`) and, like the token, goes nowhere else: never logged, never in a repr.

Every failure is a BrowserError whose `code` is one of CODES, never browserd's text:

    unavailable   no secret configured (logged as such at most once each UNCONFIGURED_EVERY_S
                  per client, never a value), nobody answers within TIMEOUT_S, the connection broke,
                  browserd answered 401 (a secret that differs) or 5xx (Chromium did not start)
    busy          browserd holds another lease (409); `holder_purpose` says its purpose, and no
                  id or person of it ever comes back
    refused       browserd refused the request as malformed (400, 404 on a start, 413), or a
                  wrong token or capability (403)
    ended         a tool call on a lease that is no longer the current one (404, 410)
    waiting_person  a tool call while the lease waits for the person to sign in (409, B12a)

Control calls use TIMEOUT_S (5 s); startup, shutdown and handoff have their bounded operation
allowances. Tool calls and the private opener allow TOOL_TIMEOUT_S for their native work plus
TAKEOVER_WAIT_S for the person's specified live input pause. Startup waits for CDP: browserd
allows START_S (30 s) and the client a small margin for a slow profile lock. The lease token
is returned to the caller and goes nowhere else: it is not logged, and app/browser/leases.py does
not store it. One log line per call: the route, the status and the duration.
"""

import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("app.browser.client")

TIMEOUT_S = 5.0
START_TIMEOUT_S = 35.0  # browserd's START_S of 30 s, plus a margin
TAKEOVER_WAIT_S = 15 * 60  # SPEC-browser: Live.agent_turn's bounded person-owned pause.
TOOL_TIMEOUT_S = 120.0  # a paced browser_type of 200 characters, a 30 s navigation, a screenshot
# browserd's QUIT_S of 10 s for Chromium, then the trim and the measure of the profile (B7); an
# end or a removal that takes longer is told again later (browserd then answers it is done).
END_TIMEOUT_S = 30.0
# A hand off's "done": the person's Chromium closed (QUIT_S, 10 s), one started with CDP (30 s)
# and the noted address loaded (NAVIGATION_S, 30 s), plus a margin.
HANDOFF_TIMEOUT_S = 80.0
CODES = ("unavailable", "busy", "refused", "ended", "waiting_person")
HANDOFF_ACTIONS = ("done", "cancel")
# browserd's answers that keep a profile's directory for now (browser/browserd/signin.py).
REMOVAL_KEPT = {(409, "profile_in_use"), (409, "profile_uid_mismatch"), (503, "not_removed")}
# A client without its secret says so in the log once a minute at most: the page polls and the
# worker sweeps, and one line is enough to find the cause (production, 2026-10-27).
UNCONFIGURED_EVERY_S = 60.0
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,78}\.pdf$")  # browserd/tools/policy.py


class BrowserError(Exception):
    """A call of the control API that did not succeed. The message is the code."""

    def __init__(self, code: str, holder_purpose: str | None = None) -> None:
        if code not in CODES:
            raise ValueError(code)
        super().__init__(code)
        self.code = code
        self.holder_purpose = holder_purpose


@dataclass(frozen=True)
class Ended:
    """browserd's end of a lease: why, and the profile's size once Chromium closed (B7)."""

    reason: str
    size_bytes: int | None


def ended_of(payload: Any) -> Ended:
    """An Ended from browserd's `last_end` or DELETE answer; what is not well formed is left
    out (the reason "ended", no size)."""
    found = payload if isinstance(payload, dict) else {}
    reason, size = found.get("reason"), found.get("size_bytes")
    return Ended(
        reason=reason if isinstance(reason, str) and re.fullmatch(r"[a-z_]{1,32}", reason)
        else "ended",
        size_bytes=size if isinstance(size, int) and not isinstance(size, bool) and size >= 0
        else None,
    )  # fmt: skip


@dataclass(frozen=True)
class Started:
    lease_id: str
    token: str
    lease: dict[str, Any]
    capability: str | None = None  # the gate's, for apply_submit and profile_edit only

    def __repr__(self) -> str:  # never the token, in a log or a traceback
        return f"Started(lease_id={self.lease_id!r})"


class BrowserdClient:
    def __init__(
        self,
        base_url: str,
        secret: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # browserd strips its own copy: a secret of blanks only is no secret there either.
        self._secret = secret if secret and secret.strip() else ""
        self._transport = transport
        self._clock = clock
        self._unconfigured_said: float | None = None

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
        hosts: Sequence[str] = (),
        submit_selectors: Sequence[str] = (),
        address: str | None = None,
    ) -> Started:
        body: dict[str, Any] = {
            "user_id": user_id,
            "profile_id": profile_id,
            "uid": uid,
            "purpose": purpose,
            "automation": automation,
            "hosts": list(hosts),
            "submit_selectors": list(submit_selectors),
        }
        if address is not None:  # B7: where a person's own session opens
            body["address"] = address
        status, payload = await self._call("POST", "/leases", START_TIMEOUT_S, body)
        if status == 201 and isinstance(payload, dict):
            lease_id, token = payload.get("lease_id"), payload.get("lease_token")
            if isinstance(lease_id, str) and isinstance(token, str) and token:
                public = {
                    k: v for k, v in payload.items() if k not in ("lease_token", "gate_capability")
                }
                capability = payload.get("gate_capability")
                return Started(
                    lease_id=lease_id,
                    token=token,
                    lease=public,
                    capability=capability if isinstance(capability, str) else None,
                )
        raise self._failure(status, payload)

    async def end(self, lease_id: str) -> Ended | None:
        # Chromium of a person's session gets up to 10 s to write its profile, then the profile
        # is measured (B7): the end waits for both.
        status, payload = await self._call("DELETE", f"/leases/{lease_id}", END_TIMEOUT_S)
        if status in (200, 204):
            return ended_of(payload)
        if status == 404:
            return None
        raise self._failure(status, payload)

    async def remove_profile(self, profile_id: str, uid: int) -> str:
        """Removes a profile's directory: "removed" (also when there was none), or why browserd
        kept that one: "profile_in_use", "profile_uid_mismatch", "not_removed" (it tried), or
        "failed" (any other answer about it). Raises BrowserError only when browserd did not
        answer or refused the secret, which holds for every other directory too."""
        body = {"uid": uid}
        status, payload = await self._call("DELETE", f"/profiles/{profile_id}", END_TIMEOUT_S, body)
        if status == 204:
            return "removed"
        if status == 401:
            raise self._failure(status, payload)
        code = payload.get("error") if isinstance(payload, dict) else None
        if (status, code) in REMOVAL_KEPT:
            return str(code)
        return "failed"

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

    async def live_input(self, lease_id: str, viewer: str | None, giver: str | None = None) -> str:
        """The viewer `viewer` takes the keyboard and the mouse of the lease's live view, or
        (None) the agent has them back, when `giver` is given only if that viewer holds them.
        Answers who holds them: "person" or "agent"."""
        path = f"/leases/{lease_id}/live/input"
        body = {"viewer": viewer} if giver is None else {"viewer": viewer, "from": giver}
        status, payload = await self._call("POST", path, TIMEOUT_S, body)
        if (
            status == 200
            and isinstance(payload, dict)
            and payload.get("input") in ("person", "agent")
        ):
            return str(payload["input"])
        raise self._failure(status, payload)

    async def tool(
        self, lease_id: str, token: str, name: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One tool on the lease: its result, `ok` true or false with the refusal's code."""
        return await self._tool(lease_id, token, name, args or {}, None)

    async def gate_click(
        self, lease_id: str, token: str, ref: str, capability: str, element: str = ""
    ) -> dict[str, Any]:
        """browser_click with the gate's capability: it may click the submit element. Only the
        apply gate's code calls it (apply/submit.py); no agent tool reaches it."""
        if not capability:
            raise BrowserError("refused")
        args = {"ref": ref, "element": element}
        return await self._tool(lease_id, token, "browser_click", args, capability)

    async def handoff(self, lease_id: str, action: str) -> dict[str, Any]:
        """The person's answer to a sign in hand off (B12a): "done" restarts Chromium with CDP
        (an answer of `restarted` false when it already did), "cancel" ends the lease. `ended`
        when the lease is gone, `refused` when nothing waits on it."""
        if action not in HANDOFF_ACTIONS:
            raise ValueError(action)
        path = f"/leases/{lease_id}/handoff/{action}"
        status, payload = await self._call("POST", path, HANDOFF_TIMEOUT_S)
        if status == 200 and isinstance(payload, dict):
            return payload
        if status in (404, 410):
            raise BrowserError("ended")
        if status == 409:
            raise BrowserError("refused")
        raise self._failure(status, payload)

    async def linkedin_open(self, lease_id: str, token: str, job_id: str) -> dict[str, Any]:
        """Private code-owned opener. Never a model tool and never a submit capability."""
        status, payload = await self._call(
            "POST",
            f"/leases/{lease_id}/linkedin/open",
            TOOL_TIMEOUT_S + TAKEOVER_WAIT_S,
            {"job_id": job_id},
            headers={"X-Lease-Token": token},
        )
        if status == 200 and isinstance(payload, dict):
            return payload
        raise self._tool_failure(status, payload)

    async def apply_wait(
        self, lease_id: str, token: str, action: str, reason: str | None = None
    ) -> dict[str, Any]:
        """Code-owned Apply wait, absent from the model tool API."""
        if action not in {"begin", "read", "resume"}:
            raise ValueError(action)
        status, payload = await self._call(
            "POST",
            f"/leases/{lease_id}/apply-wait/{action}",
            TIMEOUT_S,
            {"reason": reason} if action == "begin" else {},
            headers={"X-Lease-Token": token},
        )
        if status == 200 and isinstance(payload, dict):
            return payload
        raise self._tool_failure(status, payload)

    async def put_file(self, lease_id: str, token: str, name: str, data: bytes) -> dict[str, Any]:
        # The name goes into the path: one browserd accepts, checked here too, so a "../x.pdf"
        # never becomes another route once the path is normalised.
        if not FILE_NAME.fullmatch(name):
            raise BrowserError("refused")
        status, payload = await self._call(
            "PUT",
            f"/leases/{lease_id}/files/{name}",
            TOOL_TIMEOUT_S,
            content=data,
            headers={"X-Lease-Token": token, "Content-Type": "application/pdf"},
        )
        if status == 200 and isinstance(payload, dict):
            return payload
        raise self._tool_failure(status, payload)

    async def _tool(
        self, lease_id: str, token: str, name: str, args: dict[str, Any], capability: str | None
    ) -> dict[str, Any]:
        headers = {"X-Lease-Token": token}
        if capability is not None:
            headers["X-Gate-Capability"] = capability
        status, payload = await self._call(
            "POST",
            f"/leases/{lease_id}/tools/{name}",
            TOOL_TIMEOUT_S + TAKEOVER_WAIT_S,
            args,
            headers=headers,
        )
        if status == 200 and isinstance(payload, dict) and isinstance(payload.get("ok"), bool):
            return payload
        raise self._tool_failure(status, payload)

    def _tool_failure(self, status: int, payload: Any) -> BrowserError:
        if status in (404, 410):
            return BrowserError("ended")
        if status == 409 and isinstance(payload, dict) and payload.get("error") == "waiting_person":
            return BrowserError("waiting_person")
        if status == 403:
            return BrowserError("refused")
        return self._failure(status, payload)

    @staticmethod
    def _failure(status: int, payload: Any) -> BrowserError:
        if status == 409:
            holder = payload.get("holder_purpose") if isinstance(payload, dict) else None
            return BrowserError("busy", holder if isinstance(holder, str) else None)
        if status in (400, 404, 413):
            return BrowserError("refused")
        return BrowserError("unavailable")

    async def site_session(self, profile_id: str, uid: int, host: str) -> bool | None:
        """Whether the profile holds an unexpired cookie of `host` (B17), None when browserd could
        not tell (the profile in use, not that uid's, not read). Raises BrowserError when browserd
        did not answer."""
        body = {"uid": uid, "host": host}
        path = f"/profiles/{profile_id}/site-session"
        status, payload = await self._call("POST", path, END_TIMEOUT_S, body)
        if status == 200 and isinstance(payload, dict) and type(payload.get("present")) is bool:
            return payload["present"]
        if status == 401 or (status >= 500 and status != 503):
            raise self._failure(status, payload)
        return None

    async def _call(
        self,
        method: str,
        path: str,
        timeout_s: float,
        body: dict[str, Any] | None = None,
        *,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        # The route without the lease id nor a file's name; a tool's name is kept.
        route = re.sub(r"^/leases/(?!current$)[^/]+", "/leases/{id}", path)
        route = re.sub(r"/files/[^/]+$", "/files/{name}", route)
        route = re.sub(r"^/profiles/[^/]+", "/profiles/{id}", route)
        if not self._secret:
            now = self._clock()
            if self._unconfigured_said is None or now - self._unconfigured_said >= (
                UNCONFIGURED_EVERY_S
            ):
                self._unconfigured_said = now
                logger.warning(
                    "browserd not configured: BROWSER_CONTROL_SECRET is empty (%s %s)",
                    method,
                    route,
                )
            raise BrowserError("unavailable")
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
                    json=body if content is None else None,
                    content=content,
                    headers={"Authorization": f"Bearer {self._secret}", **(headers or {})},
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
