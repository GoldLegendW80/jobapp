"""Company career sites (browser B9): a source of kind `site` is a careers page address, read by
the finder agent (app/browser/finder.py) in the person's browser, its postings validated by code
and stored as the other sources' are (app/browser/sources/__init__.py).

The steps of one site, in the source's own lease (which opens the careers page's host only):
1. Before any lease (`Site.check`): the address must be an http or https address whose host
   browserd accepts (`finder.canonical`: ASCII or IDN in its `xn--` form, no IP v6 literal, no
   underscore) and is not LinkedIn's (`finder.NOT_SITES`), `bad_address` otherwise; the person
   must have a credential for the agents (else `no_credential`: the source is skipped with that
   reason, nothing is opened). An address on Ashby, Greenhouse or Lever (`ats_board`) is not
   read at all: the board is added to the person's swept boards at once (`add_board`, the
   owner's decision of 2026-10-20), which read it whole and for free, and the entry says so
   (`board: {ats, org, added}`; `added` is false when it was there already).
2. Code opens the careers page (the first of PAGES_MAX pages; the lease opens its host and the
   host's `www.` twin, `finder.host_pair`, so http to https and apex to www redirects load) and
   looks at its links: when they lead to postings on one of those three (an embedded Greenhouse
   board, "Apply" links to jobs.lever.co, ...), the site is handled the same way (`ats_board`)
   and the agent does not run. A redirect to any other host (an ATS's own page included) is
   blocked by browserd and ends as `page_failed`.
3. The finder runs over the open page (`finder.FinderSession`). What it reported and code kept is
   stored, also when the run stopped: `token_budget` (the run's token budget is used up),
   `agent_failed` (the run ended in any other way but done: its turn limit, an error), and the
   page codes of B8 (`lease_ended`, `lease_budget`, `time_budget`; the run itself is cut at
   the sweep's deadline as `time_budget`). A run that used its
   PAGES_MAX pages read all it was allowed to: no code.

The entry of the source in the sweep's stats also holds `refused` (the codes of what code
refused the agent, by count: `other_host` for a page that sent it to another site) and the
agent's tokens.

ATS addresses (`ats_board`, a pure rule, tests/browser/test_b9_site.py): https or http, the host
one of ATS_HOSTS exactly (case ignored), and the board's name in the place that host keeps it:
the first path segment (`jobs.ashbyhq.com/<org>`, `jobs.lever.co/<org>`, `jobs.eu.lever.co/<org>`,
`boards.greenhouse.io/<org>`, `job-boards.greenhouse.io/<org>`, and their `.eu.` hosts), or the
`for` query of Greenhouse's embed (`boards.greenhouse.io/embed/job_board?for=<org>`). The name
must match the boards' own pattern (profile ORG_PATTERN). Anything else is not a board: the
agent reads the site as it is. Declared open point: an ATS behind the company's own domain
(a CNAME such as jobs.example.com) is not recognised, and is read by the agent.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urljoin, urlsplit

from app.browser import finder
from app.browser.sources import Pages, Read, SourceError, Wanted, parse_snapshot
from app.jobs import ats
from app.profile.schemas import LINE_MAX, ORG_PATTERN, BrowserSource

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

ATS_HOSTS = {
    "jobs.ashbyhq.com": "ashby",
    "boards.greenhouse.io": "greenhouse",
    "boards.eu.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "job-boards.eu.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever",
}
# Path segments of these hosts that are not a board's name.
NOT_ORGS = frozenset({"embed", "api", "v1", "static", "assets", "favicon.ico", "robots.txt"})
_ORG = re.compile(ORG_PATTERN)


@dataclass(frozen=True)
class Board:
    ats: str
    org: str


def board_of(url: str) -> Board | None:
    """The API board an address belongs to, None when it is not one (module docstring)."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or parts.username or parts.password:
        return None
    name = ATS_HOSTS.get((parts.hostname or "").rstrip(".").lower())
    if name is None:
        return None
    segments = [s for s in parts.path.split("/") if s]
    org = ""
    if name == "greenhouse" and segments[:2] == ["embed", "job_board"]:
        values = parse_qs(parts.query).get("for", [])
        org = values[0] if len(values) == 1 else ""
    elif segments and segments[0].lower() not in NOT_ORGS:
        org = segments[0]
    if not org or len(org) > 100 or ".." in org or not _ORG.fullmatch(org):
        return None
    return Board(name, org)


def board_on_page(text: str, page_url: str) -> Board | None:
    """The one board the page's links lead to: the page itself, an iframe's links or "Apply"
    links. None when no link is a board's, or when they name several boards (then the agent
    reads the site)."""
    found = {board_of(page_url)} - {None}
    for node in parse_snapshot(text).walk():
        if node.url:
            board = board_of(urljoin(page_url, node.url))
            if board is not None:
                found.add(board)
    return found.pop() if len(found) == 1 else None


def address(source: BrowserSource) -> str:
    """The careers page of the source (its query), canonical; "" when it is not an address."""
    return finder.canonical(source.query) if "://" in source.query else ""


