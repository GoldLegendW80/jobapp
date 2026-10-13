"""The worker's reading of what the parser answers, app/ingestion/parser_client.py: every byte
of an answer is hostile, and nothing of it reaches the application before it has passed here.

These are the checks alone, on answers written by hand and by a generator of malformed ones.
The same answers arriving over a connection are test_client_calls.py."""

import base64
import contextlib
import json
import random
import time
import tracemalloc
from typing import Any

import pytest

from app.ingestion import parser_client as pc
from tests.ingestion.parser.helpers import good_ping, good_sandbox, sandbox_module

server = sandbox_module("server")

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60


def jpeg_of(size: int) -> str:
    return base64.b64encode(b"\xff\xd8\xff" + b"\x00" * (size - 3)).decode()


def image(**changes: Any) -> dict:
    return {"position": 0, "width": 1568, "height": 1100, "jpeg": jpeg_of(64), **changes}


def malformed(answer: Any, rule: str, kind: str = "pdf_text") -> None:
    with pytest.raises(pc.ParserMalformedError) as raised:
        pc.check_parsed(answer, kind)
    assert raised.value.code == rule
    assert raised.value.failure == "server"


def refused(answer: Any) -> pc.ParserRefusedError:
    with pytest.raises(pc.ParserRefusedError) as raised:
        pc.check_parsed(answer, "pdf_text")
    return raised.value


# --- a result that passes -----------------------------------------------------------------------


def test_a_result_with_its_pages_its_note_and_an_image_is_read() -> None:
    answer = {
        "ok": True,
        "kind": "pdf_text",
        "text": "page one\f\fpage three",
        "pages": 44,
        "pages_text": 2,
        "pages_image": 1,
        "truncated": True,
        "notes": [{"code": "pages_not_read", "from": 31, "to": 44}, {"code": "scans_not_read"}],
        "images": [image(position=1)],
    }
    parsed = pc.check_parsed(answer, "pdf_text")
    assert parsed.text == "page one\f\fpage three"
    assert (parsed.pages, parsed.pages_text, parsed.pages_image) == (44, 2, 1)
    assert parsed.truncated is True
    assert [(note.code, dict(note.values)) for note in parsed.notes] == [
        ("pages_not_read", {"from": 31, "to": 44}),
        ("scans_not_read", {}),
    ]
    assert parsed.images == (pc.PageImage(1, 1568, 1100, base64.b64decode(jpeg_of(64))),)


def test_the_least_a_result_can_be() -> None:
    assert pc.check_parsed({"ok": True}, "docx") == pc.Parsed()
    assert pc.check_parsed({"ok": True, "text": None, "notes": [], "images": []}, "docx") == (
        pc.Parsed()
    )
    assert pc.check_parsed({"ok": True, "text": ""}, "docx").text == ""


def test_the_caps_are_those_of_the_columns_that_store_the_result() -> None:
    """Migration 0011: 500,000 characters of text, 300,000 bytes and 2,000 pixels a side per
    image. A result that passes here fits; one unit more does not pass."""
    assert (pc.MAX_TEXT_CHARS, pc.MAX_IMAGE_BYTES, pc.MAX_IMAGE_SIDE) == (500_000, 300_000, 2000)
    assert pc.MAX_READ_PAGES == 200
    assert pc.MAX_SIZE == 20 * 1024 * 1024
    assert pc.MAX_PAGES < 2**31, "ingestion_documents.pages is an integer column"

    # Characters, as Postgres counts them: 500,000 outside the basic plane are 500,000.
    assert len(pc.check_parsed({"ok": True, "text": "\U0001f600" * 500_000}, "docx").text) == (
        500_000
    )
    malformed({"ok": True, "text": "x" * 500_001}, "text_too_long")

    most = image(width=2000, height=2000, jpeg=jpeg_of(300_000))
    assert len(pc.check_parsed({"ok": True, "images": [most]}, "image").images[0].jpeg) == 300_000
    malformed({"ok": True, "images": [image(jpeg=jpeg_of(300_001))]}, "image_size")
    malformed({"ok": True, "images": [image(width=2001)]}, "image_side")
    malformed({"ok": True, "images": [image(height=2001)]}, "image_side")

    full = {"pages": 200, "pages_text": 170, "pages_image": 30, "text": "\f" * 199}
    assert pc.check_parsed({"ok": True, **full}, "pdf_text").pages == 200
    malformed({"ok": True, **full, "pages": 201, "pages_text": 171, "text": "\f" * 200}, "pages")


