"""The PDF reader for real: the worker's image, the `parser` service as compose.yml defines it,
its locked down child with pypdf inside, and files made here (tests/ingestion/pdf_fixtures.py)
sent with the application's own client. Nothing is mounted over the image and nothing is
simulated: the limits, the filter and the time budget are the real ones.

What it proves, each on Linux under the limits of the child (16 descriptors, no file, no
process, 256 MiB of address space, 45 s of CPU, the seccomp deny list):

- an ordinary PDF, of 1, 20 and 200 pages, comes back as its text, with what the child cost;
- every cap, every kind of protection (`cryptography` is blocked there: RC4 is read, AES is
  not), image pages and their note;
- each hostile file ends as text or as a code of the file, never as a death of the child, and
  the service answers the next job;
- the time budget: pages that are slow one after the other, and one page that never ends, both
  give back what was read before, with the note, at 35 s of CPU;
- the whole path of a document: the upload route, the parse job of the worker, this parser,
  and the row as the page reads it, on a real Postgres.

Marked `docker` (CI's api job runs these, serially). The image is built by the fixture from
api/Dockerfile (target `worker`) unless PARSER_TEST_IMAGE names one. About 45 s: the two tests
of the time budget run side by side on instances of their own while the rest runs.
`PDF_MEASURE_OUT=<file>` writes what each fixture cost, for the plan's Measurements.
"""

import asyncio
import functools
import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion import jobs
from app.ingestion import parser_client as pc
from app.ingestion.models import FailedKind, TextSource, TextStatus
from tests.identity.api_helpers import Api, Clock
from tests.ingestion import pdf_fixtures as fx
from tests.ingestion.helpers import listing, row, text_of, uploaded
from tests.ingestion.parser.helpers import API, HERE, ROOT, sandbox_module
from tests.pg import docker_available

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not (ROOT / "compose.yml").is_file(), reason="no repository root here"),
]

main = sandbox_module("main")
MIB = 1024 * 1024
DOORS = {"parser": 7071, "parser-slow": 7072, "parser-slower": 7073}
MEASURED: dict[str, dict[str, Any]] = {}


