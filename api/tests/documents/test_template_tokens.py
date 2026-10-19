"""template_rules' tokens (task D25a): what a design answer of the sandbox says of the look, as
values the template system takes, each within its bounds or its named default with a Note.

Invented layouts built as the parser client returns them (lengths in tenths of a point from the
top left), one per rule and its neighbours, then a property over random layouts: whatever the
answer, every value is within its bounds, every colour holds 4.5:1 where it is printed, the
fonts are of the library, and the density ladder is one the manifest validator accepts.
"""

import random
from dataclasses import replace

import pytest

from app.documents import font_map, template_rules, templates
from app.documents.template_rules import (
    BOUNDS,
    DEFAULTS,
    MM,
    NOTE_CODES,
    TIGHTEST,
    Note,
    contrast,
    from_docx,
    from_pdf,
    steps,
)
from app.ingestion.parser_client import (
    DESIGN_PROPS,
    DesignDocx,
    DesignFont,
    DesignPage,
    DesignPdf,
    Shape,
    TextRun,
)

W, H = 5953, 8419
FONTS = (
    DesignFont("ABCDEF+Calibri", 400, False, False, False, False, True),
    DesignFont("ABCDEF+Calibri-Bold", 700, False, False, False, False, True),
    DesignFont("ABCDEF+Georgia-Bold", 700, False, True, False, False, True),
    DesignFont("ABCDEF+Wingdings-Regular", 400, False, False, False, True, True),
)
LEFT = round(20 * MM)


def _run(text, x0, y, x1, size=100, font=0, colour="#222222"):
    return TextRun(text, (x0, y, x1, y + size), font, size, colour)


def _column(x0, x1, y, lines, body=100, leading=130, colour="#222222", font=0):
    return [
        _run("Built invented tools for Example Corp", x0, y + k * leading, x1, body, font, colour)
        for k in range(lines)
    ]


def _cv(
    *, body=100, leading=130, text="#222222", accent="#1f4e79", heading_font=1, left=LEFT
) -> list[TextRun]:
    runs = [_run("Alex Example", left, 400, W - left, 240, 1, text)]
    y = 1800
    for _ in range(3):
        runs.append(_run("Experience", left, y, left + 900, 130, heading_font, accent))
        runs += _column(left, W - left, y + 250, 6, body, leading, text)
        y += 250 + 6 * leading + 200
    return runs


def _pdf(runs, shapes=(), width=W, height=H, margins="auto", fonts=FONTS) -> DesignPdf:
    if margins == "auto" and runs:
        xs = [r.box[0] for r in runs] + [width - r.box[2] for r in runs]
        top = min(r.box[1] for r in runs)
        bottom = height - max(r.box[3] for r in runs)
        margins = (top, min(width - r.box[2] for r in runs), bottom, min(xs[: len(runs)]))
    elif margins == "auto":
        margins = None
    page = DesignPage(0, width, height, margins, tuple(runs), tuple(shapes), ())
    return DesignPdf(1, False, False, (), fonts, (page,))


def test_a_one_column_cv_gives_its_margin_sizes_colours_and_fonts():
    tokens = from_pdf(_pdf(_cv()))
    assert (tokens.page, tokens.margin, tokens.columns, tokens.side) == ("A4", 20.0, 1, None)
    assert (tokens.body_size, tokens.line_height) == (10.0, 1.3)
    assert (tokens.name_scale, tokens.heading_scale) == (2.4, 1.3)
    assert (tokens.text, tokens.accent, tokens.fill) == ("#222222", "#1f4e79", None)
    assert tokens.fonts == ("carlito",)
    assert tokens.notes == ()
    assert from_pdf(_pdf(_cv(heading_font=2))).fonts == ("carlito", "source-serif-4")


@pytest.mark.parametrize("side", ["left", "right"])
def test_a_tinted_column_on_a_side_is_two_columns_with_its_fill_and_its_text(side):
    band = round(W * 0.3)
    x0 = 0 if side == "left" else W - band
    inside = _column(x0 + 200, x0 + band - 200, 2000, 8, colour="#ffffff")
    main = _cv(left=band + 400 if side == "left" else LEFT)
    main = [r for r in main if not (x0 <= (r.box[0] + r.box[2]) / 2 <= x0 + band)]
    tokens = from_pdf(_pdf(main + inside, [Shape("fill", (x0, 0, x0 + band, H), "#1f3a5f", 0)]))
    assert (tokens.columns, tokens.side, tokens.side_width) == (2, side, round(band / MM, 1))
    assert (tokens.fill, tokens.on_fill) == ("#1f3a5f", "#ffffff")