def test_what_the_child_cost_is_three_integers_or_nothing() -> None:
    """Added by main.py to every document it read (G2b): for the log and the measurements."""
    usage = {"cpu_ms": 288, "max_rss_kb": 38012, "max_vm_kb": 53440}
    parsed = pc.check_parsed({"ok": True, "text": "x", "usage": usage}, "docx")
    assert dict(parsed.usage) == usage
    assert pc.check_parsed({"ok": True, "text": "x"}, "docx").usage is None
    assert pc.check_parsed({"ok": True, "usage": None}, "docx").usage is None
    for wrong in (
        {},
        {"cpu_ms": 1, "max_rss_kb": 2},
        {**usage, "note": "a sentence"},
        {**usage, "cpu_ms": -1},
        {**usage, "cpu_ms": 1.5},
        {**usage, "max_vm_kb": "53440"},
        {**usage, "max_rss_kb": True},
        {**usage, "max_vm_kb": 10**11},
        [288, 38012, 53440],
        "288",
    ):
        if isinstance(wrong, dict) and 1.5 in wrong.values():
            continue  # a float never reaches this check: the decoder refuses it (test below)
        malformed({"ok": True, "text": "x", "usage": wrong}, "usage")


# --- the shape ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer", [None, True, 7, "ok", [], [{"ok": True}], {}, {"ok": "true"}, {"ok": 1}, {"ok": None}]
)
def test_an_answer_without_a_boolean_ok_is_no_answer(answer: Any) -> None:
    malformed(answer, "ok")


@pytest.mark.parametrize(
    "more",
    [{"html": "<script>"}, {"error": "a sentence"}, {"Text": "x"}, {"documents": []}, {"": 1}],
)
def test_a_key_that_is_not_known_refuses_the_whole_answer(more: dict) -> None:
    malformed({"ok": True, "text": "fine", **more}, "keys")


def test_an_answer_for_another_kind_than_the_one_asked_is_refused() -> None:
    assert pc.check_parsed({"ok": True, "kind": "docx"}, "docx") == pc.Parsed()
    malformed({"ok": True, "kind": "pdf_text"}, "kind", kind="docx")
    malformed({"ok": True, "kind": ["docx"]}, "kind", kind="docx")


@pytest.mark.parametrize("text", [7, True, ["a"], {"a": 1}, 1.5, b"bytes"])
def test_a_text_that_is_no_string_is_refused(text: Any) -> None:
    malformed({"ok": True, "text": text}, "text")


def test_a_text_holding_u0000_is_refused() -> None:
    """Postgres refuses it in a text column: stored, the job would fail on every try."""
    for text in ("\x00", "before\x00after", "x" * 499_999 + "\x00"):
        malformed({"ok": True, "text": text}, "nul")
    malformed(pc.loads(b'{"ok": true, "text": "a\\u0000b"}'), "nul")


def test_a_text_holding_a_lone_surrogate_is_refused() -> None:
    for text in ("\ud800", "a\udfffb", "\ud83d", "\ude00\ud83d", "x" * 1000 + "\udc00"):
        malformed({"ok": True, "text": text}, "surrogate")
    # As JSON can carry them: an escape that is half of a pair.
    malformed(pc.loads(b'{"ok": true, "text": "a\\ud83db"}'), "surrogate")
    # A whole pair is one character, and fine.
    assert pc.check_parsed(pc.loads(b'{"ok": true, "text": "\\ud83d\\ude00"}'), "docx").text == (
        "\U0001f600"
    )


@pytest.mark.parametrize("truncated", [0, 1, "false", None, []])
def test_truncated_is_a_boolean(truncated: Any) -> None:
    if truncated is None:
        return malformed({"ok": True, "truncated": None}, "truncated")
    malformed({"ok": True, "truncated": truncated}, "truncated")
    return None


# --- pages and form feeds -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "counts",
    [
        {"pages": -1, "pages_text": 0, "pages_image": 0},
        {"pages": 2, "pages_text": -1, "pages_image": 0},
        {"pages": 2, "pages_text": 1, "pages_image": -2147483648},
        {"pages": True, "pages_text": 1, "pages_image": 0},
        {"pages": 2.0, "pages_text": 1, "pages_image": 0},
        {"pages": "2", "pages_text": 1, "pages_image": 0},
        {"pages": 2, "pages_text": [1], "pages_image": 0},
        {"pages": 2},
        {"pages_text": 1, "pages_image": 0},
        {"pages": 2, "pages_text": 1},
        {"pages": 1, "pages_text": 1, "pages_image": 1},
        {"pages": 5000, "pages_text": 201, "pages_image": 0},
        {"pages": 5000, "pages_text": 150, "pages_image": 51},
        {"pages": 100_001, "pages_text": 1, "pages_image": 0},
        {"pages": 2**40, "pages_text": 1, "pages_image": 0},
    ],
)
def test_page_counts_that_are_wrong_or_do_not_fit_together_are_refused(counts: dict) -> None:
    malformed({"ok": True, **counts}, "pages")


