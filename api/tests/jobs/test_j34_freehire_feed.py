"""Jobs J34: the freehire feed (app/jobs/feeds/freehire.py), read from a fake of its public
interface on a `.test` host through a fake transport, never the real host. What a sweep asks for
(titles as `OR` alternatives, places as countries or cities, the age window, the work mode),
paging within its budget, its pause and its own time bound, the statuses, a 429, an answer that
is not JSON, an empty answer, what each posting maps to (its own address, freehire's page when
it has none, a closed one left out), the bounds on what a third party writes, and a sweep that
stores, dedupes against another source and counts it as a source."""

import json
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.apply.answers import COUNTRIES
from app.config import Settings, get_settings
from app.jobs import feeds
from app.jobs.feeds import TEXT_MAX, Criteria, FeedRunner
from app.jobs.feeds import freehire as fh
from app.jobs.models import ContractType, Job, JobEvent, JobStatus, SweepStatus, WorkMode
from tests.jobs.sweep_helpers import jobs_of, new_sweep, read_sweep, set_profile
from tests.jobs.sweep_helpers import search as sweep_search
from tests.jobs.test_job_feeds import NOW, Slow, Transport, entry_of, fixture, run, search

BASE = "https://freehire.example.test"
DATABASE = "postgresql+psycopg://jobapp:invented@db.example.test:5432/jobapp"
HOST = "freehire.example.test"


def posting(n: int, **fields: Any) -> dict[str, Any]:
    return {
        "public_slug": f"ai-engineer-example-{n}",
        "source": "greenhouse",
        "url": f"https://boards.example-ats.test/example/jobs/{n}",
        "title": f"AI Engineer {n}",
        "company": f"Example Company {n}",
        "location": "Zürich, Switzerland",
        "description": "<p>Agents.</p>",
        "countries": ["ch"],
        "posted_at": (NOW - timedelta(days=1)).isoformat(),
        "enrichment": {},
        **fields,
    }


class Fake:
    """The interface as its documents describe it: `{data, meta}` on its one search path, 404
    `{"error": "not found"}` elsewhere, a 400 for more than five `OR` alternatives or an
    offset past 10000. `pages` answers by offset (a list of postings, a status, or raw bytes);
    every request is recorded."""

    def __init__(
        self,
        pages: dict[int, Any] | None = None,
        *,
        total: int | None = None,
        default: Callable[[int], Any] | None = None,
    ) -> None:
        self.pages = pages if pages is not None else {0: fixture("freehire")["data"]}
        self.total = total
        self.default = default
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host != HOST or request.url.path != fh.SEARCH:
            return httpx.Response(404, json={"error": "not found"})
        params = request.url.params
        offset, limit = int(params.get("offset", "0")), int(params.get("limit", "20"))
        if len(params.get("q", "").split(" OR ")) > 5 or offset + limit > 10_000:
            return httpx.Response(400, json={"error": "bad request"})
        answer = self.pages.get(offset)
        if answer is None and self.default is not None:
            answer = self.default(offset)
        if answer is None:
            answer = []
        if isinstance(answer, int):
            return httpx.Response(answer, json={"error": "status"})
        if isinstance(answer, bytes):
            return httpx.Response(200, stream=httpx.ByteStream(answer))
        total = self.total if self.total is not None else offset + len(answer)
        body = {"data": answer, "meta": {"total": total, "limit": limit, "offset": offset}}
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(body).encode()))

    @property
    def transport(self) -> Transport:
        return Transport(self.handler)


def titles(*terms: str) -> dict[str, Any]:
    return {"terms": list(terms), "advanced_regex": False}


def freehire_search(**overrides: Any) -> Any:
    return search(
        **{
            "title_include": titles("forward deployed", "ai engineer"),
            "location": {"terms": ["suisse"], "advanced_regex": False},
            "job_feeds": ["freehire"],
            **overrides,
        }
    )


def read(fake: Fake, section: Any = None, **runner: Any) -> tuple[dict[str, Any], Any, list[float]]:
    entries, commits, pauses = run(
        fake,  # type: ignore[arg-type]
        section if section is not None else freehire_search(),
        sources={"freehire": fh.Freehire(base=BASE)},
        transport=runner.pop("transport", None) or fake.transport,
        **runner,
    )
    return entry_of(entries, "freehire"), commits, pauses


def saved(commits: Any) -> dict[str, Any]:
    return {f.title: f for f in commits.saved.get("freehire", [])}


