"""LinkedIn's public job search, the guest API (owner, 2026-10-30: "use api to search but keep
browser to apply", then "Both, switchable in setup"), app/browser/sources/linkedin_guest.py.

Recorded fixtures only: small hand made fragments in the shape LinkedIn answered on 2026-10-30
(a search page of cards, a posting page), invented companies and ids, no tracking token. Every
request goes to an httpx.MockTransport: no test reaches the network. The clock is a fake one,
so the pacing is measured, never waited."""

import asyncio
import itertools
import json
import random
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.browser import sources
from app.browser.sources import linkedin_guest as guest
from app.jobs import sweep
from app.jobs.models import ContractType
from app.profile.schemas import SearchSection
from tests.browser.fakes import FakeLeases, Known

LINKEDIN = {"platform": "linkedin", "url": "https://www.linkedin.com/", "enabled": True,
            "mode": "llm_browser"}  # fmt: skip
CUSTOM = {"platform": "custom", "url": "https://jobs.example.test/", "enabled": True,
          "mode": "llm_browser"}  # fmt: skip


def card(job_id: str, title: str, company: str = "Example Robotics", place: str = "Paris, France",
         day: str = "2026-10-29") -> str:  # fmt: skip
    """One card as LinkedIn's search fragment holds it, trimmed (no image, no tracking id)."""
    return f"""<li>
      <div class="base-card relative w-full base-card--link base-search-card job-search-card"
           data-entity-urn="urn:li:jobPosting:{job_id}" data-impression-id="jobs-search-result-0">
        <a class="base-card__full-link absolute" href="https://fr.linkedin.com/jobs/view/x-{job_id}?position=1&amp;pageNum=0">
          <span class="sr-only">{title}</span>
        </a>
        <div class="base-search-card__info">
          <h3 class="base-search-card__title">
            {title}
          </h3>
          <h4 class="base-search-card__subtitle">
            <a class="hidden-nested-link" href="https://fr.linkedin.com/company/example">{company}</a>
          </h4>
          <div class="base-search-card__metadata">
            <span class="job-search-card__location">
              {place}
            </span>
            <time class="job-search-card__listdate" datetime="{day}">1 day ago</time>
          </div>
        </div>
      </div>
    </li>"""


SEARCH_PAGE = "\n".join(
    [
        card("4100000001", "GenAI Engineer"),
        # Missing company, place and date: kept with what it has.
        """<li><div class="base-card base-search-card"
          data-entity-urn="urn:li:jobPosting:4100000002">
          <h3 class="base-search-card__title">Senior GenAI Engineer &amp; Agents</h3>
          <time datetime="not-a-date">soon</time></div></li>""",
        # No LinkedIn id: not a card.
        """<li><div class="base-card">
          <h3 class="base-search-card__title">Promoted</h3></div></li>""",
        card("4100000003", "Senior Algorithm Engineer", "Example Travel", "Singapore"),
    ]
)

POSTING = """<section class="top-card-layout container-lined overflow-hidden">
  <h2 class="top-card-layout__title font-sans text-lg topcard__title">GenAI Engineer</h2>
  <div class="topcard__flavor-row">
    <span class="topcard__flavor"><a class="topcard__org-name-link topcard__flavor--black-link" href="https://fr.linkedin.com/company/example">
      Example Robotics
    </a></span>
    <span class="topcard__flavor topcard__flavor--bullet">
      Paris, Île-de-France, France
    </span>
  </div>
  <div class="topcard__flavor-row">
    <span class="posted-time-ago__text topcard__flavor--metadata">1 day ago</span>
    <span class="num-applicants__caption topcard__flavor--metadata topcard__flavor--bullet">
      64 applicants
    </span>
  </div>
</section>
<div class="description__text description__text--rich">
  <section class="show-more-less-html" data-max-lines="5">
    <div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5 relative">
      <strong>About the role</strong><br><br>Build agents with Python &amp; evaluation.<br>
      <ul><li>Ship LLM features</li><li>Work with customers</li></ul>
    </div>
  </section>
  <ul class="description__job-criteria-list">
    <li class="description__job-criteria-item">
      <h3 class="description__job-criteria-subheader">Seniority level</h3>
      <span class="description__job-criteria-text description__job-criteria-text--criteria">
        Mid-Senior level
      </span>
    </li>
    <li class="description__job-criteria-item">
      <h3 class="description__job-criteria-subheader">Employment type</h3>
      <span class="description__job-criteria-text description__job-criteria-text--criteria">
          Internship
      </span>
      </li>
  </ul>
</div>"""


