"""D15: the documents the command line made, imported (`legacy.import_legacy`, the command
`python -m app.documents.cli import-legacy`, the legacy PDF served by the api).

One invented person with postings of every shape the plan names: sent with its PDFs, found with
a PDF not attached, a JSON and no PDF, a PDF and no JSON, a JSON that does not validate (found
and sent), an employer key the profile does not have; a second person whose postings stay
untouched; the run twice; the dry run; a document already there; two imports at once; the
render of an imported draft; the automatic generation never writing an imported posting; and
the canary over logs and the command's output.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.config import get_settings
from app.documents import cli, eligibility, freeze, legacy, render_client, render_job, store, tick
from app.documents.models import (
    Author,
    Document,
    DocumentVersion,
    FreezeReason,
    Kind,
    Origin,
)
from app.documents.router import NO_PREVIEW
from app.jobs.models import JobFile
from tests.documents.canary import find, marker
from tests.documents.conftest import load
from tests.documents.render_support import FakeRenderer, context, give_profile, pdf_of
from tests.documents.store_support import db, rows_of
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "db", "engine", "factory", "migrated_url"]

ALEX = "e2e-legacy-alex@example.org"
BLAKE = "e2e-legacy-blake@example.org"
AT = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
API_DIR = Path(__file__).resolve().parents[2]
CV = load("legacy/cv_valid.json")
LETTER = load("legacy/letter_valid.json")
CV_INVALID = load("legacy/cv_invalid.json")
CV_UNMATCHED = load("legacy/cv_unmatched.json")


def encoded(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode()


def a_pdf(tag: str) -> bytes:
    """A real one page PDF, made unique by a comment after its end (still a PDF)."""
    return pdf_of(1) + f"%{tag}\n".encode()


def add_posting(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    status: str,
    *,
    applied_on: date | None = None,
    fit: int | None = 95,
) -> uuid.UUID:
    with factory() as db:
        found = db.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title, posting_text, status, "
                "applied_on, fit, found_on) VALUES (:u, :s, 'Lumen Example', 'Platform engineer', "
                "'Lumen Example is hiring a platform engineer for the team.', "
                "CAST(:st AS job_status), :ap, :fit, :fo) RETURNING id"
            ),
            {
                "u": user,
                "s": f"lumen-{uuid.uuid4().hex[:8]}",
                "st": status,
                "ap": applied_on,
                "fit": fit,
                "fo": AT.date(),
            },
        ).scalar_one()
        db.commit()
    return found


def add_file(
    factory: sessionmaker[Session], job: uuid.UUID, name: str, data: bytes, origin: str = "legacy"
) -> uuid.UUID:
    mime = "application/pdf" if name.endswith(".pdf") else "application/json"
    with factory() as db:
        found = db.execute(
            sa.text(
                "INSERT INTO job_files (job_id, name, mime, bytes, size, sha256, origin) VALUES "
                "(:j, :n, :m, :b, :z, :h, CAST(:o AS file_origin)) RETURNING id"
            ),
            {
                "j": job,
                "n": name,
                "m": mime,
                "b": data,
                "z": len(data),
                "h": hashlib.sha256(data).digest(),
                "o": origin,
            },
        ).scalar_one()
        db.commit()
    return found


def person(factory: sessionmaker[Session], email: str) -> uuid.UUID:
    user = make_user(factory, email=email, name=email.split("@")[0])
    give_profile(factory, user.id, AT)
    return user.id


def run_import(factory: sessionmaker[Session], user: uuid.UUID) -> legacy.Summary:
    with factory() as db:
        summary = legacy.import_legacy(db, user, AT)
        db.commit()
    return summary


def documents_of(factory: sessionmaker[Session], posting: uuid.UUID) -> dict[Kind, Document]:
    with factory() as db:
        rows = db.execute(sa.select(Document).where(Document.posting_id == posting)).scalars()
        return {d.kind: d for d in rows}


def versions_of(factory: sessionmaker[Session], document: uuid.UUID) -> list[DocumentVersion]:
    with factory() as db:
        query = (
            sa.select(DocumentVersion)
            .where(DocumentVersion.document_id == document)
            .options(undefer(DocumentVersion.content))
        )
        return list(db.execute(query.order_by(DocumentVersion.number)).scalars())


def file_state(factory: sessionmaker[Session]) -> list[tuple[Any, ...]]:
    with factory() as db:
        rows = db.execute(
            sa.text("SELECT id, name, bytes, sha256 FROM job_files ORDER BY id")
        ).all()
    return [tuple(row) for row in rows]


def table_state(factory: sessionmaker[Session]) -> list[tuple[Any, ...]]:
    """Every column of every document and version row, for "nothing changed"."""
    with factory() as db:
        documents = db.execute(sa.text("SELECT * FROM documents ORDER BY id")).all()
        versions = db.execute(sa.text("SELECT * FROM document_versions ORDER BY id")).all()
    return [tuple(row) for row in [*documents, *versions]]


class World:
    """Alex's postings of every shape, Blake's one posting, their legacy files."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory
        self.alex = person(factory, ALEX)
        self.blake = person(factory, BLAKE)
        self.pdfs: dict[str, bytes] = {}
        self.files: dict[str, uuid.UUID] = {}
        # Sent, with both JSONs and both PDFs.
        self.applied = self.posting(
            "applied", {"cv.json": CV, "letter.json": LETTER}, "cv", "letter"
        )
        # Found: a CV PDF that is not attached, a letter with no PDF.
        self.found = self.posting("found", {"cv.json": CV, "letter.json": LETTER}, "cv")
        # PDFs without their JSON: nothing imported.
        self.pdf_only = self.posting("found", {}, "cv", "letter")
        # A JSON that does not validate, found and sent.
        self.invalid = self.posting("shortlisted", {"cv.json": CV_INVALID})
        self.invalid_sent = self.posting("applied", {"cv.json": CV_INVALID}, "cv")
        # Sent before an interview: an applied_on date; an employer key not in the profile.
        self.interview = self.posting(
            "interview", {"cv.json": CV_UNMATCHED}, "cv", applied_on=date(2026, 9, 1)
        )
        # Blake's: never touched by Alex's import.
        self.blakes = self.posting("applied", {"cv.json": CV}, "cv", user=self.blake)

    def posting(
        self,
        status: str,
        jsons: dict[str, Any],
        *pdfs: str,
        applied_on: date | None = None,
        user: uuid.UUID | None = None,
    ) -> uuid.UUID:
        posting = add_posting(self.factory, user or self.alex, status, applied_on=applied_on)
        for name, data in jsons.items():
            self.files[f"{posting}/{name}"] = add_file(self.factory, posting, name, encoded(data))
        for kind in pdfs:
            data = a_pdf(f"{posting}-{kind}")
            self.pdfs[f"{posting}/{kind}.pdf"] = data
            self.files[f"{posting}/{kind}.pdf"] = add_file(
                self.factory, posting, f"{kind}.pdf", data
            )
        add_file(self.factory, posting, "answers.json", b"{}")  # another legacy file, ignored
        return posting