# --- what a sweep asks for ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("places", "countries", "cities"),
    [
        (["suisse"], ("ch",), ()),
        (["Switzerland"], ("ch",), ()),
        (["la France"], ("fr",), ()),
        (["United Kingdom"], ("gb",), ()),
        (["UK"], ("gb",), ()),
        (["Allemagne", "Germany"], ("de",), ()),
        (["Genève"], (), ("Genève", "Geneva")),
        (["geneva"], (), ("geneva",)),
        (["  Lyon  "], (), ("Lyon",)),
        (["Paris, France"], (), ("Paris, France", "Paris")),
        (["suisse", "Genève", "Remote"], ("ch",), ("Genève", "Geneva")),
        # A region, a word for remote work: not a country nor a city, left out of the request.
        (["Europe"], (), ()),
        (["EU"], (), ()),
        (["North America"], (), ()),
        (["Remote"], (), ()),
        (["télétravail"], (), ()),
        (["Full Remote"], (), ()),
        (["Europe", "Lyon"], (), ("Lyon",)),
        # Everywhere means no place at all, whatever else is saved.
        (["everywhere", "suisse"], (), ()),
        (["Partout"], (), ()),
        ([], (), ()),
        # A country's name inside a longer place is a city here, never the whole country.
        (["Remote France"], (), ("Remote France",)),
        (["Zürich"], (), ("Zürich", "Zurich")),
    ],
)
def test_places_become_countries_or_cities(
    places: list[str], countries: tuple[str, ...], cities: tuple[str, ...]
) -> None:
    criteria = Criteria.of(freehire_search(location={"terms": places, "advanced_regex": False}))
    found = fh.places_of(criteria)
    assert (found.countries, found.cities) == (countries, cities)
    assert found.sent == bool(countries or cities)


def test_every_country_of_the_apps_table_has_its_code() -> None:
    assert set(fh.ISO) | fh.REGIONS == set(COUNTRIES)
    assert not set(fh.ISO) & fh.REGIONS
    assert all(len(code) == 2 and code.islower() for code in fh.ISO.values())


def test_places_are_bounded() -> None:
    many = [f"Town {n}" for n in range(60)]
    criteria = Criteria.of(freehire_search(location={"terms": many, "advanced_regex": False}))
    assert len(fh.places_of(criteria).cities) == fh.CITIES_MAX


@pytest.mark.parametrize(
    ("titles", "groups"),
    [
        (["Forward Deployed"], [("forward deployed",)]),
        # An uppercase OR inside a title never makes it two alternatives.
        (["Data OR AI"], [("data or ai",)]),
        (['"AI" engineer: (agents)'], [("ai engineer agents",)]),
        (["AI Engineer", "ai engineer", "  AI   Engineer "], [("ai engineer",)]),
        (
            [f"title {n}" for n in range(7)],
            [tuple(f"title {n}" for n in range(5)), ("title 5", "title 6")],
        ),
        ([":::"], []),
    ],
)
def test_titles_are_alternatives_five_a_request(
    titles: list[str], groups: list[tuple[str, ...]]
) -> None:
    assert fh.groups_of(tuple(titles)) == groups


def test_a_long_title_is_cut() -> None:
    [[alternative]] = fh.groups_of(("x" * 5000,))
    assert len(alternative) == fh.ALTERNATIVE_MAX


@pytest.mark.parametrize(
    ("stance", "mode"),
    [("remote", "remote"), ("hybrid", "hybrid"), ("office", None), ("any", None), (None, None)],
)
def test_the_remote_stance_is_a_work_mode_when_it_excludes_something(
    stance: str | None, mode: str | None
) -> None:
    criteria = Criteria.of(freehire_search(remote_stance=stance, days=14))
    params = fh.params_of(criteria, fh.places_of(criteria))
    assert params.get("work_mode") == mode
    assert params["posted_within_days"] == 14


