"""The pure parts of documents.py: the type from the first bytes, decoding and cleaning a text,
display names, and the X-Filename header. No database."""

import codecs
from urllib.parse import quote

import pytest

from app.ingestion import documents
from app.ingestion.documents import (
    REFUSED,
    UNTITLED,
    DocumentError,
    Kind,
    clean_name,
    clean_text,
    decode_filename,
    decode_text,
    kind_of,
    sniff,
)
from app.ingestion.models import NAME_MAX_CHARS, TEXT_MAX_CHARS
from tests.ingestion.helpers import archive, jpeg, pdf, png

# --- sniff ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "mime"),
    [
        (pdf(64), "application/pdf"),
        (b"%PDF-", "application/pdf"),
        (archive(), "application/zip"),
        (archive(b"Connections.csv", b"First Name,Last Name"), "application/zip"),
        (b"PK\x03\x04", "application/zip"),
        (png(64), "image/png"),
        (jpeg(64), "image/jpeg"),
    ],
)
def test_a_kept_binary_kind_is_named_from_its_first_bytes(head: bytes, mime: str) -> None:
    assert sniff(head) == mime


@pytest.mark.parametrize(
    "head",
    [
        b"",
        b"plain words",
        b"# A Markdown title\n",
        b'{"json": true}',
        b"a,b,c\n1,2,3\n",
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        b"<!doctype html><script>alert(1)</script>",
        b"#!/bin/sh\necho hello\n",
        b"%PDF",  # one byte short of the magic
        b"PK\x05\x06" + b"\x00" * 18,  # an empty archive: not the local header magic
        b"MZ is how this sentence starts",  # text, not a program
        b"\x00\x00\x00\x18ftypmp42",  # a video: unknown, left to the text check
        b"RIFF\x24\x00\x00\x00WAVEfmt ",
    ],
)
def test_anything_else_is_left_to_the_text_check(head: bytes) -> None:
    assert sniff(head) is None