@pytest.fixture
def world(factory: sessionmaker[Session]) -> World:
    return World(factory)


EXPECTED = legacy.Summary(
    postings=6,
    cvs=5,
    letters=2,
    versions=6,
    frozen=4,
    invalid=2,
    json_without_pdf=2,
    pdf_without_json=2,
    existing=0,
    roles=5,
    unknown_employers=1,
)


# --- the import ---------------------------------------------------------------------------------


def test_every_shape_of_posting_is_imported_as_the_plan_says(world: World) -> None:
    files_before = file_state(world.factory)
    assert run_import(world.factory, world.alex) == EXPECTED
    assert file_state(world.factory) == files_before  # the legacy files are never touched

    # Sent: version 1 points at each legacy PDF, frozen with legacy_applied.
    sent = documents_of(world.factory, world.applied)
    assert set(sent) == {Kind.cv, Kind.letter}
    for kind, document in sent.items():
        assert document.template_slug == store.CLASSIC
        assert document.draft_written_by is Author.import_
        assert document.base_document_id is None
        assert document.language.value == "en"
        (version,) = versions_of(world.factory, document.id)
        assert (version.number, version.origin) == (1, Origin.legacy)
        assert version.frozen_reason is FreezeReason.legacy_applied
        assert version.frozen_at == AT
        assert version.legacy_file_id == world.files[f"{world.applied}/{kind.value}.pdf"]
        pdf = world.pdfs[f"{world.applied}/{kind.value}.pdf"]
        assert version.pdf_sha256 == hashlib.sha256(pdf).digest()
        with world.factory() as db:
            assert freeze.pdf(db, world.alex, version.id) == pdf
        assert version.content == document.draft

    # Found: version 1 without a PDF, not frozen, the CV PDF left in the posting's files.
    found = documents_of(world.factory, world.found)
    for document in found.values():
        (version,) = versions_of(world.factory, document.id)
        assert version.origin is Origin.legacy
        assert (version.frozen_at, version.legacy_file_id, version.pdf_sha256) == (None,) * 3
    assert found[Kind.cv].draft["notes"].startswith("Role in the imported CV for example-corp: ")
    assert all("role" not in entry for entry in found[Kind.cv].draft["experience"])
    assert found[Kind.cv].checks == []

    # PDFs without their JSON: nothing.
    assert documents_of(world.factory, world.pdf_only) == {}

    # Invalid and found: a draft noted legacy_invalid, no version.
    (draft,) = documents_of(world.factory, world.invalid).values()
    assert versions_of(world.factory, draft.id) == []
    assert draft.checks_revision == draft.draft_revision == 1
    assert {n["code"] for n in draft.checks} == {legacy.LEGACY_INVALID}
    assert [n["legacy_path"] for n in draft.checks][:2] == ["experience.0.role", "photo"]
    assert draft.draft["highlights"] == ["First highlight", "Second highlight", "Third highlight"]

    # Invalid and sent: the PDF is kept frozen all the same, with the content that validated.
    (sent_invalid,) = documents_of(world.factory, world.invalid_sent).values()
    (version,) = versions_of(world.factory, sent_invalid.id)
    assert version.frozen_reason is FreezeReason.legacy_applied
    assert version.content == sent_invalid.draft
    assert sent_invalid.checks

    # An interview with an applied_on date was sent: frozen; the unknown key kept and noted.
    (interview,) = documents_of(world.factory, world.interview).values()
    (version,) = versions_of(world.factory, interview.id)
    assert version.frozen_reason is FreezeReason.legacy_applied
    assert [e["employer_key"] for e in interview.draft["experience"]] == [
        "example-corp",
        "harbor-sample-works",
    ]
    assert [(n["field"], n["code"]) for n in interview.checks] == [
        ("experience.1.employer_key", "unknown_employer")
    ]

    # Blake's posting is untouched.
    assert documents_of(world.factory, world.blakes) == {}
    with world.factory() as db:
        assert set(rows_of(db, world.blake).values()) == {0}


