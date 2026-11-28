"""The pass over the postings stored before jobs J39 (issue #877; SPEC-jobs.md, "Text as a reader
sees it"): their texts become what a store writes now, by the same function
(app/jobs/visible_text.py), and an Arbeitnow posting stored with its HTML left in is converted
again (feeds/arbeitnow.py, `again`), with nobody's click.

The rescore's tick (rescore.py, a cron of the sweeps queue every TICK_SECONDS) gives it SLICE_S
after the rescores asked for. `step` walks every posting in id order, SCAN_ROWS at a time (a plain
read, no lock), and rewrites the ones whose texts change, one posting per transaction:
- the row is locked with SKIP LOCKED and read again under the lock; a row another transaction
  holds, one a posting reader's or a rating's run is bound to (`read_run_id`, `match.run`), or one
  of a person whose search is running, is left for now (`State.held`) and tried again once the
  walk ended;
- the texts are written by a statement that keeps `updated_at`, with what a store derives from
  the text computed again from the new one: the signals, the fingerprint and the closing date
  (`signals.stored`), and the fit with its competencies and gaps (a fit set by hand kept, its
  competencies and gaps refreshed, as a rescore does). No History event; the reader's `read_at`
  and attempts, the status and every other column stay. The career and culture reading
  (match.py) is tied to the text's digest, so a changed text is rated again.

A posting is done when its texts are what a store writes now: the walk then writes nothing for
it. The worker remembers that a walk ended with nothing left held (`State.done`) and walks no
more until it restarts; after a restart it walks once more and writes nothing. Logs carry counts
only.
"""

import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.jobs import scoring, service, signals
from app.jobs.feeds import arbeitnow
from app.jobs.models import VISIBLE_COLUMNS, Job, Sweep, SweepStatus
from app.jobs.visible_text import visible
from app.profile.schemas import SearchSection, SkillsSection

logger = logging.getLogger("app.jobs.visible_pass")

SLICE_S = 5.0  # of each rescore tick, after the rescores asked for
SCAN_ROWS = 50  # texts of at most 200,000 characters each: 10 million at most in memory


@dataclass
class State:
    """Where this worker's walk is: the last id read, the postings left for now, whether it
    ended with none left."""

    after: uuid.UUID | None = None
    walked: bool = False
    held: set[uuid.UUID] = field(default_factory=set)
    done: bool = False


_STATE = State()


def texts_of(row: Any) -> dict[str, Any]:
    """The texts a store writes now for a stored posting (`row`: its five texts and its
    source), only those that differ from the stored ones."""
    new = {
        name: visible(value) if isinstance(value := getattr(row, name), str) else value
        for name in VISIBLE_COLUMNS
    }
    text = new["posting_text"]
    if text and arbeitnow.is_its(row.source):
        converted = visible(arbeitnow.again(text))
        if converted != text:
            # As the feed stores a posting: its description is the head of its text.
            new["posting_text"] = converted
            new["description"] = converted[: service.DESCRIPTION_CHARS] or None
    return {name: value for name, value in new.items() if value != getattr(row, name)}


@dataclass
class _Person:
    skills: SkillsSection | None
    search: SearchSection | None


def _derived(job: Job, texts: dict[str, Any], person: _Person) -> dict[str, Any]:
    """What a store derives from the new texts: the signals, the fingerprint and the closing
    date of a changed text, and the fit of a changed text or title (`service.computed_score`)."""
    derived: dict[str, Any] = {}
    text = texts.get("posting_text", job.posting_text)
    if "posting_text" in texts:
        derived.update(signals.stored(text))
    if not texts.keys() & {"posting_text", "title"} or person.skills is None:
        return derived
    if text is None or not text.strip():
        return derived
    engine = scoring.engine_text(text, (job.legacy or {}).get("posting_header"))
    title = texts.get("title", job.title)
    result = scoring.score(engine, person.skills, title=title, search=person.search)
    derived["competencies"], derived["gaps"] = list(result.competencies), list(result.gaps)
    if not job.fit_overridden:
        derived["fit"] = result.fit
    return derived


