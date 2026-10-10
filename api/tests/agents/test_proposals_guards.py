"""Guards added after the review of PR #23: a status proposal made for another status than the
job has now is invalid when accepted (and superseded as soon as the status changes by any path),
two writers at once leave one pending proposal (advisory lock per user, target and kind), the
api's 409 status_changed, and a recorded stream refused in production."""

import threading
import time
import uuid

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals
from app.agents.models import Proposal, ProposalDecider, ProposalStatus
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus
from tests.agents.conftest import ALICE, FakeAnthropic, client_for
from tests.agents.run_helpers import add_job, add_user, now
from tests.agents.test_proposals_service import NOTE, STATUS, accept, job_row, propose, row
from tests.agents.test_runs_api import RecordingQueue, save_key, user_id
from tests.identity.api_helpers import Api, Clock

MOVED_FROM_FOUND = {"status": "skipped", "from": "found", "reason": "r"}


@pytest.fixture
def owner(factory: sessionmaker[Session]) -> uuid.UUID:
    return add_user(factory, "alice@example.org")


@pytest.fixture
def job(factory: sessionmaker[Session], owner: uuid.UUID) -> uuid.UUID:
    return add_job(factory, owner)


def set_job_status(
    factory: sessionmaker[Session], user: uuid.UUID, job: uuid.UUID, to: str
) -> None:
    with factory() as db, db.begin():
        locked = jobs_service.lock_job(db, user, job)
        assert locked is not None
        jobs_service.set_status(db, locked, JobStatus(to), now())


def test_a_status_proposal_from_another_status_is_invalid(
    factory: sessionmaker[Session], owner: uuid.UUID, job: uuid.UUID
) -> None:
    pid = propose(factory, owner, job, STATUS, MOVED_FROM_FOUND)
    # The status moved without going through set_status (a proposal written after a change, as
    # a run racing the user would leave it).
    with factory() as db, db.begin():
        db.execute(sa.update(Job).where(Job.id == job).values(status=JobStatus.applied))
    with pytest.raises(proposals.StatusChangedError):
        accept(factory, owner, pid)
    found = row(factory, pid)
    assert found.status is ProposalStatus.invalid
    assert found.decided_by is ProposalDecider.system
    assert found.reason == proposals.STATUS_CHANGED
    assert job_row(factory, job).status is JobStatus.applied
    # An edited status is the user's own choice: it applies whatever the status was.
    edited = propose(factory, owner, job, STATUS, MOVED_FROM_FOUND)
    _, updated = accept(factory, owner, edited, edited_value="interview")
    assert updated.status is JobStatus.interview


def test_changing_the_status_supersedes_pending_status_proposals(
    factory: sessionmaker[Session], owner: uuid.UUID, job: uuid.UUID
) -> None:
    other_job = add_job(factory, owner, "Other", "Role")
    status = propose(factory, owner, job, STATUS, MOVED_FROM_FOUND)
    note = propose(factory, owner, job)
    elsewhere = propose(factory, owner, other_job, STATUS, MOVED_FROM_FOUND)
    set_job_status(factory, owner, job, "shortlisted")
    found = row(factory, status)
    assert found.status is ProposalStatus.superseded
    assert found.decided_by is ProposalDecider.system
    assert found.reason == jobs_service.STATUS_MOVED
    assert row(factory, note).status is ProposalStatus.pending
    assert row(factory, elsewhere).status is ProposalStatus.pending
    # Accepting a status proposal keeps itself accepted and supersedes any other pending one
    # (forced here: create() alone never leaves two pending of one kind).
    with factory() as db, db.begin():
        db.add(
            Proposal(
                user_id=owner,
                target="job",
                target_id=other_job,
                kind=STATUS,
                payload={"status": "closed", "from": "found"},
                created_at=now(),
            )
        )
    accept(factory, owner, elsewhere)
    assert row(factory, elsewhere).status is ProposalStatus.accepted
    with factory() as db:
        statuses = sorted(
            p.status.value
            for p in proposals.list_proposals(db, owner, proposals.Filters(target_id=other_job), 9)
        )
    assert statuses == ["accepted", "superseded"]


