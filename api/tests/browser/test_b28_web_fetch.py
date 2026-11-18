"""B28 (owner, 2026-11-17: "It must work, even if i'm not logged in", #792): browserd's web_fetch
reads one public page signed out, without the tab: GET only, through the egress proxy, bounded,
redirects followed one hop at a time to public addresses only, and a security check or a sign in
page answers blocked, never solved. The accept and refuse tables of `allowed` and `blocked`.

Fakes only: the client's transport is a stand in (httpx's MockTransport), or a stand in of the
egress proxy on loopback; the pages are invented, the hosts are `.test` ones. The real egress
proxy runs in the docker test (test_b28_web_fetch_container.py).
"""

import asyncio
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpcore
import httpx
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
        # Round 1 of #805: every form a browser reads as a private address, refused here too.
        ("http://[::ffff:127.0.0.1]/", "private_address"),
        ("http://[::ffff:7f00:1]/", "private_address"),
        ("http://[fe80::1]/", "private_address"),
        ("http://[fe80::1%25eth0]/", "bad_url"),
        ("http://[fc00::1]/", "private_address"),
        ("http://[fd12:3456::1]/", "private_address"),
        ("http://0177.0.0.1/", "private_address"),
        ("http://017700000001/", "private_address"),
        ("http://0x7f000001/", "private_address"),
        ("http://0X7F.0.0.1/", "private_address"),
        ("http://0.0.0.0/", "private_address"),
        ("http://0/", "private_address"),
        ("http://100.64.0.1/", "private_address"),
        ("http://169.254.169.254./", "private_address"),
        ("http://LOCALHOST./", "private_address"),
        # Percent encoded hosts: WHATWG decodes them to a name this check never read.
        ("http://%31%32%37.0.0.1/", "bad_url"),
        ("http://localhost%2e/", "bad_url"),
        ("http://jobs%2eexample.test/", "bad_url"),
        ("http://%6c%6f%63%61%6c%68%6f%73%74/", "bad_url"),
        # Single label names: the Compose services and a local network's machines.
        ("http://api/", "private_address"),
        ("http://API./", "private_address"),
        ("http://worker:8000/", "private_address"),
        ("http://caddy/", "private_address"),
        ("http://browser:9000/", "private_address"),
        ("https://intranet/jobs", "private_address"),
        # Neighbours that stay public.
        ("https://jobs.example.test:8443/jobs", None),
        ("https://xn--bcher-kva.example.test/", None),
        ("https://WWW.Example-Board.test/Jobs?q=robot%20arm", None),
        ("https://jobs.example.test/search?q=100%25", None),
        ("https://1.1.1.1/", None),
        ("https://[2606:4700:4700::1111]/", None),
        ("https://api.example.test/jobs", None),
    ],
)
def test_the_addresses_web_fetch_may_get(url: Any, code: str | None) -> None:
    """Acceptance 5: public http or https only; no credentials in the address, no private or
    local host, no percent encoded or single label host (the egress proxy refuses the private
    ones too, whatever their name resolves to)."""
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


class Stream(httpx.AsyncByteStream):
    """A body served in `size` byte chunks, `total` bytes in all (None: without end), counting
    what was taken from it."""

    def __init__(self, data: bytes = b"", total: int | None = None, size: int = 65_536) -> None:
        self.data, self.total, self.size = data, total, size
        self.taken = 0

    async def __aiter__(self) -> Any:
        if self.data:
            for at in range(0, len(self.data), self.size):
                chunk = self.data[at : at + self.size]
                self.taken += len(chunk)
                yield chunk
            return
        chunk = b"x" * self.size
        while self.total is None or self.taken < self.total:
            self.taken += len(chunk)
            yield chunk


def answer(status: int, headers: dict[str, str], stream: Stream | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers, stream=stream or Stream())


class Web:
    """The internet as the client of one fetch sees it: GETs answered from `pages`, each
    request kept with its headers."""

    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages = pages
        self.got: list[httpx.Request] = []
        self.clients: list[httpx.AsyncClient] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.got.append(request)
        made = self.pages[str(request.url)]
        if isinstance(made, Exception):
            raise made
        return made() if callable(made) else made

    def client(self) -> httpx.AsyncClient:
        """fetch.client() as built, its transport (the proxy's) swapped for this stand in."""
        made = real_client()
        made._mounts = {}  # the proxy's mount: asserted in test_the_client_goes_through_...
        made._transport = httpx.MockTransport(self.handle)
        self.clients.append(made)
        return made

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.got]


