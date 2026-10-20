"""The worker's side of the sign in hand off (browser B12a): what an apply adapter, or an agent's
binding of the browser tools, calls when it meets a login or sign up wall on the lease it holds.

`request_signin(tools, site, reason)` calls browserd's tool `browser_request_signin`
(browser/browserd/handoff.py), which answers at once:
- `nobody_watching` (a sweep or full_auto lease, or no live view of the person open): the lease
  is untouched and the caller's module keeps the work as `needs_person: signin`;
- `waiting_person`: the person signs in on a Chromium without a debugging port. The api's copy
  of the lease waits too (`Leases.wait_person`, with the seconds browserd has left, so the api
  never ends it at its maximum plus MAX_GRACE_S), and this polls browserd every POLL_S until:

    signed_in         "I'm signed in": the api's copy resumes; the text names the page's title
    signin_cancelled  "Cancel": browserd released the lease
    signin_timeout    browserd's deadline passed; or, with no answer from browserd until the
                      deadline plus MAX_GRACE_S, this releases the lease itself
    ended             the lease ended otherwise (a crash, a Chromium that did not start)
    turn_ended        the caller's `budget_s` ran out first: the lease still waits, the caller
                      ends its turn and its module queues the work again (B12b's later hand off)

A second call while one waits (browserd answers 409 `waiting_person`) polls the same hand off,
until the deadline browserd still counts (its `wait_left_s`; a whole BROWSER_SESSION_MAX_S only
when browserd cannot say).
`Outcome.text()` is the sentence an agent reads: the page's title in it is data, quoted, cut and
stripped of control characters. Nothing here logs the site, the reason or the title.
"""

import asyncio
import contextlib
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.browser.client import BrowserError, ended_of
from app.browser.leases import MAX_GRACE_S, StoreUnavailableError
from app.browser.tools import LeaseTools

POLL_S = 2.0
TITLE_MAX = 120
# The outcomes after which the work waits as `needs_person: signin` in its own module.
NEEDS_PERSON = ("nobody_watching", "signin_cancelled", "signin_timeout", "turn_ended")
TEXTS = {
    "nobody_watching": "Nobody is watching the browser now. Stop here: the work waits for the "
    "person to sign in.",
    "signin_cancelled": "The person cancelled the sign in. Stop here.",
    "signin_timeout": "The person did not sign in within the time allowed. Stop here.",
    "turn_ended": "The person is still signing in and this run cannot wait longer. Stop here: "
    "the work is queued again once the person is done.",
    "ended": "The browser session ended. Stop here.",
}


@dataclass(frozen=True)
class Outcome:
    code: str
    title: str = ""
    detail: str = ""  # browserd's own sentence for a refusal (bad_args, host_not_allowed, ...)

    @property
    def needs_person(self) -> bool:
        return self.code in NEEDS_PERSON

    def text(self) -> str:
        if self.code == "signed_in":
            if self.title:
                return f'The person signed in; the page is now "{self.title}".'
            return "The person signed in; take a snapshot to see the page."
        if self.code in TEXTS:
            return TEXTS[self.code]
        return f"The sign in could not be asked: {self.detail or self.code}"


def plain(raw: Any, limit: int) -> str:
    if not isinstance(raw, str):
        return ""
    text = "".join(" " if unicodedata.category(ch)[0] in "CZ" else ch for ch in raw[: limit * 4])
    return " ".join(text.split())[:limit]


def _seconds(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else None


async def request_signin(
    tools: LeaseTools,
    site: str,
    reason: str,
    *,
    budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> Outcome:
    """Asks the person to sign in on the lease `tools` holds, and waits for the end of the hand
    off at most until its deadline (and `budget_s`, when the caller cannot wait that long).
    Raises BrowserError when browserd cannot be reached at the first call, or the lease ends in
    it (`ended`: a Chromium that could not be swapped, a cancel)."""
    try:
        answer = await tools.ask_signin(site, reason)
    except BrowserError as exc:
        if exc.code != "waiting_person":
            raise
        answer = {"ok": False, "error": "waiting_person"}
    code = answer.get("error")
    if code != "waiting_person":
        if code == "nobody_watching":
            return Outcome("nobody_watching")
        message = answer.get("message")
        return Outcome(
            code if isinstance(code, str) else "refused",
            detail=plain(message, 200) if isinstance(message, str) else "",
        )
    left = _seconds(answer.get("left_s"))
    if left is None:  # a second call: the hand off under way has its own seconds left
        left = await _left_now(tools)
    left = tools.leases.session_max_s if left is None else min(left, tools.leases.session_max_s)
    return await _poll(tools, site, clock() + left + MAX_GRACE_S, budget_s, clock, sleep)


async def _left_now(tools: LeaseTools) -> int | None:
    """browserd's seconds left on the wait of this lease, None when it cannot say."""
    try:
        found = await tools.client.current()
    except BrowserError:
        return None
    lease = found.get("lease")
    if not isinstance(lease, dict) or lease.get("lease_id") != tools.lease_id:
        return None
    return _seconds(lease.get("wait_left_s"))


async def _poll(
    tools: LeaseTools,
    site: str,
    end_at: float,
    budget_s: float | None,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[Any]],
) -> Outcome:
    user_id, lease_id = tools.user_id, tools.lease_id
    budget_end = None if budget_s is None else clock() + budget_s
    synced = False
    while True:
        try:
            found: dict[str, Any] | None = await tools.client.current()
        except BrowserError:
            found = None  # browserd restarting: asked again, within the deadline
        if found is not None:
            lease = found["lease"]
            if lease is None or lease.get("lease_id") != lease_id:
                last = found["last_end"]
                mine = last is not None and last.get("lease_id") == lease_id
                reason = ended_of(last).reason if mine else "ended"
                return Outcome(reason if reason in TEXTS else "ended")
            try:
                if lease.get("state") == "waiting_person" and not synced:
                    shown = plain(lease.get("wait_site"), 80) or plain(site, 80)
                    left = _seconds(lease.get("wait_left_s"))
                    await tools.leases.wait_person(user_id, lease_id, "signin", shown, left)
                    synced = True
                elif lease.get("state") == "active":
                    await tools.leases.resume(user_id, lease_id)
                    return Outcome("signed_in", title=await _title(tools))
            except StoreUnavailableError:
                pass  # Redis away: the api's copy is set on the next round
        now = clock()
        if now >= end_at:
            with contextlib.suppress(StoreUnavailableError):
                await tools.leases.release(user_id, lease_id, reason="signin_timeout")
            return Outcome("signin_timeout")
        if budget_end is not None and now >= budget_end:
            return Outcome("turn_ended")
        await sleep(POLL_S)


async def _title(tools: LeaseTools) -> str:
    """The title of the tab the agent acts on, read through the tool layer (so a tab the person
    left off the lease's list is closed first and never read)."""
    try:
        listed = await tools.tabs()
    except BrowserError:
        return ""
    for tab in listed.get("tabs") or []:
        if isinstance(tab, dict) and tab.get("current"):
            return plain(tab.get("title"), TITLE_MAX).replace('"', "'")
    return ""
