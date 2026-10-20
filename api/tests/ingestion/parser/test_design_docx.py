"""The style reader of a Word document for the design capture (documents D25a,
sandbox/design_docx.py), in the test process on files made in code, every answer read back
through the worker's own check (parser_client.check_design_docx)."""

import html
import json
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion import parser_client as pc
from tests.ingestion import archive_fixtures as archives
from tests.ingestion import design_fixtures as fx
from tests.ingestion.parser.helpers import (
    Plenty,
    SpentAfter,
    as_in_the_child,
    sandbox_handler,
    sandbox_module,
)

main = sandbox_module("main")
design_docx = sandbox_handler("design_docx")


@pytest.fixture(autouse=True)
def beside_its_helpers() -> Iterator[None]:
    with as_in_the_child():
        yield


def raw(data: bytes, options: dict | None = None, budget: Any = None) -> dict:
    return design_docx.handle(data, options or {}, budget or Plenty())


def ask(data: bytes, budget: Any = None) -> pc.DesignDocx:
    return pc.check_design_docx(pc.loads(main.encode(raw(data, budget=budget))))


def refusal(data: bytes, options: dict | None = None, budget: Any = None) -> tuple[str, str]:
    with pytest.raises(pc.ParserRefusedError) as caught:
        pc.check_design_docx(pc.loads(main.encode(raw(data, options, budget))))
    return caught.value.failure, caught.value.code


def style(found: pc.DesignDocx, style_id: str) -> dict:
    return dict(next(item for item in found.styles if item["id"] == style_id))


def test_the_theme_its_fonts_and_colours_resolve_the_styles() -> None:
    found = ask(fx.styled_docx())
    assert (found.truncated, found.notes) == (False, ())
    assert found.theme["major"] == "Calibri Light"
    assert found.theme["minor"] == "Calibri"
    assert dict(found.theme["colours"]) == {
        "dk1": "#000000",
        "lt1": "#ffffff",
        "accent1": "#4472c4",
        "accent2": "#ed7d31",
    }
    defaults = dict(found.defaults)
    assert (defaults["font"], defaults["size"]) == ("Calibri", 110)
    assert (defaults["after"], defaults["line"], defaults["line_rule"]) == (80, 107, "auto")


def test_each_style_gives_its_run_and_paragraph_properties() -> None:
    found = ask(fx.styled_docx())
    assert [item["id"] for item in found.styles] == ["Normal", "Heading1", "Strong", "Grid"]
    heading = style(found, "Heading1")
    assert heading == {
        **dict.fromkeys(pc.DESIGN_PROPS),
        "id": "Heading1",
        "name": "heading 1",
        "type": "paragraph",
        "based_on": "Normal",
        "default": False,
        "outline": 0,
        "font": "Calibri Light",
        "size": 160,
        "bold": True,
        "caps": True,
        # The value Word computed wins over the theme colour it names.
        "colour": "#2f5496",
        "before": 120,
        "after": 0,
        "border": "#1f4e79",
    }
    # The italic of the paragraph mark (pPr/rPr) is not the style's.
    assert heading["italic"] is None
    strong = style(found, "Strong")
    assert (strong["type"], strong["bold"], strong["italic"]) == ("character", False, True)
    assert strong["colour"] == "#ed7d31"  # through the theme
    assert style(found, "Normal")["default"] is True
    # The bold of a table's first row is conditional: not the table style's.
    assert style(found, "Grid")["bold"] is None


def test_the_layout_of_the_document_and_no_word_of_its_text() -> None:
    found = ask(fx.styled_docx())
    assert [dict(section) for section in found.sections] == [
        {"width": 5953, "height": 8419, "margins": (720, 540, 720, 540), "columns": 2}
    ]
    assert (found.tables, found.table_columns, found.drawings) == (1, 3, 1)
    assert found.paragraphs == 5  # two headings, the formatted one, a cell, the drawing
    assert found.paragraph_styles == (("Heading1", 2),)
    georgia = next(dict(item) for item in found.formats if item["font"] == "Georgia")
    assert georgia == {
        "style": None,
        "font": "Georgia",
        "size": 100,
        "bold": None,
        "italic": None,
        "caps": None,
        "colour": "#c00000",
        "chars": len("Built the " + fx.CANARY + " planner at Example Robotics"),
    }
    answer = json.dumps(raw(fx.styled_docx()))
    for word in (fx.CANARY, "Example Robotics", "Experience", "Python"):
        assert word not in answer


def test_a_document_without_styles_or_theme_still_gives_its_layout() -> None:
    found = ask(archives.docx(fx.heading("Skills"), fx.SECTION))
    assert (found.theme, found.styles, found.defaults["font"]) == (None, (), None)
    assert found.sections[0]["columns"] == 2
    broken = ask(fx.styled_docx(style_part="<w:styles", theme="not xml"))
    assert (broken.theme, broken.styles) == (None, ())


