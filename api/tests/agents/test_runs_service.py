"""The run state machine (app/agents/runs.py) on a real Postgres: every transition, the races
between them, user scoping, the per-user limit, the reapers and event numbering."""

import threading
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runs
from app.agents.models import AgentRun, RunStatus
from tests.agents.run_helpers import add_job, add_user, events, kinds, new_run, now, run_row


@pytest.fixture
def alice(factory: sessionmaker[Session]) -> uuid.UUID:
    return add_user(factory, "alice@example.org")


@pytest.fixture
def bob(factory: sessionmaker[Session]) -> uuid.UUID:
    return add_user(factory, "bob@example.org")


def claim(factory: sessionmaker[Session], run_id: uuid.UUID) -> AgentRun | None:
    with factory() as db:
        row = runs.claim(db, run_id, now())
        db.commit()
        return row


def finish(factory: sessionmaker[Session], run_id: uuid.UUID, outcome: runs.Outcome) -> bool:
    with factory() as db:
        done = runs.finish(db, run_id, outcome, now())
        db.commit()
        return done


def cancel(
    factory: sessionmaker[Session], user: uuid.UUID, run_id: uuid.UUID
) -> runs.CancelOutcome:
    with factory() as db:
        outcome = runs.request_cancel(db, user, run_id, now())
        db.commit()
        return outcome


