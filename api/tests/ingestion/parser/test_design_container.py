"""The two layout readers of the design capture (documents D25a) in the real parser service: the
worker's image, the service's own compose settings, the child under its limits and its seccomp
filter, and the worker's own client (read_design). What only the real thing can show:

- PDFium's text page and object walk, and the style reader, work under the lockdown;
- the invented CV files and the worst inputs the caps let through stay under 80 % of the
  address space of their kind, and what each one cost is written down for the plan;
- each hostile file ends as a code of the file, the service answers the next job, and the
  sandbox is still in force.

Marked `docker`. The image is built by the fixture from api/Dockerfile (target `worker`) under
a tag of its own unless PARSER_TEST_IMAGE names one. `DESIGN_MEASURE_OUT=<file>` writes what
each fixture cost.
"""

import asyncio
import json
import os
import subprocess
import time
from collections.abc import Iterator

import pytest

from app.ingestion import parser_client as pc
from tests.ingestion import archive_fixtures as archives
from tests.ingestion import design_fixtures as fx
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
MEASURED: dict[str, dict] = {}


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    if not docker_available():
        pytest.skip("Docker is not available")
    image = os.environ.get("PARSER_TEST_IMAGE", "")
    built = not image
    if built:
        image = f"jobapp-worker:design-test-{os.getpid()}"
        done = subprocess.run(
            ["docker", "build", "-q", "--target", "worker", "-t", image, str(API)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"the worker image did not build:\n{done.stderr[-3000:]}"
    running = Stack(f"jobapp-design-test-{os.getpid()}", image)
    try:
        running.run("up", "-d", "--wait", "--wait-timeout", "120")
        yield running
    finally:
        running.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)
        if built:
            subprocess.run(["docker", "rmi", image], capture_output=True, check=False)
        out = os.environ.get("DESIGN_MEASURE_OUT")
        if out and MEASURED:
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(MEASURED, handle, indent=2, sort_keys=True)
                handle.write("\n")


def read(stack: Stack, kind: str, data: bytes, name: str = "") -> pc.DesignPdf | pc.DesignDocx:
    started = time.monotonic()
    found = asyncio.run(pc.read_design(kind, data, host="127.0.0.1", port=stack.port("parser")))
    assert found.usage is not None
    assert 0 < found.usage["max_vm_kb"] * 1024 < main.LIMITS[kind]["as"] * 0.8, found.usage
    if name:
        MEASURED[name] = {
            "bytes": len(data),
            "wall_ms": int((time.monotonic() - started) * 1000),
            **dict(found.usage),
        }
    return found


def refusal(stack: Stack, kind: str, data: bytes) -> tuple[str, str]:
    with pytest.raises(pc.ParserRefusedError) as caught:
        asyncio.run(pc.read_design(kind, data, host="127.0.0.1", port=stack.port("parser")))
    return caught.value.failure, caught.value.code


def test_invented_cv_pages_are_read_in_the_child(stack: Stack) -> None:
    found = read(stack, "design_pdf", fx.one_column(), "one_column")
    assert isinstance(found, pc.DesignPdf)
    assert found.read[0].runs[0].text == "Alex Example"
    assert found.fonts[found.read[0].runs[0].font].name == "ABCDEF+Calibri-Bold"
    two = read(stack, "design_pdf", fx.two_columns(), "two_columns")
    assert [run.text for run in two.read[0].runs] == ["Alex Example", *fx.LEFT, *fx.RIGHT]
    scan = read(stack, "design_pdf", fx.scanned(ocr="Alex Example " + fx.CANARY), "scan")
    assert scan.scanned is True
    assert not scan.read[0].runs


def test_the_worst_inputs_the_caps_let_through_stay_inside_the_limits(stack: Stack) -> None:
    read(stack, "design_pdf", fx.many_runs(lines=80, per_line=20), "runs_cap")
    read(stack, "design_pdf", fx.long_lines(lines=200, characters=280), "chars_cap")
    read(stack, "design_pdf", fx.many_shapes(5_000), "objects_cap")
    read(stack, "design_pdf", scans.heavy_scans(2), "heavy_scans")
    read(stack, "design_pdf", pdfs.text_pdf(200), "200_pages")


def test_a_word_document_gives_its_styles_in_the_child(stack: Stack) -> None:
    found = read(stack, "design_docx", fx.styled_docx(), "styled_docx")
    assert isinstance(found, pc.DesignDocx)
    assert found.theme["major"] == "Calibri Light"
    assert found.sections[0]["columns"] == 2
    many = "".join(
        f'<w:style w:type="paragraph" w:styleId="S{n}"><w:name w:val="s {n}"/></w:style>'
        for n in range(5_000)
    )
    capped = read(stack, "design_docx", fx.styled_docx(style_part=fx.styles(many)), "styles_cap")
    assert len(capped.styles) == pc.DESIGN_STYLES
    read(stack, "design_docx", archives.images_docx(), "images_docx")


HOSTILE = [
    ("design_pdf", "content_bomb", lambda: scans.content_bomb(), "bomb"),
    ("design_pdf", "memory_hungry", lambda: pdfs.memory_hungry_pdf(), "bomb"),
    ("design_pdf", "form_bomb", lambda: scans.form_bomb(), "bomb"),
    ("design_pdf", "aes", lambda: pdfs.encrypted(pdfs.text_pdf(1), "AES-256", "x"), "protected"),
    ("design_pdf", "not_a_pdf", lambda: b"%PDF-1.7 and nothing", "unreadable"),
    ("design_docx", "entity_bomb", archives.entity_bomb_docx, "bomb"),
    ("design_docx", "names", lambda: archives.distinct_names_docx("attribute"), "bomb"),
    ("design_docx", "deep", archives.deep_docx, "bomb"),
    ("design_docx", "ratio_bomb", archives.ratio_bomb, "bomb"),
    ("design_docx", "nested", archives.nested_archive, "nested_archive"),
    (
        "design_docx",
        "macros",
        lambda: archives.docx(content_types=archives.MACRO_CONTENT_TYPES),
        "macros",
    ),
]


@pytest.mark.parametrize(("kind", "name", "make", "code"), HOSTILE, ids=[r[1] for r in HOSTILE])
def test_a_hostile_file_ends_as_a_code_and_the_service_lives(
    stack: Stack, kind: str, name: str, make, code: str
) -> None:
    started = time.monotonic()
    assert refusal(stack, kind, make()) == ("file", code)
    MEASURED[name] = {"wall_ms": int((time.monotonic() - started) * 1000), "code": code}
    stack.alive()