@pytest.mark.parametrize(
    ("text", "pages_text", "pages_image", "passes"),
    [
        ("a\fb\fc", 3, 0, True),
        ("a\f\fc", 2, 1, True),
        ("\f\f", 0, 3, True),
        ("a", 1, 0, True),
        ("", 0, 0, True),
        ("", 1, 0, True),
        ("a\fb", 3, 0, False),
        ("a\fb\fc", 2, 0, False),
        ("a\fb\fc\f", 3, 0, False),
        ("abc", 2, 1, False),
        ("\f", 0, 0, False),
        ("a\fb", 1, 0, False),
    ],
)
def test_the_form_feeds_are_the_pages_minus_one(
    text: str, pages_text: int, pages_image: int, passes: bool
) -> None:
    """One slot per page read, text or image, joined by form feeds. A page too many or too few
    and every citation offset and every page number shown would be wrong."""
    answer = {
        "ok": True,
        "text": text,
        "pages": 300,
        "pages_text": pages_text,
        "pages_image": pages_image,
    }
    if passes:
        assert pc.check_parsed(answer, "pdf_text").text == text
    else:
        malformed(answer, "form_feeds")


def test_a_form_feed_without_page_counts_is_refused() -> None:
    malformed({"ok": True, "text": "a\fb"}, "form_feeds")
    assert pc.check_parsed({"ok": True, "text": "a\nb\x0bc"}, "docx").text == "a\nb\x0bc"


# --- notes: codes and integers, never a sentence ------------------------------------------------


@pytest.mark.parametrize(
    ("notes", "rule"),
    [
        ("pages 31 to 44 were not read", "notes"),
        ({"code": "pages_not_read"}, "notes"),
        ([{"code": "pages_not_read"}] * 17, "notes"),
        (["pages_not_read"], "notes"),
        ([None], "notes"),
        ([{}], "note_code"),
        ([{"from": 1, "to": 2}], "note_code"),
        ([{"code": "Pages 31 to 44 were not read (time limit)."}], "note_code"),
        ([{"code": "pages_not_read_please_call_this_number"}], "note_code"),
        ([{"code": 7}], "note_code"),
        ([{"code": ["pages_not_read"]}], "note_code"),
        ([{"code": "PAGES_NOT_READ"}], "note_code"),
        ([{"code": "pages_not_read", "text": "a sentence"}], "values"),
        ([{"code": "pages_not_read", "reason": 3}], "values"),
        ([{"code": "pages_not_read", "from": "31"}], "values"),
        ([{"code": "pages_not_read", "from": True}], "values"),
        ([{"code": "pages_not_read", "from": 31.0}], "values"),
        ([{"code": "pages_not_read", "from": -1}], "values"),
        ([{"code": "pages_not_read", "from": 44, "to": 31}], "values"),
        ([{"code": "pages_not_read", "count": 100_001}], "values"),
        ([{"code": "pages_not_read", "count": None}], "values"),
    ],
)
def test_a_note_is_a_code_of_the_list_with_integers(notes: Any, rule: str) -> None:
    malformed({"ok": True, "notes": notes}, rule)


def test_every_code_is_a_word_and_the_lists_do_not_overlap() -> None:
    lists = (pc.SERVER_CODES, pc.CHILD_SERVER_CODES, pc.FILE_CODES, pc.NOTE_CODES)
    for codes in lists:
        for code in codes:
            assert code.isascii()
            assert code.replace("_", "").isalnum()
            assert code == code.lower()
            assert len(code) <= 24
    assert not pc.FILE_CODES & (pc.SERVER_CODES | pc.CHILD_SERVER_CODES)
    assert not pc.NOTE_CODES & (pc.FILE_CODES | pc.SERVER_CODES | pc.CHILD_SERVER_CODES)


