"""Opportunities O7: the freelance and project feeds of the sweep (tasks/opportunities/todo.md, O7).

Each parser on its recorded fixture (0, 1 and 500 items, missing fields, HTML, an address off
the source's host), the freelance rules as accept and refuse tables, the fetch's failures, the
sweep reading the feeds of the fake feeds server (tests/feeds_stub.py) with the person's rules
(include, exclude, location, days, never apply, duplicates across feeds and with a board), the
catalogue route, the `feeds` field of the profile, the settings, and isolation between two
people. Never a real platform: every address is the fake server's on a loopback port.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.jobs import ats, sweep
from app.jobs.models import ContractType, Job, JobEvent, JobStatus, SweepStatus
from app.opportunities import sources
from app.opportunities.sources import (
    FeedClient,
    FeedError,
    catalogue,
    hackernews,
    plain_text,
    remoteok,
    weworkremotely,
)
from app.profile.schemas import Feed, SearchSection
from tests.identity.api_helpers import Api
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import board, jobs_of, new_sweep, run, set_profile
from tests.opportunities.conftest import Person, get_deck
from tests.opportunities.o7_support import (
    feed,
    feeds_search,
    feeds_stub,
    no_sleep,
    requests_to,
    runner,
    stub_module,
)

__all__ = ["feeds_stub"]

stub = stub_module


def sweep_feeds(
    factory: sessionmaker[Session], person: Person, fake: FakeAts, hook: Any, **options: Any
) -> Any:
    return run(factory, new_sweep(factory, person.user_id), fake, feeds=hook, **options)


def rows(factory: sessionmaker[Session], person: Person) -> dict[str, Job]:
    return {job.title: job for job in jobs_of(factory, person.user_id)}


def entries(done: Any) -> dict[str, dict[str, Any]]:
    return {entry["kind"]: entry for entry in done.stats["sources"]}


# --- the parsers -------------------------------------------------------------------------------


def test_remote_ok_parses_its_recorded_answer() -> None:
    found = remoteok.parse(stub.remoteok_ok())
    titles = [posting.title for _, posting in found]
    # The legal notice is not a posting; the address off remoteok.com is refused.
    assert "AI Engineer, Platform" not in titles
    assert len(found) == 6
    company, first = found[0]
    assert company == "Quillfeather Example Labs"
    assert first.title == "AI Engineer"
    assert first.url == "https://remoteok.com/remote-jobs/remote-1-100001"
    assert first.location == "Remote, Europe"
    assert first.work_mode == "remote"
    assert first.contract_type == "Freelance"  # tagged contract
    assert (
        first.published
        == (datetime.now(UTC) - timedelta(days=1, hours=1))
        .astimezone(sweep.SWEEP_TZ)
        .date()
        .isoformat()
    )
    assert "<p>" not in first.text
    assert "Python" in first.text
    _, salaried = found[1]
    assert salaried.contract_type is None
    assert (salaried.pay.min, salaried.pay.max, salaried.pay.currency, salaried.pay.period) == (
        90_000, 120_000, "USD", "year"
    )  # fmt: skip
    assert found[2][1].pay is None  # 0 to 0: not stated


@pytest.mark.parametrize("count", [0, 1, 500])
def test_remote_ok_reads_0_1_and_500_items(count: int) -> None:
    found = remoteok.parse(stub.remoteok_many(count))
    assert len(found) == count
    assert len({posting.url for _, posting in found}) == count


@pytest.mark.parametrize(
    "item",
    [
        {"id": "1"},
        {"id": "1", "position": "AI Engineer", "url": "https://remoteok.com/remote-jobs/1"},
        {"id": "1", "company": "Example", "url": "https://remoteok.com/remote-jobs/1"},
        {"id": "1", "company": "Example", "position": "AI Engineer"},
        {"id": "1", "company": "Example", "position": "AI Engineer", "url": 7},
        {"id": "1", "company": "Example", "position": "AI Engineer",
         "url": "https://remoteok.com.example.org/remote-jobs/1"},
        {"id": "1", "company": "Example", "position": "AI Engineer",
         "url": "javascript:alert(1)"},
        {"id": "1", "company": "<b> </b>", "position": "AI Engineer",
         "url": "https://remoteok.com/remote-jobs/1"},
        "not an object",
        None,
    ],
)  # fmt: skip
def test_remote_ok_skips_an_item_without_its_fields(item: Any) -> None:
    assert remoteok.parse([{"legal": "notice"}, item]) == []


def test_remote_ok_keeps_an_item_with_odd_optional_fields() -> None:
    item = {
        "id": "9", "company": "Example", "position": "<i>AI</i> Engineer",
        "url": "https://www.remoteok.com/remote-jobs/9", "tags": "contract", "epoch": "x",
        "date": "not a date", "salary_min": 120_000, "salary_max": 90_000, "location": None,
        "description": None,
    }  # fmt: skip
    [(company, posting)] = remoteok.parse([item])
    assert (company, posting.title, posting.location) == ("Example", "AI Engineer", "Remote")
    assert posting.contract_type is None  # tags not a list
    assert posting.published == ""
    assert posting.pay is None  # reversed figures
    assert posting.text == ""


@pytest.mark.parametrize("body", [{"legal": "x"}, "text", 3, None])
def test_remote_ok_refuses_an_answer_that_is_not_a_list(body: Any) -> None:
    with pytest.raises(FeedError) as caught:
        remoteok.parse(body)
    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize(
    ("tags", "freelance"),
    [
        (["contract"], True), (["Contract"], True), (["freelance"], True),
        (["freelancer"], True), (["contractor"], True), (["ai", " Freelance "], True),
        (["full time"], False), (["contracts management"], False), (["smart contract"], False),
        (["non freelance"], False), ([], False), ("contract", False), (None, False),
        ([3, None], False),
    ],
)  # fmt: skip
def test_remote_ok_freelance_rule(tags: Any, freelance: bool) -> None:
    assert remoteok.freelance(tags) is freelance


def test_we_work_remotely_parses_its_recorded_feed() -> None:
    found = weworkremotely.parse(stub.wwr_ok().encode())
    assert [(c, p.title) for c, p in found] == [
        ("Larkspur Example Studio", "Applied AI Engineer"),
        ("Tamberlin Example", "Forward Deployed Engineer"),
        ("Example Rempart", "Ingénieur Cybersécurité"),
    ]
    _, first = found[0]
    assert first.url == "https://weworkremotely.com/remote-jobs/example-1"
    assert first.location == "Remote, Anywhere in the World"
    assert first.contract_type is None
    assert first.published
    assert "<p>" not in first.text
    assert "&lt;" not in first.text
    assert found[1][1].contract_type == "Freelance"


@pytest.mark.parametrize("count", [0, 1, 500])
def test_we_work_remotely_reads_0_1_and_500_items(count: int) -> None:
    assert len(weworkremotely.parse(stub.wwr_many(count).encode())) == count


@pytest.mark.parametrize(
    "item",
    [
        "<item><title>No colon here</title><link>https://weworkremotely.com/remote-jobs/a</link></item>",
        "<item><title>: Role only</title><link>https://weworkremotely.com/remote-jobs/a</link></item>",
        "<item><title>Company:</title><link>https://weworkremotely.com/remote-jobs/a</link></item>",
        "<item><title>Example: AI Engineer</title></item>",
        "<item><title>Example: AI Engineer</title><link>https://example.org/a</link></item>",
    ],
)
def test_we_work_remotely_skips_an_item_without_its_fields(item: str) -> None:
    assert weworkremotely.parse(stub.wwr_feed([item]).encode()) == []


@pytest.mark.parametrize(
    "body",
    [
        b"<rss><channel><item><title>",
        b"not xml at all",
        b"<html><body>Not a feed</body></html>",
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss><channel>'
        b"<item><title>&lol;: x</title></item></channel></rss>",
        b"",
    ],
)
def test_we_work_remotely_refuses_what_is_not_rss(body: bytes) -> None:
    with pytest.raises(FeedError) as caught:
        weworkremotely.parse(body)
    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize(
    ("kind", "category", "text", "freelance"),
    [
        ("Contract", "Programming", "", True),
        ("Full-Time", "Contract Programming", "", True),
        ("Full-Time", "Programming", "This is a freelance mission of six months.", True),
        ("Full-Time", "Programming", "A contract role for three months.", True),
        ("Full-Time", "Programming", "We hire a contractor.", True),
        ("Full-Time", "Programming", "You will write smart contracts in Solidity.", False),
        ("Full-Time", "Programming", "Contracting parties agree.", False),
        ("Full-Time", "Programming", "", False),
        ("Full-Time", "Programming", "x" * 3000 + " freelance", False),  # past what is read
    ],
)
def test_we_work_remotely_freelance_rule(
    kind: str, category: str, text: str, freelance: bool
) -> None:
    assert weworkremotely.freelance(kind, category, text) is freelance


def comment(item_id: int, first_line: str, parent: int = 3002, **extra: Any) -> dict[str, Any]:
    return stub.hn_comment(item_id, parent, first_line, **extra)


def test_hacker_news_freelance_thread_keeps_only_clients_missions() -> None:
    found = hackernews.parse("freelance", 3002, list(stub.hn_ok().values()))
    by_company = {company: posting for company, posting in found}
    assert set(by_company) == {"Nimbletide Example", "Example Guard", "Longform Example"}
    mission = by_company["Nimbletide Example"]
    assert mission.title == "GenAI engineer for an LLM evaluation pipeline"
    assert mission.location == "Remote, Europe"
    assert mission.contract_type == "Freelance"
    assert mission.url == "https://news.ycombinator.com/item?id=4001"
    assert "TJM 600 €" in mission.text
    # A 50,000 character post is cut at the posting text limit of a feed.
    assert len(by_company["Longform Example"].text) == sources.TEXT_MAX


@pytest.mark.parametrize(
    "item",
    [
        comment(1, "SEEKING WORK | Remote | Python"),
        comment(2, "Seeking work: AI engineer"),
        comment(3, "SEEKING FREELANCER | Example | AI engineer", deleted=True),
        comment(4, "SEEKING FREELANCER | Example | AI engineer", dead=True),
        comment(5, "SEEKING FREELANCER | Example | AI engineer", parent=4001),  # a reply
        comment(6, "SEEKING FREELANCER | Example | AI engineer", type="story"),
        {**comment(7, "SEEKING FREELANCER | Example | AI engineer"), "text": None},
        {**comment(8, "SEEKING FREELANCER | Example | AI engineer"), "id": "8"},
        comment(9, "SEEKING FREELANCER"),
        comment(10, "SEEKING FREELANCER |  |  "),
        None,
        "text",
    ],
)
def test_hacker_news_skips_what_is_not_a_mission(item: Any) -> None:
    assert hackernews.parse_comment("freelance", 3002, item) is None


def test_hacker_news_freelance_with_one_part_names_no_client() -> None:
    found = hackernews.parse_comment(
        "freelance", 3002, comment(11, "SEEKING FREELANCER: React work")
    )
    assert found is not None
    company, posting = found
    assert (company, posting.title) == (hackernews.UNNAMED_CLIENT, "React work")


def test_hacker_news_hiring_thread() -> None:
    found = hackernews.parse("hiring", 3001, list(stub.hn_ok().values()))
    assert [(c, p.title, p.location, p.contract_type) for c, p in found] == [
        ("Orbitwell Example", "AI Engineer", "Paris or Remote", None),
        ("Pinecrest Example", "Senior GenAI Engineer", "London", "Freelance"),
    ]
    picked = hackernews.parse_comment(
        "hiring", 3001,
        comment(12, "Acme Example | Paris | Staff ML Engineer | $150k | https://acme.example.org",
                parent=3001),
    )  # fmt: skip
    assert picked is not None
    assert (picked[1].title, picked[1].location) == ("Staff ML Engineer", "Paris")
    assert (
        hackernews.parse_comment("hiring", 3001, comment(13, "Lonely Example", parent=3001)) is None
    )


def test_plain_text_keeps_paragraphs_and_drops_markup() -> None:
    text = plain_text(
        "<p>One &amp; two</p><script>alert('x')</script><style>p{}</style>"
        "<ul><li>Python</li><li>LLM</li></ul>Line<br>break\x00 &#x2F; end"
    )
    assert text == "One & two\n\nPython\nLLM\nLine\nbreak / end"
    assert plain_text(None) == ""
    assert len(plain_text("a" * 100_000)) == sources.TEXT_MAX


# --- the client --------------------------------------------------------------------------------


def read_all(server: Any, variant: str, **client: Any) -> dict[str, Any]:
    """Each kind's postings, or its error code, from `variant` of the fake server."""

    async def go() -> dict[str, Any]:
        answers: dict[str, Any] = {}
        options = {"timeout_s": 1.0, "sleep": no_sleep, **client}
        async with FeedClient(stub_base_url=server.base(variant), **options) as c:
            for kind, reader in sources.readers(50).items():
                try:
                    answers[kind] = await reader(c, Feed(kind=kind))
                except FeedError as exc:
                    answers[kind] = exc.code
        return answers

    return asyncio.run(go())