def company_of(host: str) -> str:
    """The company a site's postings are stored under: its host without the usual prefixes."""
    name = host.lower().split(":")[0]
    for prefix in ("www.", "careers.", "jobs.", "career.", "join."):
        if name.startswith(prefix) and name.count(".") > 1:
            name = name[len(prefix) :]
    return name


def same_site(start: str, opened: str) -> bool:
    """Whether the careers page ended on its own site after redirects: http to https (or back),
    the host to its `www.` twin (or back), the same port. Both canonical."""
    a, b = finder.site_of(start), finder.site_of(opened)
    return a is not None and b is not None and a[1] == b[1]


def company_for(source: BrowserSource, board: Board) -> str:
    """The company of a board found from a source: the site's own name (company_of), or the
    board's name when the source's address is the board itself."""
    url = address(source)
    host = urlsplit(url).hostname or ""
    if not host or board_of(url) is not None:
        return board.org
    return company_of(host)


def add_board(db: Session, user_id: uuid.UUID, board: Board, company: str, now: datetime) -> bool:
    """Add the board to the person's swept boards (owner, 2026-10-20: at once, the person sees
    it in the Search tab and can remove it), in the caller's transaction. False, nothing
    written, when it is there already (same ATS, org case ignored, enabled or not), the search
    section was never saved or does not validate, or it holds the most boards it can."""
    from app.profile import service
    from app.profile.schemas import validate_section

    for _ in range(2):
        profile = service.get_profile(db, user_id, now)
        raw = (profile.sections or {}).get("search")
        if not isinstance(raw, dict):
            return False
        boards = raw.get("boards") if isinstance(raw.get("boards"), list) else []
        if any(
            isinstance(b, dict)
            and b.get("ats") == board.ats
            and str(b.get("org", "")).lower() == board.org.lower()
            for b in boards
        ):
            return False
        new = {"company": company[:LINE_MAX], "ats": board.ats, "org": board.org, "enabled": True}
        try:
            data = validate_section("search", raw | {"boards": [*boards, new]})
        except ValueError:
            return False  # full, or a section the person must save again
        try:
            service.save_section(db, user_id, "search", data, profile.revision, now)
        except service.StaleRevisionError:
            continue  # saved meanwhile: read it again
        return True
    return False


class Site:
    """The reader of a `site` source (sources.Reader, plus `check`)."""

    def __init__(self, agent: finder.FinderAgent | None) -> None:
        self.agent = agent

    def host(self, base_url: str | None) -> str:  # sources.Reader; Runner calls check instead
        return ""

    async def check(
        self, source: BrowserSource, user_id: str, entry: dict[str, object]
    ) -> tuple[str, str | None]:
        """The host to lease, or the code that skips the source before any lease."""
        url = address(source)
        if not url or finder.not_a_site(url):
            return "", "bad_address"  # LinkedIn: its own source only (finder.NOT_SITES)
        board = board_of(url)
        if board is not None:
            entry["board"] = {"ats": board.ats, "org": board.org}
            return "", "ats_board"
        if self.agent is None or not await self.agent.available(user_id):
            return "", "no_credential"
        return urlsplit(url).hostname or "", None

    async def read(
        self, pages: Pages, source: BrowserSource, wanted: Wanted, read: Read, base: str | None
    ) -> None:
        if self.agent is None:  # check() answered no_credential before any lease
            raise SourceError("no_credential")
        start = address(source)
        await pages.open(start)
        read.result_pages = 1
        opened = finder.canonical(pages.last_url)
        if not opened or not same_site(start, opened):
            # No address, or another site: browserd lets only the lease's two hosts load
            # (finder.host_pair), so this is a page it should not have shown. Nothing is read.
            raise SourceError("page_failed")
        board = board_on_page(pages.last_text, opened)
        if board is not None:
            read.extra["board"] = {"ats": board.ats, "org": board.org}
            raise SourceError("ats_board")
        session = finder.FinderSession(pages, opened, pages.last_text, user_id=read.user_id)
        # The run ends with the sweep's time, before the job's own timeout would cancel it
        # (worker.SWEEP_JOB_TIMEOUT_S): what was validated stays.
        remaining = max(pages.deadline - pages.monotonic(), 0.0)
        try:
            async with asyncio.timeout(remaining):
                ran = await self.agent(session)
        except TimeoutError:
            ran = finder.Ran(stopped="time_budget")
        except Exception:
            ran = finder.Ran(stopped="error")
        finally:
            read.result_pages = pages.count
            company = company_of(urlsplit(opened).netloc)
            read.postings = [
                (
                    company,
                    ats.Posting(
                        title=ats.storable(f.title),
                        location=ats.storable(f.location),
                        url=f.url,
                        published="",
                        text=ats.storable(f.text)[: finder.TEXT_MAX],
                        remote=False,
                    ),
                )
                for f in session.found
            ]
            read.extra["refused"] = dict(Counter(session.refused))
        read.extra["tokens"] = {"input": ran.input_tokens, "output": ran.output_tokens}
        if session.stopped and session.stopped != "page_budget":
            raise SourceError(session.stopped)  # page_budget: it read all it was allowed to
        if ran.stopped in ("token_budget", "time_budget"):
            raise SourceError(ran.stopped)
        if ran.stopped == "no_credential":
            raise SourceError("no_credential")
        if ran.stopped:
            raise SourceError("agent_failed")
