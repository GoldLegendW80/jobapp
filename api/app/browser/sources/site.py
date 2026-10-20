"""Company career sites (browser B9): a source of kind `site` is a careers page address, read by
the finder agent (app/browser/finder.py) in the person's browser, its postings validated by code
and stored as the other sources' are (app/browser/sources/__init__.py).

The steps of one site, in the source's own lease (which opens the careers page's host only):
1. Before any lease (`Site.check`): the address must be an http or https address whose host
   browserd accepts (`finder.canonical`: ASCII or IDN in its `xn--` form, no IP v6 literal, no
   underscore) and is not LinkedIn's (`finder.NOT_SITES`), `bad_address` otherwise; the person
   must have a credential for the agents (else `no_credential`: the source is skipped with that
   reason, nothing is opened). An address on Ashby, Greenhouse or Lever (`ats_board`) is not
   read at all (the person typed it: strong evidence): the board is added to the person's swept
   boards at once (`add_board`, the owner's decision of 2026-10-20), which read it whole and for
   free, and the entry says so (`board: {ats, org, added}`; `added` is false when it was there
   already, and `removed_by_person` is true when the person once had it and removed it: a
   removal sticks). A board added during a sweep is first read by the next sweep.
2. Code opens the careers page (the first of PAGES_MAX pages; the lease opens its host and the
   host's `www.` twin, `finder.host_pair`, so http to https and apex to www redirects load) and
   looks at its links (`board_on_page`). On strong evidence that the site's postings live on
   one of those three boards (an iframe whose address is the board, or most of the page's
   posting like links leading to one board), the site is handled the same way (`ats_board`)
   and the agent does not run. Weaker evidence (one stray "Apply" link, a footer or partner
   link: page content is untrusted) adds nothing: the agent reads the site, and the entry only
   reports the board (`board: {ats, org, added: false}`). A redirect to any other host (an
   ATS's own page included) is blocked by browserd and ends as `page_failed`.
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


# Path segments that mark a link of the site itself as a posting's (or a list of them) when
# another segment follows: /jobs/ai-engineer, /fr/offres/123. The last segment alone is a list.
POSTING_WORDS = frozenset(
    {"job", "jobs", "career", "careers", "position", "positions", "opening", "openings", "role",
     "roles", "vacancy", "vacancies", "posting", "postings", "offre", "offres", "emploi",
     "emplois", "stelle", "stellen", "apply", "opportunities"}
)  # fmt: skip
# A board is the site's only on strong evidence (BOARD_LINKS_MIN of the page's posting like
# links, and over half of them, lead to it); anything less is only reported.
BOARD_LINKS_MIN = 3


def _site_posting(url: str, page_url: str) -> bool:
    """Whether a link of the careers page's own site looks like a posting's: under the careers
    page's path, or a POSTING_WORDS segment followed by another one."""
    page = finder.canonical(page_url)
    link = finder.canonical(url)
    if not page or not link or not same_site(page, link):
        return False
    base = urlsplit(page).path.rstrip("/")
    path = urlsplit(link).path
    if base and path.startswith(base + "/") and path.rstrip("/") != base:
        return True
    segments = [s.lower() for s in path.split("/") if s]
    return any(s in POSTING_WORDS for s in segments[:-1])


def board_on_page(text: str, page_url: str) -> tuple[Board | None, bool]:
    """The board the careers page leads to, and whether the evidence is strong (then the board
    is the site's: it is added and the agent does not run). Strong: the page itself is the
    board; an iframe whose address is the board (Greenhouse's `embed/job_board?for=` included);
    or BOARD_LINKS_MIN or more of the page's posting like links (to a board, or to a posting of
    the site itself), and over half of them, lead to one board. Anything less (a footer link,
    a partner's or one stray "Apply" link) is weak: the agent reads the site, and the board is
    only reported, when the page links exactly one. (None, False) when no link is a board's."""
    own = board_of(page_url)
    if own is not None:
        return own, True
    links: dict[str, Board | None] = {}  # one vote per address: a title and its Apply link
    for node in parse_snapshot(text).walk():
        if not node.url:
            continue
        url = urljoin(page_url, node.url).split("#", 1)[0]
        board = board_of(url)
        if node.role == "iframe" and board is not None:
            return board, True
        if board is not None or _site_posting(url, page_url):
            links[url] = board
    votes = Counter(b for b in links.values() if b is not None)
    if not votes:
        return None, False
    top, count = votes.most_common(1)[0]
    if count >= BOARD_LINKS_MIN and count * 2 > len(links):
        return top, True
    return (top, False) if len(votes) == 1 else (None, False)


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


