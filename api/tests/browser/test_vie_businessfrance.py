"""Business France VIE offers through the site's public search API (owner, 2026-11-01),
app/browser/sources/vie_businessfrance.py.

Recorded fixtures only (vie_fixtures/): the public search page's inline config with an invented
key, and a search answer in the API's shape with invented companies, recruiters and ids. Every
request goes to an httpx.MockTransport: no test reaches the network. The clock is a fake one, so
the pacing is measured, never waited."""

import asyncio
import copy
import json
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.browser import sources
from app.browser.sources import linkedin_guest
from app.browser.sources import vie_businessfrance as vie
from app.jobs import sweep
from app.jobs.models import ContractType
from app.profile.schemas import SearchSection
from tests.browser.fakes import FakeLeases, Known

FIXTURES = Path(__file__).with_name("vie_fixtures")
KEY_PAGE = (FIXTURES / "search-page.html").read_text()
SEARCH = json.loads((FIXTURES / "search.json").read_text())
KEY = "stub/key/not/real"  # invented, as in the fixture page
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vie, "now", lambda: NOW)


def section(**fields: Any) -> SearchSection:
    data: dict[str, Any] = {
        "title_include": {"terms": ["GenAI Engineer", "Data Engineer"]},
        "vie_businessfrance": True,
    }
    data.update(fields)
    return SearchSection.model_validate(data)


def answer(status: int, body: str | bytes = "", headers: dict[str, str] | None = None) -> Any:
    data = body.encode() if isinstance(body, str) else body
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(data))


def page_of(offers: list[dict[str, Any]], count: int | None = None) -> str:
    return json.dumps({"result": offers, "count": len(offers) if count is None else count})


def many(start: int, size: int, title: str = "GenAI Engineer") -> list[dict[str, Any]]:
    base = SEARCH["result"][1]
    return [{**base, "id": start + i, "missionTitle": title} for i in range(size)]


class VieFake:
    """The key page, then search answers: `pages` keyed by (query, skip), else the fixture's
    answer; `answers` overrides by request number (1 based) with a status."""

    def __init__(self, now: list[float], pages: dict[tuple[str, int], str] | None = None,
                 answers: dict[int, int] | None = None,
                 key_page: str = KEY_PAGE) -> None:  # fmt: skip
        self.now, self.pages, self.answers, self.key_page = now, pages, answers or {}, key_page
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self.times: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.times.append(self.now[0])
        number = len(self.requests)
        if number in self.answers:
            return answer(self.answers[number])
        if request.url.path.endswith("/offres/recherche"):
            return answer(200, self.key_page, {"content-type": "text/html"})
        assert request.method == "POST"
        assert request.url.path.endswith("/api/Offers/search")
        if request.headers.get("x-api-key") != KEY:
            return answer(401)
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.pages is None:
            return answer(200, json.dumps(SEARCH) if body["skip"] == 0 else page_of([]))
        return answer(200, self.pages.get((body["query"], body["skip"]), page_of([])))


def execute(
    search: SearchSection,
    fake_of: Any = None,
    *,
    known: Any = None,
    deadline: float = 1000.0,
    transport: bool = True,
    pacer: Any = None,
    now: list[float] | None = None,
    cooldown: Any = None,
) -> tuple[list[dict[str, Any]], Any, list[float], Any]:
    """One sweep's VIE hook (Runner.vie) with this search; the fake answers."""
    now = now if now is not None else [0.0]
    fake = (fake_of or (lambda clock: VieFake(clock)))(now)

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    async def progress() -> None:
        json.dumps(stats.as_json())

    scope = sweep.freeze_scope(
        SimpleNamespace(sections={"search": search.model_dump(mode="json")}, revision=3),
        sweep.SweepTrigger.manual,
        NOW,
    )
    stats = sweep.Stats(sources=sweep.initial_sources(scope))
    plan = SimpleNamespace(
        search=search,
        user_id=uuid.uuid4(),
        search_scope=scope,
        sweep_id=uuid.uuid4(),
        known=known or Known(),
        rules=None,
    )
    runner = sources.Runner(
        FakeLeases(),
        lambda *_: None,
        sleep=sleep,
        monotonic=lambda: now[0],
        rng=random.Random(5),  # noqa: S311  deterministic test pacing
        pacer=pacer or sources.MemoryPacer(lambda: now[0]),
        guest_http=(lambda: httpx.MockTransport(fake.handler)) if transport else None,
        guest_cooldown=cooldown or linkedin_guest.MemoryCooldown(lambda: now[0]),
    )
    asyncio.run(runner.vie(plan, stats, progress, deadline))
    return stats.sources, fake, now, runner


