"""The layout reader of a PDF for the design capture (documents D25a, sandbox/design_pdf.py), in
the test process on files made in code, every answer read back through the worker's own check
(parser_client.check_design_pdf). The container tests run the same files in the real child."""

import json
import unicodedata
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion import parser_client as pc
from tests.ingestion import design_fixtures as fx
from tests.ingestion import pdf_fixtures as pdfs
from tests.ingestion import scan_fixtures as scans
from tests.ingestion.parser.helpers import (
    Plenty,
    SpentAfter,
    as_in_the_child,
    sandbox_handler,
    sandbox_module,
)

main = sandbox_module("main")
design_pdf = sandbox_handler("design_pdf")


@pytest.fixture(autouse=True)
def beside_its_helpers() -> Iterator[None]:
    with as_in_the_child():
        yield


def raw(data: bytes, options: dict | None = None, budget: Any = None) -> dict:
    return design_pdf.handle(data, options or {}, budget or Plenty())


def ask(data: bytes, budget: Any = None) -> pc.DesignPdf:
    return pc.check_design_pdf(pc.loads(main.encode(raw(data, budget=budget))))


def refusal(data: bytes, options: dict | None = None, budget: Any = None) -> tuple[str, str]:
    with pytest.raises(pc.ParserRefusedError) as caught:
        pc.check_design_pdf(pc.loads(main.encode(raw(data, options, budget))))
    return caught.value.failure, caught.value.code


def texts(page: pc.DesignPage) -> list[str]:
    return [run.text for run in page.runs]


# --- what a page holds --------------------------------------------------------------------------


def test_a_one_column_cv_gives_its_runs_fonts_sizes_and_colours() -> None:
    found = ask(fx.one_column())
    assert (found.pages, found.scanned, found.truncated, found.notes) == (1, False, False, ())
    (page,) = found.read
    assert (page.index, page.width, page.height) == (0, 5950, 8420)
    assert texts(page) == [
        "Alex Example",
        "Lead engineer at Example Robotics",
        "Experience",
        "Example Robotics, built the " + fx.CANARY + " planner",
        "2021 to 2024",
    ]
    name, headline, heading, body, dates = page.runs
    assert found.fonts[name.font] == pc.DesignFont(
        "ABCDEF+Calibri-Bold", 700, False, False, False, False, False
    )
    assert found.fonts[headline.font].name == "Helvetica-Bold"
    assert found.fonts[body.font].name == "Times-Roman"
    assert found.fonts[dates.font].name == "Courier"
    assert [run.size for run in page.runs] == [200, 110, 120, 110, 90]
    assert (name.colour, heading.colour, body.colour) == ("#ffffff", "#1f4f78", "#000000")
    # Boxes from the top left corner, in tenths of a point: the name sits in the band.
    assert name.box[0] == 560
    assert 0 < name.box[1] < name.box[3] < 1000
    top, right = min(r.box[1] for r in page.runs), page.width - max(r.box[2] for r in page.runs)
    bottom = page.height - max(r.box[3] for r in page.runs)
    assert page.margins == (top, right, bottom, min(r.box[0] for r in page.runs))
    assert right == page.width - dates.box[2]
    assert 0 < top < bottom


def test_bands_rules_outlines_and_shapes_inside_a_form_are_shapes_and_a_photo_a_box() -> None:
    (page,) = ask(fx.one_column()).read
    band, red_rule, frame, in_form = page.shapes
    assert (band.type, band.box, band.colour) == ("fill", (0, 0, 5950, 1000), "#1f4f78")
    assert (red_rule.type, red_rule.colour, red_rule.width) == ("rule", "#cc0000", 15)
    assert red_rule.box[3] - red_rule.box[1] <= 30
    assert (frame.type, frame.colour, frame.width) == ("outline", "#1f4f78", 20)
    # Drawn at (0, 0) inside a form whose matrix moves it by (100, 50): where it shows.
    assert (in_form.type, in_form.box, in_form.colour) == (
        "fill",
        (1000, 7720, 1500, 7920),
        "#cc0000",
    )
    assert page.images == ((4800, 100, 5600, 900),)


def test_a_two_column_page_written_across_its_columns_is_read_column_by_column() -> None:
    (page,) = ask(fx.two_columns()).read
    assert texts(page) == ["Alex Example", *fx.LEFT, *fx.RIGHT]
    assert (page.shapes[0].type, page.shapes[0].box) == ("fill", (0, 0, 1900, 8420))


