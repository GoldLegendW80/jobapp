"""The sign in hand off (browser B12a): an agent's lease waits while the person signs in or signs
up in the live view, on a Chromium without a debugging port. SPEC-browser, "The sign in hand off".

    request  the tool browser_request_signin(site, reason) (browserd/tools/request_signin.py).
             Nobody watching (a `sweep` or `full_auto` lease, or no live view of the lease open
             at the time of the call): it answers `nobody_watching` and changes nothing.
             Otherwise the lease waits (`waiting_person: signin`: no tool call, the idle timer
             and the maximum suspended, a deadline of BROWSER_SESSION_MAX_S), the tab's address
             is noted, Chromium is closed cleanly and started again on the same profile at that
             address with no debugging port, and the live view is in input mode. It answers
             `waiting_person` with the seconds left; the worker polls (api/app/browser/handoff.py).
    done     POST /leases/{id}/handoff/done ("I'm signed in"): the person's input ends, that
             Chromium is closed (its profile written), Chromium starts again with CDP and opens
             the noted address, and the lease resumes with the active time it had left. A second
             call restarts nothing: 200 `restarted: false`.
    cancel   POST /leases/{id}/handoff/cancel: the lease is released `signin_cancelled`.
    deadline Manager.tick ends the lease `signin_timeout` (leases.Lease.expired).

Never both attached: Manager.swap closes one Chromium wholly before it starts the other, and the
person's input is on only while the Chromium without CDP runs. `site` and `reason` are kept
plain (control and format characters dropped, spaces collapsed, cut); the tool takes nothing else,
so no credential can be given to it. The person is shown the host of the noted address
(`Lease.wait_host`), never `site`: a page can steer the model's words, not the tab's host. Logs:
the lease's tag, never the site, the reason, the address, nor a typed character (keys go from the
viewer to the display only).
"""

import asyncio
import contextlib
import logging
import re
import unicodedata
import weakref
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

from browserd import live
from browserd.config import NAVIGATION_S
from browserd.leases import Lease, LeaseError
from browserd.signin import check_address

log = logging.getLogger("browserd")

SIGNIN = "signin"
KEY = "handoff"  # its place in Lease.scratch
UNWATCHED = ("sweep", "full_auto")  # SPEC-browser: these never wait for a person
ARGS = frozenset({"site", "reason"})
SITE_MAX = 80
REASON_MAX = 300
NOBODY = "Nobody is watching this browser now. Stop here: the work waits for the person."
WAITING = "The person is asked to sign in to {site} in the live view."
NO_ADDRESS = "This tab has no web address to sign in at."
BAD_SITE = "site is the name of the site, as the person reads it."

_services: "weakref.WeakSet[Any]" = weakref.WeakSet()


@dataclass
class HandOff:
    site: str
    reason: str
    address: str
    done: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def bind(svc: Any) -> None:
    """browserd/__main__.py binds its Service at start; the tool finds it by its lease."""
    _services.add(svc)


def service_of(request: Any) -> Any | None:
    for svc in list(_services):
        lease = svc.manager.current()
        if lease is not None and lease.request is request:
            return svc
    return None


def plain(raw: Any, limit: int) -> str:
    """Text for the person: NFKC, every control, format or separator character a space, the
    spaces collapsed, cut at `limit`. Shown as text, never as markup."""
    if not isinstance(raw, str):
        return ""
    text = unicodedata.normalize("NFKC", raw[: limit * 4])
    text = "".join(" " if unicodedata.category(ch)[0] in "CZ" else ch for ch in text)
    return " ".join(text.split())[:limit].rstrip()


def address_of(url: Any) -> str | None:
    """Where the person signs in: the tab's address as "Sign in to a site" accepts one, else its
    origin (an address past the length limit), else None (about:blank, a data: page)."""
    found = check_address(url)
    if found is not None or not isinstance(url, str):
        return found
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return check_address(f"{parts.scheme}://{parts.netloc}/")


def watched(svc: Any, lease: Lease) -> bool:
    """A live view of that lease is open now (its websocket counted, not one still starting)."""
    if lease.request.purpose in UNWATCHED:
        return False
    viewers = live.live_of(svc).viewers
    return any(v.lease_id == lease.lease_id and not v.closed for v in viewers)


def failure(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message, **extra}