# --- images -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("images", "rule"),
    [
        ("a jpeg", "images"),
        ({"0": image()}, "images"),
        ([image(position=n) for n in range(9)], "images"),
        ([None], "images"),
        ([[0, 1568, 1100, jpeg_of(64)]], "images"),
        ([{k: v for k, v in image().items() if k != "width"}], "images"),
        ([{**image(), "name": "scan.jpg"}], "images"),
        ([image(position=-1)], "image_position"),
        ([image(position=200)], "image_position"),
        ([image(position=True)], "image_position"),
        ([image(position="0")], "image_position"),
        ([image(position=3), image(position=3)], "image_position"),
        ([image(width=0)], "image_side"),
        ([image(height=0)], "image_side"),
        ([image(width=-1568)], "image_side"),
        ([image(width=1568.0)], "image_side"),
        ([image(height=None)], "image_side"),
        ([image(jpeg=None)], "image_size"),
        ([image(jpeg=["/9j/"])], "image_size"),
        ([image(jpeg="")], "image_size"),
        ([image(jpeg="A" * 400_004)], "image_size"),
        ([image(jpeg="not base64 at all!")], "image_base64"),
        ([image(jpeg=jpeg_of(64) + "\n")], "image_base64"),
        ([image(jpeg=" " + jpeg_of(64))], "image_base64"),
        ([image(jpeg=jpeg_of(64)[:-1])], "image_base64"),
        ([image(jpeg="/9j/é")], "image_base64"),
        ([image(jpeg=base64.b64encode(PNG).decode())], "image_not_jpeg"),
        ([image(jpeg=base64.b64encode(b"GIF89a" + b"\x00" * 20).decode())], "image_not_jpeg"),
        ([image(jpeg=base64.b64encode(b"<svg onload=alert(1)>").decode())], "image_not_jpeg"),
        ([image(jpeg=base64.b64encode(b"\xff\xd8").decode())], "image_not_jpeg"),
        ([image(jpeg=base64.b64encode(b"%PDF-1.7 " + JPEG).decode())], "image_not_jpeg"),
    ],
)
def test_an_image_that_is_not_a_small_jpeg_at_a_page_refuses_the_answer(
    images: Any, rule: str
) -> None:
    malformed({"ok": True, "images": images}, rule, kind="pdf_render")


def test_a_batch_of_eight_images_at_their_largest_fits_the_answer() -> None:
    """Rendering runs in batches of 8 pages: 8 times 300,000 bytes in base64 is 3.2 MB, under
    the 4 MiB of an answer."""
    images = [image(position=n, jpeg=jpeg_of(300_000)) for n in range(8)]
    raw = json.dumps({"ok": True, "images": images}).encode()
    assert len(raw) < pc.MAX_ANSWER
    parsed = pc.check_parsed(pc.loads(raw), "pdf_render")
    assert [one.position for one in parsed.images] == list(range(8))
    assert all(one.jpeg.startswith(pc.JPEG_MAGIC) for one in parsed.images)


# --- an answer that says no ---------------------------------------------------------------------


def test_a_file_that_cannot_be_read_is_a_refusal_of_the_file() -> None:
    for code in sorted(pc.FILE_CODES):
        error = refused({"ok": False, "failure": "file", "code": code})
        assert (error.failure, error.code, dict(error.values)) == ("file", code, {})
        assert str(error) == f"file/{code}"
    error = refused({"ok": False, "failure": "file", "code": "bomb", "count": 20_000})
    assert dict(error.values) == {"count": 20_000}


def test_a_parser_that_could_not_do_it_is_a_refusal_of_the_server() -> None:
    for code in sorted(pc.CHILD_SERVER_CODES):
        error = refused({"ok": False, "failure": "server", "code": code})
        assert (error.failure, error.code) == ("server", code)
    assert "not_supported" in pc.CHILD_SERVER_CODES, "what the five kinds answer in this task"


