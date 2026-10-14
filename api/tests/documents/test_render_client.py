"""The worker's side of the renderer (render_client.py), against a stand in for the service on a
loopback port: every answer the service can give, the errors, the page count PDFium reads, the
font check, the renders at once, and the log line that never holds a page.

The real service, with Chromium, is test_renderer_container.py."""

import asyncio
import ctypes
import io
import json
import logging
import secrets
import socket
from collections.abc import Awaitable, Callable

import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
from PIL import Image

from app.documents import render_client
from app.documents.render_client import RenderError

LIMITS = {"timeout_s": 5, "max_pdf_bytes": 4 * 1024 * 1024}
HTML = "<html><body style=\"font-family: 'Helvetica', sans-serif\">Alex Example</body></html>"


def make_pdf(pages: int = 1, font: str = "Helvetica") -> bytes:
    """A PDF of `pages` A4 pages, each with one line of text in a standard font."""
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        page = document.new_page(595, 842)
        text = raw.FPDFPageObj_NewTextObj(document.raw, font.encode(), ctypes.c_float(12))
        words = "Alex Example\0".encode("utf-16-le")
        raw.FPDFText_SetText(text, ctypes.cast(ctypes.c_char_p(words), raw.FPDF_WIDESTRING))
        raw.FPDFPageObj_Transform(text, 1, 0, 0, 1, 72, 760)
        raw.FPDFPage_InsertObject(page.raw, text)
        raw.FPDFPage_GenerateContent(page.raw)
        page.close()
    out = io.BytesIO()
    document.save(out)
    document.close()
    return out.getvalue()


Answer = Callable[[bytes], Awaitable[bytes | None]]


def ok(pdf: bytes, pages: int | None = None, length: bool = True) -> Answer:
    async def answer(body: bytes) -> bytes:
        head = [b"HTTP/1.1 200 OK", b"Content-Type: application/pdf", b"Connection: close"]
        head.append(b"X-Pages: %d" % (pages if pages is not None else len(pdfium.PdfDocument(pdf))))
        if length:
            head.append(b"Content-Length: %d" % len(pdf))
        return b"\r\n".join(head) + b"\r\n\r\n" + pdf

    return answer


def refused(status: int, text: str = "") -> Answer:
    async def answer(body: bytes) -> bytes:
        payload = json.dumps({"code": "x", "detail": text}).encode()
        return b"HTTP/1.1 %d No\r\nContent-Length: %d\r\n\r\n" % (status, len(payload)) + payload

    return answer


class Stand:
    """The stand in: one answer per request, the bodies it received, renders at once."""

    def __init__(self, answer: Answer) -> None:
        self.answer, self.bodies, self.active, self.most = answer, [], 0, 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        length = next(
            int(line.split(b":")[1])
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"content-length")
        )
        body = await reader.readexactly(length)
        self.bodies.append(body)
        self.active += 1
        self.most = max(self.most, self.active)
        try:
            data = await self.answer(body)
        finally:
            self.active -= 1
        if data is not None:
            writer.write(data)
            await writer.drain()
        writer.close()


async def against(answer: Answer, *htmls: str, **options) -> tuple[list, Stand]:
    stand = Stand(answer)
    server = await asyncio.start_server(stand.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        found = await asyncio.gather(
            *(
                render_client.render(
                    html, host="127.0.0.1", port=port, **{**LIMITS, "at_once": 1, **options}
                )
                for html in htmls
            ),
            return_exceptions=True,
        )
    finally:
        # Not wait_closed: a handler that hangs on purpose is cancelled when asyncio.run ends.
        server.close()
    return list(found), stand


def one(answer: Answer, html: str = HTML, **options) -> object:
    found, _ = asyncio.run(against(answer, html, **options))
    return found[0]


def code_of(found: object) -> str:
    assert isinstance(found, RenderError), found
    assert str(found) == found.code, "the message is the code, never the engine's text"
    return found.code


def test_a_render_gives_the_pdf_and_its_page_count() -> None:
    pdf = make_pdf(2)
    found = one(ok(pdf))
    assert isinstance(found, render_client.Rendered)
    assert (found.pdf, found.pages) == (pdf, 2)


def test_the_page_count_pdfium_reads_must_be_the_services() -> None:
    assert code_of(one(ok(make_pdf(2), pages=1))) == "render_failed"
    assert code_of(one(ok(b"%PDF-1.7 not a pdf", pages=1))) == "render_failed"
    assert code_of(one(ok(b"<html>", pages=1))) == "render_failed"


def test_the_renderer_down_is_renderer_unavailable() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    async def call() -> object:
        try:
            return await render_client.render(
                HTML, host="127.0.0.1", port=port, at_once=1, **LIMITS
            )
        except RenderError as error:
            return error

    found = asyncio.run(call())
    assert code_of(found) == "renderer_unavailable"
    assert render_client.CONNECT_TIMEOUT_S <= 2


def test_a_connection_closed_without_an_answer_is_renderer_unavailable() -> None:
    async def silent(body: bytes) -> None:
        return None

    assert code_of(one(silent)) == "renderer_unavailable"


def test_a_render_that_hangs_is_stopped_at_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render_client, "MARGIN_S", 0)
    never = asyncio.Event()

    async def hang(body: bytes) -> bytes:
        await never.wait()
        return b""

    assert code_of(one(hang, timeout_s=0.2)) == "timeout"


