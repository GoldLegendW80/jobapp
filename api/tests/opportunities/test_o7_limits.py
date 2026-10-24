"""Opportunities O7, the limits of a feed read (round 2 of PR #505): HTML made plain in linear
time whatever third parties write, a huge feed refused before it is parsed, parsing off the
event loop, two unnamed Hacker News clients kept apart, a 429 on a comment stopping its feed,
the sweep's deadline inside a read, the body caps, redirects followed end to end on the fake
feeds server, and a test stack without that server reading no feed. Never a real platform:
every address is the fake server's on a loopback port, or `feeds.invalid` behind a mock
transport.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from xml.sax.saxutils import escape

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.jobs import sweep
from app.jobs.models import JobStatus
from app.opportunities import sources
from app.opportunities.sources import (
    FeedClient,
    FeedError,
    hackernews,
    parse_json,
    plain_text,
    remoteok,
    weworkremotely,
)
from app.profile.schemas import Feed
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import jobs_of, new_sweep, run, set_profile
from tests.opportunities.conftest import Person
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

LENGTH = 80_000  # what plain_text reads of a posting: TEXT_MAX * 4
FAST_S = 0.2


def _fill(unit: str) -> str:
    return (unit * (LENGTH // len(unit) + 1))[:LENGTH]


# Markup that made the round 1 patterns quadratic: a "<" never closed, a script never closed,
# break tags never closed, and their mixes.
HOSTILE = {
    "lt": _fill("<"),
    "lt-space": _fill("< "),
    "script": _fill("<script>"),
    "script-open": _fill("<script"),
    "style": _fill("<style "),
    "p": _fill("<p "),
    "br": _fill("<br"),
    "close-never-matches": ("<script>" + _fill("</scrip"))[:LENGTH],
    "mix": _fill("<p<script<style<br"),
}


def elapsed(call: Any, *args: Any) -> float:
    started = time.perf_counter()
    with contextlib.suppress(FeedError):
        call(*args)
    return time.perf_counter() - started


def sweep_feeds(factory: sessionmaker[Session], person: Person, fake: FakeAts, hook: Any) -> Any:
    return run(factory, new_sweep(factory, person.user_id), fake, feeds=hook)


# --- linear time ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_markup_is_made_plain_fast(name: str) -> None:
    text = HOSTILE[name]
    assert len(text) == LENGTH
    assert elapsed(plain_text, text) < FAST_S
    comment = stub.hn_comment(1, 2, "SEEKING FREELANCER | " + text)
    assert elapsed(hackernews.parse_comment, "freelance", 2, comment) < FAST_S
    item = {
        "id": "1", "company": text, "position": text, "location": text, "description": text,
        "url": "https://remoteok.com/remote-jobs/1",
    }  # fmt: skip
    assert elapsed(remoteok.parse, [item]) < FAST_S
    rss = stub.wwr_feed(
        [
            "<item><title>Example: AI Engineer</title>"
            "<link>https://weworkremotely.com/remote-jobs/1</link>"
            f"<description>{escape(text)}</description><region>{escape(text)}</region></item>"
        ]
    ).encode()
    assert elapsed(weworkremotely.parse, rss) < FAST_S


def test_scripts_and_styles_are_still_dropped() -> None:
    assert plain_text("a<script>x</script>b<STYLE type='t'>p{}</ style >c") == "a b c"
    # A block never closed runs to the end, as a browser reads it.
    assert plain_text("kept<script>alert(1)<p>gone") == "kept"
    assert plain_text("<p>One</p><p>Two</p>") == "One\n\nTwo"


def _huge_remote_ok() -> bytes:
    hostile = "<" * 600
    items = [
        {"id": str(n), "company": hostile, "position": hostile, "description": hostile,
         "url": f"https://remoteok.com/remote-jobs/{n}"}
        for n in range(5_000)
    ]  # fmt: skip
    return json.dumps([{"legal": "notice"}, *items]).encode()


def _huge_wwr() -> bytes:
    hostile = escape("<" * 1_500)
    item = (
        "<item><title>Example: AI Engineer</title>"
        "<link>https://weworkremotely.com/remote-jobs/{n}</link>"
        "<description>{text}</description></item>"
    )
    return stub.wwr_feed([item.format(n=n, text=hostile) for n in range(5_000)]).encode()


def test_a_huge_feed_is_refused_before_any_item_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*_: Any, **__: Any) -> str:
        raise AssertionError("an item was parsed")

    for module in (remoteok, weworkremotely):
        monkeypatch.setattr(module, "line", never)
        monkeypatch.setattr(module, "plain_text", never)
    remote_ok, wwr = _huge_remote_ok(), _huge_wwr()
    assert len(remote_ok) > 9_000_000
    assert len(wwr) > 9_000_000
    for parse, body in (
        (lambda b: remoteok.parse(parse_json(b)), remote_ok),
        (weworkremotely.parse, wwr),
    ):
        started = time.perf_counter()
        with pytest.raises(FeedError) as caught:
            parse(body)
        assert caught.value.code == "too_many"
        assert time.perf_counter() - started < 1.5


def test_exactly_the_limit_is_read() -> None:
    items = [
        {"id": str(n), "company": "Example", "position": f"AI Engineer {n}",
         "url": f"https://remoteok.com/remote-jobs/{n}"}
        for n in range(sources.ITEMS_MAX)
    ]  # fmt: skip
    assert len(remoteok.parse([{"legal": "notice"}, *items])) == sources.ITEMS_MAX
    with pytest.raises(FeedError):
        remoteok.parse([*items, {**items[0], "id": "x"}])


def test_every_reader_parses_off_the_event_loop(
    feeds_stub: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []
    original = asyncio.to_thread

    async def spy(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        called.append(getattr(func, "__module__", "") or "")
        return await original(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy)

    async def go() -> None:
        async with FeedClient(stub_base_url=feeds_stub.base("ok"), sleep=no_sleep) as client:
            for kind, reader in sources.readers(50).items():
                assert await reader(client, Feed(kind=kind)), kind

    asyncio.run(go())
    assert len(called) == 4  # one parse per feed, in a worker thread


# --- unnamed clients -----------------------------------------------------------------------------


def test_two_unnamed_clients_with_the_same_title_are_two_rows(
    factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts, feeds_stub: Any
) -> None:
    set_profile(
        factory, person_a.user_id,
        feeds_search(feed("hackernews_freelance"),
                     location={"terms": [], "advanced_regex": False}),
    )  # fmt: skip

    def mission(item_id: int) -> Any:
        found = hackernews.parse_comment(
            "freelance", 3002, stub.hn_comment(item_id, 3002, "SEEKING FREELANCER | AI engineer")
        )
        assert found is not None
        assert found[0] == hackernews.UNNAMED_CLIENT == sweep.UNNAMED_CLIENT
        assert found[1].location == ""
        return found

    batches = iter([[mission(4101), mission(4102)], [mission(4101), mission(4103)]])

    async def read(client: FeedClient, feed_: Feed) -> Any:
        return next(batches)

    hook = runner(feeds_stub)
    hook.read["hackernews_freelance"] = read
    first = sweep_feeds(factory, person_a, fake_ats, hook)
    assert first.stats["new"] == 2
    second = sweep_feeds(factory, person_a, fake_ats, hook)
    # The same comment again is a duplicate by its address; a third client is a third row.
    assert (second.stats["new"], second.stats["duplicates"]) == (1, 1)
    jobs = jobs_of(factory, person_a.user_id)
    assert sorted(job.url for job in jobs) == [
        f"https://news.ycombinator.com/item?id={n}" for n in (4101, 4102, 4103)
    ]
    assert {(job.company, job.title, job.status) for job in jobs} == {
        (sweep.UNNAMED_CLIENT, "AI engineer", JobStatus.found)
    }
    assert all(job.source == "hackernews:freelance" for job in jobs)


# --- 429 and the deadline ------------------------------------------------------------------------


def _thread_client(comment: Any) -> FeedClient:
    """A client whose Hacker News answers come from `comment(item_id, options)` past the
    account and the freelance thread (100 comments)."""

    async def get(url: str, hosts: frozenset[str], **options: Any) -> bytes:
        if url.endswith("whoishiring.json"):
            return b'{"submitted": [3002]}'
        if url.endswith("/3002.json"):
            return json.dumps(
                {"by": "whoishiring", "type": "story", "id": 3002, "kids": list(range(1, 101)),
                 "title": "Ask HN: Freelancer? Seeking freelancer? (May)"}
            ).encode()  # fmt: skip
        return await comment(int(url.rsplit("/", 1)[1].removesuffix(".json")), options)

    client = FeedClient(stub_base_url="http://feeds.invalid")
    client.get = get  # type: ignore[method-assign]
    return client


def test_a_429_on_a_comment_stops_the_feed() -> None:
    calls: list[dict[str, Any]] = []

    async def comment(item_id: int, options: dict[str, Any]) -> bytes:
        calls.append(options)
        raise FeedError("rate_limited")

    with pytest.raises(FeedError) as caught:
        asyncio.run(hackernews.read(_thread_client(comment), "freelance"))
    assert caught.value.code == "rate_limited"
    assert 1 <= len(calls) <= sources.HN_CONCURRENCY
    assert all(options["retry_rate_limited"] is False for options in calls)
    assert all(options["max_body"] == sources.HN_ITEM_MAX_BODY for options in calls)


def test_a_429_is_retried_unless_asked_not_to(feeds_stub: Any) -> None:
    async def go(**options: Any) -> str:
        async with FeedClient(
            stub_base_url=feeds_stub.base("429"), sleep=no_sleep, attempts=3
        ) as client:
            url, hosts = client.address(remoteok.API, "remoteok")
            try:
                await client.get(url, hosts, **options)
            except FeedError as exc:
                return exc.code
        return "read"

    assert asyncio.run(go(retry_rate_limited=False)) == "rate_limited"
    assert requests_to(feeds_stub, "/429/remoteok/api") == 1
    assert asyncio.run(go()) == "rate_limited"
    assert requests_to(feeds_stub, "/429/remoteok/api") == 1 + 3


def test_no_request_starts_past_the_deadline(feeds_stub: Any) -> None:
    async def go() -> Any:
        async with FeedClient(
            stub_base_url=feeds_stub.base("ok"), deadline=time.monotonic() - 1
        ) as client:
            return await hackernews.read(client, "freelance")

    with pytest.raises(FeedError) as caught:
        asyncio.run(go())
    assert caught.value.code == "time_budget"
    assert requests_to(feeds_stub, "/ok/") == 0


def test_a_thread_stops_reading_when_the_deadline_passes(feeds_stub: Any) -> None:
    ticks = iter([0.0] * 5)

    def monotonic() -> float:
        return next(ticks, 1_000.0)

    async def go() -> Any:
        async with FeedClient(
            stub_base_url=feeds_stub.base("many"), deadline=100.0, monotonic=monotonic
        ) as client:
            return await hackernews.read(client, "freelance", 500)

    with pytest.raises(FeedError) as caught:
        asyncio.run(go())
    assert caught.value.code == "time_budget"
    # The account, two submissions, then a couple of comments: never the 500.
    assert requests_to(feeds_stub, "/many/hackernews/") <= 5


def test_a_retry_never_waits_past_the_deadline(feeds_stub: Any) -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    async def go() -> str:
        async with FeedClient(
            stub_base_url=feeds_stub.base("429"), sleep=sleep, attempts=3,
            deadline=100.0, monotonic=lambda: 99.5,
        ) as client:  # fmt: skip
            url, hosts = client.address(remoteok.API, "remoteok")
            try:
                await client.get(url, hosts)
            except FeedError as exc:
                return exc.code
        return "read"

    assert asyncio.run(go()) == "time_budget"  # Retry-After: 1 would end past the deadline
    assert waits == []
    assert requests_to(feeds_stub, "/429/remoteok/api") == 1


def test_the_runner_gives_each_feed_the_sweep_deadline() -> None:
    seen: list[float | None] = []
    plan = type("P", (), {"feeds": [Feed(kind="remoteok", enabled=True)], "rules": []})()

    async def read(client: FeedClient, feed_: Feed) -> Any:
        seen.append(client.deadline)
        return []

    async def store(source: str, found: Any) -> None:
        return None

    async def progress() -> None:
        return None

    hook = sources.FeedRunner(
        lambda: FeedClient(stub_base_url="http://feeds.invalid", timeout_s=7.0),
        {"remoteok": read},
        monotonic=lambda: 0.0,
    )
    asyncio.run(hook(plan, sweep.Stats(), store, progress, 1_000.0))
    assert seen == [1_000.0 - 7.0 - sources.STORE_MARGIN_S]


# --- body caps -----------------------------------------------------------------------------------


def _mock(handler: Any, **options: Any) -> FeedClient:
    return FeedClient(
        stub_base_url="http://feeds.invalid", transport=httpx.MockTransport(handler),
        sleep=no_sleep, **options,
    )  # fmt: skip


def test_a_body_over_the_cap_is_refused_by_its_length(feeds_stub: Any) -> None:
    async def go() -> dict[str, Any]:
        answers: dict[str, Any] = {}
        async with FeedClient(
            stub_base_url=feeds_stub.base("ok"), sleep=no_sleep, max_body=100
        ) as client:
            for kind in ("remoteok", "weworkremotely"):
                try:
                    answers[kind] = await sources.readers()[kind](client, Feed(kind=kind))
                except FeedError as exc:
                    answers[kind] = exc.code
        return answers

    assert asyncio.run(go()) == {"remoteok": "too_large", "weworkremotely": "too_large"}


def test_a_body_over_the_cap_is_refused_while_streaming() -> None:
    sent: list[int] = []

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(50):
            sent.append(1)
            yield b"x" * 1_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunks())  # no Content-Length

    async def go() -> str:
        async with _mock(handler, max_body=5_000) as client:
            url, hosts = client.address(remoteok.API, "remoteok")
            try:
                await client.get(url, hosts)
            except FeedError as exc:
                return exc.code
        return "read"

    assert asyncio.run(go()) == "too_large"
    assert len(sent) < 50  # stopped at the cap, not read to the end


def test_a_comment_over_the_item_cap_is_skipped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/user/whoishiring.json"):
            return httpx.Response(200, json={"submitted": [3002]})
        if path.endswith("/item/3002.json"):
            return httpx.Response(
                200,
                json={"by": "whoishiring", "type": "story", "id": 3002, "kids": [1, 2],
                      "title": "Ask HN: Freelancer? Seeking freelancer? (May)"},
            )  # fmt: skip
        item_id = int(path.rsplit("/", 1)[1].removesuffix(".json"))
        first = f"SEEKING FREELANCER | Example {item_id} | AI engineer"
        body = stub.hn_comment(item_id, 3002, first)
        if item_id == 1:
            body["text"] += "x" * (sources.HN_ITEM_MAX_BODY + 1)
        return httpx.Response(200, json=body)

    async def go() -> Any:
        async with _mock(handler) as client:
            return await hackernews.read(client, "freelance")

    found = asyncio.run(go())
    assert [company for company, _ in found] == ["Example 2"]


# --- redirects end to end ------------------------------------------------------------------------


def read_all(server: Any, variant: str) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        answers: dict[str, Any] = {}
        async with FeedClient(stub_base_url=server.base(variant), sleep=no_sleep) as client:
            for kind, reader in sources.readers(50).items():
                try:
                    answers[kind] = await reader(client, Feed(kind=kind))
                except FeedError as exc:
                    answers[kind] = exc.code
        return answers

    return asyncio.run(go())


def test_a_redirect_on_the_same_host_is_followed(feeds_stub: Any) -> None:
    answers = read_all(feeds_stub, "redirect")
    assert len(answers["remoteok"]) == 7
    assert len(answers["weworkremotely"]) == 3
    assert {company for company, _ in answers["hackernews_freelance"]} >= {"Nimbletide Example"}
    assert requests_to(feeds_stub, "/redirect/remoteok/api") == 1
    assert requests_to(feeds_stub, "/ok/remoteok/api") == 1


def test_a_redirect_to_another_host_is_refused(feeds_stub: Any) -> None:
    assert set(read_all(feeds_stub, "redirect-off").values()) == {"upstream_error"}
    assert requests_to(feeds_stub, "/ok/") == 0  # localhost is never asked


def test_a_redirect_loop_stops_at_the_limit(feeds_stub: Any) -> None:
    assert set(read_all(feeds_stub, "redirect-loop").values()) == {"upstream_error"}
    assert requests_to(feeds_stub, "/redirect-loop/remoteok/api") == sources.MAX_REDIRECTS + 1


# --- a test stack without the fake server --------------------------------------------------------


def test_a_test_stack_without_the_fake_server_reads_no_feed(
    migrated_url: str, factory: sessionmaker[Session], person_a: Person, fake_ats: FakeAts
) -> None:
    settings = Settings(database_url=migrated_url, app_env="test", feeds_stub_base_url=None)  # type: ignore[call-arg]
    hook = sources.runner_for(settings)
    assert hook.client_factory().refuse_reads is True
    set_profile(
        factory, person_a.user_id, feeds_search(feed("remoteok"), feed("hackernews_freelance"))
    )
    done = sweep_feeds(factory, person_a, fake_ats, hook)
    assert {entry["kind"]: entry["error"] for entry in done.stats["sources"]} == {
        "remoteok": "stub_missing",
        "hackernews_freelance": "stub_missing",
    }
    assert jobs_of(factory, person_a.user_id) == []
    for env, stub_url in (("development", None), ("test", "http://feedsstub:8098/ok")):
        other = Settings(database_url=migrated_url, app_env=env, feeds_stub_base_url=stub_url)  # type: ignore[call-arg]
        assert sources.runner_for(other).client_factory().refuse_reads is False


# --- a hostile character reference, round 3 of PR #505 --------------------------------------------

LONG_DECIMAL = "&#" + "1" * 5_000
LONG_HEX = "&#x" + "a" * 5_000


@pytest.mark.parametrize(
    "hostile", [LONG_DECIMAL, LONG_DECIMAL + ";", LONG_HEX, "&#" + "1" * 4_301]
)
def test_a_long_character_reference_is_made_plain(hostile: str) -> None:
    text = plain_text(f"before {hostile} after &amp; &#65;")
    assert text.startswith("before")
    assert text.endswith("after & A")


def test_a_decoder_error_keeps_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_: str) -> str:
        raise ValueError("Exceeds the limit")

    monkeypatch.setattr(sources.html, "unescape", boom)
    assert plain_text("a &amp; b") == "a &amp; b"


def _run_feed(kind: str, parse: Any) -> dict[str, Any]:
    """One feed through the runner, its reader the parse of a recorded answer: the feed's entry."""
    from types import SimpleNamespace

    async def reader(client: Any, feed_: Feed) -> Any:
        return parse()

    async def store(source: str, kept: Any) -> None:
        stats.new += len(kept)

    async def progress() -> None:
        return None

    class Client:
        deadline = 0.0
        timeout_s = 1.0

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

    stats = SimpleNamespace(sources=[], new=0, skipped_by_rule=0, current_board="")
    hook = sources.FeedRunner(lambda: Client(), {kind: reader})  # type: ignore[arg-type,return-value]
    plan = SimpleNamespace(feeds=[Feed(**feed(kind))], sweep_id="e2e-parser-count")
    asyncio.run(hook(plan, stats, store, progress, time.monotonic() + 600))
    return {**stats.sources[0], "stored": stats.new}


