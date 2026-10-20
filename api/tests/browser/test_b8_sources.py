"""Browser B8: the extractors of Google Careers and Business France VIE on snapshots written after
the live pages (tests/browser/b8_pages.py), and the run of the sources with fake tools and a fake
lease store. Pure: no Redis, no database, no browser, no sleep (the pacing's sleep is recorded)."""

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from app.browser.client import BrowserError
from app.browser.leases import Acquired, Lease, Waiting
from app.browser.sources import (
    PAGE_PAUSE_S,
    POSTING_PAGES_MAX,
    RESULT_PAGES_MAX,
    Card,
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
    root = parse_snapshot(pages.vie_results(pages.offers(6), total=192, more=True))
    assert vie.parse_results(root, VIE_PAGE)[1] == "f7e9184"


@pytest.mark.parametrize(
    "text",
    [
        pages.vie_results(pages.offers(2)).replace("offers found", "offres trouvées"),
        pages.vie_results(pages.offers(2)).replace("[level=3]", "[level=4]"),
        pages.vie_results(pages.offers(2)).replace("[level=2] [ref=f7e1", "[level=6] [ref=f7e1"),
        "",
    ],
)
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
    ],
)
def test_vie_offer_url(href: str, kept: bool) -> None:
    assert (vie.offer_url(href, VIE_PAGE) is not None) is kept


@pytest.mark.parametrize(
    ("published", "iso"), [("20/10/2026", "2026-10-20"), ("31/13/2026", ""), ("1/1/2026", "")]
)
def test_vie_offer_text_and_date(published: str, iso: str) -> None:
    offer = pages.offers(1)[0]
    text, date = vie.parse_offer(parse_snapshot(pages.vie_offer(offer, published)), offer.title)
    assert date == iso
    assert "Missions : build agents with Python." in text
    assert "OFFRES A LA UNE" not in text
    assert "Another offer" not in text


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
    """URL to snapshot; a click shows `after_click`; `end_after` ends the lease after N calls."""

    pages: dict[str, str]
    after_click: list[str] = field(default_factory=list)
    end_after: int | None = None
    calls: list[str] = field(default_factory=list)
    current: str = ""

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
        return {"ok": True, "url": self.current, "snapshot": self.pages[self.current]}


class FakeLeases:
    def __init__(self, busy: bool = False) -> None:
        self.busy = busy
        self.acquired: list[tuple[str, str, list[str]]] = []
        self.released: list[str] = []
        self.cancelled: list[str] = []

    async def acquire(self, user: str, purpose: str, automation: bool, *, hosts: Any) -> Acquired:
        self.acquired.append((user, purpose, list(hosts)))
        if self.busy:
            return Acquired(waiting=Waiting(purpose, 1, 60))
        lease = Lease(
            lease_id=f"lease-{len(self.acquired)}", user_id=user, purpose=purpose,
            automation=True, profile_id="p", max_s=900, idle_s=300, started_at=0.0,
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


def run_sources(
    plan: Plan, tools: FakeTools, leases: FakeLeases | None = None, *, deadline: float = 1e9,
    clock: list[float] | None = None,
) -> tuple[Stats, list[tuple[str, list[Any]]], list[float], FakeLeases]:  # fmt: skip
    leases = leases or FakeLeases()
    slept: list[float] = []
    stored: list[tuple[str, list[Any]]] = []
    now = clock or [0.0]

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    async def store(name: str, found: Any) -> None:
        stored.append((name, list(found)))
        stats.new += len(found)

    async def progress() -> None:
        return None

    stats = Stats()
    runner = Runner(
        leases,  # type: ignore[arg-type]
        lambda user, lease, token: tools,
        sleep=sleep,
        rng=random.Random(7),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: now[0],
    )
    asyncio.run(runner(plan, stats, store, progress, deadline))
    return stats, stored, slept, leases


def test_a_source_reads_results_then_postings_paced_and_stores_them() -> None:
    tools = FakeTools(google_site(3))
    stats, stored, slept, leases = run_sources(Plan([g(enabled=True)]), tools)
    assert len(tools.calls) == 4  # one results page, three posting pages
    assert len(slept) == 3  # every page but the first of the lease
    assert all(PAGE_PAUSE_S[0] <= s <= PAGE_PAUSE_S[1] for s in slept)
    [(name, found)] = stored
    assert name == "google_careers:ai"
    assert [company for company, _ in found] == ["Example Robotics"] * 3
    assert found[0][1].text.startswith("AI Engineer 1")
    assert stats.sources[0] | {} == {
        "kind": "google_careers", "query": "ai", "location": None,
        "result_pages": 1, "postings": 3, "error": None,
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