def test_two_writers_at_once_leave_one_pending(
    factory: sessionmaker[Session], owner: uuid.UUID, job: uuid.UUID
) -> None:
    """The first transaction holds its new pending proposal uncommitted while the second one
    creates: the advisory lock makes the second wait, then see and supersede the first."""
    holding = threading.Event()
    errors: list[BaseException] = []

    def first() -> None:
        try:
            with factory() as db, db.begin():
                proposals.create(
                    db,
                    owner,
                    target="job",
                    target_id=job,
                    kind=NOTE,
                    payload={"text": "first"},
                    run_id=None,
                    now=now(),
                )
                holding.set()
                time.sleep(0.5)
        except BaseException as exc:  # pragma: no cover, reported by the assertion below
            errors.append(exc)
            holding.set()

    thread = threading.Thread(target=first)
    thread.start()
    assert holding.wait(5)
    started = time.monotonic()
    second = propose(factory, owner, job, payload={"text": "second"})
    waited = time.monotonic() - started
    thread.join(5)
    assert errors == []
    assert waited > 0.2  # it waited for the first writer's lock
    with factory() as db:
        found = proposals.list_proposals(db, owner, proposals.Filters(target_id=job), 10)
    assert [p.id for p in found if p.status is ProposalStatus.pending] == [second]
    superseded = [p.payload["text"] for p in found if p.status is ProposalStatus.superseded]
    assert superseded == ["first"]


def test_status_changed_is_409(
    api: Api, alice: str, factory: sessionmaker[Session], recorded: RecordingQueue
) -> None:
    me = user_id(api, alice)
    job = add_job(factory, me)
    pid = propose(factory, me, job, STATUS, MOVED_FROM_FOUND)
    with factory() as db, db.begin():
        db.execute(sa.update(Job).where(Job.id == job).values(status=JobStatus.applied))
    response = api.post(f"/api/proposals/{pid}/accept", token=alice, json={})
    assert response.status_code == 409
    body = response.json()
    assert body["reason"] == "status_changed"
    assert body["status"] == "invalid"
    assert "status changed" in body["detail"]


def test_a_manual_status_change_supersedes_through_the_api(
    api: Api, alice: str, factory: sessionmaker[Session], recorded: RecordingQueue
) -> None:
    me = user_id(api, alice)
    job = add_job(factory, me)
    pid = propose(factory, me, job, STATUS, MOVED_FROM_FOUND)
    patched = api.request("PATCH", f"/api/jobs/{job}", token=alice, json={"status": "applied"})
    assert patched.status_code == 200, patched.text
    response = api.post(f"/api/proposals/{pid}/accept", token=alice, json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "not_pending"
    assert response.json()["status"] == "superseded"


@pytest.fixture
def recorded(app: FastAPI) -> RecordingQueue:
    fake = RecordingQueue()
    app.state.queue = fake
    return fake


def test_a_recorded_stream_is_refused_in_production(
    migrated_url: str, clock: Clock, anthropic_api: FakeAnthropic, key: str
) -> None:
    from app.agents.verify import Verifier
    from app.main import create_app
    from tests.conftest import make_settings
    from tests.identity.api_helpers import make_user

    settings = make_settings(migrated_url, credentials_key=key, secret_key="s" * 40).model_copy(
        update={"app_env": "production"}
    )
    app = create_app(settings, clock=clock)
    app.state.model_verifier = Verifier()
    queue = RecordingQueue()
    app.state.queue = queue
    for api in client_for(app):
        make_user(app.state.sessionmaker, email=ALICE, name="Alice")
        token = api.login_token(ALICE)
        save_key(api, token)
        job = add_job(app.state.sessionmaker, user_id(api, token))
        refused = api.post(
            "/api/agents/runs",
            token=token,
            json={"agent": "triage", "job_id": str(job), "input": {"script": "triage_invalid"}},
        )
        assert refused.status_code == 422
        assert refused.json()["detail"] == (
            "input.script: recorded streams exist only outside production."
        )
        started = api.post(
            "/api/agents/runs",
            token=token,
            json={"agent": "triage", "job_id": str(job), "input": {}},
        )
        assert started.status_code == 202, started.text
        assert started.json()["input"] == {}
        assert len(queue.jobs) == 1
