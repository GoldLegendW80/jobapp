"""The Word reader and the reader of a LinkedIn export for real (task G7): the worker's image,
the `parser` service as compose.yml defines it, its locked down child with defusedxml inside,
and files made here (tests/ingestion/archive_fixtures.py) sent with the application's own
client. Nothing is mounted over the image and nothing is simulated.

What it proves, each on Linux under the limits of the child (16 descriptors, no file, no
process, 256 MiB of address space, 45 s of CPU, the seccomp deny list):

- a CV in DOCX and an export come back as their text, with what the child cost;
- the files of real size (a 20 MiB document of images, 19 MiB of XML in one part, an export
  whose CSV files hold 20 MiB) stay far inside the address space of their kind;
- each hostile archive ends as a code of the closed list, never as a death of the child, the
  service answers the next job, and nothing was written anywhere in its container;
- the whole path: the upload route, the parse job of the worker, this parser, and the rows as
  the page reads them, on a real Postgres.

Marked `docker`. The image is built by the fixture from api/Dockerfile (target `worker`) under
a tag of its own unless PARSER_TEST_IMAGE names one. `ARCHIVE_MEASURE_OUT=<file>` writes what
each fixture cost, for the plan's Measurements.
"""

import asyncio
import json
import os
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion import documents, jobs
from app.ingestion import parser_client as pc
from app.ingestion.models import FailedKind, TextStatus
from tests.identity.api_helpers import Api, Clock
from tests.ingestion import archive_fixtures as fx
from tests.ingestion.conftest import ALICE
from tests.ingestion.helpers import listing, row, rows, text_of, uploaded, user_id
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
        image = f"jobapp-worker:archive-test-{os.getpid()}"
        done = subprocess.run(
            ["docker", "build", "-q", "--target", "worker", "-t", image, str(API)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"the worker image did not build:\n{done.stderr[-3000:]}"
    running = Stack(f"jobapp-archive-test-{os.getpid()}", image)
    try:
        running.run("up", "-d", "--wait", "--wait-timeout", "120")
        yield running
    finally:
        running.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)
        if built:
            subprocess.run(["docker", "rmi", image], capture_output=True, check=False)
        out = os.environ.get("ARCHIVE_MEASURE_OUT")
        if out and MEASURED:
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(MEASURED, handle, indent=2, sort_keys=True)
                handle.write("\n")


def read(stack: Stack, kind: str, data: bytes, name: str = "") -> pc.Parsed:
    """One archive through the real service. Raises the client's error for a refusal."""
    started = time.monotonic()
    parsed = asyncio.run(stack.parse()(kind, data))
    if name:
        MEASURED[name] = {
            "bytes": len(data),
            "wall_ms": int((time.monotonic() - started) * 1000),
            "characters": len(parsed.text or "") + sum(len(one.text) for one in parsed.documents),
            **dict(parsed.usage or {}),
        }
    usage = parsed.usage
    assert usage is not None
    # Far inside what the child may use.
    assert 0 < usage["max_vm_kb"] * 1024 < main.LIMITS[kind]["as"] // 2
    return parsed


def refusal(stack: Stack, kind: str, data: bytes) -> tuple[str, str]:
    with pytest.raises(pc.ParserRefusedError) as caught:
        asyncio.run(stack.parse()(kind, data))
    return caught.value.failure, caught.value.code


def test_the_children_run_under_the_limits_of_their_kinds(stack: Stack) -> None:
    for kind in ("docx", "linkedin_zip"):
        limits = main.LIMITS[kind]
        assert limits["as"] == 256 * MIB
        assert (limits["nofile"], limits["fsize"], limits["nproc"], limits["cpu"]) == (16, 0, 0, 45)
    stack.alive()


def test_a_cv_in_docx_is_read_by_the_real_child(stack: Stack) -> None:
    parsed = read(stack, "docx", fx.cv_docx(), name="docx, a CV")
    assert f"Find me on LinkedIn ({fx.LINK})" in (parsed.text or "")
    assert (parsed.pages, parsed.truncated, parsed.notes) == (None, False, ())
    assert parsed.usage is not None
    assert parsed.usage["cpu_ms"] < 5_000


def test_an_export_is_read_by_the_real_child(stack: Stack) -> None:
    assert refusal(stack, "docx", fx.export()) == ("file", "not_docx")
    parsed = read(stack, "linkedin_zip", fx.export(), name="export, three small files")
    assert [one.name for one in parsed.documents] == ["Profile.csv", "Positions.csv", "Skills.csv"]
    assert parsed.documents[1].text.startswith("Company Name,Title")


def test_a_20_mib_docx_of_images(stack: Stack) -> None:
    parsed = read(stack, "docx", fx.images_docx(), name="docx, 20 MiB of images")
    assert parsed.text == "A portfolio of four photographs."


def test_19_mib_of_xml_in_one_part_is_read_or_stopped_inside_the_limits(stack: Stack) -> None:
    """The heaviest thing a Word document can ask of the walker: a part of 19 MiB whose text is
    short. It is parsed to its end or stopped by the time budget, with what was read."""
    runs = "".join(f'<w:r w:rsidR="{word}"/>' for word in fx.words(850_000, 5).split(" "))
    data = fx.docx(fx.paragraph(fx.run("The first line.")), fx.paragraph(runs))
    assert len(runs) > 18 * MIB
    parsed = read(stack, "docx", data, name="docx, 19 MiB of XML in one part")
    assert (parsed.text or "").startswith("The first line.")
    if parsed.truncated:
        assert [note.code for note in parsed.notes] == ["time_limit"]
    assert parsed.usage is not None
    assert parsed.usage["cpu_ms"] < 40_000


def test_an_export_whose_files_hold_20_mib_is_cut_inside_the_limits(stack: Stack) -> None:
    big = "Name\r\n" + fx.words(1_600_000, 8)  # about 10 MiB each
    data = fx.export({"Profile.csv": big, "Positions.csv": big[:-1] + "!"})
    parsed = read(stack, "linkedin_zip", data, name="export, 20 MiB in two files")
    assert [(one.name, len(one.text), one.truncated) for one in parsed.documents] == [
        ("Profile.csv", pc.MAX_TEXT_CHARS, True),
        ("Positions.csv", pc.MAX_EXPORT_CHARS - pc.MAX_TEXT_CHARS, True),
    ]


HOSTILE = [
    *[("docx", name, make, code) for name, make, code in fx.HOSTILE],
    ("docx", "an entity bomb", fx.entity_bomb_docx, "bomb"),
    ("docx", "an external entity", fx.external_entity_docx, "bomb"),
    ("docx", "a docm", lambda: fx.docx(content_types=fx.MACRO_CONTENT_TYPES), "macros"),
    ("linkedin_zip", "a ratio bomb", fx.ratio_bomb, "bomb"),
    ("linkedin_zip", "201 entries", fx.too_many_entries, "bomb"),
    ("linkedin_zip", "a nested archive", fx.nested_archive, "nested_archive"),
    ("linkedin_zip", "a lying CSV", lambda: fx.lying_entry("Positions.csv"), "bomb"),
    ("linkedin_zip", "a path with ..", fx.path_out, "unreadable"),
    ("linkedin_zip", "neither", lambda: fx.zip_of([("notes.txt", "notes")]), "not_export"),
]


@pytest.mark.parametrize(
    ("kind", "name", "make", "code"), HOSTILE, ids=[f"{case[0]}: {case[1]}" for case in HOSTILE]
)
def test_a_hostile_archive_ends_as_a_code_and_the_service_lives(
    stack: Stack, kind: str, name: str, make: Any, code: str
) -> None:
    assert refusal(stack, kind, make()) == ("file", code)
    stack.alive()


def test_nothing_was_extracted_anywhere_in_the_container(stack: Stack) -> None:
    """After every file above: no file was created or changed in the parser's container (its
    root is read only, and the child may not write a byte: RLIMIT_FSIZE is 0)."""
    assert refusal(stack, "docx", fx.path_out()) == ("file", "unreadable")
    changed = subprocess.run(
        ["docker", "diff", stack.run("ps", "-q", "parser").strip()],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert changed.strip() == ""
    stack.alive()


def test_archives_become_documents_through_the_job_and_the_real_parser(
    stack: Stack, api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    """The upload route, the worker's job, the service, the rows: only the queue is left out."""
    ctx = {"sessions": factory, "clock": clock, "ingestion_parse": stack.parse()}
    word = uploaded(api, alice, fx.cv_docx(), "cv.docx")
    assert asyncio.run(jobs.parse_document(ctx, word["id"])) == jobs.DONE
    stored = row(factory, word["id"])
    assert (stored.text_status, stored.mime, stored.raw) == (
        TextStatus.done,
        documents.DOCX_MIME,
        None,
    )
    assert text_of(api, alice, word["id"]).json()["text"] == stored.text
    assert f"LinkedIn ({fx.LINK})" in (stored.text or "")

    export = uploaded(api, alice, fx.export(), "export.zip")
    assert asyncio.run(jobs.parse_document(ctx, export["id"])) == jobs.DONE
    items = {item["name"]: item for item in listing(api, alice)["items"]}
    assert sorted(items) == [
        "cv.docx",
        "export.zip: Positions.csv",
        "export.zip: Profile.csv",
        "export.zip: Skills.csv",
    ]
    assert {item["status"] for item in items.values()} == {"done"}
    assert export["id"] not in {item["id"] for item in items.values()}

    bomb = uploaded(api, alice, fx.ratio_bomb(), "bomb.zip")
    assert asyncio.run(jobs.parse_document(ctx, bomb["id"])) == jobs.FAILED
    failed = row(factory, bomb["id"])
    assert (failed.text_status, failed.failed_kind) == (TextStatus.failed, FailedKind.file)
    assert failed.failed_reason == jobs.ARCHIVE_SENTENCES["bomb"]
    owner = user_id(factory, ALICE)
    assert len([one for one in rows(factory) if one.user_id == owner]) == 5