def test_a_scan_says_so_and_its_text_layer_never_reaches_the_answer() -> None:
    for data in (fx.scanned(), fx.scanned(ocr="Alex Example " + fx.CANARY), scans.scan(1)):
        found = ask(data)
        assert found.scanned is True
        assert all(not page.runs and page.margins is None for page in found.read)
        assert found.read[0].images
        assert fx.CANARY not in json.dumps(raw(data))


def test_a_page_of_text_with_a_small_picture_is_not_a_scan() -> None:
    assert ask(fx.one_column()).scanned is False
    assert ask(scans.mixed()).scanned is False  # a scan page after a text page: text was read


def test_text_past_the_edge_of_the_page_is_left_out() -> None:
    (page,) = ask(fx.outside()).read
    assert texts(page) == ["On the page"]
    assert fx.CANARY not in json.dumps(raw(fx.outside()))


def test_only_the_first_two_pages_are_read_and_the_count_is_the_files() -> None:
    found = ask(fx.pages(3))
    assert found.pages == 3
    assert [page.index for page in found.read] == [0, 1]
    assert [texts(page) for page in found.read] == [["Page 1"], ["Page 2"]]
    assert ask(fx.pages(1)).pages == 1


# --- sizes, directions and strokes under every matrix -------------------------------------------


def said(start: bytes, end: bytes = b"ET") -> bytes:
    """The line "Alex Example engineer" after `start` (the size and the matrices) and before
    `end`."""
    return start + b" (Alex Example engineer) Tj " + end


TENTH = b"q 0.1 0 0 0.1 0 0 cm /Fm1 Do Q"
TENFOLD = b"q 10 0 0 10 0 0 cm /Fm1 Do Q"

# One line "Alex Example engineer" at 11 points on the page, the size written where PDF writers
# put it: (name, content, form, the run's size in tenths).
SIZES = [
    ("in Tf", said(b"BT /F2 11 Tf 1 0 0 1 56 700 Tm"), b"", 110),
    ("in Tm, as Quartz writes", said(b"BT /F2 1 Tf 11 0 0 11 56 700 Tm"), b"", 110),
    (
        "in a Tm per word",
        b"BT /F2 1 Tf 11 0 0 11 56 700 Tm (Alex) Tj 11 0 0 11 80.2 700 Tm (Example) Tj "
        b"11 0 0 11 122 700 Tm (engineer) Tj ET",
        b"",
        110,
    ),
    (
        "in cm, as Ghostscript writes",
        said(b"q 0.1 0 0 0.1 0 0 cm BT /F2 110 Tf 1 0 0 1 560 7000 Tm", b"ET Q"),
        b"",
        110,
    ),
    (
        "in a cm that enlarges",
        said(b"q 10 0 0 10 0 0 cm BT /F2 1.1 Tf 1 0 0 1 5.6 70 Tm", b"ET Q"),
        b"",
        110,
    ),
    (
        "in Tf, Tm and cm",
        said(b"q 2 0 0 2 0 0 cm BT /F2 0.5 Tf 11 0 0 11 28 350 Tm", b"ET Q"),
        b"",
        110,
    ),
    ("in a form drawn at a tenth", TENTH, said(b"BT /F2 110 Tf 1 0 0 1 560 2000 Tm"), 110),
    ("a large Tf in a small Tm", said(b"BT /F2 11000 Tf 0.001 0 0 0.001 56 700 Tm"), b"", 110),
    (
        "20000 Tf in a 0.001 Tm, 20 points",
        said(b"BT /F2 20000 Tf 0.001 0 0 0.001 56 700 Tm"),
        b"",
        200,
    ),
    ("a negative Tf, read leftwards", said(b"BT /F2 -11 Tf 1 0 0 1 300 700 Tm"), b"", 110),
    ("a negative Tm, read leftwards", said(b"BT /F2 1 Tf -11 0 0 -11 300 700 Tm"), b"", 110),
    ("a negative Tf in a negative Tm", said(b"BT /F2 -11 Tf -1 0 0 -1 56 700 Tm"), b"", 110),
    ("mirrored top to bottom", said(b"BT /F2 11 Tf 1 0 0 -1 56 700 Tm"), b"", 110),
    ("a half turn", said(b"BT /F2 11 Tf -1 0 0 -1 300 700 Tm"), b"", 110),
    ("a quarter turn, reading up", said(b"BT /F2 11 Tf 0 1 -1 0 300 300 Tm"), b"", 110),
    ("a quarter turn, reading down", said(b"BT /F2 11 Tf 0 -1 1 0 300 600 Tm"), b"", 110),
    (
        "a quarter turn in cm",
        said(b"q 0 1 -1 0 595 0 cm BT /F2 11 Tf 1 0 0 1 300 300 Tm", b"ET Q"),
        b"",
        110,
    ),
    ("turned by 20 degrees", said(b"BT /F2 11 Tf 0.9397 0.342 -0.342 0.9397 100 300 Tm"), b"", 110),
    ("turned by 70 degrees", said(b"BT /F2 11 Tf 0.342 0.9397 -0.9397 0.342 300 300 Tm"), b"", 110),
    ("slanted, as a made up italic", said(b"BT /F2 11 Tf 1 0 0.21 1 56 700 Tm"), b"", 110),
    ("narrowed by Tz", said(b"BT /F2 11 Tf 50 Tz 1 0 0 1 56 700 Tm"), b"", 110),
    ("narrowed in Tm", said(b"BT /F2 11 Tf 0.5 0 0 1 56 700 Tm"), b"", 110),
    ("widened in cm", said(b"q 2 0 0 1 0 0 cm BT /F2 11 Tf 1 0 0 1 28 700 Tm", b"ET Q"), b"", 110),
]