def section(**fields: Any) -> SearchSection:
    data: dict[str, Any] = {
        "title_include": {"terms": ["GenAI Engineer"]},
        "interactive_sites": [LINKEDIN],
    }
    data.update(fields)
    return SearchSection.model_validate(data)


# --- the searches --------------------------------------------------------------------------------


def params(query: guest.Query, days: int = 30, start: int = 0) -> dict[str, list[str]]:
    url = guest.search_url(guest.BASE_URL, query, days, start)
    assert url.startswith("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?")
    return parse_qs(urlsplit(url).query)


@pytest.mark.parametrize("places", [[], ["everywhere"], ["Worldwide"], ["Partout"]])
def test_no_place_or_a_place_that_says_no_limit_searches_worldwide(places: list[str]) -> None:
    [query] = guest.queries_for(section(location={"terms": places}))
    assert params(query) == {
        "keywords": ["GenAI Engineer"],
        "location": ["Worldwide"],
        "geoId": ["92000000"],
        "f_TPR": ["r2592000"],
        "start": ["0"],
    }
    assert query.place == (places[0] if places else None)


def test_each_specific_place_is_one_search_as_written_without_geo_id() -> None:
    queries = guest.queries_for(
        section(
            title_include={"terms": ["GenAI Engineer", "AI Engineer"]},
            location={"terms": ["Paris", "Île-de-France", "anywhere"]},
        )
    )
    assert [(q.keywords, q.location, q.geo_id) for q in queries] == [
        ("GenAI Engineer", "Worldwide", "92000000"),
        ("GenAI Engineer", "Paris", None),
        ("GenAI Engineer", "Île-de-France", None),
        ("AI Engineer", "Worldwide", "92000000"),
        ("AI Engineer", "Paris", None),
        ("AI Engineer", "Île-de-France", None),
    ]
    assert "geoId" not in params(queries[1])
    assert params(queries[1])["location"] == ["Paris"]


@pytest.mark.parametrize(("days", "seconds"), [(1, "r86400"), (7, "r604800"), (365, "r31536000")])
def test_the_saved_days_limit_the_age(days: int, seconds: str) -> None:
    [query] = guest.queries_for(section(days=days))
    assert params(query, days)["f_TPR"] == [seconds]
    assert params(query, days, 20)["start"] == ["20"]


@pytest.mark.parametrize(
    ("contracts", "expected"),
    [
        ([], [("GenAI Engineer", None, False)]),
        (["CDI"], [("GenAI Engineer", "F", False)]),
        (["CDD"], [("GenAI Engineer", "T", False)]),
        (["Freelance"], [("GenAI Engineer", "C", False)]),
        (["Stage"], [("GenAI Engineer", "I", False)]),
        (["CDI", "CDD", "Freelance", "Stage"], [("GenAI Engineer", "C,F,I,T", False)]),
        # Alternance has no code: no filter, the postings read are checked.
        (["CDI", "Alternance"], [("GenAI Engineer", None, True)]),
        # VIE has no code: an extra keyword search, checked.
        (["CDI", "VIE"], [("GenAI Engineer", "F", False), ("GenAI Engineer VIE", None, True)]),
        (["VIE"], [("GenAI Engineer VIE", None, True)]),
    ],
)
def test_contract_types_become_linkedin_filters_or_checks(
    contracts: list[str], expected: list[tuple[str, str | None, bool]]
) -> None:
    queries = guest.queries_for(section(contract_types=contracts))
    assert [(q.keywords, q.job_types, q.check_contract) for q in queries] == expected
    assert all(q.title == "GenAI Engineer" for q in queries)
    for query in queries:
        assert params(query).get("f_JT") == ([query.job_types] if query.job_types else None)


def test_no_title_to_type_is_unsupported_and_saved_patterns_give_their_words() -> None:
    with pytest.raises(ValueError, match="unsupported_criteria"):
        guest.queries_for(section(title_include={"terms": []}))
    queries = guest.queries_for(
        section(title_include={"terms": ["gen ?ai engineer"], "advanced_regex": True})
    )
    assert [q.keywords for q in queries] == ["gen ai engineer", "genai engineer"]


