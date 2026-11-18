"""B28 (owner, 2026-11-17: "It must work, even if i'm not logged in", #792): browserd's web_fetch
reads one public page signed out, without the tab: GET only, through the egress proxy, bounded,
redirects followed one hop at a time to public addresses only, and a security check or a sign in
page answers blocked, never solved. The accept and refuse tables of `allowed` and `blocked`.

Fakes only: the request context is a stand in of Playwright's APIRequestContext, the pages are
invented, the hosts are `.test` ones. The real request context runs in the docker tests.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd.config import PROXY_PORT  # noqa: E402
from browserd.tools import actions, discovery, fetch  # noqa: E402

POSTING_URL = "https://jobs.example.test/jobs/7001"
POSTING_HTML = (
    """<!doctype html><html><head><title>Robot Arm Engineer at Invented Robotics</title>
<script>window.secret = "never read"</script><style>.a{}</style></head><body>
<nav><a href="/">Home</a></nav>
<main><h1>Robot Arm Engineer</h1>
<p>Invented Robotics builds invented robot arms in Lyon.</p>
<p>We protect our forms with a captcha, which you never see.</p>
<a href="/jobs/7001/apply">Apply</a> <a href="https://careers.example-robotics.test/7001">Our
careers page</a> <a href="mailto:x@example.org">Mail</a>
<div hidden>hidden text</div>
</main></body></html>"""
    + "<p>"
    + "Build robots with customers. " * 120
    + "</p>"
)
CLOUDFLARE = (
    "<html><head><title>Just a moment...</title></head><body>"
    "<script>window._cf_chl_opt={cvId:'3'}</script>"
    "<noscript>Enable JavaScript and cookies to continue</noscript></body></html>"
)
INDEED_FR = (
    "<html><head><title>Vérification supplémentaire requise</title></head>"
    "<body><p>Vérification supplémentaire requise</p></body></html>"
)
INDEED_EN = (
    "<html><head><title>Additional Verification Required</title></head>"
    "<body><p>Additional Verification Required</p></body></html>"
)
CAPTCHA = (
    "<html><head><title>Jobs</title></head><body><p>Please solve the captcha</p></body></html>"
)
DATADOME = (
    "<html><head><title>jobs.example.test</title></head><body>"
    "<script src='https://ct.captcha-delivery.com/c.js'></script></body></html>"
)
SIGNIN = (
    "<html><head><title>Sign in</title></head><body><form><input name=u>"
    "<input type=password name=p></form></body></html>"
)


def read(url: str, html: str) -> fetch.Read:
    return fetch.as_snapshot(url, html, "text/html")


@pytest.mark.parametrize(
    ("url", "code"),
    [
        (POSTING_URL, None),
        ("http://jobs.example.test/search?q=robot", None),
        ("https://8.8.8.8/jobs", None),
        ("https://123.example.test/", None),
        ("ftp://jobs.example.test/", "refused_scheme"),
        ("https://user:pass@jobs.example.test/", "bad_url"),
        ("javascript:alert(1)", "refused_scheme"),
        ("http://127.0.0.1/", "private_address"),
        ("http://10.0.0.2/a", "private_address"),
        ("http://192.168.1.1/", "private_address"),
        ("http://169.254.169.254/latest", "private_address"),
        ("http://[::1]/", "private_address"),
        ("http://localhost:8000/", "private_address"),
        ("http://api.localhost/", "private_address"),
        ("http://printer.local/", "private_address"),
        ("http://browserd.internal/", "private_address"),
        ("http://2130706433/", "private_address"),
        ("http://0x7f.1/", "private_address"),
        ("http://127.1/", "private_address"),
        (None, "bad_url"),
        ("", "bad_url"),
    ],
)
def test_the_addresses_web_fetch_may_get(url: Any, code: str | None) -> None:
    """Acceptance 5: public http or https only; no credentials in the address, no private or
    local host (the egress proxy refuses them too)."""
    assert fetch.allowed(url) == code


def test_a_sign_out_address_is_refused_by_the_line() -> None:
    assert fetch.allowed("https://jobs.example.test/logout") == "line"


@pytest.mark.parametrize(
    ("status", "headers", "html", "url", "reason"),
    [
        (403, {}, CLOUDFLARE, POSTING_URL, "security_check"),
        (200, {}, CLOUDFLARE, POSTING_URL, "security_check"),
        (200, {"cf-mitigated": "challenge"}, "<p>x</p>", POSTING_URL, "security_check"),
        (200, {}, INDEED_FR, POSTING_URL, "security_check"),
        (200, {}, INDEED_EN, POSTING_URL, "security_check"),
        (200, {}, CAPTCHA, POSTING_URL, "security_check"),
        (200, {}, DATADOME, POSTING_URL, "security_check"),
        (429, {}, "<p>Too many</p>", POSTING_URL, "security_check"),
        (503, {}, "", POSTING_URL, "security_check"),
        (200, {}, SIGNIN, "https://jobs.example.test/login", "signin_wall"),
        (200, {}, SIGNIN, POSTING_URL, "signin_wall"),
        (200, {}, "<p>Join now</p>", "https://www.example.test/authwall?x=1", "signin_wall"),
        # A posting whose own long text says "captcha" is a page to read.
        (200, {}, POSTING_HTML, POSTING_URL, ""),
        (404, {}, POSTING_HTML, POSTING_URL, ""),
    ],
)
def test_a_security_check_or_a_sign_in_page_is_blocked(
    status: int, headers: dict[str, str], html: str, url: str, reason: str
) -> None:
    """Acceptance 4: Cloudflare's "Just a moment", Indeed's "Vérification supplémentaire
    requise", a captcha, DataDome's, an error status with almost no text, a sign in page: each
    is blocked with its reason; a long posting that mentions a captcha is not."""
    assert fetch.blocked(url, status, headers, html, read(url, html)) == reason


def test_a_page_is_read_as_a_snapshot_with_absolute_links_and_no_script() -> None:
    page = read(POSTING_URL, POSTING_HTML)
    text = "\n".join(page.lines)
    assert page.title == "Robot Arm Engineer at Invented Robotics"
    assert '- heading "Robot Arm Engineer" [level=1]' in text
    assert "- paragraph: Invented Robotics builds invented robot arms in Lyon." in text
    assert "  - /url: https://jobs.example.test/jobs/7001/apply" in text
    assert "  - /url: https://careers.example-robotics.test/7001" in text
    assert "mailto:" not in text
    assert "never read" not in text
    assert "hidden text" not in text
    assert page.links == 3  # Home, Apply, the careers page; never mailto


class Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.status, self.headers, self._body = status, headers, body
        self.disposed = False

    async def body(self) -> bytes:
        return self._body

    async def dispose(self) -> None:
        self.disposed = True


class Context:
    """Playwright's APIRequestContext as web_fetch uses it: GETs answered from `pages`."""

    def __init__(self, pages: dict[str, Response | Exception]) -> None:
        self.pages = pages
        self.got: list[tuple[str, dict[str, Any]]] = []
        self.disposed = False

    async def get(self, url: str, **kw: Any) -> Response:
        self.got.append((url, kw))
        answer = self.pages[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def dispose(self) -> None:
        self.disposed = True


def tab_of(context: Context, made: list[dict[str, Any]], *, discovering: bool = True) -> Any:
    async def new_context(**kw: Any) -> Context:
        made.append(kw)
        return context

    request = SimpleNamespace(new_context=new_context)
    return SimpleNamespace(
        state=SimpleNamespace(discovery=discovering, native_failed=False),
        lease_tag="lease0000",
        browser=SimpleNamespace(playwright=SimpleNamespace(request=request)),
    )


def html(body: str, status: int = 200) -> Response:
    data = body.encode()
    return Response(status, {"content-type": "text/html; charset=utf-8"}, data)


def fetched(pages: dict[str, Any], url: str = POSTING_URL, **kw: Any) -> tuple[dict, Any, list]:
    context, made = Context(pages), []
    tab = tab_of(context, made, discovering=kw.pop("discovering", True))
    answer = asyncio.run(fetch.run(tab, {"url": url, **kw}))
    return answer, context, made


def test_a_public_page_is_fetched_signed_out_through_the_proxy() -> None:
    """Acceptance 1: a fresh request context (no cookie of any context) through the egress
    proxy, GET without automatic redirects, the page answered as a snapshot."""
    answer, context, made = fetched({POSTING_URL: html(POSTING_HTML)})
    assert answer["ok"] is True
    assert answer["url"] == POSTING_URL
    assert answer["status"] == 200
    assert answer["title"] == "Robot Arm Engineer at Invented Robotics"
    assert '- heading "Robot Arm Engineer" [level=1]' in answer["snapshot"]
    assert made == [
        {"proxy": {"server": f"http://127.0.0.1:{PROXY_PORT}"}, "ignore_https_errors": False}
    ]
    [(_, kw)] = context.got
    assert kw["max_redirects"] == 0
    assert kw["timeout"] == fetch.FETCH_S * 1000
    assert context.disposed


def test_a_redirect_to_a_public_page_is_followed_one_hop_at_a_time() -> None:
    moved = Response(301, {"location": "/jobs/7001-robot-arm-engineer"})
    final = "https://jobs.example.test/jobs/7001-robot-arm-engineer"
    answer, context, _ = fetched({POSTING_URL: moved, final: html(POSTING_HTML)})
    assert answer["ok"] is True
    assert answer["url"] == final
    assert [url for url, _ in context.got] == [POSTING_URL, final]
    assert moved.disposed


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/admin",
        "http://localhost/",
        "https://u:p@jobs.example.test/",
        "file:///etc/x",
    ],
)
def test_a_redirect_off_limits_stops_the_fetch(location: str) -> None:
    answer, context, _ = fetched({POSTING_URL: Response(302, {"location": location})})
    assert answer == {
        "ok": False,
        "error": "redirect_refused",
        "message": "Refused: the page redirects off limits.",
    }
    assert len(context.got) == 1
    assert context.disposed


