"""The board check (boards.py): each ATS against a fake transport (no network), every failure mode,
the org validation that runs before any request, the redirect policy, the rate limit, the cache,
and the endpoint behind the session guard and CSRF, against a real Postgres."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.identity.deps import CSRF_REFUSED, UNAUTHORIZED
from app.profile import boards
from app.profile.boards import (
    CACHE_TTL_S,
    MAX_BODY,
    RATE_LIMIT,
    RATE_WINDOW_S,
    TOO_MANY,
    BoardChecker,
    board_url,
)
from tests.identity.api_helpers import Api

PATH = "/api/profile/boards/check"

ASHBY = {
    "apiVersion": "1",
    "jobs": [
        {"title": "Applied AI Engineer", "isListed": True},
        {"title": "Research Engineer"},
        {"title": "Hidden", "isListed": False},
    ],
}
GREENHOUSE = {
    "jobs": [
        {"title": "AI Engineer", "company_name": "Anthropic"},
        {"title": "Forward Deployed Engineer", "company_name": "Anthropic"},
    ],
    "meta": {"total": 2},
}
LEVER = [{"text": "ML Engineer"}, {"text": "Data Engineer"}, {"text": "Designer"}]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    """A MockTransport whose handler is swappable and which records every request."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.handler(request)

        return httpx.MockTransport(handle)


def json_response(payload: Any, status: int = 200) -> Handler:
    return lambda _request: httpx.Response(status, json=payload)


def make(handler: Handler, **kwargs: Any) -> tuple[BoardChecker, Recorder, FakeClock]:
    recorder = Recorder(handler)
    clock = FakeClock()
    checker = BoardChecker(transport=recorder.transport(), clock=clock, **kwargs)
    return checker, recorder, clock


def run(checker: BoardChecker, ats: str, org: str) -> dict[str, Any]:
    return asyncio.run(checker.check(ats, org)).model_dump()


# --- addresses ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ats", "org", "url"),
    [
        ("ashby", "mistral.ai", "https://api.ashbyhq.com/posting-api/job-board/mistral.ai"),
        ("greenhouse", "anthropic", "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs"),
        ("lever", "spotify", "https://api.lever.co/v0/postings/spotify?mode=json"),
    ],
)
def test_board_url_is_the_engines(ats: str, org: str, url: str) -> None:
    assert board_url(ats, org) == url


def test_board_url_encodes_the_org_as_one_segment() -> None:
    assert board_url("ashby", "a/b?c#d") == (
        "https://api.ashbyhq.com/posting-api/job-board/a%2Fb%3Fc%23d"
    )


def test_board_url_refuses_an_unknown_ats() -> None:
    with pytest.raises(ValueError, match="unknown ATS"):
        board_url("workday", "x")


# --- each ATS -----------------------------------------------------------------------------------


def test_ashby_counts_listed_postings() -> None:
    checker, recorder, _ = make(json_response(ASHBY))
    assert run(checker, "ashby", "mistral.ai") == {
        "exists": True,
        "postings": 2,
        "company_name": None,
        "error": None,
    }
    [request] = recorder.requests
    assert str(request.url) == "https://api.ashbyhq.com/posting-api/job-board/mistral.ai"
    assert request.method == "GET"
    assert request.headers["accept"] == "application/json"
    assert "cookie" not in request.headers


def test_greenhouse_counts_jobs_and_names_the_company() -> None:
    checker, recorder, _ = make(json_response(GREENHOUSE))
    assert run(checker, "greenhouse", "anthropic") == {
        "exists": True,
        "postings": 2,
        "company_name": "Anthropic",
        "error": None,
    }
    assert recorder.requests[0].url.host == "boards-api.greenhouse.io"


def test_greenhouse_without_company_name() -> None:
    checker, _, _ = make(json_response({"jobs": [{"title": "x"}, "odd"]}))
    assert run(checker, "greenhouse", "acme")["company_name"] is None


def test_lever_counts_the_list() -> None:
    checker, recorder, _ = make(json_response(LEVER))
    assert run(checker, "lever", "spotify") == {
        "exists": True,
        "postings": 3,
        "company_name": None,
        "error": None,
    }
    assert recorder.requests[0].url.params["mode"] == "json"