def test_searches_are_capped() -> None:
    titles = [f"Engineer {n}" for n in range(20)]
    places = [f"City {n}" for n in range(5)]
    queries = guest.queries_for(
        section(title_include={"terms": titles}, location={"terms": places})
    )
    assert len(queries) == guest.SEARCHES_MAX


# --- the HTML ------------------------------------------------------------------------------------


def test_cards_are_parsed_with_missing_fields_and_without_non_cards() -> None:
    cards = guest.parse_cards(SEARCH_PAGE)
    assert cards == [
        guest.Card(
            "4100000001", "GenAI Engineer", "Example Robotics", "Paris, France", "2026-10-29"
        ),
        guest.Card("4100000002", "Senior GenAI Engineer & Agents", "", "", ""),
        guest.Card(
            "4100000003", "Senior Algorithm Engineer", "Example Travel", "Singapore", "2026-10-29"
        ),
    ]


def test_cards_are_bounded_once_each_and_an_empty_or_odd_page_gives_none() -> None:
    many = "".join(card(str(4100000100 + n), f"GenAI Engineer {n}") for n in range(40))
    assert len(guest.parse_cards(many)) == guest.CARDS_MAX
    twice = card("4100000001", "GenAI Engineer") * 2
    assert len(guest.parse_cards(twice)) == 1
    assert guest.parse_cards("") == []
    assert guest.parse_cards("<li><div>Something else</div></li>") == []
    assert guest.looks_like_cards("<li><div>Something else</div></li>")
    assert not guest.looks_like_cards("")
    # A title too long or made of control characters only is no card.
    assert guest.parse_cards(card("4100000009", "x" * 400)) == []
    assert guest.parse_cards(card("4100000009", "​")) == []
    # An invalid calendar date is no date.
    assert (
        guest.parse_cards(card("4100000009", "GenAI Engineer", day="2026-02-30"))[0].published == ""
    )


def test_the_posting_gives_its_description_and_criteria() -> None:
    detail = guest.parse_posting(POSTING)
    assert detail is not None
    assert detail.description == (
        "About the role\n\nBuild agents with Python & evaluation.\n\nShip LLM features\n"
        "Work with customers"
    )
    assert detail.criteria == {
        "Seniority level": "Mid-Senior level",
        "Employment type": "Internship",
    }
    assert (detail.title, detail.company, detail.location) == (
        "GenAI Engineer",
        "Example Robotics",
        "Paris, Île-de-France, France",
    )
    assert detail.text().endswith("Seniority level: Mid-Senior level\nEmployment type: Internship")
    assert guest.parse_posting("<div class='top-card-layout__title'>No description</div>") is None
    assert guest.parse_posting("") is None


@pytest.mark.parametrize(
    ("title", "criteria", "expected"),
    [
        ("GenAI Engineer", {"Employment type": "Internship"}, ContractType.Stage),
        ("GenAI Engineer", {"Employment type": "Temporary"}, ContractType.CDD),
        ("GenAI Engineer", {"Employment type": "Contract"}, ContractType.Freelance),
        ("GenAI Engineer", {"Employment type": "Full-time"}, ContractType.CDI),
        ("GenAI Engineer", {}, ContractType.CDI),
        # The title wins when it states one.
        ("GenAI Engineer VIE", {"Employment type": "Full-time"}, ContractType.VIE),
        ("Stage GenAI Engineer", {"Employment type": "Contract"}, ContractType.Stage),
    ],
)
def test_the_contract_type_reads_the_title_then_the_employment_type(
    title: str, criteria: dict[str, str], expected: ContractType
) -> None:
    assert (
        guest.contract_of(title, "Paris", "https://www.linkedin.com/jobs/view/1/", criteria)
        is expected
    )


# --- the source, against a recorded LinkedIn -----------------------------------------------------


class Allow:
    """The platform policy as linkedin_guard answers a ticked, accepted LinkedIn; `refuse` from
    the n-th check on."""

    def __init__(self, refuse_from: int | None = None, code: str = "permission_revoked") -> None:
        self.checks, self.refuse_from, self.code = 0, refuse_from, code

    async def check(self, context: Any) -> str | None:
        self.checks += 1
        if self.refuse_from is not None and self.checks >= self.refuse_from:
            return self.code
        return None

    async def reserve(self, context: Any) -> str | None:
        return None

    async def observe(self, context: Any, url: str, snapshot: str) -> str | None:
        return None


