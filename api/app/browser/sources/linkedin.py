"""LinkedIn job search (browser B10, owner decision 2 "Full use"): the results of a search and the
page of each posting, read from the person's own signed in session, no model. It runs only when
app/browser/linkedin_guard.py allows it (the Runner's check, before any lease), at the pace of
SPEC-browser decision 2: RESULT_PAGES result pages at most, PAUSE_S between two pages of the
host on the shared per host pacer, one sweep a day (the guard).

Modelled on the snapshots of 2026-10-20 (PR body; invented postings in every test):
- results: `https://www.linkedin.com/jobs/search/?keywords=<query>&location=<location>&start=
  <25 per page>`. A level 1 heading names the jobs ("4,000+ AI Engineer Jobs in France"); each
  card is a `listitem` holding a link to `/jobs/view/<slug>-<digits>?<tracking>`, the title as a
  level 3 heading, the company as a level 4 heading (its link's name), then the location as the
  first text of the card that is neither, and a `time`.
- a posting: `main` holds the title as a paragraph or heading, then "Apply on company website"
  (a link through `/safety/go/?url=<the company's address>`) or the button "Easy Apply", then
  the level 2 heading "About the job" and the text, up to the next level 2 heading. French labels
  ("À propos de l'offre d'emploi", "Postuler sur le site de l'entreprise") are read the same.

Walls: a page under `/checkpoint/` (LinkedIn's security check), or a page that is not what the
reader asked for and whose heading speaks of a security check or unusual activity, is
`linkedin_checkpoint`: the guard pauses every LinkedIn path for 7 days. A page under `/login`,
`/authwall`, `/uas/` or `/signup`, or a sign in heading on a page that is not what was asked, is
`linkedin_signin`: the source is skipped and the person told ("LinkedIn needs you to sign in");
this module never starts a sign in hand off (that is B12b's, when the person opens it). Both set
`result_pages` to the page they met, so the guard counts the sweep of the day.

Fail closed: a results page without its heading, or a card with a posting link but no title or
company, is `layout_changed`; a posting page without "About the job" is too. A posting page
whose title is not the card's, or reached at another address, is skipped. A posting address is
kept as `/jobs/view/<digits>/` on www.linkedin.com (the slug and the tracking query dropped), so
the same posting found twice is one row. The company's own Apply address, when the posting has
one, is the job's address (apply then works on the company's form, and the posting found by
API on that board is the same row by address); the LinkedIn address ends the posting text.
"""

import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from app.browser.sources import (
    Card,
    Node,
    Pages,
    Read,
    SourceError,
    Wanted,
    host_of,
    posting,
    same_page,
    same_site,
    same_title,
    visible_lines,
)
from app.profile.schemas import BrowserSource

HOST = "www.linkedin.com"
SEARCH = "https://www.linkedin.com/jobs/search/"
RESULT_PAGES = 5  # SPEC-browser decision 2
PER_PAGE = 25
PAUSE_S = (10.0, 30.0)  # SPEC-browser decision 2: 10 to 30 seconds per page
URL_MAX = 2048

POSTING_PATH = re.compile(r"^(?P<prefix>(?:/[^/]+)*?)/jobs/view/(?:[^/]*-)?(?P<id>\d{4,20})/?$")
RESULTS_HEADING = re.compile(r"\b(?:jobs?|emplois?|offres? d.emploi)\b")
DESCRIPTION = frozenset({"about the job", "à propos de l'offre d'emploi"})
EXTERNAL_APPLY = frozenset(
    {"apply on company website", "postuler sur le site de l'entreprise", "apply", "postuler"}
)
REDIRECTS = frozenset({("safety", "go"), ("jobs", "view", "externalApply")})
CHECKPOINT_WORDS = (
    "security check",
    "security verification",
    "unusual activity",
    "vérification de sécurité",
    "activité inhabituelle",
)
SIGNIN_HEADINGS = frozenset(
    {
        "sign in",
        "sign in to linkedin",
        "join linkedin",
        "welcome back",
        "s'identifier",
        "connexion",
        "rejoindre linkedin",
        "bon retour parmi nous",
    }
)
SIGNIN_SEGMENTS = frozenset({"login", "authwall", "uas", "signup"})


APOSTROPHES = {0x2019: "'", 0x02BC: "'"}  # typographic apostrophes


