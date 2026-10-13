"""J8 (issue #122): the row lock of a job and what stands on it, on real Postgres with real
connections. `set_status` refuses a row that was not read under the lock in this transaction;
two changes to one posting queue up and the second sees the first; a lock wait longer than the
lock timeout answers 409 "This job is being changed. Try again." (not a hung request, not a 500)
and leaves nothing on the pooled connection; another user's row can be neither locked nor changed.

The 5 s lock timeout is `service.LOCK_TIMEOUT_MS`: the tests that wait for it set it to 300 ms
(monkeypatch), so each runs in about half a second."""

import contextlib
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals
from app.agents.models import Proposal, ProposalStatus
from app.jobs import service
from app.jobs.models import Job, JobEvent, JobStatus
from tests.agents.run_helpers import add_job
from tests.agents.test_proposals_api import accept as accept_route
from tests.agents.test_proposals_service import STATUS, propose
from tests.agents.test_proposals_service import row as proposal_row
from tests.identity.api_helpers import Api
from tests.jobs.conftest import NOW
from tests.jobs.test_api import Signed, seed, sign_in
from tests.jobs.test_edit_api import bulk, patch

WAIT_S = 10
SHORT_LOCK_MS = 300


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "alice@example.org")


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "bob@example.org")


@pytest.fixture
def short_lock(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(service, "LOCK_TIMEOUT_MS", SHORT_LOCK_MS)
    return SHORT_LOCK_MS


def new_job(factory: sessionmaker[Session], owner: uuid.UUID, **fields: Any) -> uuid.UUID:
    unique = uuid.uuid4().hex[:8]
    fields.setdefault("slug", f"job-{unique}")
    fields.setdefault("title", f"Role {unique}")
    fields.setdefault("url", f"https://jobs.example/{unique}")
    return seed(factory, owner, [fields])[0].id


def status_of(factory: sessionmaker[Session], job_id: uuid.UUID) -> JobStatus:
    with factory() as db:
        return db.scalars(sa.select(Job.status).where(Job.id == job_id)).one()


def status_events(factory: sessionmaker[Session], job_id: uuid.UUID) -> list[dict[str, Any]]:
    with factory() as db:
        rows = db.scalars(
            sa.select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.kind == "status")
            .order_by(JobEvent.id)
        )
        return [event.data for event in rows]


def _release_after(factory: sessionmaker[Session], pid: int) -> threading.Timer:
    """A watchdog for a holder: if its block outlasts WAIT_S, its backend is terminated, so a
    test that depends on the lock timeout fails on its assertions (the waiter got through)
    instead of hanging for ever behind a lock the timeout should have cut."""

    def terminate() -> None:
        with factory() as db:
            db.execute(sa.text("select pg_terminate_backend(:pid)"), {"pid": pid})
            db.commit()

    timer = threading.Timer(WAIT_S, terminate)
    timer.daemon = True
    timer.start()
    return timer


def _rollback_quietly(holder: Session) -> None:
    with contextlib.suppress(DBAPIError):  # the watchdog may have ended the backend
        holder.rollback()


@contextmanager
def holding(factory: sessionmaker[Session], owner: uuid.UUID, job_id: uuid.UUID) -> Iterator[Job]:
    """Another connection holds the job's row lock, in an open transaction, until the block ends
    (or for WAIT_S at most)."""
    with factory() as holder:
        job = service.lock_job(holder, owner, job_id)
        assert job is not None
        watchdog = _release_after(factory, holder.scalar(sa.text("select pg_backend_pid()")))
        try:
            yield job
        finally:
            watchdog.cancel()
            _rollback_quietly(holder)


@contextmanager
def holding_proposal(factory: sessionmaker[Session], proposal_id: uuid.UUID) -> Iterator[None]:
    """Another connection holds a proposal's row lock until the block ends."""
    with factory() as holder:
        holder.execute(sa.select(Proposal).where(Proposal.id == proposal_id).with_for_update())
        watchdog = _release_after(factory, holder.scalar(sa.text("select pg_backend_pid()")))
        try:
            yield
        finally:
            watchdog.cancel()
            _rollback_quietly(holder)


