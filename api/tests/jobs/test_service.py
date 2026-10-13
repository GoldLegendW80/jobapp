"""The service helpers against a real Postgres: user scoping on every read, duplicates and
slugs, filters, status and fit rules, files, and one running sweep per user under concurrency."""

import hashlib
import threading
import uuid
from datetime import date, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import service
from app.jobs.models import (
    ContractType,
    FileOrigin,
    Job,
    JobEventKind,
    JobFile,
    JobStatus,
    SweepStatus,
    SweepTrigger,
)
from app.jobs.scoring import ScoreResult
from app.jobs.service import DuplicateJobError, JobFilters, NewJob, SweepRunningError
from tests.jobs.conftest import NOW


def add(
    db: Session,
    user: uuid.UUID,
    company: str = "Mistral AI",
    title: str = "AI Engineer",
    **fields: object,
) -> Job:
    job = service.create_job(db, user, NewJob(company=company, title=title, **fields), NOW)  # type: ignore[arg-type]
    db.commit()
    return job


def test_create_job_defaults_slug_and_event(db: Session, alice: uuid.UUID) -> None:
    job = add(db, alice, url="https://jobs.example/1", location="Paris")
    assert job.slug == "mistral-ai-ai-engineer"
    assert (job.status, job.contract_type, job.fit, job.fit_overridden) == (
        JobStatus.found,
        ContractType.CDI,
        None,
        False,
    )
    assert job.found_on == NOW.date()
    assert (job.competencies, job.gaps, job.notes) == ([], [], "")
    (event,) = service.list_events(db, alice, job.id)
    assert (event.kind, event.data, event.user_id) == (
        JobEventKind.created,
        {"slug": job.slug},
        alice,
    )


def test_duplicates_are_refused_with_the_existing_job(db: Session, alice: uuid.UUID) -> None:
    first = add(db, alice, url="https://jobs.example/1", location="Paris")
    for duplicate in (
        NewJob(company="Other", title="Other", url="http://jobs.example/1/?utm_source=x"),
        NewJob(company="mistral-ai", title="AI ENGINEER", location="paris"),
    ):
        with pytest.raises(DuplicateJobError) as caught:
            service.create_job(db, alice, duplicate, NOW)
        assert caught.value.job.id == first.id
    db.rollback()
    # Same company and title elsewhere: another posting, with the next free slug.
    second = add(db, alice, location="London")
    third = add(db, alice, location="Berlin")
    assert (second.slug, third.slug) == ("mistral-ai-ai-engineer-2", "mistral-ai-ai-engineer-3")


def test_users_never_see_each_other(db: Session, alice: uuid.UUID, bob: uuid.UUID) -> None:
    mine = add(db, alice, url="https://jobs.example/1")
    theirs = add(db, bob, url="https://jobs.example/1")  # the same posting is not a duplicate
    assert mine.slug == theirs.slug
    assert service.get_job(db, alice, mine.id) is not None
    assert service.get_job(db, alice, theirs.id) is None
    assert service.get_job(db, bob, mine.id) is None
    assert service.get_job_by_slug(db, bob, mine.slug).id == theirs.id
    assert service.get_job(db, alice, uuid.uuid4()) is None
    assert service.list_events(db, bob, mine.id) == []
    listed = db.execute(service.filtered(alice, JobFilters())).scalars().all()
    assert [j.id for j in listed] == [mine.id]
    file = service_file(db, mine)
    assert service.get_file(db, alice, mine.id, file.id) is not None
    assert service.get_file(db, bob, mine.id, file.id) is None
    assert service.get_file(db, bob, theirs.id, file.id) is None  # someone else's file id
    assert service.list_files(db, bob, mine.id) == []
    sweep = service.start_sweep(db, alice, SweepTrigger.manual, NOW)
    db.commit()
    assert service.get_sweep(db, bob, sweep.id) is None
    assert service.running_sweep(db, bob) is None
    assert service.running_sweep(db, alice).id == sweep.id