@pytest.mark.parametrize(
    ("answer", "rule"),
    [
        ({"ok": False}, "failure"),
        ({"ok": False, "failure": "file"}, "failure"),
        ({"ok": False, "code": "protected"}, "failure"),
        ({"ok": False, "failure": "user", "code": "protected"}, "failure"),
        ({"ok": False, "failure": "FILE", "code": "protected"}, "failure"),
        ({"ok": False, "failure": ["file"], "code": "protected"}, "failure"),
        ({"ok": False, "failure": {"file": 1}, "code": "protected"}, "failure"),
        ({"ok": False, "failure": "file", "code": ["protected"]}, "failure"),
        ({"ok": False, "failure": "file", "code": {"protected": 1}}, "failure"),
        ({"ok": False, "failure": "file", "code": 7}, "failure"),
        ({"ok": False, "failure": "file", "code": None}, "failure"),
        ({"ok": False, "failure": "file", "code": "This PDF is protected."}, "failure"),
        ({"ok": False, "failure": "file", "code": "call_0800_123_now"}, "failure"),
        # A code of the other list: a file cannot be "not supported", a server "protected".
        ({"ok": False, "failure": "file", "code": "not_supported"}, "failure"),
        ({"ok": False, "failure": "server", "code": "protected"}, "failure"),
        ({"ok": False, "failure": "server", "code": "busy"}, "failure"),
        ({"ok": False, "failure": "file", "code": "protected", "text": "x"}, "keys"),
        ({"ok": False, "failure": "file", "code": "protected", "message": "a sentence"}, "keys"),
        ({"ok": False, "failure": "file", "code": "protected", "from": "1"}, "values"),
        ({"ok": False, "failure": "file", "code": "protected", "from": 9, "to": 1}, "values"),
    ],
)
def test_a_refusal_that_is_not_a_code_of_its_list_is_malformed(answer: dict, rule: str) -> None:
    malformed(answer, rule)


# --- the JSON itself ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "rule"),
    [
        (b"", "not_json"),
        (b"not json", "not_json"),
        (b"{", "not_json"),
        (b'{"ok": true} trailing', "not_json"),
        (b'{"ok": true}{"ok": true}', "not_json"),
        (b"\xef\xbb\xbf{}", "not_json"),
        (b"{'ok': true}", "not_json"),
        (b'{"ok": True}', "not_json"),
        (b'{"n": ' + b"9" * 5000 + b"}", "not_json"),
        (b"\xff\xfe{}", "not_utf8"),
        (b'{"text": "\xe9"}', "not_utf8"),
        (b'{"text": "\xed\xa0\x80"}', "not_utf8"),
        (b'{"ok": true, "ok": false}', "duplicate_key"),
        (b'{"a": {"text": "x", "text": "y"}}', "duplicate_key"),
        (b'{"pages": 1.0}', "float"),
        (b'{"pages": 1e3}', "float"),
        (b'{"pages": NaN}', "constant"),
        (b'{"pages": Infinity}', "constant"),
        (b'{"pages": -Infinity}', "constant"),
        (b"[" * 100_000, "too_deep"),
    ],
)
def test_what_is_not_strict_json_is_refused_by_name(raw: bytes, rule: str) -> None:
    with pytest.raises(pc.ParserMalformedError) as raised:
        pc.loads(raw)
    assert raised.value.code == rule


@pytest.mark.parametrize("depth", [1_000, 10_000, 100_000, 1_000_000])
def test_an_answer_nested_however_deep_is_malformed_and_never_a_crash(depth: int) -> None:
    """Whether the decoder follows 10,000 levels depends on the interpreter. Where it does, the
    result is a list, which is no answer; where it does not, RecursionError is caught."""
    for raw in (b"[" * depth + b"]" * depth, b'{"a":' * depth + b"1" + b"}" * depth):
        with pytest.raises(pc.ParserMalformedError):
            pc.check_parsed(pc.loads(raw), "pdf_text")
    inside = b'{"ok": true, "notes": ' + b"[" * depth + b"]" * depth + b"}"
    with pytest.raises(pc.ParserMalformedError):
        pc.check_parsed(pc.loads(inside), "pdf_text")


def test_decoding_the_costliest_4_mib_stays_bounded() -> None:
    """A million empty arrays, or objects, in 4 MiB: the most memory an answer can make the
    worker take for the moment of the check. Held here so that a change of decoder shows."""
    for raw in (
        b"[" + b"[]," * 1_398_000 + b"[]]",
        b"[" + b"{}," * 1_398_000 + b"{}]",
        b'{"ok": true, "notes": [' + b'{"code":7},' * 380_000 + b"{}]}",
    ):
        assert len(raw) <= pc.MAX_ANSWER
        tracemalloc.start()
        started = time.monotonic()
        with pytest.raises(pc.ParserMalformedError):
            pc.check_parsed(pc.loads(raw), "pdf_text")
        elapsed = time.monotonic() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak < 160 * 1024 * 1024, f"{peak / 1024 / 1024:.0f} MB"
        assert elapsed < 30


# --- the header line ----------------------------------------------------------------------------


def header(**changes: Any) -> bytes:
    line = {"ok": True, "failure": None, "code": "ok", "length": 10, **changes}
    return json.dumps(line).encode() + b"\n"


