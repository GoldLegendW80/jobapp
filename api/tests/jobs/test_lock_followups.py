"""The follow ups of the jobs locks (issue #166, item 3 of #341), against real Postgres with two
sessions: the row lock is FOR NO KEY UPDATE, so a row that references a held posting is inserted
without waiting; the rescore skips a held row at once and scores it on the next run; a statement
timeout (57014) and a deadlock (40P01) answer 503 with Retry-After instead of 500; every multi
row lock takes its rows in one order (jobs by id, then proposals by id).

The waits here are bounded by the lock timeouts the tests set (300 ms) and by the watchdog of
`holding`; no test sleeps."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import service
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from tests.agents.test_proposals_service import STATUS, propose
from tests.identity.api_helpers import Api
from tests.jobs.conftest import NOW
from tests.jobs.test_api import Signed, sign_in
from tests.jobs.test_edit_api import POSTING, bulk, give_skills, stored
from tests.jobs.test_status_lock import (
    SHORT_LOCK_MS,
    _release_after,
    _rollback_quietly,
    holding,
    join_all,
    new_job,
    run_in_thread,
    short_lock,  # noqa: F401 (a fixture)
    status_of,
    wait_until_blocked,
)

pytestmark = pytest.mark.usefixtures("short_lock")


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "alice@example.org")


def insert_event(db: Session, owner: uuid.UUID, job_id: uuid.UUID) -> None:
    """A row that references the posting: its foreign key check takes the posting's KEY SHARE."""
    db.add(JobEvent(job_id=job_id, user_id=owner, kind=JobEventKind.note, data={"to": "x"}, at=NOW))
    db.flush()


@contextmanager
def holding_for_update(factory: sessionmaker[Session], job_id: uuid.UUID) -> Iterator[None]:
    """Another connection holds the posting with the old, stronger FOR UPDATE: the control that
    shows the insert test can fail."""
    with factory() as holder:
        holder.execute(sa.select(Job.id).where(Job.id == job_id).with_for_update()).all()
        watchdog = _release_after(factory, holder.scalar(sa.text("select pg_backend_pid()")))
        try:
            yield
        finally:
            watchdog.cancel()
            _rollback_quietly(holder)


# --- FOR NO KEY UPDATE (#341 item 3) --------------------------------------------------------


