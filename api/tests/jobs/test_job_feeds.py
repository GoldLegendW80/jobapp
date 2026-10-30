"""The job feeds (app/jobs/feeds): Arbeitnow, Himalayas and Adzuna read from small hand-made JSON
fixtures through a fake transport, never the network. What each maps, what the person's
criteria leave out, paging and the cursor, a 429, a missing or refused key, the politeness of
the requests and what is logged. The sweep side (storing, duplicates, stats, settings and the
key routes) is in test_job_feeds_sweep.py."""

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.jobs import feeds
from app.jobs.feeds import Criteria, FeedContext, FeedRunner, Http
from app.jobs.feeds.arbeitnow import Arbeitnow, contract_of
from app.jobs.models import ContractType
from app.profile.schemas import SearchSection

FIXTURES = Path(__file__).parent / "feed_fixtures"
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)  # the fixtures' "now"
APP_ID = "ab" * 4
APP_KEY = "z" * 30 + "42"  # a made-up key of Adzuna's shape


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def stamp(**ago: float) -> int:
    return int((NOW - timedelta(**ago)).timestamp())


Handler = Callable[[httpx.Request], httpx.Response]


class Transport(httpx.AsyncBaseTransport):
    """A transport answering from `handler` with an unread stream, as the network does (httpx's
    MockTransport reads the body first, which the size cap would never see)."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self.handler(request)


class Api:
    """A fake of the three APIs: answers by path (and page), records every request."""

    def __init__(self, answers: dict[tuple[str, str], Any] | None = None) -> None:
        self.answers = answers or {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        page = request.url.params.get("page", "")
        if request.url.host == "api.adzuna.com":
            page = request.url.path.rsplit("/", 1)[-1]
        answer = self.answers.get(
            (request.url.host, page), self.answers.get((request.url.host, ""))
        )
        if answer is None:
            return httpx.Response(404, json={})
        if isinstance(answer, int):
            return httpx.Response(answer, json={})
        # A stream, as the network gives it: the size cap reads it chunk by chunk.
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(answer).encode()))

    @property
    def transport(self) -> Transport:
        return Transport(self.handler)


def arbeitnow_api(page2: Any = None) -> Api:
    return Api(
        {
            ("www.arbeitnow.com", "1"): fixture("arbeitnow_page1"),
            ("www.arbeitnow.com", "2"): page2 if page2 is not None else fixture("arbeitnow_page2"),
        }
    )


def search(**overrides: Any) -> SearchSection:
    data: dict[str, Any] = {
        "title_include": {"terms": ["forward deployed", "ai engineer"], "advanced_regex": False},
        "title_exclude": {"terms": ["sales"], "advanced_regex": False},
        "location": {"terms": ["paris", "lyon"], "advanced_regex": False},
        "contract_types": [],
        "days": 30,
        "job_feeds": ["arbeitnow", "himalayas", "adzuna"],
    }
    data.update(overrides)
    return SearchSection.model_validate(data)


class Commits:
    """A fake of the run's commits: what was saved, per source."""

    def __init__(self, stats: Any) -> None:
        self.stats = stats
        self.saved: dict[str, list[Any]] = {}
        self.admitted: list[str] = []

    def admit(self, source_id: str) -> None:
        self.admitted.append(source_id)

    async def save(self, source_id: str, source: str, batch: Any) -> None:
        assert source_id in self.admitted
        self.saved.setdefault(source, []).extend(batch.findings)
        for entry in self.stats.sources:  # as sweep._counted does
            if entry["source_id"] == source_id:
                entry["found"] += len(batch.findings)

    async def settle(self) -> bool:
        return True


