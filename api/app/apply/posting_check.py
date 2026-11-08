"""Before an application's run starts, is its posting still open (SPEC-apply, "The flow of one
application"; SPEC-jobs, "Liveness").

`worker.apply_fill` asks `before_fill` first, whatever the mode and the channel. An application
still `queued` whose posting is closed, or whose posting's own page now says it is
(app/jobs/liveness_check.py, a check younger than an hour reused, so a fill that waits for its
documents asks the site once an hour at most), ends `posting_closed` before the run takes its
documents, a lease or a model: the posting's row lock, then the application's, as the run's own
step 1 (`run._locked`), and only while the application is still `queued` and the posting still
closed. Whatever the check cannot tell lets the run go on, as does a lock held past its timeout
or any other error of the check, so a page the check cannot read never keeps an application
queued for the next tick.
The ended event carries the code; the page's own sentence is the posting's (`jobs.liveness`).
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import run, states
from app.apply.models import Application, State
from app.jobs import liveness_check
from app.jobs.models import JobStatus

logger = logging.getLogger(__name__)

CODE = "posting_closed"


def _queued(db: Session, application_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID] | None:
    row = db.execute(
        sa.select(Application.user_id, Application.posting_id, Application.state).where(
            Application.id == application_id
        )
    ).one_or_none()
    if row is None or row.state is not State.queued:
        return None
    return row.user_id, row.posting_id


def _end(db: Session, application_id: uuid.UUID, now: datetime) -> bool:
    found = run._locked(db, application_id)
    if found is None:
        db.rollback()
        return False
    application, posting = found
    if application.state is not State.queued or posting.status is not JobStatus.closed:
        db.rollback()
        return False
    states.move(application, State.posting_closed, now)
    application.error_code = CODE
    run._event(db, application, "ended", now, code=CODE)
    db.commit()
    return True


async def before_fill(
    checker: liveness_check.Checker,
    sessions: sessionmaker[Session],
    clock: Callable[[], datetime],
    application_id: uuid.UUID,
) -> bool:
    """True when the application ended `posting_closed` here: the run must not start."""

    def in_db[T](step: Callable[[Session], T]) -> T:
        with sessions() as db:
            return step(db)

    try:
        head = await asyncio.to_thread(in_db, lambda db: _queued(db, application_id))
        if head is None:
            return False
        outcome = await liveness_check.ensure(checker, sessions, *head, clock())
        if outcome is None or outcome.status is not JobStatus.closed:
            return False
        ended = await asyncio.to_thread(in_db, lambda db: _end(db, application_id, clock()))
    except Exception as exc:
        logger.warning(
            "apply.posting_not_checked",
            extra={"application_id": str(application_id), "error": type(exc).__name__},
        )
        return False
    if ended:
        logger.info("apply.posting_closed", extra={"application_id": str(application_id)})
    return ended