# --- the setting ---------------------------------------------------------------------------------


def test_the_tick_is_off_by_default_and_a_sweep_then_reads_nothing() -> None:
    assert SearchSection.model_validate({}).vie_businessfrance is False
    entries, fake, _, _ = execute(section(vie_businessfrance=False))
    assert entries == []
    assert fake.requests == []


def test_off_is_never_frozen_and_on_is_one_api_source() -> None:
    def scope(on: bool) -> dict[str, Any]:
        return sweep.freeze_scope(
            SimpleNamespace(
                sections={"search": section(vie_businessfrance=on).model_dump(mode="json")},
                revision=1,
            ),
            sweep.SweepTrigger.manual,
            NOW,
        )

    off = scope(False)
    assert "vie_businessfrance" not in off["search"]
    assert off["sources"] == []
    on = scope(True)
    assert on["search"]["vie_businessfrance"] is True
    assert on["sources"] == [vie.scope_entry()]
    assert vie.scope_entry() == {
        "kind": "vie", "mode": "api", "host": "mon-vie-via.businessfrance.fr",
        "source_id": sweep._source_id("api", "vie"),
    }  # fmt: skip
    assert sweep.has_sources(section())
    assert not sweep.has_sources(section(vie_businessfrance=False))


# --- the key -------------------------------------------------------------------------------------


def test_the_key_is_read_from_the_public_page_and_its_escapes_undone() -> None:
    assert vie.public_key(KEY_PAGE) == KEY
    assert vie.public_key("<html>no config</html>") is None
    assert vie.public_key('API_KEY:"has space inside"') is None
    assert vie.public_key('API_KEY:""') is None


def test_a_page_without_key_ends_vie_key_missing_after_one_request() -> None:
    entries, fake, _, _ = execute(section(), lambda now: VieFake(now, key_page="<html></html>"))
    [entry] = entries
    assert (entry["error"], entry["outcome"], entry["requests"]) == (
        "vie_key_missing", "failed", 1,
    )  # fmt: skip
    assert len(fake.requests) == 1


def test_the_key_is_sent_as_a_header_and_never_written_to_the_entry() -> None:
    entries, fake, _, _ = execute(section())
    assert all(r.headers["x-api-key"] == KEY for r in fake.requests[1:])
    assert "x-api-key" not in fake.requests[0].headers
    assert KEY not in json.dumps(entries)
    assert all("cookie" not in r.headers for r in fake.requests)


# --- the searches --------------------------------------------------------------------------------


def test_one_search_per_saved_title_vie_only_with_the_countries_of_the_places() -> None:
    queries, skipped = vie.queries_for(
        section(location={"terms": ["Germany", "London, United Kingdom", "Bordeaux", "usa", "DE"]})
    )
    assert [q.title for q in queries] == ["GenAI Engineer", "Data Engineer"]
    assert queries[0].countries == ("DE", "GB", "US")
    assert skipped == ["Bordeaux"]  # a city: the app's place data gives no city's country
    body = vie.search_body(queries[0], 1)
    assert body["missionsTypesIds"] == ["VIE"]
    assert body["countriesIds"] == ["DE", "GB", "US"]
    assert (body["limit"], body["skip"], body["query"]) == (50, 50, "genai engineer")


@pytest.mark.parametrize("places", [[], ["Worldwide"], ["Paris", "partout"], ["everywhere"]])
def test_no_place_or_a_place_without_limit_sends_no_country(places: list[str]) -> None:
    queries, skipped = vie.queries_for(section(location={"terms": places}))
    assert all(q.countries == () for q in queries)
    assert skipped == []


def test_places_naming_no_country_send_no_country_and_are_listed() -> None:
    entries, fake, _, _ = execute(section(location={"terms": ["Bordeaux", "Remote"]}))
    assert fake.bodies[0]["countriesIds"] == []
    assert entries[0]["places_skipped"] == ["Bordeaux", "Remote"]
    assert "countries" not in entries[0]