real_client = fetch.client
TAB = SimpleNamespace(
    state=SimpleNamespace(discovery=True, native_failed=False), lease_tag="lease0000"
)


def html(body: str, status: int = 200, **headers: str) -> httpx.Response:
    data = body.encode()
    kind = {"content-type": "text/html; charset=utf-8", **headers}
    return answer(status, kind, Stream(data))


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> Any:
    def make(pages: dict[str, Any]) -> Web:
        made = Web(pages)
        monkeypatch.setattr(fetch, "client", made.client)
        return made

    return make


def fetched(web: Any, pages: dict[str, Any], url: str = POSTING_URL, **kw: Any) -> tuple:
    stand_in = web(pages)
    tab = SimpleNamespace(
        state=SimpleNamespace(discovery=kw.pop("discovering", True), native_failed=False),
        lease_tag="lease0000",
    )
    return asyncio.run(fetch.run(tab, {"url": url, **kw})), stand_in


def test_a_public_page_is_fetched_signed_out(web: Any) -> None:
    """Acceptance 1: a fresh client (no cookie of any context), GET without automatic
    redirects, `identity` asked for, the page answered as a snapshot."""
    result, stand_in = fetched(web, {POSTING_URL: html(POSTING_HTML)})
    assert result["ok"] is True
    assert result["url"] == POSTING_URL
    assert result["status"] == 200
    assert result["title"] == "Robot Arm Engineer at Invented Robotics"
    assert '- heading "Robot Arm Engineer" [level=1]' in result["snapshot"]
    [request] = stand_in.got
    assert request.method == "GET"
    assert request.headers["accept-encoding"] == "identity"
    assert "cookie" not in request.headers
    [client] = stand_in.clients
    assert client.is_closed