@pytest.mark.parametrize(("name", "content", "form", "size"), SIZES, ids=[row[0] for row in SIZES])
def test_the_size_of_a_run_is_its_height_on_the_page(
    name: str, content: bytes, form: bytes, size: int
) -> None:
    (page,) = ask(fx.build([content], form=form)).read
    assert [(run.text, run.size) for run in page.runs] == [("Alex Example engineer", size)], name


HUGE = b"99999999999999999999999999999999999999999"
ZILLION = b"1000000000000000000000"


def test_text_turned_by_an_eighth_is_cut_into_letters_that_keep_their_size() -> None:
    """A declared open point: from about 28 to 62 degrees off an axis, the upright boxes PDFium
    gives overlap too much along the line to follow it."""
    turned = said(b"BT /F2 11 Tf 0.7071 0.7071 -0.7071 0.7071 100 300 Tm")
    (page,) = ask(fx.build([turned])).read
    assert len(page.runs) > 3
    assert {run.size for run in page.runs} == {110}


# Odd and extreme matrices: never a failure of the answer, every size within its bounds.
EXTREMES = [
    ("a page sized glyph", b"BT /F2 1 Tf 20000 0 0 20000 0 0 Tm (A) Tj ET", [("A", 144_000)]),
    ("a cm of 100000", b"q 100000 0 0 100000 0 0 cm BT /F2 9 Tf (A) Tj ET Q", [("A", 144_000)]),
    ("a Tf of minus a million", b"BT /F2 -1000000 Tf 1 0 0 1 0 842 Tm (A) Tj ET", [("A", 144_000)]),
    (
        "under a twentieth of a point",
        b"BT /F2 0.04 Tf 1 0 0 1 56 700 Tm (Alex) Tj ET",
        [("Alex", 0)],
    ),
    ("a Tm of nothing", b"BT /F2 11 Tf 0 0 0 0 56 700 Tm (Alex) Tj ET", []),
    ("a Tf of nothing", b"BT /F2 0 Tf 1 0 0 1 56 700 Tm (Alex) Tj ET", []),
    (
        "numbers past a float",
        b"q %s 0 0 %s 0 0 cm BT /F2 %s Tf (A) Tj ET Q" % (HUGE, HUGE, HUGE),
        None,
    ),
    (
        "a CTM past a float",
        b"q %s 0 0 %s 0 0 cm " % (ZILLION, ZILLION) * 2 + b"BT /F2 11 Tf (A) Tj ET Q Q",
        None,
    ),
]


@pytest.mark.parametrize(("name", "content", "runs"), EXTREMES, ids=[row[0] for row in EXTREMES])
def test_an_odd_matrix_never_fails_the_answer(name: str, content: bytes, runs: list | None) -> None:
    found = ask(fx.build([content]))
    found_runs = [(run.text, run.size) for page in found.read for run in page.runs]
    assert all(0 <= size <= pc.DESIGN_LENGTH for _, size in found_runs), name
    if runs is not None:
        assert found_runs == runs, name


