"""Company career sites (browser B9): a source of kind `site` is a careers page address, read by
the finder agent (app/browser/finder.py) in the person's browser, its postings validated by code
and stored as the other sources' are (app/browser/sources/__init__.py).

The steps of one site, in the source's own lease (which opens the careers page's host only):
1. Before any lease (`Site.check`): the address must be an http or https address with a host
   (`bad_address` otherwise), and the person must have a credential for the agents (else
   `no_credential`: the source is skipped with that reason, nothing is opened). An address on
   Ashby, Greenhouse or Lever (`ats_board`) is not read at all: the entry names the board
   (`board: {ats, org}`) so it can be added as an API board, which reads it whole and for free.
2. Code opens the careers page (the first of PAGES_MAX pages) and looks at its links: when they
   lead to postings on one of those three (an embedded Greenhouse board, "Apply" links to
   jobs.lever.co, ...), the site is reported the same way (`ats_board`) and the agent does not
   run.
3. The finder runs over the open page (`finder.FinderSession`). What it reported and code kept is
   stored, also when the run stopped: `token_budget` (the run's token budget is used up),
   `agent_failed` (the run ended in any other way but done: its turn limit, an error), and the
   page codes of B8 (`lease_ended`, `lease_budget`, `time_budget`). A run that used its
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

import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit

from app.browser import finder
from app.browser.sources import Pages, Read, SourceError, Wanted, parse_snapshot
from app.jobs import ats
from app.profile.schemas import ORG_PATTERN, BrowserSource

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
        if not url:
            return "", "bad_address"
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
        opened = finder.canonical(pages.last_url) or start
        if finder.origin(opened) != finder.origin(start):
            # Redirected to another site: an ATS's own page perhaps, never read by the agent.
            board = board_of(opened)
            if board is not None:
                read.extra["board"] = {"ats": board.ats, "org": board.org}
                raise SourceError("ats_board")
            raise SourceError("page_failed")
        board = board_on_page(pages.last_text, opened)
        if board is not None:
            read.extra["board"] = {"ats": board.ats, "org": board.org}
            raise SourceError("ats_board")
        session = finder.FinderSession(pages, opened, pages.last_text, user_id=read.user_id)
        try:
            ran = await self.agent(session)
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
        if ran.stopped == "token_budget":
            raise SourceError("token_budget")
        if ran.stopped == "no_credential":
            raise SourceError("no_credential")
        if ran.stopped:
            raise SourceError("agent_failed")
