"""Browser B8: the extractors of Google Careers and Business France VIE on snapshots written after
the live pages (tests/browser/b8_pages.py), and the run of the sources with fake tools and a fake
lease store. Pure but for the pacing's Redis tests at the end: no database, no browser, no sleep
(the pacing's sleep is recorded and moves an injected clock)."""

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from app.browser.client import BrowserError
from app.browser.leases import Acquired, Lease, StoreUnavailableError, Waiting
from app.browser.sources import (
    PAGE_LOAD_S,
    PAGE_PAUSE_S,
    POSTING_PAGES_MAX,
    RESULT_PAGES_MAX,
    Card,
    MemoryPacer,
    Pacer,
    RedisPacer,
    Runner,
    SourceError,
    parse_snapshot,
    source_name,
    vie,
    visible_lines,
)
from app.browser.sources import google_careers as gc
from app.jobs import slug as slugs
from app.profile.schemas import BrowserSource, SearchSection
from tests.browser import b8_pages as pages
from tests.browser.b3_support import flush, pool_of, redis_url  # noqa: F401  (fixture)

PAGE = "https://www.google.com/about/careers/applications/jobs/results/?q=ai"
VIE_PAGE = "https://mon-vie-via.businessfrance.fr/en/offres/recherche?query=ai"


def g(**kw: Any) -> BrowserSource:
    return BrowserSource.model_validate({"kind": "google_careers", "query": "ai", **kw})


# --- the snapshot parser ----------------------------------------------------------------------


def test_snapshot_lines_quoted_and_plain() -> None:
    root = parse_snapshot(
        "\n".join(
            [
                '- heading "A \\"quoted\\" title" [level=3] [ref=e1]',
                "- 'heading \"Missions: it''s here\" [level=2] [ref=e2]'",
                '- text: "Pay: \\"fair\\""',
                "- generic [aria-hidden] [ref=e3]: place",
                '- link "x" [ref=e4]:',
                "  - /url: jobs/results/1-a",
                "not a snapshot line",
                "- ???",
            ]
        )
    )
    kids = root.children
    assert kids[0].name == 'A "quoted" title'
    assert kids[0].level() == 3
    assert kids[0].ref == "e1"
    assert kids[1].name == "Missions: it's here"
    assert kids[1].level() == 2
    assert kids[2].text == 'Pay: "fair"'
    assert kids[3].hidden
    assert kids[4].url == "jobs/results/1-a"
    assert len(kids) == 5


def test_visible_lines_leave_out_icons_buttons_and_stop() -> None:
    root = parse_snapshot(pages.google_posting(pages.offers(1)[0]))
    main = root.find("main")
    assert main is not None
    lines = visible_lines(main, stop=lambda n: n.label().startswith(gc.LEGAL))
    assert "share" not in lines
    assert "corporate_fare" not in lines
    assert not any(line.startswith("Share") for line in lines)
    assert "Equal opportunity employer." not in lines
    assert "Build agents with Python and LLM evaluation." in lines


def test_a_huge_snapshot_parses_in_one_pass() -> None:
    text = pages.google_results(pages.offers(25)) * 1  # 25 cards
    deep = "\n".join("  " * i + f"- generic [ref=d{i}]:" for i in range(3000))
    root = parse_snapshot(text + "\n" + deep)
    assert len(root.walk()) > 3000  # iterative walk: no recursion limit


# --- Google Careers ------------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 25])
def test_google_results_of_0_1_and_25(count: int) -> None:
    made = pages.offers(count)
    cards, more = gc.parse_results(parse_snapshot(pages.google_results(made)), PAGE)
    assert [c.title for c in cards] == [o.title for o in made]
    assert all(c.company == "Example Robotics" and c.location == "Paris, France" for c in cards)
    assert all(
        c.url.startswith("https://www.google.com/about/careers/applications/jobs/results/")
        for c in cards
    )
    assert all("?" not in c.url for c in cards)
    assert more is False


def test_google_icons_printed_without_the_aria_hidden_attribute() -> None:
    """Playwright 1.55 (browserd's pin) prints an aria-hidden icon as a plain `generic`: the
    company and the locations are read all the same, and the posting text leaves the icons out
    (tests/browser_pages/b8/ in a real Chromium showed this form)."""
    made = pages.offers(2, location="Paris, France; Munich, Germany")
    text = pages.google_results(made).replace(" [aria-hidden]", "")
    cards, _ = gc.parse_results(parse_snapshot(text), PAGE)
    assert [(c.company, c.location) for c in cards] == [
        ("Example Robotics", "Paris, France; Munich, Germany")
    ] * 2
    posting = pages.google_posting(made[0]).replace(" [aria-hidden]", "")
    lines = gc.parse_posting(parse_snapshot(posting), made[0].title).splitlines()
    assert "Example Robotics" in lines
    assert not set(lines) & gc.ICONS


def test_google_next_page_when_the_status_counts_more() -> None:
    root = parse_snapshot(pages.google_results(pages.offers(20), total=57))
    assert gc.parse_results(root, PAGE)[1] is True