def norm(text: str) -> str:
    """NFKC, casefolded, typographic apostrophes as plain ones, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text).translate(APOSTROPHES)
    return " ".join(text.casefold().split())


def search_base(base_url: str | None) -> str:
    return f"{base_url.rstrip('/')}/linkedin/jobs/search/" if base_url else SEARCH


def results_url(source: BrowserSource, page: int, base_url: str | None = None) -> str:
    query = {"keywords": source.query}
    if source.location:
        query["location"] = source.location
    if page > 1:
        query["start"] = str(PER_PAGE * (page - 1))
    return f"{search_base(base_url)}?{urlencode(query)}"


def _linkedin(host: str | None) -> bool:
    return bool(host) and (host == "linkedin.com" or str(host).endswith(".linkedin.com"))


def posting_url(href: str, page_url: str) -> str | None:
    """The posting's address as `/jobs/view/<digits>/`, or None when the link is not one."""
    if len(href) > URL_MAX:
        return None
    parts = urlsplit(urljoin(page_url, href))
    match = POSTING_PATH.match(parts.path)
    if parts.scheme not in ("http", "https") or match is None:
        return None
    page = urlsplit(page_url)
    if _linkedin(page.hostname) and _linkedin(parts.hostname) and parts.scheme == "https":
        if parts.username is not None or parts.password is not None or parts.port is not None:
            return None
        return f"https://{HOST}/jobs/view/{match['id']}/"
    if not same_site(parts, page_url):
        return None
    path = f"{match['prefix']}/jobs/view/{match['id']}/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, "", ""))


def external_url(href: str, page_url: str) -> str | None:
    """The company's Apply address behind LinkedIn's redirect, None when it is not one: http or
    https, a host that is not LinkedIn's, no user or password, at most URL_MAX characters."""
    if len(href) > URL_MAX:
        return None
    parts = urlsplit(urljoin(page_url, href))
    if not (_linkedin(parts.hostname) and parts.scheme == "https") and not same_site(
        parts, page_url
    ):
        return None
    segments = tuple(s for s in parts.path.split("/") if s)
    if not any(segments[i : i + len(r)] == r for r in REDIRECTS for i in range(len(segments))):
        return None
    target = parse_qs(parts.query).get("url", [""])[0]
    out = urlsplit(target)
    try:
        out.port  # noqa: B018  a malformed port is refused
    except ValueError:
        return None
    if (
        out.scheme not in ("http", "https")
        or not out.hostname
        or _linkedin(out.hostname)
        or out.username is not None
        or out.password is not None
        or len(target) > URL_MAX
    ):
        return None
    return urlunsplit((out.scheme, out.netloc.lower(), out.path or "/", out.query, ""))


def _segments(page_url: str, base_url: str | None) -> list[str]:
    path = urlsplit(page_url).path
    if base_url:
        prefix = urlsplit(base_url).path.rstrip("/") + "/linkedin"
        path = path[len(prefix) :] if path.startswith(prefix) else path
    return [s.casefold() for s in path.split("/") if s]


def wall_by_url(page_url: str, base_url: str | None) -> str | None:
    """The code of a wall page told by its address, None for any other page."""
    segments = _segments(page_url, base_url)
    if segments[:1] == ["checkpoint"]:
        return "linkedin_checkpoint"
    if segments[:1] and segments[0] in SIGNIN_SEGMENTS:
        return "linkedin_signin"
    return None


def wall_by_heading(root: Node) -> str | None:
    """The code of a wall page told by its headings (read only on a page that is not the one
    asked for), None when no heading is a wall's."""
    headings = [norm(n.label()) for n in root.walk() if n.role == "heading" and not n.hidden]
    if any(word in h for h in headings for word in CHECKPOINT_WORDS):
        return "linkedin_checkpoint"
    if any(h in SIGNIN_HEADINGS for h in headings):
        return "linkedin_signin"
    return None


def _texts(item: Node) -> list[str]:
    return [n.text.strip() for n in item.walk() if n.role in ("generic", "text") and n.text]