def test_titles_are_capped_and_none_is_unsupported() -> None:
    queries, _ = vie.queries_for(section(title_include={"terms": [f"role {i}" for i in range(14)]}))
    assert len(queries) == vie.QUERIES_MAX == 10
    with pytest.raises(ValueError, match="unsupported_criteria"):
        vie.queries_for(section(title_include={"terms": []}))
    entries, fake, _, _ = execute(section(title_include={"terms": []}))
    assert entries[0]["error"] == "unsupported_criteria"
    assert fake.requests == []


# --- reading and filtering -----------------------------------------------------------------------


def test_offers_are_refiltered_on_the_saved_titles_and_kept_with_their_facts() -> None:
    entries, fake, _, _ = execute(section(title_include={"terms": ["GenAI Engineer"]}))
    [entry] = entries
    # The key page, then one page of the one title (3 offers, a short page).
    assert len(fake.requests) == 2
    assert entry["found"] == 1  # "Maintenance Technician" matched the API's substring only
    assert entry["left_out"] == {"title": 2}
    assert (entry["searches"], entry["planned"], entry["result_pages"]) == (1, 1, 1)
    assert entry["searched"] == [{"title": "GenAI Engineer", "place": None}]
    assert entry["error"] == "finding_persistence_pending"


def test_an_offer_becomes_a_finding_with_the_public_page_and_the_employer_address() -> None:
    found = vie.finding_of(SEARCH["result"][0], vie.SITE_URL, NOW)
    assert found is not None
    assert found.url == "https://mon-vie-via.businessfrance.fr/offres/900001"
    assert (found.title, found.company, found.location) == (
        "GENAI ENGINEER (H/F)", "Example Aero", "Munich, Germany",
    )  # fmt: skip
    assert found.contract is ContractType.VIE
    assert found.published_on == "2026-10-28"
    assert found.apply_url == "https://careers.example-aero.test/jobs/123"
    assert found.evidence["apply_url"] == found.apply_url
    assert found.evidence["external_id"] == "900001"
    assert "Duration: 18 months" in found.text
    assert "Monthly allowance: 3 121 EUR a month" in found.text
    assert "Build LLM agents for the flight test team." in found.text
    assert "Engineering degree." in found.text


def test_recruiter_names_e_mails_and_phone_numbers_are_dropped() -> None:
    found = vie.finding_of(SEARCH["result"][0], vie.SITE_URL, NOW)
    assert found is not None
    dumped = json.dumps([found.text, found.evidence, found.title, found.company, found.location])
    for private in ("Jane", "Doe", "e2e-recruiter-one", "@example.org", "1234 5678", "06 12 34"):
        assert private not in dumped
    assert "[e-mail removed]" in found.text
    assert "[phone removed]" in found.text


def test_an_unusable_contact_address_is_not_kept() -> None:
    for link in ["", "javascript:alert(1)", "http://careers.example.test/x", "https://x"]:
        offer = {**SEARCH["result"][0], "contactURL": link}
        found = vie.finding_of(offer, vie.SITE_URL, NOW)
        assert found is not None
        assert found.apply_url is None
        assert "apply_url" not in found.evidence


def test_an_offer_without_id_or_title_is_left_out_and_an_old_one_too() -> None:
    assert vie.finding_of({**SEARCH["result"][0], "id": "x1"}, vie.SITE_URL, NOW) is None
    assert vie.finding_of({**SEARCH["result"][0], "missionTitle": ""}, vie.SITE_URL, NOW) is None
    old = {**SEARCH["result"][1], "startBroadcastDate": None}  # created a year ago
    pages = {("data engineer", 0): page_of([old])}
    entries, _, _, _ = execute(section(), lambda now: VieFake(now, pages))
    assert entries[0]["left_out"] == {"old": 1}


def test_a_reposted_offer_counts_from_its_broadcast_start_but_shows_its_creation() -> None:
    found = vie.finding_of(SEARCH["result"][1], vie.SITE_URL, NOW)
    assert found is not None
    assert found.published_on == "2025-10-01"
    assert found.posted_at == datetime(2026, 10, 30, tzinfo=UTC)


def test_unwanted_titles_and_places_and_other_mission_types_are_left_out() -> None:
    via = {**SEARCH["result"][0], "id": 900010, "missionType": "VIA"}
    pages = {("genai engineer", 0): page_of([SEARCH["result"][0], SEARCH["result"][1], via])}
    entries, _, _, _ = execute(
        section(
            title_include={"terms": ["GenAI Engineer", "Data Engineer"]},
            title_exclude={"terms": ["data"]},
            location_exclude={"terms": ["Germany"]},
        ),
        lambda now: VieFake(now, pages),
    )
    assert entries[0]["left_out"] == {"unwanted": 2, "contract": 1}