class LinkedInFake:
    """Answers by path: search pages from `pages` (keyed by start), postings from `postings`;
    `answers` overrides by request number (1 based) with a status (and headers)."""

    def __init__(self, now: list[float], pages: dict[int, str] | None = None,
                 postings: dict[str, str] | None = None,
                 answers: dict[int, tuple[int, dict[str, str]]] | None = None) -> None:  # fmt: skip
        self.now = now
        self.pages = {0: SEARCH_PAGE} if pages is None else pages
        self.postings = postings
        self.answers = answers or {}
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.times.append(self.now[0])
        number = len(self.requests)
        if number in self.answers:
            status, headers = self.answers[number]
            return httpx.Response(status, headers=headers, text="")
        path = request.url.path
        if path.endswith("/seeMoreJobPostings/search"):
            start = int(request.url.params["start"])
            body = self.pages.get(start)
            if body is None:
                return httpx.Response(400, text="")
            return httpx.Response(200, text=body, headers={"set-cookie": "lang=v=2&lang=en-us"})
        job_id = path.rsplit("/", 1)[1]
        if self.postings is not None and job_id not in self.postings:
            return httpx.Response(404, text="")
        text = (self.postings or {}).get(job_id, POSTING)
        return httpx.Response(200, text=text)

    def paths(self) -> list[str]:
        return [
            r.url.path.rsplit("/", 1)[1]
            + (f"?start={r.url.params['start']}" if "start" in r.url.params else "")
            for r in self.requests
        ]


def execute(
    search: SearchSection,
    fake_of: Any = None,
    *,
    policy: Any = None,
    known: Any = None,
    deadline: float = 1000.0,
    base_url: str | None = None,
    transport: bool = True,
    pacer: Any = None,
    now: list[float] | None = None,
    rules: Any = None,
) -> tuple[list[dict[str, Any]], Any, FakeLeases, list[float]]:
    """One sweep's browser hook (sources.Runner) with this search; the LinkedIn fake answers."""
    now = now if now is not None else [0.0]
    fake = (fake_of or (lambda clock: LinkedInFake(clock)))(now)
    leases = FakeLeases()

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    async def progress() -> None:
        json.dumps(stats.as_json())  # what a progress write stores is JSON

    scope = sweep.freeze_scope(
        SimpleNamespace(sections={"search": search.model_dump(mode="json")}, revision=3),
        sweep.SweepTrigger.manual,
        datetime.fromisoformat("2026-10-30T00:00:00+00:00"),
    )
    stats = sweep.Stats(sources=sweep.initial_sources(scope))
    plan = SimpleNamespace(
        search=search,
        user_id=uuid.uuid4(),
        search_scope=scope,
        sweep_id=uuid.uuid4(),
        known=known or Known(),
        rules=rules,
    )
    runner = sources.Runner(
        leases,
        lambda *_: None,
        sleep=sleep,
        monotonic=lambda: now[0],
        rng=random.Random(7),  # noqa: S311  deterministic test pacing
        pacer=pacer or sources.MemoryPacer(lambda: now[0]),
        interactive_policy=Allow() if policy is None else policy,
        base_url=base_url,
        guest_http=(lambda: httpx.MockTransport(fake.handler)) if transport else None,
    )
    asyncio.run(runner(plan, stats, progress, deadline))
    return stats.sources, fake, leases, now


def test_linkedin_ticked_is_searched_through_the_api_without_the_browser() -> None:
    entries, fake, leases, _ = execute(section())
    [entry] = entries
    assert leases.acquired == []  # no lease of the person's browser
    assert fake.paths() == ["search?start=0", "4100000001", "4100000002"]
    assert entry == {
        "source_id": sweep._source_id("api", LINKEDIN["url"]),
        "mode": "api",
        "host": "www.linkedin.com",
        "kind": "linkedin",
        "phase": "done",
        "outcome": "partial",
        "reason": "finding_persistence_pending",
        "result_pages": 1,
        "found": 2,
        "postings": 0,
        "error": "finding_persistence_pending",
        "details": 2,
        "searches": 1,
        "searched": [{"title": "GenAI Engineer", "place": None}],
        "unwanted": 1,  # "Senior Algorithm Engineer" matches no saved title
        "requests": 3,
    }
    request = fake.requests[0]
    assert request.headers["user-agent"].startswith("Mozilla/5.0")
    assert "cookie" not in request.headers