def parse_results(root: Node, page_url: str) -> list[Card]:
    """The cards of a results page. Raises layout_changed when it is not one."""
    heading = next(
        (n for n in root.walk() if n.role == "heading" and n.level() == 1 and not n.hidden), None
    )
    if heading is None or RESULTS_HEADING.search(norm(heading.label())) is None:
        raise SourceError("layout_changed")
    cards: list[Card] = []
    seen: set[str] = set()
    for item in root.walk():
        if item.role != "listitem":
            continue
        link = next(
            (n for n in item.walk() if n.role == "link" and posting_url(n.url, page_url)), None
        )
        if link is None:
            continue
        title_node = next((n for n in item.walk() if n.role == "heading" and n.level() == 3), None)
        company_node = next(
            (n for n in item.walk() if n.role == "heading" and n.level() == 4), None
        )
        title = (title_node.label() if title_node else "").strip()
        company = ""
        if company_node is not None:
            company = company_node.label().strip() or next(
                (n.label().strip() for n in company_node.walk() if n.label().strip()), ""
            )
        if not title or not company:
            raise SourceError("layout_changed")
        url = posting_url(link.url, page_url) or ""
        if url in seen:
            continue
        seen.add(url)
        location = next((t for t in _texts(item) if t not in (title, company)), "")
        cards.append(Card(title=title, company=company, location=location, url=url))
    return cards


def parse_posting(root: Node, title: str, page_url: str) -> tuple[str, str | None] | None:
    """The text of a posting page and the company's Apply address (None for Easy Apply); None
    when the page's title is not `title`. Raises layout_changed without "About the job"."""
    main = root.find("main")
    nodes = main.walk() if main is not None else []
    start = next(
        (
            i
            for i, n in enumerate(nodes)
            if n.role == "heading" and n.level() == 2 and norm(n.label()) in DESCRIPTION
        ),
        None,
    )
    parent = None
    if start is not None:
        parent = next((n for n in nodes if any(c is nodes[start] for c in n.children)), None)
    if main is None or start is None or parent is None:
        raise SourceError("layout_changed")
    top = nodes[:start]
    if not any(n.role in ("paragraph", "heading") and same_title(n.label(), title) for n in top):
        return None
    external = next(
        (
            found
            for n in top
            if n.role == "link"
            and norm(n.label()) in EXTERNAL_APPLY
            and (found := external_url(n.url, page_url)) is not None
        ),
        None,
    )
    # The description: the heading's next siblings, up to the next level 2 heading.
    after = parent.children[[id(c) for c in parent.children].index(id(nodes[start])) + 1 :]
    body: list[Node] = []
    for node in after:
        if node.role == "heading" and node.level() == 2:
            break
        body.append(node)
    lines = visible_lines(Node(role="root", children=body))
    return "\n".join([title, *lines]), external


class LinkedIn:
    pause = PAUSE_S

    def host(self, base_url: str | None) -> str:
        return host_of(base_url) if base_url else HOST

    async def _open(self, pages: Pages, url: str, read: Read, number: int, base: str | None):
        root, opened = await pages.open(url)
        code = wall_by_url(opened or url, base)
        if code is not None:
            read.result_pages = max(read.result_pages, number)
            raise SourceError(code)
        return root, opened

    async def read(
        self, pages: Pages, source: BrowserSource, wanted: Wanted, read: Read, base: str | None
    ) -> None:
        seen: set[str] = set()
        for number in range(1, RESULT_PAGES + 1):
            asked = results_url(source, number, base)
            root, opened = await self._open(pages, asked, read, number, base)
            try:
                cards = parse_results(root, opened or asked)
            except SourceError:
                code = wall_by_heading(root)
                if code is not None:
                    read.result_pages = max(read.result_pages, number)
                    raise SourceError(code) from None
                raise
            read.result_pages = number
            fresh = [card for card in cards if card.url not in seen]
            seen.update(card.url for card in cards)
            for card in fresh:
                if not wanted(card):
                    continue
                page, at = await self._open(pages, card.url, read, number, base)
                if not same_page(at, card.url):
                    code = wall_by_heading(page)
                    if code is not None:
                        raise SourceError(code)
                    continue  # redirected to another page: not that posting
                try:
                    found = parse_posting(page, card.title, at)
                except SourceError:
                    code = wall_by_heading(page)
                    if code is not None:
                        raise SourceError(code) from None
                    raise
                if found is None:
                    continue
                text, external = found
                if external is not None:
                    text = f"{text}\nFound on LinkedIn: {card.url}"
                    kept = Card(card.title, card.company, card.location, external)
                else:
                    kept = card
                read.postings.append((card.company, posting(kept, text, "")))
            if not fresh or len(cards) < PER_PAGE:
                return


READER = LinkedIn()
