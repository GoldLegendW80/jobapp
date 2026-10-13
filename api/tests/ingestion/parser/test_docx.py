"""The Word reader, sandbox/docx.py, and the caps it opens an archive under, sandbox/zipcaps.py,
called in the test process on files made here (tests/ingestion/archive_fixtures.py): what an
ordinary document becomes, the three rules of the walker, and each hostile file ending as a
code of the closed list, never as an exception, with nothing extracted.

Every answer is also passed through the worker's own reading of it (main.encode, then
parser_client.loads and check_parsed): what the handler writes is what the client accepts.
The same files against the real child, under its limits and its filter, in the container of the
service: test_archive_container.py.
"""

import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.ingestion import parser_client as pc
from tests.ingestion import archive_fixtures as fx
from tests.ingestion.parser.helpers import Plenty, SpentAfter, sandbox_handler, sandbox_module

main = sandbox_module("main")
docx = sandbox_handler("docx")
zipcaps = sandbox_handler("zipcaps")


def read(data: bytes, budget: Any = None) -> pc.Parsed:
    """The document as the worker gets it: the handler's answer through the client's checks."""
    answer = docx.handle(data, {}, budget or Plenty())
    return pc.check_parsed(pc.loads(main.encode(answer)), "docx")


def refusal(data: bytes, budget: Any = None) -> str:
    with pytest.raises(pc.ParserRefusedError) as caught:
        read(data, budget)
    assert caught.value.failure == "file"
    return caught.value.code


def test_the_caps_are_the_ones_of_the_plan() -> None:
    assert (zipcaps.ENTRIES_MAX, zipcaps.TOTAL_MAX, zipcaps.ENTRY_MAX, zipcaps.RATIO_MAX) == (
        200,
        50 * fx.MIB,
        20 * fx.MIB,
        100,
    )
    assert docx.TEXT_MAX_CHARS == pc.MAX_TEXT_CHARS


def test_a_docx_with_tables_headers_footers_tabs_and_unicode() -> None:
    data = fx.docx(
        fx.paragraph(fx.run("Zoë Müller"), "<w:r><w:tab/></w:r>", fx.run("ingénieure 数据")),
        fx.paragraph(fx.run("Line one"), "<w:r><w:br/></w:r>", fx.run("line two")),
        fx.paragraph(fx.run("Before the page break"), '<w:r><w:br w:type="page"/></w:r>'),
        fx.table(("Language", "Level"), ("Français", "Natif")),
        fx.paragraph(fx.run("e\u0301t\u00e9 \U0001f680")),
        headers=["Curriculum vitae", "Curriculum vitae", "First page only"],
        footers=["zoe.muller@example.org"],
    )
    parsed = read(data)
    assert parsed.text == (
        "Curriculum vitae\n\nFirst page only\n\n"
        "Zoë Müller\tingénieure 数据\nLine one\nline two\nBefore the page break\n\n"
        "Language\tLevel\nFrançais\tNatif\nété \U0001f680\n\n"
        "zoe.muller@example.org"
    )
    # No pages, so no form feed, and nothing was left out.
    assert (parsed.pages, parsed.pages_text, parsed.pages_image) == (None, None, None)
    assert (parsed.truncated, parsed.notes, parsed.documents) == (False, (), ())
    assert "\f" not in parsed.text


def test_a_hyperlink_with_an_external_target_an_internal_anchor_and_a_missing_relationship() -> (
    None
):
    data = fx.docx(
        fx.paragraph(fx.run("Find me on "), fx.hyperlink("LinkedIn", "rId1"), fx.run(".")),
        fx.paragraph(fx.hyperlink("See the summary", anchor="summary")),
        fx.paragraph(fx.hyperlink("A link that lost its target", "rId404")),
        fx.paragraph(fx.hyperlink(fx.LINK, "rId1")),
        fx.paragraph(fx.hyperlink("Write to me", "rId2")),
        fx.paragraph(fx.hyperlink("A script", "rId3")),
        links={
            "rId1": fx.LINK,
            "rId2": "mailto:camille.durand@example.org",
            "rId3": "javascript:alert(1)",
        },
    )
    assert read(data).text.split("\n") == [
        f"Find me on LinkedIn ({fx.LINK}).",
        "See the summary",
        "A link that lost its target",
        fx.LINK,  # the address is the text already: written once
        "Write to me (mailto:camille.durand@example.org)",
        "A script",
    ]
    # A target inside the package is not an address, and a document without its relationship
    # file is read all the same.
    internal = fx.zip_of(
        [
            ("[Content_Types].xml", fx.CONTENT_TYPES),
            ("word/document.xml", fx.part("w:document", fx.paragraph(fx.hyperlink("Here", "r1")))),
            ("word/_rels/document.xml.rels", fx.relationships({"r1": fx.LINK}, mode="Internal")),
        ]
    )
    assert read(internal).text == "Here"
    assert read(fx.docx(fx.paragraph(fx.hyperlink("Here", "r1")))).text == "Here"