def _same_board(entry: object, board: Board) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("ats") == board.ats
        and str(entry.get("org", "")).lower() == board.org.lower()
    )


def removed_by_person(db: Session, user_id: uuid.UUID, board: Board) -> bool:
    """Whether a saved revision of the person's search section ever held the board (same ATS,
    org case ignored): once the board is not in the section now, the person removed it."""
    import sqlalchemy as sa

    from app.profile.models import ProfileRevision

    rows = db.execute(
        sa.select(ProfileRevision.data).where(
            ProfileRevision.user_id == user_id, ProfileRevision.section == "search"
        )
    ).scalars()
    for data in rows:
        boards = data.get("boards") if isinstance(data, dict) else None
        if isinstance(boards, list) and any(_same_board(b, board) for b in boards):
            return True
    return False


# What add_board did: the board was added; it was there already; the person removed it once
# (never added again); or it could not be (no search section, one that does not validate, full).
ADDED, HELD, REMOVED, NOT_ADDED = "added", "held", "removed", "not_added"


def add_board(db: Session, user_id: uuid.UUID, board: Board, company: str, now: datetime) -> str:
    """Add the board to the person's swept boards (owner, 2026-10-20: at once, the person sees
    it in the Search tab and can remove it), in the caller's transaction. Nothing is written
    unless it answers ADDED: HELD when it is there already (same ATS, org case ignored, enabled
    or not), REMOVED when an earlier revision held it (the person removed it: that sticks),
    NOT_ADDED when the search section was never saved or does not validate, or it holds the most
    boards it can."""
    from app.profile import service
    from app.profile.schemas import validate_section

    for _ in range(2):
        profile = service.get_profile(db, user_id, now)
        raw = (profile.sections or {}).get("search")
        if not isinstance(raw, dict):
            return NOT_ADDED
        boards = raw.get("boards") if isinstance(raw.get("boards"), list) else []
        if any(_same_board(b, board) for b in boards):
            return HELD
        if removed_by_person(db, user_id, board):
            return REMOVED
        new = {"company": company[:LINE_MAX], "ats": board.ats, "org": board.org, "enabled": True}
        try:
            data = validate_section("search", raw | {"boards": [*boards, new]})
        except ValueError:
            return NOT_ADDED  # full, or a section the person must save again
        try:
            service.save_section(db, user_id, "search", data, profile.revision, now)
        except service.StaleRevisionError:
            continue  # saved meanwhile: read it again
        return ADDED
    return NOT_ADDED


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
        board, strong = board_on_page(pages.last_text, opened)
        if board is not None and strong:
            read.extra["board"] = {"ats": board.ats, "org": board.org}
            raise SourceError("ats_board")
        if board is not None:  # weak evidence: reported, never added, and the agent reads on
            read.extra["board"] = {"ats": board.ats, "org": board.org, "added": False}
        session = finder.FinderSession(pages, opened, pages.last_text, user_id=read.user_id)
        # The run ends with the sweep's time, before the job's own timeout would cancel it
        # (worker.SWEEP_JOB_TIMEOUT_S): what was validated stays.
        remaining = max(pages.deadline - pages.monotonic(), 0.0)
        cap = asyncio.timeout(remaining)
        try:
            async with cap:
                ran = await self.agent(session)
        except TimeoutError:
            # The sweep's time cap, or a timeout of the agent's own (a model call's): only the
            # first is time_budget.
            ran = finder.Ran(stopped="time_budget" if cap.expired() else "error")
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