@pytest.mark.parametrize(
    ("variant", "code"),
    [
        ("404", "not_found"),
        ("403", "blocked"),
        ("429", "rate_limited"),
        ("500", "upstream_error"),
        ("malformed", "invalid_response"),
        ("shape", "invalid_response"),
    ],
)
def test_each_failure_has_its_code(feeds_stub: Any, variant: str, code: str) -> None:
    assert set(read_all(feeds_stub, variant).values()) == {code}


def test_a_timeout_is_reported(feeds_stub: Any) -> None:
    answers = read_all(feeds_stub, "timeout", timeout_s=0.3, attempts=1)
    assert answers["remoteok"] == "timeout"
    assert answers["weworkremotely"] == "timeout"


def test_429_once_is_retried(feeds_stub: Any) -> None:
    answers = read_all(feeds_stub, "429-once")
    assert len(answers["remoteok"]) == 6
    assert len(answers["weworkremotely"]) == 3


def test_one_request_per_feed_and_the_thread_reads_its_comments_only(feeds_stub: Any) -> None:
    read_all(feeds_stub, "many")
    assert requests_to(feeds_stub, "/many/remoteok/api") == 1
    assert requests_to(feeds_stub, "/many/weworkremotely/") == 1
    # Two threads: the account twice, the submissions up to each thread, 50 comments each.
    assert requests_to(feeds_stub, "/user/whoishiring.json") == 2
    assert requests_to(feeds_stub, "/many/hackernews/v0/item/") == 2 + 3 + 50 * 2