def test_styles_past_the_cap_are_cut_with_a_note() -> None:
    many = "".join(
        f'<w:style w:type="paragraph" w:styleId="S{n}"><w:name w:val="s {n}"/></w:style>'
        for n in range(design_docx.STYLES_MAX + 5)
    )
    found = ask(fx.styled_docx(style_part=fx.styles(many)))
    assert len(found.styles) == design_docx.STYLES_MAX
    assert found.truncated
    assert "styles_limit" in {note.code for note in found.notes}


def test_names_and_values_from_the_file_are_cleaned_and_bounded() -> None:
    hostile = (
        '<w:style w:type="script" w:styleId="X\u200bY"><w:name w:val="' + "n" * 500 + '"/>'
        '<w:rPr><w:rFonts w:ascii="' + "F" * 500 + '"/><w:sz w:val="99999999999"/>'
        '<w:color w:val="javascript"/></w:rPr><w:pPr><w:spacing w:before="-5" w:line="7"'
        ' w:lineRule="weird"/><w:jc w:val="sideways"/><w:outlineLvl w:val="42"/></w:pPr>'
        "</w:style>"
    )
    found = ask(fx.styled_docx(style_part=fx.styles(hostile)))
    (item,) = found.styles
    assert item["id"] == "XY"
    assert item["type"] == "paragraph"
    assert len(item["name"]) == len(item["font"]) == design_docx.NAME_MAX
    assert (item["size"], item["colour"], item["before"], item["line"]) == (None, None, None, None)
    assert (item["align"], item["outline"]) == (None, None)


# A value of w:sz (half points) and of w:spacing w:before (twips), and what the style keeps of
# each, in tenths of a point. Only ASCII digits with one minus sign at most are a number.
NUMBERS = [
    ("24", 120, 12),
    (" 24 ", 120, 12),
    ("0024", 120, 12),
    ("0", None, 0),
    ("-0", None, 0),
    ("3276", 16_380, 1_638),
    ("3277", None, 1_638),
    ("200000", None, 100_000),
    ("200001", None, None),
    ("999999999", None, None),
    ("9999999999", None, None),
    ("-5", None, None),
    ("--5", None, None),
    ("-+5", None, None),
    ("+5", None, None),
    ("- 5", None, None),
    ("5-", None, None),
    ("", None, None),
    (" ", None, None),
    ("-", None, None),
    ("²", None, None),
    ("2²", None, None),
    ("⁵", None, None),
    ("½", None, None),
    ("Ⅻ", None, None),
    ("١٢", None, None),
    ("１２", None, None),
    ("𝟙𝟚", None, None),
    ("12pt", None, None),
    ("1.5", None, None),
    ("1,5", None, None),
    ("1e3", None, None),
    ("1_000", None, None),
    ("0x10", None, None),
    ("NaN", None, None),
    ("auto", None, None),
]


@pytest.mark.parametrize(("value", "size", "before"), NUMBERS, ids=[repr(r[0]) for r in NUMBERS])
def test_a_number_the_file_writes_is_read_or_left_out_never_the_whole_file(
    value: str, size: int | None, before: int | None
) -> None:
    hostile = (
        f'<w:style w:type="paragraph" w:styleId="Odd"><w:rPr><w:sz w:val="{value}"/></w:rPr>'
        f'<w:pPr><w:spacing w:before="{value}"/></w:pPr></w:style>'
    )
    item = style(ask(fx.styled_docx(style_part=fx.styles(hostile))), "Odd")
    assert (item["size"], item["before"]) == (size, before)


# A colour the file writes, and what is kept of it. Only six ASCII hex digits are a colour.
COLOURS = [
    ("1F4E79", "#1f4e79"),
    ("1f4e79", "#1f4e79"),
    ("aBcDeF", "#abcdef"),
    ("000000", "#000000"),
    ("FFFFFF", "#ffffff"),
    ("1e3456", "#1e3456"),
    ("+abcde", None),
    ("-abcde", None),
    ("ab_cde", None),
    ("a_bcde", None),
    (" abcd ", None),
    (" abcde", None),
    ("abcde ", None),
    ("\tabcde", None),
    ("0xabcd", None),
    ("0Xabcd", None),
    ("١٢٣٤٥٦", None),
    ("۱۲۳۴۵۶", None),
    ("１２３４５６", None),
    ("ＡＢＣＤＥＦ", None),
    ("𝟙𝟚𝟛𝟜𝟝𝟞", None),
    ("²³⁴⁵⁶⁷", None),
    ("ab.cde", None),
    ("ab,cde", None),
    ("12 456", None),
    ("abcdeg", None),
    ("ABCDEG", None),
    ("abcde", None),
    ("abcdef0", None),
    ("#abcde", None),
    ("#abcdef", None),
    ("auto", None),
    ("", None),
    ("Infini", None),
    ("&bcdef", None),
]


