"""Jobs J22 (#808): when LinkedIn's public job search API refuses (429, 999) or fails before it
searched every saved pair, the same sweep reads LinkedIn's public job search PAGE in the browser,
signed out, by code (owner, 2026-11-18: "Yes, fall back to browser").

Fakes only: the guest API is test_linkedin_guest's LinkedInFake (an httpx.MockTransport) told to
refuse; the browser is `PageTools`, a stand in for browserd's discovery tools that serves the
invented LinkedIn search and posting pages of tests/browser_pages/j22/ (the pages server of the
browser test stack serves the same files) and answers browser_evaluate as the reader's scripts
read those pages (`read_cards`, `read_posting`: the same selectors). The scripts themselves run
on real Chromium in test_j22_linkedin_page_container.py. No test reaches LinkedIn."""

import asyncio
import itertools
import json
import random
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
import pytest

from app.agents import activity_steps
from app.browser import finder, sources
from app.browser.sources import linkedin_guest as guest
from app.browser.sources import linkedin_page as page
from app.jobs import sweep
from tests.browser.fakes import FakeLeases, Known
from tests.browser.test_linkedin_guest import LINKEDIN, Allow, LinkedInFake, section

ROOT = Path(__file__).resolve().parents[3] / "tests" / "browser_pages"
BASE_URL = "http://pages.test:8080/j22"
BASE = f"{BASE_URL}/linkedin"


def node_text(node: Any) -> str:
    return " ".join(node.text().split()) if node is not None else ""


def read_cards(html: str, here: str) -> dict[str, Any]:
    """CARDS_SCRIPT's reading of a page, by the same selectors."""
    root = guest._tree(html)
    cards = []
    for card in root.walk():
        if "base-search-card" not in card.classes():
            continue
        link = next(
            (n for n in card.walk() if n.tag == "a" and "base-card__full-link" in n.classes()),
            None,
        )
        stamp = next((n for n in card.walk() if n.tag == "time" and "datetime" in n.attrs), None)
        cards.append(
            {
                "urn": card.attrs.get("data-entity-urn", ""),
                "link": urljoin(here, link.attrs.get("href", "")) if link else "",
                "title": node_text(card.first("base-search-card__title")),
                "company": node_text(card.first("base-search-card__subtitle")),
                "place": node_text(card.first("job-search-card__location")),
                "date": stamp.attrs.get("datetime", "") if stamp else "",
            }
        )
    lists = [n for n in root.walk() if "jobs-search__results-list" in n.classes()]
    items = (
        sum(1 for c in lists[0].children if not isinstance(c, str) and c.tag == "li")
        if lists
        else 0
    )
    urns = sum(1 for n in root.walk() if "data-entity-urn" in n.attrs)
    none = {"no-results", "jobs-search-no-results", "jobs-search-no-results-banner"}
    return {
        "cards": cards,
        "listed": max(items, urns),
        "results": bool(lists),
        "none": any(n.classes() & none for n in root.walk()),
    }


def read_posting(html: str) -> dict[str, Any]:
    """POSTING_SCRIPT's reading of a page, by the same selectors."""
    root = guest._tree(html)
    markup = root.first("show-more-less-html__markup")
    criteria = [
        [
            node_text(item.first("description__job-criteria-subheader")),
            node_text(item.first("description__job-criteria-text")),
        ]
        for item in root.walk()
        if "description__job-criteria-item" in item.classes()
    ]
    place = next(
        (
            n
            for n in root.walk()
            if "topcard__flavor--bullet" in n.classes()
            and "num-applicants__caption" not in n.classes()
        ),
        None,
    )
    return {
        "description": markup.text(blocks=True) if markup is not None else "",
        "criteria": criteria,
        "title": node_text(root.first("top-card-layout__title")),
        "company": node_text(root.first("topcard__org-name-link")),
        "location": node_text(place),
    }


