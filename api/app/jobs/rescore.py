"""The rescore that follows a change of the skills section (product audit, chapter 2, owner
answer 4 of 2026-11-05: "Postings are rescored by themselves after a skills change, with the
possible document cost said before saving"; SPEC-jobs, Scoring).

Whoever changes the skills section (the Skills tab, an import, an accepted proposal, a profile an
agent filled), the profile's write calls `on_sections` in its own transaction
(`profile.service.register_section_callback`): the person's `job_rescores` row asks for that
revision (`wanted_revision`). Nothing is scored there: the save stays as fast as before, and a
save that rolls back asks for nothing.

The worker's `rescore_tick` (a cron job of the sweeps queue every TICK_SECONDS) runs what is
asked, one person after the other, and never two rescores of one person at once:

1. `claim`: one UPDATE marks the row running (`running_revision`, the revision asked for, and
   `running_since`, the claim's time) only while nothing runs for the person, or while what runs
   started more than STALE_AFTER ago (a worker killed under it). Two ticks claim it once.
2. `run`: every posting of the person, RESCORE_BATCH at a time, each batch committed, with the
   skills read when it starts (so at least the claimed revision): a fit set by hand is kept
   (`overwrite_manual` false), a posting another transaction holds is tried once more at the
   end, then counted as busy. The fits that reached the documents threshold
   (`search.documents.min_fit`, 70 by default) and those that fell under it are counted.
3. `finish`: the claimed revision is done, the counts are the outcome, nothing runs. Only the
   claim that is still current finishes (a stale run that ends late changes nothing). A change
   saved during the run asked for a later revision: the next tick claims it, so it runs once more
   after it, and the changes saved meanwhile make that one run.

A profile whose skills section is missing or no longer validates scores nothing (outcome
`no_skills`); a run that raises is logged and done with `failed`, so it is not tried every tick
("Rescore all postings" in Jobs stays). The documents tick then writes the documents of the
postings that reached the threshold under its own rules (SPEC-documents, Automatic generation);
the Skills tab says that cost before the save (`POST /api/documents/spend/skills`).
Logs carry ids and counts, never a skill.
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import service
from app.jobs.models import JobRescore
from app.profile import service as profile_service
from app.profile.schemas import DocumentsGeneration

logger = logging.getLogger("app.jobs.rescore")

TICK_SECONDS = 10
# Over the cron job's timeout: a claim this old belongs to a run that is gone.
STALE_AFTER = timedelta(minutes=15)
TICK_TIMEOUT_S = 600
TICK_BUDGET_S = 300.0  # a tick claims no one new past this; the next tick goes on
RESCORE_BATCH = 100


def on_sections(
    db: Session, user_id: uuid.UUID, sections: frozenset[str], revision: int, now: datetime
) -> None:
    """The profile's callback: a change of `skills` asks for a rescore at `revision`."""
    if "skills" not in sections:
        return
    stmt = insert(JobRescore).values(user_id=user_id, wanted_revision=revision, wanted_at=now)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[JobRescore.user_id],
            set_={
                "wanted_revision": sa.func.greatest(
                    JobRescore.wanted_revision, stmt.excluded.wanted_revision
                ),
                "wanted_at": stmt.excluded.wanted_at,
            },
        )
    )


profile_service.register_section_callback(on_sections)


def _claimable(now: datetime) -> sa.ColumnElement[bool]:
    return sa.and_(
        JobRescore.wanted_revision > JobRescore.done_revision,
        sa.or_(JobRescore.running_since.is_(None), JobRescore.running_since < now - STALE_AFTER),
    )


def due(db: Session, now: datetime) -> list[uuid.UUID]:
    """The people with a rescore asked for and none running, the oldest ask first."""
    return list(
        db.scalars(
            sa.select(JobRescore.user_id)
            .where(_claimable(now))
            .order_by(JobRescore.wanted_at, JobRescore.user_id)
        )
    )


def claim(db: Session, user_id: uuid.UUID, now: datetime) -> int | None:
    """Mark the person's rescore running at `now`: the revision it covers, or None when nothing
    is asked for or one runs (step 1). The caller commits."""
    return db.scalar(
        sa.update(JobRescore)
        .where(JobRescore.user_id == user_id, _claimable(now))
        .values(running_revision=JobRescore.wanted_revision, running_since=now)
        .returning(JobRescore.running_revision)
    )