def test_google_locations_joined_and_more_dropped() -> None:
    made = pages.offers(1, location="Zürich, Switzerland; Munich, Germany; +1 more")
    cards, _ = gc.parse_results(parse_snapshot(pages.google_results(made)), PAGE)
    assert cards[0].location == "Zürich, Switzerland; Munich, Germany"


@pytest.mark.parametrize(
    "text",
    [
        pages.google_results(pages.offers(3)).replace("Jobs search results", "Search jobs"),
        pages.google_results(pages.offers(2)).replace("[level=3]", "[level=4]"),
        pages.google_results(pages.offers(2)).replace("corporate_fare", "domain"),
        pages.google_results([], total=12),
        "\n".join(pages.google_results(pages.offers(2)).splitlines()[:5]),
        "",
    ],
    ids=[
        "no_heading",
        "no_title",
        "no_company",
        "status_counts_cards_missing",
        "no_cards_no_status",
        "empty",
    ],
)
def test_google_layout_changed(text: str) -> None:
    with pytest.raises(SourceError) as caught:
        gc.parse_results(parse_snapshot(text), PAGE)
    assert caught.value.code == "layout_changed"


def test_google_snapshot_cut_before_the_status_reads_the_cards_and_tries_the_next_page() -> None:
    text = pages.google_results(pages.offers(3))
    text = text[: text.index("- status")]
    cards, more = gc.parse_results(parse_snapshot(text), PAGE)
    assert len(cards) == 3
    assert more is True


@pytest.mark.parametrize(
    ("href", "kept"),
    [
        ("jobs/results/90289280902931142-staff-software-engineer?q=ml&hl=en_US", True),
        ("./jobs/results/1-a", True),
        ("/about/careers/applications/jobs/results/123-ai-engineer/", True),
        ("https://www.google.com/about/careers/applications/jobs/results/7-x", True),
        ("jobs/results/", False),
        ("jobs/results/?q=ai&page=2", False),
        ("jobs/results/abc-ai-engineer", False),
        ("jobs/results/123", False),
        ("jobs/results/123-AI-Engineer", False),
        ("jobs/results/123-ai-engineer/apply", False),
        ("./apply?jobId=123", False),
        ("javascript:alert(1)", False),
        ("mailto:jobs@example.org", False),
        ("https://www.instagram.com/lifeatgoogle/", False),
        ("", False),
        # B6: only on the results page's own site (scheme, host, port, no user).
        ("https://WWW.GOOGLE.COM/about/careers/applications/jobs/results/7-x", True),
        ("https://evil.example/jobs/results/1001-ai-engineer-1", False),
        ("https://careers.google.com/jobs/results/1001-ai-engineer-1", False),
        ("https://www.google.com.evil.example/jobs/results/1-a", False),
        ("//evil.example/about/careers/applications/jobs/results/1-a", False),
        ("http://www.google.com/about/careers/applications/jobs/results/7-x", False),
        ("https://www.google.com:8443/about/careers/applications/jobs/results/7-x", False),
        ("https://www.google.com:99999/about/careers/applications/jobs/results/7-x", False),
        ("https://someone@www.google.com/about/careers/applications/jobs/results/7-x", False),
        ("https://google.com/about/careers/applications/jobs/results/7-x", False),
    ],
)
def test_google_posting_url(href: str, kept: bool) -> None:
    assert (gc.posting_url(href, PAGE) is not None) is kept


def test_google_results_url() -> None:
    assert gc.results_url(g(location="France"), 1) == (
        "https://www.google.com/about/careers/applications/jobs/results/"
        "?q=ai&location=France&hl=en_US"
    )
    assert gc.results_url(g(), 3, "http://pages:8080/b8").endswith(
        "/b8/google/jobs/results/?q=ai&hl=en_US&page=3"
    )


@pytest.mark.parametrize(
    ("status", "more"),
    [
        ("Showing , to , of , rows", True),  # no figures: read as no status, never a crash
        ("Showing 1 to 20 of 1,234 rows", True),
        ("Showing 1,221 to 1,234 of 1,234 rows", False),
        ("Showing 1 to 3 of 3 rows", False),
    ],
)
def test_google_status_figures(status: str, more: bool) -> None:
    text = pages.google_results(pages.offers(3)).replace("Showing 1 to 3 of 3 rows", status)
    cards, found = gc.parse_results(parse_snapshot(text), PAGE)
    assert len(cards) == 3
    assert found is more


@pytest.mark.parametrize(
    ("heading", "title", "same"),
    [
        ("AI Engineer 1", "AI Engineer 1", True),
        ("ai engineer 1", "AI Engineer 1", True),
        ("  AI   Engineer\u00a01 ", "AI Engineer 1", True),
        ("ＡＩ Engineer 1", "AI Engineer 1", True),
        ("Ingénieur IA", "INGÉNIEUR IA", True),
        ("AI Engineer 10", "AI Engineer 1", False),
        ("AI Engineer", "AI Engineer 1", False),
        ("Ingenieur IA", "Ingénieur IA", False),
        ("Jobs search results", "AI Engineer 1", False),
        ("Search results", "AI Engineer 1", False),
        ("", "", False),
        ("AI Engineer 1", "", False),
    ],
)
def test_same_title(heading: str, title: str, same: bool) -> None:
    from app.browser.sources import same_title

    assert same_title(heading, title) is same


