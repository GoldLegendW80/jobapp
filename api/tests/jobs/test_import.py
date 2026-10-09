"""service.import_jobs against a real Postgres: create, idempotent second run, the update path
and its event, files deduped, files of sent rows never replaced, matching by slug then by the
duplicate rules, URL conflicts, and isolation between users."""

import hashlib
import uuid
from datetime import date, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.jobs import service
from app.jobs.models import (
    ContractType,
    FileOrigin,
    Job,
    JobEvent,
    JobEventKind,
    JobFile,
    JobStatus,
)
from app.jobs.schemas import ImportFile, ImportJob
from app.jobs.service import NewJob
from tests.jobs.conftest import NOW
from tests.jobs.sources import PDF


def item(slug: str = "acme-ai-engineer", **fields: Any) -> ImportJob:
    base: dict[str, Any] = {
        "slug": slug,
        "company": "Acme",
        "title": "AI Engineer",
        "location": "Paris",
        "url": f"https://jobs.example/{slug}",
        "source": "ashby:acme",
        "status": "found",
        "fit": 80,
        "competencies": ["agents", "python"],
        "gaps": ["rag"],
        "description": "Summary",
        "posting_text": "Build agents.",
        "published_on": "2026-10-01",
        "found_on": "2026-10-07",
        "notes": "",
        "legacy": {"jd": f"data/jobs/{slug}/posting.txt"},
        "files": [],
    }
    base.update(fields)
    return ImportJob.model_validate(base)


def pdf(name: str = "cv.pdf", extra: bytes = b"") -> ImportFile:
    return ImportFile.of(name, PDF + extra)


def run(db: Session, user: uuid.UUID, items: list[ImportJob], later: int = 0) -> Any:
    summary = service.import_jobs(db, user, items, NOW + timedelta(minutes=later))
    db.commit()
    return summary


def jobs(db: Session, user: uuid.UUID) -> list[Job]:
    return list(db.execute(service.jobs_of(user).order_by(Job.slug)).scalars())


def events(db: Session, user: uuid.UUID) -> list[JobEvent]:
    query = sa.select(JobEvent).where(JobEvent.user_id == user).order_by(JobEvent.id)
    return list(db.execute(query).scalars())


def files(db: Session, job: Job) -> dict[str, bytes]:
    query = sa.select(JobFile).where(JobFile.job_id == job.id)
    return {f.name: f.content for f in db.execute(query).scalars()}


def test_create_maps_every_field(db: Session, alice: uuid.UUID) -> None:
    summary = run(db, alice, [item(files=[pdf(), ImportFile.of("cv.json", b"{}")])])
    assert (summary.created, summary.updated, summary.unchanged, summary.files_added) == (
        1,
        0,
        0,
        2,
    )
    (job,) = jobs(db, alice)
    assert job.slug == "acme-ai-engineer"
    assert (job.status, job.contract_type, job.fit, job.fit_overridden) == (
        JobStatus.found,
        ContractType.CDI,
        80,
        False,
    )
    assert (job.found_on, job.published_on, job.applied_on) == (
        date(2026, 10, 7),
        date(2026, 10, 1),
        None,
    )
    assert (job.posting_text, job.source, job.competencies, job.gaps) == (
        "Build agents.",
        "ashby:acme",
        ["agents", "python"],
        ["rag"],
    )
    assert job.legacy == {"jd": "data/jobs/acme-ai-engineer/posting.txt"}
    assert job.created_at == NOW
    stored = db.execute(sa.select(JobFile).where(JobFile.job_id == job.id)).scalars().all()
    assert {f.origin for f in stored} == {FileOrigin.legacy}
    by_name = {f.name: f for f in stored}
    assert by_name["cv.pdf"].content == PDF
    assert by_name["cv.pdf"].sha256 == hashlib.sha256(PDF).digest()
    assert (by_name["cv.pdf"].mime, by_name["cv.json"].mime) == (
        "application/pdf",
        "application/json",
    )
    ((kind, data),) = [(e.kind, e.data) for e in events(db, alice)]
    assert (kind, data) == (JobEventKind.created, {"slug": job.slug, "via": "import"})


def test_found_on_defaults_to_today_and_is_kept(db: Session, alice: uuid.UUID) -> None:
    run(db, alice, [item(found_on=None)])
    assert jobs(db, alice)[0].found_on == NOW.date()
    assert run(db, alice, [item(found_on=None)], later=60 * 24 * 3).unchanged == 1
    assert jobs(db, alice)[0].found_on == NOW.date()