def run(
    api: Api,
    section: SearchSection,
    keys: dict[str, dict[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], Commits, list[float]]:
    """One FeedRunner over the ticked feeds: the stats entries, the commits and the pauses."""
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    stats = SimpleNamespace(sources=[], current_board=None)
    commits = Commits(stats)
    plan = SimpleNamespace(
        search=section, user_id=uuid.uuid4(), sweep_id=uuid.uuid4(), commits=commits
    )

    async def progress() -> None:
        return None

    runner = FeedRunner(
        None,
        keys=lambda user, feed: (keys or {}).get(feed),
        transport=api.transport,
        sleep=sleep,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    asyncio.run(runner(plan, stats, progress, 900.0))
    return stats.sources, commits, pauses


def entry_of(entries: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    [found] = [e for e in entries if e["kind"] == kind]
    return found


# --- Arbeitnow ------------------------------------------------------------------------------------


def test_arbeitnow_maps_filters_and_pages_back_to_the_window() -> None:
    api = arbeitnow_api()
    entries, commits, pauses = run(api, search(job_feeds=["arbeitnow"]))
    entry = entry_of(entries, "arbeitnow")
    kept = {f.external_id: f for f in commits.saved["arbeitnow"]}
    # Kept: a wanted title in a wanted place, the security role (the store marks it skipped,
    # test_job_feeds_sweep), and page 2's Lyon posting; page 2's 40 day old one ends the paging.
    assert set(kept) == {
        "forward-deployed-engineer-paris-1001",
        "security-ai-engineer-paris-1005",
        "senior-ai-engineer-lyon-2001",
    }
    first = kept["forward-deployed-engineer-paris-1001"]
    assert first.title == "Forward Deployed Engineer"
    assert first.company == "Example Robotics"
    assert first.location == "Paris, France"
    assert first.url == (
        "https://www.arbeitnow.com/jobs/companies/example-robotics/"
        "forward-deployed-engineer-paris-1001"
    )
    assert first.contract is ContractType.CDI
    assert first.published_on == "2026-10-30"
    assert first.text == "Forward Deployed Engineer at Example Robotics.\nPython\nLLM agents"
    assert first.evidence == {"api": "arbeitnow", "external_id": first.external_id}
    assert kept["senior-ai-engineer-lyon-2001"].contract is ContractType.Stage
    # A title not wanted, an unwanted one, two places not wanted (Berlin, and a remote posting
    # with no place); the broken and the non https postings never map.
    assert entry["left_out"] == {"title": 1, "unwanted": 1, "place": 2}
    assert entry["read"] == 7
    assert entry["mode"] == "api"
    assert entry["host"] == "www.arbeitnow.com"
    assert entry["result_pages"] == 2
    assert entry["error"] is None
    assert entry["outcome"] == "complete"
    assert entry["phase"] == "done"
    assert entry["cursor"] == stamp(hours=1)  # the newest posting read
    # Polite: two requests, one pause between them, the app named.
    assert [r.url.params["page"] for r in api.requests] == ["1", "2"]
    assert pauses == [feeds.PAUSE_S]
    assert all(r.headers["User-Agent"].startswith("jobapp/") for r in api.requests)


def test_arbeitnow_everywhere_means_no_place_filter_and_contracts_filter() -> None:
    entries, commits, _ = run(
        arbeitnow_api(),
        search(
            job_feeds=["arbeitnow"],
            location={"terms": ["Partout"], "advanced_regex": False},
            contract_types=["CDI"],
        ),
    )
    kept = {f.external_id for f in commits.saved["arbeitnow"]}
    assert kept == {
        "forward-deployed-engineer-paris-1001",
        "ai-engineer-berlin-1004",  # "Unbefristeter arbeitsvertrag": CDI
        "security-ai-engineer-paris-1005",  # no stated type: the title's, CDI
        "ai-engineer-remote-1006",
    }
    assert entry_of(entries, "arbeitnow")["left_out"] == {
        "title": 1,
        "unwanted": 1,
        "contract": 1,  # the internship
    }
    remote = next(f for f in commits.saved["arbeitnow"] if f.external_id.endswith("1006"))
    assert remote.location == "Remote"
    assert remote.work_mode == "remote"


def test_arbeitnow_stops_at_the_previous_cursor() -> None:
    api = arbeitnow_api()
    criteria = Criteria.of(search())
    ctx = FeedContext(Http(api.transport, budget=10, sleep=_no_sleep), NOW, cursor=stamp(hours=3))
    fetched = asyncio.run(Arbeitnow().fetch(ctx, criteria))
    # The cursor less an hour of overlap: postings of the last four hours, one page.
    assert [f.external_id for f in fetched.findings][:4] == [
        "forward-deployed-engineer-paris-1001",
        "account-executive-paris-1002",
        "ai-engineer-sales-tools-paris-1003",
        "ai-engineer-berlin-1004",
    ]
    assert len(fetched.findings) == 4
    assert fetched.requests == 1
    assert fetched.cursor == stamp(hours=1)


def test_a_429_stops_the_feed_keeps_what_was_read_and_keeps_the_cursor() -> None:
    api = arbeitnow_api(page2=429)
    entries, commits, _ = run(api, search(job_feeds=["arbeitnow"]))
    entry = entry_of(entries, "arbeitnow")
    assert entry["error"] == "rate_limited"
    assert entry["reason"] == "rate_limited"
    assert entry["outcome"] == "partial"
    assert len(commits.saved["arbeitnow"]) == 2  # page 1's
    assert "cursor" not in entry  # the next sweep reads the same pages again
    assert len(api.requests) == 2


def test_arbeitnow_budget_is_its_page_cap() -> None:
    endless = fixture("arbeitnow_page1")  # always newer than the window, always a next page
    api = Api({("www.arbeitnow.com", ""): endless})
    _, _, pauses = run(api, search(job_feeds=["arbeitnow"]))
    assert len(api.requests) == Arbeitnow.budget == 10
    assert len(pauses) == 9


@pytest.mark.parametrize(
    ("types", "contract"),
    [
        (["Unbefristeter arbeitsvertrag"], ContractType.CDI),
        (["Befristeter Vertrag"], ContractType.CDD),
        (["Praktikum"], ContractType.Stage),
        (["Werkstudent"], ContractType.Alternance),
        (["Freelance"], ContractType.Freelance),
        (["Full Time"], None),
        ("Full Time", None),
    ],
)
def test_arbeitnow_contract_words(types: Any, contract: ContractType | None) -> None:
    assert contract_of(types) is contract


# --- Himalayas ------------------------------------------------------------------------------------


def test_himalayas_searches_saved_titles_in_france_and_keeps_its_links() -> None:
    api = Api({("himalayas.app", ""): fixture("himalayas")})
    entries, commits, pauses = run(
        api,
        search(
            job_feeds=["himalayas"],
            title_include={"terms": ["genai", "ai engineer"], "advanced_regex": False},
            location={"terms": ["bordeaux"], "advanced_regex": False},
        ),
    )
    entry = entry_of(entries, "himalayas")
    assert [r.url.params["q"] for r in api.requests] == ["genai", "ai engineer"]
    assert {r.url.params["country"] for r in api.requests} == {"FR"}
    assert {r.url.params["sort"] for r in api.requests} == {"recent"}
    assert pauses == [feeds.PAUSE_S]
    assert entry["searches"] == 2
    assert entry["searched"] == [
        {"title": "genai", "place": None},
        {"title": "ai engineer", "place": None},
    ]
    kept = {f.title: f for f in commits.saved["himalayas"]}
    # Both answers hold the same postings: each is kept once. A saved city does not leave out a
    # remote job open to France; a title not wanted and a non https link do.
    assert set(kept) == {"GenAI Engineer", "AI Engineer"}
    genai = kept["GenAI Engineer"]
    assert (
        genai.url
        == "https://himalayas.app/companies/example-cloud/jobs/genai-engineer?utm_source=api"
    )
    assert genai.location == "Remote, France, Spain"
    assert genai.work_mode == "remote"
    assert genai.text == "GenAI Engineer\nRemote team, Python."
    assert kept["AI Engineer"].contract is ContractType.Freelance
    assert entry["left_out"] == {"title": 1}
    assert entry["outcome"] == "complete"


def test_himalayas_unwanted_places_still_apply() -> None:
    api = Api({("himalayas.app", ""): fixture("himalayas")})
    _, commits, _ = run(
        api,
        search(
            job_feeds=["himalayas"],
            title_include={"terms": ["genai"], "advanced_regex": False},
            location_exclude={"terms": ["spain"], "advanced_regex": False},
        ),
    )
    assert "himalayas" not in commits.saved


# --- Adzuna ---------------------------------------------------------------------------------------


def test_adzuna_without_a_key_is_skipped_without_a_request() -> None:
    api = Api({("api.adzuna.com", ""): fixture("adzuna")})
    entries, commits, _ = run(api, search(job_feeds=["adzuna"]))
    entry = entry_of(entries, "adzuna")
    assert api.requests == []
    assert entry["error"] == "no_key"
    assert entry["reason"] == "no_key"
    assert entry["outcome"] == "not_attempted"
    assert entry["phase"] == "done"
    assert commits.saved == {}


def test_adzuna_searches_france_with_the_key_and_maps(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    api = Api({("api.adzuna.com", "1"): fixture("adzuna")})
    entries, commits, _ = run(
        api,
        search(job_feeds=["adzuna"], location={"terms": ["bordeaux"], "advanced_regex": False}),
        keys={"adzuna": {"app_id": APP_ID, "app_key": APP_KEY}},
    )
    entry = entry_of(entries, "adzuna")
    assert [r.url.path for r in api.requests] == ["/v1/api/jobs/fr/search/1"] * 2
    params = api.requests[0].url.params
    assert params["app_id"] == APP_ID
    assert params["app_key"] == APP_KEY
    assert params["what"] == "forward deployed"
    assert params["max_days_old"] == "30"
    assert params["results_per_page"] == "50"
    [kept] = commits.saved["adzuna"]
    assert kept.external_id == "4001"
    assert kept.title == "AI Engineer H/F"
    assert kept.url == "https://www.adzuna.fr/land/ad/4001"  # no tracking query
    assert kept.location == "Bordeaux, Gironde, Nouvelle-Aquitaine, France"
    assert kept.contract is ContractType.CDI
    assert kept.company == "Example Conseil"
    assert kept.published_on == "2026-10-29"
    assert entry["left_out"] == {"place": 1}  # Paris
    # The key never reaches a log line, nor an address with its query.
    assert APP_KEY not in caplog.text
    assert "app_key" not in caplog.text


def test_adzuna_refused_key() -> None:
    api = Api({("api.adzuna.com", "1"): 401})
    entries, _, _ = run(
        api, search(job_feeds=["adzuna"]), keys={"adzuna": {"app_id": APP_ID, "app_key": APP_KEY}}
    )
    entry = entry_of(entries, "adzuna")
    assert entry["error"] == "key_refused"
    assert entry["outcome"] == "failed"
    assert len(api.requests) == 1


# --- shared rules ---------------------------------------------------------------------------------


def test_feeds_run_in_order_and_only_the_ticked_ones() -> None:
    api = Api({("himalayas.app", ""): fixture("himalayas"), **arbeitnow_api().answers})
    entries, _, _ = run(api, search(job_feeds=["himalayas", "arbeitnow"]))
    assert [e["kind"] for e in entries] == ["arbeitnow", "himalayas"]
    assert {r.url.host for r in api.requests} == {"www.arbeitnow.com", "himalayas.app"}


def test_no_wanted_title_never_widens_a_feed() -> None:
    api = arbeitnow_api()
    entries, commits, _ = run(
        api, search(job_feeds=["arbeitnow"], title_include={"terms": [], "advanced_regex": False})
    )
    assert entry_of(entries, "arbeitnow")["error"] == "unsupported_criteria"
    assert api.requests == []
    assert commits.saved == {}


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (httpx.Response(200, content=b"<html>"), "invalid_response"),
        (httpx.Response(503), "upstream_error"),
        (httpx.Response(403), "blocked"),
        (httpx.Response(200, content=b"x" * (feeds.BODY_MAX + 1)), "too_large"),
        (httpx.Response(200, stream=httpx.ByteStream(b"x" * (feeds.BODY_MAX + 1))), "too_large"),
    ],
)
def test_http_failures_are_codes(answer: httpx.Response, code: str) -> None:
    http = Http(Transport(lambda request: answer), budget=1, sleep=_no_sleep)
    with pytest.raises(feeds.FeedError) as caught:
        asyncio.run(http.get_json("https://www.arbeitnow.com/api/job-board-api"))
    assert caught.value.code == code


def test_a_network_failure_is_a_code() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    http = Http(Transport(fail), budget=1, sleep=_no_sleep)
    with pytest.raises(feeds.FeedError) as caught:
        asyncio.run(http.get_json("https://himalayas.app/jobs/api/search"))
    assert caught.value.code == "network"


def test_plain_text_reads_html_as_lines() -> None:
    assert feeds.plain_text("<p>One&nbsp;<b>two</b></p><script>x()</script><li>three</li>") == (
        "One two\nthree"
    )
    assert feeds.line("<strong>AI</strong>\n Engineer") == "AI Engineer"
    assert feeds.plain_text(None) == ""


async def _no_sleep(seconds: float) -> None:
    return None