def test_the_request_names_the_titles_places_and_window() -> None:
    fake = Fake()
    entry, _, _ = read(fake, freehire_search(remote_stance="remote"))
    [request] = fake.requests
    assert request.url.scheme == "https"
    assert request.url.host == HOST
    assert request.url.path == "/api/v1/agent/jobs/search"
    params = request.url.params
    assert params["q"] == "title:forward deployed OR title:ai engineer"
    assert params.get_list("countries") == ["ch"]
    assert "cities" not in params
    assert params["posted_within_days"] == "30"
    assert params["work_mode"] == "remote"
    assert (params["limit"], params["offset"]) == ("25", "0")
    assert params["description_format"] == "html"
    assert request.headers["User-Agent"] == feeds.USER_AGENT
    assert request.headers["Accept"] == "application/json"
    assert "authorization" not in request.headers
    assert entry["kind"] == "freehire"
    assert entry["searches"] == 2
    assert entry["searched"] == [
        {"title": "forward deployed", "place": None},
        {"title": "ai engineer", "place": None},
    ]


def test_cities_are_repeated_parameters() -> None:
    fake = Fake()
    read(fake, freehire_search(location={"terms": ["Genève", "Lyon"], "advanced_regex": False}))
    assert fake.requests[0].url.params.get_list("cities") == ["Genève", "Geneva", "Lyon"]


# --- paging, budget, pause and time ---------------------------------------------------------------


def full_page(offset: int) -> list[dict[str, Any]]:
    return [posting(offset + n) for n in range(fh.PAGE_SIZE)]


def test_it_pages_within_its_budget_two_seconds_apart() -> None:
    fake = Fake({}, total=5000, default=full_page)
    entry, commits, pauses = read(fake)
    assert [r.url.params["offset"] for r in fake.requests] == [str(25 * n) for n in range(8)]
    assert pauses == [fh.PAUSE_S] * 7
    assert (fh.PAUSE_S, fh.BUDGET, fh.TIME_S) == (2.0, 8, 60.0)
    assert entry["result_pages"] == 8
    assert entry["outcome"] == "complete"
    assert entry["read"] == 200
    assert len(commits.saved["freehire"]) == 200


def test_it_stops_at_a_short_page_and_at_the_total() -> None:
    fake = Fake({0: full_page(0), 25: [posting(99)]}, total=5000)
    read(fake)
    assert [r.url.params["offset"] for r in fake.requests] == ["0", "25"]
    exact = Fake({0: full_page(0), 25: full_page(25)}, total=50)
    read(exact)
    assert [r.url.params["offset"] for r in exact.requests] == ["0", "25"]


def test_title_groups_share_the_budget_and_the_rest_is_left_out() -> None:
    fake = Fake({}, total=5000, default=full_page)
    twelve = [f"engineer {n}" for n in range(12)]
    entry, _, _ = read(fake, freehire_search(title_include=titles(*twelve)))
    queries = [r.url.params["q"] for r in fake.requests]
    assert [queries.count(q) for q in dict.fromkeys(queries)] == [3, 3, 2]
    assert entry["searches"] == 12
    many = [f"role {n}" for n in range(45)]
    again = Fake({}, total=5000, default=full_page)
    entry, _, _ = read(again, freehire_search(title_include=titles(*many)))
    assert len(again.requests) == 8
    assert entry["searches"] == 40
    assert entry["titles_left_out"] == 5


def test_its_own_time_bound_keeps_what_was_read() -> None:
    class Quick(fh.Freehire):
        time_s = 0.2

    fake = Fake({0: full_page(0)}, total=5000)
    slow = Slow(fake.handler, lambda request: request.url.params.get("offset") == "25")
    entries, commits, _ = run(
        fake,  # type: ignore[arg-type]
        freehire_search(),
        sources={"freehire": Quick(base=BASE)},
        transport=slow,
    )
    entry = entry_of(entries, "freehire")
    assert entry["error"] == "time_budget"
    assert entry["outcome"] == "partial"
    assert len(commits.saved["freehire"]) == 25


# --- statuses and answers -------------------------------------------------------------------------


def test_a_429_stops_the_feed_for_the_sweep_and_keeps_what_was_read() -> None:
    fake = Fake({0: full_page(0), 25: 429}, total=5000)
    entry, commits, _ = read(fake)
    assert len(fake.requests) == 2
    assert entry["error"] == "rate_limited"
    assert entry["outcome"] == "partial"
    assert len(commits.saved["freehire"]) == 25