def test_the_run_twice_changes_nothing(world: World) -> None:
    run_import(world.factory, world.alex)
    before = table_state(world.factory)
    second = run_import(world.factory, world.alex)
    assert table_state(world.factory) == before
    assert second == legacy.Summary(postings=6, existing=7, pdf_without_json=2)


def test_the_dry_run_counts_what_a_run_does_and_writes_nothing(world: World) -> None:
    with world.factory() as db:
        dry = legacy.import_legacy(db, world.alex, AT)
        db.rollback()
    with world.factory() as db:
        assert set(rows_of(db, world.alex).values()) == {0}
    assert dry == EXPECTED
    assert run_import(world.factory, world.alex) == dry


def test_b_imports_only_his_own_and_a_is_untouched(world: World) -> None:
    run_import(world.factory, world.alex)
    before = table_state(world.factory)
    summary = run_import(world.factory, world.blake)
    assert summary == legacy.Summary(postings=1, cvs=1, versions=1, frozen=1, roles=2)
    after = table_state(world.factory)
    assert all(row in after for row in before)
    assert len(after) == len(before) + 2  # Blake's document and its version


def test_a_document_already_there_is_left_as_it_is(world: World) -> None:
    with world.factory() as db:
        written = store.get_or_create(db, world.alex, world.found, Kind.cv, at=AT)
        written_id, draft = written.id, dict(written.draft)
        db.commit()
    summary = run_import(world.factory, world.alex)
    assert summary.existing == 1
    assert summary.cvs == EXPECTED.cvs - 1
    document = documents_of(world.factory, world.found)[Kind.cv]
    assert (document.id, document.draft, document.draft_written_by) == (
        written_id,
        draft,
        Author.writer,
    )
    assert versions_of(world.factory, written_id) == []


