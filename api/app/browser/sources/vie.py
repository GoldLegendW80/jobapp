"""Business France VIE, mon-vie-via (browser B8): the offers of a search and the page of each
offer, read from the snapshot of the page, no model. Modelled on the live site on 2026-10-20
(PR body), in its English version (`/en/`), whose labels this module reads:

- results: `https://mon-vie-via.businessfrance.fr/en/offres/recherche?query=<query>`. The page
  holds a paragraph "<n> offers found"; each offer is a `figure` with the company as a level 3
  heading, then two level 2 headings, the title and "COUNTRY - CITY", and a link "Show Offer /
  Apply" to `/en/offres/<digits>`. The list grows by a click on "Show more offers": each click is
  one result page here.
- an offer: its `main` holds the title as a level 1 heading and the company as a level 2 one, a
  paragraph "Published on DD/MM/YYYY", then "The job", "The ideal profile" and "Further
  information"; "INTERESTED IN THIS POSITION? APPLY NOW!" ends the text.

The source's location, when set, keeps the offers whose location names it (case and accents
ignored): the site's own search box takes words only. Fail closed as google_careers.py.
"""

import re
import unicodedata
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
    posting,
    visible_lines,
)
from app.profile.schemas import BrowserSource

HOST = "mon-vie-via.businessfrance.fr"
RESULTS = "https://mon-vie-via.businessfrance.fr/en/offres/recherche"
FOUND = re.compile(r"([\d][\d ,.]*) offers? found")
OFFER_PATH = re.compile(r"/offres/\d{1,12}/?$")
MORE = "Show more offers"
PUBLISHED = re.compile(r"Published on (\d{2})/(\d{2})/(\d{4})")
END = "INTERESTED IN THIS POSITION"


def results_url(source: BrowserSource, base_url: str | None = None) -> str:
    base = f"{base_url.rstrip('/')}/vie/en/offres/recherche" if base_url else RESULTS
    return f"{base}?{urlencode({'query': source.query})}"


def offer_url(href: str, page_url: str) -> str | None:
    """The offer's address without a query, or None when it is not one."""
    parts = urlsplit(urljoin(page_url, href))
    if parts.scheme not in ("http", "https") or not OFFER_PATH.search(parts.path):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def location_of(text: str) -> str:
    """ "ITALY - MILAN" as "Milan, Italy"; a text without the separator as it is, title cased."""
    country, sep, city = text.partition(" - ")
    if not sep:
        return text.strip().title()
    return f"{city.strip().title()}, {country.strip().title()}"


def fold(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )


def parse_results(root: Node, page_url: str) -> tuple[list[Card], str | None]:
    """The offers listed so far, and the ref of "Show more offers" (None without it)."""
    if not any(FOUND.fullmatch(n.label().strip()) for n in root.walk()):
        raise SourceError("layout_changed")
    cards: list[Card] = []
    seen: set[str] = set()
    for figure in root.walk():
        if figure.role != "figure":
            continue
        link = next(
            (n for n in figure.walk() if n.role == "link" and offer_url(n.url, page_url)), None
        )
        if link is None:
            continue
        headings = [n for n in figure.walk() if n.role == "heading"]
        company = next((h.label() for h in headings if h.level() == 3), "").strip()
        second = [h for h in headings if h.level() == 2]
        if not company or len(second) < 2 or not second[0].label().strip():
            raise SourceError("layout_changed")
        url = offer_url(link.url, page_url) or ""
        if url in seen:
            continue
        seen.add(url)
        place = second[1].label() or " ".join(t.label() for t in second[1].children)
        cards.append(
            Card(
                title=second[0].label().strip(),
                company=company,
                location=location_of(place),
                url=url,
            )
        )
    more = next((n.ref for n in root.walk() if n.label().strip() == MORE and n.ref), None)
    return cards, more


def parse_offer(root: Node, title: str) -> tuple[str, str]:
    """The text of an offer page and its published date (YYYY-MM-DD, "" when not shown)."""
    main = root.find("main")
    heading = None
    if main is not None:
        heading = next((n for n in main.walk() if n.role == "heading" and n.level() == 1), None)
    if main is None or heading is None or not heading.label().strip():
        raise SourceError("layout_changed")
    lines = visible_lines(main, stop=lambda n: n.label().startswith(END))
    published = ""
    for line in lines:
        found = PUBLISHED.search(line)
        if found is not None:
            day, month, year = found.groups()
            if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
                published = f"{year}-{month}-{day}"
            break
    if title not in lines[:2]:
        lines.insert(0, title)
    return "\n".join(lines), published


class Vie:
    def host(self, base_url: str | None) -> str:
        return host_of(base_url) if base_url else HOST

    async def read(
        self, pages: Pages, source: BrowserSource, wanted: Wanted, read: Read, base: str | None
    ) -> None:
        start = results_url(source, base)
        root, page_url = await pages.open(start)
        place = fold(source.location) if source.location else None
        todo: list[Card] = []
        seen: set[str] = set()
        number = 1
        while True:
            cards, more = parse_results(root, page_url or start)
            read.result_pages = number
            fresh = [card for card in cards if card.url not in seen]
            seen.update(card.url for card in cards)
            todo += [c for c in fresh if place is None or place in fold(c.location)]
            if not fresh or more is None or number >= RESULT_PAGES_MAX:
                break
            # The whole list first, the offers after: leaving the page loses the grown list.
            root, page_url = await pages.click(more, MORE)
            number += 1
        for card in todo:
            if not wanted(card):
                continue
            page, _ = await pages.open(card.url)
            text, published = parse_offer(page, card.title)
            read.postings.append((card.company, posting(card, text, published)))


READER = Vie()
