"""The image reader and the renderer in the real parser service (task G8 of the ingestion plan):
the worker's image, the service's own compose settings, the child under its limits and its
seccomp filter. What only the real thing can show:

- Pillow and PDFium work under the lockdown: no thread, no process, no file (assumption A3),
  and PDFium's pause callback, a ctypes closure, can be made there;
- the worst inputs the guards let through stay inside the address space of their kind, and
  what each one cost is written down for the plan's Measurements;
- each hostile picture or PDF ends as a code of the closed lists, the service answers the next
  job, and nothing was written anywhere in its container;
- an input that meets the memory limit ends as a code of the FILE, and the next job is served.

Marked `docker`. The image is built by the fixture from api/Dockerfile (target `worker`) under
a tag of its own unless PARSER_TEST_IMAGE names one. `IMAGE_MEASURE_OUT=<file>` writes what
each fixture cost.
"""

import asyncio
import json
import os
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion import parser_client as pc
from tests.ingestion import image_fixtures as images
from tests.ingestion import pdf_fixtures as pdfs
from tests.ingestion import scan_fixtures as scans
from tests.ingestion.parser.helpers import API, ROOT, sandbox_module
from tests.ingestion.parser.test_pdf_container import Stack
from tests.pg import docker_available

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not (ROOT / "compose.yml").is_file(), reason="no repository root here"),
]

