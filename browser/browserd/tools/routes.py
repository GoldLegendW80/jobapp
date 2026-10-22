"""The tool API of the current lease (browser B5), added to browserd/app.py's router list:

  POST /leases/{id}/tools/{name}   args as JSON; `X-Lease-Token` required. 200 with the tool's
                                   result (`ok` true, or false with the refusal's code); 404
                                   unknown tool or not the current lease, 403 a wrong token or
                                   capability, 409 the lease waits for the person or is a
                                   person's own session (`person_session`, B7), 410 the lease
                                   ended during the call
  PUT  /leases/{id}/files/{name}   a document for browser_file_upload: a PDF by its magic bytes,
                                   at most 20 MB (413 past it, before a byte is read), at most
                                   FILES_MAX per lease, held in memory with the lease and gone
                                   with it (never on a disk, never readable by Chromium's uid;
                                   State.end drops them when the lease ends)

The gate's capability: a random value made for a lease of a purpose in GATE_PURPOSES when it is
created, answered once to the api in POST /leases (`gate_capability`), kept here as its hash
only. A `browser_click` that carries it in `X-Gate-Capability` may click an element the submit
detector marks; nothing else may, and a wrong value is refused before the page is touched. The
api's `client.gate_click` is its only sender (api/app/browser/client.py).

Apply myself (A9): `browser_snapshot` with `{"passive": true}` on an `apply_manual` lease is the
app's watch of the page the person fills; it does not count as activity (Manager.call). Outside
the app's turn (`app_turn`, a "Fill my details") only PERSON_TIME's tools run there: a click, a
key or a typed value is refused `person_input`, so a Take over during the fill stops it.

Calls on one lease run one at a time, through `Manager.call` (the lease's idle timer and its end
cancel them).
"""

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
from typing import Any

from browserd.leases import MANUAL_PURPOSE, Lease, LeaseEnded, LeaseError
from browserd.tools import actions, linkedin, pacing, policy

log = logging.getLogger("browserd")
GATE_PURPOSES = ("apply_submit", "profile_edit")
LEASE_ID = r"(?P<lease_id>[0-9a-f-]{36})"
LOCKS: dict[str, asyncio.Lock] = {}
PACER = pacing.Pacer()
# What the app may do on an `apply_manual` lease while the person holds the input: read, open the
# form, answer a file chooser or a file input with a document of the lease.
PERSON_TIME = frozenset({
    "browser_snapshot", "browser_navigate", "browser_file_upload", "browser_box",
    "browser_take_screenshot", "browser_wait_for",
})  # fmt: skip