def test_a_thread_whose_every_comment_failed_reports_the_error() -> None:
    async def go() -> Any:
        calls = {"n": 0}

        async def get(url: str, hosts: frozenset[str]) -> bytes:
            calls["n"] += 1
            if url.endswith("whoishiring.json"):
                return b'{"submitted": [3002]}'
            if url.endswith("/3002.json"):
                return (
                    b'{"by": "whoishiring", "type": "story", "id": 3002, "kids": [1, 2],'
                    b' "title": "Ask HN: Freelancer? Seeking freelancer? (May)"}'
                )
            raise FeedError("timeout")

        client = FeedClient(stub_base_url="http://feeds.invalid")
        client.get = get  # type: ignore[method-assign]
        return await hackernews.read(client, "freelance")

    with pytest.raises(FeedError) as caught:
        asyncio.run(go())
    assert caught.value.code == "timeout"


def test_a_redirect_off_the_source_is_refused() -> None:
    with pytest.raises(FeedError) as caught:
        FeedClient._allowed("https://remoteok.com/api", "https://evil.example.org/api",
                            frozenset({"remoteok.com"}))  # fmt: skip
    assert caught.value.code == "upstream_error"
    assert (
        FeedClient._allowed("https://remoteok.com/api", "/api?x=1", frozenset({"remoteok.com"}))
        == "https://remoteok.com/api?x=1"
    )