def test_second_run_changes_nothing(db: Session, alice: uuid.UUID) -> None:
    items = [
        item(files=[pdf()]),
        item("beta-ml", company="Beta", title="ML", status="applied", applied_on="2026-10-06"),
        item("gamma-x", company="Gamma", title="X", url=None, fit=None, description=None),
    ]
    run(db, alice, items)
    before = [(j.id, j.updated_at) for j in jobs(db, alice)]
    count = len(events(db, alice))
    summary = run(db, alice, items, later=5)
    assert (summary.created, summary.updated, summary.unchanged, summary.skipped) == (0, 0, 3, 0)
    assert (summary.files_added, summary.files_replaced, summary.files_unchanged) == (0, 0, 1)
    assert summary.warnings == []
    db.expire_all()
    assert [(j.id, j.updated_at) for j in jobs(db, alice)] == before
    assert len(events(db, alice)) == count


def test_update_only_changed_fields_with_an_import_event(db: Session, alice: uuid.UUID) -> None:
    run(db, alice, [item()])
    summary = run(
        db,
        alice,
        [item(status="shortlisted", fit=91, notes="call", posting_text="New text.")],
        later=5,
    )
    assert (summary.updated, summary.unchanged) == (1, 0)
    (job,) = jobs(db, alice)
    assert (job.status, job.fit, job.notes, job.posting_text) == (
        JobStatus.shortlisted,
        91,
        "call",
        "New text.",
    )
    assert job.updated_at == NOW + timedelta(minutes=5)
    last = events(db, alice)[-1]
    assert last.kind is JobEventKind.import_
    assert last.data == {
        "changed": ["fit", "notes", "posting_text", "status"],
        "status": {"from": "found", "to": "shortlisted"},
    }


def test_matched_by_duplicate_rules_when_the_slug_differs(db: Session, alice: uuid.UUID) -> None:
    existing = service.create_job(
        db,
        alice,
        NewJob(company="Acme", title="AI Engineer", location="Paris", url="https://x.org/1"),
        NOW,
    )
    other = service.create_job(
        db, alice, NewJob(company="Beta", title="ML", location="Lyon", url="https://x.org/2"), NOW
    )
    db.commit()
    summary = run(
        db,
        alice,
        [
            # same URL once normalised, another slug
            item("acme-renamed", url="http://www.x.org/1/?utm_source=a"),
            # same company, title and location, no URL
            item("beta-ml-old", company="BETA", title="ml", location="lyon", url=None),
        ],
    )
    assert (summary.created, summary.updated) == (0, 2)
    db.expire_all()
    assert {j.id for j in jobs(db, alice)} == {existing.id, other.id}
    # The stored slug stays: it is how the app names the row.
    assert db.get(Job, existing.id).slug == "acme-ai-engineer"  # type: ignore[union-attr]


def test_one_row_is_never_matched_twice(db: Session, alice: uuid.UUID) -> None:
    run(db, alice, [item(url=None)])
    # Two items with the same identity (another city would differ): the first takes the row by
    # slug, the second is a new row with its own slug.
    summary = run(db, alice, [item(url=None), item("acme-ai-engineer-2", url=None)])
    assert (summary.unchanged, summary.created) == (1, 1)
    assert [j.slug for j in jobs(db, alice)] == ["acme-ai-engineer", "acme-ai-engineer-2"]


def test_url_owned_by_another_row_is_skipped(db: Session, alice: uuid.UUID) -> None:
    run(db, alice, [item(), item("beta-ml", company="Beta", title="ML", url="https://x.org/b")])
    summary = run(db, alice, [item(url="https://x.org/b")])
    assert summary.skipped == 1
    assert summary.warnings == ["acme-ai-engineer: its url belongs to the row beta-ml, skipped"]
    assert jobs(db, alice)[0].url == "https://jobs.example/acme-ai-engineer"


def test_slug_taken_by_a_row_matched_elsewhere(db: Session, alice: uuid.UUID) -> None:
    run(db, alice, [item(url="https://x.org/a")])
    # The first item takes the stored row by URL (its legacy jd path differs: an update); the
    # second has the stored slug but no row is left for it, so it gets the next free slug.
    summary = run(
        db,
        alice,
        [
            item("renamed", url="https://x.org/a"),
            item(company="Other", title="Role", url="https://x.org/z"),
        ],
    )
    assert (summary.updated, summary.created) == (1, 1)
    assert summary.warnings == [
        "acme-ai-engineer: slug taken by another row, stored as acme-ai-engineer-2"
    ]