def _remote_ok() -> Any:
    def item(n: int, description: str) -> dict[str, Any]:
        return {"id": str(n), "position": f"AI engineer {n}", "company": f"Example {n}",
                "url": f"https://remoteok.com/remote-jobs/{n}", "description": description,
                "tags": ["freelance"]}  # fmt: skip

    return [{"legal": "notice"}, item(1, LONG_DECIMAL + " " + LONG_HEX), item(2, "Good.")]


def test_remote_ok_keeps_the_good_posting_beside_a_hostile_one() -> None:
    entry = _run_feed("remoteok", lambda: remoteok.parse(_remote_ok()))
    assert entry["error"] is None
    assert entry["stored"] == 2
    assert entry["items_skipped"] == 0


def _skipping(monkeypatch: pytest.MonkeyPatch, module: Any, name: str) -> None:
    """Make the hostile one fail its parse, as a future parser bug would."""
    real = getattr(module, name)

    def fail_on_hostile(*args: Any) -> Any:
        if "1" * 4_000 in json.dumps(args[-1] if name == "_posting" else args, default=str):
            raise ValueError("boom")
        return real(*args)

    monkeypatch.setattr(module, name, fail_on_hostile)


def test_remote_ok_skips_and_counts_an_item_that_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _skipping(monkeypatch, remoteok, "_posting")
    entry = _run_feed("remoteok", lambda: remoteok.parse(_remote_ok()))
    assert entry["error"] is None
    assert entry["stored"] == 1
    assert entry["items_skipped"] == 1