def service_file(db: Session, job: Job, content: bytes = b"%PDF-1.7") -> JobFile:
    file = JobFile(
        job_id=job.id,
        name="cv.pdf",
        mime="application/pdf",
        content=content,
        size=len(content),
        sha256=hashlib.sha256(content).digest(),
        origin=FileOrigin.legacy,
    )
    db.add(file)
    db.commit()
    return file


def test_files_are_listed_without_their_bytes(db: Session, alice: uuid.UUID) -> None:
    job = add(db, alice)
    service_file(db, job, b"x" * 1000)
    db.expunge_all()
    (listed,) = service.list_files(db, alice, job.id)
    assert "content" not in listed.__dict__  # deferred
    assert listed.size == 1000
    assert listed.content == b"x" * 1000  # loaded on access
    db.add(
        JobFile(
            job_id=job.id,
            name="bad",
            mime="text/plain",
            content=b"abc",
            size=4,  # not the byte count
            sha256=hashlib.sha256(b"abc").digest(),
            origin=FileOrigin.upload,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_filters(db: Session, alice: uuid.UUID, bob: uuid.UUID) -> None:
    a = add(
        db,
        alice,
        "Société Générale",
        "Ingénieur IA",
        location="Paris",
        contract_type=ContractType.VIE,
    )
    b = add(db, alice, "Mistral AI", "Applied AI Engineer", location="Paris", notes="call back")
    c = add(db, alice, "100%_Acme", "Data Engineer", location="Remote")
    add(db, bob, "Mistral AI", "Applied AI Engineer")
    service.lock_job(db, alice, c.id)  # set_status wants the row read under the lock
    b.fit, c.fit = 80, 40
    service.set_status(db, c, JobStatus.skipped, NOW)
    c.found_on = NOW.date() - timedelta(days=10)
    db.commit()

    def ids(**filters: object) -> set[uuid.UUID]:
        query = service.filtered(alice, JobFilters(**filters))  # type: ignore[arg-type]
        return {j.id for j in db.execute(query).scalars()}

    assert ids() == {a.id, b.id, c.id}
    assert ids(statuses=[JobStatus.found]) == {a.id, b.id}
    assert ids(statuses=[JobStatus.found, JobStatus.skipped]) == {a.id, b.id, c.id}
    assert ids(contract_types=[ContractType.VIE]) == {a.id}
    assert ids(company="mistral") == {b.id}
    assert ids(company="  GÉNÉRALE ") == {a.id}
    assert ids(company="%") == {c.id}  # LIKE wildcards are literal
    assert ids(company="0%_a") == {c.id}
    assert ids(company="_") == {c.id}
    assert ids(company="   ") == {a.id, b.id, c.id}
    assert ids(q="ingénieur") == {a.id}
    assert ids(q="paris engineer") == {b.id}
    assert ids(q="paris OR remote") == {a.id, b.id, c.id}
    assert ids(q="engineer -data") == {b.id}
    assert ids(q='"call back"') == {b.id}  # notes are searched
    assert ids(q="nowhere") == set()
    assert ids(q="!!! & | :*") == set()  # odd input is not an error
    assert ids(fit_min=50) == {b.id}  # unscored rows are left out of a fit range
    assert ids(fit_max=50) == {c.id}
    assert ids(fit_min=40, fit_max=80) == {b.id, c.id}
    assert ids(found_from=NOW.date()) == {a.id, b.id}
    assert ids(found_to=NOW.date() - timedelta(days=10)) == {c.id}
    assert ids(found_from=date(2000, 1, 1), found_to=date(2000, 1, 2)) == set()


def test_set_status_fills_applied_on_once(db: Session, alice: uuid.UUID) -> None:
    job = add(db, alice)
    service.lock_job(db, alice, job.id)
    assert service.set_status(db, job, JobStatus.found, NOW) is False
    assert service.set_status(db, job, JobStatus.applied, NOW) is True
    assert job.applied_on == NOW.date()
    later = NOW + timedelta(days=3)
    service.set_status(db, job, JobStatus.interview, later)
    service.set_status(db, job, JobStatus.applied, later)
    assert job.applied_on == NOW.date()
    db.commit()
    kinds = [(e.kind, e.data) for e in service.list_events(db, alice, job.id)]
    assert kinds[1:] == [
        (JobEventKind.status, {"from": "found", "to": "applied"}),
        (JobEventKind.status, {"from": "applied", "to": "interview"}),
        (JobEventKind.status, {"from": "interview", "to": "applied"}),
    ]


def test_apply_score_keeps_a_manual_fit(db: Session, alice: uuid.UUID) -> None:
    job = add(db, alice)
    result = ScoreResult(fit=72, competencies=["agents", "rag"], gaps=["rag"])
    assert service.apply_score(db, job, result, NOW) is True
    assert (job.fit, job.competencies, job.gaps) == (72, ["agents", "rag"], ["rag"])
    assert service.apply_score(db, job, result, NOW) is False  # unchanged: no event
    job.fit, job.fit_overridden = 90, True
    rescored = ScoreResult(fit=60, competencies=["agents"], gaps=[])
    # The manual fit is kept; the competencies and gaps changed, so the call reports a change.
    assert service.apply_score(db, job, rescored, NOW) is True
    assert (job.fit, job.fit_overridden, job.competencies, job.gaps) == (90, True, ["agents"], [])
    assert service.apply_score(db, job, rescored, NOW) is False  # nothing new this time
    assert service.apply_score(db, job, rescored, NOW, overwrite_manual=True) is True
    assert (job.fit, job.fit_overridden) == (60, False)
    db.commit()
    fits = [e.data for e in service.list_events(db, alice, job.id) if e.kind is JobEventKind.fit]
    assert fits == [
        {"from": None, "to": 72, "manual": False},
        {"from": 90, "to": 60, "manual": False, "was_manual": True},
    ]


def test_one_running_sweep_per_user(db: Session, alice: uuid.UUID, bob: uuid.UUID) -> None:
    first = service.start_sweep(db, alice, SweepTrigger.manual, NOW)
    with pytest.raises(SweepRunningError):
        service.start_sweep(db, alice, SweepTrigger.schedule, NOW)
    service.start_sweep(db, bob, SweepTrigger.schedule, NOW)  # the savepoint kept the session
    db.commit()
    first.status, first.finished_at = SweepStatus.done, NOW
    db.commit()
    second = service.start_sweep(db, alice, SweepTrigger.schedule, NOW)
    db.commit()
    assert service.running_sweep(db, alice).id == second.id


def test_concurrent_sweep_starts_let_one_through(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """Eight transactions start a sweep at once: exactly one wins, the rest are refused."""
    barrier = threading.Barrier(8)
    outcomes: list[str] = []
    lock = threading.Lock()

    def start() -> None:
        with factory() as session:
            barrier.wait()
            try:
                service.start_sweep(session, alice, SweepTrigger.manual, NOW)
                session.commit()
                outcome = "started"
            except SweepRunningError:
                session.rollback()
                outcome = "refused"
            with lock:
                outcomes.append(outcome)

    threads = [threading.Thread(target=start) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == ["refused"] * 7 + ["started"]


def test_concurrent_creations_of_one_posting(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """Two transactions add the same posting at once: one row, the other fails cleanly."""
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def create() -> None:
        with factory() as session:
            barrier.wait()
            try:
                new = NewJob("Acme", "AI Engineer", url="https://a.example/1")
                service.create_job(session, alice, new, NOW)
                session.commit()
                outcomes.append("created")
            except (IntegrityError, DuplicateJobError):
                session.rollback()
                outcomes.append("refused")

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == ["created", "refused"]
    with factory() as session:
        count = session.execute(sa.select(sa.func.count()).select_from(Job)).scalar_one()
    assert count == 1


def test_free_slug_ignores_lookalikes(db: Session, alice: uuid.UUID) -> None:
    add(db, alice, "Acme", "AI")  # acme-ai
    add(db, alice, "Acme", "AI Lead")  # acme-ai-lead: starts with "acme-ai-" but is no suffix
    assert service.free_slug(db, alice, "acme-ai") == "acme-ai-2"
    assert service.free_slug(db, alice, "acme") == "acme"
    assert service.free_slug(db, alice, "acme_ai") == "acme_ai"  # LIKE wildcard escaped