def test_files_are_deduped_and_replaced(db: Session, alice: uuid.UUID) -> None:
    run(db, alice, [item(files=[pdf()])])
    summary = run(
        db,
        alice,
        [
            item(
                files=[
                    pdf(),  # same bytes, same name
                    ImportFile.of("letter.pdf", PDF),  # same bytes, another name: not stored again
                    ImportFile.of("answers.json", b"{}"),  # new
                ]
            )
        ],
        later=1,
    )
    assert (summary.files_unchanged, summary.files_added) == (2, 1)
    (job,) = jobs(db, alice)
    assert sorted(files(db, job)) == ["answers.json", "cv.pdf"]
    summary = run(db, alice, [item(files=[pdf("cv.pdf", b"v2")])], later=2)
    assert (summary.files_replaced, summary.updated) == (1, 1)
    db.expire_all()
    assert files(db, job)["cv.pdf"] == PDF + b"v2"
    assert events(db, alice)[-1].data == {"changed": [], "files_replaced": ["cv.pdf"]}


def test_files_of_sent_rows_are_never_replaced(db: Session, alice: uuid.UUID) -> None:
    sent = {"status": "applied", "applied_on": "2026-10-06"}
    later_stage = {"status": "interview", "applied_on": "2026-10-06"}
    run(
        db,
        alice,
        [
            item(files=[pdf()], **sent),
            item("beta-ml", company="Beta", title="ML", files=[pdf()], **later_stage),
            item("gamma-x", company="Gamma", title="X", files=[pdf()]),
        ],
    )
    changed = [pdf("cv.pdf", b"other"), ImportFile.of("answers.json", b"{}")]
    summary = run(
        db,
        alice,
        [
            item(files=changed, **sent),
            item("beta-ml", company="Beta", title="ML", files=changed, **later_stage),
            # Not sent before, but this import marks it applied: protected too.
            item("gamma-x", company="Gamma", title="X", files=changed, **sent),
        ],
        later=1,
    )
    assert (summary.files_kept, summary.files_replaced, summary.files_added) == (3, 0, 3)
    assert summary.warnings == [
        f"{slug}: cv.pdf differs from the file already sent, the stored one is kept (files of "
        "applied rows never change)"
        for slug in ("acme-ai-engineer", "beta-ml", "gamma-x")
    ]
    db.expire_all()
    for job in jobs(db, alice):
        assert files(db, job) == {"cv.pdf": PDF, "answers.json": b"{}"}


def test_an_import_never_deletes(db: Session, alice: uuid.UUID) -> None:
    run(db, alice, [item(files=[pdf()]), item("beta-ml", company="Beta", title="ML")])
    summary = run(db, alice, [item(files=[])])
    assert summary.unchanged == 1
    assert len(jobs(db, alice)) == 2
    assert files(db, jobs(db, alice)[0]) == {"cv.pdf": PDF}


def test_isolation(db: Session, alice: uuid.UUID, bob: uuid.UUID) -> None:
    run(db, bob, [item(files=[pdf()], notes="bob's")])
    bob_before = [(j.id, j.updated_at, j.notes) for j in jobs(db, bob)]
    summary = run(db, alice, [item(files=[pdf("cv.pdf", b"alice")], notes="alice's")])
    # Bob's same slug and URL are not Alice's: she gets her own row.
    assert summary.created == 1
    db.expire_all()
    assert [(j.id, j.updated_at, j.notes) for j in jobs(db, bob)] == bob_before
    assert files(db, jobs(db, bob)[0]) == {"cv.pdf": PDF}
    assert files(db, jobs(db, alice)[0]) == {"cv.pdf": PDF + b"alice"}
    assert {e.user_id for e in events(db, alice)} == {alice}


def test_unicode_and_long_values(db: Session, alice: uuid.UUID) -> None:
    text = "Ingénieur IA, équipe données 日本語 🚀\n" * 5_000
    run(db, alice, [item(company="Société Générale", posting_text=text, notes="é" * 10_000)])
    (job,) = jobs(db, alice)
    assert (job.company, job.posting_text, len(job.notes)) == ("Société Générale", text, 10_000)
    assert (
        run(
            db, alice, [item(company="Société Générale", posting_text=text, notes="é" * 10_000)]
        ).unchanged
        == 1
    )