def test_google_posting_page_of_another_posting_is_skipped() -> None:
    one, two = pages.offers(2)
    assert gc.parse_posting(parse_snapshot(pages.google_posting(two)), one.title) is None
    # The results page an expired posting sends back to.
    results = pages.google_results(pages.offers(3))
    assert gc.parse_posting(parse_snapshot(results), one.title) is None


def test_google_posting_text() -> None:
    offer = pages.offers(1)[0]
    text = gc.parse_posting(parse_snapshot(pages.google_posting(offer)), offer.title)
    assert text.splitlines()[0] == offer.title
    assert "About the job" in text
    assert "Information collected" not in text
    assert "Other posting" not in text  # the list beside the posting is outside main


@pytest.mark.parametrize(
    "text",
    [
        pages.google_posting(pages.offers(1)[0]).replace("- main", "- region"),
        pages.google_posting(pages.offers(1)[0]).replace("[level=2] [ref=f4e266]", "[level=5]"),
        "",
    ],
)
def test_google_posting_layout_changed(text: str) -> None:
    with pytest.raises(SourceError):
        gc.parse_posting(parse_snapshot(text), "AI Engineer 1")


# --- Business France VIE ------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 25])
def test_vie_results_of_0_1_and_25(count: int) -> None:
    made = pages.offers(count, location="FRANCE - PARIS")
    cards, more = vie.parse_results(parse_snapshot(pages.vie_results(made)), VIE_PAGE)
    assert [c.title for c in cards] == [o.title for o in made]
    assert all(c.location == "Paris, France" for c in cards)
    assert all(
        c.url == f"https://mon-vie-via.businessfrance.fr/en/offres/{o.number}"
        for c, o in zip(cards, made, strict=True)
    )
    assert more is None


def test_vie_show_more_is_a_ref() -> None:
    made = pages.offers(6, location="FRANCE - PARIS")
    root = parse_snapshot(pages.vie_results(made, total=192, more=True))
    assert vie.parse_results(root, VIE_PAGE)[1] == "f7e9184"


VIE_TWO = pages.offers(2, location="FRANCE - PARIS")


def swapped(o: pages.Offer) -> pages.Offer:
    """The card with its title and place headings swapped."""
    return pages.Offer(title=o.location, company=o.company, location=o.title, number=o.number)


@pytest.mark.parametrize(
    "text",
    [
        pages.vie_results(VIE_TWO).replace("offers found", "offres trouvées"),
        pages.vie_results(VIE_TWO).replace("[level=3]", "[level=4]"),
        pages.vie_results(VIE_TWO).replace("[level=2] [ref=f7e1", "[level=6] [ref=f7e1"),
        # B5: the place heading has the COUNTRY - CITY shape, the title heading does not.
        pages.vie_results([swapped(o) for o in VIE_TWO]),
        pages.vie_results(pages.offers(2, location="Paris, France")),
        pages.vie_results(pages.offers(2, location="France - Paris")),
        pages.vie_results(pages.offers(2, location="FRANCE PARIS")),
        pages.vie_results(pages.offers(2, location="FRANCE -")),
        pages.vie_results(pages.offers(2, location=" - PARIS")),
        pages.vie_results(pages.offers(2, location="12 - 34")),
        pages.vie_results(pages.offers(2, title="DATA - PARIS", location="FRANCE - PARIS")),
        "",
    ],
    ids=[
        "french", "no_company", "no_title_level", "swapped", "comma_place", "mixed_case_place",
        "no_separator", "no_city", "no_country", "digits_only", "title_shaped", "empty",
    ],
)  # fmt: skip
def test_vie_layout_changed(text: str) -> None:
    with pytest.raises(SourceError) as caught:
        vie.parse_results(parse_snapshot(text), VIE_PAGE)
    assert caught.value.code == "layout_changed"


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("ITALY - MILAN", "Milan, Italy"),
        ("BELGIUM - LOUVAIN/LEUVEN", "Louvain/Leuven, Belgium"),
        ("UNITED STATES - NEW YORK", "New York, United States"),
        ("CZECH REPUBLIC - NOVY JICIN", "Novy Jicin, Czech Republic"),
        ("SINGAPORE", "Singapore"),
        ("  FRANCE -  PARIS ", "Paris, France"),
    ],
)
def test_vie_location(raw: str, shown: str) -> None:
    assert vie.location_of(raw) == shown


@pytest.mark.parametrize(
    ("text", "shaped"),
    [
        ("FRANCE - PARIS", True),
        ("UNITED STATES - NEW YORK", True),
        ("BELGIUM - LOUVAIN/LEUVEN", True),
        ("CÔTE D'IVOIRE - ABIDJAN", True),
        ("ROYAUME-UNI - LONDRES", True),
        ("  ITALY -  MILAN ", True),
        ("CHINE - SHANGHAI 2", True),
        ("SINGAPORE", False),
        ("France - Paris", False),
        ("FRANCE - Paris", False),
        ("FRANCE-PARIS", False),
        ("FRANCE – PARIS", False),
        ("FRANCE -", False),
        ("- PARIS", False),
        ("1 - 2", False),
        ("AI Engineer 1", False),
        ("", False),
    ],
)
def test_vie_place_shape(text: str, shaped: bool) -> None:
    assert vie.place_shaped(text) is shaped