def test_a_429_skips_the_title_groups_left() -> None:
    fake = Fake({0: 429})
    seven = [f"engineer {n}" for n in range(7)]
    entry, _, _ = read(fake, freehire_search(title_include=titles(*seven)))
    assert len(fake.requests) == 1
    assert entry["titles_left_out"] == 2


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (400, "upstream_error"),
        (403, "blocked"),
        (404, "not_found"),
        (500, "upstream_error"),
        (503, "upstream_error"),
        (b"<html><body>Maintenance</body></html>", "invalid_response"),
        (b'{"data": {"not": "a list"}}', "invalid_response"),
        (b'["a", "list"]', "invalid_response"),
        (b"", "invalid_response"),
    ],
)
def test_statuses_and_answers_that_are_not_postings(answer: Any, code: str) -> None:
    fake = Fake({0: answer})
    entry, commits, _ = read(fake)
    assert entry["error"] == code
    assert commits.saved == {}


def test_a_path_the_host_does_not_serve_is_not_found() -> None:
    fake = Fake()
    entries, _, _ = run(
        fake,  # type: ignore[arg-type]
        freehire_search(),
        sources={"freehire": fh.Freehire(base=f"{BASE}/old")},
        transport=fake.transport,
    )
    assert entry_of(entries, "freehire")["error"] == "not_found"


def test_an_empty_answer_ends_complete_with_nothing() -> None:
    fake = Fake({0: []}, total=0)
    entry, commits, _ = read(fake)
    assert len(fake.requests) == 1
    assert entry["outcome"] == "no_matches"
    assert entry["error"] is None
    assert (entry["read"], entry["found"]) == (0, 0)
    assert commits.saved == {}


# --- what a posting maps to -----------------------------------------------------------------------


def test_each_posting_maps_and_the_criteria_filter() -> None:
    entry, commits, _ = read(Fake())
    kept = saved(commits)
    assert set(kept) == {"Forward Deployed Engineer", "Senior AI Engineer", "AI Engineer Intern"}
    # Read: neither the closed one nor the one with no address; the sales and the old one are
    # left out by the criteria.
    assert entry["read"] == 5
    assert entry["left_out"] == {"unwanted": 1, "old": 1}
    fde = kept["Forward Deployed Engineer"]
    # Its own address: the application system's page, never freehire's.
    assert fde.url == "https://boards.example-ats.test/example-robotics/jobs/4001"
    assert fde.company == "Example Robotics"
    # "suisse" is not in "Zürich, Switzerland": the request asked for Switzerland.
    assert fde.location == "Zürich, Switzerland"
    assert fde.placed is True
    assert fde.work_mode == "hybrid"
    assert fde.published_on == "2026-10-28"
    assert fde.contract is None  # full time says no contract type
    assert "Build LLM agents" in fde.text
    assert "Ship agents" in fde.text
    assert "❤️" in fde.text
    # No markup, no script, and no address of the description kept.
    assert "steal" not in fde.text
    assert "elsewhere.example.test" not in fde.text
    assert fde.evidence == {
        "api": "freehire",
        "external_id": "forward-deployed-engineer-example-robotics-a1b2",
        "upstream": "greenhouse",
        "page": f"{BASE}/jobs/forward-deployed-engineer-example-robotics-a1b2",
    }
    # No address of its own: freehire's page of it, and its work mode in the place.
    remote = kept["Senior AI Engineer"]
    assert remote.url == f"{BASE}/jobs/senior-ai-engineer-example-freight-c3d4"
    assert remote.location == "Remote"
    assert remote.work_mode == "remote"
    assert remote.evidence["upstream"] == "lever"
    intern = kept["AI Engineer Intern"]
    assert intern.url == "http://careers.example-lab.test/jobs/i9"
    assert intern.contract is ContractType.Stage
    assert intern.work_mode == WorkMode.office.value
    assert intern.posted_at is None  # no date: kept