def test_a_band_across_the_top_is_a_fill_and_one_column():
    runs = [replace(r, colour="#ffffff") if r.size == 240 else r for r in _cv()]
    tokens = from_pdf(_pdf(runs, [Shape("fill", (0, 0, W, 1200), "#1f4e79", 0)]))
    assert (tokens.columns, tokens.fill, tokens.on_fill) == (1, "#1f4e79", "#ffffff")


@pytest.mark.parametrize(
    ("fill", "inside", "on_fill", "notes"),
    [
        ("#f2f2f2", "#999999", "#222222", ()),  # the inside grey fails, the body text holds
        ("#333333", "#444444", "#ffffff", ()),
        ("#777777", "#888888", None, (Note("on_fill", "low_contrast"),)),
        ("#fdfdfd", "#222222", None, ()),  # near white: no fill at all
    ],
)
def test_the_text_on_a_fill_holds_4_5_to_1_or_the_fill_is_dropped(fill, inside, on_fill, notes):
    band = round(W * 0.3)
    runs = _column(200, band - 200, 2000, 8, colour=inside) + [
        r for r in _cv(left=band + 400) if r.box[0] > band
    ]
    tokens = from_pdf(_pdf(runs, [Shape("fill", (0, 0, band, H), fill, 0)]))
    assert tokens.on_fill == on_fill
    assert tokens.fill == (fill if on_fill else None)
    assert tokens.notes == notes


def test_two_columns_by_a_gutter_and_one_when_a_line_crosses_it():
    left = _column(LEFT, 2000, 2000, 20)
    right = _column(2300, W - LEFT, 2000, 20)
    tokens = from_pdf(_pdf(left + right))
    assert (tokens.columns, tokens.side, tokens.side_width) == (
        2,
        "left",
        round((2000 - LEFT) / MM, 1),
    )
    crossing = _column(LEFT, W - LEFT, 5000, 1)
    assert from_pdf(_pdf(left + right + crossing)).columns == 1
    narrow = _column(LEFT, 2300 - 119, 2000, 20)
    assert from_pdf(_pdf(narrow + right)).columns == 1
    mirrored = _column(LEFT, 3700, 2000, 20) + _column(4000, W - LEFT, 2000, 20)
    assert from_pdf(_pdf(mirrored)).side == "right"


def test_three_columns_are_read_as_one_and_said():
    runs = _column(LEFT, 1900, 2000, 20) + _column(2200, 3700, 2000, 20)
    runs += _column(4000, W - LEFT, 2000, 20)
    tokens = from_pdf(_pdf(runs))
    assert tokens.columns == 1
    assert Note("columns", "columns") in tokens.notes


@pytest.mark.parametrize(
    ("change", "token", "code", "value"),
    [
        ({"left": round(2 * MM)}, "margin", "out_of_bounds", DEFAULTS["margin"]),
        ({"left": round(31 * MM)}, "margin", "out_of_bounds", DEFAULTS["margin"]),
        ({"body": 200, "leading": 260}, "body_size", "out_of_bounds", DEFAULTS["body_size"]),
        ({"body": 40, "leading": 52}, "body_size", "out_of_bounds", DEFAULTS["body_size"]),
        ({"leading": 95}, "line_height", "out_of_bounds", DEFAULTS["line_height"]),
        ({"left": round(5 * MM) - 1}, "margin", "out_of_bounds", DEFAULTS["margin"]),
        ({"leading": 240}, "line_height", "out_of_bounds", DEFAULTS["line_height"]),
        ({"text": "#cccccc"}, "text", "low_contrast", DEFAULTS["text"]),
        ({"text": "#ffffff"}, "text", "low_contrast", DEFAULTS["text"]),
        ({"accent": "#ffff00"}, "accent", "low_contrast", DEFAULTS["accent"]),
        ({"accent": "#7fb3e6"}, "accent", "low_contrast", DEFAULTS["accent"]),
    ],
)
def test_a_value_out_of_bounds_or_under_the_contrast_is_its_default_and_said(
    change, token, code, value
):
    tokens = from_pdf(_pdf(_cv(**change)))
    assert getattr(tokens, token) == value
    assert Note(token, code) in tokens.notes


def test_at_the_bounds_the_value_is_kept():
    assert from_pdf(_pdf(_cv(left=round(5 * MM) + 1))).margin == 5.04
    assert from_pdf(_pdf(_cv(leading=100))).line_height == 1.0
    assert from_pdf(_pdf(_cv(body=140, leading=182))).body_size == 14.0
    assert from_pdf(_pdf(_cv(text="#767676"))).text == "#767676"  # 4.54 to 1


