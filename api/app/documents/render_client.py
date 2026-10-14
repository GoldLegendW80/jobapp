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
"""

import asyncio
import io
import logging
import re
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


def _check(pdf: bytes, said: int, html: str) -> int:
    """The page count PDFium reads, which must be the service's, and the font check."""
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(pdf)
    except pdfium.PdfiumError:
        raise RenderError("render_failed") from None
    try:
        pages = len(document)
        if pages != said or pages < 1:
            raise RenderError("render_failed")
        if not fonts_asked(fonts_of(document), families_asked(html)):
            raise RenderError("render_failed")
    finally:
        document.close()
    return pages


def fonts_of(document: object) -> list[str]:
    """The base font name of every text object of every page, subset tag removed."""
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
    """Page 1 of a PDF as a PNG `width` pixels wide (a preview, a thumbnail)."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf)
    try:
        page = document[0]
        bitmap = page.render(scale=width / page.get_width())
        image = bitmap.to_pil()
        if image.width != width:
            image = image.resize((width, max(1, round(image.height * width / image.width))))
        out = io.BytesIO()
        image.save(out, format="PNG")
        page.close()
    finally:
        document.close()
    return out.getvalue()
