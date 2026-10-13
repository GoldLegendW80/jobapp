"""The PDF reader, sandbox/pdf_text.py, called in the test process on files made here
(tests/ingestion/pdf_fixtures.py): what an ordinary PDF becomes, every cap, every kind of
protection, and each hostile file ending as text or as a code, never as an exception.

Every answer is also passed through the worker's own reading of it (main.encode, then
parser_client.loads and check_parsed): what the handler writes is what the client accepts.

The protected files run in a process of their own that blocks `cryptography` first, as main.py
does: with it importable, pypdf would open the AES ones and the test would prove nothing.
The same files against the real child, under its limits and its filter, in the container of the
service: test_pdf_container.py.
"""

import importlib.util
import json
import subprocess
import sys
import threading
import time
from types import ModuleType
from typing import Any

import pytest

from app.ingestion import parser_client as pc
from tests.ingestion import pdf_fixtures as fx
from tests.ingestion.parser.helpers import SANDBOX, sandbox_module

main = sandbox_module("main")


def _load() -> ModuleType:
    path = SANDBOX / "pdf_text.py"
    spec = importlib.util.spec_from_file_location("parser_sandbox_pdf_text", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pdf_text = _load()


class Plenty:
    """A budget that is never spent."""

    def spent(self) -> bool:
        return False


class SpentAfter:
    """A budget that is spent from its `calls`th question on."""

    def __init__(self, calls: int) -> None:
        self.left = calls

    def spent(self) -> bool:
        self.left -= 1
        return self.left < 0


class SpentAt:
    """A budget that is spent `seconds` after it was made."""

    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds

    def spent(self) -> bool:
        return time.monotonic() >= self.end


def read(data: bytes, budget: Any = None) -> dict:
    return pdf_text.handle(data, {}, budget or Plenty())


def accepted(answer: dict) -> pc.Parsed:
    """The answer as the worker reads it, after the trip main.py gives it."""
    return pc.check_parsed(pc.loads(main.encode(answer)), "pdf_text")


def refused(answer: dict) -> tuple[str, str, dict]:
    with pytest.raises(pc.ParserRefusedError) as caught:
        accepted(answer)
    return caught.value.failure, caught.value.code, dict(caught.value.values)


def notes(answer: dict) -> list[tuple[str, dict]]:
    return [(note.code, dict(note.values)) for note in accepted(answer).notes]


# --- an ordinary PDF ----------------------------------------------------------------------------


def test_a_text_pdf_gives_its_pages_joined_by_form_feeds() -> None:
    answer = read(fx.text_pdf(3, rows=4))
    parsed = accepted(answer)
    pages = parsed.text.split("\f")
    assert len(pages) == 3
    assert (parsed.pages, parsed.pages_text, parsed.pages_image) == (3, 3, 0)
    assert parsed.truncated is False
    assert parsed.notes == ()
    for number, page in enumerate(pages, start=1):
        assert page.split("\n") == [
            f"{fx.SENTENCE} Line {row} of page {number}." for row in range(1, 5)
        ]
    assert answer["kind"] == "pdf_text"


def test_the_handler_is_the_one_main_loads_for_the_kind() -> None:
    """Through main.run_handler, with a real Budget: the file is found, loaded and called."""
    answer = main.run_handler("pdf_text", fx.text_pdf(1, rows=3), {})
    sys.modules.pop("pdf_text", None)
    assert accepted(answer).text.startswith(fx.SENTENCE)
    assert main.run_handler("pdf_text", b"not a pdf", {}) == {
        "ok": False,
        "failure": "file",
        "code": "unreadable",
    }
    sys.modules.pop("pdf_text", None)


def test_a_deflated_pdf_reads_the_same() -> None:
    assert read(fx.text_pdf(2, compress=True))["text"] == read(fx.text_pdf(2))["text"]


def test_the_addresses_of_link_annotations_follow_the_text_of_their_page() -> None:
    first = fx.Page(
        fx.lines(*fx.page_text(1, 3).split("\n")),
        links=[
            "https://example.org/jane",
            "mailto:jane@example.org",
            "https://example.org/jane",  # twice on the page: once in the text
            "https://example.org/a b\x00c",
        ],
    )
    parsed = accepted(read(fx.build([first, fx.page_text(2, 3)])))
    one, two = parsed.text.split("\f")
    assert one.split("\n")[-1] == (
        "Links: https://example.org/jane mailto:jane@example.org https://example.org/abc"
    )
    assert "Links:" not in two


def test_at_most_fifty_addresses_a_page_and_none_longer_than_the_cap() -> None:
    links = [f"https://example.org/{n}" for n in range(70)] + ["https://example.org/" + "x" * 5000]
    text = read(fx.build([fx.Page(fx.lines(*fx.page_text(1, 3).split("\n")), links=links[::-1])]))[
        "text"
    ]
    addresses = text.split("\n")[-1].removeprefix("Links: ").split(" ")
    assert len(addresses) == pdf_text.LINKS_MAX
    assert len(addresses[0]) == pdf_text.LINK_MAX_CHARS


def test_a_broken_annotation_never_costs_the_page() -> None:
    page = fx.Page(
        fx.lines(*fx.page_text(1, 3).split("\n")),
        extra=b"/Annots [ 999 0 R << /Subtype /Link /A 7 >> << /Subtype /Link /A << /URI 12 >> >> "
        b"<< /Subtype /Link >> << /Subtype /Widget >> "
        b"<< /Subtype /Link /A << /S /URI /URI (https://example.org/kept) >> >> ]",
    )
    text = read(fx.build([page]))["text"]
    assert text.split("\n")[-1] == "Links: https://example.org/kept"
    not_a_list = fx.Page(fx.lines(*fx.page_text(1, 3).split("\n")), extra=b"/Annots 5")
    assert "Links" not in read(fx.build([not_a_list]))["text"]


def test_the_text_of_a_page_is_cleaned() -> None:
    raw = "Cafe\u0301  \r\nnext\x0cpage\x00 \x1b[0m\ud83d end \n\n\n\n\nlast \u2028line\x85."
    assert pdf_text.clean(raw) == "Caf\u00e9\nnext\npage [0m end\n\nlast\nline\n."
    assert pdf_text.clean(" \n\t ") == ""


# --- pages that are images ----------------------------------------------------------------------


def test_a_mixed_pdf_keeps_its_text_and_an_empty_slot_for_each_scanned_page() -> None:
    answer = read(fx.mixed_pdf())
    parsed = accepted(answer)
    slots = parsed.text.split("\f")
    assert [bool(slot) for slot in slots] == [True, False, True, False, True]
    assert (parsed.pages, parsed.pages_text, parsed.pages_image) == (5, 3, 2)
    assert notes(answer) == [("scans_not_read", {"count": 2})]
    assert parsed.truncated is False
    assert "Page 4 of 5" not in parsed.text, "a page number under a scan is not a page of text"
    assert slots[4].startswith(fx.SENTENCE)


def test_a_page_counts_as_text_from_fifty_characters_that_are_not_blank() -> None:
    def page(letters: int) -> str:
        return " ".join(["abcde"] * (letters // 5))

    answer = read(fx.build([page(45), page(50), page(55)]))
    assert [len(slot.replace(" ", "")) for slot in answer["text"].split("\f")] == [0, 50, 55]
    assert (answer["pages_text"], answer["pages_image"]) == (2, 1)


def test_a_pdf_without_any_text_is_refused_with_the_count_of_its_pages() -> None:
    scans = fx.build([fx.scanned_page(), fx.scanned_page(), fx.Page(b"")])
    assert refused(read(scans)) == ("file", "no_text", {"count": 3})
    blank = fx.build([fx.Page(fx.lines("   ", " ")), "\t"])
    assert refused(read(blank)) == ("file", "no_text", {"count": 2})


# --- the caps -----------------------------------------------------------------------------------


def test_the_201st_page_is_not_read_and_the_note_says_so() -> None:
    answer = read(fx.text_pdf(201, rows=2))
    parsed = accepted(answer)
    assert (parsed.pages, parsed.pages_text, parsed.pages_image) == (201, 200, 0)
    assert parsed.text.count("\f") == 199
    assert parsed.truncated is True
    assert notes(answer) == [("pages_not_read", {"from": 201, "to": 201})]
    assert "page 200." in parsed.text
    assert "page 201." not in parsed.text


def test_exactly_200_pages_are_all_read() -> None:
    answer = read(fx.text_pdf(200, rows=2))
    assert (answer["pages"], answer["pages_text"], answer["truncated"]) == (200, 200, False)
    assert answer["notes"] == []


def test_the_text_stops_at_500_000_characters_and_names_the_pages_left_out() -> None:
    answer = read(fx.text_pdf(150, rows=60))  # about 5,600 characters a page
    parsed = accepted(answer)
    assert len(parsed.text) <= pdf_text.TEXT_MAX_CHARS
    assert len(parsed.text) > pdf_text.TEXT_MAX_CHARS - 100
    assert parsed.truncated is True
    read_pages = parsed.pages_text
    assert 80 < read_pages < 100
    assert notes(answer) == [("text_limit", {"from": read_pages + 1, "to": 150})]
    assert parsed.text.count("\f") == read_pages - 1


def test_a_last_page_cut_at_the_cap_leaves_no_page_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_text, "TEXT_MAX_CHARS", 1000)
    answer = read(fx.text_pdf(1, rows=20))
    assert len(answer["text"]) <= 1000
    assert answer["truncated"] is True
    assert answer["notes"] == [{"code": "text_limit"}]
    assert notes(answer) == [("text_limit", {})]


def test_a_page_that_no_longer_fits_at_all_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    whole = read(fx.text_pdf(3, rows=3))["text"]
    first, second, _ = whole.split("\f")
    # Room for the two first pages and their form feed, and not for one more form feed.
    monkeypatch.setattr(pdf_text, "TEXT_MAX_CHARS", len(first) + 1 + len(second))
    answer = read(fx.text_pdf(3, rows=3))
    assert answer["text"] == first + "\f" + second
    assert (answer["pages"], answer["pages_text"]) == (3, 2)
    assert answer["notes"] == [{"code": "text_limit", "from": 3, "to": 3}]
    # The same when the page that does not fit is an image page: its form feed has no room.
    scan_last = fx.build([fx.page_text(1, 3), fx.page_text(2, 3), fx.scanned_page()])
    answer = read(scan_last)
    assert (answer["pages_text"], answer["pages_image"]) == (2, 0)
    assert answer["notes"] == [{"code": "text_limit", "from": 3, "to": 3}]


def test_500_000_characters_outside_the_basic_plane_fit_the_answer() -> None:
    answer = read(fx.astral_pdf(520_000, per_page=2600))
    payload = main.encode(answer)
    assert len(payload) < main.MAX_ANSWER, "2 MB of UTF-8, where ASCII escapes would be 6 MB"
    parsed = accepted(answer)
    assert len(parsed.text) <= pdf_text.TEXT_MAX_CHARS
    assert parsed.text.count(fx.ASTRAL) > 480_000
    assert parsed.truncated is True
    assert parsed.pages == 200
    assert notes(answer)[0][0] == "text_limit"


# --- the time budget ----------------------------------------------------------------------------


def test_a_budget_spent_between_two_pages_keeps_the_pages_read() -> None:
    answer = read(fx.text_pdf(10, rows=3), SpentAfter(4))
    parsed = accepted(answer)
    assert (parsed.pages, parsed.pages_text) == (10, 4)
    assert parsed.truncated is True
    assert notes(answer) == [("time_limit", {"from": 5, "to": 10})]
    assert "page 4." in parsed.text
    assert "page 5." not in parsed.text


def test_a_budget_spent_before_the_first_page_is_a_file_too_slow_to_read() -> None:
    assert refused(read(fx.text_pdf(3), SpentAfter(0))) == ("file", "too_slow", {})


def heavy_page(operators: int = 400_000) -> fx.Page:
    body = b"BT /F1 6 Tf 56 780 Td " + b"(slow) Tj 1 0 Td " * operators + b"ET"
    return fx.Page(body, compress=True)


def test_one_page_written_to_take_for_ever_is_interrupted_and_the_pages_before_it_kept() -> None:
    """The second page alone takes several seconds of pypdf: the timer stops it in the middle,
    a quarter of a second after the budget ended."""
    data = fx.build([fx.page_text(1, 3), heavy_page(), fx.page_text(3, 3)])
    started = time.monotonic()
    answer = read(data, SpentAt(0.3))
    assert time.monotonic() - started < 3, "interrupted, not run to its end"
    parsed = accepted(answer)
    assert (parsed.pages, parsed.pages_text) == (3, 1)
    assert notes(answer) == [("time_limit", {"from": 2, "to": 3})]
    assert parsed.truncated is True


def test_a_first_page_that_never_ends_is_a_file_too_slow_to_read() -> None:
    started = time.monotonic()
    assert refused(read(fx.build([heavy_page()]), SpentAt(0.2))) == ("file", "too_slow", {})
    assert time.monotonic() - started < 3


def test_the_timer_is_gone_and_the_signal_restored_after_a_read() -> None:
    import signal

    before = signal.getsignal(signal.SIGALRM)
    read(fx.text_pdf(1, rows=3))
    assert signal.getsignal(signal.SIGALRM) == before
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_outside_the_main_thread_the_budget_is_still_checked_between_pages() -> None:
    found: list[dict] = []
    worker = threading.Thread(
        target=lambda: found.append(read(fx.text_pdf(5, rows=3), SpentAfter(2)))
    )
    worker.start()
    worker.join(30)
    assert found[0]["pages_text"] == 2
    assert found[0]["notes"] == [{"code": "time_limit", "from": 3, "to": 5}]


def test_a_ring_between_the_end_of_the_reading_and_the_end_of_the_watch_is_a_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RingsOnTheWayOut(pdf_text._Watch):
        def __exit__(self, *exc: object) -> None:
            super().__exit__(*exc)
            raise pdf_text._OutOfTimeError

    monkeypatch.setattr(pdf_text, "_Watch", RingsOnTheWayOut)
    answer = read(fx.text_pdf(2, rows=3))
    assert answer["pages_text"] == 2
    assert answer["truncated"] is True


# --- protected ----------------------------------------------------------------------------------

_ISOLATED = """
import importlib.util, json, sys
sys.modules["cryptography"] = None
spec = importlib.util.spec_from_file_location("pdf_text", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
class Plenty:
    def spent(self):
        return False
answer = module.handle(sys.stdin.buffer.read(), {}, Plenty())
loaded = sorted(name for name in sys.modules if name.split(".")[0] == "cryptography")
import pypdf._crypt_providers as providers
print(json.dumps({"answer": answer, "cryptography": loaded, "provider": providers.crypt_provider[0],
                  "blocked": sys.modules["cryptography"] is None}))
"""


def isolated(data: bytes) -> dict:
    """The handler in a process that blocks `cryptography` before pypdf is imported."""
    done = subprocess.run(
        [sys.executable, "-I", "-c", _ISOLATED, str(SANDBOX / "pdf_text.py")],
        input=data,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr.decode()[-2000:]
    found = json.loads(done.stdout)
    assert found["blocked"] is True
    assert found["cryptography"] == ["cryptography"], "the blocked name and nothing under it"
    assert found["provider"] == "local_crypt_fallback"
    return found["answer"]


PLAIN = fx.text_pdf(2, rows=4)


def test_an_rc4_file_that_only_forbids_copying_is_read_with_the_empty_password() -> None:
    answer = isolated(fx.encrypted(PLAIN, "RC4-128"))
    parsed = accepted(answer)
    assert parsed.text == read(PLAIN)["text"]
    assert (parsed.pages, parsed.pages_text) == (2, 2)
    assert accepted(isolated(fx.encrypted(PLAIN, "RC4-40"))).text == parsed.text


def test_an_rc4_file_with_a_real_password_is_protected() -> None:
    answer = isolated(fx.encrypted(PLAIN, "RC4-128", "a real password"))
    assert refused(answer) == ("file", "protected", {})
    assert fx.SENTENCE not in json.dumps(answer)


@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256", "AES-256-R5"])
@pytest.mark.parametrize("password", ["", "a real password"])
def test_an_aes_file_is_not_opened_without_cryptography(algorithm: str, password: str) -> None:
    """With or without a password, the parser has no AES and reads none of them. Without a
    password every one ends as `encryption`: an AES-128 file even accepts the empty password
    (its check is RC4 and MD5) and fails at the first string. With one, the files whose password
    check needs no AES (AES-128, and revision 5 of AES-256) say what is true of them: a
    password is needed. Revision 6 hashes its password with AES and cannot tell."""
    answer = isolated(fx.encrypted(PLAIN, algorithm, password))
    failure, code, _ = refused(answer)
    assert failure == "file"
    assert code == ("protected" if password and algorithm != "AES-256" else "encryption")
    assert fx.SENTENCE not in json.dumps(answer)


def test_in_this_process_the_same_refusals_come_from_the_errors_pypdf_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Where `cryptography` is importable the AES files open. The mapping of pypdf's errors is
    then tried by hand: at opening and on a page."""
    errors = pdf_text.errors

    def raising(error: Exception) -> Any:
        def reader(*_: object, **__: object) -> None:
            raise error

        return reader

    for error, code in (
        (errors.DependencyError("cryptography>=3.1 is required"), "encryption"),
        (errors.FileNotDecryptedError("File has not been decrypted"), "protected"),
        (errors.WrongPasswordError("Wrong password"), "protected"),
        (errors.LimitReachedError("too much"), "bomb"),
        (MemoryError(), "bomb"),
        (ValueError("anything"), "unreadable"),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(pdf_text.pypdf, "PdfReader", raising(error))
            assert refused(read(PLAIN)) == ("file", code, {}), error
    for error, code in (
        (errors.DependencyError("cryptography>=3.1 is required"), "encryption"),
        (errors.FileNotDecryptedError("File has not been decrypted"), "protected"),
        (MemoryError(), "bomb"),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(pdf_text.pypdf.PageObject, "extract_text", raising(error))
            assert refused(read(PLAIN)) == ("file", code, {}), error


# --- hostile ------------------------------------------------------------------------------------


def test_not_a_pdf_an_empty_file_and_a_pdf_without_pages_are_unreadable() -> None:
    for data in (b"", b"hello", b"PK\x03\x04", b" %PDF-1.7", b"%PDF-1.7\n", fx.build([])):
        assert refused(read(data)) == ("file", "unreadable", {}), data[:20]


def test_a_pdf_cut_in_the_middle_is_unreadable() -> None:
    assert refused(read(fx.truncated_pdf())) == ("file", "unreadable", {})
    whole = fx.text_pdf(2)
    for cut in (20, len(whole) // 3, len(whole) - 40):
        answer = read(whole[:cut])
        assert answer["ok"] is False or accepted(answer).text


def test_a_page_tree_that_loops_is_unreadable() -> None:
    assert refused(read(fx.cyclic_tree_pdf())) == ("file", "unreadable", {})


@pytest.mark.parametrize("depth", [150, 5000])
def test_a_page_tree_nested_past_the_limit_is_a_bomb(depth: int) -> None:
    assert refused(read(fx.nested_tree_pdf(depth))) == ("file", "bomb", {})


def test_a_page_tree_of_ordinary_depth_is_read() -> None:
    assert accepted(read(fx.nested_tree_pdf(50))).pages == 1


def test_arrays_nested_without_end_break_their_page_not_the_reader() -> None:
    assert refused(read(fx.nested_arrays_pdf(5000))) == ("file", "unreadable", {})
    # Next to a page that reads, the broken one is an empty slot.
    deep = fx.Page(b"BT /F1 11 Tf " + b"[" * 5000 + b"]" * 5000 + b" TJ ET")
    answer = read(fx.build([fx.page_text(1, 3), deep]))
    assert (answer["pages_text"], answer["pages_image"]) == (1, 1)


def test_a_stream_that_inflates_to_a_gigabyte_is_a_bomb() -> None:
    started = time.process_time()
    assert refused(read(fx.inflating_pdf(1024 * fx.MIB))) == ("file", "bomb", {})
    assert time.process_time() - started < 20
    # Among pages that read, it is one empty slot: the others are kept.
    one = fx.inflating_pdf(64 * fx.MIB)
    assert refused(read(one)) == ("file", "bomb", {})


def test_a_stream_far_longer_than_it_declares_ends_as_a_code() -> None:
    answer = read(fx.inflating_pdf(64 * fx.MIB, declared=10))
    assert refused(answer)[:2] in {("file", "no_text"), ("file", "bomb"), ("file", "unreadable")}


def test_a_chain_of_forty_filters_is_a_bomb() -> None:
    assert refused(read(fx.filter_chain_pdf(40))) == ("file", "bomb", {})


def test_a_tree_that_claims_fifty_million_pages_has_the_pages_it_has() -> None:
    parsed = accepted(read(fx.lying_count_pdf()))
    assert (parsed.pages, parsed.pages_text) == (1, 1)


def test_a_cross_reference_table_that_points_at_itself_is_read() -> None:
    assert accepted(read(fx.looping_xref_pdf())).pages_text == 1


def test_scripts_attached_files_and_form_fields_are_never_text() -> None:
    data = fx.active_pdf()
    for marker in (fx.SCRIPT_MARKER, fx.ATTACHED_MARKER, fx.FIELD_MARKER):
        assert marker.encode() in data, "the fixture carries it"
    parsed = accepted(read(data))
    assert parsed.text.startswith(fx.SENTENCE)
    for marker in (fx.SCRIPT_MARKER, fx.ATTACHED_MARKER, fx.FIELD_MARKER, "alert", "notes.txt"):
        assert marker not in parsed.text


def test_a_pdf_header_on_a_zip_archive_is_unreadable() -> None:
    assert refused(read(fx.pdf_with_zip_tail())) == ("file", "unreadable", {})


def test_a_20_mib_pdf_is_read() -> None:
    data = fx.padded_pdf(20 * fx.MIB)
    assert len(data) == 20 * fx.MIB
    parsed = accepted(read(data))
    assert (parsed.pages, parsed.pages_text) == (1, 1)


def test_a_page_that_cannot_be_fetched_is_an_empty_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    real = pdf_text.pypdf.PdfReader

    class Pages:
        def __init__(self, pages: Any) -> None:
            self.pages = pages

        def __len__(self) -> int:
            return len(self.pages)

        def __getitem__(self, index: int) -> Any:
            if index == 1:
                raise ValueError("a page object that is no dictionary")
            if index == 2:
                raise MemoryError
            return self.pages[index]

    class Reader:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.pages = Pages(real(*args, **kwargs).pages)

    monkeypatch.setattr(pdf_text.pypdf, "PdfReader", Reader)
    assert refused(read(fx.text_pdf(3, rows=3))) == ("file", "bomb", {}), "the memory limit"
    answer = read(fx.text_pdf(2, rows=3))
    assert (answer["pages_text"], answer["pages_image"]) == (1, 1)


def test_the_limits_given_to_pypdf_are_fields_it_has_and_lower_than_its_own() -> None:
    """On an upgrade of pypdf: a renamed field would raise here, not in the child."""
    defaults = pdf_text.pypdf.Configuration()
    with pdf_text.pypdf.apply_configuration(**pdf_text.LIMITS) as applied:
        for name, value in pdf_text.LIMITS.items():
            assert getattr(applied, name) == value
            if isinstance(value, bool) or value is None:
                continue
            assert value <= getattr(defaults, name), name
    assert pdf_text.pypdf.get_configuration() == defaults, "nothing is left changed"
    assert pdf_text.PAGES_MAX == pc.MAX_READ_PAGES
    assert pdf_text.TEXT_MAX_CHARS == pc.MAX_TEXT_CHARS
    assert pdf_text.LIMITS["page_tree_maximum_entries"] == pc.MAX_PAGES


def test_pypdf_is_the_version_the_plan_pins_and_its_warnings_are_off() -> None:
    import logging

    assert pdf_text.pypdf.__version__ == "6.20.1"
    read(fx.truncated_pdf())
    assert not logging.getLogger("pypdf._reader").isEnabledFor(logging.CRITICAL)


def test_the_codes_the_handler_answers_are_codes_the_client_knows() -> None:
    source = (SANDBOX / "pdf_text.py").read_text(encoding="utf-8")
    for code in ("unreadable", "protected", "encryption", "bomb", "no_text", "too_slow"):
        assert f'"{code}"' in source
        assert code in pc.FILE_CODES
    for code in ("pages_not_read", "text_limit", "time_limit", "scans_not_read"):
        assert f'"{code}"' in source
        assert code in pc.NOTE_CODES
