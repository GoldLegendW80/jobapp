"""Two sessions driven step by step, without a sleep or a clock: one runs in a thread until
Postgres shows it waiting for a lock the other holds, then the test moves the other on."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.documents import freeze, store  # noqa: F401 (freeze registers its status hook)
from app.jobs import service as jobs_service
from app.jobs.models import JobStatus


def wait_until_blocked(engine: sa.Engine, threads: list[threading.Thread], count: int) -> None:
    """Return once `count` backends of this database wait for a lock another session holds
    (Postgres says so), or once a thread has ended. Polls; no sleep, no clock."""
    query = sa.text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    with engine.connect() as watcher:
        while all(t.is_alive() for t in threads) and watcher.execute(query).scalar_one() < count:
            watcher.rollback()


def in_thread(target: Any, outcome: dict[str, Any]) -> threading.Thread:
    """`target()` in a started thread: its result in `outcome["result"]`, its error in "error"."""

    def body() -> None:
        try:
            outcome["result"] = target()
        except Exception as exc:  # the main thread asserts on it
            outcome["error"] = exc

    thread = threading.Thread(target=body)
    thread.start()
    return thread


def mark_applied(
    factory: sessionmaker[Session], user_id: uuid.UUID, posting_id: uuid.UUID, at: datetime
) -> None:
    """The person marks the posting applied, as the jobs service does: the posting locked for
    update, then the freeze of D14 locks its CV and its letter."""
    with factory() as db:
        job = jobs_service.lock_job(db, user_id, posting_id)
        assert job is not None
        jobs_service.set_status(db, job, JobStatus.applied, at)
        store.commit(db)