def test_we_work_remotely_skips_and_counts_an_item_that_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def rss() -> bytes:
        def item(n: int, text: str) -> str:
            return (f"<item><title>Example {n}: AI engineer</title>"
                    f"<link>https://weworkremotely.com/remote-jobs/{n}</link>"
                    f"<description>{escape(text)}</description></item>")  # fmt: skip

        return (f"<rss><channel>{item(1, LONG_DECIMAL)}{item(2, 'Good.')}</channel></rss>").encode()

    ok = _run_feed("weworkremotely", lambda: weworkremotely.parse(rss()))
    assert ok["error"] is None
    assert ok["stored"] == 2
    assert ok["items_skipped"] == 0

    real = weworkremotely._posting

    def fail(item: Any) -> Any:
        if "1" * 4_000 in (item.findtext("description") or ""):
            raise ValueError("boom")
        return real(item)

    monkeypatch.setattr(weworkremotely, "_posting", fail)
    bad = _run_feed("weworkremotely", lambda: weworkremotely.parse(rss()))
    assert bad["error"] is None
    assert bad["stored"] == 1
    assert bad["items_skipped"] == 1


def _comments() -> list[Any]:
    def comment(n: int, text: str) -> dict[str, Any]:
        return stub.hn_comment(n, 3002, f"SEEKING FREELANCER | Example {n} | AI engineer") | {
            "text": f"SEEKING FREELANCER | Example {n} | AI engineer<p>{text}"
        }

    return [comment(1, LONG_DECIMAL + " " + LONG_HEX), comment(2, "Good.")]


