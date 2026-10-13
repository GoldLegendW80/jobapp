"""The reader of a LinkedIn export, sandbox/linkedin_zip.py, called in the test process on
archives made here (tests/ingestion/archive_fixtures.py): one document per known CSV, every
other entry ignored, and each hostile archive ending as a code of the closed list.

Every answer is also passed through the worker's own reading of it (main.encode, then
parser_client.loads and check_parsed). The same files against the real child, in the container
of the service: test_archive_container.py.
"""

import hashlib
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.ingestion import parser_client as pc
from tests.ingestion import archive_fixtures as fx
from tests.ingestion.parser.helpers import Plenty, SpentAfter, sandbox_handler, sandbox_module

main = sandbox_module("main")
linkedin_zip = sandbox_handler("linkedin_zip")
zipcaps = sandbox_handler("zipcaps")

# The hostile archives as an export meets them: the lying entry under a name it reads.
HOSTILE = [
    (name, (lambda: fx.lying_entry("Positions.csv")) if make is fx.lying_entry else make, code)
    for name, make, code in fx.HOSTILE
]


def answer_of(data: bytes, budget: Any = None) -> dict:
    return linkedin_zip.handle(data, {}, budget or Plenty())


def read(data: bytes, budget: Any = None) -> pc.Parsed:
    return pc.check_parsed(pc.loads(main.encode(answer_of(data, budget))), "linkedin_zip")


def refusal(data: bytes, budget: Any = None) -> str:
    with pytest.raises(pc.ParserRefusedError) as caught:
        read(data, budget)
    assert caught.value.failure == "file"
    return caught.value.code


def test_the_known_files_are_one_closed_list_in_the_parser_and_in_the_worker() -> None:
    assert linkedin_zip.FILES == pc.LINKEDIN_FILES
    assert linkedin_zip.EXPORT_MAX_CHARS == pc.MAX_EXPORT_CHARS
    assert linkedin_zip.TEXT_MAX_CHARS == pc.MAX_TEXT_CHARS
    # Four bytes a character at most, and the answer still fits what the worker reads.
    assert 4 * pc.MAX_EXPORT_CHARS + 64 * 1024 < pc.MAX_ANSWER
    # Nothing about other people: no connection, message, contact or invitation.
    lowered = " ".join(pc.LINKEDIN_FILES).lower()
    for word in ("connection", "message", "contact", "invitation", "email", "phone"):
        assert word not in lowered


def test_an_export_gives_one_document_per_known_csv_and_ignores_every_other_entry() -> None:
    parsed = read(fx.export())
    assert [file.name for file in parsed.documents] == [
        "Profile.csv",
        "Positions.csv",
        "Skills.csv",
    ]
    by_name = {file.name: file for file in parsed.documents}
    positions = by_name["Positions.csv"]
    assert positions.text == fx.POSITIONS.replace("\r\n", "\n").strip()
    # The hash and the size are those of the file in the archive: the CSV added alone has them.
    assert positions.sha256 == hashlib.sha256(fx.POSITIONS.encode()).digest()
    assert positions.size == len(fx.POSITIONS.encode())
    assert not any(file.truncated for file in parsed.documents)
    assert (parsed.text, parsed.pages, parsed.notes) == (None, None, ())
    everything = " ".join(file.text for file in parsed.documents)
    assert "never read" not in everything
    assert "Someone" not in everything


def test_the_name_of_a_document_is_the_one_of_the_list_never_the_one_of_the_archive() -> None:
    data = fx.zip_of(
        [
            ("POSITIONS.CSV", fx.POSITIONS),
            ("positions.csv", "Company Name\r\nA second spelling\r\n"),
            ("backup/Profile.csv", fx.PROFILE),  # not at the root
            ("Profile.csv.txt", fx.PROFILE),
            ("skills.csv", fx.SKILLS),
            ("Languages.csv", fx.SKILLS),  # the same bytes as another file: one document
            ("Education.csv", " \r\n"),  # nothing in it
        ]
    )
    parsed = read(data)
    assert [file.name for file in parsed.documents] == ["Positions.csv", "Skills.csv"]
    assert parsed.documents[0].text.startswith("Company Name,Title")


def test_an_export_with_a_byte_order_mark_or_in_utf_16_is_read() -> None:
    marked = b"\xef\xbb\xbf" + "Name\r\nFrançais\r\n".encode()
    wide = "Name\r\nDeutsch\r\n".encode("utf-16")
    parsed = read(fx.export({"Languages.csv": marked, "Skills.csv": wide}))
    assert [(file.name, file.text) for file in parsed.documents] == [
        ("Skills.csv", "Name\nDeutsch"),
        ("Languages.csv", "Name\nFrançais"),
    ]


def test_an_export_whose_csv_is_not_utf8() -> None:
    latin = "Name\r\nFrançais\r\n".encode("latin-1")
    assert refusal(fx.export({"Positions.csv": fx.POSITIONS, "Languages.csv": latin})) == "not_utf8"
    assert refusal(fx.export({"Skills.csv": b"Name\r\nPy\x00thon\r\n"})) == "not_utf8"
    # A file nobody reads may be anything.
    parsed = read(fx.export({"Positions.csv": fx.POSITIONS, "messages.csv": latin}))
    assert [file.name for file in parsed.documents] == ["Positions.csv"]