def test_the_real_addresses_without_a_stub() -> None:
    client = FeedClient()
    assert client.address(remoteok.API, "remoteok")[0] == "https://remoteok.com/api"
    assert client.address(weworkremotely.FEED, "weworkremotely")[0] == weworkremotely.FEED
    assert client.address(f"{hackernews.API}/item/1.json", "hackernews")[0] == (
        "https://hacker-news.firebaseio.com/v0/item/1.json"
    )
    stubbed = FeedClient(stub_base_url="http://feedsstub:8098/ok")
    assert stubbed.address(remoteok.API, "remoteok") == (
        "http://feedsstub:8098/ok/remoteok/api",
        frozenset({"feedsstub"}),
    )


# --- the sweep ---------------------------------------------------------------------------------


def test_a_sweep_reads_the_enabled_feeds_and_stores_their_missions(
    factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts, feeds_stub: Any
) -> None:
    set_profile(
        factory, person_a.user_id,
        feeds_search(feed("remoteok"), feed("weworkremotely"), feed("hackernews_freelance")),
    )  # fmt: skip
    done = sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub))
    assert done.status is SweepStatus.done, done.error
    found = rows(factory, person_a)
    assert set(found) == {
        "AI Engineer",  # Remote OK, Quillfeather, contract
        "GenAI Engineer",  # Remote OK, Brightmoor
        "Applied AI Engineer",  # Remote OK and We Work Remotely: one row
        "Forward Deployed Engineer",  # We Work Remotely, contract
        "GenAI engineer for an LLM evaluation pipeline",  # Hacker News, a client
        "AI engineer",  # Hacker News, the long post
    }
    mission = found["AI Engineer"]
    assert mission.source == "remoteok"
    assert mission.url.startswith("https://remoteok.com/")
    assert mission.contract_type is ContractType.Freelance
    assert mission.status is JobStatus.found
    assert mission.work_mode == "remote"
    assert mission.fit is not None
    assert found["GenAI Engineer"].contract_type is ContractType.CDI
    assert (found["GenAI Engineer"].pay_min, found["GenAI Engineer"].pay_currency) == (
        90_000,
        "USD",
    )
    both = found["Applied AI Engineer"]
    assert both.source == "remoteok, weworkremotely"
    hn = found["GenAI engineer for an LLM evaluation pipeline"]
    assert hn.source == "hackernews:freelance"
    assert hn.contract_type is ContractType.Freelance
    # The daily rate the text states (O3's parser): a day rate is a pay with period day.
    assert (hn.pay_min, hn.pay_max, hn.pay_currency, hn.pay_period) == (600, 600, "EUR", "day")
    assert len(found["AI engineer"].posting_text or "") == sources.TEXT_MAX
    assert "<p>" not in (hn.posting_text or "")
    # Every never apply title (folded: "Cybersecurity", "Cybersécurité", a soft hyphen) is
    # never stored, so never found; nor the excluded "Sales Lead" title.
    assert not any("ecur" in title or "Cyber" in title for title in found)
    reported = entries(done)
    assert reported["remoteok"]["error"] is None
    assert reported["remoteok"]["excluded"] == 2
    assert reported["weworkremotely"]["excluded"] == 1
    assert reported["hackernews_freelance"]["excluded"] == 1
    assert reported["remoteok"]["postings"] == 3
    assert reported["weworkremotely"]["postings"] == 1  # Larkspur's was a duplicate
    # The events name the sweep and the feed.
    with factory() as db:
        boards = (
            db.execute(
                sa.select(JobEvent.data["board"].astext).where(
                    JobEvent.job_id == mission.id, JobEvent.kind == "sweep"
                )
            )
            .scalars()
            .all()
        )
    assert boards == ["remoteok"]


