"""The renderer service (task D3 of the documents plan): one HTML page in, one A4 PDF out.

Playwright's Chromium headless shell, the engine D1 measured and chose on the Jetson. The worker
is its only caller (app/documents/render_client.py), over `rendering`, an internal network the
two share with nobody. The HTML is built by the application from escaped text, with its fonts
and photo as `data:` addresses; nothing else may load, and four layers say so on their own:

- JavaScript is off in every context, so no script runs and no WebSocket can be opened;
- every request of a context goes through `only_data`, which lets a `data:` address through and
  aborts everything else (http, https, file, ws, a blob of another origin);
- the context is `offline`;
- the container sits on an internal network without a gateway, as uid 10003, with a read only
  root, no capability and no new privilege (compose.yml).

One warm browser; a fresh context per render, closed after it, never shared between renders. One
render at a time: the others wait their turn, at most WAITING of them. A render longer than
RENDER_TIMEOUT_S is stopped: its context is closed and, when even that does not finish within
CLOSE_TIMEOUT_S, the browser is killed and launched again for the next render.

Protocol, HTTP/1.1, one request per connection, no other path and no parameter:

    POST /render   the HTML as the body (Content-Length, at most MAX_HTML bytes, UTF-8).
                   200 application/pdf with `X-Pages`, or a JSON body {"code": ...}:
                   400 bad_request, 404 not_found, 413 too_large, 503 busy, 504 timeout,
                   500 render_failed. A code, never the engine's text.
    POST /check    the visibility check of a template a model wrote (documents D25b): a JSON
                   body {"html", "needles", "width", "name"}. The page is laid out for print at
                   `width` CSS pixels, the width of an A4 page's text, and for each needle (a
                   text the page must show) every occurrence in the body's text is measured
                   glyph by glyph: its box, its font size, and whether `elementFromPoint` at
                   the glyph's centre is the element that holds the text, so nothing lies over
                   it. Then two screenshots: the page as it is, and the page with the needles
                   painted transparent through a CSS highlight named `name`, which changes no
                   layout; the worker compares their pixels (app/documents/design_check.py).
                   200 application/json, or the refusals of /render. The page's own scripts
                   stay off: the measures run through the DevTools protocol, which a page
                   without JavaScript cannot reach or change.
    GET /health    200 when the browser is connected, 503 otherwise.

    python -I server.py            serves on 0.0.0.0:7071
    python -I server.py --check    the healthcheck: GET /health on 127.0.0.1, exit 0 when 200

Nothing of a page or of a PDF is ever logged: one line per request, with the outcome as a code,
the sizes and the duration. Playwright is imported in `main` only, so the logic above it is
tested without a browser (api/tests/documents/test_renderer_server.py).
"""

import asyncio
import base64
import json
import re
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

PORT = 7071
MAX_HTML = 5 * 1024 * 1024  # an HTML of 1 MB renders; fonts and photo are inside it
MAX_HEAD = 8192
READ_TIMEOUT_S = 10.0  # the headers and the body of a request
RENDER_TIMEOUT_S = 30.0  # the spec's 30 s; the client gives up a little after it
CLOSE_TIMEOUT_S = 5.0
WAITING = 8
LAUNCH_ARGS = ["--disable-dev-shm-usage", "--disable-gpu"]

STATUS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    413: "Payload Too Large",
    500: "Internal Server Error",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}
# One code per status: what the client turns into its own errors.
CODES = {400: "bad_request", 404: "not_found", 413: "too_large", 500: "render_failed"}
CODES |= {503: "busy", 504: "timeout"}

_PAGE = re.compile(rb"/Type\s*/Page(?![A-Za-z])")

CHECK_SCALE = 2  # device pixels per CSS pixel of the screenshots
CHECK_PAGE_HEIGHT = 1123  # an A4 page at 96 CSS pixels an inch
CHECK_MAX_HEIGHT = 4 * CHECK_PAGE_HEIGHT  # what is laid out lower is outside the check
CHECK_WIDTHS = (200, 1200)
CHECK_NEEDLES = 400
CHECK_NEEDLE = 2000
CHECK_FOUND = 8  # occurrences of one needle measured
_NAME = re.compile(r"[a-z][a-z0-9]{7,39}")