class Stack:
    """The running test stack, and the application's client pointed at one of its parsers."""

    def __init__(self, project: str, image: str) -> None:
        self.project, self.image = project, image
        self.compose = ["docker", "compose", "-p", project, "-f", str(HERE / "compose.pdf.yml")]
        self.env = {
            **os.environ,
            "PARSER_TEST_IMAGE": image,
            "POSTGRES_USER": "unused",
            "POSTGRES_PASSWORD": "unused",
            "POSTGRES_DB": "unused",
        }
        self.ports: dict[str, int] = {}

    def run(self, *arguments: str, check: bool = True, timeout: float = 300) -> str:
        done = subprocess.run(
            [*self.compose, *arguments],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if check and done.returncode != 0:
            raise AssertionError(f"compose {arguments[:4]} failed:\n{done.stdout}\n{done.stderr}")
        return done.stdout

    def port(self, parser: str) -> int:
        if parser not in self.ports:
            published = self.run("port", "relay", str(DOORS[parser])).strip()
            self.ports[parser] = int(published.rsplit(":", 1)[1])
        return self.ports[parser]

    def parse(self, parser: str = "parser") -> jobs.Parse:
        """parser_client.parse, through the door to that parser: what the worker calls."""
        return functools.partial(pc.parse, host="127.0.0.1", port=self.port(parser))

    def read(self, data: bytes, parser: str = "parser", name: str = "") -> pc.Parsed:
        """One PDF through the real service. Raises the client's error for a refusal."""
        started = time.monotonic()
        parsed = asyncio.run(self.parse(parser)("pdf_text", data))
        if name:
            MEASURED[name] = {
                "bytes": len(data),
                "wall_ms": int((time.monotonic() - started) * 1000),
                "pages": parsed.pages,
                "pages_read": (parsed.pages_text or 0) + (parsed.pages_image or 0),
                "characters": len(parsed.text or ""),
                **dict(parsed.usage or {}),
            }
        return parsed

    def refusal(self, data: bytes, parser: str = "parser") -> tuple[str, str, dict[str, int]]:
        with pytest.raises(pc.ParserRefusedError) as caught:
            self.read(data, parser)
        return caught.value.failure, caught.value.code, dict(caught.value.values)

    def alive(self) -> None:
        """The service still answers, and its sandbox is in force: nothing a file did hurt it."""
        report = asyncio.run(pc.ping(host="127.0.0.1", port=self.port("parser")))
        assert report.in_force, report.problems


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    if not docker_available():
        pytest.skip("Docker is not available")
    image = os.environ.get("PARSER_TEST_IMAGE", "")
    built = not image
    if built:
        image = f"jobapp-worker:pdf-test-{os.getpid()}"
        done = subprocess.run(
            ["docker", "build", "-q", "--target", "worker", "-t", image, str(API)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"the worker image did not build:\n{done.stderr[-3000:]}"
    running = Stack(f"jobapp-pdf-test-{os.getpid()}", image)
    try:
        running.run("up", "-d", "--wait", "--wait-timeout", "120")
        yield running
    finally:
        running.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)
        if built:
            subprocess.run(["docker", "rmi", image], capture_output=True, check=False)
        out = os.environ.get("PDF_MEASURE_OUT")
        if out and MEASURED:
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(MEASURED, handle, indent=2, sort_keys=True)
                handle.write("\n")  # the end-of-file hook of pre-commit wants one


@pytest.fixture(scope="module")
def slow(stack: Stack) -> Iterator[dict]:
    """The two files that only the time budget ends, started together as soon as the stack is
    up, each on a parser of its own. A test that needs one waits for it."""
    found: dict[str, Any] = {}

    def job(name: str, parser: str, data: bytes) -> None:
        try:
            found[name] = stack.read(data, parser, name=name)
        except Exception as error:
            found[name] = error

    threads = [
        threading.Thread(target=job, args=("slow pages", "parser-slow", fx.slow_pdf(200, 60_000))),
        threading.Thread(
            target=job, args=("one endless page", "parser-slower", fx.one_endless_page_pdf())
        ),
    ]
    for thread in threads:
        thread.start()

    def wait(name: str) -> Any:
        deadline = time.monotonic() + 120
        while name not in found and time.monotonic() < deadline:
            time.sleep(0.2)
        assert name in found, f"did not come back: {name}"
        return found[name]

    yield {"wait": wait}
    for thread in threads:
        thread.join(120)


@pytest.fixture(scope="module", autouse=True)
def start_the_slow_jobs_first(slow: dict) -> None:
    """Module wide and automatic: the 35 s start with the first test of the file."""


def notes(parsed: pc.Parsed) -> list[tuple[str, dict[str, int]]]:
    return [(note.code, dict(note.values)) for note in parsed.notes]


# --- an ordinary PDF, and what it costs ---------------------------------------------------------


@pytest.mark.parametrize("pages", [1, 20, 200])
def test_a_text_pdf_is_read_within_the_limits_of_the_child(stack: Stack, pages: int) -> None:
    parsed = stack.read(fx.text_pdf(pages, rows=20, compress=True), name=f"text, {pages} pages")
    slots = parsed.text.split("\f")
    assert (parsed.pages, parsed.pages_text, parsed.pages_image) == (pages, pages, 0)
    assert len(slots) == pages
    assert slots[0].split("\n")[0] == f"{fx.SENTENCE} Line 1 of page 1."
    assert slots[-1].split("\n")[-1] == f"{fx.SENTENCE} Line 20 of page {pages}."
    assert (parsed.truncated, parsed.notes) == (False, ())
    usage = parsed.usage
    assert usage is not None
    # Far inside what the child may use: 256 MiB of address space, 35 s of CPU.
    assert 0 < usage["max_vm_kb"] * 1024 < main.LIMITS["pdf_text"]["as"] // 2
    assert 0 < usage["max_rss_kb"] <= usage["max_vm_kb"]
    assert usage["cpu_ms"] < 15_000


def test_the_child_runs_under_the_limits_of_its_kind(stack: Stack) -> None:
    """The limits are set from main.LIMITS before the handler is imported: 256 MiB for this
    kind, and the 16 descriptors, no file and no process every test of this file ran under."""
    limits = main.LIMITS["pdf_text"]
    assert limits["as"] == 256 * MIB
    assert (limits["nofile"], limits["fsize"], limits["nproc"], limits["cpu"]) == (16, 0, 0, 45)
    assert main.LIMITS["ping"]["as"] == 512 * MIB, "the other kinds keep theirs until measured"
    stack.alive()


def test_a_20_mib_pdf_is_read(stack: Stack) -> None:
    data = fx.padded_pdf(20 * MIB)
    parsed = stack.read(data, name="20 MiB, 1 page")
    assert (parsed.pages, parsed.pages_text) == (1, 1)
    assert parsed.text.startswith(fx.SENTENCE)
    assert parsed.usage is not None
    assert parsed.usage["max_vm_kb"] * 1024 < main.LIMITS["pdf_text"]["as"] // 2


def test_links_and_cleaning_come_through(stack: Stack) -> None:
    page = fx.Page(
        fx.lines(*fx.page_text(1, 3).split("\n")),
        links=["https://example.org/jane", "mailto:jane@example.org"],
    )
    parsed = stack.read(fx.build([page]))
    assert parsed.text.split("\n")[-1] == "Links: https://example.org/jane mailto:jane@example.org"


# --- the caps -----------------------------------------------------------------------------------


def test_the_201st_page_is_not_read(stack: Stack) -> None:
    parsed = stack.read(fx.text_pdf(201, rows=2))
    assert (parsed.pages, parsed.pages_text, parsed.truncated) == (201, 200, True)
    assert notes(parsed) == [("pages_not_read", {"from": 201, "to": 201})]


def test_the_text_stops_at_the_character_cap(stack: Stack) -> None:
    parsed = stack.read(fx.text_pdf(150, rows=60))
    assert pc.MAX_TEXT_CHARS - 100 < len(parsed.text) <= pc.MAX_TEXT_CHARS
    assert parsed.truncated is True
    assert notes(parsed) == [("text_limit", {"from": parsed.pages_text + 1, "to": 150})]


def test_500_000_characters_outside_the_basic_plane_arrive_whole(stack: Stack) -> None:
    """The largest answer this reader can write: 2 MB of UTF-8 on the pipe, under the 4 MiB
    cap, where ASCII escapes would have been 6 MB."""
    parsed = stack.read(fx.astral_pdf(520_000, per_page=2600), name="500,000 astral characters")
    assert len(parsed.text) <= pc.MAX_TEXT_CHARS
    assert parsed.text.count(fx.ASTRAL) > 480_000
    assert len(parsed.text.encode()) > 1_900_000
    assert (parsed.truncated, notes(parsed)[0][0]) == (True, "text_limit")


# --- protected ----------------------------------------------------------------------------------

PLAIN = fx.text_pdf(2, rows=4)


def test_an_rc4_restricted_pdf_is_read_with_the_empty_password(stack: Stack) -> None:
    parsed = stack.read(fx.encrypted(PLAIN, "RC4-128"))
    assert parsed.text == stack.read(PLAIN).text
    assert (parsed.pages, parsed.pages_text) == (2, 2)


@pytest.mark.parametrize(
    ("algorithm", "password", "code"),
    [
        ("RC4-128", "a real password", "protected"),
        ("AES-128", "", "encryption"),
        ("AES-256", "", "encryption"),
        ("AES-256", "a real password", "encryption"),
        ("AES-128", "a real password", "protected"),
    ],
)
def test_what_the_child_cannot_open_is_refused_with_its_code(
    stack: Stack, algorithm: str, password: str, code: str
) -> None:
    """`cryptography` is not importable in the child (the ping reports it blocked, and
    stack.alive() requires that report): pypdf reads RC4 and nothing else there."""
    assert stack.refusal(fx.encrypted(PLAIN, algorithm, password)) == ("file", code, {})
    report = asyncio.run(pc.ping(host="127.0.0.1", port=stack.port("parser")))
    assert report.sandbox["cryptography_blocked"] is True


# --- pages that are images ----------------------------------------------------------------------


def test_a_mixed_pdf_keeps_its_text_and_counts_its_scans(stack: Stack) -> None:
    parsed = stack.read(fx.mixed_pdf())
    assert [bool(slot) for slot in parsed.text.split("\f")] == [True, False, True, False, True]
    assert (parsed.pages, parsed.pages_text, parsed.pages_image) == (5, 3, 2)
    assert notes(parsed) == [("scans_not_read", {"count": 2})]


def test_a_pdf_without_text_and_one_of_blanks_are_refused(stack: Stack) -> None:
    scans = fx.build([fx.scanned_page(), fx.scanned_page()])
    assert stack.refusal(scans) == ("file", "no_text", {"count": 2})
    assert stack.refusal(fx.build([fx.Page(fx.lines("  ", " "))])) == (
        "file",
        "no_text",
        {"count": 1},
    )


# --- hostile ------------------------------------------------------------------------------------

HOSTILE = [
    ("cut in the middle", fx.truncated_pdf, "unreadable"),
    ("a page tree that loops", fx.cyclic_tree_pdf, "unreadable"),
    ("a page tree 150 deep", lambda: fx.nested_tree_pdf(150), "bomb"),
    ("a page tree 5,000 deep", lambda: fx.nested_tree_pdf(5000), "bomb"),
    ("arrays and dictionaries 5,000 deep", lambda: fx.nested_arrays_pdf(5000), "unreadable"),
    ("a stream that inflates to 1 GiB", lambda: fx.inflating_pdf(1024 * MIB), "bomb"),
    (
        "a stream of 64 MiB that declares 10 bytes",
        lambda: fx.inflating_pdf(64 * MIB, declared=10),
        "bomb",
    ),
    ("forty filters on one stream", fx.filter_chain_pdf, "bomb"),
    ("a gigabyte of objects from 28 MB of operators", fx.memory_hungry_pdf, "bomb"),
    ("a PDF header on a ZIP archive", fx.pdf_with_zip_tail, "unreadable"),
    ("not a PDF", lambda: b"MZ\x90\x00 not a pdf at all", "unreadable"),
    ("a PDF without a page", lambda: fx.build([]), "unreadable"),
]


@pytest.mark.parametrize(("name", "make", "code"), HOSTILE, ids=[case[0] for case in HOSTILE])
def test_a_hostile_file_ends_as_a_code_and_the_service_lives(
    stack: Stack, name: str, make: Any, code: str
) -> None:
    started = time.monotonic()
    assert stack.refusal(make()) == ("file", code, {})
    assert time.monotonic() - started < 30, "a code, long before any limit of time"
    stack.alive()
    # And the next document is read as if nothing had happened.
    assert stack.read(fx.text_pdf(1, rows=3)).pages_text == 1


def test_files_that_lie_are_read_for_what_they_hold(stack: Stack) -> None:
    lying = stack.read(fx.lying_count_pdf())
    assert (lying.pages, lying.pages_text) == (1, 1), "50 million pages claimed, one found"
    assert stack.read(fx.looping_xref_pdf()).pages_text == 1


def test_scripts_attached_files_and_form_fields_are_never_text(stack: Stack) -> None:
    parsed = stack.read(fx.active_pdf())
    assert parsed.text.startswith(fx.SENTENCE)
    for marker in (fx.SCRIPT_MARKER, fx.ATTACHED_MARKER, fx.FIELD_MARKER, "alert", "notes.txt"):
        assert marker not in parsed.text
    stack.alive()


# --- the time budget ----------------------------------------------------------------------------


def test_slow_pages_are_stopped_at_the_budget_and_the_pages_read_are_kept(slow: dict) -> None:
    """200 pages of half a second each: the handler looks at the budget between two pages,
    stops at 35 s of CPU and answers what it has, ten seconds before the kill."""
    parsed = slow["wait"]("slow pages")
    assert isinstance(parsed, pc.Parsed), parsed
    read = parsed.pages_text
    assert 5 < read < 200
    assert parsed.pages == 200
    assert parsed.truncated is True
    assert notes(parsed) == [("time_limit", {"from": read + 1, "to": 200})]
    assert parsed.text.count("\f") == read - 1
    assert parsed.text.split("\f")[read - 1] == fx.SENTENCE
    spent = parsed.usage["cpu_ms"]
    assert main.BUDGET_CPU_S * 1000 - 500 < spent < main.LIMITS["pdf_text"]["cpu"] * 1000


def test_one_page_that_never_ends_is_interrupted_and_the_page_before_it_kept(slow: dict) -> None:
    """The second page would take minutes: the timer interrupts pypdf in the middle of it at
    the budget. Without it the child would be killed at 45 s and the first page lost."""
    parsed = slow["wait"]("one endless page")
    assert isinstance(parsed, pc.Parsed), parsed
    assert (parsed.pages, parsed.pages_text, parsed.pages_image) == (3, 1, 0)
    assert notes(parsed) == [("time_limit", {"from": 2, "to": 3})]
    assert parsed.text.startswith(fx.SENTENCE)
    assert parsed.usage["cpu_ms"] < (main.BUDGET_CPU_S + 3) * 1000


# --- the whole path of a document ---------------------------------------------------------------


def test_an_uploaded_pdf_becomes_text_through_the_job_and_the_real_parser(
    stack: Stack, api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    """The upload route, the worker's job, the service, the row: only the queue is left out
    (test_jobs_worker.py runs it on Redis)."""
    ctx = {"sessions": factory, "clock": clock, "ingestion_parse": stack.parse()}
    # A PDF whose pages all hold text is read and done, as before G8: nothing is rendered,
    # nothing waits for a transcription.
    page = fx.Page(fx.lines(*fx.page_text(1, 12).split("\n")), links=["https://example.org/robin"])
    whole = uploaded(api, alice, fx.build([page, fx.page_text(2, 12)]), "cv.pdf")
    assert (whole["status"], whole["readable"]) == ("pending", True)
    assert asyncio.run(jobs.parse_document(ctx, whole["id"])) == jobs.DONE
    stored = row(factory, whole["id"])
    assert (stored.text_status, stored.raw) == (TextStatus.done, None)
    assert (stored.pages, stored.pages_text, stored.pages_image) == (2, 2, 0)
    assert (stored.note, stored.text_source) == (None, TextSource.parsed)
    first, second = stored.text.split("\f")
    assert first.split("\n")[-1] == "Links: https://example.org/robin"
    assert second.split("\n")[0] == f"{fx.SENTENCE} Line 1 of page 2."
    shown = text_of(api, alice, whole["id"]).json()
    assert (shown["text"], shown["images"]) == (stored.text, [])

    # The same with a scanned page between two pages of text. Until G8 it ended `done` with a
    # note ("1 page holds no text and waits for the image reader."). Now the scanned page is
    # rendered and the document waits for its transcription: the two pages read are stored and
    # shown at once, the slot of the scan is empty, and its image sits at its page index.
    data = fx.build([page, fx.scanned_page(), fx.page_text(3, 12)])
    body = uploaded(api, alice, data, "scan.pdf")
    assert (body["status"], body["readable"]) == ("pending", True)
    assert asyncio.run(jobs.parse_document(ctx, body["id"])) == jobs.DONE
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.raw) == (TextStatus.awaiting_transcription, None)
    assert (stored.pages, stored.pages_text, stored.pages_image) == (3, 2, 1)
    assert (stored.note, stored.text_source) == (None, TextSource.parsed)
    first, scan, third = stored.text.split("\f")
    assert first.split("\n")[-1] == "Links: https://example.org/robin"
    assert scan == ""
    assert third.split("\n")[0] == f"{fx.SENTENCE} Line 1 of page 3."
    item = next(item for item in listing(api, alice)["items"] if item["id"] == body["id"])
    assert (item["status"], item["pages_text"], item["pages_image"]) == (
        "awaiting_transcription",
        2,
        1,
    )
    shown = text_of(api, alice, body["id"]).json()
    assert shown["text"] == stored.text
    assert [image["position"] for image in shown["images"]] == [1]

    protected = uploaded(api, alice, fx.encrypted(fx.text_pdf(1, 5), "AES-256"), "locked.pdf")
    assert asyncio.run(jobs.parse_document(ctx, protected["id"])) == jobs.FAILED
    stored = row(factory, protected["id"])
    assert (stored.text_status, stored.failed_kind) == (TextStatus.failed, FailedKind.file)
    assert stored.failed_reason == jobs.FILE_SENTENCES["encryption"]
    assert (stored.raw, stored.text) == (None, None)

    cut = uploaded(api, alice, fx.truncated_pdf(), "cut.pdf")
    assert asyncio.run(jobs.parse_document(ctx, cut["id"])) == jobs.FAILED
    assert row(factory, cut["id"]).failed_reason == jobs.FILE_SENTENCES["unreadable"]


def test_a_parser_that_is_stopped_is_a_failure_of_the_server_not_of_the_file(
    stack: Stack,
    slow: dict,
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    clock: Clock,
) -> None:
    """Last in the file: it stops a parser, once that one has finished its slow job. The job
    keeps the document for one more try, then fails it for the server; the bytes go only then."""
    slow["wait"]("slow pages")
    stack.run("stop", "-t", "0", "parser-slow")
    ctx = {"sessions": factory, "clock": clock, "ingestion_parse": stack.parse("parser-slow")}
    data = fx.text_pdf(1, rows=5)
    body = uploaded(api, alice, data, "cv.pdf")
    assert asyncio.run(jobs.parse_document(ctx, body["id"])) == jobs.RETRY
    stored = row(factory, body["id"])
    assert (stored.text_status, stored.failed_kind, stored.raw) == (
        TextStatus.pending,
        FailedKind.server,
        data,
    )
    assert asyncio.run(jobs.parse_document(ctx, body["id"])) == jobs.FAILED
    stored = row(factory, body["id"])
    assert (stored.failed_kind, stored.failed_reason) == (FailedKind.server, jobs.SERVER_FAILED)
    assert stored.raw is None