@pytest.mark.parametrize("ats", ["ashby", "greenhouse", "lever"])
def test_an_empty_board_exists_with_zero_postings(ats: str) -> None:
    payload: Any = [] if ats == "lever" else {"jobs": []}
    checker, _, _ = make(json_response(payload))
    assert run(checker, ats, "quiet") == {
        "exists": True,
        "postings": 0,
        "company_name": None,
        "error": None,
    }


def test_lever_error_object_is_not_found() -> None:
    checker, _, _ = make(json_response({"ok": False, "error": "Document not found"}))
    assert run(checker, "lever", "nope")["error"] == "not_found"


# --- failures -----------------------------------------------------------------------------------


@pytest.mark.parametrize("ats", ["ashby", "greenhouse", "lever"])
def test_404_is_not_found(ats: str) -> None:
    checker, _, _ = make(json_response({"error": "not found"}, 404))
    assert run(checker, ats, "no-such-company") == {
        "exists": False,
        "postings": None,
        "company_name": None,
        "error": "not_found",
    }


@pytest.mark.parametrize("status", [401, 403, 429])
def test_refusals_are_blocked(status: int) -> None:
    checker, _, _ = make(json_response({}, status))
    assert run(checker, "ashby", "acme")["error"] == "blocked"


@pytest.mark.parametrize("status", [500, 502, 503, 504, 400, 410, 204])
def test_other_statuses_are_upstream_errors(status: int) -> None:
    checker, _, _ = make(lambda _r: httpx.Response(status))
    answer = run(checker, "greenhouse", "acme")
    assert answer == {
        "exists": False,
        "postings": None,
        "company_name": None,
        "error": "upstream_error",
    }


@pytest.mark.parametrize(
    "body",
    [
        b"<html>Just a moment...</html>",
        b"",
        b"{",
        b'{"jobs": {"not": "a list"}}',
        b'"jobs"',
        b"null",
        b"[1, 2]",  # a list is Lever's shape, not Ashby's
        b"[" * 100_000 + b"]" * 100_000,  # deep nesting
    ],
)
def test_malformed_bodies_are_upstream_errors(body: bytes) -> None:
    checker, _, _ = make(lambda _r: httpx.Response(200, content=body))
    assert run(checker, "ashby", "acme")["error"] == "upstream_error"


def test_lever_object_without_ok_false_is_upstream_error() -> None:
    checker, _, _ = make(json_response({"jobs": []}))
    assert run(checker, "lever", "acme")["error"] == "upstream_error"