def test_the_saved_contract_types_do_not_filter_the_vie_tick() -> None:
    entries, _, _, _ = execute(section(contract_types=["CDI"]))
    assert entries[0]["found"] == 2


def test_the_same_offer_in_two_searches_is_kept_once() -> None:
    pages = {
        ("genai engineer", 0): page_of([SEARCH["result"][1]]),
        ("data engineer", 0): page_of([SEARCH["result"][1]]),
    }
    entries, _, _, _ = execute(section(), lambda now: VieFake(now, pages))
    assert entries[0]["found"] == 1


# --- paging and caps -----------------------------------------------------------------------------


def test_pages_go_in_rounds_and_stop_at_three_a_search() -> None:
    pages = {
        ("genai engineer", skip): page_of(many(skip + 1, 50), 500) for skip in (0, 50, 100, 150)
    }
    pages[("data engineer", 0)] = page_of(many(1000, 10, "Data Engineer"))
    entries, fake, _, _ = execute(section(), lambda now: VieFake(now, pages))
    assert [(b["query"], b["skip"]) for b in fake.bodies] == [
        ("genai engineer", 0), ("data engineer", 0), ("genai engineer", 50),
        ("genai engineer", 100),
    ]  # fmt: skip
    assert entries[0]["found"] == 160
    assert entries[0]["error"] == "finding_persistence_pending"


def test_a_search_ends_at_its_count() -> None:
    pages = {("genai engineer", 0): page_of(many(1, 50), 50)}
    _, fake, _, _ = execute(section(title_include={"terms": ["GenAI Engineer"]}),
                            lambda now: VieFake(now, pages))  # fmt: skip
    assert len(fake.bodies) == 1


def test_at_most_thirty_requests_a_sweep_the_key_page_included() -> None:
    titles = [f"Engineer {i}" for i in range(10)]
    pages = {
        (t.casefold(), skip): page_of(many(i * 1000 + skip + 1, 50, t), 1000)
        for i, t in enumerate(titles)
        for skip in (0, 50, 100)
    }
    entries, fake, _, _ = execute(section(title_include={"terms": titles}),
                                  lambda now: VieFake(now, pages))  # fmt: skip
    assert len(fake.requests) == vie.REQUESTS_MAX == 30
    [entry] = entries
    assert entry["requests"] == 30
    assert entry["result_pages"] == 29
    assert entry["error"] == "finding_persistence_pending"  # the cap is no error


def test_requests_are_paced_three_to_five_seconds_apart_on_the_api_host() -> None:
    now = [100.0]
    pacer = sources.MemoryPacer(lambda: now[0])
    _, fake, _, _ = execute(section(), pacer=pacer, now=now)
    gaps = [b - a for a, b in zip(fake.times, fake.times[1:], strict=False)]
    assert gaps
    assert all(3.0 <= gap <= 5.0 for gap in gaps)


def test_the_source_stops_at_its_own_time_cap() -> None:
    assert vie.time_limit(0.0, 900.0, websites=0) == 180.0
    assert vie.time_limit(800.0, 900.0, websites=1) == 850.0  # half the time left
    titles = [f"Engineer {i}" for i in range(10)]
    entries, fake, _, _ = execute(section(title_include={"terms": titles}), deadline=50.0)
    assert entries[0]["error"] == "time_budget"
    assert len(fake.requests) < 10


# --- stops ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_refusal_ends_the_source_and_cools_the_host_for_every_sweep(status: int) -> None:
    now = [0.0]
    cooldown = linkedin_guest.MemoryCooldown(lambda: now[0])
    entries, fake, _, _ = execute(section(), lambda clock: VieFake(clock, answers={2: status}),
                                  now=now, cooldown=cooldown)  # fmt: skip
    assert entries[0]["error"] == "vie_rate_limited"
    assert len(fake.requests) == 2
    assert asyncio.run(cooldown.active(vie.API_HOST))
    # A second sweep within the cooldown sends nothing.
    again, fake2, _, _ = execute(section(), now=now, cooldown=cooldown)
    assert again[0]["error"] == "vie_rate_limited"
    assert fake2.requests == []
    # Once it ended, the host is read again.
    now[0] += vie.COOLDOWN_S + 1
    after, fake3, _, _ = execute(section(), now=now, cooldown=cooldown, deadline=now[0] + 1000)
    assert after[0]["error"] == "finding_persistence_pending"
    assert len(fake3.requests) == 3  # the key page and one page of each title


