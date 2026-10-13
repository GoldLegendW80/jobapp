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


def nested(depth: int) -> bytes:
    """A paragraph inside `depth` elements, each inside the last."""
    opened = "".join(f'<w:sdt w:n="{word}">' for word in fx.words(depth).split(" "))
    return fx.docx(opened + fx.paragraph(fx.run("deep")) + "</w:sdt>" * depth)


def test_nesting_is_read_to_a_depth_no_document_has_and_control_characters_end_as_text() -> None:
    # Around the paragraph: w:document and w:body. Inside it: w:r and w:t.
    assert docx.DEPTH_MAX == 512
    assert read(nested(docx.DEPTH_MAX - 5)).text == "deep"
    assert refusal(nested(docx.DEPTH_MAX - 4)) == "bomb"
    assert refusal(nested(50_000)) == "bomb"
    odd = fx.docx(fx.paragraph(fx.run("a&#x85;b\u2028c&#x9;d&#x7f;")))
    assert read(odd).text == "a\nb\nc\td"


def scrambled(count: int) -> list[str]:
    """`count` distinct names that pack as text does, not as a bomb."""
    return [f"{number * 2654435761 % 2**32:08x}" for number in range(count)]


def named(count: int) -> bytes:
    """A document that reads "named" and holds `count` distinct names: the eleven of the
    fixture (five prefixes, w:document, w:body, w:p, w:r, w:t and xml:space) and new ones."""
    new = "".join(f'<w:q{name} w:a{name}=""/>' for name in scrambled((count - 11) // 2))
    return fx.docx(fx.paragraph(fx.run("named")), new + ("<w:odd/>" if count % 2 == 0 else ""))


def test_a_part_may_hold_the_names_of_a_vocabulary_and_no_more() -> None:
    """The parser keeps every distinct name for as long as it lives: 19 MiB of new names cost
    it more than 500 MiB (measured, the G7 block of the plan). Both targets count them, in the
    body and in the relationship file, and stop at NAMES_MAX. Each shape is read with twenty
    names fewer, so it is the count that refuses it, not a cap of the archive."""
    assert docx.NAMES_MAX == 4_096
    assert read(named(docx.NAMES_MAX)).text == "named"
    assert refusal(named(docx.NAMES_MAX + 1)) == "bomb"
    line = fx.paragraph(fx.run("named"))
    for most, code in ((docx.NAMES_MAX - 20, ""), (docx.NAMES_MAX, "bomb")):
        names = scrambled(most)
        skipped = "".join(f"<w:q{name}/>" for name in names)
        shapes = {
            # A name is counted where it is skipped too: inside what was deleted.
            "skipped": fx.docx(line, f"<w:del>{skipped}</w:del>"),
            "prefixes": fx.docx(line, "".join(f'<w:r xmlns:p{name}="urn:x"/>' for name in names)),
            "one tag": fx.docx(line, f"<w:r {' '.join(f'a{name}=""' for name in names)}/>"),
            "the relationship file": fx.zip_of(
                [
                    ("[Content_Types].xml", fx.CONTENT_TYPES),
                    ("word/document.xml", fx.part("w:document", line)),
                    (
                        "word/_rels/document.xml.rels",
                        fx.relationships({"rId1": fx.LINK}).replace(
                            "</R", "".join(f"<q{name}/>" for name in names) + "</R"
                        ),
                    ),
                ]
            ),
        }
        for shape, data in shapes.items():
            if code:
                assert refusal(data) == code, shape
            else:
                assert read(data).text == "named", shape
    # The same names again and again are one name each.
    again = "".join(
        f'<w:q{number % 100} w:a{number % 100}="{word}"/>'
        for number, word in enumerate(fx.words(20_000).split(" "))
    )
    assert read(fx.docx(line, again)).text == "named"


@pytest.mark.parametrize(
    "make",
    [
        lambda: fx.distinct_names_docx("element", fx.MIB),
        lambda: fx.distinct_names_docx("attribute", fx.MIB),
        lambda: fx.distinct_names_docx("one tag", fx.MIB),
        lambda: fx.deep_docx(fx.MIB),
    ],
    ids=["a new element name each time", "a new attribute name each time", "one tag", "deep"],
)
def test_the_shapes_that_grow_the_parser_are_a_bomb(make: Any) -> None:
    """Their 19 MiB against the real child and its limit: test_archive_container.py."""
    assert refusal(make()) == "bomb"


def out_of_memory(code: int) -> type:
    """A parser that fails as expat does when the kernel gives it no more memory (code 1)."""

    class Parser:
        def __init__(self, **options: Any) -> None:
            return None

        def feed(self, content: bytes) -> None:
            error = docx.ElementTree.ParseError("out of memory: line 1, column 0")
            error.code, error.position = code, (1, 0)
            raise error

    return Parser


def test_no_memory_left_is_a_bomb_and_never_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """At the address space limit either Python or expat is refused its memory, wherever the
    parsing is: both end as `bomb`, a code of the file, in the body, in a header and in the
    relationship file (where another parse error only loses the header or the addresses)."""
    data = fx.docx(fx.paragraph(fx.hyperlink("Here", "r1")), links={"r1": fx.LINK}, headers=["H"])

    def no_memory(self: Any, *told: Any) -> None:
        raise MemoryError

    for where, method in ((docx.Walker, "data"), (docx.Walker, "start"), (docx._Links, "start")):
        with monkeypatch.context() as patched:
            patched.setattr(where, method, no_memory)
            assert docx.handle(data, {}, Plenty()) == docx.failure("bomb")
            assert refusal(data) == "bomb"
    with monkeypatch.context() as patched:
        patched.setattr(docx.ElementTree, "DefusedXMLParser", out_of_memory(1))
        assert docx.handle(data, {}, Plenty()) == docx.failure("bomb")
        assert refusal(fx.docx(fx.paragraph(fx.run("No link, no header.")))) == "bomb"
    with monkeypatch.context() as patched:  # any other error of the parser stays what it was
        patched.setattr(docx.ElementTree, "DefusedXMLParser", out_of_memory(2))
        assert refusal(data) == "unreadable"


def test_an_address_cannot_add_a_line_a_form_feed_or_turn_the_line_around() -> None:
    def linked(target: str) -> pc.Parsed:
        return read(fx.docx(fx.paragraph(fx.hyperlink("te", "r1")), links={"r1": target}))

    odd = (
        "https://x.example/&#8232;LINE&#9;SEP&#8233;PARA&#10;SEP&#13;&#x85;&#x202E;gnp&#x2066;"
        "&#x200F;&#xA0;&#x7f; end"
    )
    assert linked(odd).text == "te (https://x.example/LINESEPPARASEPgnp\xa0end)"
    # XML has no form feed and no NUL, written or as a reference: the relationship file does
    # not parse, and the text is read without its addresses.
    for refused in ("&#12;", "\x0c", "&#0;"):
        assert linked(f"https://x.example/a{refused}b").text == "te"
    longest = "https://x.example/" + "a" * (docx.LINK_MAX_CHARS - 18)
    assert linked(longest).text == f"te ({longest})"
    assert linked(longest + "a").text == "te"
    # What is dropped is not counted.
    assert linked("&#8232; &#9;" + longest).text == f"te ({longest})"
