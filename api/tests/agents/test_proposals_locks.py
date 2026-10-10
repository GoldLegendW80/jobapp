"""Lock order and batching of issue #25: accepting a proposal locks the job before the proposal,
like PATCH, bulk and undo do (so an accept racing a bulk status change on the same job never
deadlocks), and a bulk or undo status change supersedes the pending status proposals of all its
jobs in one statement."""

import threading
import time
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals
from app.agents.models import Proposal, ProposalDecider, ProposalStatus
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus
from tests.agents.run_helpers import add_job, add_user, now
from tests.agents.test_proposals_service import STATUS, job_row, propose, row

MOVED_FROM_FOUND = {"status": "skipped", "from": "found", "reason": "r"}
WAIT_S = 10


@pytest.mark.parametrize("iteration", range(20))
def test_accept_racing_a_bulk_status_change_does_not_deadlock(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, iteration: int
) -> None:
    """The bulk change holds the job's lock when the accept starts; the accept reads its proposal
    and goes on while the bulk, released, supersedes the job's pending status proposals. With
    the old order (proposal locked, then the job) each side waits for the other's row and
    Postgres aborts one of them as a deadlock."""
    owner = add_user(factory, f"alice{iteration}@example.org")
    job = add_job(factory, owner)
    pid = propose(factory, owner, job, STATUS, MOVED_FROM_FOUND)

    bulk_has_job = threading.Event()
    accept_has_read = threading.Event()
    errors: list[BaseException] = []
    outcome: dict[str, Any] = {}

    real_lock_jobs = jobs_service.lock_jobs
    real_get = proposals.get

    def lock_jobs_then_wait(*args: Any, **kwargs: Any) -> list[Job]:
        locked = real_lock_jobs(*args, **kwargs)
        bulk_has_job.set()
        # Let the accept read its proposal (and, with the old order, lock it) first.
        assert accept_has_read.wait(WAIT_S)
        time.sleep(0.2)
        return locked

    def get_then_signal(*args: Any, **kwargs: Any) -> Proposal | None:
        found = real_get(*args, **kwargs)
        accept_has_read.set()
        return found

    monkeypatch.setattr(jobs_service, "lock_jobs", lock_jobs_then_wait)
    monkeypatch.setattr(proposals, "get", get_then_signal)

    def bulk() -> None:
        try:
            with factory() as db, db.begin():
                jobs_service.bulk_update(db, owner, [job], now(), status=JobStatus.closed)
        except BaseException as exc:
            errors.append(exc)
            bulk_has_job.set()

    def accept() -> None:
        assert bulk_has_job.wait(WAIT_S)
        try:
            with factory() as db, db.begin():
                try:
                    outcome["accepted"] = proposals.accept(db, owner, pid, now())
                except proposals.NotPendingError as exc:
                    outcome["not_pending"] = exc.proposal.status
        except BaseException as exc:
            errors.append(exc)
            accept_has_read.set()

    threads = [threading.Thread(target=bulk), threading.Thread(target=accept)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_S * 2)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    # The bulk went first: the accept waited for the job, then found its proposal superseded.
    assert outcome == {"not_pending": ProposalStatus.superseded}
    assert job_row(factory, job).status is JobStatus.closed
    found = row(factory, pid)
    assert found.status is ProposalStatus.superseded
    assert found.reason == jobs_service.STATUS_MOVED


