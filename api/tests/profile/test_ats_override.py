"""ATS_BASE_URL: the test-only override that points the board check at a stub (the CI e2e job runs
tests/ats_stub.py next to the api). Refused in production; the real hosts stay the default; the
stub itself answers the paths the checker asks."""

import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.config import ConfigError, load_settings
from app.main import create_app
from app.profile import boards
from app.profile.boards import BoardChecker, board_url
from tests.conftest import make_settings
from tests.profile.test_boards import ASHBY, json_response, make, run

URL = "postgresql+psycopg://jobapp:secret@db:5432/jobapp"
STUB = "http://atsstub:8080"


# --- settings -----------------------------------------------------------------------------------


def test_unset_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    assert load_settings().ats_base_url is None


@pytest.mark.parametrize("env", ["development", "test"])
def test_read_outside_production(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("ATS_BASE_URL", f" {STUB}/ ")
    assert load_settings().ats_base_url == STUB


def test_empty_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("ATS_BASE_URL", "  ")
    assert load_settings().ats_base_url is None


def test_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    monkeypatch.setenv("ATS_BASE_URL", STUB)
    with pytest.raises(ConfigError, match=r"ATS_BASE_URL .* must not be set in production"):
        load_settings()


@pytest.mark.parametrize(
    "value",
    ["atsstub:8080", "ftp://atsstub", "http://", "http://u:p@atsstub", "http://atsstub/?x=1"],
)
def test_refuses_a_value_that_is_not_a_plain_address(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("ATS_BASE_URL", value)
    with pytest.raises(ConfigError, match="ATS_BASE_URL"):
        load_settings()


# --- the checker --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ats", "url"),
    [
        ("ashby", f"{STUB}/ashby/posting-api/job-board/acme"),
        ("greenhouse", f"{STUB}/greenhouse/v1/boards/acme/jobs"),
        ("lever", f"{STUB}/lever/v0/postings/acme?mode=json"),
    ],
)
def test_board_url_under_the_base(ats: str, url: str) -> None:
    assert board_url(ats, "acme", STUB) == url
    assert board_url(ats, "acme", f"{STUB}/") == url


def test_the_checker_asks_the_base_and_keeps_redirects_on_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Acme"):
            return httpx.Response(301, headers={"Location": "/ashby/posting-api/job-board/acme"})
        return httpx.Response(200, json=ASHBY)

    checker, recorder, _ = make(handler, base_url=STUB)
    assert run(checker, "ashby", "Acme")["postings"] == 2
    assert [str(r.url) for r in recorder.requests] == [
        f"{STUB}/ashby/posting-api/job-board/Acme",
        f"{STUB}/ashby/posting-api/job-board/acme",
    ]


def test_an_org_prefix_sends_only_those_orgs_to_the_stub() -> None:
    """ATS_BASE_URL_ORG_PREFIX (jobs J5 scratch stacks): fake boards on the stub, real ones real."""
    checker, recorder, _ = make(
        lambda r: httpx.Response(200, json=ASHBY), base_url=STUB, org_prefix="fake-"
    )
    run(checker, "ashby", "fake-acme")
    run(checker, "ashby", "acme")
    assert [str(r.url) for r in recorder.requests] == [
        f"{STUB}/ashby/posting-api/job-board/fake-acme",
        "https://api.ashbyhq.com/posting-api/job-board/acme",
    ]


@pytest.mark.parametrize(
    "location",
    [
        "https://atsstub:8080/ashby/posting-api/job-board/acme",  # other scheme
        "http://atsstub:9090/ashby/posting-api/job-board/acme",  # other port
        "http://api.ashbyhq.com/posting-api/job-board/acme",  # other host
    ],
)
def test_a_redirect_off_the_base_is_refused(location: str) -> None:
    checker, recorder, _ = make(
        lambda r: httpx.Response(302, headers={"Location": location}), base_url=STUB
    )
    assert run(checker, "ashby", "acme")["error"] == "upstream_error"
    assert len(recorder.requests) == 1


def test_the_real_host_accepts_an_explicit_443_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Acme"):
            return httpx.Response(
                301, headers={"Location": "https://api.ashbyhq.com:443/posting-api/job-board/acme"}
            )
        return httpx.Response(200, json=ASHBY)

    checker, _, _ = make(handler)
    assert run(checker, "ashby", "Acme")["postings"] == 2


def test_the_app_checker_takes_the_setting(database_url: str) -> None:
    app: FastAPI = create_app(make_settings(database_url, ats_base_url=STUB))
    made = boards.checker(type("R", (), {"app": app})())  # type: ignore[arg-type]
    assert made.base_url == STUB
    plain = create_app(make_settings(database_url))
    assert boards.checker(type("R", (), {"app": plain})()).base_url is None  # type: ignore[arg-type]
    app.state.engine.dispose()
    plain.state.engine.dispose()


def test_json_response_helper_is_shared() -> None:
    # Guard against the import above silently changing meaning.
    assert json_response(ASHBY)(httpx.Request("GET", "http://x")).status_code == 200


# --- the stub -----------------------------------------------------------------------------------


def _stub() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "tests" / "ats_stub.py"
    spec = importlib.util.spec_from_file_location("ats_stub", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def stub_url() -> Any:
    module = _stub()
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize(
    ("ats", "org", "expected"),
    [
        ("ashby", "mistral.ai", {"exists": True, "postings": 3, "company_name": None}),
        ("ashby", "Mistral.AI", {"exists": True, "postings": 3, "company_name": None}),
        ("greenhouse", "anthropic", {"exists": True, "postings": 3, "company_name": "Example AI"}),
        ("lever", "example", {"exists": True, "postings": 3, "company_name": None}),
        ("ashby", "jobapp-no-such-board", {"exists": False, "error": "not_found"}),
        ("lever", "nope", {"exists": False, "error": "not_found"}),
    ],
)
def test_the_checker_against_the_stub(
    stub_url: str, ats: str, org: str, expected: dict[str, Any]
) -> None:
    answer = run(BoardChecker(base_url=stub_url), ats, org)
    assert {k: answer[k] for k in expected} == expected


def test_the_stub_health_and_unknown_paths(stub_url: str) -> None:
    assert httpx.get(f"{stub_url}/healthz").json() == {"ok": True}
    unknown = httpx.get(f"{stub_url}/workday/x")
    assert unknown.status_code == 404
    assert json.loads(unknown.text) == {"error": "unknown path"}
