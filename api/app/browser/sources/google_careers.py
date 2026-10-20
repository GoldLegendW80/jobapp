"""Google Careers (browser B8): the results of a search and the page of each posting, read from
the snapshot of the page, no model. Modelled on the live site on 2026-10-20 (PR body):

- results: `https://www.google.com/about/careers/applications/jobs/results/?q=<query>&location=
  <location>&hl=en_US&page=<n>`, 20 cards a page. The page holds the heading "Jobs search
  results"; each card is a `listitem` with the title as a level 3 heading, the company after the
  hidden icon `corporate_fare`, the locations after the hidden icon `place` (the second and later
  ones start with "; ", and "; +N more" ends a long list), and a link "Learn more about <title>"
  to `jobs/results/<digits>-<slug>?...`. Below the list a status "Showing 1 to 20 of 57 rows".
- a posting: its `main` holds the title as a level 2 heading, then the qualifications, "About the
  job" and "Responsibilities"; the legal paragraphs start at "Information collected".

Fail closed: a results page without the heading, a card with a posting link but no title or
company, a list of cards empty while the status counts some, or a posting page without its
heading is `layout_changed`. A posting page whose heading is not the card's title (an expired
posting sent back to the results, or to another posting) is skipped: not stored, the source goes
on. A posting address must be on the results page's own site (scheme, host and port) and is kept
without its query (the search's) and with its host in lower case, so the same posting found
twice is one row. A posting page is read only when the address the browser ended on (after
redirects) is the card's: otherwise the posting is skipped.
"""

import re
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from app.browser.sources import (
    RESULT_PAGES_MAX,
    Card,
    Node,
    Pages,
    Read,
    SourceError,
    Wanted,
    host_of,
    own_texts,
    posting,
    same_page,
    same_site,
    same_title,
    visible_lines,
)
from app.profile.schemas import BrowserSource

HOST = "www.google.com"
RESULTS = "https://www.google.com/about/careers/applications/jobs/results/"
RESULTS_HEADING = "Jobs search results"
POSTING_PATH = re.compile(r"/jobs/results/\d{1,30}-[a-z0-9]+(?:-[a-z0-9]+)*/?$")
STATUS = re.compile(r"Showing (\d[\d,]*) to (\d[\d,]*) of (\d[\d,]*)")
MORE = re.compile(r"\+\s*\d+\s+more")
LEGAL = "Information collected"
# The icon ligatures of the posting page, left out of its text when printed without
# `[aria-hidden]` (see `_is_icon`).
ICONS = frozenset({"corporate_fare", "place", "share", "bar_chart"})


def results_base(base_url: str | None) -> str:
    return f"{base_url.rstrip('/')}/google/jobs/results/" if base_url else RESULTS


def results_url(source: BrowserSource, page: int, base_url: str | None = None) -> str:
    query: dict[str, str] = {"q": source.query}
    if source.location:
        query["location"] = source.location
    query["hl"] = "en_US"
    if page > 1:
        query["page"] = str(page)
    return f"{results_base(base_url)}?{urlencode(query)}"


def posting_url(href: str, page_url: str) -> str | None:
    """The posting's address without the search's query, or None when it is not one. The page's
    links are relative to the site's root (`<base href=".../applications/">`), the folder above
    `jobs/results/` of the page's address."""
    path = urlsplit(page_url).path
    root = (
        page_url[: page_url.find(path) + path.rfind("/jobs/results/") + 1]
        if "/jobs/results/" in path
        else page_url
    )
    parts = urlsplit(urljoin(root, href))
    if parts.scheme not in ("http", "https") or not POSTING_PATH.search(parts.path):
        return None
    if not same_site(parts, page_url):
        return None
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, "", ""))


def _is_icon(node: Node, icon: str) -> bool:
    """The icon `icon`: the ligature's text alone in its element. Playwright 1.55, browserd's
    pinned version, prints an `aria-hidden` element without the `[aria-hidden]` attribute that
    newer versions add (seen on the fixture pages, tests/browser_pages/b8/), so both are read."""
    return node.label() == icon and not node.children and node.role in ("generic", "text")


def _after_icon(card: Node, icon: str) -> list[str]:
    """The visible texts that follow the icon `icon` in its parent."""
    for parent in card.walk():
        kids = parent.children
        for index, kid in enumerate(kids):
            if _is_icon(kid, icon):
                return [
                    t
                    for k in kids[index + 1 :]
                    if not k.hidden and not _is_icon(k, icon)
                    for t in own_texts(k)
                ]
    return []


def location_of(parts: list[str]) -> str:
    names = [part.strip().lstrip(";").strip() for part in parts]
    return "; ".join(name for name in names if name and not MORE.fullmatch(name))


def parse_results(root: Node, page_url: str) -> tuple[list[Card], bool]:
    """The cards of a results page, and whether a next page exists."""
    if root.find("heading", RESULTS_HEADING) is None:
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
        title = next((n.label() for n in item.walk() if n.role == "heading" and n.level() == 3), "")
        company = " ".join(_after_icon(item, "corporate_fare")[:1])
        if not title.strip() or not company:
            raise SourceError("layout_changed")
        url = posting_url(link.url, page_url) or ""
        if url in seen:
            continue
        seen.add(url)
        location = location_of(_after_icon(item, "place"))
        cards.append(Card(title=title.strip(), company=company, location=location, url=url))
    status = next((m for n in root.walk() if (m := STATUS.search(n.label())) is not None), None)
    if status is None:
        if not cards:
            raise SourceError("layout_changed")
        return cards, True  # a snapshot cut before the status: try the next page
    last, total = (int(status[i].replace(",", "")) for i in (2, 3))
    if total and not cards:
        raise SourceError("layout_changed")
    return cards, last < total


def parse_posting(root: Node, title: str) -> str | None:
    """The text of a posting page, from its title to the legal paragraphs; None when the page's
    heading is not `title` (not that posting's page)."""
    main = root.find("main")
    heading = None
    if main is not None:
        heading = next((n for n in main.walk() if n.role == "heading" and n.level() == 2), None)
    if main is None or heading is None or not heading.label().strip():
        raise SourceError("layout_changed")
    if not same_title(heading.label(), title):
        return None
    lines = [
        line
        for line in visible_lines(main, stop=lambda n: n.label().startswith(LEGAL))
        if line not in ICONS
    ]
    if title not in lines[:1]:
        lines.insert(0, title)
    return "\n".join(lines)


class GoogleCareers:
    def host(self, base_url: str | None) -> str:
        return host_of(base_url) if base_url else HOST

    async def read(
        self, pages: Pages, source: BrowserSource, wanted: Wanted, read: Read, base: str | None
    ) -> None:
        seen: set[str] = set()
        for number in range(1, RESULT_PAGES_MAX + 1):
            root, page_url = await pages.open(results_url(source, number, base))
            cards, more = parse_results(root, page_url or results_url(source, number, base))
            read.result_pages = number
            fresh = [card for card in cards if card.url not in seen]
            seen.update(card.url for card in cards)
            for card in fresh:
                if not wanted(card):
                    continue
                page, opened = await pages.open(card.url)
                if not same_page(opened, card.url):
                    continue  # redirected to another page: not that posting
                text = parse_posting(page, card.title)
                if text is not None:
                    read.postings.append((card.company, posting(card, text, "")))
            if not more or not fresh:
                return


READER = GoogleCareers()