class PageTools:
    """browserd's discovery tools on one sweep lease, over the J22 fixture pages: a search page
    answers `search/<search>` whatever the words (engineer.html: three cards), a posting
    `view/<id>/index.html` (404 when there is none). `statuses` and `redirects` override a
    navigation by its number (1 based); `heading` is the page's one heading in the snapshot."""

    def __init__(self, now: list[float], statuses: dict[int, int] | None = None,
                 redirects: dict[int, str] | None = None, heading: str = "Engineer jobs",
                 empty: bool = False, search: str = "engineer.html") -> None:  # fmt: skip
        self.now = now
        self.statuses, self.redirects = statuses or {}, redirects or {}
        self.heading, self.empty, self.search = heading, empty, search
        self.navigations: list[tuple[str, float]] = []
        self.calls: list[str] = []
        self.scopes: list[Any] = []
        self.here, self.html = "", ""

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        assert args.get("_discovery") == "native_get"
        self.scopes.append(args.get("_discovery_scope"))
        if name == "browser_navigate":
            self.navigations.append((args["url"], self.now[0]))
            number = len(self.navigations)
            self.here = self.redirects.get(number, args["url"])
            status = self.statuses.get(number, 200)
            path = urlsplit(self.here).path.removeprefix("/j22/linkedin/jobs/")
            if path == "search":
                file = ROOT / "j22/linkedin/jobs/search" / self.search
            else:
                file = ROOT / "j22/linkedin/jobs" / path / "index.html"
            if not file.is_file() or self.empty:
                status = self.statuses.get(number, 404 if path != "search" else 200)
                self.html = "<html><body></body></html>"
            else:
                self.html = file.read_text()
            return {"ok": True, "status": status, "url": self.here}
        if name == "browser_evaluate":
            if args["function"] == page.CARDS_SCRIPT:
                value = read_cards(self.html, self.here)
            else:
                assert args["function"] == page.POSTING_SCRIPT
                value = read_posting(self.html)
            return {"ok": True, "result": json.dumps(value), "cut": False}
        raise AssertionError(f"no {name} on a reader of LinkedIn's page")

    async def snapshot(self) -> dict[str, Any]:
        self.calls.append("browser_snapshot")
        return {"ok": True, "snapshot": f'- heading "{self.heading}" [level=1]', "url": self.here}

    async def wait_for(self, **args: Any) -> dict[str, Any]:
        raise AssertionError("no wait")

    def paths(self) -> list[str]:
        out = []
        for url, _ in self.navigations:
            parts = urlsplit(url)
            start = parse_qs(parts.query).get("start", ["0"])[0]
            out.append(parts.path.rsplit("/", 1)[1] + (f"?start={start}" if start != "0" else ""))
        return out


def run(
    search: Any,
    answers: dict[int, tuple[int, dict[str, str]]] | None = None,
    tools: PageTools | None = None,
    *,
    enabled: bool = True,
    known: Any = None,
    policy: Any = None,
) -> tuple[list[dict[str, Any]], PageTools, FakeLeases, LinkedInFake]:
    """One sweep's browser hook (sources.Runner) as the worker builds it: LinkedIn's guest API
    first, the page after it when it refused."""
    now = [0.0]
    fake = LinkedInFake(now, answers={1: (429, {})} if answers is None else answers)
    tools = tools or PageTools(now)
    tools.now = now
    leases = FakeLeases()

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    async def progress() -> None:
        json.dumps(stats.as_json())

    scope = sweep.freeze_scope(
        SimpleNamespace(sections={"search": search.model_dump(mode="json")}, revision=3),
        sweep.SweepTrigger.manual,
        datetime.fromisoformat("2026-11-18T00:00:00+00:00"),
    )
    stats = sweep.Stats(sources=sweep.initial_sources(scope))
    plan = SimpleNamespace(
        search=search,
        user_id=uuid.uuid4(),
        search_scope=scope,
        sweep_id=uuid.uuid4(),
        known=known or Known(),
        rules=None,
        linkedin_not_searched={},  # sweep.Plan's: every pair the public job search left
    )
    runner = sources.Runner(
        leases,
        lambda *_: tools,
        sleep=sleep,
        monotonic=lambda: now[0],
        rng=random.Random(11),  # noqa: S311  deterministic test pacing
        pacer=sources.MemoryPacer(lambda: now[0]),
        interactive_policy=policy or Allow(),
        base_url=BASE_URL,
        guest_http=lambda: httpx.MockTransport(fake.handler),
        guest_cooldown=guest.MemoryCooldown(lambda: now[0]),
        linkedin_page=enabled,
    )
    asyncio.run(runner(plan, stats, progress, 100_000.0))
    return stats.sources, tools, leases, fake


