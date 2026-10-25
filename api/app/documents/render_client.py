"""The worker's side of the renderer service (task D3 of the documents plan).

`render(html) -> Rendered(pdf, pages)` sends one HTML page to the `renderer` service (compose.yml,
renderer/server.py: Playwright's Chromium headless shell, the engine D1 chose) and checks what
comes back. Nothing else of the application knows which engine runs: the fit loop (fit.py) and
the render jobs call this module only.

What the caller gets is a Rendered, or a RenderError whose `code` is one of four, never the
engine's text:

    renderer_unavailable   nobody answers within CONNECT_TIMEOUT_S (stopped, restarting, an
                           unknown name), the connection broke, or the service is busy
    timeout                the render took longer than DOCUMENTS_RENDER_TIMEOUT_S (the service
                           stops it at 30 s and recreates its browser context)
    too_large              the HTML is over MAX_HTML, or the PDF over DOCUMENTS_MAX_PDF_BYTES
    render_failed          the engine failed, or the answer is not a PDF whose page count
                           PDFium reads as the service said, or the PDF holds a font the HTML
                           did not ask for

**The font check.** Both engines D1 measured fall back silently to another font when one is
missing, and a CV in the wrong font is worse than no preview. So every font the PDF uses must
belong to a family the HTML names in a `font-family` declaration (generic names such as
`sans-serif` excluded): a PDF font is named after its PostScript name ("DejaVuSans-Bold",
"SourceSerif4-Regular", after a subset tag), which is the family without its spaces, then the
style. A font of no named family is a fallback, and the render fails as `render_failed`.

At most DOCUMENTS_RENDERS_AT_ONCE renders at once per process; the others wait their turn.
Nothing of a page or of a PDF is ever logged: one line per call, with the outcome as a code, the
sizes and the duration.

PDFium (pypdfium2, the `ingestion` group, which the worker image installs) is imported inside
the functions that read a PDF, so the api image imports this module without it.

**One PDFium call at a time.** PDFium is not thread safe, not even across two documents: its
font cache and page loader are process wide (pypdfium2's README says the same). The api draws
page images on its threadpool (`page1_png`, the D6 route) and the worker checks PDFs through
asyncio.to_thread (`_check`), so two threads can meet in PDFium; when they did, the api process
died of a segmentation fault (issue #316: two `page1.png` of the job page at the same
millisecond). Every use of PDFium in this module holds PDFIUM, from opening the document to
closing it, with every page and bitmap closed inside, so no finalizer reaches PDFium later on
another thread.
"""

import asyncio
import base64
import binascii
import io
import json
import logging
import math
import re
import threading
import time
import weakref
from dataclasses import dataclass

import httpx

logger = logging.getLogger("app.documents.render_client")

HOST = "renderer"
PORT = 7071
CONNECT_TIMEOUT_S = 2.0
MARGIN_S = 2.0  # the service answers `timeout` at its own 30 s; the client waits a little more
MAX_HTML = 5 * 1024 * 1024  # the service's cap (renderer/server.py)
CODES = ("renderer_unavailable", "timeout", "too_large", "render_failed")

# The settings of the module (app/config.py, task D2), and the spec's values while they are absent.
DEFAULTS = {
    "documents_render_timeout_s": 30,
    "documents_renders_at_once": 1,
    "documents_max_pdf_bytes": 4 * 1024 * 1024,
}
# What the service's refusals mean here (renderer/server.py, CODES).
REFUSALS = {413: "too_large", 503: "renderer_unavailable", 504: "timeout"}
GENERIC = frozenset(
    {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-serif"}
    | {"ui-sans-serif", "ui-monospace", "ui-rounded", "emoji", "math", "fangsong", "inherit"}
    | {"initial", "unset", "revert", "revert-layer"}
)


PAGE_RATIO_MAX = 10  # page 1's height over its width: an A4 page is 1.41
PDFIUM = threading.Lock()  # held around every use of PDFium in this process (module docstring)


class RenderError(Exception):
    """A render that did not give a PDF. `code` is one of CODES, and the message is the code."""

    def __init__(self, code: str) -> None:
        if code not in CODES:
            raise ValueError(code)
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Rendered:
    pdf: bytes
    pages: int


def _setting(name: str) -> int:
    from app.config import get_settings

    return int(getattr(get_settings(), name, DEFAULTS[name]))


_gates: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]"
_gates = weakref.WeakKeyDictionary()