def test_hacker_news_keeps_the_good_comment_beside_a_hostile_one() -> None:
    entry = _run_feed(
        "hackernews_freelance", lambda: hackernews.parse("freelance", 3002, _comments())
    )
    assert entry["error"] is None
    assert entry["stored"] == 2
    assert entry["items_skipped"] == 0


def test_hacker_news_skips_and_counts_a_comment_that_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = hackernews.parse_comment

    def fail(thread: str, story_id: object, comment: Any) -> Any:
        if "1" * 4_000 in comment["text"]:
            raise ValueError("boom")
        return real(thread, story_id, comment)

    monkeypatch.setattr(hackernews, "parse_comment", fail)
    entry = _run_feed(
        "hackernews_freelance", lambda: hackernews.parse("freelance", 3002, _comments())
    )
    assert entry["error"] is None
    assert entry["stored"] == 1
    assert entry["items_skipped"] == 1


# --- the comment cap, the 429 wait ---------------------------------------------------------------


def test_the_comment_setting_is_capped_at_the_item_limit(migrated_url: str) -> None:
    ok = Settings(database_url=migrated_url, feeds_hn_max_comments=sources.ITEMS_MAX)  # type: ignore[call-arg]
    assert ok.feeds_hn_max_comments == sources.ITEMS_MAX
    with pytest.raises(ValidationError):
        Settings(database_url=migrated_url, feeds_hn_max_comments=sources.ITEMS_MAX + 1)  # type: ignore[call-arg]