def finish(
    db: Session,
    user_id: uuid.UUID,
    since: datetime,
    revision: int,
    outcome: dict[str, Any],
    now: datetime,
) -> bool:
    """Step 3, for the claim made at `since` only. The caller commits."""
    done = db.execute(
        sa.update(JobRescore)
        .where(JobRescore.user_id == user_id, JobRescore.running_since == since)
        .values(
            done_revision=sa.func.greatest(JobRescore.done_revision, revision),
            running_revision=None,
            running_since=None,
            finished_at=now,
            outcome=outcome,
        )
    )
    return bool(getattr(done, "rowcount", 0))


def threshold_of(db: Session, user_id: uuid.UUID) -> int:
    """The documents threshold the outcome counts against (`search.documents.min_fit`)."""
    search = service.user_search(db, user_id)
    return (search.documents if search is not None else DocumentsGeneration()).min_fit


def run(
    sessions: sessionmaker[Session], user_id: uuid.UUID, clock: Callable[[], datetime]
) -> dict[str, Any]:
    """Step 2: rescore every posting of the person, and say what changed."""
    with sessions() as db:
        skills = service.user_skills(db, user_id)
        threshold = threshold_of(db, user_id)
    if skills is None:
        return {"no_skills": True, "threshold": threshold}
    outcome = service.RescoreOutcome()

    def batches(ids: list[uuid.UUID] | None) -> None:
        after = None
        while True:
            with sessions() as db:
                count = service.rescore_batch(
                    db,
                    user_id,
                    skills,
                    clock(),
                    ids=ids,
                    after=after,
                    limit=RESCORE_BATCH,
                    overwrite_manual=False,
                    outcome=outcome,
                    threshold=threshold,
                )
                db.commit()
            if count == 0:
                return
            after = outcome.last_id

    batches(None)
    if outcome.busy:
        held, outcome.busy = outcome.busy, []
        outcome.skipped_busy -= len(held)
        batches(held)
    return {
        "scored": outcome.scored,
        "changed": outcome.changed,
        "up": outcome.up,
        "down": outcome.down,
        "skipped_busy": outcome.skipped_busy,
        "threshold": threshold,
    }


def tick(
    sessions: sessionmaker[Session],
    clock: Callable[[], datetime],
    budget_s: float = TICK_BUDGET_S,
) -> int:
    """One pass over the people with a rescore asked for. Returns how many were done."""
    started = time.monotonic()
    with sessions() as db:
        people = due(db, clock())
    done = 0
    for user_id in people:
        if time.monotonic() - started > budget_s:
            break
        since = clock()
        with sessions() as db:
            revision = claim(db, user_id, since)
            db.commit()
        if revision is None:
            continue  # another tick claimed it, or it ran meanwhile
        try:
            outcome = run(sessions, user_id, clock)
        except Exception:
            logger.exception("jobs.rescore.failed", extra={"user_id": str(user_id)})
            outcome = {"failed": True}
        with sessions() as db:
            finished = finish(db, user_id, since, revision, outcome, clock())
            db.commit()
        if finished:
            done += 1
        logger.info(
            "jobs.rescore",
            extra={
                "user_id": str(user_id),
                "revision": revision,
                **{k: v for k, v in outcome.items() if isinstance(v, int)},
            },
        )
    return done


@dataclass(frozen=True)
class Status:
    state: str  # idle, waiting (asked, not started), running
    wanted_revision: int | None
    done_revision: int
    finished_at: datetime | None
    outcome: dict[str, Any]


def status(db: Session, user_id: uuid.UUID) -> Status:
    row = db.get(JobRescore, user_id)
    if row is None:
        return Status("idle", None, 0, None, {})
    if row.running_since is not None:
        state = "running"
    elif row.wanted_revision > row.done_revision:
        state = "waiting"
    else:
        state = "idle"
    return Status(state, row.wanted_revision, row.done_revision, row.finished_at, row.outcome)
