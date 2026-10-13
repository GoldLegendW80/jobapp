"""The renderer, sandbox/pdf_render.py, called in the test process on PDFs made here
(tests/ingestion/scan_fixtures.py): which pages become images, the page size guards, the
batches, the text layer of a file only PDFium opens, and each hostile file ending as a code of
the closed lists, never as an exception.

Every answer is also passed through the worker's own reading of it (main.encode, then
parser_client.loads and check_parsed). The same files against the real child, under its limits
and its filter, in the container of the service: test_image_container.py.
"""

import io
import re
from collections.abc import Iterator
from typing import Any

import pytest
from PIL import Image

from app.ingestion import parser_client as pc
from tests.ingestion import pdf_fixtures as pdfs
from tests.ingestion import scan_fixtures as fx
from tests.ingestion.parser.helpers import (
    Plenty,
    SpentAfter,
    as_in_the_child,
    sandbox_handler,
    sandbox_module,
)

main = sandbox_module("main")
pdf_text = sandbox_handler("pdf_text")
pdf_render = sandbox_handler("pdf_render")


@pytest.fixture(autouse=True)
def the_reader_is_found_by_its_name() -> Iterator[None]:
    """The render job imports the PDF reader by its name, as it does beside it in the child."""
    with as_in_the_child():
        yield


def ask(data: bytes, options: dict, budget: Any = None) -> pc.Parsed:
    answer = pdf_render.handle(data, options, budget or Plenty())
    return pc.check_parsed(pc.loads(main.encode(answer)), "pdf_render")


def render(data: bytes, pages: list[int], budget: Any = None, **options: Any) -> pc.Parsed:
    return ask(data, {"pages": pages, **options}, budget)


def refusal(data: bytes, options: dict, budget: Any = None) -> tuple[str, str]:
    with pytest.raises(pc.ParserRefusedError) as caught:
        ask(data, options, budget)
    return caught.value.failure, caught.value.code


def opened(stored: pc.PageImage) -> Image.Image:
    decoded = Image.open(io.BytesIO(stored.jpeg))
    decoded.load()
    assert (decoded.format, decoded.mode) == ("JPEG", "RGB")
    assert decoded.size == (stored.width, stored.height)
    assert len(stored.jpeg) <= pc.MAX_IMAGE_BYTES
    return decoded


def red(pixel: tuple[int, ...]) -> bool:
    return pixel[0] > 150 and pixel[1] < 80 and pixel[2] < 80