def row_is_locked(factory: sessionmaker[Session], job_id: uuid.UUID) -> bool:
    """Whether another connection holds the job's row lock, asked of Postgres on its own: a raw
    FOR UPDATE with its own 300 ms lock timeout, independent of the code under test."""
    with factory() as db:
        db.execute(sa.text("set local lock_timeout = 300"))
        try:
            db.execute(sa.select(Job.id).where(Job.id == job_id).with_for_update()).all()
        except DBAPIError as exc:
            if not service.is_lock_timeout(exc):
                raise
            return True
        return False


def run_in_thread(errors: list[BaseException], work: Callable[[], None]) -> threading.Thread:
    def guarded() -> None:
        try:
            work()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=guarded, daemon=True)  # a hung one never holds the run
    thread.start()
    return thread


def wait_until_blocked(factory: sessionmaker[Session]) -> None:
    """Return once Postgres reports a backend waiting for a lock (polled up to WAIT_S): the
    proof that a second transaction is queued behind the first, whatever the machine's speed."""
    deadline = time.monotonic() + WAIT_S
    query = sa.text(
        "select count(*) from pg_stat_activity "
        "where datname = current_database() and wait_event_type = 'Lock'"
    )
    while time.monotonic() < deadline:
        with factory() as db:
            if db.scalar(query):
                return
        threading.Event().wait(0.01)
    raise AssertionError("no transaction ever waited for a lock")


def join_all(*threads: threading.Thread) -> None:
    for thread in threads:
        thread.join(WAIT_S)
    assert not any(thread.is_alive() for thread in threads)


# --- set_status refuses a row not read under the lock ----------------------------------------


