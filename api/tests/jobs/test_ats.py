"""app/jobs/ats.py: the parsers against recorded bodies of six real boards (two per ATS) and the
engine's own output on them, every odd shape, and the client's HTTP behaviour (retries, limits,
redirects, the test-only override) over httpx.MockTransport."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.jobs import ats

FIXTURES = Path(__file__).parent / "fixtures" / "ats"
PARIS = ZoneInfo("Europe/Paris")
BOARDS = [
    "ashby-mistral.ai",
    "ashby-openai",
    "greenhouse-anthropic",
    "greenhouse-databricks",
    "lever-palantir",
    "lever-sonarsource",
]
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# --- parsers against the engine -----------------------------------------------------------------


@pytest.mark.parametrize("name", BOARDS)
def test_parse_matches_the_engine_on_recorded_boards(name: str) -> None:
    board_ats = name.split("-", 1)[0]
    result = ats.parse(board_ats, load(name), PARIS)
    assert [list(p.engine_tuple()) for p in result.postings] == EXPECTED[name]
    assert result.invalid == 0
    assert len(result.postings) >= 3


def test_fixtures_hold_kept_and_left_out_postings_and_no_email() -> None:
    for name in BOARDS:
        raw = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
        emails = {m for m in ats.re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", raw)}
        assert emails <= {"jobs@example.org"}, name


def test_decode_reads_raw_bytes() -> None:
    raw = (FIXTURES / "lever-sonarsource.json").read_bytes()
    assert [list(p.engine_tuple()) for p in ats.decode("lever", raw, PARIS).postings] == (
        EXPECTED["lever-sonarsource"]
    )


# --- ashby --------------------------------------------------------------------------------------


def ashby_job(**fields: Any) -> dict[str, Any]:
    job = {
        "title": "AI Engineer",
        "location": "Paris",
        "secondaryLocations": [],
        "isRemote": False,
        "isListed": True,
        "jobUrl": "https://jobs.ashbyhq.com/acme/1",
        "publishedAt": "2026-10-01T08:00:00.000+00:00",
        "descriptionPlain": "Build agents.",
    }
    job.update(fields)
    return job


def test_ashby_locations_joined_in_order_without_duplicates_and_remote_last() -> None:
    job = ashby_job(
        location="Paris",
        secondaryLocations=[{"location": "London"}, {"location": "Paris"}, {"location": ""}, {}],
        isRemote=True,
    )
    [posting] = ats.parse("ashby", {"jobs": [job]}, PARIS).postings
    assert posting.location == "Paris, London, Remote"
    assert posting.remote is True
    assert posting.published == "2026-10-01"


def test_ashby_unlisted_skipped_missing_fields_counted() -> None:
    jobs = [
        ashby_job(isListed=False),
        ashby_job(title=""),
        ashby_job(jobUrl=None),
        "not an object",
        ashby_job(title="Kept", descriptionPlain=None, publishedAt=None, location=None),
    ]
    result = ats.parse("ashby", {"jobs": jobs}, PARIS)
    assert [p.title for p in result.postings] == ["Kept"]
    assert result.postings[0].text == ""
    assert result.postings[0].published == ""
    assert result.postings[0].location == ""
    assert result.invalid == 3


@pytest.mark.parametrize("payload", [[], {"jobs": None}, {"postings": []}, "x", None])
def test_ashby_and_greenhouse_wrong_shape(payload: Any) -> None:
    for name in ("ashby", "greenhouse"):
        with pytest.raises(ats.BoardError) as caught:
            ats.parse(name, payload, PARIS)
        assert caught.value.code == "invalid_response"


def test_text_made_storable_unicode_kept() -> None:
    job = ashby_job(title="AI Engineer 東京 🤖 é", descriptionPlain="a\x00b \ud800 c مرحبا")
    [posting] = ats.parse("ashby", {"jobs": [job]}, PARIS).postings
    assert posting.title == "AI Engineer 東京 🤖 é"
    assert posting.text == "ab  c مرحبا"


# --- greenhouse ---------------------------------------------------------------------------------


def test_greenhouse_text_unescaped_twice_and_tags_dropped() -> None:
    job = {
        "title": "AI Engineer",
        "location": {"name": "Remote, Europe"},
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
        "first_published": None,
        "updated_at": "2026-09-30T10:00:00-04:00",
        "content": "&lt;p&gt;Agents &amp;amp; tools&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python",
    }
    [posting] = ats.parse("greenhouse", {"jobs": [job]}, PARIS).postings
    assert posting.text == "Agents & tools Python"
    assert posting.published == "2026-09-30"
    assert posting.remote is True


def test_greenhouse_missing_location_and_dates() -> None:
    job = {"title": "T", "absolute_url": "https://x.example/1"}
    [posting] = ats.parse("greenhouse", {"jobs": [job]}, PARIS).postings
    assert (posting.location, posting.published, posting.text) == ("", "", "")


# --- lever --------------------------------------------------------------------------------------


def lever_job(**fields: Any) -> dict[str, Any]:
    job = {
        "text": "AI Engineer",
        "categories": {"location": "Paris"},
        "hostedUrl": "https://jobs.lever.co/acme/1",
        "createdAt": 1_790_000_000_000,
        "descriptionPlain": "Agents.",
        "workplaceType": "onsite",
    }
    job.update(fields)
    return job


def test_lever_date_in_the_sweep_time_zone() -> None:
    # 2026-09-30T22:30:00Z is already October 1st in Paris.
    millis = 1_790_807_400_000
    [posting] = ats.parse("lever", [lever_job(createdAt=millis)], PARIS).postings
    assert posting.published == "2026-10-01"
    [utc] = ats.parse("lever", [lever_job(createdAt=millis)], ZoneInfo("UTC")).postings
    assert utc.published == "2026-09-30"


@pytest.mark.parametrize("created", [None, "x", True, -1e20, 0])
def test_lever_odd_created_at(created: Any) -> None:
    [posting] = ats.parse("lever", [lever_job(createdAt=created)], PARIS).postings
    assert posting.published in ("", "1970-01-01")


def test_lever_remote_flag_and_missing_categories() -> None:
    result = ats.parse(
        "lever",
        [lever_job(workplaceType="remote"), lever_job(categories=None), lever_job(text=None)],
        PARIS,
    )
    assert [p.remote for p in result.postings] == [True, False]
    assert result.postings[1].location == ""
    assert result.invalid == 1


def test_lever_error_object_and_wrong_shape() -> None:
    with pytest.raises(ats.BoardError) as caught:
        ats.parse("lever", {"ok": False, "error": "Document not found"}, PARIS)
    assert caught.value.code == "not_found"
    with pytest.raises(ats.BoardError) as caught:
        ats.parse("lever", {"jobs": []}, PARIS)
    assert caught.value.code == "invalid_response"


def test_api_path_encodes_org_and_refuses_unknown_ats() -> None:
    assert ats.api_path("ashby", "a/b") == "/posting-api/job-board/a%2Fb"
    assert ats.api_path("greenhouse", "x") == "/v1/boards/x/jobs?content=true"
    assert ats.api_path("lever", "x") == "/v0/postings/x?mode=json"
    with pytest.raises(ValueError, match="unknown ATS"):
        ats.api_path("workday", "x")


# --- the client ---------------------------------------------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def fetch(handler: Handler, ats_name: str = "lever", org: str = "acme", **options: Any) -> Any:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def go() -> Any:
        client = ats.AtsClient(
            tz=PARIS, transport=httpx.MockTransport(handler), sleep=sleep, **options
        )
        async with client:
            return await client.fetch_board(ats_name, org)

    try:
        return run(go()), sleeps
    except ats.BoardError as exc:
        return exc, sleeps


def ok_lever(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=[lever_job()])


def test_fetch_ok_sends_user_agent_to_the_fixed_host() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok_lever(request)

    result, sleeps = fetch(handler)
    assert [p.title for p in result.postings] == ["AI Engineer"]
    assert str(seen[0].url) == "https://api.lever.co/v0/postings/acme?mode=json"
    assert seen[0].headers["user-agent"].startswith("jobapp-sweep/")
    assert sleeps == []


@pytest.mark.parametrize(
    ("status", "code"),
    [(404, "not_found"), (401, "blocked"), (403, "blocked"), (418, "upstream_error")],
)
def test_fetch_final_statuses_are_not_retried(status: int, code: str) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status)

    error, _ = fetch(handler)
    assert isinstance(error, ats.BoardError)
    assert error.code == code
    assert len(calls) == 1


def test_fetch_429_then_ok_waits_retry_after_capped() -> None:
    answers = iter([httpx.Response(429, headers={"Retry-After": "120"}), None])

    def handler(request: httpx.Request) -> httpx.Response:
        answer = next(answers)
        return answer if answer is not None else ok_lever(request)

    result, sleeps = fetch(handler)
    assert len(result.postings) == 1
    assert sleeps == [ats.RETRY_AFTER_MAX_S]


@pytest.mark.parametrize(
    ("status", "code"), [(429, "rate_limited"), (500, "upstream_error"), (503, "upstream_error")]
)
def test_fetch_retries_then_fails(status: int, code: str) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status)

    error, sleeps = fetch(handler, backoff_s=0.5)
    assert error.code == code
    assert len(calls) == ats.ATTEMPTS
    assert sleeps == [0.5, 1.0]


def test_fetch_timeout_and_connection_errors_are_retried() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    error, sleeps = fetch(timeout)
    assert error.code == "timeout"
    assert len(sleeps) == ats.ATTEMPTS - 1

    answers = iter([httpx.ConnectError("refused"), None])

    def flaky(request: httpx.Request) -> httpx.Response:
        answer = next(answers)
        if answer is not None:
            raise answer
        return ok_lever(request)

    result, _ = fetch(flaky)
    assert len(result.postings) == 1


def test_fetch_malformed_json_and_wrong_shape() -> None:
    error, _ = fetch(lambda r: httpx.Response(200, content=b'{"jobs": ['), "ashby")
    assert error.code == "invalid_response"
    error, _ = fetch(lambda r: httpx.Response(200, json={"jobs": 3}), "greenhouse")
    assert error.code == "invalid_response"


def test_fetch_body_size_limit() -> None:
    big = b"[" + b" " * 2000 + b"]"
    error, _ = fetch(lambda r: httpx.Response(200, content=big), max_body=1000)
    assert error.code == "too_large"
    declared = httpx.Response(200, content=b"[]", headers={"Content-Length": "99999999999"})
    error, _ = fetch(lambda r: declared, max_body=1000)
    assert error.code == "too_large"


def test_fetch_redirects_same_host_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/old"):
            return httpx.Response(301, headers={"Location": "/v0/postings/new?mode=json"})
        return ok_lever(request)

    result, _ = fetch(handler, org="old")
    assert len(result.postings) == 1

    for target in (
        "https://evil.example.net/x",
        "http://api.lever.co/v0/postings/new",
        "https://user:pw@api.lever.co/x",
        "",
    ):
        headers = {"Location": target} if target else {}
        error, _ = fetch(lambda r, h=headers: httpx.Response(302, headers=h))
        assert error.code == "upstream_error", target

    loops = fetch(lambda r: httpx.Response(302, headers={"Location": "/again"}))[0]
    assert loops.code == "upstream_error"


def test_stub_for_every_org_or_only_a_prefix() -> None:
    every = ats.AtsClient(tz=PARIS, stub_base_url="http://stub:8099")
    assert every.board_url("lever", "palantir") == (
        "http://stub:8099/lever/v0/postings/palantir?mode=json",
        "stub",
    )
    client = ats.AtsClient(tz=PARIS, stub_base_url="http://fakeats:8099/", stub_org_prefix="fake-")
    assert client.board_url("ashby", "fake-ok") == (
        "http://fakeats:8099/ashby/posting-api/job-board/fake-ok",
        "fakeats",
    )
    assert client.board_url("ashby", "mistral.ai") == (
        "https://api.ashbyhq.com/posting-api/job-board/mistral.ai",
        "api.ashbyhq.com",
    )
    plain_client = ats.AtsClient(tz=PARIS)
    assert plain_client.board_url("lever", "fake-ok")[1] == "api.lever.co"


def test_client_outside_context_manager_is_an_error() -> None:
    client = ats.AtsClient(tz=PARIS)
    with pytest.raises(RuntimeError, match="context manager"):
        run(client.fetch_board("lever", "x"))


def test_retry_after_parsing() -> None:
    assert ats._retry_after("5") == 5.0
    assert ats._retry_after(None) is None
    assert ats._retry_after("soon") is None
    assert ats._retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert ats._retry_after("Wed, 21 Oct 2015 07:28:00") is None


def test_published_date() -> None:
    assert str(ats.published_date("2026-10-01")) == "2026-10-01"
    assert ats.published_date("") is None
    assert ats.published_date("2026-13-01") is None


def test_the_body_gate_caps_boards_in_flight() -> None:
    in_flight = {"now": 0, "max": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        await asyncio.sleep(0.02)
        in_flight["now"] -= 1
        return httpx.Response(200, json=[lever_job()])

    async def go() -> list[Any]:
        gate = asyncio.Semaphore(2)
        client = ats.AtsClient(tz=PARIS, transport=httpx.MockTransport(handler), body_gate=gate)
        async with client:
            return await asyncio.gather(*(client.fetch_board("lever", f"o{n}") for n in range(6)))

    results = run(go())
    assert len(results) == 6
    assert in_flight["max"] == 2
