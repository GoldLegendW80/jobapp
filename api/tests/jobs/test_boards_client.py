"""Company boards read politely (app/jobs/boards/client.py), over httpx's MockTransport: one
request per board, two with SmartRecruiters' paging, a pause between two requests to one ATS,
a 429 that stops the ATS for the sweep, the error codes, redirects and sizes. No network, no
real wait (the pauses are recorded)."""

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.jobs.boards import ats
from app.jobs.boards.client import BoardClient

FIXTURES = Path(__file__).with_name("boards_fixtures")


class Clock:
    """A monotonic clock that only moves when the client sleeps."""

    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


def client(handler: Callable[[httpx.Request], httpx.Response], clock: Clock) -> BoardClient:
    return BoardClient(
        httpx.MockTransport(handler), pause_s=1.5, sleep=clock.sleep, monotonic=clock
    )


def read(c: BoardClient, *boards: tuple[str, str]) -> list[object]:
    async def go() -> list[object]:
        out: list[object] = []
        async with c:
            for kind, board in boards:
                try:
                    out.append(await c.read(kind, board))
                except ats.BoardError as exc:
                    out.append(exc.code)
        return out

    return asyncio.run(go())


def test_one_request_per_board_and_a_pause_between_two_of_one_ats() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.headers["user-agent"].startswith("jobapp/")
        assert "cookie" not in request.headers
        name = "ashby" if request.url.host == "api.ashbyhq.com" else "greenhouse"
        return httpx.Response(200, content=(FIXTURES / f"{name}.json").read_bytes())

    clock = Clock()
    results = read(
        client(handler, clock),
        ("ashby", "example-robotics"),
        ("ashby", "example-two"),
        ("greenhouse", "examplelabs"),
    )
    assert seen == [
        "https://api.ashbyhq.com/posting-api/job-board/example-robotics",
        "https://api.ashbyhq.com/posting-api/job-board/example-two",
        "https://boards-api.greenhouse.io/v1/boards/examplelabs/jobs?content=true",
    ]
    assert [r.requests for r in results] == [1, 1, 1]  # type: ignore[attr-defined]
    assert clock.slept == [1.5]  # only between the two Ashby boards


def test_smartrecruiters_pages_twice_at_most() -> None:
    offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offsets.append(request.url.params["offset"])
        start = int(request.url.params["offset"])
        content = [{"id": str(start + i), "name": f"AI Engineer {start + i}"} for i in range(100)]
        return httpx.Response(200, json={"totalFound": 950, "content": content})

    clock = Clock()
    (result,) = read(client(handler, clock), ("smartrecruiters", "ExampleConsulting"))
    assert offsets == ["0", "100"]
    assert (result.requests, len(result.parsed.postings)) == (2, 200)  # type: ignore[attr-defined]
    assert clock.slept == [1.5]


def test_smartrecruiters_one_page_when_it_holds_everything() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=(FIXTURES / "smartrecruiters.json").read_bytes())

    read(client(handler, Clock()), ("smartrecruiters", "ExampleConsulting"))
    assert calls == 1


def test_a_429_stops_that_ats_for_the_sweep() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "api.ashbyhq.com":
            return httpx.Response(429, headers={"retry-after": "60"})
        return httpx.Response(200, content=(FIXTURES / "lever.json").read_bytes())

    results = read(
        client(handler, Clock()),
        ("ashby", "one"),
        ("ashby", "two"),
        ("lever", "exampleworks"),
        ("ashby", "three"),
    )
    assert results[:2] == ["rate_limited", "host_rate_limited"]
    assert results[3] == "host_rate_limited"
    assert hosts == ["api.ashbyhq.com", "api.lever.co"]  # no retry, the next Ashby boards unasked


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (httpx.Response(404), "not_found"),
        (httpx.Response(403), "blocked"),
        (httpx.Response(401), "blocked"),
        (httpx.Response(500), "upstream_error"),
        (httpx.Response(204), "upstream_error"),
        (httpx.Response(200, content=b"<html>"), "invalid_response"),
        (httpx.Response(200, headers={"content-length": "999999999"}, content=b"{}"), "too_large"),
    ],
)
def test_error_codes(answer: httpx.Response, code: str) -> None:
    assert read(client(lambda _: answer, Clock()), ("greenhouse", "x")) == [code]