@pytest.mark.parametrize(
    ("query", "location", "kept"),
    [
        ("paris", "Paris, France", True),
        ("Paris", "Paris, France", True),
        ("PARIS", "Paris, France", True),
        ("  paris ", "Paris, France", True),
        ("france", "Paris, France", True),
        ("paris france", "Paris, France", True),
        ("paris, france", "Paris, France", True),
        ("new york", "New York, United States", True),
        ("york", "New York, United States", True),  # a word of the city: kept (documented)
        ("united states", "New York, United States", True),
        ("Sao Paulo", "São Paulo, Brazil", True),
        ("são paulo", "Sao Paulo, Brazil", True),
        ("leuven", "Louvain/Leuven, Belgium", True),
        ("saint-denis", "Saint Denis, Reunion", True),
        ("zürich", "Zurich, Switzerland", True),
        ("us", "Moscow, Russia", False),
        ("us", "New York, United States", False),
        ("us", "Sydney, Australia", False),
        ("us", "Nicosia, Cyprus", False),
        ("us", "Minsk, Belarus", False),
        ("nice", "Venice, Italy", False),
        ("nice", "Nice, France", True),
        ("paris", "Parisot, France", False),
        ("par", "Paris, France", False),
        ("york new", "New York, United States", False),
        ("new france", "New York, United States", False),
        ("states united", "New York, United States", False),
        ("milan", "Paris, France", False),
        ("", "Paris, France", False),
        ("-", "Paris, France", False),
        ("___", "Paris, France", False),
        ("paris", "", False),
        ("paris", "-", False),
        ("Pa ris", "Paris, France", False),
        ("paris " * 50, "Paris, France", False),
        ("paris", "Paris, France " * 1000, True),
    ],
)
def test_vie_location_filter_on_words(query: str, location: str, kept: bool) -> None:
    assert vie.in_place(query, location) is kept


@pytest.mark.parametrize(
    ("href", "kept"),
    [
        ("/en/offres/247265", True),
        ("/offres/247265", True),
        ("/fr/offres/1/", True),
        ("/en/offres", False),
        ("/en/offres/recherche?query=data", False),
        ("/en/offres/24a", False),
        ("#", False),
        ("/en/offres/247265/postuler", False),
        ("https://mon-vie-via.businessfrance.fr/en/offres/9", True),
        ("https://evil.example/en/offres/247265", False),
        ("https://www.businessfrance.fr/en/offres/247265", False),
        ("//evil.example/en/offres/247265", False),
        ("http://mon-vie-via.businessfrance.fr/en/offres/9", False),
        ("https://x@mon-vie-via.businessfrance.fr/en/offres/9", False),
    ],
)
def test_vie_offer_url(href: str, kept: bool) -> None:
    assert (vie.offer_url(href, VIE_PAGE) is not None) is kept


@pytest.mark.parametrize(
    ("published", "iso"), [("20/10/2026", "2026-10-20"), ("31/13/2026", ""), ("1/1/2026", "")]
)
def test_vie_offer_text_and_date(published: str, iso: str) -> None:
    offer = pages.offers(1)[0]
    parsed = vie.parse_offer(parse_snapshot(pages.vie_offer(offer, published)), offer.title)
    assert parsed is not None
    text, date = parsed
    assert date == iso
    assert "Missions : build agents with Python." in text
    assert "OFFRES A LA UNE" not in text
    assert "Another offer" not in text


def test_vie_offer_page_of_another_offer_or_the_results_is_skipped() -> None:
    one, two = pages.offers(2, location="FRANCE - PARIS")
    assert vie.parse_offer(parse_snapshot(pages.vie_offer(two)), one.title) is None
    assert vie.parse_offer(parse_snapshot(pages.vie_results([one, two])), one.title) is None


@pytest.mark.parametrize(
    "text",
    [
        pages.vie_offer(pages.offers(1)[0]).replace("[level=1]", "[level=4]"),
        pages.vie_offer(pages.offers(1)[0]).replace("- main", "- region"),
        "",
    ],
    ids=["no_heading_in_main", "no_main", "empty"],
)
def test_vie_offer_layout_changed(text: str) -> None:
    with pytest.raises(SourceError) as caught:
        vie.parse_offer(parse_snapshot(text), "AI Engineer 1")
    assert caught.value.code == "layout_changed"


# --- the schema ----------------------------------------------------------------------------------


def test_the_search_section_takes_browser_sources() -> None:
    section = SearchSection.model_validate(
        {"browser_sources": [{"kind": "vie", "query": "data", "enabled": True}]}
    )
    assert section.browser_sources[0].kind == "vie"
    assert section.browser_sources[0].location is None
    assert SearchSection.model_validate({}).browser_sources == []
    assert g().enabled is False  # off until the person turns it on


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "indeed", "query": "ai"},
        {"kind": "vie"},
        {"kind": "vie", "query": ""},
        {"kind": "vie", "query": "x" * 201},
        {"kind": "vie", "query": "ai", "enabled": "yes"},
        {"kind": "vie", "query": "ai", "url": "https://example.org"},
        {"kind": "vie", "query": "ai\nbad"},
    ],
)
def test_bad_browser_sources_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BrowserSource.model_validate(bad)
    with pytest.raises(ValidationError):
        SearchSection.model_validate({"browser_sources": [{"kind": "vie", "query": "a"}] * 11})