def test_a_text_box_gives_its_text_once() -> None:
    data = fx.docx(
        fx.paragraph(fx.run("Before"), fx.text_box("In the box"), fx.run("After")),
        fx.paragraph("<w:r><w:pict><v:textbox><w:txbxContent>"
                     + fx.paragraph(fx.run("An older box"))
                     + "</w:txbxContent></v:textbox></w:pict></w:r>"),
    )  # fmt: skip
    text = read(data).text
    assert text.count("In the box") == 1
    assert text.count("An older box") == 1
    assert text.index("Before") < text.index("In the box") < text.index("After")


def test_tracked_changes_inserted_text_in_deleted_text_out() -> None:
    data = fx.docx(
        fx.paragraph(
            fx.run("I led "),
            "<w:ins>" + fx.run("a team of four") + "</w:ins>",
            "<w:del><w:r><w:delText>the whole company</w:delText></w:r></w:del>",
            "<w:moveFrom>" + fx.run("moved away") + "</w:moveFrom>",
            "<w:moveTo>" + fx.run(" for two years") + "</w:moveTo>",
            '<w:r><w:instrText>HYPERLINK "https://www.example.org/never"</w:instrText></w:r>',
            fx.run("."),
        )
    )
    assert read(data).text == "I led a team of four for two years."


def test_a_docm_and_a_docx_with_vba_project_are_refused_and_their_macros_never_read() -> None:
    docm = fx.docx(fx.paragraph(fx.run("text")), content_types=fx.MACRO_CONTENT_TYPES)
    with_macros = fx.docx(
        fx.paragraph(fx.run("text")), extra=[("word/vbaProject.bin", b"Sub AutoOpen()")]
    )
    other_case = fx.docx(fx.paragraph(fx.run("text")), extra=[("word/VBAPROJECT.BIN", b"Sub")])
    for data in (docm, with_macros, other_case):
        assert refusal(data) == "macros"


@pytest.mark.parametrize("make", [fx.entity_bomb_docx, fx.external_entity_docx])
def test_a_docx_with_an_entity_bomb_and_with_an_external_entity(make: Any) -> None:
    assert refusal(make()) == "bomb"
    # In a header or in the relationship file too: the document is refused, not read around it.
    prolog = fx.ENTITY_BOMB if make is fx.entity_bomb_docx else fx.EXTERNAL_ENTITY
    header = fx.part("w:hdr", fx.paragraph(fx.run("x")), prolog=prolog)
    assert refusal(fx.docx(fx.paragraph(fx.run("t")), extra=[("word/header9.xml", header)])) == (
        "bomb"
    )
    rels = prolog + fx.relationships({"rId1": fx.LINK})
    data = fx.docx(fx.paragraph(fx.run("t")), extra=[("word/_rels/document.xml.rels", rels)])
    assert refusal(data) == "bomb"


