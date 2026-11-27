"""Jobs J36: after a website's agent ends in a search, the app reads by itself the pages of the
postings the person holds from a card of that website (jobs J31), so they get their text.

Owner, 2026-11-27, asked "Est-ce que l'app doit ouvrir elle-même ces pages après chaque
recherche, une à la fois, au rythme d'une personne ?": "Oui", LinkedIn signed in included.

Where it runs: in the website's part of the search, on its lease, after its agent (`read`,
called by interactive._search), when the agent ended or stopped with a stop after which the
store still works (finder.STORES_AFTER_STOP), but not after the person's Cancel nor after a stop
that leaves no browser to read with (NO_BROWSER). No model runs and no token is spent: code opens
each page as the app opens a reported posting (B27, `FinderSession.open_page`: a tab of its own,
2 to 4 s apart, browserd's navigate under the same line, egress and request floor, the tab
closed and the agent's selected again), one at a time, only the postings whose address is on the
website's own hosts (finder.on_lease_hosts), those this search kept from a card first, then the
older ones, the oldest first (sweep.listed_postings), each once a search whatever website of it
opens it (sweep.Plan.listed_tried).

What a page answers (`verdict`, accept and refuse table: tests/browser/test_j36_listed_pages.py):
a wall (401, 403, 429, a sign in or check address, a security check or sign in page, whatever
its headings hold) leaves the posting as it is and ends the website's reading; a gone page (the
liveness rule's `closed`: 404 or 410 on its own address, or its page says it is closed) closes
it as the liveness check does; the posting's own page (`own_page`: never a list, another posting
or a search) that shows its title and at least service.TEXT_MIN_CHARS characters of text
replaces the card's text through the search's own store (sweep._page_read, service.page_text),
the person's exclusions and never apply rules met as there; anything else is a failed reading,
written once a day, and after service.PAGE_TRIES_DAYS such days the posting is no longer tried.
When no tab of its own could be made, nothing is stored and the reading ends (`no_tab`).

Its ends (ENDS), each said in the summary: `done`, `time_bound` (Runner.listed_pages_s, a
page never started past it), `browser_wanted` (an application, or the person's own session,
waits for the browser: the reading ends and the website gives its lease back), `walled`,
`memory` (the memory guard closed a tab), `permission` (the website's permission, checked
before each page, was taken back), `store_failed` (the store refused, or the sweep ended),
`browser_lost` (the lease ended or the agent's tab could not be selected again), `no_tab`
(browserd made no tab of its own: its tabs full, the person holding the input, a refusal),
`failed`. Its counts are what the store did (sweep.ListedPage.done), never what a page answered.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import logging
from collections.abc import Callable, Collection
from typing import Any

from app.browser import finder, platform_policy, same_posting
from app.browser.leases import StoreUnavailableError
from app.browser.sources import PAGE_LOAD_S
from app.jobs import liveness, service, sweep

logger = logging.getLogger("app.browser.sources.listed_pages")

READ, GONE, WALLED, UNREAD = "read", "gone", "walled", "unread"
WALLED_STATUSES = frozenset({401, 403, 429})
WALLED_READS = frozenset({"bot_check", "sign_in"})  # liveness.judge's reasons of a wall
# The rules of `finder.title_on` that show the posting's own title: B21's loose primary heading
# (one meaningful word shared) is the agent's reading's, never code's alone.
TITLE_RULES = frozenset({"heading", "page_text"})
NO_BROWSER = frozenset({"lease_ended", "browser_unavailable", "guard_failed"})
NO_RUN = frozenset({"cancelled", "no_credential"})
PERMISSIONS = frozenset({"permission_revoked", "permission_unavailable", "permission_checkpoint"})
ENDS = frozenset(
    {"done", "time_bound", "browser_wanted", "walled", "memory", "permission", "store_failed",
     "browser_lost", "no_tab", "failed"}
)  # fmt: skip


def own_page(requested: str, landed: str, lists: Collection[str] = ()) -> bool:
    """Whether the page the app landed on is the posting's own (canonical addresses): the
    liveness rule's (`liveness.own_page`), on a board the board's page of the same posting id
    (`same_posting`), and never a list the session read (`lists`). Fails closed."""
    if landed in lists or not liveness.own_page(requested, landed):
        return False
    if same_posting.board_of(requested) is None:
        return True
    posting = same_posting.posting_id(requested)
    return (
        posting is not None
        and same_posting.posting_id(landed) == posting
        and same_posting.posting_page(landed)
    )


def verdict(
    opened: finder.AppOpen,
    requested: str,
    page: finder.PageRead | None,
    snapshot: str,
    title: str,
    platform: str,
    base_url: str | None = None,
    lists: Collection[str] = (),
) -> tuple[str, liveness.Finding | None]:
    """What the app's open of a posting's own address (`requested`) answered: READ, GONE (with
    the liveness finding that closes it), WALLED or UNREAD. Fail closed: a wall is never read
    as the posting whatever its headings hold, a page that is not the posting's own (`own_page`,
    `lists` the session's) is never read, and only the liveness rule's `closed` closes it."""
    if not opened.landed:
        return UNREAD, None
    if opened.status in WALLED_STATUSES or platform_policy.wall(
        platform, opened.landed, "", base_url
    ):
        return WALLED, None
    text = "\n".join(page.lines) if page is not None else ""
    judged = liveness.judge(opened.status or 200, text, requested, opened.landed)
    if judged.verdict == liveness.CLOSED:
        return GONE, judged
    walled = platform_policy.wall(platform, opened.landed, snapshot, base_url)
    if judged.reason in WALLED_READS or walled:
        return WALLED, None
    if page is None or not own_page(requested, opened.landed, lists):
        return UNREAD, None
    shown = finder.title_on(page, title)
    long_enough = len(" ".join(page.text.split())) >= service.TEXT_MIN_CHARS
    if shown is not None and shown[1] in TITLE_RULES and long_enough:
        return READ, None
    return UNREAD, None


async def _wanted(leases: Any) -> bool:
    """Whether an application, or a person's own session, waits for the browser
    (`Leases.waiters`, the tiers before the sweeps'): it comes first. A lease store that cannot
    say counts as one waiting."""
    waiters = getattr(leases, "waiters", None)
    if waiters is None:
        return False
    try:
        return bool(await waiters())
    except StoreUnavailableError:
        return True


async def read(
    session: finder.FinderSession, ran_stopped: str, *, site: Any, runner: Any, plan: Any
) -> None:
    """The website's reading of the pages of its postings seen only in a list (module
    docstring), its counts and its end in `site.listed_pages`. The agent's stop stays the
    website's; nothing raised here changes its result."""
    commits = getattr(plan, "commits", None)
    stop = session.stopped
    if (
        commits is None
        or not site.host
        or not runner.listed_pages_s
        or ran_stopped in NO_RUN
        or stop in NO_BROWSER
        or (stop and stop not in finder.STORES_AFTER_STOP)
    ):
        return
    counts = {READ: 0, "closed": 0, UNREAD: 0, "given_up": 0}
    session.stopped = ""
    try:
        ended = await _pages(session, site, runner, plan, commits, counts)
    except Exception as error:
        logger.warning("reading the listed postings' pages failed: error=%s", type(error).__name__)
        ended = "failed"
    finally:
        session.stopped = stop
    entry: dict[str, Any] = {**counts, "ended": ended}
    try:
        entry["left"] = await asyncio.to_thread(
            sweep.listed_left, commits.sessions, plan.user_id, site.host, _on_site(session)
        )
    except Exception as error:
        logger.warning("counting the listed postings left failed: error=%s", type(error).__name__)
    site.listed_pages = entry