def test_accept_rechecks_the_proposal_after_locking_the_job(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposal decided between the unlocked read and the job's lock (rejected here) is not
    applied: the locked re-read sees it is no longer pending."""
    owner = add_user(factory, "alice@example.org")
    job = add_job(factory, owner)
    pid = propose(factory, owner, job, STATUS, MOVED_FROM_FOUND)
    real_lock_job = jobs_service.lock_job

    def reject_first(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> Job | None:
        with factory() as other, other.begin():
            proposals.reject(other, owner, pid, now())
        return real_lock_job(db, user_id, job_id)

    monkeypatch.setattr(jobs_service, "lock_job", reject_first)
    with factory() as db, db.begin():
        with pytest.raises(proposals.NotPendingError) as raised:
            proposals.accept(db, owner, pid, now())
        assert raised.value.proposal.status is ProposalStatus.rejected
    assert job_row(factory, job).status is JobStatus.found
    assert row(factory, pid).status is ProposalStatus.rejected


def test_accept_returns_none_when_the_proposal_vanishes_after_the_read(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = add_user(factory, "alice@example.org")
    job = add_job(factory, owner)
    pid = propose(factory, owner, job, STATUS, MOVED_FROM_FOUND)
    real_lock_job = jobs_service.lock_job

    def delete_first(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> Job | None:
        with factory() as other, other.begin():
            other.execute(sa.delete(Proposal).where(Proposal.id == pid))
        return real_lock_job(db, user_id, job_id)

    monkeypatch.setattr(jobs_service, "lock_job", delete_first)
    with factory() as db, db.begin():
        assert proposals.accept(db, owner, pid, now()) is None
    assert job_row(factory, job).status is JobStatus.found


def _count_supersede_updates(engine: sa.Engine) -> tuple[list[str], Any]:
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        if statement.lstrip().upper().startswith("UPDATE PROPOSALS"):
            statements.append(statement)

    sa.event.listen(engine, "before_cursor_execute", record)
    return statements, record


@pytest.mark.parametrize("undo", [False, True])
def test_bulk_and_undo_supersede_500_jobs_in_one_statement(
    factory: sessionmaker[Session], engine: sa.Engine, undo: bool
) -> None:
    owner = add_user(factory, "alice@example.org")
    other = add_user(factory, "bob@example.org")
    with factory() as db, db.begin():
        ids = [
            jobs_service.create_job(
                db, owner, jobs_service.NewJob(company=f"Company {i}", title="Role"), now()
            ).id
            for i in range(500)
        ]
        db.add_all(
            Proposal(
                user_id=owner,
                target="job",
                target_id=job_id,
                kind=STATUS,
                payload=MOVED_FROM_FOUND,
                created_at=now(),
            )
            for job_id in ids
        )
        # Another user's pending proposal on a job of their own, and a note on one of ours,
        # both untouched.
        theirs = jobs_service.create_job(
            db, other, jobs_service.NewJob(company="Theirs", title="Role"), now()
        ).id
        db.add(
            Proposal(
                user_id=other,
                target="job",
                target_id=theirs,
                kind=STATUS,
                payload=MOVED_FROM_FOUND,
                created_at=now(),
            )
        )
        db.add(
            Proposal(
                user_id=owner,
                target="job",
                target_id=ids[0],
                kind="job_note",
                payload={"text": "keep"},
                created_at=now(),
            )
        )
    with factory() as db, db.begin():
        outcome = jobs_service.bulk_update(db, owner, ids, now(), status=JobStatus.closed)
    if undo:
        # Pending status proposals made after the bulk change, superseded again by its undo.
        with factory() as db, db.begin():
            db.add_all(
                Proposal(
                    user_id=owner,
                    target="job",
                    target_id=job_id,
                    kind=STATUS,
                    payload={"status": "skipped", "from": "closed", "reason": "r"},
                    created_at=now(),
                )
                for job_id in ids
            )
    statements, listener = _count_supersede_updates(engine)
    try:
        with factory() as db, db.begin():
            if undo:
                restored = jobs_service.undo_bulk(db, owner, outcome.undo, now())
                assert len(restored.restored) == 500
            else:
                again = jobs_service.bulk_update(db, owner, ids, now(), status=JobStatus.skipped)
                assert all(result.changed for result in again.results)
    finally:
        sa.event.remove(engine, "before_cursor_execute", listener)
    assert len(statements) == 1
    with factory() as db:
        counts = dict(
            db.execute(
                sa.select(Proposal.status, sa.func.count())
                .where(Proposal.user_id == owner, Proposal.kind == STATUS)
                .group_by(Proposal.status)
            ).all()
        )
        assert counts == {ProposalStatus.superseded: 1000 if undo else 500}
        superseded = db.scalars(
            sa.select(Proposal).where(Proposal.user_id == owner, Proposal.kind == STATUS)
        ).all()
        assert {p.decided_by for p in superseded} == {ProposalDecider.system}
        assert {p.reason for p in superseded} == {jobs_service.STATUS_MOVED}
        untouched = db.scalars(
            sa.select(Proposal.status).where(
                sa.or_(Proposal.user_id == other, Proposal.kind == "job_note")
            )
        ).all()
        assert untouched == [ProposalStatus.pending, ProposalStatus.pending]


def test_a_bulk_change_without_status_supersedes_nothing(
    factory: sessionmaker[Session],
) -> None:
    owner = add_user(factory, "alice@example.org")
    job = add_job(factory, owner)
    pid = propose(factory, owner, job, STATUS, MOVED_FROM_FOUND)
    with factory() as db, db.begin():
        jobs_service.bulk_update(db, owner, [job], now(), notes_append="hello")
        # Unchanged status: nothing superseded either.
        jobs_service.bulk_update(db, owner, [job], now(), status=JobStatus.found)
    assert row(factory, pid).status is ProposalStatus.pending