def _gate(at_once: int) -> asyncio.Semaphore:
    """The renders of this process at once: one semaphore per event loop."""
    loop = asyncio.get_running_loop()
    if loop not in _gates:
        _gates[loop] = asyncio.Semaphore(at_once)
    return _gates[loop]


async def render(
    html: str,
    *,
    timeout_s: float | None = None,
    max_pdf_bytes: int | None = None,
    at_once: int | None = None,
    host: str | None = None,
    port: int | None = None,
) -> Rendered:
    """One HTML page to one checked PDF. The keywords default to the settings of the module."""
    timeout_s = timeout_s if timeout_s is not None else _setting("documents_render_timeout_s")
    if max_pdf_bytes is None:
        max_pdf_bytes = _setting("documents_max_pdf_bytes")
    at_once = at_once if at_once is not None else _setting("documents_renders_at_once")
    data = html.encode("utf-8")
    started = time.monotonic()
    outcome, size = "ok", 0
    try:
        if len(data) > MAX_HTML:
            raise RenderError("too_large")
        async with _gate(at_once):
            pdf, said = await _call(
                data, host or HOST, port or PORT, timeout_s + MARGIN_S, max_pdf_bytes
            )
        size = len(pdf)
        pages = await asyncio.to_thread(_check, pdf, said, html)
        return Rendered(pdf=pdf, pages=pages)
    except RenderError as error:
        outcome = error.code
        raise
    finally:
        logger.info(
            "documents.render.call",
            extra={
                "html_bytes": len(data),
                "pdf_bytes": size,
                "outcome": outcome,
                "ms": int((time.monotonic() - started) * 1000),
            },
        )


async def _call(
    data: bytes, host: str, port: int, timeout_s: float, max_pdf_bytes: int
) -> tuple[bytes, int]:
    timeouts = httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S)
    try:
        async with (
            asyncio.timeout(timeout_s),
            httpx.AsyncClient(timeout=timeouts, trust_env=False) as client,
            client.stream(
                "POST",
                f"http://{host}:{port}/render",
                content=data,
                headers={"Content-Type": "text/html; charset=utf-8"},
            ) as response,
        ):
            if response.status_code != 200:
                raise RenderError(REFUSALS.get(response.status_code, "render_failed"))
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_pdf_bytes:
                raise RenderError("too_large")
            pdf = bytearray()
            async for chunk in response.aiter_bytes():
                pdf += chunk
                if len(pdf) > max_pdf_bytes:
                    raise RenderError("too_large")
            pages = response.headers.get("x-pages", "")
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        raise RenderError("renderer_unavailable") from None
    except (TimeoutError, httpx.TimeoutException):
        raise RenderError("timeout") from None
    except httpx.HTTPError:
        raise RenderError("renderer_unavailable") from None
    if not pages.isdigit() or not pdf.startswith(b"%PDF-"):
        raise RenderError("render_failed")
    return bytes(pdf), int(pages)


# --- the visibility check of a template a model wrote (D25b, renderer/server.py: POST /check) ---

CHECK_MAX_ANSWER = 32 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_CHECK_KEYS = frozenset(
    ("width", "scroll_width", "height", "shown", "scale", "found", "before", "after")
)


@dataclass(frozen=True)
class Seen:
    """One occurrence of a needle: the font size of its element in CSS pixels, per glyph
    `(x, y, width, height, own)`, `own` True when nothing lies over the glyph's centre, and
    `within`, the left and right edges it should stay within: those of its element and of every
    box that holds it, up to the body."""

    size: float
    glyphs: tuple[tuple[float, float, float, float, bool], ...]
    within: tuple[float, float]


@dataclass(frozen=True)
class Checked:
    """What the renderer measured: the page laid out at `width` CSS pixels, `scroll_width` wide
    (wider when a box overflows the page), its lowest text at `height`, the top `shown` measured
    and photographed at `scale` device pixels each; per needle its occurrences; the PNG of the
    page, and of the page with the needles transparent."""

    width: int
    scroll_width: int
    height: int
    shown: int
    scale: int
    found: tuple[tuple[Seen, ...], ...]
    before: bytes
    after: bytes


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise RenderError("render_failed")
    return float(value)