def nonblank(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def test_the_caps_are_the_ones_of_the_plan_and_of_the_text_reader() -> None:
    assert pdf_render.BATCH == 8 == pc.MAX_IMAGES
    assert pdf_render.PAGES_MAX == pdf_text.PAGES_MAX == pc.MAX_READ_PAGES
    assert pdf_render.PAGE_MAX_INCHES == 200
    assert pdf_render.LONG_SIDE == 1568
    assert "pages_not_rendered" in pc.NOTE_CODES


def test_a_one_page_scan_becomes_one_image_at_1568_pixels() -> None:
    parsed = render(fx.scan(1), [0])
    assert (parsed.pages, parsed.pages_text, parsed.pages_image) == (1, 0, 1)
    assert parsed.text is None
    assert not parsed.truncated
    (stored,) = parsed.images
    assert (stored.position, stored.width, stored.height) == (0, 1108, 1568)
    assert red(opened(stored).getpixel((30, 30)))  # the top left corner of the page, upright


def test_a_rotated_page_is_drawn_as_it_is_read() -> None:
    parsed = render(fx.scan_pdf([fx.Sheet(fx.jpeg_picture(), rotate=90)]), [0])
    (stored,) = parsed.images
    assert (stored.width, stored.height) == (1568, 1108)
    assert red(opened(stored).getpixel((1568 - 30, 30)))  # the top of the scan is on the right


def test_a_31_page_scan_is_rendered_8_pages_a_child_and_30_in_all() -> None:
    """The child stops at its limit; the worker asks again after the last image it got, and
    stops at 30 by the limit it sends (jobs.py: the 31st page is named in the note)."""
    data = fx.scan(31)
    pages, positions, limit = list(range(31)), [], 30
    while pages and len(positions) < limit:
        parsed = render(data, pages, limit=min(8, limit - len(positions)))
        assert parsed.pages == 31
        assert 1 <= len(parsed.images) <= 8
        positions += [stored.position for stored in parsed.images]
        pages = [index for index in pages if index > positions[-1]]
    assert positions == list(range(30))
    assert pages == [30]


def test_in_a_mixed_pdf_positions_are_page_indexes_not_a_running_count() -> None:
    data = fx.mixed()
    text = pc.check_parsed(pc.loads(main.encode(pdf_text.handle(data, {}, Plenty()))), "pdf_text")
    slots = (text.text or "").split("\f")
    assert [bool(slot) for slot in slots] == [True, False, True, True, True]
    assert 50 <= nonblank(slots[3]) < 200  # the footer: a text by the first trigger
    assert 50 <= nonblank(slots[4]) < 200
    parsed = render(data, [1, 3, 4], check=[3, 4])
    assert [stored.position for stored in parsed.images] == [1, 3]
    assert parsed.pages == 5
    assert parsed.notes == ()


def test_a_scanned_page_under_a_footer_of_80_characters_is_rendered_by_the_second_trigger() -> None:
    assert 50 <= nonblank(fx.FOOTER) < 200
    data = fx.scan_pdf([fx.Sheet(fx.jpeg_picture(), text=fx.FOOTER)])
    assert [stored.position for stored in render(data, [0], check=[0]).images] == [0]
    # Half the page is the rule: an image over 49 percent of it does not count.
    under = fx.scan_pdf([fx.Sheet(fx.jpeg_picture(), text=fx.FOOTER, box=(0, 0, 595, 412))])
    assert render(under, [0], check=[0]).images == ()
    over = fx.scan_pdf([fx.Sheet(fx.jpeg_picture(), text=fx.FOOTER, box=(0, 0, 595, 425))])
    assert len(render(over, [0], check=[0]).images) == 1


def test_a_text_page_with_a_small_logo_is_kept_as_text() -> None:
    data = fx.scan_pdf([fx.Sheet(fx.jpeg_picture(), text=fx.SHORT, box=(400, 700, 120, 80))])
    parsed = render(data, [0], check=[0])
    assert parsed.images == ()
    assert parsed.notes == ()  # a page that stays a text is not a page left out


def test_a_page_of_200_inches_is_rendered_and_a_longer_one_is_left_out() -> None:
    edge = 200 * fx.INCH
    (stored,) = render(fx.sized(edge, edge / 2), [0]).images
    assert (stored.width, stored.height) == (1568, 784)  # a page of 5 metres costs an A4
    parsed = render(fx.sized(edge + 1, edge / 2), [0])
    assert parsed.images == ()
    assert [(note.code, dict(note.values)) for note in parsed.notes] == [
        ("pages_not_rendered", {"count": 1})
    ]


def test_a_page_1_wide_and_100_long_is_left_out() -> None:
    parsed = render(fx.sized(72, 7200), [0])
    assert parsed.images == ()
    assert [note.code for note in parsed.notes] == ["pages_not_rendered"]
    assert pdf_render.renderable(100, 1000)
    assert not pdf_render.renderable(99, 1000)
    assert not pdf_render.renderable(0, 1000)


def test_a_pdf_whose_pages_are_600_dpi_images_of_20_mib_in_all() -> None:
    data = fx.heavy_scans(6)
    assert 16 * fx.MIB < len(data) <= 20 * fx.MIB
    parsed = render(data, list(range(6)))
    assert [stored.position for stored in parsed.images] == list(range(6))
    for stored in parsed.images:
        assert (stored.width, stored.height) == (1108, 1568)
        opened(stored)


def test_an_aes_pdf_with_an_empty_password_is_read_and_with_a_real_one_it_fails() -> None:
    plain = pdfs.text_pdf(3)
    for algorithm in ("AES-128", "AES-256"):
        data = pdfs.encrypted(plain, algorithm)
        # The reader of G2b answers `encryption` for it in the parser, where `cryptography`
        # is blocked (test_pdf_text.py); PDFium decrypts by itself (assumption A8).
        parsed = ask(data, {"text": True})
        assert (parsed.pages, parsed.pages_text, parsed.pages_image) == (3, 3, 0)
        assert pdfs.SENTENCE in (parsed.text or "")
        assert parsed.images == ()
        locked = pdfs.encrypted(plain, algorithm, "a real password")
        assert refusal(locked, {"text": True}) == ("file", "protected")
        assert refusal(locked, {"pages": [0]}) == ("file", "protected")


def test_a_file_both_readers_open_gives_texts_that_differ_only_in_whitespace() -> None:
    plain = pdfs.build(
        [
            pdfs.Page(
                pdfs.lines(*pdfs.page_text(1, 12).split("\n")), links=["https://example.org/a b"]
            ),
            pdfs.scanned_page(),
            pdfs.page_text(3, 30),
        ]
    )
    for data in (plain, pdfs.encrypted(plain, "RC4-128"), fx.mixed()):
        by_pypdf = pc.check_parsed(
            pc.loads(main.encode(pdf_text.handle(data, {}, Plenty()))), "pdf_text"
        )
        by_pdfium = ask(data, {"text": True})
        for name in ("pages", "pages_text", "pages_image", "truncated"):
            assert getattr(by_pdfium, name) == getattr(by_pypdf, name)
        assert [note.code for note in by_pdfium.notes] == [note.code for note in by_pypdf.notes]
        ours, theirs = (by_pdfium.text or "").split("\f"), (by_pypdf.text or "").split("\f")
        assert [re.sub(r"\s+", "", slot) for slot in ours] == [
            re.sub(r"\s+", "", slot) for slot in theirs
        ]
        assert "Links: https://" in ours[0] or "Links: https://" in ours[2]


def test_the_text_job_stops_at_the_caps_of_the_text_reader() -> None:
    data = pdfs.encrypted(pdfs.text_pdf(4), "AES-256")
    stopped = ask(data, {"text": True}, SpentAfter(2))
    assert (stopped.pages, stopped.pages_text, stopped.truncated) == (4, 2, True)
    assert [(note.code, dict(note.values)) for note in stopped.notes] == [
        ("time_limit", {"from": 3, "to": 4})
    ]
    assert refusal(data, {"text": True}, SpentAfter(0)) == ("file", "too_slow")
    scans = pdfs.encrypted(fx.scan(2), "AES-256")
    assert refusal(scans, {"text": True}) == ("file", "no_text")


def test_rendering_stops_when_the_time_budget_is_spent() -> None:
    data = fx.scan(4)
    assert refusal(data, {"pages": [0, 1, 2, 3]}, SpentAfter(1)) == ("file", "too_slow")

    class AfterTwoPages:
        """Spent once two pages were drawn, whoever asks: the loop, or PDFium while it draws."""

        def spent(self) -> bool:
            return drawn["pages"] >= 2

    drawn = {"pages": 0}
    entry = pdf_render.pictures.entry

    def counted(position: int, picture: Any) -> dict:
        drawn["pages"] += 1
        return entry(position, picture)

    pdf_render.pictures.entry = counted
    try:
        parsed = render(data, [0, 1, 2, 3], AfterTwoPages())
    finally:
        pdf_render.pictures.entry = entry
    assert [stored.position for stored in parsed.images] == [0, 1]
    assert parsed.truncated
    assert [note.code for note in parsed.notes] == ["time_limit"]


def test_a_page_with_thousands_of_images_ends_inside_the_budget() -> None:
    data = fx.many_pictures(5000)
    parsed = render(data, [0])
    assert len(parsed.images) == 1
    # And the search for a covering image looks at a bounded number of objects.
    assert pdf_render.OBJECTS_MAX == 2000
    small = fx.scan_pdf(
        [fx.Sheet(fx.jpeg_picture(), text=fx.SHORT, box=(0, 0, 10, 10), draws=5000)]
    )
    assert render(small, [0], check=[0]).images == ()


@pytest.mark.parametrize("filter_name", ["JBIG2Decode", "JPXDecode"])
def test_a_jbig2_or_jpx_stream_of_noise_is_a_blank_page_or_a_page_left_out(
    filter_name: str,
) -> None:
    parsed = render(fx.with_broken_picture(filter_name), [0])
    assert len(parsed.images) + sum(note.values.get("count", 0) for note in parsed.notes) == 1


HOSTILE = [
    ("not a PDF", lambda: b"PK\x03\x04 not a pdf", {"pages": [0]}, ("file", "unreadable")),
    ("a PDF cut short", pdfs.truncated_pdf, {"pages": [0]}, ("file", "unreadable")),
    ("a header and nothing else", lambda: b"%PDF-1.7\n", {"text": True}, ("file", "unreadable")),
    ("a real password", lambda: pdfs.encrypted(fx.scan(1), "AES-256", "secret"), {"pages": [0]},
     ("file", "protected")),
    ("options of no job", lambda: fx.scan(1), {}, ("server", "handler_error")),
    ("pages out of order", lambda: fx.scan(3), {"pages": [2, 1]}, ("server", "handler_error")),
    ("a page index over the cap", lambda: fx.scan(1), {"pages": [200]},
     ("server", "handler_error")),
    ("a limit over a batch", lambda: fx.scan(1), {"pages": [0], "limit": 9},
     ("server", "handler_error")),
    ("a check outside the pages", lambda: fx.scan(2), {"pages": [0], "check": [1]},
     ("server", "handler_error")),
]  # fmt: skip


@pytest.mark.parametrize(
    ("name", "make", "options", "outcome"), HOSTILE, ids=[row[0] for row in HOSTILE]
)
def test_a_hostile_file_or_request_ends_as_a_code(
    name: str, make: Any, options: dict, outcome: tuple[str, str]
) -> None:
    assert refusal(make(), options) == outcome


def test_pages_the_file_does_not_have_are_counted_as_left_out() -> None:
    parsed = render(fx.scan(2), [0, 1, 2, 3])
    assert [stored.position for stored in parsed.images] == [0, 1]
    assert [(note.code, dict(note.values)) for note in parsed.notes] == [
        ("pages_not_rendered", {"count": 2})
    ]


def test_a_memory_error_anywhere_ends_as_the_code_of_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_memory(*_: object, **__: object) -> None:
        raise MemoryError

    for name in ("open_document", "render_page", "draw"):
        with monkeypatch.context() as patch:
            patch.setattr(pdf_render, name, no_memory)
            assert refusal(fx.scan(1), {"pages": [0]}) == ("file", "bomb")
    for name in ("page_text", "read_text"):
        with monkeypatch.context() as patch:
            patch.setattr(pdf_render, name, no_memory)
            assert refusal(pdfs.text_pdf(1), {"text": True}) == ("file", "bomb")
    with monkeypatch.context() as patch:
        patch.setattr(pdf_render.pictures, "encode", no_memory)
        assert refusal(fx.scan(1), {"pages": [0]}) == ("file", "bomb")


# --- the content of a page is weighed before PDFium loads it (review of PR #157, B2) ------------


@pytest.fixture
def loaded(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The pages PDFium was asked to load."""
    asked: list[int] = []
    get_page = pdf_render.pdfium.PdfDocument.get_page

    def counted(self: Any, index: int) -> Any:
        asked.append(index)
        return get_page(self, index)

    monkeypatch.setattr(pdf_render.pdfium.PdfDocument, "get_page", counted)
    return asked


def paths(size: int) -> bytes:
    return fx.content_pdf([fx.deflated_paths(size)])


def test_a_page_whose_content_decodes_past_the_cap_is_a_bomb_pdfium_never_loads(
    loaded: list[int],
) -> None:
    assert pdf_render.CONTENT_MAX == 4 * 1024 * 1024
    assert refusal(paths(8 * fx.MIB), {"pages": [0]}) == ("file", "bomb")
    assert refusal(paths(pdf_render.CONTENT_MAX + len(fx.PATH)), {"pages": [0]}) == ("file", "bomb")
    assert refusal(paths(8 * fx.MIB), {"pages": [0], "check": [0]}) == ("file", "bomb")
    assert refusal(paths(8 * fx.MIB), {"text": True}) == ("file", "bomb")
    two = fx.content_pdf([fx.deflated_paths(3 * fx.MIB), fx.deflated_paths(3 * fx.MIB)])
    assert refusal(two, {"pages": [0]}) == ("file", "bomb")
    assert loaded == []


def test_the_pdf_of_the_review_is_refused_in_no_time_and_no_memory(loaded: list[int]) -> None:
    """381 KB that took 3,976 MiB and 15.6 s of CPU when PDFium loaded the page."""
    import time
    import tracemalloc

    data = fx.content_bomb()
    assert len(data) < 400_000
    tracemalloc.start()
    started = time.process_time()
    try:
        assert refusal(data, {"pages": [0]}) == ("file", "bomb")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert time.process_time() - started < 3
    assert peak < 16 * fx.MIB, peak
    assert loaded == []


def test_a_page_at_the_cap_is_rendered(loaded: list[int]) -> None:
    parsed = render(paths(pdf_render.CONTENT_MAX), [0])
    assert [stored.position for stored in parsed.images] == [0]
    assert loaded == [0]


def test_the_streams_of_a_page_are_counted(loaded: list[int]) -> None:
    one = fx.deflated_paths(len(fx.PATH))
    most = pdf_render.CONTENT_STREAMS_MAX
    assert len(render(fx.content_pdf([one] * most), [0]).images) == 1
    assert refusal(fx.content_pdf([one] * (most + 1)), {"pages": [0]}) == ("file", "bomb")
    assert loaded == [0]


def test_the_pages_one_job_loads_are_weighed_together(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pdf_render.JOB_CONTENT_MAX == 64 * 1024 * 1024
    scan = fx.scan(3)
    assert len(render(scan, [0, 1, 2]).images) == 3
    contents = pdf_render._Contents(scan)
    contents.weigh(0)
    assert 0 < contents.total < 200  # one picture drawn: a few dozen bytes
    monkeypatch.setattr(pdf_render, "JOB_CONTENT_MAX", contents.total * 2)
    assert refusal(scan, {"pages": [0, 1, 2]}) == ("file", "bomb")
    assert len(render(scan, [0, 1]).images) == 2


def test_a_file_pypdf_cannot_weigh_is_left_to_pdfium(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is a guard: what it cannot read it does not refuse (the limit of the child
    and the client's reading of its death hold there)."""
    monkeypatch.setattr(pdf_render._Contents, "_open", lambda self: None)
    assert len(render(fx.scan(2), [0, 1]).images) == 2
    monkeypatch.undo()

    def broken(self: Any, index: int) -> int:
        raise ValueError("pypdf could not read this page")

    monkeypatch.setattr(pdf_render._Contents, "_size", broken)
    assert len(render(fx.scan(2), [0, 1]).images) == 2