def reunion(**fields: Any) -> Any:
    return section(
        title_include={"terms": ["AI Engineer", "Data Engineer"]},
        location={"terms": ["La Réunion"]},
        **fields,
    )


def test_after_a_429_the_page_reads_the_pairs_not_searched_signed_out_by_code() -> None:
    entries, tools, leases, fake = run(reunion())
    [entry] = entries
    assert len(fake.requests) == 1  # the guest API refused at once
    # One sweep lease, a discovery whose scope never says signed in: a fresh context.
    assert [(purpose, hosts) for _, purpose, hosts in leases.acquired] == [
        ("sweep", finder.lease_hosts(f"{BASE}/jobs/search"))
    ]
    assert leases.released == ["lease-1"]
    assert all(scope == {"start": f"{BASE}/jobs/search"} for scope in tools.scopes)
    # Code reads every page: navigate, its snapshot, one script; never a sign in, never a click.
    assert set(tools.calls) == {"browser_navigate", "browser_snapshot", "browser_evaluate"}
    # Breadth first: the first page of both searches, then the new cards' postings; no next
    # page, as a page of three cards is not a full page (PAGE_SIZE).
    assert tools.paths() == ["search", "search", "4200000101", "4200000102"]
    first = urlsplit(tools.navigations[0][0])
    assert first.path == "/j22/linkedin/jobs/search"
    assert first.query == "keywords=AI%20Engineer&location=Reunion&f_TPR=r2592000"
    assert (entry["fallback"], entry["fallback_after"]) == ("page", "linkedin_rate_limited")
    assert entry["found"] == 2  # Miami, FL is of no saved place (J20): never read
    assert entry["other_place"] == 1
    assert (entry["searches"], entry["planned"]) == (2, 2)
    assert entry["not_searched"] == []
    assert entry["searched"] == [
        {"title": "AI Engineer", "place": "La Réunion"},
        {"title": "Data Engineer", "place": "La Réunion"},
    ]
    assert entry["page"] == {
        "searches": 2,
        "result_pages": 2,
        "cards": 6,
        "details": 2,
        "missing": 0,
        "postings": 0,  # no commits in this run: `found` counts them
    }


def test_the_page_is_paced_ten_to_thirty_seconds_after_any_page_of_linkedin() -> None:
    _, tools, _, fake = run(reunion())
    times = [fake.times[0]] + [at for _, at in tools.navigations]
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert gaps
    assert all(10.0 <= gap <= 30.0 for gap in gaps), gaps


def test_after_a_failure_before_every_pair_the_page_reads_the_rest() -> None:
    entries, tools, _, _ = run(reunion(), answers={1: (500, {})})
    [entry] = entries
    assert (entry["fallback"], entry["fallback_after"]) == ("page", "page_failed")
    assert entry["found"] == 2
    assert tools.navigations


def test_no_page_after_a_public_search_that_searched_every_pair() -> None:
    entries, tools, leases, _ = run(reunion(), answers={})
    [entry] = entries
    assert "fallback" not in entry
    assert (tools.navigations, leases.acquired) == ([], [])


def test_a_runner_without_the_page_never_opens_the_browser() -> None:
    entries, tools, leases, _ = run(reunion(), enabled=False)
    assert entries[0]["reason"] == "linkedin_rate_limited"
    assert (tools.navigations, leases.acquired) == ([], [])