def test_a_header_announces_the_length_of_the_answer() -> None:
    assert pc.check_header(header()) == 10
    assert pc.check_header(header(length=0)) == 0
    assert pc.check_header(header(length=pc.MAX_ANSWER)) == 4 * 1024 * 1024


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        (b"\n", "not_json"),
        (b"HTTP/1.1 200 OK\r\n", "not_json"),
        (b"[]\n", "header"),
        (b"null\n", "header"),
        (b'{"ok": true, "length": 10}\n', "header"),
        (header(more=1), "header"),
        (header(failure="server"), "header"),
        (header(code="busy"), "header"),
        (header(ok=1), "header"),
        (header(ok="true"), "header"),
        (header(ok=None), "header"),
        (header(length=-1), "length"),
        (header(length=pc.MAX_ANSWER + 1), "length"),
        (header(length=2**70), "length"),
        (header(length=True), "length"),
        (header(length="10"), "length"),
        (header(length=None), "length"),
        (header(length=[10]), "length"),
        (b'{"ok": true, "failure": null, "code": "ok", "length": 10.0}\n', "float"),
        (header(ok=False, failure="server", code="busy", length=5), "header"),
        (header(ok=False, failure="file", code="protected", length=0), "header"),
        (header(ok=False, failure=None, code="busy", length=0), "header"),
        (header(ok=False, failure="server", code="ok", length=0), "header"),
        (header(ok=False, failure="server", code="made_up", length=0), "header"),
        (header(ok=False, failure="server", code="The parser is busy.", length=0), "header"),
        (header(ok=False, failure="server", code=["busy"], length=0), "header"),
        (header(ok=False, failure="server", code={"busy": 1}, length=0), "header"),
        (header(ok=False, failure="server", code=None, length=0), "header"),
        (header(ok=False, failure="server", code="not_supported", length=0), "header"),
    ],
)
def test_a_header_that_is_not_one_is_malformed(line: bytes, rule: str) -> None:
    with pytest.raises(pc.ParserMalformedError) as raised:
        pc.check_header(line)
    assert raised.value.code == rule


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("busy", pc.ParserBusyError),
        ("wall_limit", pc.ParserTimeoutError),
        ("cpu_limit", pc.ParserTimeoutError),
        ("read_timeout", pc.ParserTimeoutError),
        ("bad_request", pc.ParserRefusedError),
        ("header_too_long", pc.ParserRefusedError),
        ("unknown_kind", pc.ParserRefusedError),
        ("too_large", pc.ParserRefusedError),
        ("output_cap", pc.ParserRefusedError),
        ("sandbox", pc.ParserRefusedError),
        ("no_child", pc.ParserRefusedError),
        ("crashed", pc.ParserRefusedError),
    ],
)
def test_every_refusal_of_the_server_is_its_error_and_a_failure_of_the_server(
    code: str, error: type[pc.ParserError]
) -> None:
    with pytest.raises(pc.ParserError) as raised:
        pc.check_header(header(ok=False, failure="server", code=code, length=0))
    assert type(raised.value) is error
    assert (raised.value.code, raised.value.failure) == (code, "server")
    assert str(raised.value) == f"server/{code}"


def test_every_code_of_the_server_has_its_error() -> None:
    assert set(server.CODES) - {"ok"} == pc.SERVER_CODES


def test_the_errors_say_whose_failure_it_is() -> None:
    """What the engine acts on: a failure of the file is final, one of the server is tried once
    more. Only a refusal can be the file's."""
    for error in (
        pc.ParserDownError("unreachable"),
        pc.ParserBusyError("busy"),
        pc.ParserTimeoutError("deadline"),
        pc.ParserMalformedError("nul"),
        pc.ParserRefusedError("crashed"),
    ):
        assert error.failure == "server"
        assert isinstance(error, pc.ParserError)
    assert pc.ParserRefusedError("protected", failure="file").failure == "file"
    assert pc.ParserError.failure == "server"


# --- a ping -------------------------------------------------------------------------------------


def test_a_ping_of_a_locked_down_child_is_in_force() -> None:
    report = pc.check_ping(good_ping(b"abc"))
    assert report.in_force
    assert report.problems == ()
    assert (report.received, report.cpu_ms, report.max_rss_kb) == (3, 49, 26308)
    assert report.sandbox["kernel"] == "5.15.148-tegra"
    assert report.sandbox["denied"]["socket"] == "EPERM"


def changed(**changes: Any) -> dict:
    answer = good_ping()
    answer["sandbox"] = {**answer["sandbox"], **changes}
    return answer