# --- the run -------------------------------------------------------------------------------------


@dataclass
class FakeTools:
    """URL to snapshot; a click shows `after_click`; `end_after` ends the lease after N calls;
    `store_gone_after` makes the snapshot after N calls find the lease store gone."""

    pages: dict[str, str]
    after_click: list[str] = field(default_factory=list)
    end_after: int | None = None
    calls: list[str] = field(default_factory=list)
    current: str = ""
    store_gone_after: int | None = None

    def _count(self) -> None:
        if self.end_after is not None and len(self.calls) >= self.end_after:
            raise BrowserError("ended")

    async def navigate(self, url: str) -> dict[str, Any]:
        self._count()
        self.calls.append(url)
        if url not in self.pages:
            return {"ok": False, "error": "navigation_failed"}
        self.current = url
        return {"ok": True}

    async def click(self, ref: str, element: str = "") -> dict[str, Any]:
        self._count()
        self.calls.append(f"click {ref}")
        self.pages[self.current] = self.after_click.pop(0)
        return {"ok": True}

    async def snapshot(self) -> dict[str, Any]:
        if self.store_gone_after is not None and len(self.calls) > self.store_gone_after:
            raise StoreUnavailableError  # LeaseTools._held without Redis
        return {"ok": True, "url": self.current, "snapshot": self.pages[self.current]}


class FakeLeases:
    def __init__(self, busy: bool = False, max_s: int = 900) -> None:
        self.busy, self.max_s = busy, max_s
        self.acquired: list[tuple[str, str, list[str]]] = []
        self.released: list[str] = []
        self.cancelled: list[str] = []

    async def acquire(self, user: str, purpose: str, automation: bool, *, hosts: Any) -> Acquired:
        self.acquired.append((user, purpose, list(hosts)))
        if self.busy:
            return Acquired(waiting=Waiting(purpose, 1, 60))
        lease = Lease(
            lease_id=f"lease-{len(self.acquired)}", user_id=user, purpose=purpose,
            automation=True, profile_id="p", max_s=self.max_s, idle_s=300, started_at=0.0,
        )  # fmt: skip
        return Acquired(lease=lease, token="invented-token")

    async def release(self, user: str, lease_id: str, reason: str = "") -> bool:
        self.released.append(lease_id)
        return True

    async def cancel(self, user: str, purposes: Any = None) -> int:
        self.cancelled.append(user)
        return 1


class Known:
    """The sweep's Known by URL only, normalised by the same rule (slug.py)."""

    def __init__(self, urls: set[str] | None = None) -> None:
        self.urls = {slugs.PostingKey.of("", "", "", url).url for url in urls or set()}

    def has(self, key: Any) -> bool:
        return key.url in self.urls


@dataclass
class Plan:
    sources: list[BrowserSource]
    known: Known = field(default_factory=Known)
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Stats:
    sources: list[dict[str, Any]] = field(default_factory=list)
    current_board: str | None = None
    new: int = 0


def google_site(count: int, total: int | None = None) -> dict[str, str]:
    made = pages.offers(count)
    site = {gc.results_url(g(), 1): pages.google_results(made, total=total)}
    for o in made:
        site[f"{gc.RESULTS}{o.number}-{pages.slug(o.title)}"] = pages.google_posting(o)
    return site


def run_sources(*args: Any, **kwargs: Any) -> Any:
    return asyncio.run(arun_sources(*args, **kwargs))


async def arun_sources(
    plan: Plan, tools: FakeTools, leases: FakeLeases | None = None, *, deadline: float = 1e9,
    clock: list[float] | None = None, pacer: Pacer | None = None,
    store_fails: set[str] | None = None, rng: random.Random | None = None,
) -> tuple[Stats, list[tuple[str, list[Any]]], list[float], FakeLeases]:  # fmt: skip
    leases = leases or FakeLeases()
    slept: list[float] = []
    stored: list[tuple[str, list[Any]]] = []
    now = clock or [0.0]

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    async def store(name: str, found: Any) -> None:
        if name in (store_fails or set()):
            raise RuntimeError("the database went away")
        stored.append((name, list(found)))
        stats.new += len(found)

    async def progress() -> None:
        return None

    stats = Stats()
    runner = Runner(
        leases,  # type: ignore[arg-type]
        lambda user, lease, token: tools,
        sleep=sleep,
        rng=rng or random.Random(7),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: now[0],
        pacer=pacer or MemoryPacer(lambda: now[0]),
    )
    await runner(plan, stats, store, progress, deadline)
    return stats, stored, slept, leases


