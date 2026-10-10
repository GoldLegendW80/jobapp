"""The proposals service (app/agents/proposals.py) against a real Postgres: every status
transition, superseding, applying through the jobs service (events, applied_on, the job's
version), edited values validated by kind, the stale job version, the job gone, user isolation,
the kind filter, and the transitions the triage agent may propose."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals
from app.agents.models import Proposal, ProposalDecider, ProposalStatus
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from tests.agents.run_helpers import add_job, add_user, new_run, now

NOTE = proposals.ProposalKind.job_note.value
STATUS = proposals.ProposalKind.job_status.value


def propose(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    job: uuid.UUID | None,
    kind: str = NOTE,
    payload: dict[str, Any] | None = None,
    run: uuid.UUID | None = None,
    **extra: Any,
) -> uuid.UUID:
    if payload is None:
        payload = {"text": "Strong fit."} if kind == NOTE else {"status": "skipped", "reason": "r"}
    with factory() as db, db.begin():
        return proposals.create(
            db,
            user,
            target=proposals.TARGET_JOB,
            target_id=job,
            kind=kind,
            payload=payload,
            run_id=run,
            now=now(),
            **extra,
        ).id


def row(factory: sessionmaker[Session], proposal_id: uuid.UUID) -> Proposal:
    with factory() as db:
        found = db.get(Proposal, proposal_id)
        assert found is not None
        return found


def job_row(factory: sessionmaker[Session], job_id: uuid.UUID) -> Job:
    with factory() as db:
        found = db.get(Job, job_id)
        assert found is not None
        return found


def job_events(factory: sessionmaker[Session], job_id: uuid.UUID) -> list[JobEvent]:
    with factory() as db:
        return list(
            db.scalars(
                sa.select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            ).all()
        )


def accept(factory: sessionmaker[Session], user: uuid.UUID, pid: uuid.UUID, **kwargs: Any) -> Any:
    with factory() as db:
        try:
            done = proposals.accept(db, user, pid, now(), **kwargs)
        except proposals.TargetGoneError:
            db.commit()
            raise
        except Exception:
            db.rollback()
            raise
        db.commit()
        return done


def reject(factory: sessionmaker[Session], user: uuid.UUID, pid: uuid.UUID) -> Any:
    with factory() as db:
        try:
            done = proposals.reject(db, user, pid, now())
        except Exception:
            db.rollback()
            raise
        db.commit()
        return done


@pytest.fixture
def alice(factory: sessionmaker[Session]) -> uuid.UUID:
    return add_user(factory, "alice@example.org")


@pytest.fixture
def job(factory: sessionmaker[Session], alice: uuid.UUID) -> uuid.UUID:
    return add_job(factory, alice)


def test_create_is_pending_and_bound(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    run = new_run(factory, alice, job_id=job, agent="triage", data={})
    pid = propose(factory, alice, job, run=run)
    found = row(factory, pid)
    assert found.status is ProposalStatus.pending
    assert (found.user_id, found.run_id, found.target, found.target_id) == (alice, run, "job", job)
    assert found.decided_by is None
    assert found.decided_at is None
    assert found.payload == {"text": "Strong fit."}


def test_a_newer_proposal_supersedes_the_pending_one_of_the_same_kind(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    first = propose(factory, alice, job)
    status = propose(factory, alice, job, STATUS)
    other_job = add_job(factory, alice, "Other", "Role")
    elsewhere = propose(factory, alice, other_job)
    decided = propose(factory, alice, job, payload={"text": "decided"})
    reject(factory, alice, decided)
    second = propose(factory, alice, job, payload={"text": "Newer."})
    old = row(factory, first)
    assert old.status is ProposalStatus.superseded
    assert old.decided_by is ProposalDecider.system
    assert old.reason == proposals.SUPERSEDED
    assert old.decided_at is not None
    # Another kind, another job and a decided one are left alone.
    assert row(factory, status).status is ProposalStatus.pending
    assert row(factory, elsewhere).status is ProposalStatus.pending
    assert row(factory, decided).status is ProposalStatus.rejected
    assert row(factory, second).status is ProposalStatus.pending


def test_system_proposal_created_accepted_without_run(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    pid = propose(
        factory,
        alice,
        job,
        "mail_status",
        {"before": "applied", "after": "rejected"},
        status=ProposalStatus.accepted,
    )
    found = row(factory, pid)
    assert found.status is ProposalStatus.accepted
    assert found.decided_by is ProposalDecider.system
    assert found.run_id is None
    with factory() as db, pytest.raises(ValueError, match="pending or accepted"):
        proposals.create(
            db,
            alice,
            target="job",
            target_id=job,
            kind=NOTE,
            payload={},
            run_id=None,
            now=now(),
            status=ProposalStatus.rejected,
        )
    # A proposal that creates something has no target id yet, and supersedes nothing.
    created = propose(factory, alice, None)
    assert row(factory, created).target_id is None


def test_accept_note_appends_through_the_jobs_service(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    with factory() as db, db.begin():
        locked = jobs_service.lock_job(db, alice, job)
        assert locked is not None
        jobs_service.set_notes(db, locked, "My own note.", now() - timedelta(hours=1))
    before = job_row(factory, job).updated_at
    pid = propose(factory, alice, job, payload={"text": "Strong fit."})
    proposal, updated = accept(factory, alice, pid, expected_updated_at=before)
    assert proposal.status is ProposalStatus.accepted
    assert proposal.decided_by is ProposalDecider.user
    assert proposal.edited_value is None
    assert updated.notes == "My own note.\nStrong fit."
    assert job_row(factory, job).updated_at > before
    last = job_events(factory, job)[-1]
    assert last.kind is JobEventKind.note
    assert last.data == {
        "from": "My own note.",
        "to": "My own note.\nStrong fit.",
        "via": "agent",
        "proposal_id": str(pid),
    }


def test_accept_edited_note(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    pid = propose(factory, alice, job)
    proposal, updated = accept(factory, alice, pid, edited_value="  Edited by me ✍️\r\nline 2  ")
    assert updated.notes == "Edited by me ✍️\nline 2"
    assert proposal.edited_value == "Edited by me ✍️\nline 2"
    assert row(factory, pid).payload == {"text": "Strong fit."}


def test_accept_status_writes_the_event(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    payload = {"status": "skipped", "from": "found", "reason": "r"}
    pid = propose(factory, alice, job, STATUS, payload)
    _, updated = accept(factory, alice, pid)
    assert updated.status is JobStatus.skipped
    assert updated.applied_on is None
    last = job_events(factory, job)[-1]
    assert last.kind is JobEventKind.status
    assert last.data == {"from": "found", "to": "skipped", "via": "agent", "proposal_id": str(pid)}


def test_accept_status_edited_to_applied_fills_applied_on(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    pid = propose(factory, alice, job, STATUS)
    proposal, updated = accept(factory, alice, pid, edited_value="applied")
    assert updated.status is JobStatus.applied
    assert updated.applied_on == now().date()
    assert proposal.edited_value == "applied"


def test_accept_status_already_reached_changes_nothing(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    pid = propose(factory, alice, job, STATUS, {"status": "found", "reason": "r"})
    before = [event.id for event in job_events(factory, job)]
    proposal, _ = accept(factory, alice, pid)
    assert proposal.status is ProposalStatus.accepted
    assert [event.id for event in job_events(factory, job)] == before


@pytest.mark.parametrize(
    ("kind", "value", "message"),
    [
        (NOTE, "", "must not be empty"),
        (NOTE, "   \n ", "must not be empty"),
        (NOTE, None, "a note is a text"),
        (NOTE, 42, "a note is a text"),
        (NOTE, ["a"], "a note is a text"),
        (NOTE, "x" * (proposals.NOTE_MAX + 1), "at most"),
        (STATUS, "maybe", "must be one of"),
        (STATUS, None, "must be one of"),
        (STATUS, "", "must be one of"),
        (STATUS, "SKIPPED", "must be one of"),
    ],
)
def test_edited_value_is_validated_by_kind(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    job: uuid.UUID,
    kind: str,
    value: Any,
    message: str,
) -> None:
    pid = propose(factory, alice, job, kind)
    before = job_row(factory, job)
    with pytest.raises(proposals.InvalidValueError, match=message):
        accept(factory, alice, pid, edited_value=value)
    assert row(factory, pid).status is ProposalStatus.pending
    after = job_row(factory, job)
    assert (after.notes, after.status, after.updated_at) == (
        before.notes,
        before.status,
        before.updated_at,
    )


def test_notes_never_grow_past_their_limit(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    with factory() as db, db.begin():
        locked = jobs_service.lock_job(db, alice, job)
        assert locked is not None
        jobs_service.set_notes(db, locked, "n" * proposals.NOTES_MAX, now())
    pid = propose(factory, alice, job)
    with pytest.raises(proposals.InvalidValueError, match="would exceed"):
        accept(factory, alice, pid)
    assert row(factory, pid).status is ProposalStatus.pending


def test_stale_job_version_applies_nothing(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    seen = job_row(factory, job).updated_at
    with factory() as db, db.begin():
        locked = jobs_service.lock_job(db, alice, job)
        assert locked is not None
        jobs_service.set_status(db, locked, JobStatus.shortlisted, now())
    pid = propose(factory, alice, job, STATUS)
    with pytest.raises(jobs_service.StaleJobError):
        accept(factory, alice, pid, expected_updated_at=seen)
    assert row(factory, pid).status is ProposalStatus.pending
    assert job_row(factory, job).status is JobStatus.shortlisted
    # With the current version it goes through.
    current = job_row(factory, job).updated_at
    accept(factory, alice, pid, expected_updated_at=current)
    assert job_row(factory, job).status is JobStatus.skipped


def test_job_gone_marks_it_invalid(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    pid = propose(factory, alice, job)
    with factory() as db, db.begin():
        db.execute(sa.delete(Job).where(Job.id == job))
    with pytest.raises(proposals.TargetGoneError):
        accept(factory, alice, pid)
    found = row(factory, pid)
    assert found.status is ProposalStatus.invalid
    assert found.decided_by is ProposalDecider.system
    assert found.reason == proposals.JOB_GONE
    # A proposal without a target id cannot be applied to a job either.
    orphan = propose(factory, alice, None)
    with pytest.raises(proposals.TargetGoneError):
        accept(factory, alice, orphan)


def test_unknown_kind_cannot_be_applied(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    pid = propose(factory, alice, job, "mail_link", {"thread": "t"})
    with pytest.raises(proposals.InvalidValueError, match="cannot be applied"):
        accept(factory, alice, pid)
    assert row(factory, pid).status is ProposalStatus.pending


@pytest.mark.parametrize("first", ["accept", "reject", "supersede", "invalid"])
def test_only_pending_proposals_are_decided(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID, first: str
) -> None:
    pid = propose(factory, alice, job)
    expected = {
        "accept": ProposalStatus.accepted,
        "reject": ProposalStatus.rejected,
        "supersede": ProposalStatus.superseded,
        "invalid": ProposalStatus.invalid,
    }[first]
    if first == "accept":
        accept(factory, alice, pid)
    elif first == "reject":
        reject(factory, alice, pid)
    elif first == "supersede":
        propose(factory, alice, job)
    else:
        with factory() as db, db.begin():
            db.execute(sa.delete(Job).where(Job.id == job))
        with pytest.raises(proposals.TargetGoneError):
            accept(factory, alice, pid)
    notes = job_row(factory, job).notes if first != "invalid" else None
    for again in (accept, reject):
        with pytest.raises(proposals.NotPendingError):
            again(factory, alice, pid)
    assert row(factory, pid).status is expected
    if notes is not None:
        assert job_row(factory, job).notes == notes  # accepted once, applied once


def test_reject_changes_nothing_else(
    factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID
) -> None:
    before = job_row(factory, job)
    pid = propose(factory, alice, job, STATUS)
    found = reject(factory, alice, pid)
    assert found.status is ProposalStatus.rejected
    assert found.decided_by is ProposalDecider.user
    after = job_row(factory, job)
    assert (after.status, after.updated_at) == (before.status, before.updated_at)


def test_isolation(factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID) -> None:
    bob = add_user(factory, "bob@example.org")
    pid = propose(factory, alice, job)
    with factory() as db:
        assert proposals.get(db, bob, pid) is None
        assert proposals.list_proposals(db, bob, proposals.Filters(), 50) == []
    assert accept(factory, bob, pid) is None
    assert reject(factory, bob, pid) is None
    assert accept(factory, alice, uuid.uuid4()) is None
    assert row(factory, pid).status is ProposalStatus.pending
    # Bob's proposal naming Alice's job (impossible through the tools) cannot touch it either.
    forged = propose(factory, bob, job)
    with pytest.raises(proposals.TargetGoneError):
        accept(factory, bob, forged)
    assert job_row(factory, job).notes == ""


def test_list_filters(factory: sessionmaker[Session], alice: uuid.UUID, job: uuid.UUID) -> None:
    run = new_run(factory, alice, job_id=job, agent="triage", data={})
    other = add_job(factory, alice, "Other", "Role")
    note = propose(factory, alice, job, run=run)
    status = propose(factory, alice, job, STATUS, run=run)
    elsewhere = propose(factory, alice, other)
    reject(factory, alice, elsewhere)

    def ids(**filters: Any) -> list[uuid.UUID]:
        with factory() as db:
            found = proposals.list_proposals(db, alice, proposals.Filters(**filters), 50)
            return [p.id for p in found]

    assert set(ids()) == {note, status, elsewhere}
    assert ids()[0] == elsewhere  # newest first
    assert ids(kinds=[NOTE]) == [elsewhere, note]
    assert ids(kinds=[STATUS]) == [status]
    assert set(ids(kinds=[NOTE, STATUS])) == {note, status, elsewhere}
    assert ids(kinds=["nothing"]) == []
    assert set(ids(statuses=[ProposalStatus.pending])) == {note, status}
    assert ids(statuses=[ProposalStatus.rejected]) == [elsewhere]
    assert set(ids(target="job", target_id=job)) == {note, status}
    assert ids(target="mail") == []
    assert set(ids(run_id=run)) == {note, status}
    assert ids(target_id=other, statuses=[ProposalStatus.pending]) == []
    with factory() as db:
        assert len(proposals.list_proposals(db, alice, proposals.Filters(), 1)) == 1


@pytest.mark.parametrize(
    ("current", "proposed", "allowed"),
    [
        ("found", "shortlisted", True),
        ("found", "skipped", True),
        ("found", "closed", True),
        ("shortlisted", "skipped", True),
        ("cv_ready", "skipped", True),
        ("staged", "closed", True),
        ("shortlisted", "shortlisted", False),
        ("cv_ready", "shortlisted", False),
        ("staged", "shortlisted", False),
        ("skipped", "skipped", False),
        ("applied", "skipped", False),
        ("interview", "closed", False),
        ("rejected", "skipped", False),
        ("closed", "skipped", False),
        ("skipped", "shortlisted", False),
        ("found", "applied", False),
        ("found", "interview", False),
        ("found", "rejected", False),
        ("found", "cv_ready", False),
        ("found", "staged", False),
        ("found", "found", False),
    ],
)
def test_triage_transitions(current: str, proposed: str, allowed: bool) -> None:
    error = proposals.triage_transition_error(JobStatus(current), JobStatus(proposed))
    assert (error is None) is allowed, error


@pytest.mark.parametrize(
    ("value", "message"),
    [(None, "must not be empty"), ("  ", "must not be empty"), ("r" * 1001, "at most")],
)
def test_reason_is_checked(value: Any, message: str) -> None:
    with pytest.raises(proposals.InvalidValueError, match=message):
        proposals.clean_reason(value)
    assert proposals.clean_reason("  two\n words ") == "two words"
