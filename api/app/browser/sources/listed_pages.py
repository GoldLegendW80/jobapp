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
older ones in an order that changes each day (sweep.listed_postings), each once a search
whatever website of it opens it (sweep.Plan.listed_tried).

What a page answers (`verdict`, accept and refuse table: tests/browser/test_j36_listed_pages.py):
a page that did not load (no page, a status of 500 or more or outside 100 to 599, or under
service.TEXT_MIN_CHARS characters of text on its own address: a shell whose content was not
there yet) and a wall (401, 403, 429, a sign in or check address, a security check or sign in
page, whatever its headings hold) are held: stored as a failed reading of their posting (a
wall's with its reason, `walled`) only once a later page of the reading answers otherwise;
HELD_MAX pages in a row that did not load end the reading (`no_page`), WALLS_MAX walls with no
other answer between them end it (`walled`), none of the held stored. A gone page (the liveness
rule's `closed`: 404 or 410 on its own address, or its page says it is closed) closes it as the
liveness check does. The posting's own page (`own_page`: never a list the session read before,
another posting or a search) that shows its title as B21 judges a page it opens for a report
(`finder.title_on`, the rule of FinderSession.page_finding) replaces the card's text through the
search's own store (sweep._page_read, service.page_text), the person's exclusions and never apply
rules met as there. Nothing reads the page's shape: a list of results served at the posting's own
address whose heading holds its title is read as the posting, as B27 reads it for a report.
Anything else is a failed reading, written once a day, and after service.PAGE_TRIES_DAYS such
days the posting is no longer tried. When no tab of its own could be made, nothing is stored and
the reading ends (`no_tab`).

Its ends (ENDS), each said in the summary: `done`, `time_bound` (Runner.listed_pages_s, a
page never started past it), `browser_wanted` (an application, or the person's own session,
waits for the browser: the reading ends and the website gives its lease back), `walled`,
`memory` (the memory guard closed a tab), `permission` (the website's permission, checked
before each page, was taken back), `store_failed` (the store refused, or the sweep ended),
`browser_lost` (the lease ended or the agent's tab could not be selected again), `no_tab`
(browserd made no tab of its own: its tabs full, the person holding the input, a refusal),
`no_page` (the website's pages did not load), `failed`. Its counts are what the store did
(sweep.ListedPage.done), never what a page answered. The pages held when it ends are not
stored: the day's order of the older postings keeps the same ones from standing first for good.
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
NO_PAGE = "no_page"  # a page that did not load: held
HELD_MAX = 3  # pages in a row that did not load: the website's pages do not load
WALLS_MAX = 2  # walls with no other answer between them: the website itself is walled
WALLED_STATUSES = frozenset({401, 403, 429})
WALLED_READS = frozenset({"bot_check", "sign_in"})  # liveness.judge's reasons of a wall
NO_BROWSER = frozenset({"lease_ended", "browser_unavailable", "guard_failed"})
NO_RUN = frozenset({"cancelled", "no_credential"})
PERMISSIONS = frozenset({"permission_revoked", "permission_unavailable", "permission_checkpoint"})
ENDS = frozenset(
    {"done", "time_bound", "browser_wanted", "walled", "memory", "permission", "store_failed",
     "browser_lost", "no_tab", "no_page", "failed"}
)  # fmt: skip


def own_page(requested: str, landed: str, lists: Collection[str] = ()) -> bool:
    """Whether the page the app landed on is the posting's own (canonical addresses): the
    liveness rule's (`liveness.own_page`), on a board the board's page of the same posting id
    (`same_posting`), and never another address that is a list the session read before
    (`lists`; the posting's own address is never refused for it, since any page of
    LIST_CARDS_MIN links, its header and footer included, counts as one). Fails closed."""
    if (landed != requested and landed in lists) or not liveness.own_page(requested, landed):
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
    """What the app's open of a posting's own address (`requested`) answered: NO_PAGE (it did
    not load, or holds under service.TEXT_MIN_CHARS characters of text), READ, GONE (with the
    liveness finding that closes it), WALLED or UNREAD. READ only on the posting's own page
    (`own_page`, `lists` the session's before the open), no wall whatever its headings hold, and
    with its title on it as B21 judges a page it opens for a report (`finder.title_on`, the rule
    of FinderSession.page_finding); only the liveness rule's `closed` closes it. Fails closed."""
    if not opened.landed or opened.odd_status or (opened.status or 0) >= 500:
        return NO_PAGE, None
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
    if page is None:
        return UNREAD, None  # an error status that is no wall nor gone
    if not own_page(requested, opened.landed, lists):
        return UNREAD, None
    if len(" ".join(page.text.split())) < service.TEXT_MIN_CHARS:
        return NO_PAGE, None
    return (READ if finder.title_on(page, title) is not None else UNREAD), None


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
    today = commits.clock().astimezone(sweep.SWEEP_TZ).date()
    postings = await asyncio.to_thread(
        sweep.listed_postings, commits.sessions, plan.user_id, site.host, _on_site(session),
        set(plan.known.carded), today,
    )  # fmt: skip
    tried: set[Any] = plan.listed_tried
    context = getattr(session.pages.tools, "context", None)
    platform = (
        platform_policy.platform_for(context)
        if context is not None
        else platform_policy.platform_of(session.start)
    )
    held: list[sweep.ListedPage] = []  # the walls and the pages that did not load, not stored yet
    unloaded = walls = 0  # of them: the pages in a row that did not load, the walls
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
        memory = session.tab_closed["memory"]
        lists = set(session.lists)  # before the open: the landed page's own links make no list
        opened = await session.open_page(url)
        if opened.tried:
            tried.add(posting.id)
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
                runner.base_url, lists,
            )  # fmt: skip
            one = _stored(session, site, posting, opened, page, found, closed)
            if found in (NO_PAGE, WALLED):
                held.append(one)
                unloaded = unloaded + 1 if found == NO_PAGE else 0
                walls += found == WALLED
                if unloaded >= HELD_MAX:
                    return "no_page"  # none of them stored: the website's pages do not load
                if walls >= WALLS_MAX:
                    return "walled"  # none of them stored: the website itself is walled
            else:
                if not await _saved(commits, site, [*held, one], counts):
                    return "store_failed"
                held, unloaded, walls = [], 0, 0
        if lost:
            return "memory"
        if stop:
            return "browser_lost"
    return "done"


async def _saved(commits: Any, site: Any, pages: list[sweep.ListedPage], counts: dict) -> bool:
    """Store each page in order (Commits.save) and count what the store did; False when it
    refused one (the later ones are not stored)."""
    for listed in pages:
        try:
            await commits.save(site.source_id, site.host, None, listed=listed)
        except Exception:
            return False
        if listed.done:
            counts[listed.done] += 1
        counts["given_up"] += listed.given_up
    return True


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
    failed reading (with its reason for a wall)."""
    if found == WALLED:
        return sweep.ListedPage(posting.id, posting.url, UNREAD, reason=WALLED)
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