@pytest.mark.parametrize(
    "tools",
    [
        PageTools([0.0], redirects={1: f"{BASE}/authwall?trk=public_jobs"}),
        PageTools([0.0], redirects={1: f"{BASE}/checkpoint/challenge/x"}),
        PageTools([0.0], statuses={1: 429}),
        PageTools([0.0], statuses={1: 999}),
        PageTools([0.0], heading="Sign in to continue", empty=True),
    ],
    ids=["authwall", "checkpoint", "429", "999", "sign-in-heading"],
)
def test_a_wall_or_a_refusal_stops_the_page_at_once_and_is_recorded(tools: PageTools) -> None:
    entries, tools, leases, _ = run(reunion(), tools=tools)
    [entry] = entries
    assert len(tools.navigations) == 1  # never retried, never a sign in
    assert (entry["reason"], entry["outcome"], entry["fallback"]) == (
        "linkedin_page_refused",
        "failed",
        "page",
    )
    assert leases.released == ["lease-1"]
    # The refused page read nothing: both pairs stay not searched.
    assert entry["not_searched"] == [
        {"title": "AI Engineer", "place": "La Réunion"},
        {"title": "Data Engineer", "place": "La Réunion"},
    ]
    assert (entry["searches"], entry["page"]["searches"]) == (0, 0)


def test_once_a_sweep_and_never_chained_with_the_retry_or_the_guest_fallback() -> None:
    entries, tools, _, _ = run(reunion())
    # The page took the retry slot (B24) of the refused public search: never both.
    assert entries[0]["not_retried"] == sweep.LINKEDIN_PAGE
    refused = {**entries[0], "fallback": None, "phase": "done", "outcome": "failed"}
    refused.update(reason="linkedin_rate_limited", not_searched=[{"title": "x", "place": None}])
    assert sweep.linkedin_page_due(refused)
    assert sweep._failed(sweep.Stats(sources=[refused]), {refused["source_id"]}) == []
    # A failure B24 retries, or is about to, is not followed by the page.
    failed = {**refused, "reason": "page_failed", "not_retried": None}
    assert sweep.linkedin_page_due(failed)
    assert not sweep.linkedin_page_due({**failed, "first_attempt": {"outcome": "failed"}})
    assert not sweep.linkedin_page_due({**failed, "attempt": 2})
    # A public search that stood in for the browser search (J20) never falls back again.
    assert not sweep.linkedin_page_due({**refused, "fallback": "guest"})
    assert not sweep.linkedin_page_due({**refused, "not_searched": []})
    assert not sweep.linkedin_page_due({**refused, "reason": "permission_revoked"})
    # A second call in the same sweep opens nothing.
    stats = sweep.Stats(sources=[dict(e) for e in entries])
    count = len(tools.navigations)

    async def progress() -> None:
        return None

    runner = SimpleNamespace(base_url=BASE_URL)
    plan = SimpleNamespace(search=reunion(), search_scope=None, commits=None, sweep_id=None)
    asyncio.run(page.fall_back(runner, plan, stats, progress, 1000.0))  # type: ignore[arg-type]
    assert len(tools.navigations) == count
    assert "linkedin_page_refused" in sweep.NOT_RETRIED


def test_known_postings_are_never_opened_again() -> None:
    known = Known({f"{BASE}/jobs/view/4200000101/"})
    entries, tools, _, _ = run(reunion(), known=known)
    assert "4200000101" not in tools.paths()
    assert entries[0]["duplicates"] == 1


def test_activity_and_the_summary_say_the_page_stood_in() -> None:
    note = activity_steps._source_note(
        {"mode": "api", "fallback": "page", "fallback_after": "linkedin_rate_limited", "found": 2}
    )
    assert note == "2 found, LinkedIn's public job search page, as the public API refused"