def test_a_light_accent_gives_way_to_a_darker_rule_of_the_page():
    rule = Shape("rule", (LEFT, 1500, W - LEFT, 1510), "#7a2e0e", 10)
    tokens = from_pdf(_pdf(_cv(accent="#ffff00"), [rule]))
    assert tokens.accent == "#7a2e0e"
    assert tokens.notes == ()


def test_a_letter_page_is_printed_on_a4_and_said():
    tokens = from_pdf(_pdf(_cv(), width=6120, height=7920))
    assert tokens.page == "A4"
    assert tokens.notes == (Note("page", "not_a4"),)


def test_a_scan_and_an_empty_answer_give_every_default_and_say_why():
    for design in (_pdf([]), DesignPdf(1, True, False, (), (), ())):
        tokens = from_pdf(design)
        assert (tokens.margin, tokens.body_size, tokens.line_height) == (15.0, 10.0, 1.35)
        assert (tokens.text, tokens.accent, tokens.columns) == ("#1d232b", "#1f4e79", 1)
        assert tokens.fonts == ("arimo",)
        assert {n.token for n in tokens.notes} >= {"margin", "body_size", "text", "accent"}


def test_a_symbol_font_is_never_a_family_of_the_tokens():
    runs = [replace(r, font=3) if r.size == 100 and k % 2 else r for k, r in enumerate(_cv())]
    runs += _column(LEFT, W - LEFT, 7000, 4, font=3)
    assert from_pdf(_pdf(runs)).fonts == ("carlito",)


def _style(id_, name, *, default=False, outline=None, based_on=None, **props):
    keys = dict.fromkeys(DESIGN_PROPS)
    return {
        "id": id_, "name": name, "type": "paragraph", "based_on": based_on, "default": default,
        "outline": outline, **keys, **props,
    }  # fmt: skip


def _docx(styles=(), sections=None, theme=None, tables=0, table_columns=0, **defaults):
    return DesignDocx(
        truncated=False,
        notes=(),
        theme=theme,
        defaults={**dict.fromkeys(DESIGN_PROPS), **defaults},
        styles=tuple(styles),
        sections=tuple(sections or ()),
        tables=tables,
        table_columns=table_columns,
        drawings=0,
        paragraphs=10,
        paragraph_styles=(),
        formats=(),
    )


WORD = {
    "styles": [
        _style("Normal", "Normal", default=True),
        _style("Heading1", "heading 1", outline=0, based_on="Normal", size=160,
               colour="#2f5496", font="Calibri Light"),
        _style("Title", "Title", based_on="Normal", size=280, font="Calibri Light"),
    ],
    "sections": [{"width": 5953, "height": 8419, "margins": (720, 720, 720, 720), "columns": 1}],
    "theme": {"major": "Calibri Light", "minor": "Calibri", "colours": {"dk1": "#000000",
              "accent1": "#4472c4"}},
    "font": "Calibri", "size": 110, "line": 108, "line_rule": "auto",
}  # fmt: skip


def test_a_word_cv_gives_its_page_sizes_colours_and_fonts():
    tokens = from_docx(_docx(**WORD))
    assert (tokens.page, tokens.margin, tokens.columns) == ("A4", 25.4, 1)
    assert (tokens.body_size, tokens.line_height) == (11.0, 1.3)
    assert (tokens.heading_scale, tokens.name_scale) == (1.45, 2.55)
    assert (tokens.text, tokens.accent, tokens.fill) == ("#000000", "#2f5496", None)
    assert tokens.fonts == ("carlito",)
    assert tokens.notes == ()


NOT_A4 = [{"width": 6120, "height": 7920, "margins": (720,) * 4, "columns": 1}]
THREE = [{"width": 5953, "height": 8419, "margins": None, "columns": 3}]
LIGHT = [_style("N", "Normal", default=True), _style("H", "x", outline=0, colour="#ffd966")]
CYCLE = [_style("A", "Normal", default=True, based_on="B"), _style("B", "B", based_on="A")]
DOCX_NEIGHBOURS = [
    # (the change, the token, its value)
    ({"sections": NOT_A4}, "notes", {Note("page", "not_a4")}),
    ({"tables": 1, "table_columns": 2}, "columns", 2),
    ({"tables": 1, "table_columns": 5}, "columns", 1),
    ({"sections": THREE}, "notes", {Note("columns", "columns"), Note("margin", "unknown")}),
    ({"sections": THREE}, "columns", 1),
    ({"line": 150, "line_rule": "exact"}, "line_height", round(150 / 110, 2)),
    ({"line": 250, "line_rule": "auto"}, "notes", {Note("line_height", "out_of_bounds")}),
    ({"styles": LIGHT}, "accent", "#4472c4"),
    ({"theme": None, "styles": []}, "text", DEFAULTS["text"]),
    ({"theme": None, "styles": [], "font": None}, "fonts", ("arimo",)),
    ({"styles": CYCLE, "size": None}, "body_size", DEFAULTS["body_size"]),
    ({"size": 0}, "notes", {Note("body_size", "unknown"), Note("name_scale", "unknown")}),
]