main = sandbox_module("main")
MIB = 1024 * 1024
MEASURED: dict[str, dict[str, Any]] = {}


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    if not docker_available():
        pytest.skip("Docker is not available")
    image = os.environ.get("PARSER_TEST_IMAGE", "")
    built = not image
    if built:
        image = f"jobapp-worker:image-test-{os.getpid()}"
        done = subprocess.run(
            ["docker", "build", "-q", "--target", "worker", "-t", image, str(API)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"the worker image did not build:\n{done.stderr[-3000:]}"
    running = Stack(f"jobapp-image-test-{os.getpid()}", image)
    try:
        running.run("up", "-d", "--wait", "--wait-timeout", "120")
        yield running
    finally:
        running.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)
        if built:
            subprocess.run(["docker", "rmi", image], capture_output=True, check=False)
        out = os.environ.get("IMAGE_MEASURE_OUT")
        if out and MEASURED:
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(MEASURED, handle, indent=2, sort_keys=True)
                handle.write("\n")


def read(stack: Stack, kind: str, data: bytes, options: dict | None = None, name: str = ""):
    """One file through the real service. Raises the client's error for a refusal."""
    started = time.monotonic()
    try:
        parsed = asyncio.run(stack.parse()(kind, data, options))
    except pc.ParserError as error:
        if name:
            MEASURED[name] = {
                "bytes": len(data),
                "wall_ms": int((time.monotonic() - started) * 1000),
                "failure": error.failure,
                "code": error.code,
            }
        raise
    usage = parsed.usage
    assert usage is not None
    if name:
        MEASURED[name] = {
            "bytes": len(data),
            "wall_ms": int((time.monotonic() - started) * 1000),
            "images": len(parsed.images),
            "image_bytes": sum(len(one.jpeg) for one in parsed.images),
            **dict(usage),
        }
    assert 0 < usage["max_vm_kb"] * 1024 <= main.LIMITS[kind]["as"]
    return parsed


def refusal(stack: Stack, kind: str, data: bytes, options: dict | None = None) -> tuple[str, str]:
    with pytest.raises(pc.ParserRefusedError) as caught:
        read(stack, kind, data, options)
    return caught.value.failure, caught.value.code


def outcome(stack: Stack, kind: str, data: bytes, options: dict | None, name: str) -> str:
    """ "ok", or "file/<code>", or "server/<code>": for an input whose answer may be either a
    result or a refusal, and must not be a failure of the server."""
    try:
        read(stack, kind, data, options, name)
    except pc.ParserError as error:
        return f"{error.failure}/{error.code}"
    return "ok"


def test_the_children_run_under_the_limits_of_their_kinds(stack: Stack) -> None:
    for kind in ("image", "pdf_render"):
        limits = main.LIMITS[kind]
        assert (limits["nofile"], limits["fsize"], limits["nproc"], limits["cpu"]) == (16, 0, 0, 45)
    assert main.LIMITS["image"]["as"] == main.IMAGE_AS
    assert main.LIMITS["pdf_render"]["as"] == main.PDF_RENDER_AS
    stack.alive()


def test_pillow_reads_pictures_under_the_lockdown(stack: Stack) -> None:
    for name, data in (
        ("a screenshot PNG", images.screenshot_png()),
        ("a phone photo with EXIF", images.rotated_jpeg()),
        ("a CMYK JPEG", images.cmyk_jpeg()),
        ("a noise picture over 300,000 bytes at quality 50", images.noise_png()),
    ):
        parsed = read(stack, "image", data, name=name)
        (stored,) = parsed.images
        assert stored.position == 0
        assert max(stored.width, stored.height) <= 1568
        assert images.GPS_MARK not in stored.jpeg
    stack.alive()


# The largest pictures the header guard lets through, by what they cost a decoder.
WORST_IMAGES = [
    ("25 megapixels of RGBA PNG", lambda: images.blank_png(5000, 5000, colour=6)),
    ("25 megapixels of grey PNG", lambda: images.blank_png(5000, 5000)),
    ("a PNG 20,000 by 1,250", lambda: images.blank_png(20_000, 1250, colour=6)),
    ("a PNG 1,250 by 20,000", lambda: images.blank_png(1250, 20_000, colour=6)),
    ("25 megapixels of progressive JPEG", lambda: images.progressive_jpeg(5000, 5000)),
    ("25 megapixels of baseline JPEG", lambda: images.photo_jpeg(5000, 5000)),
]


@pytest.mark.parametrize(("name", "make"), WORST_IMAGES, ids=[row[0] for row in WORST_IMAGES])
def test_the_largest_image_the_guard_lets_through_is_read_inside_the_limit(
    stack: Stack, name: str, make: Any
) -> None:
    data = make()
    assert len(data) <= 20 * MIB, "an upload the API takes"
    parsed = read(stack, "image", data, name=name)
    (stored,) = parsed.images
    assert max(stored.width, stored.height) == 1568
    # With room to spare: the limit is not what an honest picture meets.
    assert MEASURED[name]["max_vm_kb"] * 1024 < main.LIMITS["image"]["as"] * 0.8
    stack.alive()


@pytest.mark.parametrize(
    ("name", "make", "code"), images.HOSTILE, ids=[row[0] for row in images.HOSTILE]
)
def test_a_hostile_image_ends_as_a_code_and_the_service_lives(
    stack: Stack, name: str, make: Any, code: str
) -> None:
    assert refusal(stack, "image", make()) == ("file", code)
    stack.alive()


def test_pdfium_renders_scans_under_the_lockdown(stack: Stack) -> None:
    parsed = read(stack, "pdf_render", scans.scan(8), {"pages": list(range(8))}, "8 scanned pages")
    assert [stored.position for stored in parsed.images] == list(range(8))
    assert {(stored.width, stored.height) for stored in parsed.images} == {(1108, 1568)}
    mixed = read(stack, "pdf_render", scans.mixed(), {"pages": [1, 3, 4], "check": [3, 4]})
    assert [stored.position for stored in mixed.images] == [1, 3]
    stack.alive()


def test_pdfium_reads_the_text_of_an_aes_pdf_under_the_lockdown(stack: Stack) -> None:
    data = pdfs.encrypted(pdfs.text_pdf(3), "AES-256")
    assert refusal(stack, "pdf_text", data) == ("file", "encryption")
    parsed = read(stack, "pdf_render", data, {"text": True}, "the text of 3 AES pages")
    assert (parsed.pages, parsed.pages_text, parsed.pages_image) == (3, 3, 0)
    assert pdfs.SENTENCE in (parsed.text or "")
    locked = pdfs.encrypted(pdfs.text_pdf(1), "AES-256", "a real password")
    assert refusal(stack, "pdf_render", locked, {"text": True}) == ("file", "protected")
    stack.alive()


# The heaviest pages the guards let through.
WORST_PAGES = [
    ("6 pages of 600 dpi grey scans, 17.5 MiB", lambda: scans.heavy_scans(6), list(range(6))),
    ("a page of 200 inches", lambda: scans.sized(14_400, 14_400), [0]),
    ("a page that draws one picture 5,000 times", lambda: scans.many_pictures(5000), [0]),
    ("a page with a JBIG2 stream of noise", lambda: scans.with_broken_picture("JBIG2Decode"), [0]),
    ("a page with a JPX stream of noise", lambda: scans.with_broken_picture("JPXDecode"), [0]),
    ("a picture that inflates to 75 MiB", lambda: scans.inflating_picture(5000), [0]),
]


@pytest.mark.parametrize(
    ("name", "make", "pages"), WORST_PAGES, ids=[row[0] for row in WORST_PAGES]
)
def test_the_heaviest_page_the_guards_let_through_is_rendered_inside_the_limit(
    stack: Stack, name: str, make: Any, pages: list[int]
) -> None:
    data = make()
    assert len(data) <= 20 * MIB, "an upload the API takes"
    parsed = read(stack, "pdf_render", data, {"pages": pages}, name)
    assert len(parsed.images) + sum(note.values.get("count", 0) for note in parsed.notes) == len(
        pages
    )
    assert MEASURED[name]["max_vm_kb"] * 1024 < main.LIMITS["pdf_render"]["as"] * 0.8
    stack.alive()


def test_an_input_that_meets_the_memory_limit_is_a_code_and_the_next_job_runs(
    stack: Stack,
) -> None:
    """The path the memory of the Jetson rests on. A page whose picture is a few kilobytes
    that inflate to far more than the child may hold: the kernel refuses the memory, to
    Python, to Pillow or to PDFium. The answer is never a hang and never a dead service; what
    it is, for each kind, is written in the plan's Measurements. Then the next job is read."""
    for name, kind, data, options in (
        ("a PDF picture that inflates to 1.7 GiB", "pdf_render", scans.inflating_picture(24_000),
         {"pages": [0]}),
        ("a PDF picture that inflates to 432 MiB", "pdf_render", scans.inflating_picture(12_000),
         {"pages": [0]}),
    ):  # fmt: skip
        started = time.monotonic()
        result = outcome(stack, kind, data, options, name)
        assert time.monotonic() - started < 60
        assert result in MEMORY_OUTCOMES, (name, result)
        stack.alive()
    parsed = read(stack, "pdf_render", scans.scan(1), {"pages": [0]})
    assert len(parsed.images) == 1
    (stored,) = read(stack, "image", images.screenshot_png()).images
    assert (stored.width, stored.height) == (1200, 800)


# What an input over the memory of the child may end as: a result (PDFium gave up on the
# picture and drew the page without it), the file's `bomb`, or the death of the child, which
# the server reports and the job counts as a try that failed.
MEMORY_OUTCOMES = {"ok", "file/bomb", "server/memory_limit", "server/crashed"}


def test_nothing_was_written_anywhere_in_the_container(stack: Stack) -> None:
    """After every file above: no file was created or changed in the parser's container, and
    no core was left by a child that died."""
    changed = subprocess.run(
        ["docker", "diff", stack.run("ps", "-q", "parser").strip()],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert changed.strip() == ""
    stack.alive()