async def _pages(
    session: finder.FinderSession,
    site: Any,
    runner: Any,
    plan: Any,
    commits: Any,
    counts: dict[str, int],
) -> str:
    started = runner.monotonic()
    postings = await asyncio.to_thread(
        sweep.listed_postings, commits.sessions, plan.user_id, site.host, _on_site(session),
        set(plan.known.carded),
    )  # fmt: skip
    tried: set[Any] = plan.listed_tried
    context = getattr(session.pages.tools, "context", None)
    platform = (
        platform_policy.platform_for(context)
        if context is not None
        else platform_policy.platform_of(session.start)
    )
    for posting in postings:
        if sweep.abandoned(plan):
            return "store_failed"
        latest = runner.monotonic() + finder.APP_OPEN_PAUSE_S[1] + PAGE_LOAD_S
        if latest > started + runner.listed_pages_s:
            return "time_bound"
        if await _wanted(runner.leases):
            return "browser_wanted"
        await session._permitted()
        if session.stopped:
            return "permission"
        url = finder.canonical(posting.url)
        if not url or posting.id in tried:
            continue  # another website of this search on the same host opened it
        tried.add(posting.id)
        memory = session.tab_closed["memory"]
        opened = await session.open_page(url)
        stop = session.stopped
        if stop in PERMISSIONS:
            return "permission"
        lost = session.tab_closed["memory"] > memory
        if not opened.tried:  # no page was opened: nothing is the posting's
            return "memory" if lost else "browser_lost" if stop else "no_tab"
        if opened.landed or not (lost or stop):
            page = session.pages_read.get(opened.landed) if opened.read else None
            found, closed = await asyncio.to_thread(
                verdict, opened, url, page, session.pages.last_text, posting.title, platform,
                runner.base_url, set(session.lists),
            )  # fmt: skip
            if found == WALLED:
                return "walled"
            listed = _stored(session, site, posting, opened, page, found, closed)
            try:
                await commits.save(site.source_id, site.host, None, listed=listed)
            except Exception:
                return "store_failed"
            if listed.done:
                counts[listed.done] += 1
            counts["given_up"] += listed.given_up
        if lost:
            return "memory"
        if stop:
            return "browser_lost"
    return "done"


def _on_site(session: finder.FinderSession) -> Callable[[str], bool]:
    """Whether a posting's address is on the website's own hosts (finder.on_lease_hosts)."""
    return functools.partial(finder.on_lease_hosts, start=session.start)


def _stored(
    session: finder.FinderSession,
    site: Any,
    posting: sweep.Listed,
    opened: finder.AppOpen,
    page: finder.PageRead | None,
    found: str,
    closed: liveness.Finding | None,
) -> sweep.ListedPage:
    """What the store gets for one page: the page's finding (B21's rule, the card's company and
    place when the page labels none) with the person's exclusion it meets, a closure, or a
    failed reading."""
    if found == GONE:
        return sweep.ListedPage(posting.id, posting.url, GONE, closed=closed)
    if found == READ and page is not None:
        item = {"title": posting.title, "company": posting.company, "location": posting.location}
        finding, _why = session.page_finding(item, opened.landed, page)
        if finding is not None:
            finding = dataclasses.replace(
                finding,
                company=finding.company or posting.company,
                location=finding.location or posting.location,
            )
            excluded = site._excluded(finding.title, finding.location)
            return sweep.ListedPage(posting.id, posting.url, READ, finding, excluded)
    return sweep.ListedPage(posting.id, posting.url, UNREAD)