# How far down the page lays out, in CSS pixels: [the lowest text below the root element's top,
# the lowest text or box of the body from the viewport's top].
# The viewport's own height does not count, as the document's scroll height would.
HEIGHT_JS = """() => {
  let text = 0;
  let box = 0;
  for (const element of document.body.querySelectorAll("*")) {
    box = Math.max(box, element.getBoundingClientRect().bottom);
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  while (walker.nextNode()) {
    if (walker.currentNode.data.trim()) {
      range.selectNodeContents(walker.currentNode);
      text = Math.max(text, range.getBoundingClientRect().bottom);
    }
  }
  // The text's height from the top of the root element, whose margin the check may set.
  const top = document.documentElement.getBoundingClientRect().top;
  return [Math.ceil(Math.max(0, text - top)), Math.ceil(Math.max(text, box))];
}"""
# Every occurrence of every needle in the body's text: [font size, [[x, y, w, h, own]]], a glyph
# a line, `own` 1 when the element at its centre is the one that holds the text.
MEASURE_JS = """(needles) => {
  const nodes = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) nodes.push(walker.currentNode);
  return needles.map((needle) => {
    const found = [];
    const wanted = needle.toLowerCase();
    for (const node of nodes) {
      // Lower case on both sides (a template may print a field in capitals); a text whose
      // lower case changes its length is left out, its indexes would not be the node's.
      const data = node.data.toLowerCase();
      let at = data.length === node.data.length ? data.indexOf(wanted) : -1;
      while (at >= 0 && found.length < FOUND_MAX) {
        const parent = node.parentElement;
        const glyphs = [];
        for (let i = at; i < at + needle.length; i++) {
          const code = node.data.charCodeAt(i);
          const wide = code >= 0xd800 && code < 0xdc00 ? 2 : 1;
          if (!/\\s/.test(node.data[i])) {
            const range = document.createRange();
            range.setStart(node, i);
            range.setEnd(node, i + wide);
            const box = range.getClientRects()[0];
            if (!box || box.width <= 0 || box.height <= 0) {
              glyphs.push([0, 0, 0, 0, 0]);
            } else {
              const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
              glyphs.push([box.left, box.top, box.width, box.height, hit === parent ? 1 : 0]);
            }
          }
          i += wide - 1;
        }
        found.push([parseFloat(getComputedStyle(parent).fontSize) || 0, glyphs]);
        at = data.indexOf(wanted, at + needle.length);
      }
    }
    return found;
  });
}""".replace("FOUND_MAX", str(CHECK_FOUND))
# The needles painted transparent: the same boxes, the same layout, no glyph.
HIDE_JS = """([name, needles]) => {
  const style = document.createElement("style");
  style.textContent = `::highlight(${name}){color:transparent;-webkit-text-fill-color:transparent;text-shadow:none}`;
  document.head.append(style);
  const marked = new Highlight();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const data = node.data.toLowerCase();
    if (data.length !== node.data.length) continue;
    for (const needle of needles.map((one) => one.toLowerCase())) {
      for (let at = data.indexOf(needle); at >= 0; at = data.indexOf(needle, at + 1)) {
        const range = document.createRange();
        range.setStart(node, at);
        range.setEnd(node, at + needle.length);
        marked.add(range);
      }
    }
  }
  CSS.highlights.set(name, marked);
}"""


class RenderTimeout(Exception):
    pass


def count_pages(pdf: bytes) -> int:
    """Page objects of Chromium's PDF (Skia writes its dictionaries uncompressed). The client
    counts them again with PDFium and refuses an answer where the two disagree."""
    return len(_PAGE.findall(pdf))


def log(outcome: str, **values: int) -> None:
    print(
        json.dumps({"event": "renderer.request", "outcome": outcome, **values}),
        flush=True,
    )


async def only_data(route: Any) -> None:
    """The context's one route: a `data:` address goes on, anything else is aborted."""
    if route.request.url.startswith("data:"):
        await route.continue_()
    else:
        await route.abort("blockedbyclient")


