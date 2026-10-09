"""importer.convert on Harald's real ../data (read only): every row, every status count, every
posting header and every file of the folders.

Skipped, with the reason, where the data does not exist (CI, containers). Set
JOBAPP_SOURCE_DIR to the job folder (the one holding data/jobs.csv) to point at it explicitly.
"""

import csv
import hashlib
import os
from collections import Counter
from pathlib import Path

import pytest

from app.jobs.importer import FILE_NAMES, SKIPPED_SILENTLY, convert
from app.jobs.schemas import IMPORT_CLI_MAX_BYTES, IMPORT_HTTP_MAX_BYTES, parse_document


def _find_job_root() -> Path | None:
    explicit = os.environ.get("JOBAPP_SOURCE_DIR")
    candidates = [Path(explicit)] if explicit else list(Path(__file__).resolve().parents)
    for parent in candidates:
        if (parent / "data" / "jobs.csv").is_file() and (parent / "data" / "jobs").is_dir():
            return parent
    return None


ROOT = _find_job_root()
pytestmark = pytest.mark.skipif(
    ROOT is None,
    reason="Harald's data (data/jobs.csv, data/jobs/) is not on this machine; set "
    "JOBAPP_SOURCE_DIR to the job folder to run the real-data importer test",
)


@pytest.fixture(scope="module")
def data() -> Path:
    assert ROOT is not None
    return ROOT / "data"


@pytest.fixture(scope="module")
def rows(data: Path) -> list[dict[str, str]]:
    with (data / "jobs.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_every_row_and_status(data: Path, rows: list[dict[str, str]]) -> None:
    document = convert(data)
    assert len(document.jobs) == len(rows)
    assert len(rows) >= 433
    assert [j.slug for j in document.jobs] == [r["id"] for r in rows]
    assert Counter(j.status.value for j in document.jobs) == Counter(r["status"] for r in rows)
    assert Counter(j.contract_type.value for j in document.jobs) == Counter(r["type"] for r in rows)
    assert document.warnings == []
    for job, values in zip(document.jobs, rows, strict=True):
        assert (job.company, job.title, job.location) == (
            values["company"],
            values["title"],
            values["location"],
        )
        assert job.url == (values["url"] or None)
        assert job.notes == values["notes"]
        assert (job.description or "") == values["description"]
        assert job.fit == (int(values["fit"]) if values["fit"] else None)
        assert ", ".join(job.competencies) == values["competencies"]
        assert ", ".join(job.gaps) == values["gaps"]
        assert (job.found_on.isoformat() if job.found_on else "") == values["found_on"]
        assert (job.applied_on.isoformat() if job.applied_on else "") == values["applied_on"]
        for column in ("jd", "cv", "letter", "report"):
            assert job.legacy.get(column, "") == values[column]


def test_every_posting_header_parsed(data: Path, rows: list[dict[str, str]]) -> None:
    document = convert(data)
    postings = 0
    for job, values in zip(document.jobs, rows, strict=True):
        path = data / "jobs" / values["id"] / "posting.txt"
        if not path.is_file():
            assert job.posting_text is None
            continue
        postings += 1
        header = job.legacy["posting_header"]
        assert {"COMPANY", "TITLE", "LOCATION", "URL"} <= set(header)
        assert header["COMPANY"]
        assert job.posting_text
        assert not job.posting_text.startswith("COMPANY:")
        assert job.posting_text in path.read_text(encoding="utf-8")
        if header.get("PUBLISHED"):
            assert job.published_on is not None
            assert job.published_on.isoformat() == header["PUBLISHED"][:10]
    assert postings == sum(1 for v in rows if (data / "jobs" / v["id"] / "posting.txt").is_file())


def test_every_file_found_byte_for_byte(data: Path, rows: list[dict[str, str]]) -> None:
    document = convert(data)
    expected = 0
    for job, values in zip(document.jobs, rows, strict=True):
        folder = data / "jobs" / values["id"]
        on_disk = {p.name for p in folder.iterdir() if p.is_file()} if folder.is_dir() else set()
        assert on_disk - set(FILE_NAMES) <= SKIPPED_SILENTLY
        wanted = on_disk & set(FILE_NAMES)
        expected += len(wanted)
        assert {f.name for f in job.files} == wanted
        for file in job.files:
            raw = (folder / file.name).read_bytes()
            assert file.content == raw
            assert file.sha256 == hashlib.sha256(raw).hexdigest()
    assert sum(len(j.files) for j in document.jobs) == expected


def test_the_document_round_trips_and_needs_the_cli(data: Path) -> None:
    document = convert(data)
    raw = document.to_json().encode("utf-8")
    # Over Caddy's 10 MiB: Harald's import goes through the CLI, never through HTTP.
    assert IMPORT_HTTP_MAX_BYTES < len(raw) < IMPORT_CLI_MAX_BYTES // 2
    assert parse_document(raw, IMPORT_CLI_MAX_BYTES) == document