@pytest.mark.parametrize(
    ("url", "slug", "expected"),
    [
        ("https://jobs.example-ats.test/x", "s-1", "https://jobs.example-ats.test/x"),
        ("HTTPS://Jobs.Example-ATS.test/x#apply", "s-1", "https://jobs.example-ats.test/x"),
        ("javascript:alert(1)", "s-1", f"{BASE}/jobs/s-1"),
        ("data:text/html,hi", "s-1", f"{BASE}/jobs/s-1"),
        ("ftp://jobs.example-ats.test/x", "s-1", f"{BASE}/jobs/s-1"),
        ("https://user:pw@jobs.example-ats.test/x", "s-1", f"{BASE}/jobs/s-1"),
        ("https://127.0.0.1/x", "s-1", f"{BASE}/jobs/s-1"),
        ("https://jobs.example-ats.test\\@evil.test/x", "s-1", f"{BASE}/jobs/s-1"),
        ("https://jobs.example-ats.test/" + "a" * 3000, "s-1", f"{BASE}/jobs/s-1"),
        ("/relative/path", "s-1", f"{BASE}/jobs/s-1"),
        (None, "s-1", f"{BASE}/jobs/s-1"),
        (["https://jobs.example-ats.test/x"], "s-1", f"{BASE}/jobs/s-1"),
        # No address and no slug a page can be built from: left out.
        (None, None, None),
        ("javascript:alert(1)", "../../admin", None),
        (None, "a/b", None),
        (None, "x" * 300, None),
    ],
)
def test_addresses_are_http_or_https_else_freehires_page(
    url: Any, slug: Any, expected: str | None
) -> None:
    found = fh.finding_of(posting(1, url=url, public_slug=slug), NOW, BASE, placed=False)
    assert (found.url if found is not None else None) == expected


@pytest.mark.parametrize("closed", ["2026-10-29T10:00:00Z", "yes", 1])
def test_a_closed_posting_is_left_out(closed: Any) -> None:
    assert fh.finding_of(posting(1, closed_at=closed), NOW, BASE, placed=False) is None


def test_an_upstream_name_is_kept_only_when_it_is_a_plain_name() -> None:
    for upstream in ("<script>", "a" * 60, 42, "Greenhouse Jobs"):
        found = fh.finding_of(posting(1, source=upstream), NOW, BASE, placed=False)
        assert found is not None
        assert "upstream" not in found.evidence


def test_what_a_third_party_writes_is_bounded_and_one_bad_item_fails_alone() -> None:
    huge = posting(
        1,
        title="AI Engineer " + "x" * 100_000,
        company="Example " * 10_000,
        location="Zürich " * 10_000,
        description="<p>" + "agents " * 200_000 + "</p>",
    )
    poisoned: list[Any] = [
        "a string",
        None,
        {"title": ["a", "list"], "url": {"a": "dict"}},
        posting(2, title=None),
        posting(3, posted_at="not a date"),
        huge,
        *full_page(100),
    ]
    entry, commits, _ = read(Fake({0: poisoned}, total=10_000))
    kept = commits.saved["freehire"]
    # Only the page's 25 first items are read, whatever the answer holds.
    assert entry["read"] == 21
    big = next(f for f in kept if f.external_id == "ai-engineer-example-1")
    assert len(big.title) <= 300
    assert len(big.company or "") <= 300
    assert len(big.location) <= 300
    assert len(big.text) <= TEXT_MAX
    undated = next(f for f in kept if f.external_id == "ai-engineer-example-3")
    assert undated.posted_at is None


# --- the places filter --------------------------------------------------------------------------


def test_with_no_place_sent_the_saved_places_filter_the_answer() -> None:
    fake = Fake()
    europe = freehire_search(location={"terms": ["Europe"], "advanced_regex": False})
    entry, commits, _ = read(fake, europe)
    assert "countries" not in fake.requests[0].url.params
    assert commits.saved == {}
    assert entry["left_out"] == {"place": 4, "unwanted": 1}
    # "Remote" alone: no place is sent, and the remote posting is kept by its place's word.
    remote = freehire_search(location={"terms": ["Remote"], "advanced_regex": False})
    _, commits, _ = read(Fake(), remote)
    assert set(saved(commits)) == {"Senior AI Engineer"}


def test_unwanted_places_still_apply_to_a_placed_posting() -> None:
    section = freehire_search(location_exclude={"terms": ["zürich"], "advanced_regex": False})
    entry, commits, _ = read(Fake(), section)
    assert "Forward Deployed Engineer" not in saved(commits)
    assert entry["left_out"] == {"unwanted": 3}


def test_a_feed_without_a_wanted_title_sends_nothing() -> None:
    fake = Fake()
    entry, _, _ = read(fake, freehire_search(title_include={"terms": [], "advanced_regex": False}))
    assert entry["error"] == "unsupported_criteria"
    assert fake.requests == []


# --- the setting ----------------------------------------------------------------------------------


def test_the_base_address_is_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE)
    monkeypatch.setenv("FREEHIRE_BASE_URL", f"{BASE}/")
    get_settings.cache_clear()
    try:
        assert fh.Freehire().base == BASE
        assert feeds.scope_entry("freehire")["host"] == HOST
    finally:
        monkeypatch.delenv("FREEHIRE_BASE_URL")
        get_settings.cache_clear()
    assert fh.Freehire().base == "https://freehire.me"
    assert feeds.scope_entry("freehire")["host"] == "freehire.me"