def test_the_client_goes_through_the_egress_proxy_whatever_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every address, http and https, goes to 127.0.0.1:PROXY_PORT: no environment variable is
    read (a NO_PROXY of `*` or another proxy changes nothing), no redirect is followed by the
    client, no cookie is kept."""
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("HTTPS_PROXY", "http://10.9.9.9:1")
    monkeypatch.setenv("ALL_PROXY", "http://10.9.9.9:1")
    client = fetch.client()
    assert client.trust_env is False
    assert client.follow_redirects is False
    for url in (POSTING_URL, "http://jobs.example.test/", "https://8.8.8.8/"):
        transport = client._transport_for_url(httpx.URL(url))
        pool = transport._pool  # type: ignore[attr-defined]
        assert isinstance(pool, httpcore.AsyncHTTPProxy), url
        assert pool._proxy_url == httpcore.URL(f"http://127.0.0.1:{PROXY_PORT}")
    request = httpx.Request("GET", POSTING_URL)
    set_cookie = httpx.Response(200, headers={"set-cookie": "s=1; Path=/"}, request=request)
    client.cookies.extract_cookies(set_cookie)
    assert not client.cookies
    asyncio.run(client.aclose())


def test_a_redirect_sends_no_cookie_the_first_hop_set(web: Any) -> None:
    final = "https://jobs.example.test/jobs/7001-robot-arm-engineer"
    moved = answer(302, {"location": final, "set-cookie": "session=s1; Path=/"})
    _, stand_in = fetched(web, {POSTING_URL: moved, final: html(POSTING_HTML)})
    assert stand_in.urls == [POSTING_URL, final]
    assert "cookie" not in stand_in.got[1].headers


def test_a_redirect_to_a_public_page_is_followed_one_hop_at_a_time(web: Any) -> None:
    body = Stream(total=fetch.FETCH_MAX * 4)
    moved = answer(301, {"location": "/jobs/7001-robot-arm-engineer"}, body)
    final = "https://jobs.example.test/jobs/7001-robot-arm-engineer"
    result, stand_in = fetched(web, {POSTING_URL: moved, final: html(POSTING_HTML)})
    assert result["ok"] is True
    assert result["url"] == final
    assert stand_in.urls == [POSTING_URL, final]
    assert body.taken == 0  # a redirect's body is never read


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/admin",
        "http://localhost/",
        "https://u:p@jobs.example.test/",
        "file:///etc/x",
        "http://api:8000/",
        "http://%31%32%37.0.0.1/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_a_redirect_off_limits_stops_the_fetch(web: Any, location: str) -> None:
    result, stand_in = fetched(web, {POSTING_URL: answer(302, {"location": location})})
    assert result == {
        "ok": False,
        "error": "redirect_refused",
        "message": "Refused: the page redirects off limits.",
    }
    assert stand_in.urls == [POSTING_URL]


def test_a_redirect_loop_ends_at_the_bound(web: Any) -> None:
    loop = {POSTING_URL: lambda: answer(302, {"location": POSTING_URL})}
    result, stand_in = fetched(web, loop)
    assert result["error"] == "redirects_max"
    assert len(stand_in.got) == fetch.REDIRECTS_MAX + 1


def test_a_declared_length_over_10_mb_is_refused_unread(web: Any) -> None:
    body = Stream(total=fetch.FETCH_MAX * 2)
    big = {"content-type": "text/html", "content-length": str(fetch.FETCH_MAX + 1)}
    result, _ = fetched(web, {POSTING_URL: answer(200, big, body)})
    assert result["error"] == "too_large"
    assert body.taken == 0


@pytest.mark.parametrize("total", [fetch.FETCH_MAX + 1, None])
def test_a_stream_past_10_mb_is_cut_at_the_bound(web: Any, total: int | None) -> None:
    """Round 1 of #805: a body without Content-Length (chunked), past the bound or without end,
    is read only up to FETCH_MAX and one chunk, then refused."""
    body = Stream(total=total)
    result, _ = fetched(web, {POSTING_URL: answer(200, {"content-type": "text/html"}, body)})
    assert result["error"] == "too_large"
    assert body.taken <= fetch.FETCH_MAX + body.size


def bomb(decoded: int, wbits: int = 16 + zlib.MAX_WBITS) -> bytes:
    """`decoded` zero bytes compressed (gzip by default), made part by part."""
    packer = zlib.compressobj(9, zlib.DEFLATED, wbits)
    zeros = bytes(1_048_576)
    parts = [packer.compress(zeros) for _ in range(decoded // len(zeros))]
    return b"".join([*parts, packer.flush()])


def test_a_gzip_bomb_is_refused_without_inflating_it_whole() -> None:
    """Round 1 of #805: 64 MB of zeros in about 64 KB of gzip, fed at once: the body inflates
    no more than the room left, keeps at most FETCH_MAX bytes, and refuses."""
    packed = bomb(64 * 1_048_576)
    assert len(packed) < 1_048_576
    body = fetch.Body("gzip")
    assert body.feed(packed) is False
    assert sum(map(len, body.parts)) <= fetch.FETCH_MAX
    assert body.size <= fetch.FETCH_MAX + 1


def test_a_gzip_page_decoding_past_10_mb_answers_too_large(web: Any) -> None:
    packed = bomb(16 * 1_048_576)
    headers = {"content-type": "text/html", "content-encoding": "gzip"}
    result, _ = fetched(web, {POSTING_URL: answer(200, headers, Stream(packed))})
    assert result["error"] == "too_large"


@pytest.mark.parametrize(
    ("encoding", "wbits"),
    [("gzip", 16 + zlib.MAX_WBITS), ("x-gzip", 16 + zlib.MAX_WBITS), ("deflate", zlib.MAX_WBITS),
     ("deflate", -zlib.MAX_WBITS), ("identity", None), ("", None)],
)  # fmt: skip
def test_a_page_sent_encoded_anyway_is_decoded_within_the_bound(
    web: Any, encoding: str, wbits: int | None
) -> None:
    """A server that ignores `identity`: gzip and deflate (zlib wrapped or raw) are read."""
    data = POSTING_HTML.encode()
    if wbits is not None:
        packer = zlib.compressobj(6, zlib.DEFLATED, wbits)
        data = packer.compress(data) + packer.flush()
    headers = {"content-type": "text/html", "content-encoding": encoding}
    result, _ = fetched(web, {POSTING_URL: answer(200, headers, Stream(data, size=100))})
    assert result["ok"] is True
    assert result["title"] == "Robot Arm Engineer at Invented Robotics"


@pytest.mark.parametrize("encoding", ["br", "zstd", "compress", "gzip, br", "x-unknown"])
def test_another_encoding_is_refused_unread(web: Any, encoding: str) -> None:
    body = Stream(b"\x1b\x00\x00")
    headers = {"content-type": "text/html", "content-encoding": encoding}
    result, _ = fetched(web, {POSTING_URL: answer(200, headers, body)})
    assert result["error"] == "encoding"
    assert body.taken == 0


def test_a_broken_gzip_stream_is_no_page(web: Any) -> None:
    headers = {"content-type": "text/html", "content-encoding": "gzip"}
    result, _ = fetched(web, {POSTING_URL: answer(200, headers, Stream(b"not gzip at all"))})
    assert result["error"] == "fetch_failed"


def test_a_file_that_is_no_text_is_refused(web: Any) -> None:
    body = Stream(total=fetch.FETCH_MAX)
    result, _ = fetched(web, {POSTING_URL: answer(200, {"content-type": "application/pdf"}, body)})
    assert result["error"] == "not_text"
    assert body.taken == 0


def test_a_network_failure_is_no_page(web: Any) -> None:
    failed = httpx.ProxyError("403 Forbidden")
    result, _ = fetched(web, {POSTING_URL: failed})
    assert result["error"] == "fetch_failed"


def test_a_fetch_past_its_time_bound_answers_a_timeout(
    web: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError

    monkeypatch.setattr(fetch, "FETCH_S", 0.0)
    stand_in = web({})
    stand_in.handle = never  # type: ignore[method-assign]
    result = asyncio.run(fetch.run(TAB, {"url": POSTING_URL}))
    assert result["error"] == "fetch_timeout"
    assert all(client.is_closed for client in stand_in.clients)


def test_a_stream_past_10_mb_through_a_proxy_on_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real client on real sockets: a stand in of the egress proxy on loopback gets the
    request in its absolute form with `identity` and no cookie (a .test name nobody resolves:
    nothing went direct), and serves a chunked body without end; the fetch stops at the bound
    and closes the connection. Variables of the environment change nothing."""
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("HTTP_PROXY", "http://10.9.9.9:1")
    url = "http://jobs.example.test/jobs/7001"
    heads: list[bytes] = []
    sent = [0]

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        heads.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n")
        writer.write(b"Transfer-Encoding: chunked\r\n\r\n")
        chunk = b"x" * 65_536
        framed = b"%x\r\n%s\r\n" % (len(chunk), chunk)
        try:
            while sent[0] < fetch.FETCH_MAX * 3:  # a bound for the test itself only
                writer.write(framed)
                await writer.drain()
                sent[0] += len(chunk)
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def go() -> dict[str, Any]:
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(fetch, "PROXY_PORT", port)
        async with server:
            return await fetch.run(TAB, {"url": url})

    result = asyncio.run(go())
    assert result["error"] == "too_large"
    [head] = heads
    lines = head.decode().lower().split("\r\n")
    assert lines[0] == f"get {url} http/1.1"
    assert "accept-encoding: identity" in lines
    assert not any(line.startswith("cookie:") for line in lines)
    assert sent[0] < fetch.FETCH_MAX * 3  # the client hung up before the stand in's own bound


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({"url": POSTING_URL, "method": "POST"}, "method"),
        ({"url": "http://10.1.2.3/"}, "private_address"),
        ({"url": "http://worker:8000/"}, "private_address"),
        ({"url": "http://localhost%2e/"}, "bad_url"),
        ({"url": POSTING_URL, "discovering": False}, discovery.UNSUPPORTED),
    ],
)
def test_refused_before_any_request(web: Any, args: dict[str, Any], code: str) -> None:
    """Acceptance 5: no non GET, no private address, a discovery's lease only."""
    result, stand_in = fetched(web, {}, **args)
    assert result["ok"] is False
    assert result["error"] == code
    assert stand_in.clients == []
    assert stand_in.got == []


def test_a_challenge_page_answers_blocked_with_its_reason(web: Any) -> None:
    result, _ = fetched(web, {POSTING_URL: html(CLOUDFLARE, 403)})
    assert result["ok"] is False
    assert result["error"] == "blocked"
    assert result["reason"] == "security_check"
    assert "snapshot" not in result


def test_a_challenge_header_answers_blocked(web: Any) -> None:
    page = html("<p>x</p>", 200, **{"cf-mitigated": "challenge"})
    result, _ = fetched(web, {POSTING_URL: page})
    assert result["reason"] == "security_check"


def test_web_fetch_is_a_discovery_tool_of_browserd() -> None:
    assert actions.TOOLS["web_fetch"] is fetch.run
    assert "web_fetch" in discovery.TOOLS
    assert "web_fetch" not in actions.AGENT_ACTS