RED_RULE = b"1 0 0 RG %s w %s %s m %s %s l S"

# A red rule 1.5 points wide, the width written where PDF writers put it: (name, content, form).
STROKES = [
    ("in w", RED_RULE % (b"1.5", b"56", b"500", b"500", b"500"), b""),
    (
        "in cm, as Ghostscript writes",
        b"q 0.1 0 0 0.1 0 0 cm " + RED_RULE % (b"15", b"560", b"5000", b"5000", b"5000") + b" Q",
        b"",
    ),
    (
        "in a cm that enlarges",
        b"q 10 0 0 10 0 0 cm " + RED_RULE % (b"0.15", b"5.6", b"50", b"50", b"50") + b" Q",
        b"",
    ),
    (
        "in a turned cm",
        b"q 0 1 -1 0 595 0 cm " + RED_RULE % (b"1.5", b"100", b"100", b"100", b"500") + b" Q",
        b"",
    ),
    (
        "in a mirrored cm",
        b"q -1 0 0 -1 595 842 cm " + RED_RULE % (b"1.5", b"56", b"300", b"500", b"300") + b" Q",
        b"",
    ),
    ("in a form drawn at a tenth", TENTH, RED_RULE % (b"15", b"560", b"1000", b"4000", b"1000")),
    ("in a form drawn tenfold", TENFOLD, RED_RULE % (b"0.15", b"-94.4", b"0", b"-50", b"0")),
]


@pytest.mark.parametrize(("name", "content", "form"), STROKES, ids=[row[0] for row in STROKES])
def test_the_width_of_a_stroke_is_its_width_on_the_page(
    name: str, content: bytes, form: bytes
) -> None:
    (page,) = ask(fx.build([content], form=form)).read
    assert [(shape.type, shape.colour, shape.width) for shape in page.shapes] == [
        ("rule", "#ff0000", 15)
    ], name


def test_an_uneven_scale_gives_a_stroke_the_mean_of_its_two_and_a_huge_one_is_clamped() -> None:
    uneven = b"q 1 0 0 0.25 0 0 cm 1 0 0 RG 4 w 56 2000 m 500 2000 l S Q"
    (page,) = ask(fx.build([uneven])).read
    assert [shape.width for shape in page.shapes] == [20]
    huge = b"q 1000 0 0 1000 0 0 cm 1 0 0 RG 5 w 0 0.4 m 0.5 0.4 l S Q"
    (page,) = ask(fx.build([huge])).read
    assert [shape.width for shape in page.shapes] == [design_pdf.STROKE_MAX]


def test_only_painted_text_is_read_in_the_colour_it_is_painted() -> None:
    def painted(mode: int, words: str) -> bytes:
        return b"BT /F2 11 Tf 0 0 0 rg 1 0 0 RG %d Tr 1 0 0 1 56 %d Tm (%s) Tj ET\n" % (
            mode,
            700 - 20 * mode,
            words.encode(),
        )

    names = ["Fill", "Stroke", "Both", "Hidden", "Fill clip", "Stroke clip", "Both clip"]
    content = b"".join(painted(mode, name) for mode, name in enumerate(names))
    data = fx.build([content + painted(7, "Clip " + fx.CANARY)])
    (page,) = ask(data).read
    assert [(run.text, run.colour) for run in page.runs] == [
        ("Fill", "#000000"),
        ("Stroke", "#ff0000"),
        ("Both", "#000000"),
        ("Fill clip", "#000000"),
        ("Stroke clip", "#ff0000"),
        ("Both clip", "#000000"),
    ]
    assert fx.CANARY not in json.dumps(raw(data))


def test_a_scan_whose_text_layer_only_clips_is_a_scan() -> None:
    data = fx.build(
        [fx.picture(0, 0, 595, 842) + fx.text("Alex Example " + fx.CANARY, 56, 700, mode=7)]
    )
    found = ask(data)
    assert found.scanned is True
    assert fx.CANARY not in json.dumps(raw(data))


# --- caps ---------------------------------------------------------------------------------------