def test_the_deck_shows_the_missions_with_their_source(
    api: Api, factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts, feeds_stub: Any
) -> None:
    set_profile(factory, person_a.user_id, feeds_search(feed("remoteok"), min_fit=0))
    sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub))
    cards = get_deck(api, person_a).json()["cards"]
    mission = next(card for card in cards if card["company"] == "Quillfeather Example Labs")
    assert mission["source"] == "remoteok"
    assert mission["url"].startswith("https://remoteok.com/remote-jobs/")
    assert mission["contract_type"] == "Freelance"
    assert not any("ecur" in card["title"] for card in cards)


def test_a_disabled_feed_is_not_fetched(
    factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts, feeds_stub: Any
) -> None:
    set_profile(
        factory, person_a.user_id,
        feeds_search(feed("remoteok", enabled=False), feed("weworkremotely")),
    )  # fmt: skip
    done = sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub))
    assert done.status is SweepStatus.done
    assert requests_to(feeds_stub, "remoteok") == 0
    assert requests_to(feeds_stub, "weworkremotely") == 1
    assert set(entries(done)) == {"weworkremotely"}


def test_only_disabled_feeds_and_no_board_fails_as_before(
    factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts, feeds_stub: Any
) -> None:
    set_profile(factory, person_a.user_id, feeds_search(feed("remoteok", enabled=False)))
    done = sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub))
    assert done.status is SweepStatus.failed
    assert done.error == sweep.NO_BOARDS
    assert requests_to(feeds_stub, "remoteok") == 0


@pytest.mark.parametrize(
    ("variant", "code"),
    [
        ("404", "not_found"),
        ("429", "rate_limited"),
        ("malformed", "invalid_response"),
        ("shape", "invalid_response"),
        ("huge", "too_many"),
    ],
)
def test_a_failing_feed_is_reported_and_the_sweep_goes_on(
    factory: sessionmaker[Session],
    person_a: Person,
    fake_ats: FakeAts,
    feeds_stub: Any,
    variant: str,
    code: str,
) -> None:
    set_profile(
        factory, person_a.user_id,
        feeds_search(feed("remoteok"), feed("weworkremotely"), boards=[board("fake-ok")]),
    )  # fmt: skip
    done = sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub, variant))
    assert done.status is SweepStatus.done
    assert done.stats["boards_ok"] == 1
    assert {kind: entry["error"] for kind, entry in entries(done).items()} == {
        "remoteok": code,
        "weworkremotely": code,
    }
    # The board's postings are kept; nothing of a failed feed is.
    assert {job.source for job in jobs_of(factory, person_a.user_id)} == {"ashby:fake-ok"}