def test_no_cookie_is_ever_sent_back_and_no_redirect_is_followed() -> None:
    _, fake, _, _ = execute(section())
    assert len(fake.requests) == 3
    assert all("cookie" not in r.headers for r in fake.requests)


def test_requests_are_paced_at_least_eight_seconds_apart_after_any_page_of_the_host() -> None:
    now = [100.0]
    pacer = sources.MemoryPacer(lambda: now[0])
    # A browser page of LinkedIn (another person's Easy Apply) just ended.
    asyncio.run(pacer.release("www.linkedin.com", "browser-page"))
    _, fake, _, _ = execute(section(), pacer=pacer, now=now)
    times = [100.0, *fake.times]
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert len(gaps) == 3
    assert all(gap >= guest.PAUSE_S[0] for gap in gaps), gaps
    assert all(gap <= guest.PAUSE_S[1] for gap in gaps), gaps


def test_a_request_waits_while_another_holder_has_the_host() -> None:
    now = [0.0]
    pacer = sources.MemoryPacer(lambda: now[0])
    asyncio.run(pacer.reserve("www.linkedin.com", 0.0, "another-worker"))

    class Releasing(sources.MemoryPacer):
        async def reserve(self, host: str, pause: float, holder: str) -> float | None:
            if now[0] >= 30.0:  # the other page ends at 30 s
                self.holders.pop(host, None)
            return await super().reserve(host, pause, holder)

    releasing = Releasing(lambda: now[0])
    releasing.holders, releasing.last = dict(pacer.holders), {"www.linkedin.com": 0.0}
    _, fake, _, _ = execute(section(), pacer=releasing, now=now)
    assert fake.times[0] >= 30.0


@pytest.mark.parametrize(
    ("answers", "requests", "found"),
    [
        ({1: (429, {})}, 1, 0),
        ({1: (999, {})}, 1, 0),
        ({1: (302, {"location": "https://www.linkedin.com/authwall?trk=x"})}, 1, 0),
        ({1: (303, {"location": "https://www.linkedin.com/uas/login"})}, 1, 0),
        ({3: (429, {})}, 3, 1),  # the second posting: the first is kept
    ],
)
def test_a_refusal_stops_linkedin_at_once_without_retry(
    answers: dict[int, tuple[int, dict[str, str]]], requests: int, found: int
) -> None:
    entries, fake, _, _ = execute(section(), lambda now: LinkedInFake(now, answers=answers))
    [entry] = entries
    assert len(fake.requests) == requests
    assert entry["error"] == "rate_limited"
    assert entry["reason"] == "rate_limited"
    assert entry["found"] == found
    assert entry["outcome"] == ("partial" if found else "failed")


@pytest.mark.parametrize(
    ("answers", "code"),
    [
        ({1: (500, {})}, "page_failed"),
        ({1: (302, {"location": "https://www.linkedin.com/jobs/"})}, "page_failed"),
        ({1: (403, {})}, "page_failed"),
    ],
)
def test_another_answer_ends_the_source_page_failed(
    answers: dict[int, tuple[int, dict[str, str]]], code: str
) -> None:
    entries, fake, _, _ = execute(section(), lambda now: LinkedInFake(now, answers=answers))
    assert entries[0]["error"] == code
    assert len(fake.requests) == 1


def test_a_network_failure_and_an_oversized_answer_end_page_failed() -> None:
    def broken(now: list[float]) -> LinkedInFake:
        fake = LinkedInFake(now)

        def handler(request: httpx.Request) -> httpx.Response:
            fake.requests.append(request)
            raise httpx.ConnectTimeout("timed out", request=request)

        fake.handler = handler  # type: ignore[method-assign]
        return fake

    entries, fake, _, _ = execute(section(), broken)
    assert (entries[0]["error"], len(fake.requests)) == ("page_failed", 1)
    huge = "<li>" + "x" * (guest.BODY_MAX + 10)
    entries, _, _, _ = execute(section(), lambda now: LinkedInFake(now, pages={0: huge}))
    assert entries[0]["error"] == "page_failed"