def test_a_body_over_the_limit_is_refused_while_streaming() -> None:
    def huge(_r: httpx.Request) -> httpx.Response:
        async def chunks() -> Any:
            yield b'{"jobs": ['
            for _ in range(MAX_BODY // (1024 * 1024) + 2):
                yield b" " * (1024 * 1024)
            yield b"]}"

        return httpx.Response(200, content=chunks())

    checker, _, _ = make(huge)
    assert run(checker, "ashby", "acme")["error"] == "upstream_error"


def test_a_declared_length_over_the_limit_is_refused() -> None:
    checker, _, _ = make(
        lambda _r: httpx.Response(
            200, headers={"Content-Length": str(MAX_BODY + 1)}, content=b'{"jobs": []}'
        )
    )
    assert run(checker, "ashby", "acme")["error"] == "upstream_error"


def test_a_read_timeout_is_timeout() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    checker, recorder, _ = make(slow)
    assert run(checker, "ashby", "acme") == {
        "exists": False,
        "postings": None,
        "company_name": None,
        "error": "timeout",
    }
    assert len(recorder.requests) == 1  # no retry on a timeout


def test_a_connect_timeout_is_timeout() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    checker, _, _ = make(slow)
    assert run(checker, "lever", "acme")["error"] == "timeout"


def test_the_whole_exchange_has_a_deadline() -> None:
    """A server that keeps the connection busy past the timeout is cut, whatever httpx does."""

    async def stall(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=ASHBY)  # pragma: no cover, cut before

    checker = BoardChecker(transport=httpx.MockTransport(stall), timeout_s=0.05)
    assert run(checker, "ashby", "acme")["error"] == "timeout"


def test_a_connect_error_is_retried_once() -> None:
    calls = []

    def flaky(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, json=ASHBY)

    checker, _, _ = make(flaky)
    assert run(checker, "ashby", "acme")["postings"] == 2
    assert len(calls) == 2


def test_two_connect_errors_are_an_upstream_error() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    checker, recorder, _ = make(down)
    assert run(checker, "ashby", "acme")["error"] == "upstream_error"
    assert len(recorder.requests) == 2


def test_another_transport_error_is_an_upstream_error_without_retry() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("garbage", request=request)

    checker, recorder, _ = make(broken)
    assert run(checker, "ashby", "acme")["error"] == "upstream_error"
    assert len(recorder.requests) == 1


# --- redirects ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example.org/jobs",
        "http://api.ashbyhq.com/posting-api/job-board/acme",  # downgrade to http
        "https://api.ashbyhq.com.evil.example.org/x",
        "https://api.ashbyhq.com:8443/x",
        "https://user:pw@api.ashbyhq.com/x",
        "//evil.example.org/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/api/health",
        "file:///etc/passwd",
        "",
    ],
)
def test_a_redirect_off_the_ats_host_is_refused_unrequested(location: str) -> None:
    headers = {"Location": location} if location else {}
    checker, recorder, _ = make(lambda _r: httpx.Response(302, headers=headers))
    assert run(checker, "ashby", "acme")["error"] == "upstream_error"
    assert [r.url.host for r in recorder.requests] == ["api.ashbyhq.com"]


def test_a_redirect_on_the_same_host_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Acme"):
            return httpx.Response(301, headers={"Location": "/posting-api/job-board/acme"})
        return httpx.Response(200, json=ASHBY)

    checker, recorder, _ = make(handler)
    assert run(checker, "ashby", "Acme")["postings"] == 2
    assert [r.url.path for r in recorder.requests] == [
        "/posting-api/job-board/Acme",
        "/posting-api/job-board/acme",
    ]


def test_a_redirect_loop_stops() -> None:
    checker, recorder, _ = make(
        lambda r: httpx.Response(307, headers={"Location": str(r.url)}),
    )
    assert run(checker, "lever", "loop")["error"] == "upstream_error"
    assert len(recorder.requests) == boards.MAX_REDIRECTS + 1


# --- cache --------------------------------------------------------------------------------------


def test_a_found_board_is_cached_for_ten_minutes_ignoring_case() -> None:
    checker, recorder, clock = make(json_response(ASHBY))
    first = run(checker, "ashby", "mistral.ai")
    assert run(checker, "ashby", "Mistral.AI") == first
    assert len(recorder.requests) == 1
    clock.now += CACHE_TTL_S - 1
    run(checker, "ashby", "mistral.ai")
    assert len(recorder.requests) == 1
    clock.now += 2
    run(checker, "ashby", "mistral.ai")
    assert len(recorder.requests) == 2


def test_the_cache_key_includes_the_ats() -> None:
    checker, recorder, _ = make(json_response({"jobs": []}))
    run(checker, "ashby", "acme")
    run(checker, "greenhouse", "acme")
    assert len(recorder.requests) == 2


def test_not_found_is_cached_failures_are_not() -> None:
    checker, recorder, _ = make(json_response({}, 404))
    run(checker, "ashby", "gone")
    run(checker, "ashby", "gone")
    assert len(recorder.requests) == 1

    recorder.handler = json_response({}, 503)
    run(checker, "ashby", "down")
    recorder.handler = json_response(ASHBY)
    assert run(checker, "ashby", "down")["postings"] == 2  # retried for real
    assert len(recorder.requests) == 3


def test_the_cache_is_bounded() -> None:
    checker, _, _ = make(json_response({"jobs": []}))
    for index in range(boards.CACHE_MAX + 5):
        run(checker, "ashby", f"org-{index}")
    assert len(checker._cache) == boards.CACHE_MAX
    assert checker.cached("ashby", "org-0") is None
    assert checker.cached("ashby", f"org-{boards.CACHE_MAX + 4}") is not None