def test_a_source_reads_results_then_postings_paced_and_stores_them() -> None:
    tools = FakeTools(google_site(3))
    stats, stored, slept, leases = run_sources(Plan([g(enabled=True)]), tools)
    assert len(tools.calls) == 4  # one results page, three posting pages
    # Every page waits out the pause since the host's previous page; the host was idle before
    # the first one, which opens at once.
    assert len(slept) == 3
    assert all(PAGE_PAUSE_S[0] <= s <= PAGE_PAUSE_S[1] for s in slept)
    [(name, found)] = stored
    assert name == "google_careers:ai"
    assert [company for company, _ in found] == ["Example Robotics"] * 3
    assert found[0][1].text.startswith("AI Engineer 1")
    assert stats.sources[0] | {} == {
        "kind": "google_careers", "query": "ai", "location": None,
        "result_pages": 1, "found": 3, "postings": 3, "error": None,
    }  # fmt: skip
    assert leases.acquired[0][1:] == ("sweep", ["www.google.com"])
    assert leases.released == ["lease-1"]


def test_at_most_ten_result_pages() -> None:
    site: dict[str, str] = {}
    for n in range(1, 13):
        made = pages.offers(1, start=n)
        site[gc.results_url(g(), n)] = pages.google_results(made, first=n, total=99)
    tools = FakeTools(site)
    plan = Plan(
        [g()], known=Known({f"{gc.RESULTS}{1000 + n}-ai-engineer-{n}" for n in range(1, 13)})
    )
    stats, _, _, _ = run_sources(plan, tools)
    assert len(tools.calls) == RESULT_PAGES_MAX
    assert stats.sources[0]["result_pages"] == RESULT_PAGES_MAX


def test_known_postings_are_not_opened_and_the_posting_pages_are_capped() -> None:
    site = google_site(25, total=25)
    tools = FakeTools(site)
    first = f"{gc.RESULTS}1001-ai-engineer-1"
    run_sources(Plan([g()], known=Known({first})), tools)
    assert first not in tools.calls
    assert len(tools.calls) == 1 + 24
    assert POSTING_PAGES_MAX >= 24


def test_a_changed_layout_stores_nothing_and_the_next_source_runs() -> None:
    broken = google_site(2)
    broken[gc.results_url(g(), 1)] = broken[gc.results_url(g(), 1)].replace("corporate_fare", "x")
    vie_source = BrowserSource.model_validate({"kind": "vie", "query": "ai"})
    made = pages.offers(1, location="FRANCE - PARIS")
    broken[vie.results_url(vie_source)] = pages.vie_results(made)
    broken[f"https://mon-vie-via.businessfrance.fr/en/offres/{made[0].number}"] = pages.vie_offer(
        made[0]
    )
    stats, stored, _, leases = run_sources(Plan([g(), vie_source]), FakeTools(broken))
    assert [s["error"] for s in stats.sources] == ["layout_changed", None]
    assert [name for name, _ in stored] == ["vie:ai"]
    assert stored[0][1][0][1].published == "2026-10-20"
    assert leases.released == ["lease-1", "lease-2"]  # every lease released


def test_a_lease_ended_mid_source_keeps_what_was_read_and_stops_the_sweeps_sources() -> None:
    tools = FakeTools(google_site(3), end_after=3)  # results, two postings, then the end
    other = g(query="robotics")
    stats, stored, _, leases = run_sources(Plan([g(), other]), tools)
    assert [s["error"] for s in stats.sources] == ["lease_ended", "lease_ended"]
    assert len(stored) == 1
    assert len(stored[0][1]) == 2
    assert len(leases.acquired) == 1  # the second source never asks for the browser


def test_a_busy_browser_is_not_waited_for() -> None:
    leases = FakeLeases(busy=True)
    stats, stored, _, _ = run_sources(Plan([g(), g(query="b")]), FakeTools({}), leases)
    assert [s["error"] for s in stats.sources] == ["browser_busy", "browser_busy"]
    assert leases.cancelled
    assert not stored
    assert len(leases.acquired) == 1


def test_the_time_budget_stops_before_the_deadline() -> None:
    clock = [0.0]
    stats, stored, _, _ = run_sources(
        Plan([g()]), FakeTools(google_site(5)), deadline=85.0, clock=clock
    )
    assert stats.sources[0]["error"] == "time_budget"
    assert clock[0] + 60 >= 85.0 - 15  # stopped within one pause of the budget
    assert stored
    assert 0 < len(stored[0][1]) < 5


def test_a_page_that_does_not_load_is_page_failed() -> None:
    site = google_site(2)
    del site[f"{gc.RESULTS}1002-ai-engineer-2"]
    stats, stored, _, _ = run_sources(Plan([g()]), FakeTools(site))
    assert stats.sources[0]["error"] == "page_failed"
    assert len(stored[0][1]) == 1


def test_vie_grows_the_list_by_clicks_then_reads_offers_in_the_location() -> None:
    source = BrowserSource.model_validate({"kind": "vie", "query": "ai", "location": "france"})
    first = pages.offers(2, location="FRANCE - PARIS")
    second = first + pages.offers(2, start=3, location="ITALY - MILAN")
    site = {vie.results_url(source): pages.vie_results(first, total=4, more=True)}
    for o in second:
        site[f"https://mon-vie-via.businessfrance.fr/en/offres/{o.number}"] = pages.vie_offer(o)
    tools = FakeTools(site, after_click=[pages.vie_results(second, total=4)])
    stats, stored, _, leases = run_sources(Plan([source]), tools)
    assert tools.calls[1] == "click f7e9184"
    assert stats.sources[0]["result_pages"] == 2
    assert [p.location for _, p in stored[0][1]] == ["Paris, France", "Paris, France"]
    assert leases.acquired[0][2] == ["mon-vie-via.businessfrance.fr"]