def test_create_queues_with_a_first_event(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    run_id = new_run(factory, alice, {"message": "hé"})
    row = run_row(factory, run_id)
    assert row.status is RunStatus.queued
    assert row.input == {"message": "hé"}
    assert row.started_at is None
    assert row.finished_at is None
    assert events(factory, run_id) == [("status", {"status": "queued"})]


def test_full_lifecycle(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    run_id = new_run(factory, alice)
    claimed = claim(factory, run_id)
    assert claimed is not None
    assert claimed.status is RunStatus.running
    assert claim(factory, run_id) is None  # claimed once only
    outcome = runs.Outcome(
        status=RunStatus.done,
        output={"text": "ok"},
        model="claude-opus-5-5",
        input_tokens=10,
        output_tokens=2,
        cost_usd=Decimal("0.012345"),
    )
    assert finish(factory, run_id, outcome)
    row = run_row(factory, run_id)
    assert (row.status, row.output, row.input_tokens, row.cost_usd) == (
        RunStatus.done,
        {"text": "ok"},
        10,
        Decimal("0.012345"),
    )
    # A final run never changes again.
    assert not finish(factory, run_id, runs.Outcome(status=RunStatus.failed, error="late"))
    assert cancel(factory, alice, run_id) is runs.CancelOutcome.final
    assert run_row(factory, run_id).status is RunStatus.done


def test_finish_refuses_a_non_final_status(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    run_id = new_run(factory, alice)
    claim(factory, run_id)
    with pytest.raises(ValueError, match="not a final status"):
        finish(factory, run_id, runs.Outcome(status=RunStatus.running))


def test_finish_of_a_queued_run_does_nothing(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    run_id = new_run(factory, alice)
    assert not finish(factory, run_id, runs.Outcome(status=RunStatus.done))
    assert run_row(factory, run_id).status is RunStatus.queued


def test_cancel_queued_running_and_missing(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    queued = new_run(factory, alice)
    assert cancel(factory, bob, queued) is runs.CancelOutcome.missing  # not Bob's
    assert run_row(factory, queued).status is RunStatus.queued
    assert cancel(factory, alice, queued) is runs.CancelOutcome.cancelled
    row = run_row(factory, queued)
    assert row.status is RunStatus.cancelled
    assert row.finished_at is not None
    assert kinds(factory, queued) == ["status", "status"]
    assert claim(factory, queued) is None  # the worker skips it

    running = new_run(factory, alice)
    claim(factory, running)
    assert cancel(factory, bob, running) is runs.CancelOutcome.missing
    with factory() as db:
        assert not runs.cancel_requested(db, running)
    assert cancel(factory, alice, running) is runs.CancelOutcome.requested
    first = run_row(factory, running).cancel_requested_at
    assert cancel(factory, alice, running) is runs.CancelOutcome.requested  # idempotent
    assert run_row(factory, running).cancel_requested_at == first
    assert run_row(factory, running).status is RunStatus.running  # the worker decides the end
    with factory() as db:
        assert runs.cancel_requested(db, running)
    assert cancel(factory, alice, uuid.uuid4()) is runs.CancelOutcome.missing


def test_cancel_and_claim_race_has_one_winner(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    for _ in range(10):
        run_id = new_run(factory, alice)
        results: dict[str, object] = {}
        barrier = threading.Barrier(2)

        def do_claim(
            run_id: uuid.UUID = run_id,
            barrier: threading.Barrier = barrier,
            results: dict[str, object] = results,
        ) -> None:
            barrier.wait()
            results["claim"] = claim(factory, run_id)

        def do_cancel(
            run_id: uuid.UUID = run_id,
            barrier: threading.Barrier = barrier,
            results: dict[str, object] = results,
        ) -> None:
            barrier.wait()
            results["cancel"] = cancel(factory, alice, run_id)

        threads = [threading.Thread(target=do_claim), threading.Thread(target=do_cancel)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        status = run_row(factory, run_id).status
        if results["claim"] is not None:
            assert status is RunStatus.running
            assert results["cancel"] is runs.CancelOutcome.requested
        else:
            assert status is RunStatus.cancelled
            assert results["cancel"] is runs.CancelOutcome.cancelled
        finish(factory, run_id, runs.Outcome(status=RunStatus.cancelled))


def test_user_scoping_of_reads(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    mine = new_run(factory, alice)
    theirs = new_run(factory, bob)
    with factory() as db:
        assert runs.get_run(db, alice, mine) is not None
        assert runs.get_run(db, alice, theirs) is None
        assert [r.id for r in runs.list_runs(db, alice, 10)] == [mine]
        assert [r.id for r in runs.list_runs(db, bob, 10)] == [theirs]


def test_list_is_newest_first_and_pages(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    ids = []
    for _ in range(3):
        run_id = new_run(factory, alice)
        ids.append(run_id)
        assert claim(factory, run_id) is not None
        assert finish(factory, run_id, runs.Outcome(status=RunStatus.done))
    with factory() as db:
        listed = runs.list_runs(db, alice, 10)
        assert [r.id for r in listed] == ids[::-1]
        page = runs.list_runs(db, alice, 1, before=listed[0].created_at)
        assert [r.id for r in page] == [ids[1]]
        assert runs.list_runs(db, alice, 10, before=listed[-1].created_at) == []


def test_active_limit_per_user(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    created = [new_run(factory, alice) for _ in range(runs.ACTIVE_PER_USER)]
    with pytest.raises(runs.ActiveRunsError):
        new_run(factory, alice)
    new_run(factory, bob)  # Bob is not limited by Alice's runs
    cancel(factory, alice, created[0])
    new_run(factory, alice)  # one ended: room again


def test_active_limit_holds_under_concurrent_starts(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    barrier = threading.Barrier(8)
    outcomes: list[str] = []
    lock = threading.Lock()

    def start() -> None:
        barrier.wait()
        try:
            new_run(factory, alice)
            result = "ok"
        except runs.ActiveRunsError:
            result = "limited"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=start) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("ok") == runs.ACTIVE_PER_USER
    with factory() as db:
        assert runs.count_active(db, alice) == runs.ACTIVE_PER_USER


def test_fail_queued(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    run_id = new_run(factory, alice)
    with factory() as db:
        assert runs.fail_queued(db, run_id, runs.QUEUE_DOWN, now())
        db.commit()
    row = run_row(factory, run_id)
    assert row.status is RunStatus.failed
    assert row.error == runs.QUEUE_DOWN
    with factory() as db:
        assert not runs.fail_queued(db, run_id, runs.QUEUE_DOWN, now())


def test_reap_orphans_fails_only_running_runs(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    queued = new_run(factory, alice)
    running = new_run(factory, alice)
    claim(factory, running)
    done = new_run(factory, alice)
    claim(factory, done)
    finish(factory, done, runs.Outcome(status=RunStatus.done))
    with factory() as db:
        assert runs.reap_orphans(db, now()) == 1
        db.commit()
    assert run_row(factory, running).status is RunStatus.failed
    assert run_row(factory, running).error == runs.INTERRUPTED
    assert events(factory, running)[-1] == (
        "status",
        {"status": "failed", "error": runs.INTERRUPTED},
    )
    assert run_row(factory, queued).status is RunStatus.queued
    assert run_row(factory, done).status is RunStatus.done


def test_reap_stale(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    fresh_running = new_run(factory, alice)
    claim(factory, fresh_running)
    old_running = new_run(factory, alice)
    claim(factory, old_running)
    old_queued = new_run(factory, alice)
    fresh_queued = new_run(factory, alice)
    with factory() as db, db.begin():
        db.execute(
            sa.update(AgentRun)
            .where(AgentRun.id == old_running)
            .values(started_at=now() - timedelta(hours=1))
        )
        db.execute(
            sa.update(AgentRun)
            .where(AgentRun.id == old_queued)
            .values(created_at=now() - timedelta(days=1))
        )
    with factory() as db:
        count = runs.reap_stale(db, now(), timedelta(minutes=15), timedelta(hours=6))
        db.commit()
    assert count == 2
    assert run_row(factory, old_running).error == runs.STALE
    assert run_row(factory, old_queued).error == runs.LOST
    assert run_row(factory, fresh_running).status is RunStatus.running
    assert run_row(factory, fresh_queued).status is RunStatus.queued


def test_events_after_and_unique_seq(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    run_id = new_run(factory, alice)
    with factory() as db:
        for seq in range(2, 6):
            runs.append_event(db, run_id, seq, "text", {"text": str(seq)}, now())
        db.commit()
        assert runs.next_seq(db, run_id) == 6
        assert [e.seq for e in runs.events_after(db, run_id, 3)] == [4, 5]
        assert [e.seq for e in runs.events_after(db, run_id, 0, limit=2)] == [1, 2]
        assert runs.events_after(db, run_id, 5) == []
        assert runs.next_seq(db, uuid.uuid4()) == 1
    with pytest.raises(IntegrityError), factory() as db:
        runs.append_event(db, run_id, 3, "text", {}, now())


def test_deleting_a_job_keeps_the_run(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    from app.jobs.models import Job

    job = add_job(factory, alice)
    run_id = new_run(factory, alice, job_id=job)
    with factory() as db, db.begin():
        db.execute(sa.delete(Job).where(Job.id == job))
    assert run_row(factory, run_id).job_id is None
