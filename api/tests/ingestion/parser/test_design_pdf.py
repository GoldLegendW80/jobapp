"""The layout reader of a PDF for the design capture (documents D25a, sandbox/design_pdf.py), in
the test process on files made in code, every answer read back through the worker's own check
(parser_client.check_design_pdf). The container tests run the same files in the real child."""

import json
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
    ]
    assert all(mine == theirs for mine, theirs in pairs), pairs


# --- the budget and hostile files ---------------------------------------------------------------


def test_a_budget_spent_before_a_page_is_too_slow_and_spent_inside_one_keeps_it() -> None:
    assert refusal(fx.one_column(), budget=SpentAfter(0)) == ("file", "too_slow")
    found = ask(fx.many_runs(lines=80, per_line=20), budget=SpentAfter(2))
    assert found.truncated
    assert "time_limit" in {note.code for note in found.notes}
    assert 0 < len(found.read[0].runs) < design_pdf.RUNS_MAX


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("not a pdf", b"PK\x03\x04 a zip", "unreadable"),
        ("cut in the middle", pdfs.truncated_pdf(), "unreadable"),
        ("content past the cap", scans.content_pdf([scans.deflated_paths(8 * scans.MIB)]), "bomb"),
        ("a password", pdfs.encrypted(pdfs.text_pdf(1), "AES-256", "secret"), "protected"),
    ],
)
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