def test_the_client_and_the_healthcheck_judge_a_report_alike() -> None:
    """The healthcheck of the service and the worker's client each hold the judgement, since
    neither may import the other. Every defect the one sees, the other sees: with the same
    names, or by refusing the report outright where a value has not even its type."""
    from tests.ingestion.parser.test_server_protocol import DEFECTS

    assert server.sandbox_problems(good_sandbox()) == []
    for change, problems in DEFECTS:
        try:
            found = list(pc.check_ping(changed(**change)).problems)
        except pc.ParserMalformedError as error:
            found = [f"malformed:{error.code}"]
        assert found in (problems, ["malformed:sandbox"]), change


@pytest.mark.parametrize(
    "change",
    [
        {"seccomp": "2"},
        {"seccomp": -1},
        {"seccomp": 2.0},
        {"filters": "two"},
        {"capabilities": -1},
        {"limits": []},
        {"limits": {"as": 1}},
        {"limits": {**good_sandbox()["limits"], "more": 1}},
        {"limits": {**good_sandbox()["limits"], "cpu": "45"}},
        {"denied": []},
        {"denied": {"socket": "EPERM", "invented_call": "EPERM"}},
        {"denied": {"socket": "a sentence about sockets"}},
        {"denied": {"socket": 1}},
        {"denied": {"socket": None}},
        {"isolated": "yes"},
        {"parent_hidden": 1},
        {"descriptors": "0,1,2"},
        {"descriptors": [0, 1, "2"]},
        {"descriptors": list(range(300))},
        {"writable": "/run"},
        {"writable": ["a sentence, not a place"]},
        {"writable": [7]},
        {"writable": ["/x"] * 17},
        {"machine": "riscv64"},
        {"machine": ["aarch64"]},
        {"kernel": "5.15 with <script>"},
        {"kernel": ""},
        {"kernel": "x" * 65},
        {"kernel": 5},
        {"uid": "root"},
        {"more": 1},
    ],
)
def test_a_report_whose_values_have_not_their_type_is_malformed(change: dict) -> None:
    with pytest.raises(pc.ParserMalformedError) as raised:
        pc.check_ping(changed(**change))
    assert raised.value.code == "sandbox"


@pytest.mark.parametrize(
    "change",
    [
        {"ok": False},
        {"ok": 1},
        {"kind": "pdf_text"},
        {"received": -1},
        {"received": 20 * 1024 * 1024 + 1},
        {"received": "0"},
        {"sha256": "e3b0"},
        {"sha256": "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855"},
        {"sha256": 7},
        {"usage": {"cpu_ms": 1}},
        {"usage": {"cpu_ms": -1, "max_rss_kb": 1}},
        {"usage": {"cpu_ms": 1.5, "max_rss_kb": 1}},
        {"usage": []},
        {"more": 1},
    ],
)
def test_a_ping_answer_that_is_not_one_is_malformed(change: dict) -> None:
    with pytest.raises(pc.ParserMalformedError) as raised:
        pc.check_ping({**good_ping(), **change})
    assert raised.value.code == "ping"
    for missing in ("ok", "kind", "sandbox", "received", "sha256", "usage"):
        with pytest.raises(pc.ParserMalformedError):
            pc.check_ping({k: v for k, v in good_ping().items() if k != missing})
    for junk in (None, [], "ping", 7):
        with pytest.raises(pc.ParserMalformedError):
            pc.check_ping(junk)
        with pytest.raises(pc.ParserMalformedError):
            pc.check_ping({**good_ping(), "sandbox": junk})


# --- anything at all ----------------------------------------------------------------------------

JUNK: list[Any] = [
    None, True, False, 0, 1, -1, 2**31, 2**70, 1.5, float("nan"), "", "x", "ok", "file",
    "protected", "\x00", "\ud800", "\f", "a\fb", "/9j/", [], [[]], [None], [1, 2], {}, {"a": 1},
    {"code": "pages_not_read"}, {"ok": True}, "x" * 600_000, b"bytes",
]  # fmt: skip