def _seen(value: object, needle: str) -> Seen:
    if not isinstance(value, list) or len(value) != 3 or not isinstance(value[1], list):
        raise RenderError("render_failed")
    if not isinstance(value[2], list) or len(value[2]) != 2:
        raise RenderError("render_failed")
    if len(value[1]) > len(needle):
        raise RenderError("render_failed")
    glyphs = []
    for glyph in value[1]:
        if not isinstance(glyph, list) or len(glyph) != 5:
            raise RenderError("render_failed")
        x, y, w, h, own = (_number(part) for part in glyph)
        glyphs.append((x, y, w, h, own == 1))
    left, right = (_number(edge) for edge in value[2])
    return Seen(_number(value[0]), tuple(glyphs), (left, right))


def _png(value: object) -> bytes:
    try:
        data = base64.b64decode(str(value), validate=True)
    except (binascii.Error, ValueError):
        raise RenderError("render_failed") from None
    if not data.startswith(PNG_MAGIC):
        raise RenderError("render_failed")
    return data


def read_checked(raw: bytes, needles: tuple[str, ...], width: int) -> Checked:
    """The renderer's answer to a check, every value checked; RenderError otherwise."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise RenderError("render_failed") from None
    if not isinstance(data, dict) or set(data) != _CHECK_KEYS:
        raise RenderError("render_failed")
    found = data["found"]
    if not isinstance(found, list) or len(found) != len(needles):
        raise RenderError("render_failed")
    sizes = [data["width"], data["scroll_width"], data["height"] + 1, data["shown"], data["scale"]]
    if not all(type(value) is int and value >= 1 for value in sizes) or data["width"] != width:
        raise RenderError("render_failed")
    if data["scale"] > 4:
        raise RenderError("render_failed")
    occurrences = []
    for needle, seen in zip(needles, found, strict=True):
        if not isinstance(seen, list):
            raise RenderError("render_failed")
        occurrences.append(tuple(_seen(one, needle) for one in seen))
    return Checked(
        width=width,
        scroll_width=data["scroll_width"],
        height=data["height"],
        shown=data["shown"],
        scale=data["scale"],
        found=tuple(occurrences),
        before=_png(data["before"]),
        after=_png(data["after"]),
    )


async def check(
    html: str,
    needles: tuple[str, ...],
    width: int,
    name: str,
    *,
    timeout_s: float | None = None,
    at_once: int | None = None,
    host: str | None = None,
    port: int | None = None,
) -> Checked:
    """Lay `html` out at `width` CSS pixels and measure where each needle shows (renderer
    POST /check). One line of log: sizes, outcome and duration."""
    timeout_s = timeout_s if timeout_s is not None else _setting("documents_render_timeout_s")
    at_once = at_once if at_once is not None else _setting("documents_renders_at_once")
    body = json.dumps({"html": html, "needles": list(needles), "width": width, "name": name})
    data = body.encode("utf-8")
    started = time.monotonic()
    outcome, size = "ok", 0
    try:
        if len(data) > MAX_HTML:
            raise RenderError("too_large")
        async with _gate(at_once):
            raw = await _exchange(
                "/check", data, "application/json", host or HOST, port or PORT,
                timeout_s + MARGIN_S, CHECK_MAX_ANSWER,
            )  # fmt: skip
        size = len(raw)
        return await asyncio.to_thread(read_checked, raw, needles, width)
    except RenderError as error:
        outcome = error.code
        raise
    finally:
        logger.info(
            "documents.render.check",
            extra={
                "html_bytes": len(data),
                "answer_bytes": size,
                "outcome": outcome,
                "ms": int((time.monotonic() - started) * 1000),
            },
        )


async def _exchange(
    path: str, data: bytes, kind: str, host: str, port: int, timeout_s: float, most: int
) -> bytes:
    timeouts = httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S)
    try:
        async with (
            asyncio.timeout(timeout_s),
            httpx.AsyncClient(timeout=timeouts, trust_env=False) as client,
            client.stream(
                "POST", f"http://{host}:{port}{path}", content=data, headers={"Content-Type": kind}
            ) as response,
        ):
            if response.status_code != 200:
                raise RenderError(REFUSALS.get(response.status_code, "render_failed"))
            found = bytearray()
            async for chunk in response.aiter_bytes():
                found += chunk
                if len(found) > most:
                    raise RenderError("too_large")
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        raise RenderError("renderer_unavailable") from None
    except (TimeoutError, httpx.TimeoutException):
        raise RenderError("timeout") from None
    except httpx.HTTPError:
        raise RenderError("renderer_unavailable") from None
    return bytes(found)


def _check(pdf: bytes, said: int, html: str) -> int:
    """The page count PDFium reads, which must be the service's, and the font check."""
    import pypdfium2 as pdfium

    asked = families_asked(html)
    with PDFIUM:
        try:
            document = pdfium.PdfDocument(pdf)
        except pdfium.PdfiumError:
            raise RenderError("render_failed") from None
        try:
            pages = len(document)
            fonts = fonts_of(document) if pages == said and pages >= 1 else []
        finally:
            document.close()
    if pages != said or pages < 1:
        raise RenderError("render_failed")
    if not fonts_asked(fonts, asked):
        raise RenderError("render_failed")
    return pages