async def request(tab: Any, args: dict[str, Any]) -> dict[str, Any]:
    """The tool. Its answer is never `ok`: the agent cannot act on the page until the hand off
    ends, and the code says why (`nobody_watching`, `waiting_person`, `bad_args`)."""
    site, reason = plain(args.get("site"), SITE_MAX), plain(args.get("reason"), REASON_MAX)
    if set(args) - ARGS or not site:
        return failure("bad_args", BAD_SITE)
    svc = service_of(tab.request)
    lease = svc.manager.current() if svc is not None else None
    if lease is None or lease.request is not tab.request:
        return failure("lease_ended", "The lease ended.")
    if not watched(svc, lease):
        log.info("lease %s: sign in asked, nobody watching", tab.lease_tag)
        return failure("nobody_watching", NOBODY)
    page = await tab.page()  # off the list: the tool answers host_not_allowed (actions.run)
    address = address_of(page.url)
    if address is None:
        return failure("bad_address", NO_ADDRESS)
    handoff = HandOff(site=site, reason=reason, address=address)
    lease.scratch[KEY] = handoff
    host = urlsplit(address).hostname or ""
    lease.wait_person(svc.manager.clock(), SIGNIN, host, svc.manager.config.session_max_s)
    log.info("lease %s: sign in hand off begins", tab.lease_tag)
    await handoff.lock.acquire()  # free, so taken at once: a `done` waits for the swap below
    # Shielded: a release that cancels this call never interrupts a Chromium half started.
    await asyncio.shield(_to_person(svc, lease, handoff))
    if svc.manager.current() is not lease:  # a swap that failed, or a cancel meanwhile
        return failure("lease_ended", "The lease ended.")
    return failure(
        "waiting_person",
        WAITING.format(site=site),
        left_s=lease.wait_left_s(svc.manager.clock()),
    )


async def _to_person(svc: Any, lease: Lease, handoff: HandOff) -> None:
    """Runs with handoff.lock held by `request`, and lets it go."""
    try:
        person = replace(lease.request, automation=False, address=handoff.address)
        if await svc.manager.swap(lease, person):
            lease.person_input = True
    finally:
        handoff.lock.release()


async def _to_agent(svc: Any, lease: Lease, handoff: HandOff) -> bool:
    """Chromium again with CDP; the tools' navigation guard is on its context before any tab is
    sent to the noted address, so a redirect off the list never loads (B5). A guard that cannot
    be installed leaves the tab where Chromium opened it."""
    from browserd.tools import actions, routes  # actions imports this module (request_signin)

    if not await svc.manager.swap(lease, lease.request):
        return False
    with contextlib.suppress(Exception):  # the agent's next snapshot shows where the tab is
        tab = actions.Tab(
            request=lease.request,
            state=routes.state_of(lease),
            browser=svc.manager.browser,
            pacer=routes.PACER,
            lease_tag=lease.lease_id[:8],
        )
        await tab.guard()
        tab.state.page = await open_address(svc.manager.browser, handoff.address)
    return True


async def open_address(browser: Any, address: str) -> Any:
    """The tab that shows the noted address: one already there, else the tab Chromium opened
    (prep removes the last session's tabs, so none is restored: issue #488), sent there."""
    context = browser.browser.contexts[0]
    for page in context.pages:
        if page.url == address:
            return page
    page = context.pages[-1] if context.pages else await context.new_page()
    await page.bring_to_front()
    with contextlib.suppress(Exception):  # a timeout leaves the tab where it got to
        await page.goto(address, wait_until="load", timeout=NAVIGATION_S * 1000)
    return page


def _waiting(svc: Any, lease_id: str) -> tuple[Lease, HandOff]:
    lease = svc.manager.current()
    if lease is None or lease.lease_id != lease_id:
        raise LeaseError(404, "no_such_lease")
    handoff = lease.scratch.get(KEY)
    if not isinstance(handoff, HandOff):
        raise LeaseError(409, "not_waiting")
    return lease, handoff


async def done(svc: Any, _: Any, match: re.Match[str]) -> tuple[int, Any]:
    lease, handoff = _waiting(svc, match["lease_id"])
    async with handoff.lock:
        if handoff.done:
            return 200, {"lease_id": lease.lease_id, "state": lease.state, "restarted": False}
        if lease.state != "waiting_person" or svc.manager.current() is not lease:
            raise LeaseError(409, "not_waiting")
        # The person's keys stop before any Chromium with CDP exists.
        lease.person_input = False
        live.live_of(svc).give_back(lease.lease_id)
        if not await asyncio.shield(_to_agent(svc, lease, handoff)):
            reason = (svc.manager.last_end or {}).get("reason", "ended")
            raise LeaseError(410, "lease_ended", reason=reason)
        handoff.done = True
        lease.resume(svc.manager.clock())
    log.info("lease %s: sign in hand off done", lease.lease_id[:8])
    return 200, {"lease_id": lease.lease_id, "state": lease.state, "restarted": True}


async def cancel(svc: Any, _: Any, match: re.Match[str]) -> tuple[int, Any]:
    lease, handoff = _waiting(svc, match["lease_id"])
    if handoff.done:
        raise LeaseError(409, "not_waiting")
    lease.person_input = False
    await svc.manager.release(lease.lease_id, "signin_cancelled")
    return 200, svc.manager.last_end


LEASE_ID = r"(?P<lease_id>[0-9a-f-]{36})"
# (method, pattern, handler, body_max): browserd/app.py makes its Route of each.
ROUTES = [
    ("POST", re.compile(rf"/leases/{LEASE_ID}/handoff/done"), done, 1024),
    ("POST", re.compile(rf"/leases/{LEASE_ID}/handoff/cancel"), cancel, 1024),
]