class Renderer:
    """One warm browser, a fresh context per render, and the stop of a render that hangs.

    `launch` returns a connected browser (Playwright's, or a stand in for the tests)."""

    def __init__(
        self,
        launch: Callable[[], Awaitable[Any]],
        timeout_s: float = RENDER_TIMEOUT_S,
        close_timeout_s: float = CLOSE_TIMEOUT_S,
    ) -> None:
        self.launch = launch
        self.timeout_s = timeout_s
        self.close_timeout_s = close_timeout_s
        self.browser: Any = None
        self.context: Any = None
        self.turn = asyncio.Lock()
        self.waiting = 0
        self.relaunched = 0

    def healthy(self) -> bool:
        return self.browser is not None and self.browser.is_connected()

    async def ensure_browser(self) -> None:
        if not self.healthy():
            await self.kill_browser()
            self.browser = await self.launch()

    async def kill_browser(self) -> None:
        browser, self.browser = self.browser, None
        if browser is None:
            return
        try:
            await asyncio.wait_for(browser.close(), self.close_timeout_s)
        except Exception:  # noqa: BLE001  (the browser is replaced whatever happened)
            kill = getattr(browser, "kill", None)  # the stand ins of the tests have one
            if kill is not None:
                kill()

    async def render(self, html: str) -> tuple[bytes, int]:
        return await self._turn(lambda: self._render(html))

    async def check(self, asked: "CheckAsked") -> dict[str, Any]:
        return await self._turn(lambda: self._check(asked))

    async def _turn(self, make: Callable[[], Awaitable[Any]]) -> Any:
        if self.waiting >= WAITING:
            raise BusyError
        self.waiting += 1
        try:
            await self.turn.acquire()
        finally:
            self.waiting -= 1
        try:
            await self.ensure_browser()
            job = asyncio.ensure_future(make())
            job.add_done_callback(lambda done: done.cancelled() or done.exception())
            finished, _ = await asyncio.wait({job}, timeout=self.timeout_s)
            if job not in finished:
                job.cancel()
                await self.stop(hung=True)
                raise RenderTimeout
            await self.stop(hung=False)
            return job.result()
        finally:
            self.turn.release()

    async def _render(self, html: str) -> tuple[bytes, int]:
        self.context = await self.browser.new_context(
            java_script_enabled=False,
            offline=True,
            service_workers="block",
            accept_downloads=False,
        )
        await self.context.route("**/*", only_data)
        page = await self.context.new_page()
        await page.set_content(html, wait_until="load", timeout=self.timeout_s * 1000)
        pdf = await page.pdf(
            format="A4", print_background=True, prefer_css_page_size=True
        )
        return pdf, count_pages(pdf)

    async def _check(self, asked: "CheckAsked") -> dict[str, Any]:
        self.context = await self.browser.new_context(
            java_script_enabled=False,
            offline=True,
            service_workers="block",
            accept_downloads=False,
            viewport={"width": asked.width, "height": CHECK_PAGE_HEIGHT},
            device_scale_factor=CHECK_SCALE,
        )
        await self.context.route("**/*", only_data)
        page = await self.context.new_page()
        await page.emulate_media(media="print")
        await page.set_content(
            asked.html, wait_until="load", timeout=self.timeout_s * 1000
        )
        height, lowest = await page.evaluate(HEIGHT_JS)
        height = int(height)
        shown = max(1, min(int(lowest), CHECK_MAX_HEIGHT))
        await page.set_viewport_size({"width": asked.width, "height": shown})
        found = await page.evaluate(MEASURE_JS, list(asked.needles))
        before = await page.screenshot(type="png")
        await page.evaluate(HIDE_JS, [asked.name, list(asked.needles)])
        after = await page.screenshot(type="png")
        return {
            "width": asked.width,
            "height": height,
            "shown": shown,
            "scale": CHECK_SCALE,
            "found": found,
            "before": base64.b64encode(before).decode("ascii"),
            "after": base64.b64encode(after).decode("ascii"),
        }

    async def stop(self, hung: bool) -> None:
        """The context of the render that ended is closed, never reused. One that does not
        close in time, or a render that hung before it had one, takes the browser with it: it
        is killed, and the next render launches a new one."""
        context, self.context = self.context, None
        try:
            if context is None:
                if hung:
                    raise RenderTimeout
                return
            await asyncio.wait_for(context.close(), self.close_timeout_s)
        except Exception:  # noqa: BLE001  (whatever happened, the browser is replaced)
            self.relaunched += 1
            await self.kill_browser()


class BusyError(Exception):
    pass


class CheckAsked:
    def __init__(
        self, html: str, needles: tuple[str, ...], width: int, name: str
    ) -> None:
        self.html, self.needles, self.width, self.name = html, needles, width, name