def test_the_setting_is_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="FREEHIRE_BASE_URL"):
        Settings(
            database_url=DATABASE, app_env="production", secret_key="s" * 40, freehire_base_url=BASE
        )
    with pytest.raises(ValidationError, match="FREEHIRE_BASE_URL must be an http or https"):
        Settings(database_url=DATABASE, freehire_base_url="ftp://freehire.example.test")


def test_it_is_read_last_and_only_when_ticked() -> None:
    assert list(feeds.feeds()) == ["arbeitnow", "himalayas", "adzuna", "freehire"]
    assert tuple(feeds.feeds()) == feeds.FEED_NAMES
    fake = Fake()
    entries, _, _ = run(
        fake,  # type: ignore[arg-type]
        search(job_feeds=["himalayas"]),
        sources={"freehire": fh.Freehire(base=BASE)},
        transport=fake.transport,
    )
    assert entries == []
    assert fake.requests == []


# --- in a sweep, against Postgres -----------------------------------------------------------------


def test_a_sweep_stores_dedupes_and_counts_freehire_as_a_source(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    import asyncio

    from app.jobs import sweep

    set_profile(
        factory,
        alice,
        sweep_search(
            interactive_sites=[],
            title_include={"terms": ["forward deployed", "ai engineer"], "advanced_regex": False},
            location={"terms": ["suisse"], "advanced_regex": False},
            remote_stance="remote",
            job_feeds=["freehire"],
            never_apply=["No interns: intern (test rule)"],
        ),
    )
    # Already in the list from another source: the same employer, title and place.
    with factory() as db, db.begin():
        db.add(
            Job(
                user_id=alice,
                slug="example-robotics-forward-deployed-engineer",
                company="Example Robotics",
                title="Forward Deployed Engineer",
                location="Zürich, Switzerland",
                url="https://careers.example-robotics.test/fde",
                source="manual",
                status=JobStatus.found,
                found_on=NOW.date(),
            )
        )
    sweep_id = new_sweep(factory, alice)
    frozen = read_sweep(factory, sweep_id).stats["search_scope"]["sources"]
    assert [(s["kind"], s["mode"]) for s in frozen] == [("freehire", "api")]
    fake = Fake()

    async def no_sleep(seconds: float) -> None:
        return None

    runner = FeedRunner(
        factory,
        transport=fake.transport,
        sleep=no_sleep,
        clock=lambda: NOW,
        sources={"freehire": fh.Freehire(base=BASE)},
    )
    asyncio.run(sweep.run_sweep(factory, sweep_id, lambda: NOW, feeds=runner))

    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.done
    [entry] = row.stats["sources"]
    assert entry["kind"] == "freehire"
    assert entry["outcome"] == "complete"
    assert entry["found"] == 3
    assert entry["postings"] == 2
    assert entry["duplicates"] == 1
    assert entry["skipped_by_rule"] == 1
    # The intern posting is on site, outside the remote stance: kept with its badge.
    assert entry["outside"] == 1
    assert {r.url.host for r in fake.requests} == {HOST}

    jobs = {job.title: job for job in jobs_of(factory, alice)}
    assert len(jobs) == 3
    assert jobs["Forward Deployed Engineer"].source == "manual, freehire"
    remote = jobs["Senior AI Engineer"]
    assert remote.source == "freehire"
    assert remote.url == f"{BASE}/jobs/senior-ai-engineer-example-freight-c3d4"
    assert remote.work_mode is WorkMode.remote
    assert "freight planning" in (remote.posting_text or "")
    assert remote.fit is not None
    intern = jobs["AI Engineer Intern"]
    assert intern.status is JobStatus.skipped
    assert intern.contract_type is ContractType.Stage
    with factory() as db:
        [event] = db.execute(
            sa.select(JobEvent.data).where(JobEvent.job_id == remote.id, JobEvent.kind == "sweep")
        ).scalars()
    assert event["discovery"]["evidence"] == {
        "api": "freehire",
        "external_id": "senior-ai-engineer-example-freight-c3d4",
        "upstream": "lever",
        "page": f"{BASE}/jobs/senior-ai-engineer-example-freight-c3d4",
    }