def _hash(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def issue_capability(lease: Lease) -> str | None:
    """The gate's capability of a new lease, for the gate's purposes only."""
    if lease.request.purpose not in GATE_PURPOSES:
        return None
    capability = secrets.token_urlsafe(32)
    lease.gate_hash = _hash(capability)
    return capability


def state_of(lease: Lease) -> actions.State:
    state = lease.scratch.get("tools")
    if state is None:
        state = lease.scratch["tools"] = actions.State()
    return state


def _lock(lease: Lease) -> asyncio.Lock:
    # One lock per lease, and only the current lease's is kept.
    for old in [key for key in LOCKS if key != lease.lease_id]:
        del LOCKS[old]
    return LOCKS.setdefault(lease.lease_id, asyncio.Lock())


async def _run(svc: Any, lease_id: str, token: str, work: Any, *, touch: bool = True) -> Any:
    try:
        return await svc.manager.call(lease_id, token, work, touch=touch)
    except LeaseEnded as exc:
        raise LeaseError(410, "lease_ended", reason=exc.reason) from None


async def call_tool(svc: Any, req: Any, match: re.Match[str]) -> tuple[int, Any]:
    name, lease_id = match["name"], match["lease_id"]
    if name not in actions.TOOLS:
        raise LeaseError(404, "unknown_tool")
    token = req.headers.get("x-lease-token", "")
    lease = svc.manager.check_token(lease_id, token)
    if not lease.request.automation:  # B7: a person's own session has no agent
        raise LeaseError(409, "person_session")
    args = req.json()
    if not isinstance(args, dict):
        raise LeaseError(400, "bad_args")
    capability = req.headers.get("x-gate-capability")
    if capability is not None:
        good = bool(lease.gate_hash) and hmac.compare_digest(lease.gate_hash, _hash(capability))
        if name != "browser_click" or not good:
            raise LeaseError(403, "bad_capability")
    manual = lease.request.purpose == MANUAL_PURPOSE
    tab = actions.Tab(
        request=lease.request,
        state=state_of(lease),
        browser=svc.manager.browser,
        pacer=PACER,
        gate=capability is not None,
        lease_tag=lease_id[:8],
    )

    async def work() -> dict[str, Any]:
        async with _lock(lease):
            if manual and not lease.app_turn and name not in PERSON_TIME:
                log.info("lease %s: %s refused: person_input", lease_id[:8], name)
                return actions.failure("person_input", "Refused: the person holds the input.")
            try:
                return await actions.run(tab, name, args)
            except ValueError as exc:
                return actions.failure("bad_args", f"Bad argument: {exc}.")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("lease %s: %s failed: %s", lease_id[:8], name, type(exc).__name__)
                return actions.failure("page_error", "The page did not let this action finish.")

    passive = manual and name == "browser_snapshot" and args.get("passive") is True
    return 200, await _run(svc, lease_id, token, work, touch=not passive)


async def put_file(svc: Any, req: Any, match: re.Match[str]) -> tuple[int, Any]:
    name, lease_id = match["name"], match["lease_id"]
    token = req.headers.get("x-lease-token", "")
    lease = svc.manager.check_token(lease_id, token)
    if not lease.request.automation:
        raise LeaseError(409, "person_session")
    state = state_of(lease)
    code = policy.file_verdict(name, req.body, state.files)
    if code:
        raise LeaseError(413 if code == "file_too_large" else 400, code)

    async def work() -> dict[str, Any]:
        async with _lock(lease):
            # Again under the lease's lock: two PUTs of new names at FILES_MAX - 1 both passed
            # the check above, and only one may be held.
            again = policy.file_verdict(name, req.body, state.files)
            if again:
                raise LeaseError(413 if again == "file_too_large" else 400, again)
            state.files[name] = req.body
        return {"ok": True, "name": name, "size": len(req.body)}

    return 200, await _run(svc, lease_id, token, work)


async def linkedin_open(svc: Any, req: Any, match: re.Match[str]) -> tuple[int, Any]:
    """Private application code endpoint, never dispatched as an agent tool."""
    lease_id = match["lease_id"]
    token = req.headers.get("x-lease-token", "")
    lease = svc.manager.check_token(lease_id, token)
    body = req.json()
    if not isinstance(body, dict) or set(body) != {"job_id"} or not isinstance(body["job_id"], str):
        raise LeaseError(400, "bad_args")
    if not linkedin.JOB.fullmatch(body["job_id"]):
        raise LeaseError(400, "bad_args")
    tab = actions.Tab(
        lease.request, state_of(lease), svc.manager.browser, PACER, lease_tag=lease_id[:8]
    )

    async def work() -> dict[str, Any]:
        async with _lock(lease):
            try:
                result = await linkedin.open_modal(
                    tab, body["job_id"], svc.manager.config.linkedin_stub_base_url
                )
            except actions.OffList:
                return await tab.settle(actions.refused("host_not_allowed"))
            except Exception:
                # A detached element during approach is a refusal, never a replacement click.
                result = actions.refused("linkedin_opener_refused")
            return await tab.settle(result)

    return 200, await _run(svc, lease_id, token, work)


# (method, pattern, handler, body_max): browserd/app.py makes its Route of each.
ROUTES = [
    ("POST", re.compile(rf"/leases/{LEASE_ID}/linkedin/open"), linkedin_open, 128),
    ("POST", re.compile(rf"/leases/{LEASE_ID}/tools/(?P<name>[a-z_]{{1,48}})"), call_tool,
     64 * 1024),
    ("PUT", re.compile(rf"/leases/{LEASE_ID}/files/(?P<name>[^/]{{1,100}})"), put_file,
     policy.FILE_MAX),
]  # fmt: skip