def test_a_body_too_large_is_refused_while_read() -> None:
    c = client(lambda _: httpx.Response(200, content=b"[" + b" " * 2000 + b"]"), Clock())
    c.max_body = 1000
    assert read(c, ("lever", "x")) == ["too_large"]


def test_timeouts_and_network_errors() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert read(client(slow, Clock()), ("lever", "x")) == ["timeout"]
    assert read(client(down, Clock()), ("lever", "x")) == ["network"]


def test_redirects_only_to_the_same_host_over_https() -> None:
    def same(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/old"):
            return httpx.Response(301, headers={"location": "/v0/postings/new?mode=json"})
        return httpx.Response(200, json=[])

    def elsewhere(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.lever.co"
        return httpx.Response(302, headers={"location": "https://evil.test/v0/postings/x"})

    (ok,) = read(client(same, Clock()), ("lever", "old"))
    assert ok.requests == 1  # type: ignore[attr-defined]
    assert read(client(elsewhere, Clock()), ("lever", "x")) == ["upstream_error"]


@pytest.mark.parametrize(
    "target",
    [
        "http://api.lever.co/v0/postings/new?mode=json",  # the same host, without TLS
        "https://api.lever.co:8443/v0/postings/new?mode=json",  # an explicit port
        "https://api.lever.co:443/v0/postings/new?mode=json",  # even the default one
        "https://user:secret@api.lever.co/v0/postings/new?mode=json",  # credentials
        "https://api.lever.co:notaport/v0/postings/new",  # a malformed port
        "https://api.lever.co:99999/v0/postings/new",  # a port out of range
        "//evil.test/v0/postings/new",  # another host, scheme relative
    ],
)
def test_a_redirect_elsewhere_than_the_same_https_host_is_refused(target: str) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(301, headers={"location": target})

    assert read(client(handler, Clock()), ("lever", "x")) == ["upstream_error"]
    assert seen == ["https://api.lever.co/v0/postings/x?mode=json"]  # never followed


def test_too_many_redirects_end_the_board() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/v0/postings/again?mode=json"})

    assert read(client(handler, Clock()), ("lever", "x")) == ["upstream_error"]


def test_one_answer_is_parsed_at_a_time_across_the_atss(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two ATSs read side by side never parse two answers at once (memory)."""
    parsing = {"now": 0, "most": 0}
    real = ats.parse

    def counted(kind: str, board: str, body: bytes | bytearray) -> ats.Parsed:
        parsing["now"] += 1
        parsing["most"] = max(parsing["most"], parsing["now"])
        try:
            time.sleep(0.02)  # in the worker thread: long enough for the other to overlap
            return real(kind, board, body)
        finally:
            parsing["now"] -= 1

    monkeypatch.setattr(ats, "parse", counted)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.lever.co":
            return httpx.Response(200, content=(FIXTURES / "lever.json").read_bytes())
        return httpx.Response(200, content=(FIXTURES / "greenhouse.json").read_bytes())

    async def go() -> list[object]:
        async with client(handler, Clock()) as c:
            return list(
                await asyncio.gather(c.read("lever", "exampleworks"), c.read("greenhouse", "x"))
            )

    lever, greenhouse = asyncio.run(go())
    assert parsing["most"] == 1
    assert len(lever.parsed.postings) == 2  # type: ignore[attr-defined]
    assert len(greenhouse.parsed.postings) == 2  # type: ignore[attr-defined]


def test_recruitee_reads_the_boards_own_subdomain() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=json.dumps({"offers": []}).encode())

    read(client(handler, Clock()), ("recruitee", "examplelabs"))
    assert seen == ["https://examplelabs.recruitee.com/api/offers/"]