def test_base_url_points_every_source_at_the_fixture_host() -> None:
    assert gc.READER.host("http://pages:8080/b8") == "pages"
    assert vie.READER.host(None) == vie.HOST
    assert vie.results_url(
        BrowserSource.model_validate({"kind": "vie", "query": "a b"}), "http://pages:8080/b8/"
    ) == ("http://pages:8080/b8/vie/en/offres/recherche?query=a+b")


def test_source_name() -> None:
    assert source_name(g()) == "google_careers:ai"
    assert source_name(g(location="Paris")) == "google_careers:ai/Paris"


def test_card_is_a_value() -> None:
    assert Card("a", "b", "c", "d") == Card("a", "b", "c", "d")


# --- round 2: pacing per host, the lease's budget, a failure per source ---------------------------


def empty_google(source: BrowserSource) -> dict[str, str]:
    return {gc.results_url(source, 1): pages.google_results([])}


def test_the_first_page_of_a_lease_waits_out_the_hosts_previous_page() -> None:
    other = g(query="robotics")
    tools = FakeTools(google_site(3) | empty_google(other))
    _, _, slept, leases = run_sources(Plan([g(), other]), tools)
    assert len(leases.acquired) == 2  # two leases, one host
    assert len(tools.calls) == 5
    assert len(slept) == 4  # the second lease's first page is paced too
    assert all(PAGE_PAUSE_S[0] <= s <= PAGE_PAUSE_S[1] for s in slept)


def test_a_host_idle_for_longer_than_the_pause_opens_at_once() -> None:
    pacer = MemoryPacer(lambda: clock[0])
    clock = [0.0]
    assert asyncio.run(pacer.reserve("h", 10.0)) == 0.0
    assert asyncio.run(pacer.reserve("h", 10.0)) == 10.0  # a slot after the one just taken
    assert asyncio.run(pacer.reserve("other", 10.0)) == 0.0  # per host
    clock[0] = 100.0
    assert asyncio.run(pacer.reserve("h", 10.0)) == 0.0


def test_two_people_and_two_workers_share_the_pace_of_a_host(redis_url: str) -> None:  # noqa: F811
    flush(redis_url)
    clock = [0.0]
    pool, close = pool_of(redis_url)

    async def go() -> list[float]:
        try:
            one, two = RedisPacer(pool, lambda: clock[0]), RedisPacer(pool, lambda: clock[0])
            waits = [await one.reserve("www.google.com", 10.0)]
            waits.append(await two.reserve("www.google.com", 12.0))  # another worker, at once
            waits.append(await one.reserve("www.google.com", 5.0))
            waits.append(await two.reserve("mon-vie-via.businessfrance.fr", 5.0))
            return waits
        finally:
            await close()

    assert asyncio.run(go()) == [0.0, 12.0, 17.0, 0.0]

    # Person A's source, then person B's on the same host: B's first page is paced after A's.
    flush(redis_url)
    pool, close = pool_of(redis_url)
    shared = [0.0]

    async def people() -> tuple[list[float], list[float]]:
        try:
            pacer = RedisPacer(pool, lambda: shared[0])
            a, b = FakeTools(google_site(1)), FakeTools(google_site(1))
            _, _, slept_a, _ = await arun_sources(Plan([g()]), a, clock=shared, pacer=pacer)
            _, _, slept_b, _ = await arun_sources(Plan([g()]), b, clock=shared, pacer=pacer)
            return slept_a, slept_b
        finally:
            await close()

    slept_a, slept_b = asyncio.run(people())
    assert len(slept_a) == 1
    assert len(slept_b) == 2  # B's first page waited for A's last one
    assert PAGE_PAUSE_S[0] <= slept_b[0] <= PAGE_PAUSE_S[1]


def test_the_pacer_without_redis_is_browser_unavailable() -> None:
    async def gone() -> Any:
        raise ConnectionError("no redis")

    pacer = RedisPacer(gone, lambda: 0.0)
    stats, stored, _, leases = run_sources(
        Plan([g(), g(query="b")]), FakeTools(google_site(1)), pacer=pacer
    )
    assert [s["error"] for s in stats.sources] == ["browser_unavailable", "browser_unavailable"]
    assert not stored
    assert leases.released == ["lease-1"]


class Slowest(random.Random):
    """The longest pause every time: the worst case of the lease's budget."""

    def uniform(self, a: float, b: float) -> float:
        return b