def test_a_posting_gone_is_skipped_and_a_changed_layout_is_said() -> None:
    entries, fake, _, _ = execute(
        section(), lambda now: LinkedInFake(now, postings={"4100000002": POSTING})
    )
    assert entries[0]["found"] == 1
    assert entries[0]["error"] == "finding_persistence_pending"
    assert fake.paths() == ["search?start=0", "4100000001", "4100000002"]
    odd = "<ul><li><div class='job-card-new'>GenAI Engineer</div></li></ul>"
    entries, _, _, _ = execute(section(), lambda now: LinkedInFake(now, pages={0: odd}))
    assert entries[0]["error"] == "layout_changed"
    entries, _, _, _ = execute(section(), lambda now: LinkedInFake(now, pages={0: ""}))
    assert (entries[0]["error"], entries[0]["outcome"]) == (None, "no_matches")


def full_page(first: int, title: str = "GenAI Engineer") -> str:
    return "".join(card(str(first + n), f"{title} {n}") for n in range(guest.PAGE_SIZE))


def test_pages_per_search_and_requests_per_sweep_are_capped() -> None:
    pages = {start: full_page(4100001000 + start) for start in range(0, 100, 10)}
    search = section(title_include={"terms": ["GenAI Engineer", "AI Engineer", "ML Engineer"]})
    entries, fake, _, _ = execute(search, lambda now: LinkedInFake(now, pages=pages))
    [entry] = entries
    assert len(fake.requests) == guest.REQUESTS_MAX == entry["requests"]
    starts = [r.url.params["start"] for r in fake.requests if "start" in r.url.params]
    assert max(int(s) for s in starts) <= (guest.PAGES_PER_SEARCH - 1) * guest.PAGE_SIZE
    # Rounds: the first page of every search before any second page.
    assert starts[:3] == ["0", "0", "0"]
    assert entry["error"] is None or entry["error"] == "finding_persistence_pending"


def test_a_search_stops_at_a_short_page_and_never_asks_more_than_three_pages() -> None:
    pages = {start: full_page(4100002000 + start) for start in range(0, 100, 10)}
    _, fake, _, _ = execute(section(), lambda now: LinkedInFake(now, pages=pages))
    starts = [r.url.params["start"] for r in fake.requests if "start" in r.url.params]
    assert starts == ["0", "10", "20"]
    _, fake, _, _ = execute(section())  # SEARCH_PAGE holds 3 cards: one page only
    assert [r for r in fake.paths() if r.startswith("search")] == ["search?start=0"]


def test_unwanted_titles_places_never_apply_and_known_postings_are_never_fetched() -> None:
    page = "".join(
        [
            card("4100000011", "GenAI Engineer"),
            card("4100000012", "GenAI Engineer Sales"),  # an unwanted title
            card("4100000013", "GenAI Engineer", place="Berlin, Germany"),  # an unwanted place
            card("4100000014", "GenAI Security Engineer"),  # the app's rule against cybersecurity
            card("4100000015", "GenAI Engineer", company="Example Casino"),  # a never apply rule
            card("4100000016", "GenAI Engineer, Agents"),  # the person has it already
            card("4100000017", "Data Engineer"),  # no saved title
        ]
    )
    search = section(
        title_exclude={"terms": ["sales"]},
        location_exclude={"terms": ["Germany"]},
        never_apply=["No gambling: casino (test rule)"],
    )
    known = Known({"https://fr.linkedin.com/jobs/view/genai-engineer-agents-at-example-4100000016"})
    entries, fake, _, _ = execute(
        search,
        lambda now: LinkedInFake(now, pages={0: page}),
        known=known,
        rules=sweep.never_apply_rules(search),
    )
    [entry] = entries
    assert fake.paths() == ["search?start=0", "4100000011"]
    assert entry["unwanted"] == 5
    assert entry["found"] == 2  # the new one and the duplicate
    assert entry["duplicates"] == 1


def test_a_posting_of_another_contract_is_dropped_only_when_linkedin_could_not_filter() -> None:
    entries, _, _, _ = execute(section(contract_types=["CDI", "Alternance"]))
    assert entries[0]["other_contract"] == 2  # both postings are internships
    assert entries[0]["found"] == 0
    entries, _, _, _ = execute(section(contract_types=["CDI"]))  # f_JT=F filtered natively
    assert "other_contract" not in entries[0]
    assert entries[0]["found"] == 2