def fonts_of(document: object) -> list[str]:
    """The base font name of every text object of every page, subset tag removed. The caller
    holds PDFIUM."""
    import ctypes

    import pypdfium2.raw as raw

    names: set[str] = set()
    for page in document:  # type: ignore[attr-defined]
        try:
            for item in page.get_objects(filter=[raw.FPDF_PAGEOBJ_TEXT], max_depth=8):
                font = raw.FPDFTextObj_GetFont(item.raw)
                size = raw.FPDFFont_GetBaseFontName(font, None, 0) if font else 0
                buffer = ctypes.create_string_buffer(max(size, 1))
                if size:
                    raw.FPDFFont_GetBaseFontName(font, buffer, size)
                name = buffer.value.decode("latin-1")
                names.add(re.sub(r"^[A-Z]{6}\+", "", name))
        finally:
            page.close()
    return sorted(names)


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def families_asked(html: str) -> frozenset[str]:
    """Every family the HTML names in a `font-family` declaration, generic names excluded,
    reduced to lower case letters and digits ("Source Serif 4" -> "sourceserif4")."""
    found: set[str] = set()
    for match in re.finditer(r"font-family\s*:", html, re.IGNORECASE):
        for name in _names(html, match.end()):
            if name.lower() not in GENERIC and _key(name):
                found.add(_key(name))
    return frozenset(found)


def _names(text: str, at: int) -> list[str]:
    """The comma separated family names that start at `at`: quoted, or bare words."""
    names: list[str] = []
    while True:
        while at < len(text) and text[at] in " \t\r\n":
            at += 1
        if at < len(text) and text[at] in "\"'":
            end = text.find(text[at], at + 1)
            if end < 0:
                return names
            names.append(text[at + 1 : end])
            at = end + 1
        else:
            word = re.match(r"[A-Za-z0-9_\- ]+", text[at:])
            if not word:
                return names
            names.append(word.group().strip())
            at += word.end()
        while at < len(text) and text[at] in " \t\r\n":
            at += 1
        if at >= len(text) or text[at] != ",":
            return names
        at += 1


def fonts_asked(fonts: list[str], asked: frozenset[str]) -> bool:
    """True when every font of the PDF belongs to a family the HTML asked for: its PostScript
    name without the style ("DejaVuSans-Bold" -> "dejavusans") starts with that family."""
    for font in fonts:
        base = _key(font.split("-", 1)[0])
        if not base or not any(base.startswith(family) for family in asked):
            return False
    return True


def page1_png(pdf: bytes, width: int) -> bytes:
    """Page 1 of a PDF the app's renderer made, as a PNG `width` pixels wide (a preview, a
    thumbnail). Raises RenderError("render_failed") when PDFium cannot open the file or draw
    its first page, or when that page is no CV page (more than PAGE_RATIO_MAX times as long as
    it is wide). Never give it a file from elsewhere: the api parses nothing (pyproject.toml)."""
    import pypdfium2 as pdfium

    with PDFIUM:
        try:
            document = pdfium.PdfDocument(pdf)
        except pdfium.PdfiumError:
            raise RenderError("render_failed") from None
        try:
            if len(document) < 1:
                raise RenderError("render_failed")
            try:
                page = document[0]
            except pdfium.PdfiumError:
                raise RenderError("render_failed") from None
            try:
                page_width, page_height = page.get_size()
                if page_width <= 0 or not 0 < page_height <= page_width * PAGE_RATIO_MAX:
                    raise RenderError("render_failed")
                try:
                    bitmap = page.render(scale=width / page_width)
                except pdfium.PdfiumError:
                    raise RenderError("render_failed") from None
                try:
                    # A copy, so the image owns its pixels once the bitmap is closed.
                    image = bitmap.to_pil().copy()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()
    if image.width != width:
        image = image.resize((width, max(1, round(image.height * width / image.width))))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