def test_an_export_with_unknown_csv_names_only() -> None:
    data = fx.export({"Connections.csv": "a,b\r\n", "messages.csv": "c\r\n", "Ads.csv": "d\r\n"})
    assert refusal(data) == "not_export"


def test_a_zip_that_is_neither_docx_nor_an_export() -> None:
    assert refusal(fx.zip_of([("notes.txt", "some notes"), ("photo.jpeg", b"\xff\xd8\xff")])) == (
        "not_export"
    )
    assert refusal(fx.cv_docx()) == "not_export"


def test_an_export_whose_known_files_are_empty_has_no_text() -> None:
    assert refusal(fx.export({"Positions.csv": "", "Skills.csv": " \r\n \r\n"})) == "no_text"


@pytest.mark.parametrize(("name", "make", "code"), HOSTILE, ids=[case[0] for case in HOSTILE])
def test_zip_bombs_end_as_a_code_with_nothing_extracted(
    name: str, make: Any, code: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*_: Any, **__: Any) -> None:
        raise AssertionError("an entry was opened or extracted through zipfile")

    data = make()
    for method in ("open", "read", "extract", "extractall"):
        monkeypatch.setattr(zipfile.ZipFile, method, never)
    monkeypatch.chdir(tmp_path)
    assert refusal(data) == code
    assert list(tmp_path.iterdir()) == []


def test_a_long_csv_is_cut_and_every_text_together_fits_the_answer() -> None:
    long = "Name\r\n" + fx.words(110_000, 1)  # about 700,000 characters
    longer = "Name\r\n" + fx.words(110_000, 2)
    parsed = read(fx.export({"Profile.csv": long, "Positions.csv": longer}))
    sizes = {file.name: (len(file.text), file.truncated) for file in parsed.documents}
    assert sizes == {
        "Profile.csv": (linkedin_zip.TEXT_MAX_CHARS, True),
        "Positions.csv": (linkedin_zip.EXPORT_MAX_CHARS - linkedin_zip.TEXT_MAX_CHARS, True),
    }
    assert len(
        main.encode(answer_of(fx.export({"Profile.csv": long, "Positions.csv": longer})))
    ) < (pc.MAX_ANSWER)
    # A known file that would be left out whole: the export is refused, nothing is half kept.
    files = {"Profile.csv": long, "Positions.csv": longer, "Skills.csv": "Name\r\nPython\r\n"}
    assert refusal(fx.export(files)) == "bomb"


def test_the_time_budget_ends_as_a_code() -> None:
    assert refusal(fx.export(), SpentAfter(1)) == "too_slow"


# --- what the worker accepts of an answer -------------------------------------------------------


def good() -> dict:
    return answer_of(fx.export())


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        (lambda a: a["documents"][0].update(name="Connections.csv"), "document_name"),
        (lambda a: a["documents"][0].update(name="../Positions.csv"), "document_name"),
        (lambda a: a["documents"][1].update(name=a["documents"][0]["name"]), "document_name"),
        (lambda a: a["documents"][1].update(sha256=a["documents"][0]["sha256"]), "document_hash"),
        (lambda a: a["documents"][0].update(sha256="ABC"), "document_hash"),
        (lambda a: a["documents"][0].update(text="a\fb"), "document_text"),
        (lambda a: a["documents"][0].update(text=""), "document_text"),
        (lambda a: a["documents"][0].update(text="a\x00b"), "nul"),
        (lambda a: a["documents"][0].update(size=0), "document_size"),
        (lambda a: a["documents"][0].update(truncated="no"), "truncated"),
        (lambda a: a["documents"][0].update(path="/etc"), "document_keys"),
        (lambda a: a.update(documents=[]), "documents"),
        (lambda a: a.update(documents=a["documents"] * 6), "documents"),
        (lambda a: a.update(text="a text beside the files"), "keys"),
        (lambda a: a.update(pages=1, pages_text=1, pages_image=0), "keys"),
    ],
)
def test_an_export_answer_that_breaks_a_rule_is_malformed(change: Any, rule: str) -> None:
    answer = good()
    change(answer)
    with pytest.raises(pc.ParserMalformedError) as caught:
        pc.check_parsed(pc.loads(main.encode(answer)), "linkedin_zip")
    assert rule in str(caught.value)


@pytest.mark.parametrize("kind", ["docx", "pdf_text"])
def test_no_other_kind_may_answer_with_files(kind: str) -> None:
    answer = {**good(), "kind": kind, "text": "a text"}
    with pytest.raises(pc.ParserMalformedError):
        pc.check_parsed(pc.loads(main.encode(answer)), kind)


def test_the_new_codes_are_in_the_closed_list() -> None:
    assert {"not_docx", "not_export", "macros", "nested_archive", "not_utf8"} <= pc.FILE_CODES