def test_a_json_file_of_the_person_not_from_the_command_line_is_not_imported(
    factory: sessionmaker[Session],
) -> None:
    alex = person(factory, ALEX)
    uploaded = add_posting(factory, alex, "found")
    add_file(factory, uploaded, "cv.json", encoded(CV), origin="upload")
    assert run_import(factory, alex) == legacy.Summary()
    assert documents_of(factory, uploaded) == {}


def test_two_imports_at_once_make_each_document_once(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "LOCK_TIMEOUT_MS", 30)
    with world.factory() as first, world.factory() as second:
        legacy.import_legacy(first, world.alex, AT)
        with pytest.raises(store.DocumentBusyError):
            legacy.import_legacy(second, world.alex, AT)
        second.rollback()
        first.commit()
        assert legacy.import_legacy(second, world.alex, AT).existing == 7
        second.commit()
    with world.factory() as db:
        assert rows_of(db, world.alex)["documents"] == 7


def test_a_status_change_waits_for_the_import(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The import holds the posting row: a move to `applied` meanwhile is busy. Once the import
    committed, the move goes through, and D14's hook finds no PDF to freeze on a posting that
    was not sent (its version 1 has none until a render)."""
    from app.jobs import service as jobs_service
    from app.jobs.models import JobStatus

    monkeypatch.setattr(jobs_service, "LOCK_TIMEOUT_MS", 30)
    with world.factory() as importing, world.factory() as moving:
        legacy.import_legacy(importing, world.alex, AT)
        with pytest.raises(jobs_service.JobBusyError):
            jobs_service.lock_job(moving, world.alex, world.found)
        moving.rollback()
        importing.commit()
        job = jobs_service.lock_job(moving, world.alex, world.found)
        assert job is not None
        assert jobs_service.set_status(moving, job, JobStatus.applied, AT)
        moving.commit()
    cv = documents_of(world.factory, world.found)[Kind.cv]
    assert [v.frozen_at for v in versions_of(world.factory, cv.id)] == [None]


# --- after the import ---------------------------------------------------------------------------


def test_a_found_postings_draft_renders_into_its_version_1(world: World) -> None:
    run_import(world.factory, world.alex)
    cv = documents_of(world.factory, world.found)[Kind.cv]
    with world.factory() as db:
        render_job.mark_queued(db, world.alex, cv.id, bounded=False)
        db.commit()
    renderer = FakeRenderer()
    asyncio.run(render_job.render_document(context(world.factory, renderer, AT), str(cv.id), 1))
    assert len(renderer.calls) == 1
    (version,) = versions_of(world.factory, cv.id)
    assert (version.number, version.origin, version.frozen_at) == (1, Origin.legacy, None)
    assert version.pdf_sha256 is not None


def test_a_sent_postings_draft_is_never_rendered_again(world: World) -> None:
    run_import(world.factory, world.alex)
    cv = documents_of(world.factory, world.applied)[Kind.cv]
    pdf = world.pdfs[f"{world.applied}/cv.pdf"]
    with world.factory() as db:
        render_job.mark_queued(db, world.alex, cv.id, bounded=False)
        db.commit()
    renderer = FakeRenderer()
    asyncio.run(render_job.render_document(context(world.factory, renderer, AT), str(cv.id), 1))
    assert renderer.calls == []
    (version,) = versions_of(world.factory, cv.id)
    with world.factory() as db:
        assert freeze.pdf(db, world.alex, version.id) == pdf


def test_imported_postings_are_never_written_automatically(world: World) -> None:
    run_import(world.factory, world.alex)
    fresh = add_posting(world.factory, world.alex, "found")
    setting = eligibility.Setting(auto=True, min_fit=70, daily_ceiling=100)
    with world.factory() as db:
        eligible = tick.eligible(db, world.alex, setting, None, AT.date(), 30)
    # Every imported posting (and the one with PDFs only) is refused; a new one is not.
    assert [candidate.id for candidate in eligible] == [fresh]


def test_the_apply_module_reads_the_imported_versions(world: World) -> None:
    run_import(world.factory, world.alex)
    with world.factory() as db:
        sent = freeze.current(db, world.alex, world.applied)
        unsent = freeze.current(db, world.alex, world.found)
    assert sent.cv.version is not None
    assert sent.cv.version.frozen_reason is FreezeReason.legacy_applied
    assert sent.letter.version is not None
    assert (unsent.cv.version, unsent.letter.version) == (None, None)  # nothing rendered yet


# --- the api ------------------------------------------------------------------------------------


def test_the_api_serves_the_legacy_pdf_byte_for_byte_to_its_owner_only(
    world: World, api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(pdf: bytes, width: int) -> bytes:
        raise AssertionError("the command line's file reached PDFium in the api")

    monkeypatch.setattr(render_client, "page1_png", never)
    run_import(world.factory, world.alex)
    alex, blake = api.login_token(ALEX), api.login_token(BLAKE)
    cv = documents_of(world.factory, world.applied)[Kind.cv]
    pdf = world.pdfs[f"{world.applied}/cv.pdf"]

    listed = api.get(f"/api/documents/{cv.id}/versions", token=alex)
    assert listed.status_code == 200
    (summary,) = listed.json()
    assert (
        summary["origin"],
        summary["frozen_reason"],
        summary["has_pdf"],
        summary["has_preview"],
    ) == ("legacy", "legacy_applied", True, False)
    served = api.get(f"/api/documents/{cv.id}/versions/1/pdf", token=alex)
    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"
    assert served.content == pdf
    page = api.get(f"/api/documents/{cv.id}/versions/1/page1.png", token=alex)
    assert (page.status_code, page.json()) == (404, {"detail": NO_PREVIEW})

    posting = api.get(f"/api/jobs/{world.applied}/documents", token=alex).json()
    assert [d["sent"] for d in posting["documents"]] == [[1], [1]]

    for path in ("", "/versions", "/versions/1/pdf", "/versions/1/page1.png"):
        assert api.get(f"/api/documents/{cv.id}{path}", token=blake).status_code == 404

    found = documents_of(world.factory, world.found)[Kind.cv]
    unrendered = api.get(f"/api/documents/{found.id}/versions/1/pdf", token=alex)
    assert unrendered.status_code == 404
    listed = api.get(f"/api/documents/{found.id}/versions", token=alex).json()
    assert [v["has_pdf"] for v in listed] == [False]


# --- the command --------------------------------------------------------------------------------


def run_cli(url: str | None, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONIOENCODING"] = "utf-8"
    if url is not None:
        env["DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "app.documents.cli", *args],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(
    ("dry_run", "event"),
    [(True, "documents.legacy.dry_run"), (False, "documents.legacy.imported")],
)
def test_the_command_logs_what_it_did_once_its_transaction_ended(
    world: World,
    migrated_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    dry_run: bool,
    event: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated_url)
    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.INFO, logger="app.documents"):
            assert cli.run_import_legacy(ALEX, dry_run) == 0
    finally:
        get_settings.cache_clear()
    records = [r for r in caplog.records if r.getMessage().startswith("documents.legacy.")]
    assert [r.getMessage() for r in records] == [event]
    assert (records[0].cvs, records[0].letters) == (5, 2)
    with world.factory() as db:
        written = set(rows_of(db, world.alex).values()) != {0}
    assert written is not dry_run


def test_a_failed_command_logs_no_import(
    world: World,
    migrated_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def busy(*args: Any, **kwargs: Any) -> legacy.Summary:
        raise store.DocumentBusyError(store.BUSY)

    monkeypatch.setenv("DATABASE_URL", migrated_url)
    monkeypatch.setattr(legacy, "import_legacy", busy)
    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.INFO, logger="app.documents"):
            assert cli.run_import_legacy(ALEX, False) == 1
    finally:
        get_settings.cache_clear()
    assert not [r for r in caplog.records if r.getMessage().startswith("documents.legacy.")]


def test_the_command_dry_run_then_import_then_nothing(world: World, migrated_url: str) -> None:
    dry = run_cli(migrated_url, "import-legacy", ALEX, "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert dry.stdout.startswith(
        "Dry run, nothing was written. Would import 5 CVs and 2 letters from 6 postings"
    )
    with world.factory() as db:
        assert set(rows_of(db, world.alex).values()) == {0}

    done = run_cli(migrated_url, "import-legacy", f"  {ALEX.upper()} ")
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "Imported 5 CVs and 2 letters from 6 postings with legacy files.\n"
        "Versions: 6, of which 4 frozen as sent with their PDF.\n"
        "Drafts with fields left out (legacy_invalid): 2.\n"
        "JSON without its PDF: 2. PDF without its JSON, not imported: 2.\n"
        "Already there, left as they are: 0.\n"
        "Roles moved to notes: 5. Employer keys not in the profile: 1.\n"
    )
    assert dry.stdout.split("\n", 1)[1] == done.stdout.split("\n", 1)[1]

    again = run_cli(migrated_url, "import-legacy", ALEX)
    assert again.returncode == 0, again.stderr
    assert "Imported 0 CVs and 0 letters from 6 postings" in again.stdout
    assert "Already there, left as they are: 7." in again.stdout


def test_the_command_refusals(factory: sessionmaker[Session], migrated_url: str) -> None:
    bad = run_cli(migrated_url, "import-legacy", "not an email")
    assert (bad.returncode, bad.stderr) == (2, "Invalid email address. Nothing was imported.\n")
    nobody = run_cli(migrated_url, "import-legacy", "e2e-nobody@example.org")
    assert nobody.returncode == 1
    assert nobody.stderr == "No account with that email. Nothing was imported.\n"
    assert run_cli(None, "import-legacy").returncode == 2  # the email is required
    assert run_cli(None, "frobnicate", ALEX).returncode == 2


def test_the_canary_never_leaves_the_files(
    factory: sessionmaker[Session], migrated_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    alex = person(factory, ALEX)
    planted = [marker(), marker(), marker(), marker()]
    cv = {
        **CV,
        "headline": planted[0],
        "photo": planted[1],
        "experience": [{"employer_key": planted[2], "role": planted[3], "bullets": ["b"]}],
    }
    sent = add_posting(factory, alex, "applied")
    add_file(factory, sent, "cv.json", encoded(cv))
    add_file(factory, sent, "cv.pdf", a_pdf(planted[0]))
    unsent = add_posting(factory, alex, "found")
    add_file(factory, unsent, "letter.json", encoded({"paragraphs": [planted[0]], "x": planted[1]}))
    caplog.set_level(logging.DEBUG)
    with factory() as db:
        summary = legacy.import_legacy(db, alex, AT)
        db.rollback()
    assert summary.cvs == summary.letters == 1
    output = run_cli(migrated_url, "import-legacy", ALEX)
    assert output.returncode == 0, output.stderr
    assert find(planted, logs=caplog.records, values=[output.stdout, output.stderr]) == []
    assert ALEX not in output.stdout + output.stderr


def test_the_jobs_import_files_are_what_the_import_reads(world: World) -> None:
    """The names the jobs importer attaches are the names read here."""
    from app.jobs.importer import FILE_NAMES

    assert {name for pair in legacy.FILES.values() for name in pair} <= set(FILE_NAMES)
    with world.factory() as db:
        names = set(db.execute(sa.select(JobFile.name)).scalars())
    assert {"cv.json", "letter.json", "cv.pdf", "letter.pdf"} <= names