def test_a_redirect_loop_ends_at_the_bound() -> None:
    loop = {POSTING_URL: Response(302, {"location": POSTING_URL})}
    answer, context, _ = fetched(loop)
    assert answer["error"] == "redirects_max"
    assert len(context.got) == fetch.REDIRECTS_MAX + 1


@pytest.mark.parametrize(
    "response",
    [
        Response(200, {"content-type": "text/html", "content-length": str(fetch.FETCH_MAX + 1)}),
        Response(200, {"content-type": "text/html"}, b"x" * (fetch.FETCH_MAX + 1)),
    ],
)
def test_a_page_over_10_mb_is_refused(response: Response) -> None:
    answer, _, _ = fetched({POSTING_URL: response})
    assert answer["error"] == "too_large"


def test_a_file_that_is_no_text_is_refused() -> None:
    answer, _, _ = fetched({POSTING_URL: Response(200, {"content-type": "application/pdf"})})
    assert answer["error"] == "not_text"


def test_a_fetch_past_its_time_bound_answers_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class Never(Context):
        async def get(self, url: str, **kw: Any) -> Response:
            await asyncio.Event().wait()
            raise AssertionError

    monkeypatch.setattr(fetch, "FETCH_S", 0.0)
    context = Never({})
    answer = asyncio.run(fetch.run(tab_of(context, []), {"url": POSTING_URL}))
    assert answer["error"] == "fetch_timeout"
    assert context.disposed


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({"url": POSTING_URL, "method": "POST"}, "method"),
        ({"url": "http://10.1.2.3/"}, "private_address"),
        ({"url": POSTING_URL, "discovering": False}, discovery.UNSUPPORTED),
    ],
)
def test_refused_before_any_request(args: dict[str, Any], code: str) -> None:
    """Acceptance 5: no non GET, no private address, a discovery's lease only."""
    answer, context, made = fetched({}, **args)
    assert answer["ok"] is False
    assert answer["error"] == code
    assert made == []
    assert context.got == []


def test_a_challenge_page_answers_blocked_with_its_reason() -> None:
    answer, _, _ = fetched({POSTING_URL: html(CLOUDFLARE, 403)})
    assert answer["ok"] is False
    assert answer["error"] == "blocked"
    assert answer["reason"] == "security_check"
    assert "snapshot" not in answer


def test_web_fetch_is_a_discovery_tool_of_browserd() -> None:
    assert actions.TOOLS["web_fetch"] is fetch.run
    assert "web_fetch" in discovery.TOOLS
    assert "web_fetch" not in actions.AGENT_ACTS