def test_a_timeout_of_one_feed_leaves_the_next_one(
    factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts, feeds_stub: Any
) -> None:
    set_profile(factory, person_a.user_id, feeds_search(feed("remoteok"), feed("weworkremotely")))
    hook = runner(feeds_stub, "timeout", timeout_s=0.3, attempts=1)

    async def wwr_ok(client: FeedClient, feed_: Feed) -> Any:
        async with FeedClient(stub_base_url=feeds_stub.base("ok"), sleep=no_sleep) as other:
            return await weworkremotely.read(other, feed_)

    hook.read["weworkremotely"] = wwr_ok
    done = sweep_feeds(factory, person_a, fake_ats, hook)
    reported = entries(done)
    assert reported["remoteok"]["error"] == "timeout"
    assert reported["weworkremotely"]["error"] is None
    assert reported["weworkremotely"]["postings"] == 2


def test_the_same_posting_on_a_board_and_in_a_feed_is_one_row(
    factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts, feeds_stub: Any
) -> None:
    # fake-ok lists "AI Engineer" in Paris; Remote OK lists it for the same company, remote.
    set_profile(
        factory, person_a.user_id,
        feeds_search(feed("remoteok"),
                     boards=[board("fake-ok", company="Quillfeather Example Labs")]),
    )  # fmt: skip
    sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub))
    same = [job for job in jobs_of(factory, person_a.user_id) if job.title == "AI Engineer"]
    assert len(same) == 1
    assert same[0].source == "ashby:fake-ok, remoteok"
    assert same[0].location == "Paris, France"  # the board's row, kept as it was


def test_a_second_sweep_adds_nothing_twice(
    factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts, feeds_stub: Any
) -> None:
    set_profile(
        factory, person_a.user_id,
        feeds_search(feed("remoteok"), feed("weworkremotely"), feed("hackernews_freelance")),
    )  # fmt: skip
    sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub))
    first = {job.id for job in jobs_of(factory, person_a.user_id)}
    again = sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub))
    assert {job.id for job in jobs_of(factory, person_a.user_id)} == first
    assert again.stats["new"] == 0
    assert all(entry["postings"] == 0 for entry in again.stats["sources"])


def test_the_person_rules_apply_to_a_feed(
    factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts, feeds_stub: Any
) -> None:
    set_profile(
        factory, person_a.user_id,
        feeds_search(
            feed("remoteok", terms=["genai"]), feed("weworkremotely"),
            title_exclude={"terms": ["forward deployed"], "advanced_regex": False},
            contract_types=["CDI"],
        ),
    )  # fmt: skip
    sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub))
    # Remote OK: only titles with the feed's own term; the contract filter drops missions;
    # We Work Remotely: the excluded title is gone.
    assert set(rows(factory, person_a)) == {"GenAI Engineer", "Applied AI Engineer"}


def test_the_days_window_applies_to_a_feed(
    factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts, feeds_stub: Any
) -> None:
    set_profile(factory, person_a.user_id, feeds_search(feed("remoteok"), days=30))
    today = datetime.now(sweep.SWEEP_TZ).date()

    def dated(title: str, published: str) -> tuple[str, ats.Posting]:
        url = f"https://remoteok.com/remote-jobs/{title.replace(' ', '-').lower()}"
        return "Example Dates", ats.Posting(title, "Remote", url, published, "Python.", True)

    async def read(client: FeedClient, feed_: Feed) -> Any:
        return [
            dated("AI Engineer Old", (today - timedelta(days=31)).isoformat()),
            dated("AI Engineer Edge", (today - timedelta(days=30)).isoformat()),
            dated("AI Engineer Undated", ""),  # counts as found today, as a browser source's
        ]

    hook = runner(feeds_stub)
    hook.read["remoteok"] = read
    sweep_feeds(factory, person_a, fake_ats, hook)
    found = rows(factory, person_a)
    assert set(found) == {"AI Engineer Edge", "AI Engineer Undated"}
    assert found["AI Engineer Undated"].published_on is None


