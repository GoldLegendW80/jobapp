"""importer.convert on synthetic data folders: every column, every file type, and the edge cases
(missing posting, empty fields, unknown status and type, unicode, commas, quotes and newlines
inside fields, duplicate ids, a byte order mark), plus the document checks of schemas.py."""

import base64
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.jobs.importer import ConversionError, convert, kind, parse_posting, source_of
from app.jobs.models import ContractType, JobStatus
from app.jobs.schemas import (
    IMPORT_CLI_MAX_BYTES,
    DocumentError,
    ImportFile,
    JobsImportDocument,
    describe_validation_error,
    mime_of,
    parse_document,
)
from tests.jobs.sources import PDF, posting, row, write_data

NOW = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)


def one(data: Path) -> tuple[JobsImportDocument, dict]:
    document = convert(data, NOW)
    assert len(document.jobs) == 1
    return document, document.jobs[0].model_dump()


def test_every_column_is_mapped(tmp_path: Path) -> None:
    values = row(
        fit="88",
        competencies="agents, llm_apps, python",
        gaps="rag, 4+ years",
        description="Short summary",
        jd="data/jobs/acme-ai-engineer/posting.txt",
        cv="data/jobs/acme-ai-engineer/cv.pdf",
        letter="data/jobs/acme-ai-engineer/letter.pdf",
        report="data/reports/2026-10-05-sweep.html",
        notes="Strong fit; ask about remote",
        status="applied",
        applied_on="2026-10-06",
    )
    files = {
        "acme-ai-engineer": {
            "posting.txt": posting(SOURCE="indeed https://example.org/x"),
            "cv.pdf": PDF,
            "letter.pdf": PDF + b"letter",
            "cv.json": '{"target": "x"}',
            "letter.json": "{}",
            "answers.json": '{"why": "because"}',
            "review.json": "[]",
            "apply.py": "print('submit')\n",
            "cv.html": "<html></html>",
            "letter.html": "<html></html>",
        }
    }
    document, job = one(write_data(tmp_path, [values], files))
    assert document.warnings == []
    assert document.exported_at == NOW
    assert job["slug"] == "acme-ai-engineer"
    assert (job["company"], job["title"], job["location"]) == ("Acme", "AI Engineer", "Paris")
    assert job["contract_type"] is ContractType.CDI
    assert job["url"] == "https://jobs.ashbyhq.com/acme/1"
    assert job["source"] == "indeed"
    assert job["status"] is JobStatus.applied
    assert job["fit"] == 88
    assert job["competencies"] == ["agents", "llm_apps", "python"]
    assert job["gaps"] == ["rag", "4+ years"]
    assert job["description"] == "Short summary"
    assert job["notes"] == "Strong fit; ask about remote"
    assert (job["found_on"], job["applied_on"]) == (date(2026, 10, 7), date(2026, 10, 6))
    assert job["published_on"] == date(2026, 10, 1)
    assert job["posting_text"] == "About the role\n\nBuild agents."
    assert job["legacy"] == {
        "jd": "data/jobs/acme-ai-engineer/posting.txt",
        "cv": "data/jobs/acme-ai-engineer/cv.pdf",
        "letter": "data/jobs/acme-ai-engineer/letter.pdf",
        "report": "data/reports/2026-10-05-sweep.html",
        "posting_header": {
            "COMPANY": "Acme",
            "TITLE": "AI Engineer",
            "LOCATION": "Paris",
            "URL": "https://jobs.ashbyhq.com/acme/1",
            "PUBLISHED": "2026-10-01",
            "SOURCE": "indeed https://example.org/x",
        },
    }
    by_name = {f["name"]: f for f in job["files"]}
    assert sorted(by_name) == [
        "answers.json",
        "apply.py",
        "cv.json",
        "cv.pdf",
        "letter.json",
        "letter.pdf",
        "review.json",
    ]  # html renders skipped
    assert by_name["cv.pdf"]["mime"] == "application/pdf"
    assert by_name["cv.json"]["mime"] == "application/json"
    assert by_name["apply.py"]["mime"] == "text/x-python"
    assert base64.b64decode(by_name["cv.pdf"]["base64"]) == PDF
    assert by_name["cv.pdf"]["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert by_name["cv.pdf"]["size"] == len(PDF)


def test_missing_folder_and_posting(tmp_path: Path) -> None:
    data = write_data(
        tmp_path,
        [row(), row(id="b-x", url="https://e.org/2", jd="data/jobs/b-x/posting.txt")],
        {"b-x": {"cv.json": "{}"}},
    )
    document = convert(data, NOW)
    first, second = document.jobs
    assert (first.posting_text, first.published_on, first.files) == (None, None, [])
    assert "posting_header" not in first.legacy
    assert second.posting_text is None
    assert [f.name for f in second.files] == ["cv.json"]
    assert document.warnings == ["b-x: the jd column names a missing file"]


def test_empty_fields(tmp_path: Path) -> None:
    values = {column: "" for column in row()}
    values.update(id="x-y", company="X", title="Y")
    _, job = one(write_data(tmp_path, [values]))
    assert job["url"] is None
    assert job["status"] is JobStatus.found
    assert job["contract_type"] is ContractType.CDI
    assert (job["fit"], job["found_on"], job["applied_on"]) == (None, None, None)
    assert (job["competencies"], job["gaps"], job["description"], job["notes"]) == (
        [],
        [],
        None,
        "",
    )
    assert job["legacy"] == {}
    assert job["source"] == "import"


@pytest.mark.parametrize(
    ("raw", "stored", "warned"),
    [
        ("shortlisted", JobStatus.shortlisted, False),
        ("Shortlisted", JobStatus.shortlisted, True),
        ("CV ready", JobStatus.cv_ready, True),
        ("cv-ready", JobStatus.cv_ready, True),
        ("intervew", JobStatus.interview, True),
        ("ghosted", JobStatus.found, True),
        ("", JobStatus.found, False),
    ],
)
def test_status_values(tmp_path: Path, raw: str, stored: JobStatus, warned: bool) -> None:
    document, job = one(write_data(tmp_path, [row(status=raw)]))
    assert job["status"] is stored
    if warned:
        assert job["legacy"]["status"] == raw
        assert document.warnings == [f"acme-ai-engineer: unknown status, stored as {stored.value}"]
    else:
        assert "status" not in job["legacy"]
        assert document.warnings == []


@pytest.mark.parametrize(
    ("raw", "title", "stored", "warned"),
    [
        ("VIE", "AI Engineer", ContractType.VIE, False),
        ("vie", "AI Engineer", ContractType.VIE, False),
        ("", "AI Engineer Internship", ContractType.Stage, False),
        ("", "Ingénieur IA (VIE)", ContractType.VIE, False),
        ("", "Développeur en alternance", ContractType.Alternance, False),
        ("permanent", "AI Engineer", ContractType.CDI, True),
        ("permanent", "Freelance AI Engineer", ContractType.Freelance, True),
    ],
)
def test_type_values(
    tmp_path: Path, raw: str, title: str, stored: ContractType, warned: bool
) -> None:
    document, job = one(write_data(tmp_path, [row(type=raw, title=title)]))
    assert job["contract_type"] is stored
    assert ("type" in job["legacy"]) is warned
    assert bool(document.warnings) is warned


def test_kind_is_the_engines_rule() -> None:
    assert kind("AI Engineer") is ContractType.CDI
    assert kind("Engineer", "Paris", "https://mon-vie-via.businessfrance.fr/x") is ContractType.VIE
    assert kind("V.I.E Data") is ContractType.VIE
    assert kind("Apprentice engineer") is ContractType.Alternance
    assert kind("Stagiaire IA") is ContractType.Stage
    assert kind("Fixed-term AI engineer") is ContractType.CDD
    assert kind("Temporary analyst") is ContractType.CDD
    assert kind("AI contractor") is ContractType.Freelance
    # The first rule wins: a VIE internship is a VIE.
    assert kind("VIE internship") is ContractType.VIE
    # Whole words only: "vielle" or "international" are not VIE or intern.
    assert kind("Vielle international stagecoach") is ContractType.CDI


def test_bad_dates_and_fit_go_to_legacy(tmp_path: Path) -> None:
    values = row(found_on="07/10/2026", applied_on="soon", fit="ninety")
    files = {"acme-ai-engineer": {"posting.txt": posting(PUBLISHED="last week")}}
    document, job = one(write_data(tmp_path, [values], files))
    assert (job["found_on"], job["applied_on"], job["fit"], job["published_on"]) == (
        None,
        None,
        None,
        None,
    )
    assert job["legacy"]["found_on"] == "07/10/2026"
    assert job["legacy"]["applied_on"] == "soon"
    assert job["legacy"]["fit"] == "ninety"
    assert job["legacy"]["posting_header"]["PUBLISHED"] == "last week"
    assert document.warnings == [
        "acme-ai-engineer: found_on is not a date, left empty",
        "acme-ai-engineer: applied_on is not a date, left empty",
        "acme-ai-engineer: fit is not an integer from 0 to 100, left empty",
        "acme-ai-engineer: PUBLISHED of posting.txt is not a date, left empty",
    ]


@pytest.mark.parametrize(
    ("raw", "fit"),
    [("0", 0), ("100", 100), ("85.0", 85), (" 7 ", 7), ("101", None), ("-1", None), ("8.5", None)],
)
def test_fit_values(tmp_path: Path, raw: str, fit: int | None) -> None:
    _, job = one(write_data(tmp_path, [row(fit=raw)]))
    assert job["fit"] == fit


def test_datetime_keeps_its_date(tmp_path: Path) -> None:
    _, job = one(write_data(tmp_path, [row(found_on="2026-10-07T09:30:00+02:00")]))
    assert job["found_on"] == date(2026, 10, 7)


def test_unicode_commas_quotes_and_newlines(tmp_path: Path) -> None:
    values = row(
        id="societe-generale-ingenieur-ia",
        company="Société Générale",
        title='Ingénieur IA "GenAI", équipe données',
        location="Paris, Île-de-France",
        notes='Line one, with a comma\nLine two "quoted"\r\nLine three 日本語 🚀',
        competencies="agents,  python ,,rag",
    )
    _, job = one(write_data(tmp_path, [values]))
    assert job["company"] == "Société Générale"
    assert job["title"] == 'Ingénieur IA "GenAI", équipe données'
    assert job["location"] == "Paris, Île-de-France"
    assert job["notes"] == 'Line one, with a comma\nLine two "quoted"\r\nLine three 日本語 🚀'
    assert job["competencies"] == ["agents", "python", "rag"]


def test_byte_order_mark_is_ignored(tmp_path: Path) -> None:
    document = convert(write_data(tmp_path, [row()], bom=True), NOW)
    assert document.jobs[0].slug == "acme-ai-engineer"


def test_duplicate_and_invalid_ids(tmp_path: Path) -> None:
    rows = [
        row(),
        row(title="AI Engineer II", url="https://e.org/2"),  # same id
        row(id="Acme AI/Engineer", url="https://e.org/3"),  # not a slug
        row(id="", company="Zeta", title="ML Ops", url="https://e.org/4"),  # no id
    ]
    document = convert(write_data(tmp_path, rows), NOW)
    assert [j.slug for j in document.jobs] == [
        "acme-ai-engineer",
        "acme-ai-engineer-2",
        "acme-ai-engineer-3",
        "zeta-ml-ops",
    ]
    assert document.jobs[1].legacy["id"] == "acme-ai-engineer"
    assert document.jobs[2].legacy["id"] == "Acme AI/Engineer"
    assert document.warnings == [
        "row 3: id already used by an earlier row, stored as acme-ai-engineer-2",
        "row 4: id is not a valid slug, stored as acme-ai-engineer",
        "row 4: id already used by an earlier row, stored as acme-ai-engineer-3",
        "row 5: no id, slug zeta-ml-ops built from company and title",
    ]


def test_duplicate_url_is_kept_in_legacy(tmp_path: Path) -> None:
    rows = [row(), row(id="other", url="http://www.jobs.ashbyhq.com/acme/1/?utm_source=x")]
    document = convert(write_data(tmp_path, rows), NOW)
    assert document.jobs[1].url is None
    assert document.jobs[1].legacy["url"].startswith("http://www.")
    assert document.warnings == ["other: url already used by an earlier row, kept in legacy only"]


def test_unknown_files_orphans_and_bad_pdfs(tmp_path: Path) -> None:
    files = {
        "acme-ai-engineer": {"cv.pdf": b"not a pdf", "notes.docx": b"x", ".DS_Store": b"x"},
        "orphan-folder": {"cv.json": "{}"},
    }
    document = convert(write_data(tmp_path, [row()], files), NOW)
    assert document.jobs[0].files == []
    assert document.warnings == [
        "acme-ai-engineer: cv.pdf is not a PDF, skipped",
        "acme-ai-engineer: notes.docx is not a known job file, skipped",
        "jobs/orphan-folder: folder with no CSV row, skipped",
    ]


def test_path_like_ids_never_leave_the_folder(tmp_path: Path) -> None:
    secret = tmp_path / "data" / "cv.json"
    data = write_data(tmp_path, [row(id="..", url="https://e.org/1")])
    secret.write_text("{}")
    document = convert(data, NOW)
    assert document.jobs[0].files == []
    assert document.jobs[0].legacy["id"] == ".."


def test_extra_columns_and_cells_are_kept(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "jobs.csv").write_text(
        "id,company,title,salary\nacme-x,Acme,X,60k,surplus\nacme-y,Acme,Y\n", encoding="utf-8"
    )
    document = convert(data, NOW)
    assert document.jobs[0].legacy == {"salary": "60k", "_extra_cells": "surplus"}
    assert document.jobs[1].legacy == {}


def test_posting_not_utf8_and_without_header(tmp_path: Path) -> None:
    files = {"acme-ai-engineer": {"posting.txt": "Équipe IA\nNo header here.".encode("latin-1")}}
    document, job = one(write_data(tmp_path, [row()], files))
    assert job["posting_text"] == "Équipe IA\nNo header here."
    assert job["legacy"]["posting_header"] == {}
    assert document.warnings == ["acme-ai-engineer: posting.txt is not UTF-8, read as Latin-1"]


def test_parse_posting() -> None:
    parsed = parse_posting("COMPANY: A\r\nTITLE: B\r\nPUBLISHED:\r\n\r\nText\r\nmore\r\n")
    assert parsed.header == {"COMPANY": "A", "TITLE": "B", "PUBLISHED": ""}
    assert parsed.text == "Text\nmore"
    assert parse_posting("COMPANY: A\nText right after").text == "Text right after"
    assert parse_posting("").text == ""


def test_source_of() -> None:
    assert source_of("https://jobs.ashbyhq.com/mistral/abc", None) == "ashby:mistral"
    assert source_of("https://job-boards.greenhouse.io/anthropic/jobs/1", None) == (
        "greenhouse:anthropic"
    )
    assert source_of("https://jobs.lever.co/acme/1", None) == "lever:acme"
    assert source_of("https://jobs.ashbyhq.com/", None) == "import"
    assert source_of("https://example.org/x", "  ") == "import"
    assert source_of(None, "LinkedIn https://x") == "linkedin"


def test_missing_or_bad_csv(tmp_path: Path) -> None:
    with pytest.raises(ConversionError, match=r"cannot read jobs\.csv"):
        convert(tmp_path, NOW)
    (tmp_path / "jobs.csv").write_text("company,title\nA,B\n")
    with pytest.raises(ConversionError, match="lacks the column"):
        convert(tmp_path, NOW)
    (tmp_path / "jobs.csv").write_bytes(b"id,company,title\n\xff\xfe,x,y\n")
    with pytest.raises(ConversionError, match="not UTF-8"):
        convert(tmp_path, NOW)


def test_empty_csv(tmp_path: Path) -> None:
    (tmp_path / "jobs.csv").write_text("id,company,title\n")
    assert convert(tmp_path, NOW).jobs == []


def test_document_round_trip(tmp_path: Path) -> None:
    files = {"acme-ai-engineer": {"posting.txt": posting(), "cv.pdf": PDF}}
    document = convert(write_data(tmp_path, [row(notes="é")], files), NOW)
    raw = document.to_json().encode("utf-8")
    again = parse_document(raw, IMPORT_CLI_MAX_BYTES)
    assert again == document
    assert again.jobs[0].files[0].content == PDF


# --- the document checks ------------------------------------------------------------------------


def _doc(**job: object) -> dict:
    base = {"slug": "a-b", "company": "A", "title": "B"}
    base.update(job)
    return {"format": "jobapp-jobs", "version": 1, "exported_at": NOW.isoformat(), "jobs": [base]}


def _fails(document: dict, fragment: str) -> None:
    with pytest.raises(ValidationError) as caught:
        parse_document(json.dumps(document).encode(), IMPORT_CLI_MAX_BYTES)
    assert fragment in describe_validation_error(caught.value)


def test_document_refusals() -> None:
    for raw, limit, status, fragment in [
        (b"x" * 11, 10, 413, "larger than 10 bytes"),
        (b"{" * 11, 11, 422, "not valid JSON"),
        (b"{not json", 100, 422, "not valid JSON"),
        (b"[]", 100, 422, "one JSON object"),
        (b'{"format": "jobapp-profile", "version": 1}', 100, 422, "Unknown file format"),
        (b'{"format": "jobapp-jobs", "version": 2}', 100, 422, "Unsupported version"),
        (b'{"format": "jobapp-jobs", "version": "1"}', 100, 422, "Unsupported version"),
        (b'{"format": "jobapp-jobs", "version": true}', 100, 422, "Unsupported version"),
    ]:
        with pytest.raises(DocumentError) as caught:
            parse_document(raw, limit)
        assert caught.value.status == status
        assert fragment in caught.value.message


def test_row_refusals() -> None:
    good = ImportFile.of("cv.pdf", PDF).model_dump()
    _fails(_doc(slug="Not A Slug"), "jobs.0.slug")
    _fails(_doc(status="ghosted"), "jobs.0.status")
    _fails(_doc(contract_type="Permanent"), "jobs.0.contract_type")
    _fails(_doc(fit=101), "jobs.0.fit")
    _fails(_doc(url=""), "jobs.0.url")
    _fails(_doc(extra=1), "jobs.0.extra")
    _fails(_doc(files=[{**good, "sha256": "0" * 64}]), "sha256 does not match")
    _fails(_doc(files=[{**good, "size": 1}]), "size does not match")
    _fails(_doc(files=[{**good, "base64": "!!"}]), "base64 is invalid")
    _fails(_doc(files=[{**good, "name": "../x.pdf"}]), "jobs.0.files.0.name")
    _fails(_doc(files=[{**good, "mime": "text/html"}]), "jobs.0.files.0.mime")
    _fails(_doc(files=[good, good]), "two files have the same name")
    text = ImportFile.of("cv.pdf", b"plain text", "text/plain").model_dump()
    _fails(_doc(files=[{**text, "mime": "application/pdf"}]), "not a PDF")
    document = _doc()
    document["jobs"].append({"slug": "a-b", "company": "C", "title": "D"})
    _fails(document, "slug already used by jobs.0")
    document = _doc(url="https://e.org/1")
    document["jobs"].append(
        {"slug": "c-d", "company": "C", "title": "D", "url": "https://www.e.org/1/"}
    )
    _fails(document, "url already used by jobs.0")


def test_mime_of() -> None:
    assert mime_of("cv.PDF") == "application/pdf"
    assert mime_of("notes.md") == "text/markdown"
    assert mime_of("a.txt") == "text/plain"
    assert mime_of("README") == "application/octet-stream"
    assert mime_of("x.docx") == "application/octet-stream"
