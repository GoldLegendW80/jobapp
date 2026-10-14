"""The renderer service's own logic (renderer/server.py) without a browser: a stand in for
Playwright's browser records what each render asks of it, and can hang where Chromium could.

A fresh context per render, JavaScript off, offline, the one route that lets only `data:` through;
the stop of a render that hangs (context closed, else the browser killed and launched again);
the protocol that takes an HTML body and nothing else; the engine's text never answered."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "renderer" / "server.py"

pytestmark = pytest.mark.skipif(not SERVER.is_file(), reason="the repository root is not mounted")


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("renderer_server", SERVER)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = load() if SERVER.is_file() else None
PDF = (
    b"%PDF-1.4\n1 0 obj <</Type /Pages /Count 2>>\n2 0 obj <</Type /Page>>\n"
    b"3 0 obj <</Type /Page /Parent 1 0 R>>"
)


class Context:
    def __init__(self, browser: "Browser", options: dict) -> None:
        self.browser, self.options, self.routes, self.closed = browser, options, [], False

    async def route(self, pattern: str, handler: object) -> None:
        self.routes.append((pattern, handler))

    async def new_page(self) -> "Page":
        return Page(self.browser)

    async def close(self) -> None:
        if self.browser.close_hangs:
            await asyncio.Event().wait()
        self.closed = True


class Page:
    def __init__(self, browser: "Browser") -> None:
        self.browser = browser

    async def set_content(self, html: str, **options: object) -> None:
        self.browser.pages.append(html)

    async def pdf(self, **options: object) -> bytes:
        if self.browser.render_hangs:
            await asyncio.Event().wait()
        if self.browser.fails:
            raise RuntimeError("Protocol error (Page.printToPDF): /usr/lib/chromium secret text")
        return PDF


class Browser:
    def __init__(self) -> None:
        self.contexts: list[Context] = []
        self.pages: list[str] = []
        self.connected, self.killed = True, False
        self.render_hangs = self.close_hangs = self.fails = False

    async def new_context(self, **options: object) -> Context:
        self.contexts.append(Context(self, options))
        return self.contexts[-1]

    def is_connected(self) -> bool:
        return self.connected

    async def close(self) -> None:
        if self.close_hangs:
            await asyncio.Event().wait()
        self.connected = False

    def kill(self) -> None:
        self.killed, self.connected = True, False


def renderer(**options: float) -> tuple[object, list[Browser]]:
    browsers: list[Browser] = []

    async def launch() -> Browser:
        browsers.append(Browser())
        return browsers[-1]

    return server.Renderer(launch, **options), browsers


def test_each_render_gets_a_fresh_context_closed_after_it() -> None:
    r, browsers = renderer()

    async def two() -> list:
        return [await r.render("<p>one</p>"), await r.render("<p>two</p>")]

    assert asyncio.run(two()) == [(PDF, 2), (PDF, 2)]
    (browser,) = browsers
    first, second = browser.contexts
    assert first is not second
    assert first.closed
    assert second.closed
    for context in browser.contexts:
        assert context.options["java_script_enabled"] is False
        assert context.options["offline"] is True
        assert context.options["service_workers"] == "block"
        assert context.routes == [("**/*", server.only_data)]
    assert browser.pages == ["<p>one</p>", "<p>two</p>"]


class Route:
    def __init__(self, url: str) -> None:
        self.request = type("Request", (), {"url": url})()
        self.outcome = ""

    async def continue_(self) -> None:
        self.outcome = "continued"

    async def abort(self, reason: str) -> None:
        self.outcome = "aborted"


@pytest.mark.parametrize(
    ("url", "outcome"),
    [
        ("data:font/woff2;base64,AAAA", "continued"),
        ("data:image/jpeg;base64,/9j/", "continued"),
        ("http://listener:8080/pixel.png", "aborted"),
        ("https://example.org/font.woff2", "aborted"),
        ("file:///etc/hostname", "aborted"),
        ("ws://listener:8080/socket", "aborted"),
        ("blob:https://example.org/1", "aborted"),
        ("about:blank#data:", "aborted"),
    ],
)
def test_only_a_data_address_goes_through_the_route(url: str, outcome: str) -> None:
    route = Route(url)
    asyncio.run(server.only_data(route))
    assert route.outcome == outcome


def test_a_render_that_hangs_is_stopped_and_its_context_closed() -> None:
    r, browsers = renderer(timeout_s=0.05)

    async def go() -> None:
        await r.ensure_browser()
        browsers[0].render_hangs = True
        with pytest.raises(server.RenderTimeout):
            await r.render("<p>slow</p>")
        browsers[0].render_hangs = False
        assert await r.render("<p>next</p>") == (PDF, 2)

    asyncio.run(go())
    assert len(browsers) == 1, "the context closed: the browser stays"
    assert browsers[0].contexts[0].closed
    assert r.relaunched == 0


def test_a_context_that_does_not_close_takes_the_browser_with_it() -> None:
    r, browsers = renderer(timeout_s=0.05, close_timeout_s=0.05)

    async def go() -> None:
        await r.ensure_browser()
        browsers[0].render_hangs = browsers[0].close_hangs = True
        with pytest.raises(server.RenderTimeout):
            await r.render("<p>stuck</p>")
        assert await r.render("<p>next</p>") == (PDF, 2)

    asyncio.run(go())
    assert browsers[0].killed
    assert r.relaunched == 1
    assert len(browsers) == 2, "the next render launched a new browser"


def test_a_crashed_browser_is_launched_again_for_the_next_render() -> None:
    r, browsers = renderer()

    async def go() -> None:
        await r.render("<p>one</p>")
        browsers[0].connected = False
        await r.render("<p>two</p>")

    asyncio.run(go())
    assert len(browsers) == 2


def test_one_render_at_a_time_and_a_bounded_queue() -> None:
    r, _ = renderer()
    r.waiting = server.WAITING
    with pytest.raises(server.BusyError):
        asyncio.run(r.render("<p>x</p>"))


def request(raw: bytes) -> object:
    async def read() -> object:
        reader = asyncio.StreamReader(limit=server.MAX_HEAD)
        reader.feed_data(raw)
        reader.feed_eof()
        try:
            return await server.read_request(reader)
        except server.HttpError as error:
            return error.status

    return asyncio.run(read())


def post(target: str = "/render", body: bytes = b"<p>x</p>", extra: bytes = b"") -> bytes:
    return (
        b"POST %s HTTP/1.1\r\nContent-Length: %d\r\n%s\r\n" % (target.encode(), len(body), extra)
        + body
    )


def test_the_endpoint_takes_an_html_body_and_nothing_else() -> None:
    found = request(post())
    assert (found.method, found.path, found.body) == ("POST", "/render", b"<p>x</p>")
    assert request(b"GET /health HTTP/1.1\r\n\r\n").path == "/health"
    for target in (
        "/render?url=http://example.org",
        "/render/../etc",
        "http://x/render",
        "/",
        "/pdf",
    ):
        assert request(post(target)) == 404, target
    assert request(b"GET /render HTTP/1.1\r\n\r\n") == 404
    assert request(b"PUT /render HTTP/1.1\r\nContent-Length: 1\r\n\r\nx") == 404


def test_a_request_without_a_declared_length_or_over_the_cap_is_refused() -> None:
    assert request(b"POST /render HTTP/1.1\r\n\r\n<p>") == 400
    assert request(post(extra=b"Transfer-Encoding: chunked\r\n")) == 400
    over = b"POST /render HTTP/1.1\r\nContent-Length: %d\r\n\r\n" % (server.MAX_HTML + 1)
    assert request(over) == 413, "refused from the header, before any body is read"
    assert request(post(body=b"x" * (1024 * 1024))).body == b"x" * (1024 * 1024)
    assert request(b"POST /render HTTP/1.1\r\nContent-Length: 10\r\n\r\nshort") == 400
    assert request(b"POST /render HTTP/1.1\r\n" + b"X: y\r\n" * 2000 + b"\r\n") == 400
    assert request(b"\x00\xff garbage\r\n\r\n") == 400


def test_the_engines_text_never_leaves_the_service() -> None:
    r, browsers = renderer()

    async def go() -> tuple[bytes, str]:
        await r.ensure_browser()
        browsers[0].fails = True
        return await server.handle(r, server.Request("POST", "/render", {}, b"<p>x</p>"))

    data, outcome = asyncio.run(go())
    assert outcome == "render_failed"
    assert data.startswith(b"HTTP/1.1 500 ")
    assert json.loads(data.split(b"\r\n\r\n", 1)[1]) == {"code": "render_failed"}
    assert b"secret" not in data
    assert b"chromium" not in data


def test_a_good_render_answers_the_pdf_and_its_page_count() -> None:
    r, _ = renderer()
    data, outcome = asyncio.run(
        server.handle(r, server.Request("POST", "/render", {}, b"<p>x</p>"))
    )
    head, body = data.split(b"\r\n\r\n", 1)
    assert outcome == "ok"
    assert body == PDF
    assert b"X-Pages: 2" in head
    assert b"Content-Type: application/pdf" in head


def test_pages_are_counted_from_the_page_objects() -> None:
    assert server.count_pages(PDF) == 2
    assert server.count_pages(b"%PDF- <</Type/Page>> <</Type /Pages>>") == 1