def test_runs_past_the_cap_are_cut_with_a_note() -> None:
    found = ask(fx.many_runs(lines=80, per_line=20))
    assert len(found.read[0].runs) == design_pdf.RUNS_MAX
    assert found.truncated
    assert "runs_limit" in {note.code for note in found.notes}


def test_a_long_run_is_cut_at_the_run_cap_and_a_page_at_the_character_cap() -> None:
    (page,) = ask(fx.long_lines(lines=1, characters=400)).read
    assert [len(run.text) for run in page.runs] == [300, 100]
    found = ask(fx.long_lines(lines=200, characters=280))
    assert sum(len(run.text) for run in found.read[0].runs) <= design_pdf.CHARS_MAX
    assert "chars_limit" in {note.code for note in found.notes}


def test_the_text_of_an_answer_stops_at_its_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(design_pdf, "TEXT_MAX", 1_000)
    found = ask(fx.long_lines(lines=10, characters=280))
    assert sum(len(run.text) for run in found.read[0].runs) <= 1_000
    assert found.truncated
    assert "text_limit" in {note.code for note in found.notes}


def test_shapes_past_the_cap_keep_the_largest() -> None:
    found = ask(fx.many_shapes(500))
    shapes = found.read[0].shapes
    assert len(shapes) == design_pdf.SHAPES_MAX
    assert "shapes_limit" in {note.code for note in found.notes}
    # Sides of 3 to 27.9 points, each twice: the hundred left out are those under 8 points.
    assert min(shape.box[2] - shape.box[0] for shape in shapes) == 80


def test_the_largest_answer_the_caps_allow_fits_the_read_of_the_client() -> None:
    """Every cap at once, the text in characters of four bytes and quotes: under
    DESIGN_MAX_ANSWER, and accepted by the client."""
    width, height = 144_000, 144_000
    per_run = design_pdf.TEXT_MAX // (design_pdf.PAGES_READ * design_pdf.RUNS_MAX)
    run = {
        "text": ("\U0001f600" * per_run),
        "box": [143_999, 143_999, 144_000, 144_000],
        "font": design_pdf.FONTS_MAX - 1,
        "size": 144_000,
        "colour": "#abcdef",
    }
    shape = {"type": "outline", "box": run["box"], "colour": "#abcdef", "width": 1000}
    page = {
        "width": width,
        "height": height,
        "margins": [144_000] * 4,
        "runs": [run] * design_pdf.RUNS_MAX,
        "shapes": [shape] * design_pdf.SHAPES_MAX,
        "images": [{"box": run["box"]}] * design_pdf.IMAGES_MAX,
    }
    font = {"name": '"' * design_pdf.FONT_NAME_MAX, "weight": 900, **dict.fromkeys(
        ("italic", "serif", "monospace", "symbolic", "embedded"), True
    )}  # fmt: skip
    answer = {
        "ok": True,
        "kind": "design_pdf",
        "pages": pc.MAX_PAGES,
        "scanned": False,
        "truncated": True,
        "notes": [{"code": code} for code in sorted(pc.DESIGN_NOTE_CODES)][: pc.MAX_NOTES],
        "fonts": [font] * design_pdf.FONTS_MAX,
        "read": [{"index": i, **page} for i in range(design_pdf.PAGES_READ)],
        "usage": {"cpu_ms": 10**10, "max_rss_kb": 10**10, "max_vm_kb": 10**10},
    }
    encoded = main.encode(answer)
    assert len(encoded) < pc.DESIGN_MAX_ANSWER < main.MAX_ANSWER
    assert len(pc.check_design_pdf(pc.loads(encoded)).read[1].runs) == design_pdf.RUNS_MAX


def test_the_caps_are_the_clients() -> None:
    pairs = [
        (design_pdf.PAGES_READ, pc.DESIGN_PAGES),
        (design_pdf.RUNS_MAX, pc.DESIGN_RUNS),
        (design_pdf.SHAPES_MAX, pc.DESIGN_SHAPES),
        (design_pdf.IMAGES_MAX, pc.DESIGN_IMAGES),
        (design_pdf.TEXT_MAX, pc.DESIGN_TEXT),
        (design_pdf.RUN_TEXT_MAX, pc.DESIGN_RUN_TEXT),
        (design_pdf.FONTS_MAX, pc.DESIGN_FONTS),
        (design_pdf.FONT_NAME_MAX, pc.DESIGN_FONT_NAME),
        (design_pdf.LENGTH_MAX, pc.DESIGN_LENGTH),
        (design_pdf.STROKE_MAX, pc.DESIGN_STROKE),
        (design_pdf.PAGES_MAX, pc.MAX_PAGES),
    ]
    assert all(mine == theirs for mine, theirs in pairs), pairs