@pytest.mark.parametrize(
    ("head", "rule"),
    [
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32, "legacy-office"),
        (b"{\\rtf1\\ansi Hello}", "rtf"),
        (archive(b"mimetype", b"application/vnd.oasis.opendocument.text"), "opendocument"),
        (archive(b"mimetype", b"application/epub+zip"), "unsupported"),
        (b"GIF89a\x01\x00\x01\x00", "gif"),
        (b"GIF87a\x01\x00\x01\x00", "gif"),
        (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "webp"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00", "heic"),
        (b"\x00\x00\x00\x1cftypmif1\x00\x00\x00\x00", "heic"),
        (b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00", "heic"),
        (b"\x7fELF\x02\x01\x01\x00", "program"),
        (b"MZ\x90\x00\x03\x00\x00\x00", "program"),
        (b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01", "program"),
        (b"\xca\xfe\xba\xbe\x00\x00\x00\x02", "program"),
    ],
)
def test_a_known_type_that_is_not_read_is_refused_with_its_sentence(head: bytes, rule: str) -> None:
    with pytest.raises(DocumentError) as refused:
        sniff(head)
    assert (refused.value.status, refused.value.rule) == (415, rule)
    assert refused.value.message == REFUSED[rule]


def test_every_refusal_is_one_plain_sentence_or_two_without_a_dash() -> None:
    for message in (*REFUSED.values(), documents.TOO_LARGE, documents.QUOTA, documents.TOO_MANY):
        assert message.endswith(".")
        assert " - " not in message
        assert "\u2014" not in message
        assert "\u2013" not in message


def test_kind_of() -> None:
    assert kind_of("text/plain") is Kind.text
    assert kind_of("application/pdf") is Kind.pdf
    assert kind_of("application/zip") is Kind.archive
    assert kind_of("image/png") is Kind.image
    assert kind_of("image/jpeg") is Kind.image
    # Types a later parser writes: a ZIP it found to be a DOCX, the CSV files of an export.
    assert kind_of(documents.DOCX_MIME) is Kind.archive
    assert kind_of("text/csv") is Kind.text
    # A type nobody declared is never taken for one that has a reader.
    for unknown in ("application/x-something-new", "text/html", "image/gif", ""):
        assert kind_of(unknown) is Kind.other
    # G2b: the PDF. G7: the archive. G8: the image.
    assert frozenset({Kind.text, Kind.pdf, Kind.archive, Kind.image}) == documents.READABLE_KINDS
    assert Kind.other not in documents.READABLE_KINDS


# --- decode_text ---------------------------------------------------------------------------


def test_utf8_text_is_decoded() -> None:
    text = "na\u00efve caf\u00e9 \u65e5\u672c\u8a9e \U0001f600\n"
    assert decode_text(text.encode()) == (text, False)
    assert decode_text(bytearray(text.encode())) == (text, False)


def test_a_utf8_bom_is_dropped() -> None:
    assert decode_text(codecs.BOM_UTF8 + b"hello") == ("hello", False)
    assert decode_text(codecs.BOM_UTF8) == ("", False)


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_utf16_with_its_bom_is_decoded(codec: str) -> None:
    bom = codecs.BOM_UTF16_LE if codec == "utf-16-le" else codecs.BOM_UTF16_BE
    text = "R\u00e9sum\u00e9 \U0001f600 fin"
    assert decode_text(bom + text.encode(codec)) == (text, False)


@pytest.mark.parametrize(
    "data",
    [
        b"\x00" * 100,
        b"text with a \x00 inside",
        b"\xff\xfe\xfd",  # a UTF-16 BOM, then half a code unit
        b"\xc3\x28",  # invalid UTF-8
        b"caf\xe9",  # latin-1
        b"cut \xe2\x82",  # a sequence cut at the end of the file
        "h\x00i".encode("utf-16"),  # a NUL character behind a BOM
        "plain".encode("utf-16-le"),  # UTF-16 without its BOM: NUL bytes
        codecs.BOM_UTF32_LE + "hi".encode("utf-32-le"),
        codecs.BOM_UTF16_LE + b"\x00\xd8",  # a lone surrogate
    ],
)
def test_what_is_not_text_is_none(data: bytes) -> None:
    assert decode_text(data) is None


def test_an_empty_file_decodes_to_nothing() -> None:
    assert decode_text(b"") == ("", False)


def test_a_sequence_split_between_two_slices_is_still_one_character(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(documents, "_SLICE", 3)
    text = "\u00e9\u00e9\u00e9 \U0001f600 \u65e5\u672c"
    assert decode_text(text.encode()) == (text, False)
    assert decode_text(codecs.BOM_UTF16_LE + text.encode("utf-16-le")) == (text, False)


def test_only_the_beginning_of_a_long_text_is_kept_and_the_rest_is_still_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(documents, "_SLICE", 100)
    monkeypatch.setattr(documents, "_RAW_MAX_CHARS", 250)
    kept, more = decode_text(b"a" * 1000) or ("", False)
    assert (len(kept), more) == (300, True)  # the slice that passes the cap is kept whole
    assert decode_text(b"a" * 300) == ("a" * 300, False)
    # An invalid byte far behind the part that is kept still makes the file "not text".
    assert decode_text(b"a" * 900 + b"\xff" + b"a" * 99) is None


def test_a_slice_that_ends_inside_a_character_past_the_cap_is_not_more_text_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the cap, a slice holding only the first byte of a character decodes to nothing: it
    says nothing about there being more text. The character itself, one slice later, does."""
    monkeypatch.setattr(documents, "_SLICE", 1)
    monkeypatch.setattr(documents, "_RAW_MAX_CHARS", 2)
    assert decode_text(b"ab") == ("ab", False)
    assert decode_text(b"ab\xc3\xa9") == ("ab", True)
    assert decode_text(b"ab\xc3") is None  # and a character never finished is not text


def test_the_raw_cap_leaves_room_for_the_cut() -> None:
    assert documents._RAW_MAX_CHARS >= 2 * TEXT_MAX_CHARS


# --- clean_text ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("a\r\nb\rc\nd", "a\nb\nc\nd"),
        ("page one\x0cpage two\x0bthree", "page one\npage two\nthree"),
        ("line\u2028sep\u2029para\x85nel", "line\nsep\npara\nnel"),
        ("tab\tkept", "tab\tkept"),
        ("bell\x07 null\x00 esc\x1b del\x7f c1\x9b gone", "bell null esc del c1 gone"),
        ("e\u0301", "\u00e9"),  # NFC
        ("\ufeffstarts with a BOM", "starts with a BOM"),
        (
            "zero\u200dwidth joiner and \u200f marks stay",
            "zero\u200dwidth joiner and \u200f marks stay",
        ),
        ("", ""),
    ],
)
def test_clean_text(raw: str, cleaned: str) -> None:
    assert clean_text(raw) == cleaned


# --- names ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("notes.md", "notes.md"),
        ("  spaced   out  .txt ", "spaced out .txt"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\config", "config"),
        ("C:\\Users\\robin\\CV final.pdf", "CV final.pdf"),
        ("/absolute/path/cv.pdf", "cv.pdf"),
        ("folder/", "folder"),
        ("../", UNTITLED),
        ("..", UNTITLED),
        ("/", UNTITLED),
        ("", UNTITLED),
        ("   ", UNTITLED),
        (".env", ".env"),
        ("line\nbreak\ttab\x00null.txt", "linebreaktabnull.txt"),
        ("r\u00e9sum\u00e9 \u65e5\u672c\u8a9e.pdf", "r\u00e9sum\u00e9 \u65e5\u672c\u8a9e.pdf"),
        (
            "\u0633\u064a\u0631\u0629 \u0630\u0627\u062a\u064a\u0629.pdf",
            "\u0633\u064a\u0631\u0629 \u0630\u0627\u062a\u064a\u0629.pdf",
        ),
        (
            "cv \U0001f600\U0001f468\u200d\U0001f4bb.png",
            "cv \U0001f600\U0001f468\u200d\U0001f4bb.png",
        ),
        # The override that would show "exe.fdp" as "pdf.exe" is dropped.
        ("invoice\u202efdp.exe", "invoicefdp.exe"),
        ("e\u0301.txt", "\u00e9.txt"),
    ],
)
def test_clean_name(raw: str, name: str) -> None:
    assert clean_name(raw) == name


def test_a_name_is_cut_at_the_limit() -> None:
    assert clean_name("x" * 1000) == "x" * NAME_MAX_CHARS
    assert len(clean_name("\U0001f600" * 500)) == NAME_MAX_CHARS
    # Cut, then trimmed: the limit never leaves a trailing space.
    assert clean_name("a" * (NAME_MAX_CHARS - 1) + " b") == "a" * (NAME_MAX_CHARS - 1)


@pytest.mark.parametrize(
    ("header", "name"),
    [
        (None, UNTITLED),
        ("", UNTITLED),
        ("   ", UNTITLED),
        ("UTF-8''notes.md", "notes.md"),
        ("utf-8''notes.md", "notes.md"),
        ("UTF-8'en'notes.md", "notes.md"),
        (
            "UTF-8''" + quote("r\u00e9sum\u00e9 \U0001f600.pdf", safe=""),
            "r\u00e9sum\u00e9 \U0001f600.pdf",
        ),
        ("UTF-8''..%2F..%2Fetc%2Fpasswd", "passwd"),
        ("UTF-8''%00%0A%09", UNTITLED),
        ("UTF-8''", UNTITLED),
        ("UTF-8''%FF%FE", UNTITLED),  # not UTF-8
        ("UTF-8''%E2%82", UNTITLED),  # a sequence cut short
        ("UTF-8''100%25%20done.txt", "100% done.txt"),
        ("UTF-8''bad%zzescape.txt", "bad%zzescape.txt"),  # not an escape: kept as written
        ("plain name.txt", "plain name.txt"),
        ("ISO-8859-1''caf%E9.txt", "ISO-8859-1''caf%E9.txt"),  # another charset: a plain value
        ("caf\u00c3\u00a9.txt", "caf\u00e9.txt"),  # raw UTF-8 bytes, as a server reads them
        ("caf\u00e9.txt", UNTITLED),  # raw latin-1: not UTF-8
        ("\u65e5\u672c.txt", UNTITLED),  # not even latin-1
    ],
)
def test_decode_filename(header: str | None, name: str) -> None:
    assert decode_filename(header) == name


def test_a_thousand_character_header_is_cut_not_refused() -> None:
    header = "UTF-8''" + quote("\u00e9" * 1000 + ".txt", safe="")
    assert decode_filename(header) == "\u00e9" * NAME_MAX_CHARS