def test_the_cards_and_the_posting_are_read_whatever_their_shape() -> None:
    cards = page.cards_of(
        {
            "cards": [
                {"urn": "urn:li:jobPosting:11", "title": "AI Engineer", "date": "2026-11-31"},
                {"urn": "", "link": f"{BASE}/jobs/view/ai-engineer-at-x-12?trk=1", "title": "B"},
                {"urn": "urn:li:jobPosting:11", "title": "Again"},
                {"urn": "urn:li:jobPosting:13", "title": ""},
                {"link": "javascript:alert(1)", "title": "No id"},
                "not a card",
            ]
        },
        "pages.test",
    )
    assert [(c.job_id, c.title, c.published) for c in cards] == [
        ("11", "AI Engineer", ""),
        ("12", "B", ""),
    ]
    assert page.cards_of({"cards": "x"}, "pages.test") == []
    assert page.posting_of({"description": "  "}) is None
    detail = page.posting_of(
        {"description": "Build\x00 agents", "criteria": [["Employment type", "Internship"], [1]]}
    )
    assert detail is not None
    assert (detail.description, detail.criteria) == (
        "Build agents",
        {"Employment type": "Internship"},
    )
    with pytest.raises(guest.StopError):
        page._loads({"result": "[1, 2]"})


def test_the_address_is_built_by_code_from_the_saved_pair() -> None:
    [query] = guest.every_query(section(location={"terms": ["Tahiti"]}, days=7))
    assert page.page_url(guest.BASE_URL, query, 7, 25) == (
        "https://www.linkedin.com/jobs/search?keywords=GenAI%20Engineer"
        "&location=French%20Polynesia&f_TPR=r604800&start=25"
    )
    assert page.posting_page(guest.BASE_URL, "42") == "https://www.linkedin.com/jobs/view/42"
    assert LINKEDIN["url"].startswith("https://www.linkedin.com")


def test_activity_says_failed_after_a_failure_of_the_public_api() -> None:
    for reason in ("page_failed", "layout_changed"):
        note = activity_steps._source_note(
            {"mode": "api", "fallback": "page", "fallback_after": reason}
        )
        assert note == "LinkedIn's public job search page, as the public API failed"


# --- fail closed: a changed layout, a page or a card off LinkedIn (round 1) ----------------------


@pytest.mark.parametrize(
    "tools",
    [
        PageTools([0.0], search="changed.html"),  # list items, cards of a renamed class
        PageTools([0.0], empty=True),  # neither a results list nor a no results message
    ],
    ids=["renamed-cards", "no-list-no-message"],
)
def test_a_first_page_on_a_changed_layout_is_never_an_empty_result(tools: PageTools) -> None:
    entries, tools, leases, _ = run(reunion(), tools=tools)
    [entry] = entries
    assert len(tools.navigations) == 1  # the source ends at the first page
    assert (entry["reason"], entry["outcome"], entry["fallback"]) == (
        "layout_changed",
        "failed",
        "page",
    )
    assert entry["not_searched"] == [
        {"title": "AI Engineer", "place": "La Réunion"},
        {"title": "Data Engineer", "place": "La Réunion"},
    ]
    assert (entry["searches"], entry["page"]["searches"]) == (0, 0)
    assert leases.released == ["lease-1"]


def test_linkedins_no_results_page_is_an_empty_result() -> None:
    entries, tools, _, _ = run(reunion(), tools=PageTools([0.0], search="none.html"))
    [entry] = entries
    assert tools.paths() == ["search", "search"]
    assert entry["reason"] is None
    assert (entry["searches"], entry["not_searched"]) == (2, [])
    assert (entry["page"]["searches"], entry["page"]["cards"]) == (2, 0)