@pytest.mark.parametrize("thread", ["freelance", "hiring"])
@pytest.mark.parametrize("count", [0, 2, 3, 4, 1200])
def test_hacker_news_reads_only_the_first_valid_ids_up_to_the_cap(thread: str, count: int) -> None:
    reads: list[int] = []
    ids = list(range(1, count + 1))
    title = (
        "Ask HN: Freelancer? Seeking freelancer? (May)"
        if thread == "freelance"
        else "Ask HN: Who is hiring? (May)"
    )

    async def get(url: str, hosts: frozenset[str], **options: Any) -> bytes:
        if url.endswith("whoishiring.json"):
            return b'{"submitted": [3002]}'
        if url.endswith("/3002.json"):
            return json.dumps(
                {
                    "by": "whoishiring",
                    "type": "story",
                    "id": 3002,
                    "kids": [True, "4", None, False, *ids],
                    "title": title,
                }
            ).encode()
        item_id = int(url.rsplit("/", 1)[1].removesuffix(".json"))
        reads.append(item_id)
        assert options == {"max_body": sources.HN_ITEM_MAX_BODY, "retry_rate_limited": False}
        return json.dumps(
            stub.hn_comment(
                item_id,
                3002,
                f"SEEKING FREELANCER | Example {item_id} | AI engineer"
                if thread == "freelance"
                else f"Example {item_id} | AI engineer",
            )
        ).encode()

    client = FeedClient(stub_base_url="http://feeds.invalid")
    client.get = get  # type: ignore[method-assign]
    found = asyncio.run(hackernews.read(client, thread, 3))
    assert reads == ids[:3]
    assert [posting.url for _, posting in found] == [
        f"https://news.ycombinator.com/item?id={item_id}" for item_id in ids[:3]
    ]
    assert found.skipped == 0


def test_the_wait_asked_by_a_429_is_kept_when_a_comment_stops_the_feed() -> None:
    async def go() -> FeedError:
        async def get(url: str, max_body: int) -> Any:
            raise FeedError("rate_limited", retryable=True, retry_after=7.0)

        client = FeedClient(stub_base_url="http://feeds.invalid")
        client._get = get  # type: ignore[method-assign]
        with pytest.raises(FeedError) as caught:
            await client.get("http://feeds.invalid/x", frozenset({"feeds.invalid"}),
                             retry_rate_limited=False)  # fmt: skip
        return caught.value

    assert asyncio.run(go()).retry_after == 7.0