# --- values the client would refuse: cleaned or left out, never the whole answer ----------------


@pytest.mark.parametrize(("letter", "written"), [(b"A", "\u0958"), (b"B", "\u2adc")])
def test_a_run_that_nfc_writes_longer_is_cut_at_the_run_cap(letter: bytes, written: str) -> None:
    content = b"BT /F5 1 Tf 1 0 0 1 20 700 Tm (%s) Tj ET" % (letter * 310)
    (page,) = ask(fx.build([content])).read
    whole = unicodedata.normalize("NFC", written * 310)
    assert all(len(run.text) <= design_pdf.RUN_TEXT_MAX for run in page.runs)
    assert len(unicodedata.normalize("NFC", written * 300)) > design_pdf.RUN_TEXT_MAX
    assert texts(page)[0] == whole[: design_pdf.RUN_TEXT_MAX]


@pytest.mark.parametrize("claimed", [pc.MAX_PAGES, pc.MAX_PAGES + 1, 500_000])
def test_a_page_count_past_the_clients_cap_is_given_as_the_cap(claimed: int) -> None:
    found = ask(fx.build([fx.text("Page 1", 56, 780)], count=claimed))
    assert found.pages == min(claimed, pc.MAX_PAGES)
    assert [texts(page) for page in found.read] == [["Page 1"]]


# A page size in points, and whether the page is read: a side under a twentieth of a point is
# zero tenths, and the page is not read.
TINY = [
    ((0.01, 0.01), False),
    ((0.04, 0.04), False),
    ((0.04, 0.3), False),
    ((0.3, 0.04), False),
    ((0.05, 0.05), True),
    ((0.06, 0.5), True),
    ((0.1, 1), True),
    ((1, 1), True),
]


@pytest.mark.parametrize(("size", "read"), TINY, ids=[repr(row[0]) for row in TINY])
def test_a_page_too_small_for_a_tenth_of_a_point_is_not_read(size: tuple, read: bool) -> None:
    found = ask(fx.build([b"0 0 1 rg 0 0 0.01 0.01 re f"], size=size))
    assert found.pages == 1
    assert len(found.read) == int(read)
    assert {note.code for note in found.notes} == (set() if read else {"pages_not_read"})


# --- the budget and hostile files ---------------------------------------------------------------


def test_a_budget_spent_before_a_page_is_too_slow_and_spent_inside_one_keeps_it() -> None:
    assert refusal(fx.one_column(), budget=SpentAfter(0)) == ("file", "too_slow")
    found = ask(fx.many_runs(lines=80, per_line=20), budget=SpentAfter(2))
    assert found.truncated
    assert "time_limit" in {note.code for note in found.notes}
    assert 0 < len(found.read[0].runs) < design_pdf.RUNS_MAX


HOSTILE = [
    ("not a pdf", b"PK\x03\x04 a zip", "unreadable"),
    ("cut in the middle", pdfs.truncated_pdf(), "unreadable"),
    ("content past the cap", scans.content_pdf([scans.deflated_paths(8 * scans.MIB)]), "bomb"),
    ("a password", pdfs.encrypted(pdfs.text_pdf(1), "AES-256", "secret"), "protected"),
]


@pytest.mark.parametrize(("name", "data", "code"), HOSTILE, ids=[row[0] for row in HOSTILE])
def test_a_hostile_file_ends_as_a_code_of_the_file(name: str, data: bytes, code: str) -> None:
    assert refusal(data) == ("file", code), name


def test_options_are_refused_as_the_servers_failure() -> None:
    assert refusal(fx.one_column(), {"pages": [0]}) == ("server", "handler_error")


# --- the reading order, as a table --------------------------------------------------------------


def box(x0: float, y0: float, x1: float, y1: float) -> list[int]:
    return [round(x0 * 10), round(y0 * 10), round(x1 * 10), round(y1 * 10)]


def line(x0: float, x1: float, y: float, height: float = 10) -> list[int]:
    return box(x0, y, x1, y + height)