@pytest.mark.parametrize(
    ("value", "changed"),
    [
        ({"cards": [], "listed": 0, "results": True, "none": False}, False),  # an empty list
        ({"cards": [], "listed": 0, "results": False, "none": True}, False),  # the message
        ({"cards": [], "listed": 0, "results": True, "none": True}, False),
        ({"cards": [], "listed": 3, "results": True, "none": False}, True),  # renamed cards
        ({"cards": [], "listed": 3, "results": False, "none": False}, True),  # urns, no list
        ({"cards": [], "listed": 1, "results": True, "none": True}, True),
        ({"cards": [], "listed": 0, "results": False, "none": False}, True),  # nothing known
        ({"cards": []}, True),  # an older script
        ({"cards": [], "listed": "0", "results": True, "none": False}, True),
        ({"cards": [], "listed": -1, "results": True, "none": False}, True),
        ({"cards": [], "listed": 0.0, "results": True, "none": False}, True),
        ({"cards": [], "listed": True, "results": True, "none": False}, True),
        ({"cards": [], "listed": 0, "results": 1, "none": False}, True),
        ({"cards": [], "listed": 0, "results": True, "none": None}, True),
        ({"cards": [], "listed": None, "results": None, "none": None}, True),
    ],
)
def test_the_layout_rule_fails_closed(value: dict[str, Any], changed: bool) -> None:
    assert page._layout_changed(value) is changed


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("https://www.linkedin.com/jobs/search?keywords=x", True),
        ("https://linkedin.com/jobs/search", True),
        ("https://fr.linkedin.com/jobs/search", True),
        ("http://www.linkedin.com/jobs/search", True),
        ("https://WWW.LinkedIn.com/jobs/search", True),
        ("http://pages.test:8080/j22/linkedin/jobs/search", True),  # the fixture host
        ("https://evil.example/jobs/view/1", False),
        ("https://linkedin.com.evil.example/jobs/view/1", False),
        ("https://evillinkedin.com/jobs/view/1", False),
        ("https://www.linkedin.com.example/jobs/view/1", False),
        ("https://www.linkedin.co/jobs/view/1", False),
        ("https://linkedin.evil.example/jobs/view/1", False),
        ("https://evil.example/?u=https://www.linkedin.com/jobs/view/1", False),
        ("https://evil.example#www.linkedin.com", False),
        ("https://www.linkedin.com@evil.example/jobs/view/1", False),
        ("ftp://www.linkedin.com/jobs/view/1", False),
        ("javascript:alert(1)", False),
        ("data:text/html,linkedin.com", False),
        ("//www.linkedin.com/jobs/view/1", False),
        ("/jobs/view/1", False),
        ("", False),
        ("https://[::1/jobs", False),
        ("https://www.linkedin.com/" + "x" * 2000, False),
        ("http://pages.test.evil.example/jobs/view/1", False),
    ],
)
def test_an_address_is_linkedins_or_the_fixture_hosts_only(url: str, kept: bool) -> None:
    assert page.on_linkedin(url, "pages.test") is kept


def test_a_page_off_linkedin_ends_the_page_with_nothing_stored() -> None:
    tools = PageTools([0.0], redirects={1: "https://evil.example/jobs/search?keywords=AI"})
    entries, tools, leases, _ = run(reunion(), tools=tools)
    [entry] = entries
    assert len(tools.navigations) == 1
    assert "browser_evaluate" not in tools.calls  # nothing it shows is read
    assert (entry["reason"], entry["fallback"]) == ("page_failed", "page")
    assert entry["found"] == 0
    assert (entry["searches"], len(entry["not_searched"])) == (0, 2)
    assert leases.released == ["lease-1"]


def test_a_card_whose_link_leads_off_linkedin_is_dropped() -> None:
    cards = page.cards_of(
        {
            "cards": [
                {"link": "https://evil.example/jobs/view/21", "title": "Elsewhere"},
                {"urn": "urn:li:jobPosting:22", "link": "https://evil.example/x", "title": "U"},
                {"link": "https://fr.linkedin.com/jobs/view/ai-engineer-23", "title": "Fr"},
                {"urn": "urn:li:jobPosting:24", "link": "", "title": "No link"},
                {"link": "https://www.linkedin.com.evil.example/jobs/view/25", "title": "Look"},
            ]
        },
        "www.linkedin.com",
    )
    assert [(c.job_id, c.title) for c in cards] == [("23", "Fr"), ("24", "No link")]


# --- the bounds: pages, permission, every pair, next pages, missing pages (round 1) --------------


