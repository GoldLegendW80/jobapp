"""The style reader of a Word document for the design capture (documents D25a,
sandbox/design_docx.py), in the test process on files made in code, every answer read back
through the worker's own check (parser_client.check_design_docx)."""

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


def test_the_caps_are_the_clients() -> None:
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