def column(x0: float, x1: float, rows: int, top: float = 100, pitch: float = 14) -> list[list[int]]:
    return [line(x0, x1, top + pitch * row) for row in range(rows)]


ORDER = [
    ("nothing", [], []),
    ("one box", [line(50, 100, 50)], [0]),
    ("one line left to right", [line(300, 400, 50), line(50, 200, 50)], [1, 0]),
    (
        "a paragraph, top to bottom",
        [line(50, 500, 80), line(50, 500, 66), line(50, 500, 52)],
        [2, 1, 0],
    ),
    # Two columns at the same heights, written across: left column first.
    (
        "two aligned columns",
        [b for pair in zip(column(40, 180, 4), column(220, 540, 4), strict=True) for b in pair],
        [0, 2, 4, 6, 1, 3, 5, 7],
    ),
    # A title and its dates on two lines: two bands are not columns, each read left to right.
    (
        "title and dates",
        [line(50, 200, 50), line(450, 540, 50), line(50, 180, 64), line(450, 540, 64)],
        [0, 1, 2, 3],
    ),
    (
        "header over two columns",
        [line(40, 540, 30, 20), *column(40, 180, 3), *column(220, 540, 3)],
        [0, 1, 2, 3, 4, 5, 6],
    ),
    (
        "three columns",
        [*column(20, 150, 3), *column(200, 350, 3), *column(400, 560, 3)],
        list(range(9)),
    ),
    # A blank line in the right column does not break the block.
    (
        "a gap in the right column",
        [
            *column(40, 180, 5),
            line(220, 540, 100),
            line(220, 540, 114),
            line(220, 540, 142),
            line(220, 540, 156),
        ],
        list(range(9)),
    ),
    ("a shorter right column", [*column(40, 180, 6), *column(220, 540, 3)], list(range(9))),
    # Loose boxes of lines set tight touch: still a paragraph read top to bottom.
    (
        "tight leading",
        [box(50, 100, 500, 112), box(50, 109, 500, 121), box(50, 118, 500, 130)],
        [0, 1, 2],
    ),
    ("a superscript", [line(50, 100, 50), box(101, 48, 106, 54), line(108, 200, 50)], [0, 1, 2]),
    # A gap narrower than the gutter is a space between words, not two columns.
    ("words, not columns", [*column(40, 200, 3), *column(205, 400, 3)], [0, 3, 1, 4, 2, 5]),
    (
        "a full line under two columns",
        [*column(40, 180, 3), *column(220, 540, 3), line(40, 540, 200)],
        list(range(7)),
    ),
    (
        "a sidebar right of the main column",
        [*column(40, 380, 4), *column(420, 560, 4)],
        list(range(8)),
    ),
    # Declared: three titles with their dates on the right read as two columns.
    (
        "three title and date lines",
        [
            line(50, 200, 50),
            line(450, 540, 50),
            line(50, 180, 64),
            line(450, 540, 64),
            line(50, 190, 78),
            line(450, 540, 78),
        ],
        [0, 2, 4, 1, 3, 5],
    ),
    ("a gutter of exactly 12 points", [*column(40, 180, 3), *column(192, 400, 3)], list(range(6))),
    ("a gap of 11.9 points", [*column(40, 180, 3), *column(191.9, 400, 3)], [0, 3, 1, 4, 2, 5]),
    (
        "a photo box beside the name",
        [box(40, 30, 120, 110), line(140, 400, 40, 22), line(140, 400, 70)],
        [0, 1, 2],
    ),
]


@pytest.mark.parametrize(("name", "boxes", "order"), ORDER, ids=[row[0] for row in ORDER])
def test_reading_order(name: str, boxes: list[list[int]], order: list[int]) -> None:
    assert design_pdf.reading_order(boxes) == order, name


def test_reading_order_keeps_every_box_once_on_any_layout() -> None:
    import random

    noise = random.Random(25)  # noqa: S311, a fixture, not a secret
    for _ in range(200):
        boxes = []
        for _ in range(noise.randint(0, 60)):
            x0, y0 = noise.uniform(0, 500), noise.uniform(0, 800)
            boxes.append(box(x0, y0, x0 + noise.uniform(0, 90), y0 + noise.uniform(0, 30)))
        assert sorted(design_pdf.reading_order(boxes)) == list(range(len(boxes)))