@pytest.mark.parametrize(("change", "token", "value"), DOCX_NEIGHBOURS)
def test_a_word_document_read_with_its_neighbours(change, token, value):
    found = getattr(from_docx(_docx(**{**WORD, **change})), token)
    assert (set(found) if token == "notes" else found) == value


def _random_pdf(rng: random.Random) -> DesignPdf:
    colours = ["#ffffff", "#000000", "#222222", "#cccccc", "#1f4e79", "#ffff00", "#777777"]
    colours += [f"#{rng.randrange(1 << 24):06x}" for _ in range(3)]
    width, height = rng.choice(
        [(W, H), (6120, 7920), (rng.randint(1, 20000), rng.randint(1, 20000))]
    )
    runs = []
    for _ in range(rng.randint(0, 60)):
        x0, y0 = rng.randrange(width), rng.randrange(height)
        size = rng.choice([0, 1, 60, 100, 110, 140, 240, 900])
        x1, y1 = min(width, x0 + rng.randint(0, 3000)), min(height, y0 + size)
        runs.append(
            TextRun(
                "Example", (x0, y0, x1, y1), rng.randrange(len(FONTS)), size, rng.choice(colours)
            )
        )
    shapes = []
    for _ in range(rng.randint(0, 6)):
        x0, y0 = rng.randrange(width), rng.randrange(height)
        box = (x0, y0, rng.randint(x0, width), rng.randint(y0, height))
        shapes.append(Shape(rng.choice(["fill", "rule", "outline"]), box, rng.choice(colours), 5))
    margins = tuple(rng.randint(0, max(width, height)) for _ in range(4)) if runs else None
    page = DesignPage(0, width, height, margins, tuple(runs), tuple(shapes), ())
    return DesignPdf(1, False, False, (), FONTS, (page,) if rng.random() < 0.95 else ())


def test_whatever_the_layout_the_tokens_are_within_bounds_readable_and_a_valid_ladder():
    rng = random.Random(219)  # noqa: S311, a repeatable draw of layouts, nothing secret
    for _ in range(300):
        tokens = from_pdf(_random_pdf(rng))
        for token, (low, high) in BOUNDS.items():
            assert low <= getattr(tokens, token) <= high
        assert contrast(tokens.text, "#ffffff") >= 4.5
        assert contrast(tokens.accent, "#ffffff") >= 4.5
        assert (tokens.fill is None) == (tokens.on_fill is None)
        if tokens.fill:
            assert contrast(tokens.on_fill, tokens.fill) >= 4.5
        assert tokens.columns in (1, 2)
        assert (tokens.side is None) == (tokens.columns == 1)
        assert 1 <= len(tokens.fonts) <= 2
        assert set(tokens.fonts) <= set(font_map.FAMILIES)
        assert {note.code for note in tokens.notes} <= set(NOTE_CODES)
        sheet = f"p {{ color: {tokens.text}; }} h2 {{ color: var(--accent); }}"
        if tokens.fill:
            sheet += (
                f" .side {{ background: {tokens.fill}; }} .side p {{ color: {tokens.on_fill}; }}"
            )
        found = template_rules.check_css(sheet, tokens.fonts, tokens.accent)
        assert not [p for p in found if p.code in ("contrast", "colour")]
        for kind in templates.KINDS:
            ladder = steps(tokens, kind)
            spec = templates._kind("upload-test0001", kind, {"steps": ladder, "soft_limits": {}})
            assert spec.steps[-1] == TIGHTEST[kind]


@pytest.mark.parametrize("kind", templates.KINDS)
def test_the_ladder_starts_from_the_captured_density_and_ends_at_the_tightest(kind):
    tokens = from_pdf(_pdf(_cv()))
    ladder = steps(tokens, kind)
    assert len(ladder) == template_rules.COUNT[kind]
    assert ladder[0]["body_size"] == tokens.body_size + (1 if kind == "letter" else 0)
    assert ladder[0]["margin"] == tokens.margin
    assert ladder[-1] == {key: getattr(TIGHTEST[kind], key) for key in templates.BOUNDS}