def check_asked(body: bytes) -> CheckAsked:
    """The body of POST /check, strictly; HttpError(400) otherwise."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise HttpError(400) from None
    if not isinstance(data, dict) or set(data) != {"html", "needles", "width", "name"}:
        raise HttpError(400)
    html, needles, width, name = (
        data["html"],
        data["needles"],
        data["width"],
        data["name"],
    )
    if (
        not isinstance(html, str)
        or not isinstance(name, str)
        or not _NAME.fullmatch(name)
    ):
        raise HttpError(400)
    if type(width) is not int or not CHECK_WIDTHS[0] <= width <= CHECK_WIDTHS[1]:
        raise HttpError(400)
    if not isinstance(needles, list) or not 1 <= len(needles) <= CHECK_NEEDLES:
        raise HttpError(400)
    if not all(isinstance(n, str) and 1 <= len(n) <= CHECK_NEEDLE for n in needles):
        raise HttpError(400)
    return CheckAsked(html, tuple(needles), width, name)


class Request:
    def __init__(
        self, method: str, path: str, headers: dict[str, str], body: bytes
    ) -> None:
        self.method, self.path, self.headers, self.body = method, path, headers, body


class HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


async def read_request(reader: asyncio.StreamReader) -> Request:
    """One request, strictly: a known method and path, no query, a declared length."""
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_TIMEOUT_S)
    except asyncio.LimitOverrunError:
        raise HttpError(400) from None
    except (asyncio.IncompleteReadError, TimeoutError, ConnectionError):
        raise HttpError(400) from None
    if len(head) > MAX_HEAD:
        raise HttpError(400)
    try:
        lines = head.decode("ascii").split("\r\n")
        method, target, version = lines[0].split(" ")
    except (UnicodeDecodeError, ValueError):
        raise HttpError(400) from None
    if not version.startswith("HTTP/1."):
        raise HttpError(400)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if line:
            name, sep, value = line.partition(":")
            if not sep:
                raise HttpError(400)
            headers[name.strip().lower()] = value.strip()
    if (method, target) == ("GET", "/health"):
        return Request(method, target, headers, b"")
    if (method, target) not in (("POST", "/render"), ("POST", "/check")):
        raise HttpError(404)  # another path, a query string, a URL or a file to fetch
    if "transfer-encoding" in headers:
        raise HttpError(400)
    length = headers.get("content-length", "")
    if not length.isdigit():
        raise HttpError(400)
    if int(length) > MAX_HTML:
        raise HttpError(413)
    try:
        body = await asyncio.wait_for(reader.readexactly(int(length)), READ_TIMEOUT_S)
    except (asyncio.IncompleteReadError, TimeoutError, ConnectionError):
        raise HttpError(400) from None
    return Request(method, target, headers, body)


def answer(
    status: int, body: bytes, kind: str, extra: dict[str, str] | None = None
) -> bytes:
    head = [f"HTTP/1.1 {status} {STATUS[status]}", f"Content-Type: {kind}"]
    head += [f"Content-Length: {len(body)}", "Connection: close"]
    head += [f"{name}: {value}" for name, value in (extra or {}).items()]
    return ("\r\n".join(head) + "\r\n\r\n").encode("ascii") + body


def refusal(status: int) -> bytes:
    return answer(
        status, json.dumps({"code": CODES[status]}).encode(), "application/json"
    )


async def handle(renderer: Renderer, request: Request) -> tuple[bytes, str]:
    """The answer to one request, and its outcome for the log line."""
    if request.method == "GET":
        if renderer.healthy():
            return answer(200, b"ok", "text/plain"), "health"
        return answer(503, b"no browser", "text/plain"), "unhealthy"
    if request.path == "/check":
        return await handle_check(renderer, request)
    try:
        html = request.body.decode("utf-8")
    except UnicodeDecodeError:
        return refusal(400), "bad_request"
    try:
        pdf, pages = await renderer.render(html)
    except BusyError:
        return refusal(503), "busy"
    except RenderTimeout:
        return refusal(504), "timeout"
    except Exception:  # noqa: BLE001  (the engine's text never leaves the service)
        return refusal(500), "render_failed"
    return answer(200, pdf, "application/pdf", {"X-Pages": str(pages)}), "ok"


async def handle_check(renderer: Renderer, request: Request) -> tuple[bytes, str]:
    try:
        asked = check_asked(request.body)
    except HttpError:
        return refusal(400), "bad_request"
    try:
        found = await renderer.check(asked)
    except BusyError:
        return refusal(503), "busy"
    except RenderTimeout:
        return refusal(504), "timeout"
    except Exception:  # noqa: BLE001  (the engine's text never leaves the service)
        return refusal(500), "render_failed"
    return answer(200, json.dumps(found).encode(), "application/json"), "checked"


async def serve_one(
    renderer: Renderer, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    started = time.monotonic()
    size = 0
    try:
        try:
            request = await read_request(reader)
            size = len(request.body)
            data, outcome = await handle(renderer, request)
        except HttpError as error:
            data, outcome = refusal(error.status), CODES[error.status]
        writer.write(data)
        await writer.drain()
    except ConnectionError:
        outcome = "gone"
    finally:
        writer.close()
    log(outcome, bytes=size, ms=int((time.monotonic() - started) * 1000))


async def check(port: int = PORT) -> int:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), 3
        )
        writer.write(b"GET /health HTTP/1.1\r\nHost: renderer\r\n\r\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), 5)
        writer.close()
    except (OSError, TimeoutError):
        return 1
    return 0 if line.startswith(b"HTTP/1.1 200 ") else 1


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:

        async def launch() -> Any:
            # --no-sandbox, Playwright's default: Chromium's own sandbox needs user namespaces,
            # which Docker's default seccomp profile and no-new-privileges refuse (D1). The
            # container is the boundary (compose.yml).
            return await playwright.chromium.launch(headless=True, args=LAUNCH_ARGS)

        renderer = Renderer(launch)
        await renderer.ensure_browser()
        server = await asyncio.start_server(
            lambda r, w: serve_one(renderer, r, w), "0.0.0.0", PORT, limit=MAX_HEAD
        )
        log("listening", port=PORT)
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(asyncio.run(check()))
    asyncio.run(main())
