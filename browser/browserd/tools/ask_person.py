"""browser_ask_person(reason) (browser B20): the website search's agent asks the person for help,
in one sentence: solve a security check or a captcha, accept something, sign in again. On the
owner's Mac the person is there; on the Jetson the person may be watching the live view (the
Account card or the run's page, B18), or not.

- Nobody watching (no live view of this lease open now): `nobody_watching` at once, nothing
  changes; the agent goes on another way or ends the website with its reason.
- Watched: the lease goes `waiting_person` (reason ASK, the tab's host shown, never the model's
  words: a page can steer them, B12b), the tool answers `waiting_person` with the seconds left,
  and the api polls the lease until it is active again: the person took over (live.Live.take)
  and gave it back, or TAKEOVER_S passed with nobody taking over (live.Live.tick resumes it). No
  Chromium restart: the debugging port stays, as in any takeover, and the person's input never
  mixes with the agent's (the agent's calls wait while the lease waits).
"""

from typing import Any
from urllib.parse import urlsplit

from browserd import handoff, live
from browserd.live import TAKEOVER_S

ASK = "ask_person"
ARGS = frozenset({"reason"})
NOBODY = "Nobody is watching this browser now: go on another way, or end the website."


def watched(svc: Any, lease: Any) -> bool:
    """A live view of that lease is open now (its websocket counted, not one still starting)."""
    return any(v.lease_id == lease.lease_id and not v.closed for v in live.live_of(svc).viewers)


async def run(tab: Any, args: dict[str, Any]) -> dict[str, Any]:
    reason = handoff.plain(args.get("reason"), handoff.REASON_MAX)
    if set(args) - ARGS or not reason:
        return handoff.failure("bad_args", "reason is one sentence for the person.")
    svc = handoff.service_of(tab.request)
    lease = svc.manager.lease_of(tab.request) if svc is not None else None
    if lease is None:
        return handoff.failure("lease_ended", "The lease ended.")
    if not watched(svc, lease):
        return handoff.failure("nobody_watching", NOBODY)
    page = await tab.page()
    host = ""
    try:
        host = (urlsplit(page.url).hostname or "")[:253]
    except ValueError:
        host = ""
    lease.wait_person(svc.manager.clock(), ASK, host, TAKEOVER_S)
    return handoff.failure(
        "waiting_person",
        "The person is asked in the live view; wait for the answer.",
        left_s=TAKEOVER_S,
    )