def many(titles: int) -> Any:
    return section(
        title_include={"terms": [f"Engineer {n}" for n in range(titles)]},
        location={"terms": ["La Réunion"]},
    )


def test_at_most_pages_max_pages_and_the_rest_stays_not_searched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(page, "PAGES_MAX", 3)
    entries, tools, leases, _ = run(many(4))
    [entry] = entries
    assert tools.paths() == ["search", "search", "search"]
    assert entry["reason"] is None  # the cap ends it without an error, what it read kept
    assert entry["page"]["searches"] == 3
    assert entry["not_searched"] == [{"title": "Engineer 3", "place": "La Réunion"}]
    assert leases.released == ["lease-1"]


class RevokedAfterFirstPage(Allow):
    """The permission withdrawn once the reader opened its first page."""

    def __init__(self, tools: PageTools) -> None:
        super().__init__()
        self.tools = tools

    async def check(self, context: Any) -> str | None:
        self.checks += 1
        return "permission_revoked" if self.tools.navigations else None


def test_a_permission_withdrawn_mid_run_stops_the_page_before_its_next_call() -> None:
    tools = PageTools([0.0])
    entries, tools, leases, _ = run(reunion(), tools=tools, policy=RevokedAfterFirstPage(tools))
    [entry] = entries
    # The navigation was checked before it; the next call (its snapshot) is refused.
    assert tools.calls == ["browser_navigate"]
    assert (entry["reason"], entry["fallback"]) == ("permission_revoked", "page")
    assert (entry["searches"], len(entry["not_searched"])) == (0, 2)
    assert leases.released == ["lease-1"]


def test_every_pair_left_is_read_past_the_fifty_the_summary_lists() -> None:
    entries, tools, _, _ = run(many(55))
    [entry] = entries
    assert tools.paths().count("search") == 55
    assert (entry["searches"], entry["planned"]) == (55, 55)
    assert entry["not_searched"] == []
    assert len(entry["searched"]) == 50  # the summary lists 50, `searches` counts them all
    assert entry["page"]["searches"] == 55


@pytest.mark.parametrize(
    ("size", "paths"),
    [
        # A full page with new cards: its next page is read (start = page * PAGE_SIZE).
        (3, ["search", "search", "4200000101", "4200000102", "search?start=3"]),
        # A page of fewer cards than a full page: no next page, even with new cards.
        (4, ["search", "search", "4200000101", "4200000102"]),
    ],
)
def test_a_next_page_only_after_a_full_page_with_new_cards(
    monkeypatch: pytest.MonkeyPatch, size: int, paths: list[str]
) -> None:
    monkeypatch.setattr(page, "PAGE_SIZE", size)
    _, tools, _, _ = run(reunion())
    assert tools.paths() == paths


def test_a_search_page_gone_leaves_its_pair_not_searched() -> None:
    entries, tools, _, _ = run(reunion(), tools=PageTools([0.0], statuses={1: 404}))
    [entry] = entries
    assert tools.paths()[:2] == ["search", "search"]  # the other search goes on
    # No failure: the cards of the other search were read (no commits in this run).
    assert (entry["reason"], entry["found"]) == ("finding_persistence_pending", 2)
    assert entry["not_searched"] == [{"title": "AI Engineer", "place": "La Réunion"}]
    assert entry["searched"] == [{"title": "Data Engineer", "place": "La Réunion"}]
    assert (entry["page"]["searches"], entry["page"]["missing"]) == (1, 1)


@pytest.mark.parametrize("status", [500, 503, 403, 302])
def test_a_search_page_answering_an_error_ends_the_page_with_its_pair_not_searched(
    status: int,
) -> None:
    entries, tools, _, _ = run(reunion(), tools=PageTools([0.0], statuses={2: status}))
    [entry] = entries
    assert tools.paths() == ["search", "search"]
    assert entry["reason"] == "page_failed"
    assert entry["searched"] == [{"title": "AI Engineer", "place": "La Réunion"}]
    assert entry["not_searched"] == [{"title": "Data Engineer", "place": "La Réunion"}]