def test_the_permission_is_checked_before_every_request() -> None:
    policy = Allow()
    _, fake, _, _ = execute(section(), policy=policy)
    assert policy.checks == 1 + 2 * len(fake.requests)  # once first, then twice a request
    refused = Allow(refuse_from=1, code="permission_unavailable")
    entries, fake, _, _ = execute(section(), policy=refused)
    assert (entries[0]["error"], fake.requests) == ("permission_unavailable", [])
    revoked = Allow(refuse_from=4)
    entries, fake, _, _ = execute(section(), policy=revoked)
    assert entries[0]["error"] == "permission_revoked"
    assert len(fake.requests) == 1


def test_no_transport_or_no_time_makes_no_request() -> None:
    entries, fake, _, _ = execute(section(), transport=False)
    assert (entries[0]["error"], fake.requests) == ("page_failed", [])
    entries, fake, _, _ = execute(section(), deadline=guest.REQUEST_S)
    assert (entries[0]["error"], fake.requests) == ("time_budget", [])


def test_the_fixture_server_replaces_linkedin_in_tests() -> None:
    entries, fake, _, _ = execute(section(), base_url="http://pages:8080/b8")
    assert {r.url.host for r in fake.requests} == {"pages"}
    assert fake.requests[0].url.path == "/b8/linkedin/jobs-guest/jobs/api/seeMoreJobPostings/search"
    assert entries[0]["host"] == "pages"


def test_browser_mode_keeps_the_finder_agent_and_api_mode_leaves_other_websites_to_it() -> None:
    # Browser: the website's agent path (no model credential here, so `no_credential`).
    entries, fake, _, _ = execute(section(linkedin_search="browser"))
    [entry] = entries
    assert (entry["mode"], entry["error"], fake.requests) == ("llm_browser", "no_credential", [])
    assert entry["source_id"] == sweep._source_id("llm_browser", LINKEDIN["url"])
    # API with another website: LinkedIn through the API, the other website in the browser.
    entries, fake, _, _ = execute(section(interactive_sites=[CUSTOM, LINKEDIN]))
    by_mode = {e["mode"]: e for e in entries}
    assert by_mode["api"]["kind"] == "linkedin"
    assert by_mode["api"]["found"] == 2
    assert by_mode["llm_browser"]["host"] == "jobs.example.test"
    assert by_mode["llm_browser"]["error"] == "no_credential"


def test_a_second_linkedin_address_is_not_searched_twice() -> None:
    other = {**LINKEDIN, "platform": "custom", "url": "https://www.linkedin.com/jobs/search/"}
    entries, fake, _, _ = execute(section(interactive_sites=[LINKEDIN, other]))
    assert [e["error"] for e in entries] == [
        "finding_persistence_pending",
        "linkedin_searched_once",
    ]
    assert len([r for r in fake.paths() if r.startswith("search")]) == 1


def test_the_frozen_scope_names_the_mode_and_wins_over_a_later_setting() -> None:
    def frozen(search: SearchSection) -> list[dict[str, Any]]:
        return sweep.freeze_scope(
            SimpleNamespace(sections={"search": search.model_dump(mode="json")}, revision=1),
            sweep.SweepTrigger.manual,
            datetime.fromisoformat("2026-10-30T00:00:00+00:00"),
        )["sources"]

    [api] = frozen(section())
    [browser] = frozen(section(linkedin_search="browser"))
    assert (api["mode"], api["source_id"]) == ("api", sweep._source_id("api", LINKEDIN["url"]))
    assert (browser["mode"], browser["source_id"]) == (
        "llm_browser",
        sweep._source_id("llm_browser", LINKEDIN["url"]),
    )
    [custom] = frozen(section(interactive_sites=[CUSTOM]))
    assert custom["mode"] == "llm_browser"
    # A sweep frozen in browser mode runs in browser mode, whatever the setting says now.
    site = section().interactive_sites[0]
    plan = SimpleNamespace(search=section(), search_scope={"sources": [browser]})
    assert guest.by_api(plan, site) is False
    plan = SimpleNamespace(search=section(linkedin_search="browser"), search_scope=None)
    assert guest.by_api(plan, site) is False
    plan = SimpleNamespace(search=section(), search_scope=None)
    assert guest.by_api(plan, site) is True


def test_the_setting_defaults_to_api_and_takes_only_its_two_values() -> None:
    assert SearchSection.model_validate({}).linkedin_search == "api"
    assert section(linkedin_search="browser").linkedin_search == "browser"
    with pytest.raises(ValueError, match="linkedin_search"):
        section(linkedin_search="headless")
    assert sweep.linkedin_mode(None) == "api"