@pytest.mark.parametrize("max_s", [900, 300, 120])
def test_the_page_caps_fit_inside_the_lease(max_s: int) -> None:
    """The source stops opening pages when the lease's remaining active time cannot hold another
    paced page, reports lease_budget, stores what it read, and the next source runs on a lease of
    its own: no source ever ends the others by running its lease out."""
    site = google_site(POSTING_PAGES_MAX, total=POSTING_PAGES_MAX)
    other = BrowserSource.model_validate({"kind": "vie", "query": "ai"})
    made = pages.offers(1, location="FRANCE - PARIS")
    site[vie.results_url(other)] = pages.vie_results(made)
    site[f"https://mon-vie-via.businessfrance.fr/en/offres/{made[0].number}"] = pages.vie_offer(
        made[0]
    )
    clock = [0.0]
    starts: list[float] = []
    tools = FakeTools(site)
    navigate = tools.navigate

    async def timed(url: str) -> dict[str, Any]:
        starts.append(clock[0])
        clock[0] += PAGE_LOAD_S  # every page takes the longest load
        return await navigate(url)

    tools.navigate = timed  # type: ignore[method-assign]
    stats, stored, _, leases = run_sources(
        Plan([g(), other]), tools, FakeLeases(max_s=max_s), clock=clock, rng=Slowest()
    )
    first, second = stats.sources
    assert first["error"] == "lease_budget"
    assert second["error"] is None  # not lease_ended: the next source ran
    assert len(leases.acquired) == 2
    assert stored[0][0] == "google_careers:ai"
    assert 0 < len(stored[0][1]) < POSTING_PAGES_MAX
    google_pages = len(stored[0][1]) + 1
    # Every page of the first lease ended within its maximum of active time.
    assert starts[google_pages - 1] + PAGE_LOAD_S <= max_s


def test_a_reader_failing_on_an_odd_page_is_layout_changed_and_the_sweep_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def odd(root: Any, title: str) -> str:
        raise ValueError("invalid literal for int()")

    site = google_site(2)
    other = BrowserSource.model_validate({"kind": "vie", "query": "ai"})
    made = pages.offers(1, location="FRANCE - PARIS")
    site[vie.results_url(other)] = pages.vie_results(made)
    site[f"https://mon-vie-via.businessfrance.fr/en/offres/{made[0].number}"] = pages.vie_offer(
        made[0]
    )
    monkeypatch.setattr(gc, "parse_posting", odd)
    stats, stored, _, leases = run_sources(Plan([g(), other]), FakeTools(site))
    assert [s["error"] for s in stats.sources] == ["layout_changed", None]
    assert [name for name, _ in stored] == ["vie:ai"]  # nothing of the failed source
    assert leases.released == ["lease-1", "lease-2"]


def test_the_lease_store_gone_mid_source_is_browser_unavailable() -> None:
    tools = FakeTools(google_site(3), store_gone_after=2)  # results, one posting, then gone
    stats, stored, _, leases = run_sources(Plan([g(), g(query="b")]), tools)
    assert [s["error"] for s in stats.sources] == ["browser_unavailable", "browser_unavailable"]
    assert len(leases.acquired) == 1  # the next source does not start without the store
    assert [len(found) for _, found in stored] == [1]  # what was read completely is kept


def test_a_store_failure_is_store_failed_and_the_next_source_runs() -> None:
    other = g(query="robotics")
    site = google_site(2) | {gc.results_url(other, 1): pages.google_results(pages.offers(1))}
    stats, stored, _, _ = run_sources(
        Plan([g(), other]), FakeTools(site), store_fails={"google_careers:ai"}
    )
    assert [s["error"] for s in stats.sources] == ["store_failed", None]
    assert stats.sources[0]["postings"] == 0
    assert [name for name, _ in stored] == ["google_careers:robotics"]


def test_a_store_failure_after_the_lease_ended_still_stops_the_sweeps_sources() -> None:
    tools = FakeTools(google_site(3), end_after=3)
    stats, _, _, leases = run_sources(
        Plan([g(), g(query="b")]), tools, store_fails={"google_careers:ai"}
    )
    assert [s["error"] for s in stats.sources] == ["store_failed", "lease_ended"]
    assert len(leases.acquired) == 1


def test_an_expired_posting_sent_to_the_results_is_skipped_not_stored() -> None:
    site = google_site(3)
    site[f"{gc.RESULTS}1002-ai-engineer-2"] = site[gc.results_url(g(), 1)]
    stats, stored, _, _ = run_sources(Plan([g()]), FakeTools(site))
    assert stats.sources[0]["error"] is None
    assert [p.title for _, p in stored[0][1]] == ["AI Engineer 1", "AI Engineer 3"]


# --- BROWSER_SOURCES_BASE_URL ------------------------------------------------------------------

DB_URL = "postgresql+psycopg://jobapp:secret@db:5432/jobapp"


def test_the_fixture_address_is_read_outside_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import load_settings

    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BROWSER_SOURCES_BASE_URL", " http://pages:8080/b8/ ")
    assert load_settings().browser_sources_base_url == "http://pages:8080/b8"
    monkeypatch.setenv("BROWSER_SOURCES_BASE_URL", "  ")
    assert load_settings().browser_sources_base_url is None


def test_the_fixture_address_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import ConfigError, load_settings

    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    monkeypatch.setenv("BROWSER_SOURCES_BASE_URL", "http://pages:8080/b8")
    with pytest.raises(ConfigError, match=r"BROWSER_SOURCES_BASE_URL .* must not be set in prod"):
        load_settings()