def test_a_row_referencing_a_held_posting_is_inserted_without_waiting(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    job_id = new_job(factory, alice.id)
    with holding(factory, alice.id, job_id), factory() as db:
        db.execute(sa.text(f"set local lock_timeout = {SHORT_LOCK_MS}"))
        insert_event(db, alice.id, job_id)  # would raise 55P03 behind a FOR UPDATE
        db.commit()


def test_the_control_a_for_update_holder_blocks_that_insert(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    job_id = new_job(factory, alice.id)
    with holding_for_update(factory, job_id), factory() as db:
        db.execute(sa.text(f"set local lock_timeout = {SHORT_LOCK_MS}"))
        with pytest.raises(DBAPIError) as raised:
            insert_event(db, alice.id, job_id)
        assert service.is_lock_timeout(raised.value)


def test_a_held_posting_still_excludes_a_second_locker_and_a_plain_update(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    """The weaker lock still serialises every writer of the row itself."""
    job_id = new_job(factory, alice.id)
    with holding(factory, alice.id, job_id):
        with factory() as db, pytest.raises(service.JobBusyError):
            service.lock_job(db, alice.id, job_id)
        with factory() as db:
            db.execute(sa.text(f"set local lock_timeout = {SHORT_LOCK_MS}"))
            with pytest.raises(DBAPIError) as raised:
                db.execute(sa.update(Job).where(Job.id == job_id).values(notes="other"))
            assert service.is_lock_timeout(raised.value)


# --- rescore: a held row is skipped, not waited for (item 1) -------------------------------------


def rescore(api: Api, who: Signed, **body: Any) -> httpx.Response:
    return api.post("/api/jobs/rescore", token=who.token, json=body)


def test_rescore_skips_a_held_row_and_the_next_rescore_scores_it(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    give_skills(factory, alice.id)
    ids = sorted(new_job(factory, alice.id, posting_text=POSTING, fit=None) for _ in range(3))
    with holding(factory, alice.id, ids[1]):
        response = rescore(api, alice)
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["scored"], body["skipped_busy"], body["remaining"]) == (2, 1, 0)
        assert stored(factory, ids[1]).fit is None  # kept as it was, nothing lost
        assert stored(factory, ids[0]).fit is not None
        assert stored(factory, ids[2]).fit is not None
    again = rescore(api, alice).json()
    assert (again["scored"], again["skipped_busy"]) == (3, 0)
    assert stored(factory, ids[1]).fit is not None


def test_rescore_batch_counts_the_held_row_in_its_window_and_resumes_after_it(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    """The window is the ids read before the lock: a held row inside it is counted once and the
    cursor moves past it, so the batch neither stops nor rescans it."""
    give_skills(factory, alice.id)
    ids = sorted(new_job(factory, alice.id, posting_text=POSTING, fit=None) for _ in range(4))
    outcome = service.RescoreOutcome()
    with holding(factory, alice.id, ids[0]), factory() as db:
        skills = service.user_skills(db, alice.id)
        assert skills is not None
        looked = service.rescore_batch(
            db,
            alice.id,
            skills,
            NOW,
            ids=None,
            after=None,
            limit=2,
            overwrite_manual=False,
            outcome=outcome,
        )
        db.commit()
    assert (looked, outcome.scored, outcome.skipped_busy, outcome.last_id) == (2, 1, 1, ids[1])


# --- 57014 and 40P01 answer 503 with a retry hint (items 2 and 3) ------------------------------


def _statement_timeout_instead(db: Session) -> None:
    """The lock wait of the route ends at a 300 ms statement timeout, as a bulk over many rows
    waited on one after the other reaches the real 15 s one."""
    db.execute(sa.text("select set_config('lock_timeout', '0', true)"))
    db.execute(sa.text("select set_config('statement_timeout', '300ms', true)"))


def assert_retry(response: httpx.Response) -> None:
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": service.RETRY_BUSY}
    assert response.headers["Retry-After"] == str(service.RETRY_AFTER_S)
    assert response.headers["Cache-Control"] == "no-store"


def test_a_statement_timeout_in_a_bulk_answers_503_with_retry_after(
    api: Api, alice: Signed, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = [new_job(factory, alice.id) for _ in range(2)]
    monkeypatch.setattr(service, "_begin_locking", _statement_timeout_instead)
    with holding(factory, alice.id, ids[1]):
        assert_retry(bulk(api, alice, ids, status="closed"))
    assert {status_of(factory, i) for i in ids} == {JobStatus.found}  # rolled back whole


def test_a_deadlock_postgres_breaks_answers_503_and_changes_nothing(
    api: Api, alice: Signed, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real deadlock: the bulk locks the first row then waits for the second, held by another
    transaction that then asks for the first (an order no path of the app uses). The other side
    waits longer before it checks for a deadlock, so Postgres breaks the bulk's transaction."""
    # The bulk must still be waiting when Postgres checks for a deadlock (after 1 s).
    monkeypatch.setattr(service, "LOCK_TIMEOUT_MS", 5_000)
    ids = sorted(new_job(factory, alice.id) for _ in range(2))
    errors: list[BaseException] = []
    answers: list[httpx.Response] = []
    with factory() as other:
        other.execute(sa.text("set local deadlock_timeout = '10s'"))
        assert service.lock_job(other, alice.id, ids[1]) is not None
        watchdog = _release_after(factory, other.scalar(sa.text("select pg_backend_pid()")))
        try:
            request = run_in_thread(
                errors, lambda: answers.append(bulk(api, alice, ids, status="closed"))
            )
            wait_until_blocked(factory)
            other.execute(sa.text("set local lock_timeout = 0"))
            other.execute(sa.select(Job.id).where(Job.id == ids[0]).with_for_update()).all()
            join_all(request)
        finally:
            watchdog.cancel()
            _rollback_quietly(other)
    assert errors == []
    assert_retry(answers[0])
    assert {status_of(factory, i) for i in ids} == {JobStatus.found}


@pytest.mark.parametrize("sqlstate", ["57014", "40P01"])
def test_only_these_two_states_are_retryable(sqlstate: str) -> None:
    class OrigError(Exception):
        def __init__(self, code: str) -> None:
            super().__init__(code)
            self.sqlstate = code

    assert service.is_retryable(DBAPIError("SELECT 1", {}, OrigError(sqlstate)))
    for other in ("55P03", "23505", "22012", "40001", None):
        assert not service.is_retryable(DBAPIError("SELECT 1", {}, OrigError(other)))  # type: ignore[arg-type]
    assert not service.is_retryable(ValueError(sqlstate))


# --- one lock order: jobs by id, then proposals by id (item 3) -----------------------------------


def _locked_ids_of(statements: list[str]) -> list[str]:
    return [s for s in statements if "FOR NO KEY UPDATE" in s or "FOR UPDATE" in s]


def test_every_multi_row_lock_of_the_jobs_service_is_ordered_by_id(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    """The statements that lock several rows (lock_jobs, undo_bulk, rescore_batch and the
    proposals a status change supersedes) all carry ORDER BY id, and the jobs are locked before
    the proposals."""
    give_skills(factory, alice.id)
    ids = [new_job(factory, alice.id, posting_text=POSTING) for _ in range(3)]
    for job_id in ids:
        for _ in range(2):  # two pending each: the supersede locks several proposal rows
            propose(factory, alice.id, job_id, STATUS, {"status": "applied", "reason": "r"})
    statements: list[str] = []
    with factory() as db:
        engine = db.get_bind()

        def record(_c: Any, _cur: Any, statement: str, *_: Any) -> None:
            statements.append(statement)

        sa.event.listen(engine, "before_cursor_execute", record)
        try:
            outcome = service.bulk_update(db, alice.id, ids, NOW, status=JobStatus.closed)
            service.undo_bulk(db, alice.id, outcome.undo, NOW)
            skills = service.user_skills(db, alice.id)
            assert skills is not None
            service.rescore_batch(
                db,
                alice.id,
                skills,
                NOW,
                ids=ids,
                after=None,
                limit=10,
                overwrite_manual=False,
                outcome=service.RescoreOutcome(),
            )
            db.rollback()
        finally:
            sa.event.remove(engine, "before_cursor_execute", record)
    locks = _locked_ids_of(statements)
    assert len(locks) >= 4
    for statement in locks:
        assert "ORDER BY" in statement, statement
        assert ".id" in statement.split("ORDER BY")[1], statement
        assert "FOR NO KEY UPDATE" in statement, statement
    first_proposal = next(i for i, s in enumerate(locks) if "FROM proposals" in s)
    assert "FROM jobs" in locks[0]
    assert first_proposal > 0