# --- the endpoint -------------------------------------------------------------------------------


@pytest.fixture
def fake(app: FastAPI) -> tuple[Recorder, FakeClock]:
    """The app's checker on a fake transport and a fake monotonic clock."""
    recorder = Recorder(json_response(ASHBY))
    clock = FakeClock()
    app.state.board_checker = BoardChecker(transport=recorder.transport(), clock=clock)
    return recorder, clock


def check(api: Api, token: str | None, body: Any, **kwargs: Any) -> httpx.Response:
    return api.post(PATH, token=token, json=body, **kwargs)


def test_endpoint_answers_the_check(api: Api, alice: str, fake: tuple[Recorder, FakeClock]) -> None:
    response = check(api, alice, {"ats": "ashby", "org": "mistral.ai"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "exists": True,
        "postings": 2,
        "company_name": None,
        "error": None,
    }
    assert response.headers["cache-control"] == "no-store"
    assert len(fake[0].requests) == 1


def test_endpoint_trims_the_org(api: Api, alice: str, fake: tuple[Recorder, FakeClock]) -> None:
    assert check(api, alice, {"ats": "lever", "org": "  acme  "}).status_code == 200
    assert fake[0].requests[0].url.path == "/v0/postings/acme"


def test_endpoint_reports_not_found(api: Api, alice: str, fake: tuple[Recorder, FakeClock]) -> None:
    fake[0].handler = json_response({}, 404)
    response = check(api, alice, {"ats": "greenhouse", "org": "no-such-board-xyz"})
    assert response.status_code == 200
    assert response.json()["error"] == "not_found"
    assert response.json()["exists"] is False


@pytest.mark.parametrize(
    "org",
    [
        "../admin",
        "..",
        ".",
        "a..b",
        "acme/../../etc",
        "acme/jobs",
        "acme\\jobs",
        ".hidden",
        "-dash",
        "acme?x=1",
        "acme#frag",
        "acme%2F..",
        "acme corp",
        "évian",
        "",
        "   ",
        "a" * 101,
        "acme\x00",
        "http://evil.example.org",
        "evil.example.org@api",
    ],
)
def test_endpoint_refuses_a_bad_org_before_any_request(
    api: Api, alice: str, fake: tuple[Recorder, FakeClock], org: str
) -> None:
    response = check(api, alice, {"ats": "ashby", "org": org})
    assert response.status_code == 422, response.text
    assert response.json()["detail"].startswith("org:")
    assert org not in response.json()["detail"] or org in ("", "   ", ".")
    assert fake[0].requests == []


@pytest.mark.parametrize(
    "body",
    [
        {"ats": "workday", "org": "acme"},
        {"ats": "ASHBY", "org": "acme"},
        {"ats": "ashby"},
        {"org": "acme"},
        {"ats": "ashby", "org": 42},
        {"ats": "ashby", "org": ["acme"]},
        {"ats": "ashby", "org": "acme", "host": "evil.example.org"},
        [],
        "ashby",
    ],
)
def test_endpoint_refuses_a_bad_body(
    api: Api, alice: str, fake: tuple[Recorder, FakeClock], body: Any
) -> None:
    assert check(api, alice, body).status_code == 422
    assert fake[0].requests == []


def test_endpoint_refuses_invalid_json(
    api: Api, alice: str, fake: tuple[Recorder, FakeClock]
) -> None:
    response = api.post(
        PATH, token=alice, content=b"{nope", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert fake[0].requests == []


def test_endpoint_needs_a_session(api: Api, fake: tuple[Recorder, FakeClock]) -> None:
    response = check(api, None, {"ats": "ashby", "org": "acme"})
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}
    assert fake[0].requests == []


def test_endpoint_refuses_a_forged_session(api: Api, fake: tuple[Recorder, FakeClock]) -> None:
    assert check(api, "x" * 43, {"ats": "ashby", "org": "acme"}).status_code == 401
    assert fake[0].requests == []


def test_endpoint_needs_the_csrf_header(
    api: Api, alice: str, fake: tuple[Recorder, FakeClock]
) -> None:
    response = check(api, alice, {"ats": "ashby", "org": "acme"}, csrf=False)
    assert response.status_code == 403
    assert response.json() == {"detail": CSRF_REFUSED}
    assert fake[0].requests == []


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_endpoint_is_post_only(
    api: Api, alice: str, fake: tuple[Recorder, FakeClock], method: str
) -> None:
    assert api.request(method, PATH, token=alice).status_code == 405


def test_rate_limit_per_user(
    api: Api, alice: str, bob: str, fake: tuple[Recorder, FakeClock]
) -> None:
    recorder, clock = fake
    for index in range(RATE_LIMIT):
        response = check(api, alice, {"ats": "ashby", "org": f"org-{index}"})
        assert response.status_code == 200, response.text
        clock.now += 1
    refused = check(api, alice, {"ats": "ashby", "org": "one-more"})
    assert refused.status_code == 429
    assert refused.json() == {"detail": TOO_MANY}
    # The oldest of the ten was 10 s ago: it leaves the window in 50 s.
    assert refused.headers["retry-after"] == str(int(RATE_WINDOW_S) - RATE_LIMIT)
    assert len(recorder.requests) == RATE_LIMIT  # the refused one never reached the ATS

    # Another user has a budget of their own.
    assert check(api, bob, {"ats": "ashby", "org": "bobs-board"}).status_code == 200

    # Refused checks do not count: once the window moves, Alice checks again.
    clock.now += RATE_WINDOW_S
    assert check(api, alice, {"ats": "ashby", "org": "later"}).status_code == 200


def test_rate_limit_counts_cache_hits_too(
    api: Api, alice: str, fake: tuple[Recorder, FakeClock]
) -> None:
    for _ in range(RATE_LIMIT):
        assert check(api, alice, {"ats": "ashby", "org": "same"}).status_code == 200
    assert check(api, alice, {"ats": "ashby", "org": "same"}).status_code == 429
    assert len(fake[0].requests) == 1


def test_cache_is_shared_between_users(
    api: Api, alice: str, bob: str, fake: tuple[Recorder, FakeClock]
) -> None:
    first = check(api, alice, {"ats": "ashby", "org": "mistral.ai"}).json()
    second = check(api, bob, {"ats": "ashby", "org": "MISTRAL.AI"}).json()
    assert first == second
    assert len(fake[0].requests) == 1


def test_endpoint_reports_each_failure_as_200(
    api: Api, alice: str, fake: tuple[Recorder, FakeClock]
) -> None:
    recorder, _ = fake

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    cases: list[tuple[Handler, str]] = [
        (timeout, "timeout"),
        (json_response({}, 503), "upstream_error"),
        (lambda _r: httpx.Response(200, content=b"not json"), "upstream_error"),
        (json_response({}, 403), "blocked"),
        (
            lambda _r: httpx.Response(302, headers={"Location": "https://evil.example.org/"}),
            "upstream_error",
        ),
    ]
    for index, (handler, error) in enumerate(cases):
        recorder.handler = handler
        response = check(api, alice, {"ats": "ashby", "org": f"case-{index}"})
        assert response.status_code == 200
        assert response.json() == {
            "exists": False,
            "postings": None,
            "company_name": None,
            "error": error,
        }
    assert all(r.url.host == "api.ashbyhq.com" for r in recorder.requests)


def test_the_app_makes_its_own_checker(app: FastAPI) -> None:
    assert getattr(app.state, "board_checker", None) is None
    request = type("R", (), {"app": app})()
    made = boards.checker(request)  # type: ignore[arg-type]
    assert isinstance(made, BoardChecker)
    assert boards.checker(request) is made  # type: ignore[arg-type]
    assert made.transport is None


def test_rate_limit_forgets_idle_users() -> None:
    checker, _, clock = make(json_response(ASHBY))
    for _ in range(boards.CACHE_MAX + 1):
        assert checker.take(uuid.uuid4()) is None
    clock.now += RATE_WINDOW_S + 1
    assert checker.take(uuid.uuid4()) is None
    assert len(checker._calls) == 1