def test_the_thread_reads_at_most_the_comments_allowed(
    factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts, feeds_stub: Any
) -> None:
    set_profile(factory, person_a.user_id, feeds_search(feed("hackernews_freelance")))
    done = sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub, "many", max_comments=10))
    assert done.status is SweepStatus.done
    assert entries(done)["hackernews_freelance"]["postings"] == 10
    # The account, the submissions up to the freelance thread (3003 then 3002), 10 comments.
    assert requests_to(feeds_stub, "/many/hackernews/") == 1 + 2 + 10


def test_a_store_failure_is_reported_without_the_text(
    factory: sessionmaker[Session],
    person_a: Person,
    fake_ats: FakeAts,
    feeds_stub: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    set_profile(factory, person_a.user_id, feeds_search(feed("remoteok")))

    def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("INSERT ... 'We build agents and LLM tools'")

    monkeypatch.setattr(sweep, "store_postings", broken)
    with caplog.at_level(logging.DEBUG):
        done = sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub))
    assert done.status is SweepStatus.done
    assert entries(done)["remoteok"]["error"] == "store_failed"
    assert jobs_of(factory, person_a.user_id) == []
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "We build agents" not in logged
    assert "Quillfeather" not in logged


def test_no_feed_starts_near_the_time_limit() -> None:
    plan = type("P", (), {"feeds": [Feed(kind="remoteok", enabled=True)], "rules": []})()
    stats = sweep.Stats()
    stored: list[Any] = []

    async def store(source: str, found: Any) -> None:
        stored.append(found)

    async def progress() -> None:
        return None

    hook = sources.FeedRunner(lambda: FeedClient(stub_base_url="http://feeds.invalid"))
    asyncio.run(hook(plan, stats, store, progress, time.monotonic() + 1))
    assert stats.sources[0]["error"] == "time_budget"
    assert stored == []


def test_b_feeds_are_never_swept_for_a(
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    fake_ats: FakeAts,
    feeds_stub: Any,
) -> None:
    set_profile(factory, person_a.user_id, feeds_search(boards=[board("fake-ok")]))
    set_profile(factory, person_b.user_id, feeds_search(feed("remoteok")))
    done = sweep_feeds(factory, person_a, fake_ats, runner(feeds_stub))
    assert done.stats["sources"] == []
    assert requests_to(feeds_stub, "remoteok") == 0
    sweep_feeds(factory, person_b, fake_ats, runner(feeds_stub))
    assert {job.source for job in jobs_of(factory, person_a.user_id)} == {"ashby:fake-ok"}
    assert {job.source for job in jobs_of(factory, person_b.user_id)} == {"remoteok"}


def test_a_schedule_runs_for_a_person_with_feeds_only(
    factory: sessionmaker[Session], person_a: Person
) -> None:
    schedule = {
        "enabled": True,
        "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
        "times": ["06:00"],
        "timezone": "Europe/Paris",
    }
    set_profile(factory, person_a.user_id, feeds_search(feed("remoteok"), sweep_schedule=schedule))
    now = datetime(2026, 10, 20, 4, 5, tzinfo=UTC)  # 06:05 in Paris
    with factory() as db:
        due = sweep.scheduled_due(db, now)
    assert person_a.user_id in {item.user_id for item in due}


# --- the profile field -------------------------------------------------------------------------


def test_the_feeds_field_validates() -> None:
    section = SearchSection.model_validate({"feeds": [feed("remoteok"), feed("hackernews_hiring")]})
    assert [f.kind for f in section.feeds] == ["remoteok", "hackernews_hiring"]
    assert SearchSection.model_validate({}).feeds == []
    assert Feed.model_validate({"kind": "weworkremotely"}).enabled is False
    for bad in (
        {"feeds": [feed("remoteok"), feed("remoteok", enabled=False)]},  # one per kind
        {"feeds": [feed("malt")]},  # O7b's, never a feed
        {"feeds": [feed("upwork")]},
        {"feeds": [{"kind": "remoteok", "enabled": "yes"}]},
        {"feeds": [{"kind": "remoteok", "url": "https://example.org"}]},
        {"feeds": [feed("remoteok")] * 11},
        {"feeds": "remoteok"},
    ):
        with pytest.raises(ValidationError):
            SearchSection.model_validate(bad)