def mutate(value: Any, rng: random.Random, depth: int = 0) -> Any:
    """`value` with something, somewhere, replaced, removed or added."""
    roll = rng.random()
    if roll < 0.15 or depth > 6:
        return rng.choice(JUNK)
    if isinstance(value, dict) and value:
        changed_ = dict(value)
        key = rng.choice(sorted(changed_))
        if roll < 0.3:
            del changed_[key]
        elif roll < 0.4:
            changed_[rng.choice(["x", "text", "code", "ok", "from", ""])] = rng.choice(JUNK)
        else:
            changed_[key] = mutate(changed_[key], rng, depth + 1)
        return changed_
    if isinstance(value, list) and value:
        changed_ = list(value)
        at = rng.randrange(len(changed_))
        if roll < 0.3:
            changed_ = changed_ * rng.choice([0, 2, 20])
        else:
            changed_[at] = mutate(changed_[at], rng, depth + 1)
        return changed_
    return rng.choice(JUNK)


def holds(parsed: pc.Parsed) -> None:
    """What every result that passed must satisfy, whatever went in."""
    if parsed.text is not None:
        assert len(parsed.text) <= pc.MAX_TEXT_CHARS
        assert "\x00" not in parsed.text
        parsed.text.encode("utf-8")  # no lone surrogate
        slots = (parsed.pages_text or 0) + (parsed.pages_image or 0)
        assert parsed.text.count("\f") == max(slots, 1) - 1
    for count in (parsed.pages, parsed.pages_text, parsed.pages_image):
        assert count is None or (type(count) is int and 0 <= count <= pc.MAX_PAGES)
    assert type(parsed.truncated) is bool
    assert len(parsed.notes) <= pc.MAX_NOTES
    for note in parsed.notes:
        assert note.code in pc.NOTE_CODES
        assert all(type(number) is int and number >= 0 for number in note.values.values())
    assert len(parsed.images) <= pc.MAX_IMAGES
    for one in parsed.images:
        assert one.jpeg.startswith(pc.JPEG_MAGIC)
        assert 0 < len(one.jpeg) <= pc.MAX_IMAGE_BYTES
        assert 1 <= one.width <= pc.MAX_IMAGE_SIDE
        assert 1 <= one.height <= pc.MAX_IMAGE_SIDE


def test_whatever_an_answer_holds_it_is_a_result_that_holds_or_a_parser_error() -> None:
    """4,000 answers, each a good one with something broken in it: the checks end with a result
    that satisfies every rule, or with a ParserError. Never with another exception: a TypeError
    on an unhashable value would be a job failing in a way nobody wrote down."""
    rng = random.Random(84)  # noqa: S311, a repeatable generator of malformed answers
    good_result = {
        "ok": True,
        "kind": "pdf_text",
        "text": "one\f\fthree",
        "pages": 9,
        "pages_text": 2,
        "pages_image": 1,
        "truncated": False,
        "notes": [{"code": "pages_not_read", "from": 4, "to": 9}],
        "images": [image(position=1)],
    }
    good_refusal = {"ok": False, "failure": "file", "code": "protected", "count": 1}
    good_header = {"ok": True, "failure": None, "code": "ok", "length": 10}
    passed = 0
    for _ in range(4000):
        for good, check in (
            (good_result, lambda answer: pc.check_parsed(answer, "pdf_text")),
            (good_refusal, lambda answer: pc.check_parsed(answer, "pdf_text")),
            (good_ping(b"x"), pc.check_ping),
        ):
            answer = mutate(good, rng)
            for _ in range(rng.randrange(3)):
                answer = mutate(answer, rng)
            try:
                result = check(answer)
            except pc.ParserError as error:
                result = error
            if isinstance(result, pc.ParserError):
                assert result.failure in ("file", "server")
                assert isinstance(result.code, str)
                continue
            passed += 1
            if isinstance(result, pc.Parsed):
                holds(result)
        line = mutate(good_header, rng)
        try:
            raw = json.dumps(line).encode()
        except (TypeError, ValueError):
            continue
        with contextlib.suppress(pc.ParserError):
            assert 0 <= pc.check_header(raw) <= pc.MAX_ANSWER
    assert passed > 50, "some mutations must leave a valid answer, or nothing was exercised"


def test_no_cap_is_looser_than_the_check_constraint_of_its_column() -> None:
    """The limits migration 0011 puts on stored data (app/ingestion/models.py): a result that
    passed this client must never be refused by the database."""
    from app.ingestion import models

    assert pc.MAX_SIZE <= models.FILE_MAX_BYTES
    assert pc.MAX_TEXT_CHARS <= models.TEXT_MAX_CHARS
    assert pc.MAX_IMAGE_BYTES <= models.IMAGE_MAX_BYTES
    assert pc.MAX_IMAGE_SIDE <= models.IMAGE_MAX_SIDE
    assert pc.MAX_READ_PAGES <= models.PAGES_MAX
    assert pc.MAX_PAGES < 2**31