def test_set_status_refuses_a_row_read_without_the_lock(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    """The guard test: with the check removed from set_status this test fails (the status would
    change and an event would be written)."""
    job_id = new_job(factory, alice.id)
    with factory() as db:
        job = service.get_job(db, alice.id, job_id)  # a plain read, no FOR UPDATE
        assert job is not None
        with pytest.raises(service.UnlockedJobError):
            service.set_status(db, job, JobStatus.applied, NOW)
        assert job.status is JobStatus.found  # nothing was touched
        db.commit()
    assert status_of(factory, job_id) is JobStatus.found
    assert status_events(factory, job_id) == []


def test_set_status_refuses_even_a_no_op_and_a_job_that_is_not_in_the_database(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    job_id = new_job(factory, alice.id)
    with factory() as db:
        job = service.get_job(db, alice.id, job_id)
        assert job is not None
        with pytest.raises(service.UnlockedJobError):
            service.set_status(db, job, job.status, NOW)  # same status: still a missing lock
        new = Job(user_id=alice.id, company="x", title="y")
        with pytest.raises(service.UnlockedJobError):
            service.set_status(db, new, JobStatus.applied, NOW)


@pytest.mark.parametrize("how", ["lock_job", "lock_jobs", "undo_bulk"])
def test_each_lock_function_marks_the_rows_it_returns(
    factory: sessionmaker[Session], alice: Signed, how: str
) -> None:
    mine = new_job(factory, alice.id, slug="mine")
    other = new_job(factory, alice.id, slug="other")
    with factory() as db, db.begin():
        outcome = service.bulk_update(db, alice.id, [mine], NOW, status=JobStatus.closed)
    with factory() as db:
        if how == "lock_job":
            job = service.lock_job(db, alice.id, mine)
        elif how == "lock_jobs":
            (job,) = service.lock_jobs(db, alice.id, [mine])
        else:
            undone = service.undo_bulk(db, alice.id, outcome.undo, NOW)
            assert undone.restored == [mine]
            job = db.get(Job, mine)
        assert job is not None
        service.set_status(db, job, JobStatus.applied, NOW)
        # A job of the same user that this transaction did not lock is still refused.
        unlocked = service.get_job(db, alice.id, other)
        assert unlocked is not None
        with pytest.raises(service.UnlockedJobError):
            service.set_status(db, unlocked, JobStatus.applied, NOW)
        db.commit()
    assert status_of(factory, mine) is JobStatus.applied
    assert status_of(factory, other) is JobStatus.found


def test_the_mark_does_not_survive_a_commit(factory: sessionmaker[Session], alice: Signed) -> None:
    """A row locked in one transaction and changed in the next is refused."""
    job_id = new_job(factory, alice.id)
    with factory() as db:
        job = service.lock_job(db, alice.id, job_id)
        assert job is not None
        assert service.set_status(db, job, JobStatus.shortlisted, NOW) is True
        db.commit()  # the lock is gone, so is the mark; the session and the object live on
        with pytest.raises(service.UnlockedJobError):
            service.set_status(db, job, JobStatus.applied, NOW)
        # Locked again in the new transaction: accepted.
        again = service.lock_job(db, alice.id, job_id)
        assert again is not None
        service.set_status(db, again, JobStatus.applied, NOW)
        db.commit()
    assert status_of(factory, job_id) is JobStatus.applied


def test_the_mark_does_not_survive_a_rollback_or_a_closed_session(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    job_id = new_job(factory, alice.id)
    with factory() as db:
        job = service.lock_job(db, alice.id, job_id)
        assert job is not None
        db.rollback()
        with pytest.raises(service.UnlockedJobError):
            service.set_status(db, job, JobStatus.applied, NOW)
        job = service.lock_job(db, alice.id, job_id)
        assert job is not None
        db.close()  # the session goes back to its pool and may be used again
        with pytest.raises(service.UnlockedJobError):
            service.set_status(db, job, JobStatus.applied, NOW)
    assert status_of(factory, job_id) is JobStatus.found


def test_a_row_locked_in_a_savepoint_that_is_rolled_back_is_refused(
    factory: sessionmaker[Session], alice: Signed, short_lock: int
) -> None:
    """Postgres releases the locks taken inside a rolled back savepoint, so the mark goes too."""
    job_id = new_job(factory, alice.id)
    with factory() as db:
        savepoint = db.begin_nested()
        job = service.lock_job(db, alice.id, job_id)
        assert job is not None
        service.set_status(db, job, JobStatus.shortlisted, NOW)  # accepted while it is held
        savepoint.rollback()
        with pytest.raises(service.UnlockedJobError):
            service.set_status(db, job, JobStatus.applied, NOW)
        assert not row_is_locked(factory, job_id)  # really free: the refusal matches the database
        db.rollback()
    assert status_of(factory, job_id) is JobStatus.found


def test_a_row_locked_before_a_savepoint_stays_accepted_when_the_savepoint_rolls_back(
    factory: sessionmaker[Session], alice: Signed, short_lock: int
) -> None:
    """The other direction: the savepoint's rollback must not drop a lock it never took."""
    before = new_job(factory, alice.id, slug="before")
    inside = new_job(factory, alice.id, slug="inside")
    with factory() as db:
        job = service.lock_job(db, alice.id, before)
        assert job is not None
        savepoint = db.begin_nested()
        taken_inside = service.lock_job(db, alice.id, inside)
        assert taken_inside is not None
        savepoint.rollback()
        assert service.set_status(db, job, JobStatus.applied, NOW) is True  # still locked
        with pytest.raises(service.UnlockedJobError):  # locked inside: released by Postgres
            service.set_status(db, taken_inside, JobStatus.applied, NOW)
        assert row_is_locked(factory, before)  # and really still held
        assert not row_is_locked(factory, inside)
        db.commit()
    assert status_of(factory, before) is JobStatus.applied
    assert status_of(factory, inside) is JobStatus.found


def test_a_row_locked_in_a_released_savepoint_is_accepted_until_the_transaction_ends(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    job_id = new_job(factory, alice.id)
    with factory() as db:
        with db.begin_nested():
            with db.begin_nested():
                job = service.lock_job(db, alice.id, job_id)
                assert job is not None
            assert service.set_status(db, job, JobStatus.shortlisted, NOW) is True  # one level up
        assert service.set_status(db, job, JobStatus.applied, NOW) is True  # in the transaction
        db.rollback()
        with pytest.raises(service.UnlockedJobError):
            service.set_status(db, job, JobStatus.closed, NOW)
    assert status_of(factory, job_id) is JobStatus.found


def test_the_mark_belongs_to_one_session_and_follows_the_row_not_the_object(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    job_id = new_job(factory, alice.id)
    with factory() as locker, factory() as reader:
        job = service.lock_job(locker, alice.id, job_id)
        assert job is not None
        # Another session reads the same row (a plain read does not wait for a FOR UPDATE) and
        # shares nothing with the locker: it is refused.
        theirs = service.get_job(reader, alice.id, job_id)
        assert theirs is not None
        # Were the guard gone, the refused UPDATE would queue behind the locker for ever (this
        # engine has no statement timeout): bound it, so the regression is a red test.
        reader.execute(sa.text("set local lock_timeout = 1000"))
        with pytest.raises(service.UnlockedJobError):
            service.set_status(reader, theirs, JobStatus.applied, NOW)
        # The locker's row reloaded as a new Python object is the same row: accepted.
        locker.expunge(job)
        reloaded = locker.get(Job, job_id)
        assert reloaded is not None
        assert reloaded is not job
        assert service.set_status(locker, reloaded, JobStatus.applied, NOW) is True
        locker.commit()
    assert status_of(factory, job_id) is JobStatus.applied


# --- two real connections ---------------------------------------------------------------------


def test_two_transactions_changing_one_posting_the_second_waits_and_sees_the_first(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    job_id = new_job(factory, alice.id)
    first_has_lock = threading.Event()
    first_may_commit = threading.Event()
    second_done = threading.Event()
    seen: dict[str, Any] = {}
    errors: list[BaseException] = []

    def first() -> None:
        with factory() as db, db.begin():
            job = service.lock_job(db, alice.id, job_id)
            assert job is not None
            service.set_status(db, job, JobStatus.shortlisted, NOW)
            first_has_lock.set()
            assert first_may_commit.wait(WAIT_S)

    def second() -> None:
        assert first_has_lock.wait(WAIT_S)
        with factory() as db, db.begin():
            job = service.lock_job(db, alice.id, job_id)  # waits for the first
            assert job is not None
            seen["status_when_locked"] = job.status
            service.set_status(db, job, JobStatus.applied, NOW)
        second_done.set()

    threads = [run_in_thread(errors, first), run_in_thread(errors, second)]
    assert first_has_lock.wait(WAIT_S)
    wait_until_blocked(factory)  # Postgres shows the second waiting for the first's row
    assert not second_done.is_set()
    first_may_commit.set()
    join_all(*threads)
    assert errors == []
    assert seen["status_when_locked"] is JobStatus.shortlisted
    assert status_of(factory, job_id) is JobStatus.applied
    assert status_events(factory, job_id) == [
        {"from": "found", "to": "shortlisted"},
        {"from": "shortlisted", "to": "applied"},
    ]


@pytest.mark.parametrize("holder", ["bulk", "single"])
def test_a_bulk_change_racing_a_single_change_on_one_of_its_rows(
    factory: sessionmaker[Session], alice: Signed, holder: str
) -> None:
    """Whoever locks first goes first; the other waits, then works from the committed row, with
    the events of both in order and no deadlock (the bulk locks its rows in id order)."""
    ids = [new_job(factory, alice.id, slug=f"job-{i}") for i in range(3)]
    target = ids[1]
    holds = threading.Event()
    may_commit = threading.Event()
    waiter_done = threading.Event()
    errors: list[BaseException] = []

    def hold_then_commit(work: Callable[[Session], None]) -> None:
        with factory() as db, db.begin():
            work(db)
            holds.set()
            assert may_commit.wait(WAIT_S)

    def bulk_change(db: Session) -> None:
        service.bulk_update(db, alice.id, ids, NOW, status=JobStatus.closed)

    def single_change(db: Session, status: JobStatus) -> None:
        job = service.lock_job(db, alice.id, target)
        assert job is not None
        service.set_status(db, job, status, NOW)

    def wait_then_change(work: Callable[[Session], None]) -> None:
        assert holds.wait(WAIT_S)
        with factory() as db, db.begin():
            work(db)
        waiter_done.set()

    if holder == "bulk":
        first = run_in_thread(errors, lambda: hold_then_commit(bulk_change))
        second = run_in_thread(
            errors, lambda: wait_then_change(lambda db: single_change(db, JobStatus.applied))
        )
        expected = [
            {"from": "found", "to": "closed", "via": "bulk"},
            {"from": "closed", "to": "applied"},
        ]
        final = JobStatus.applied
    else:
        first = run_in_thread(
            errors, lambda: hold_then_commit(lambda db: single_change(db, JobStatus.applied))
        )
        second = run_in_thread(errors, lambda: wait_then_change(bulk_change))
        expected = [
            {"from": "found", "to": "applied"},
            {"from": "applied", "to": "closed", "via": "bulk"},
        ]
        final = JobStatus.closed
    assert holds.wait(WAIT_S)
    wait_until_blocked(factory)
    assert not waiter_done.is_set()
    may_commit.set()
    join_all(first, second)
    assert errors == []
    assert status_of(factory, target) is final
    assert status_events(factory, target) == expected
    # The other two rows of the bulk are closed once, whatever the order.
    assert {status_of(factory, i) for i in (ids[0], ids[2])} == {JobStatus.closed}


@pytest.mark.parametrize("proposed", ["before", "during"])
def test_an_accept_racing_a_manual_change(
    factory: sessionmaker[Session], alice: Signed, proposed: str
) -> None:
    """The manual change holds the job; the accept waits for it, then finds the status moved.
    A proposal that was pending when the manual change ran is superseded by it ("The job's status
    was changed."). One made from the old status while the manual change was still open (an agent
    run that read the job before it) survives that, and the accept turns it invalid with "The
    job's status changed since this was proposed.", as before the lock timeout existed."""
    job_id = add_job(factory, alice.id)
    payload = {"status": "skipped", "from": "found", "reason": "r"}
    pids = [propose(factory, alice.id, job_id, STATUS, payload)] if proposed == "before" else []
    holds = threading.Event()
    may_commit = threading.Event()
    accept_done = threading.Event()
    outcome: dict[str, Any] = {}
    errors: list[BaseException] = []

    def manual() -> None:
        with factory() as db, db.begin():
            job = service.lock_job(db, alice.id, job_id)
            assert job is not None
            service.set_status(db, job, JobStatus.shortlisted, NOW)
            holds.set()
            assert may_commit.wait(WAIT_S)

    def accept() -> None:
        assert holds.wait(WAIT_S)
        with factory() as db, db.begin():
            try:
                proposals.accept(db, alice.id, pids[0], NOW)
            except proposals.NotPendingError as exc:
                outcome["not_pending"] = exc.proposal.status
            except proposals.StatusChangedError as exc:
                outcome["invalid"] = exc.proposal.reason
        accept_done.set()

    manual_thread = run_in_thread(errors, manual)
    assert holds.wait(WAIT_S)
    if proposed == "during":
        pids.append(propose(factory, alice.id, job_id, STATUS, payload))
    accept_thread = run_in_thread(errors, accept)
    wait_until_blocked(factory)  # the accept waits for the job while the manual change is open
    assert not accept_done.is_set()
    may_commit.set()
    join_all(manual_thread, accept_thread)
    assert errors == []
    stored = proposal_row(factory, pids[0])
    if proposed == "before":
        assert outcome == {"not_pending": ProposalStatus.superseded}
        assert stored.status is ProposalStatus.superseded
        assert stored.reason == service.STATUS_MOVED
    else:
        assert outcome == {"invalid": proposals.STATUS_CHANGED}
        assert stored.status is ProposalStatus.invalid
        assert stored.reason == proposals.STATUS_CHANGED
    assert status_of(factory, job_id) is JobStatus.shortlisted  # the accept changed nothing
    assert status_events(factory, job_id) == [{"from": "found", "to": "shortlisted"}]


# --- the lock timeout -------------------------------------------------------------------------


def test_a_lock_wait_over_the_timeout_raises_busy_after_about_the_timeout(
    factory: sessionmaker[Session], alice: Signed, short_lock: int
) -> None:
    job_id = new_job(factory, alice.id)
    with holding(factory, alice.id, job_id), factory() as db:
        started = time.monotonic()
        with pytest.raises(service.JobBusyError):
            service.lock_job(db, alice.id, job_id)
        waited = time.monotonic() - started
        # It waited the timeout, not the 15 s statement timeout: a bound that tells 0.3 s from a
        # hung request and that a loaded machine cannot reach.
        assert short_lock / 1000 * 0.8 < waited < 5
        db.rollback()
        with pytest.raises(service.JobBusyError):
            service.lock_jobs(db, alice.id, [job_id])
        db.rollback()
        with pytest.raises(service.JobBusyError):
            service.bulk_update(db, alice.id, [job_id], NOW, status=JobStatus.closed)
        db.rollback()


def test_a_lock_wait_over_the_timeout_answers_409_on_each_route(
    api: Api, alice: Signed, factory: sessionmaker[Session], short_lock: int
) -> None:
    job_id = new_job(factory, alice.id)
    first = bulk(api, alice, [job_id], status="applied")
    assert first.status_code == 200, first.text
    undo_token = first.json()["undo_token"]

    with holding(factory, alice.id, job_id):
        # The outcome is the test: with the lock timeout at 300 ms a request can only answer 409
        # by timing out on the lock. No wall clock here, which load would move.
        for response in (
            patch(api, alice, job_id, status="interview"),
            bulk(api, alice, [job_id], status="interview"),
            api.post("/api/jobs/bulk/undo", token=alice.token, json={"token": undo_token}),
        ):
            assert response.status_code == 409, response.text
            assert response.json() == {"detail": "This job is being changed. Try again."}
    assert status_of(factory, job_id) is JobStatus.applied
    # Released: the same calls now work, so nothing was left half done or poisoned.
    assert patch(api, alice, job_id, status="interview").status_code == 200
    assert (
        api.post("/api/jobs/bulk/undo", token=alice.token, json={"token": undo_token}).status_code
        == 409  # the job changed since the bulk: "nothing to undo", the old 409, not the busy one
    )


def test_a_bulk_change_with_one_row_held_answers_409_and_changes_none(
    api: Api, alice: Signed, factory: sessionmaker[Session], short_lock: int
) -> None:
    ids = [new_job(factory, alice.id, slug=f"job-{i}") for i in range(3)]
    with holding(factory, alice.id, ids[1]):
        response = bulk(api, alice, ids, status="applied")
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": service.BUSY}
    assert {status_of(factory, i) for i in ids} == {JobStatus.found}
    assert bulk(api, alice, ids, status="applied").status_code == 200  # released: it works


def test_accept_on_a_busy_job_answers_409_and_the_proposal_stays_pending(
    api: Api, alice: Signed, factory: sessionmaker[Session], short_lock: int
) -> None:
    """The accept route locks the job: a lock wait over the timeout is the same 409 as the jobs
    routes give (the handler of the app), not a 500."""
    job_id = add_job(factory, alice.id)
    pid = propose(factory, alice.id, job_id)
    with holding(factory, alice.id, job_id):
        response = accept_route(api, alice.token, pid)
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": service.BUSY}
    assert proposal_row(factory, pid).status is ProposalStatus.pending
    assert accept_route(api, alice.token, pid).status_code == 200  # released: it goes through


def test_accept_with_the_proposal_row_held_answers_409_too(
    api: Api, alice: Signed, factory: sessionmaker[Session], short_lock: int
) -> None:
    """The other lock of that path: the proposal's own row, taken after the job's, under the same
    transaction local timeout. The error is a raw SQLSTATE 55P03, not a JobBusyError."""
    job_id = add_job(factory, alice.id)
    pid = propose(factory, alice.id, job_id)
    with holding_proposal(factory, pid):
        response = accept_route(api, alice.token, pid)
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": service.BUSY}
    assert proposal_row(factory, pid).status is ProposalStatus.pending
    assert accept_route(api, alice.token, pid).status_code == 200


def test_the_lock_timeout_is_left_on_no_pooled_connection(
    api: Api,
    alice: Signed,
    factory: sessionmaker[Session],
    one_connection: sa.Engine,
    short_lock: int,
) -> None:
    """The request that timed out and the one that did not both use the pool's one connection;
    afterwards it carries the server's default lock_timeout, not the transaction's."""
    job_id = new_job(factory, alice.id)

    def lock_timeout() -> str:
        with one_connection.connect() as connection:
            return str(connection.execute(sa.text("show lock_timeout")).scalar_one())

    assert patch(api, alice, job_id, status="interview").status_code == 200
    assert lock_timeout() == "0"
    with holding(factory, alice.id, job_id):
        assert patch(api, alice, job_id, status="applied").status_code == 409
    assert lock_timeout() == "0"
    assert patch(api, alice, job_id, status="applied").status_code == 200  # same connection


def test_only_a_lock_timeout_is_a_busy_answer(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    """A statement timeout (SQLSTATE 57014) or a division by zero is not mistaken for one."""
    with factory() as db:
        db.execute(sa.text("set local statement_timeout = 50"))
        with pytest.raises(DBAPIError) as raised:
            db.execute(sa.text("select pg_sleep(1)"))
        assert not service.is_lock_timeout(raised.value)
        db.rollback()
        with pytest.raises(DBAPIError) as other:
            db.execute(sa.text("select 1 / 0"))
        assert not service.is_lock_timeout(other.value)
        db.rollback()
    job_id = new_job(factory, alice.id)
    with holding(factory, alice.id, job_id), factory() as db:
        db.execute(sa.text("set local lock_timeout = 100"))
        with pytest.raises(DBAPIError) as locked:
            db.execute(sa.select(Job).where(Job.id == job_id).with_for_update())
        assert service.is_lock_timeout(locked.value)


# --- another user's row -----------------------------------------------------------------------


def test_user_b_cannot_lock_or_change_user_as_row(
    api: Api,
    alice: Signed,
    bob: Signed,
    factory: sessionmaker[Session],
    short_lock: int,
) -> None:
    job_id = new_job(factory, alice.id)
    with factory() as db:
        # B asking for A's row gets nothing, and set_status has nothing to be handed.
        assert service.lock_job(db, bob.id, job_id) is None
        with pytest.raises(service.MissingJobsError) as missing:
            service.lock_jobs(db, bob.id, [job_id])
        assert missing.value.missing == [job_id]
        db.rollback()
    # While A holds the row, B is not made to wait: A's row is not B's to queue for.
    with holding(factory, alice.id, job_id), factory() as db:
        assert service.lock_job(db, bob.id, job_id) is None  # a wait would raise JobBusyError
        db.rollback()
    for response in (
        patch(api, bob, job_id, status="applied"),
        bulk(api, bob, [job_id], status="applied"),
    ):
        assert response.status_code == 404, response.text
    assert status_of(factory, job_id) is JobStatus.found
    assert status_events(factory, job_id) == []
    # A's own undo list, handed to B: nothing is restored, and A's status is untouched.
    with factory() as db, db.begin():
        outcome = service.bulk_update(db, alice.id, [job_id], NOW, status=JobStatus.closed)
    assert outcome.undo
    with factory() as db:
        assert service.undo_bulk(db, bob.id, outcome.undo, NOW).restored == []
        db.rollback()
    assert status_of(factory, job_id) is JobStatus.closed  # B's undo restored nothing
    with factory() as db, db.begin():
        assert service.undo_bulk(db, alice.id, outcome.undo, NOW).restored == [job_id]
    assert status_of(factory, job_id) is JobStatus.found