def _searching(db: Session, user_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            sa.select(Sweep.id)
            .where(Sweep.user_id == user_id, Sweep.status == SweepStatus.running)
            .limit(1)
        )
        is not None
    )


def _bound(job: Job) -> bool:
    """A posting reader's or a rating's run holds it until its end is settled."""
    return job.read_run_id is not None or (isinstance(job.match, dict) and "run" in job.match)


def clean(
    sessions: sessionmaker[Session],
    ids: Sequence[uuid.UUID],
    held: set[uuid.UUID],
    people: dict[uuid.UUID, _Person] | None = None,
) -> int:
    """Rewrite these postings' texts, one transaction each (module docstring); those left for
    now join `held`. A posting whose rewrite raises is logged and left to the next walk, the
    others go on. Returns how many were written."""
    people = {} if people is None else people
    written = 0
    for job_id in ids:
        try:
            written += _clean_one(sessions, job_id, held, people)
        except Exception:
            logger.exception("jobs.visible_pass.posting_failed", extra={"job_id": str(job_id)})
    return written


def _clean_one(
    sessions: sessionmaker[Session],
    job_id: uuid.UUID,
    held: set[uuid.UUID],
    people: dict[uuid.UUID, _Person],
) -> int:
    with sessions() as db:
        job = db.execute(
            sa.select(Job)
            .where(Job.id == job_id)
            .options(undefer(Job.posting_text))
            .with_for_update(key_share=True, skip_locked=True)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if job is None:
            if db.scalar(sa.select(Job.id).where(Job.id == job_id)) is not None:
                held.add(job_id)  # another transaction holds it
            return 0
        texts = texts_of(job)
        if not texts:
            return 0
        if _bound(job) or _searching(db, job.user_id):
            held.add(job_id)
            return 0
        if job.user_id not in people:
            people[job.user_id] = _Person(
                service.user_skills(db, job.user_id), service.user_search(db, job.user_id)
            )
        values = {**texts, **_derived(job, texts, people[job.user_id])}
        db.execute(
            sa.update(Job)
            .where(Job.id == job_id)
            .values(**values, updated_at=Job.updated_at)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return 1


def _scan(
    db: Session, after: uuid.UUID | None, rows: int
) -> tuple[uuid.UUID | None, list[uuid.UUID]]:
    """The last id of the next `rows` postings after `after`, and those of them to rewrite."""
    query = sa.select(Job.id, Job.source, *(getattr(Job, n) for n in VISIBLE_COLUMNS))
    if after is not None:
        query = query.where(Job.id > after)
    found = db.execute(query.order_by(Job.id).limit(rows)).all()
    if not found:
        return None, []
    return found[-1].id, [row.id for row in found if texts_of(row)]


def step(
    sessions: sessionmaker[Session],
    budget_s: float = SLICE_S,
    *,
    state: State | None = None,
    scan_rows: int = SCAN_ROWS,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """One slice of the walk (module docstring). Returns how many postings it rewrote."""
    state = _STATE if state is None else state
    if state.done:
        return 0
    started = monotonic()
    written = 0
    people: dict[uuid.UUID, _Person] = {}
    while not state.done and monotonic() - started < budget_s:
        if not state.walked:
            with sessions() as db:
                last, dirty = _scan(db, state.after, scan_rows)
            if last is None:
                state.walked = True
                continue
            state.after = last
            written += clean(sessions, dirty, state.held, people)
            continue
        if not state.held:
            state.done = True
            break
        again = sorted(state.held)
        state.held.clear()
        written += clean(sessions, again, state.held, people)
        if state.held:
            break  # still held: the next tick tries them again
    if written or state.done:
        logger.info(
            "jobs.visible_pass",
            extra={"written": written, "held": len(state.held), "done": state.done},
        )
    return written