@pytest.mark.parametrize(("name", "make", "code"), fx.HOSTILE, ids=[case[0] for case in fx.HOSTILE])
def test_zip_bombs_end_as_a_code_with_nothing_extracted(
    name: str, make: Any, code: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ratio bomb, a total size bomb, 201 entries, a nested archive, an entry lying about its
    size, a path with `..`: each is refused by its code, and zipfile is never asked to open or
    extract an entry (zipcaps inflates in memory what a handler names, and nothing else)."""

    def never(*_: Any, **__: Any) -> None:
        raise AssertionError("an entry was opened or extracted through zipfile")

    data = make()
    for method in ("open", "read", "extract", "extractall"):
        monkeypatch.setattr(zipfile.ZipFile, method, never)
    monkeypatch.chdir(tmp_path)
    assert refusal(data) == code
    assert list(tmp_path.iterdir()) == []


def test_an_entry_lying_about_its_size_is_given_one_byte_more_than_it_declared() -> None:
    archive = zipcaps.Archive(fx.lying_entry())
    with pytest.raises(zipcaps.Refused) as caught:
        archive.read("word/document.xml")
    assert caught.value.code == "bomb"
    assert archive.read_bytes == 0


def test_what_zipcaps_refuses_before_it_reads_an_entry() -> None:
    def code(data: bytes) -> str:
        with pytest.raises(zipcaps.Refused) as caught:
            zipcaps.Archive(data)
        return caught.value.code

    assert code(b"PK\x03\x04 and nothing of a ZIP") == "unreadable"
    assert code(fx.cv_docx()[:-40]) == "unreadable"  # cut short
    assert code(fx.zip_of([("a.csv", "a"), ("a.csv", "b")])) == "unreadable"  # a name twice
    assert code(fx.zip_of([("/etc/passwd", "a")])) == "unreadable"
    assert code(fx.zip_of([("a.csv", "a" * 2000)], zipfile.ZIP_BZIP2)) == "unreadable"
    assert len(zipcaps.Archive(fx.too_many_entries(200)).names) == 200
    encrypted = bytearray(fx.zip_of([("Positions.csv", fx.POSITIONS)]))
    at = encrypted.find(b"PK\x01\x02")
    encrypted[at + 8] |= 1  # the flag of an encrypted entry, in the central directory
    assert code(bytes(encrypted)) == "protected"


def test_a_zip_that_is_neither_docx_nor_an_export_is_not_a_docx() -> None:
    assert refusal(fx.zip_of([("notes.txt", "some notes"), ("photo.jpeg", b"\xff\xd8\xff")])) == (
        "not_docx"
    )
    assert refusal(fx.export()) == "not_docx"
    # A workbook is an Office file, not a Word document.
    workbook = fx.zip_of([("[Content_Types].xml", "<Types/>"), ("xl/workbook.xml", "<a/>")])
    assert refusal(workbook) == "not_docx"


def test_a_docx_missing_word_document_xml() -> None:
    data = fx.zip_of(
        [
            ("[Content_Types].xml", fx.CONTENT_TYPES),
            ("word/styles.xml", "<a/>"),
            ("word/header1.xml", fx.part("w:hdr", fx.paragraph(fx.run("a header alone")))),
        ]
    )
    assert refusal(data) == "unreadable"


def test_a_docx_whose_xml_does_not_parse_or_holds_no_word() -> None:
    assert refusal(fx.docx(document="<w:document><w:body>")) == "unreadable"
    assert refusal(fx.docx(document="not XML at all")) == "unreadable"
    assert refusal(fx.docx(fx.paragraph(fx.run("   ")))) == "no_text"
    # A damaged header does not cost the document its body.
    damaged = fx.docx(fx.paragraph(fx.run("The body")), extra=[("word/header1.xml", "<w:hdr")])
    assert read(damaged).text == "The body"


def test_a_20_mib_docx_of_images() -> None:
    data = fx.images_docx()
    assert 20 * fx.MIB - 64 * 1024 < len(data) <= 20 * fx.MIB
    archive = zipcaps.Archive(data)
    assert read(data).text == "A portfolio of four photographs."
    # The images were never inflated: only the two small parts were read.
    assert archive.read_bytes == 0
    assert len(archive.read("word/document.xml")) < 4096


def test_the_text_stops_at_the_character_cap() -> None:
    data = fx.docx(*(fx.paragraph(fx.run(fx.words(160, seed))) for seed in range(600)))
    parsed = read(data)
    assert len(parsed.text or "") <= docx.TEXT_MAX_CHARS
    assert len(parsed.text or "") > docx.TEXT_MAX_CHARS - 2000
    assert parsed.truncated is True
    assert [(note.code, dict(note.values)) for note in parsed.notes] == [("text_limit", {})]


def test_the_time_budget_keeps_what_was_read() -> None:
    lines = [
        fx.paragraph(fx.run(f"Line {number} " + fx.words(30, number))) for number in range(2000)
    ]
    data = fx.docx(*lines)
    parsed = read(data, SpentAfter(3))
    assert (parsed.text or "").startswith("Line 0 ")
    assert parsed.truncated is True
    assert [note.code for note in parsed.notes] == ["time_limit"]
    assert refusal(data, SpentAfter(0)) == "too_slow"


def test_deep_nesting_and_control_characters_end_as_text() -> None:
    opened = "".join(f'<w:sdt w:n="{word}">' for word in fx.words(50_000).split(" "))
    deep = opened + fx.paragraph(fx.run("deep")) + "</w:sdt>" * 50_000
    assert read(fx.docx(deep)).text == "deep"
    odd = fx.docx(fx.paragraph(fx.run("a&#x85;b\u2028c&#x9;d&#x7f;")))
    assert read(odd).text == "a\nb\nc\td"