@pytest.mark.parametrize(("value", "colour"), COLOURS, ids=[repr(r[0]) for r in COLOURS])
def test_a_colour_the_file_writes_is_read_or_left_out_never_the_whole_file(
    value: str, colour: str | None
) -> None:
    written = html.escape(value, quote=True)
    hostile = (
        f'<w:style w:type="paragraph" w:styleId="Odd"><w:rPr><w:color w:val="{written}"/>'
        f'</w:rPr><w:pPr><w:shd w:fill="{written}"/><w:pBdr><w:bottom w:val="single"'
        f' w:color="{written}"/></w:pBdr></w:pPr></w:style>'
    )
    theme = fx.THEME.replace('val="4472C4"', f'val="{written}"')
    theme = theme.replace('lastClr="000000"', f'lastClr="{written}"')
    run = f'<w:r><w:rPr><w:color w:val="{written}"/></w:rPr><w:t>Word</w:t></w:r>'
    data = archives.docx(
        archives.paragraph(run),
        extra=[("word/styles.xml", fx.styles(hostile)), ("word/theme/theme1.xml", theme)],
    )
    found = ask(data)
    item = style(found, "Odd")
    assert (item["colour"], item["shading"]) == (colour, colour)
    assert item["border"] == (colour or "#000000")
    assert found.theme is not None
    assert found.theme["colours"].get("accent1") == colour
    assert found.theme["colours"].get("dk1") == colour
    assert [entry["colour"] for entry in found.formats] == [colour]


def test_a_count_past_the_clients_cap_is_clamped_never_the_whole_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(design_docx, "COUNT_MAX", 3)
    found = ask(fx.styled_docx())
    assert (found.paragraphs, found.table_columns, found.tables, found.drawings) == (3, 3, 1, 1)
    assert [entry["chars"] for entry in found.formats] == [3, 3]
    assert [count for _, count in found.paragraph_styles] == [2]


def test_the_caps_are_the_clients() -> None:
    assert design_docx.COUNT_MAX == pc.DESIGN_COUNT
    assert design_docx.STYLES_MAX == pc.DESIGN_STYLES
    assert design_docx.SECTIONS_MAX == pc.DESIGN_SECTIONS
    assert design_docx.SHOWN == pc.DESIGN_SHOWN
    assert design_docx.NAME_MAX == pc.DESIGN_NAME
    assert design_docx.PROPS == pc.DESIGN_PROPS
    assert design_docx.RUN_PROPS == pc.DESIGN_RUN_PROPS


# --- the budget and hostile files ---------------------------------------------------------------


def test_a_budget_spent_at_once_is_too_slow_and_later_keeps_what_was_read() -> None:
    assert refusal(fx.styled_docx(), budget=SpentAfter(0)) == ("file", "too_slow")
    found = ask(fx.styled_docx(), budget=SpentAfter(2))
    assert found.truncated
    assert "time_limit" in {note.code for note in found.notes}


@pytest.mark.parametrize(
    ("name", "make", "code"),
    [
        (
            "an entity bomb in the styles",
            lambda: fx.styled_docx(
                style_part=fx.styles()
                .replace("<w:styles", archives.ENTITY_BOMB + "<w:styles", 1)
                .replace('<?xml version="1.0" encoding="UTF-8"?>', "")
            ),
            "bomb",
        ),
        ("an entity bomb in the body", archives.entity_bomb_docx, "bomb"),
        (
            "new names without end",
            lambda: archives.distinct_names_docx(size=2 * archives.MIB),
            "bomb",
        ),
        ("a ratio bomb", archives.ratio_bomb, "bomb"),
        ("too many entries", archives.too_many_entries, "bomb"),
        ("an archive inside", archives.nested_archive, "nested_archive"),
        ("macros", lambda: archives.docx(content_types=archives.MACRO_CONTENT_TYPES), "macros"),
        ("a body that is not XML", lambda: archives.docx(document="<w:document"), "unreadable"),
        ("a zip that is no Word document", lambda: archives.zip_of([("a.txt", "b")]), "not_docx"),
        ("not a zip", lambda: b"%PDF-1.7", "unreadable"),
    ],
)
def test_a_hostile_file_ends_as_a_code_of_the_file(name: str, make: Any, code: str) -> None:
    assert refusal(make()) == ("file", code), name


def test_options_are_refused_as_the_servers_failure() -> None:
    assert refusal(fx.styled_docx(), {"x": 1}) == ("server", "handler_error")