@pytest.mark.parametrize("status", [500, 302, 404])
def test_any_other_answer_ends_page_failed_without_cooldown(status: int) -> None:
    now = [0.0]
    cooldown = linkedin_guest.MemoryCooldown(lambda: now[0])
    entries, _, _, _ = execute(section(), lambda clock: VieFake(clock, answers={2: status}),
                               now=now, cooldown=cooldown)  # fmt: skip
    assert entries[0]["error"] == "page_failed"
    assert not asyncio.run(cooldown.active(vie.API_HOST))


def test_an_answer_that_is_no_search_answer_ends_page_failed() -> None:
    pages = {("genai engineer", 0): "<html>maintenance</html>"}
    entries, _, _, _ = execute(section(), lambda now: VieFake(now, pages))
    assert entries[0]["error"] == "page_failed"


def test_without_a_transport_no_request_is_made() -> None:
    entries, fake, _, _ = execute(section(), transport=False)
    assert entries[0]["error"] == "page_failed"
    assert fake.requests == []


def test_the_test_base_url_sends_everything_to_the_stub() -> None:
    fake = VieFake([0.0])
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return fake.handler(request)

    search = section()
    scope = sweep.freeze_scope(
        SimpleNamespace(sections={"search": search.model_dump(mode="json")}, revision=1),
        sweep.SweepTrigger.manual,
        NOW,
    )
    stats = sweep.Stats(sources=sweep.initial_sources(scope))
    plan = SimpleNamespace(search=search, user_id=uuid.uuid4(), search_scope=scope,
                           sweep_id=uuid.uuid4(), known=Known(), rules=None)  # fmt: skip
    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    async def progress() -> None:
        return None

    runner = sources.Runner(
        FakeLeases(), lambda *_: None, base_url="http://stub.test:8080", sleep=sleep,
        monotonic=lambda: clock[0], pacer=sources.MemoryPacer(lambda: clock[0]),
        guest_http=lambda: httpx.MockTransport(handler),
    )  # fmt: skip
    asyncio.run(runner.vie(plan, stats, progress, 1000.0))
    assert seen[0] == "http://stub.test:8080/vie/offres/recherche"
    assert all(url.startswith("http://stub.test:8080/vie/") for url in seen)


def test_an_abandoned_sweep_sends_no_further_request() -> None:
    search = section()
    plan = SimpleNamespace(search=search, user_id=uuid.uuid4(), search_scope=None,
                           sweep_id=uuid.uuid4(), known=Known(), rules=None,
                           commits=SimpleNamespace(unsettled=True,
                                                   admit=lambda _: None))  # fmt: skip

    async def settle(_: str) -> bool:
        return False

    plan.commits.settle = settle
    fake = VieFake([0.0])
    stats = sweep.Stats()

    async def progress() -> None:
        return None

    runner = sources.Runner(FakeLeases(), lambda *_: None,
                            guest_http=lambda: httpx.MockTransport(fake.handler))  # fmt: skip
    asyncio.run(runner.vie(plan, stats, progress, 1000.0))
    assert stats.sources[0]["error"] == "store_failed"
    assert fake.requests == []


def test_the_worker_runs_it_beside_the_other_api_sources() -> None:
    from app import worker

    runner = sources.Runner(FakeLeases(), lambda *_: None)
    assert worker.vie_source(runner) == runner.vie
    assert worker.vie_source(None) is None
    assert worker.vie_source(copy.copy) is None


def test_the_profile_stores_the_tick_only_once_on_and_keeps_it_when_omitted() -> None:
    from app.profile.service import _keep_consent

    assert "vie_businessfrance" not in _keep_consent(None, {"vie_businessfrance": False})
    assert _keep_consent(None, {"vie_businessfrance": True})["vie_businessfrance"] is True
    # An editor that does not know the key keeps the stored tick; sent off, it is removed.
    stored = {"vie_businessfrance": True}
    assert _keep_consent(stored, {})["vie_businessfrance"] is True
    assert "vie_businessfrance" not in _keep_consent(stored, {"vie_businessfrance": False})