def test_the_feeds_are_saved_and_read_through_the_profile(
    api: Api, person_a: Person, person_b: Person
) -> None:
    current = api.get("/api/profile", token=person_a.token).json()
    data = {**current["sections"]["search"], "feeds": [feed("remoteok", terms=["python"])]}
    saved = api.request(
        "PUT", "/api/profile/search", token=person_a.token,
        json={"data": data, "base_revision": current["revision"]},
    )  # fmt: skip
    assert saved.status_code == 200, saved.text
    again = api.get("/api/profile", token=person_a.token).json()["sections"]["search"]
    assert again["feeds"] == [feed("remoteok", terms=["python"])]
    other = api.get("/api/profile", token=person_b.token).json()["sections"]["search"]
    assert other.get("feeds", []) == []


# --- the catalogue -----------------------------------------------------------------------------


def test_the_catalogue_route(api: Api, person_a: Person) -> None:
    answer = api.get("/api/opportunities/feeds", token=person_a.token)
    assert answer.status_code == 200
    body = answer.json()
    assert [s["kind"] for s in body["supported"]] == list(sources.SOURCES)
    assert {p["kind"] for p in body["not_supported"]} == {
        "malt", "freework", "codeur", "upwork", "freelancer", "comet", "cremedelacreme"
    }  # fmt: skip
    assert all(p["checked_on"] == "2026-10-20" and p["reason"] for p in body["not_supported"])
    assert api.get("/api/opportunities/feeds").status_code == 401


def test_the_catalogue_kinds_are_the_profile_kinds() -> None:
    from typing import get_args

    from app.profile.schemas import FeedKind

    assert get_args(FeedKind) == catalogue.SUPPORTED_KINDS
    assert set(sources.SOURCES) == set(get_args(FeedKind))


def test_no_dash_as_a_separator_in_what_a_person_reads() -> None:
    texts = [
        value
        for entry in [*catalogue.SUPPORTED, *catalogue.NOT_SUPPORTED]
        for key, value in vars(entry).items()
        if key not in ("terms_url", "checked_on", "kind")
    ] + list(sources.NAMES.values())
    for text in texts:
        assert "\u2014" not in text, text
        assert "\u2013" not in text, text
        assert " - " not in text, text


def test_every_source_module_names_its_terms_and_date() -> None:
    for module in (remoteok, weworkremotely, hackernews):
        assert module.__doc__ is not None
        assert "2026-10-20" in module.__doc__
        assert "https://" in module.__doc__


# --- the settings ------------------------------------------------------------------------------


def test_the_stub_address_is_refused_in_production(migrated_url: str) -> None:
    with pytest.raises(ValidationError, match="FEEDS_STUB_BASE_URL"):
        Settings(
            database_url=migrated_url,
            app_env="production",
            feeds_stub_base_url="http://feedsstub:8098/ok",
        )  # type: ignore[call-arg]
    test = Settings(
        database_url=migrated_url, app_env="test", feeds_stub_base_url="http://feedsstub:8098/ok/"
    )  # type: ignore[call-arg]
    assert test.feeds_stub_base_url == "http://feedsstub:8098/ok"
    assert (test.feeds_timeout_s, test.feeds_hn_max_comments) == (20.0, 500)
    with pytest.raises(ValidationError):
        Settings(database_url=migrated_url, feeds_hn_max_comments=0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(database_url=migrated_url, feeds_stub_base_url="ftp://x")  # type: ignore[call-arg]


def test_the_worker_hook_follows_the_settings(migrated_url: str) -> None:
    from app import worker

    settings = Settings(
        database_url=migrated_url,
        app_env="test",
        feeds_stub_base_url="http://feedsstub:8098/ok",
        feeds_hn_max_comments=7,
        test_fast_timers=True,
    )  # type: ignore[call-arg]
    hook = worker.feed_sources({"settings": settings})
    assert isinstance(hook, sources.FeedRunner)
    client = hook.client_factory()
    assert (client.stub_base_url, client.attempts, client.timeout_s) == (
        "http://feedsstub:8098/ok", 1, 2.0
    )  # fmt: skip
    marker = object()
    assert worker.feed_sources({"settings": settings, "feed_sources": marker}) is marker


def test_a_feed_posting_states_its_contract() -> None:
    posting = ats.Posting("AI Engineer", "Remote", "https://remoteok.com/x", "", "", True,
                          contract_type="Freelance")  # fmt: skip
    assert sweep.contract_of(posting) is ContractType.Freelance
    assert sweep.contract_of(ats.Posting("AI Engineer", "", "", "", "", False)) is ContractType.CDI
    odd = ats.Posting("AI Engineer", "", "", "", "", False, contract_type="nonsense")
    assert sweep.contract_of(odd) is ContractType.CDI