@pytest.mark.parametrize(
    ("status", "code"),
    [(413, "too_large"), (504, "timeout"), (503, "renderer_unavailable"), (500, "render_failed"),
     (400, "render_failed"), (404, "render_failed")],
)  # fmt: skip
def test_each_refusal_of_the_service_has_its_code(status: int, code: str) -> None:
    engine_text = "Error: net::ERR_FAILED at /usr/lib/chromium " + secrets.token_hex(8)
    found = one(refused(status, engine_text))
    assert code_of(found) == code
    assert engine_text not in repr(found)


def test_a_pdf_over_the_cap_is_too_large_declared_or_not() -> None:
    pdf = make_pdf(1)
    assert code_of(one(ok(pdf), max_pdf_bytes=len(pdf) - 1)) == "too_large"
    assert code_of(one(ok(pdf, length=False), max_pdf_bytes=len(pdf) - 1)) == "too_large"
    assert one(ok(pdf), max_pdf_bytes=len(pdf)).pages == 1


def test_an_html_of_1_mb_is_sent_whole_and_one_over_the_cap_never_leaves() -> None:
    big = HTML.replace("Alex Example", "x" * (1024 * 1024))
    found, stand = asyncio.run(against(ok(make_pdf(1)), big))
    assert found[0].pages == 1
    assert stand.bodies == [big.encode()]
    huge = "x" * (render_client.MAX_HTML + 1)
    found, stand = asyncio.run(against(ok(make_pdf(1)), huge))
    assert code_of(found[0]) == "too_large"
    assert stand.bodies == []


@pytest.mark.parametrize("at_once", [1, 2])
def test_renders_at_once_are_bounded_by_the_setting(at_once: int) -> None:
    pdf = make_pdf(1)

    async def slow(body: bytes) -> bytes:
        for _ in range(50):  # yields only: the other render gets every chance to arrive
            await asyncio.sleep(0)
        return await ok(pdf)(body)

    found, stand = asyncio.run(against(slow, HTML, HTML, HTML, at_once=at_once))
    assert all(isinstance(f, render_client.Rendered) for f in found), found
    assert stand.most == at_once
    assert len(stand.bodies) == 3


def test_the_settings_of_the_module_are_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    class Settings:
        documents_render_timeout_s = 7

    monkeypatch.setattr("app.config.get_settings", lambda: Settings())
    assert render_client._setting("documents_render_timeout_s") == 7
    assert render_client._setting("documents_renders_at_once") == 1
    assert render_client._setting("documents_max_pdf_bytes") == 4 * 1024 * 1024


def test_a_pdf_in_a_font_the_html_did_not_ask_for_fails() -> None:
    pdf = make_pdf(1, font="Courier")
    assert code_of(one(ok(pdf))) == "render_failed"
    asks_courier = HTML.replace("'Helvetica'", "'Helvetica', Courier")
    assert one(ok(pdf), html=asks_courier).pages == 1


def test_the_families_an_html_asks_for() -> None:
    html = """<style>@font-face{font-family:"Tpl Sans";src:url(data:font/woff2;base64,AA)}
    html{font-family: "Source Serif 4", 'IBM Plex Sans' , Inter, sans-serif !important}
    h1{FONT-FAMILY:DejaVu Serif;}</style><p style="font-family:'EB Garamond'">x</p>
    <p>font-family is a word here</p>"""
    assert render_client.families_asked(html) == {
        "tplsans", "sourceserif4", "ibmplexsans", "inter", "dejavuserif", "ebgaramond",
    }  # fmt: skip
    assert render_client.families_asked("<p>no style</p>") == frozenset()
    assert render_client.families_asked("font-family: serif, monospace") == frozenset()


@pytest.mark.parametrize(
    ("fonts", "asked", "passes"),
    [
        (["DejaVuSans", "DejaVuSans-Bold"], {"dejavusans"}, True),
        (["SourceSerif4-Regular", "IBMPlexSans-SemiBold"], {"sourceserif4", "ibmplexsans"}, True),
        (["NunitoSans10pt-Regular"], {"nunitosans"}, True),
        (["DejaVuSans"], {"tplmissing"}, False),
        (["Inter-Regular", "LiberationSans"], {"inter"}, False),
        ([""], {"inter"}, False),
        ([], {"inter"}, True),
    ],
)
def test_every_font_of_the_pdf_must_be_of_an_asked_family(
    fonts: list, asked: set, passes: bool
) -> None:
    assert render_client.fonts_asked(fonts, frozenset(asked)) is passes


def test_the_fonts_of_a_pdf_are_read_without_their_subset_tag() -> None:
    document = pdfium.PdfDocument(make_pdf(2, font="Times-Roman"))
    assert render_client.fonts_of(document) == ["Times-Roman"]
    document.close()


def test_page1_png_is_page_one_at_the_width_asked() -> None:
    png = render_client.page1_png(make_pdf(2), 480)
    image = Image.open(io.BytesIO(png))
    assert (image.format, image.width) == ("PNG", 480)
    assert image.height == round(480 * 842 / 595) or abs(image.height - 480 * 842 / 595) <= 1


def test_the_log_line_holds_codes_and_sizes_never_the_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = secrets.token_hex(12)
    with caplog.at_level(logging.INFO, logger="app.documents.render_client"):
        one(ok(make_pdf(1)), html=HTML.replace("Alex Example", marker))
        one(refused(500, marker), html=HTML.replace("Alex Example", marker))
    records = [r for r in caplog.records if r.getMessage() == "documents.render.call"]
    assert [r.outcome for r in records] == ["ok", "render_failed"]
    for record in records:
        assert marker not in json.dumps(record.__dict__, default=str)
        assert {"html_bytes", "pdf_bytes", "ms"} <= set(record.__dict__)
