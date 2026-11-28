"""The career and culture reading (jobs J38, issue #876; SPEC-jobs.md, "Career and culture"): a
model reads, in batches, how each posting matches the career and the culture the person's profile
states, and the app stores that reading on the posting (`jobs.match`, migration 0055) and serves
it with `scoring.blend`. Nothing reads it to decide yet: the fit, the order of the lists, the
minimum fit and Full auto stay as they are. The two dimensions and their bands are adapted from
the evaluation rubric of ai-job-search (MIT, NOTICE).

The agent `jobs_match` (prompts/match.md) runs as the posting reader does (reader.py): `internal`
(started by the reader's backlog only), bound in code to at most BATCH_MAX postings of one person
(`bind_ratings`), `private_io`, on MODEL for both kinds of credential. Its two tools
(match_tools.py, worker only) take an index into the binding, never an id: `read_rating_batch`
shows the profile's parts (`profile_view`) and each bound posting with the text the reader is
shown, and `submit_ratings` checks and stores its rows (`submit`).

`jobs.match` holds the reading (`career`, `culture`, `strengths`, `gaps`, `rated_at`, and `text`,
the digest of the text read) and the rating's bookkeeping: `attempts` on the text `tried`, and
`run`, the run holding the last attempt until its end is settled (`settle`).

Which postings (`_ratable`), when and how many (`start`, the turn the reader's backlog gives
once it starts nothing): SPEC-jobs.md, "Career and culture". Logs carry ids and counts, never a
posting text, a score or a word.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agents import credentials, definitions, failures, runs
from app.agents.models import AgentRun, RunStatus
from app.config import get_settings
from app.jobs import assess, criteria_badge, reader, service
from app.jobs.models import Job, Sweep, SweepStatus
from app.profile import fit_summary
from app.profile.models import Profile
from app.profile.schemas import SearchSection, TermList

logger = logging.getLogger("app.jobs.match")

NAME = "jobs_match"
READ = "read_rating_batch"
SUBMIT = "submit_ratings"
MODEL = reader.MODEL
BATCH_MAX = reader.BATCH_MAX
ATTEMPTS_MAX = 3
MAX_TURNS = 8
WALL_TIME_S = 600.0
POINTS_MAX = 3
WORDS_MAX = 120  # characters of one strength or gap
FACTS_SHOWN_MAX = 30_000
INSTRUCTIONS_SHOWN_MAX = 2_000
PROMPT_PATH = Path(__file__).parent / "prompts" / "match.md"
FIRST_MESSAGE = (
    "Read the batch with read_rating_batch, submit a rating of every posting with "
    "submit_ratings, then answer with one line."
)

NO_INDEX = "no posting has this index"
TWICE = "already stored in this run"
INVALID = "not a valid row"
WORDS = "a strength or a gap is empty, too long or holds a character that is not plain text"
BUSY = "the posting is being changed"
GONE = "the posting no longer exists"
CHANGED = "the posting's text changed since this run began"


class MatchInput(BaseModel):
    """The input of one run: the ids of its postings, never a text."""

    model_config = ConfigDict(extra="forbid")

    job_ids: list[uuid.UUID] = Field(min_length=1, max_length=BATCH_MAX)
    script: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")


def _text_digest() -> sa.ColumnElement[str]:
    """`reader._digest` of the posting's text, computed by Postgres."""
    return sa.func.encode(sa.func.sha256(sa.func.convert_to(Job.posting_text, "UTF8")), "hex")


def _ratable(floor: int) -> sa.ColumnElement[bool]:
    """A posting to rate: open, its text read from its own page or a feed, no badge, a fit of at
    least `floor`, not rated for its current text, fewer than ATTEMPTS_MAX attempts on that text,
    and no run holding it."""
    digest = _text_digest()
    return sa.and_(
        Job.status.in_(criteria_badge.OPEN),
        Job.list_url.is_(None),
        reader._readable(),
        sa.func.coalesce(sa.func.cardinality(Job.outside_criteria), 0) == 0,
        Job.fit >= floor,
        Job.match["text"].astext.is_distinct_from(digest),
        sa.or_(
            Job.match["tried"].astext.is_distinct_from(digest),
            sa.func.coalesce(Job.match["attempts"].as_integer(), 0) < ATTEMPTS_MAX,
        ),
        sa.not_(sa.func.coalesce(Job.match.has_key("run"), False)),
    )


def _sweeping(user_id: uuid.UUID | None = None) -> sa.Select[tuple[uuid.UUID]]:
    query = sa.select(Sweep.user_id).where(Sweep.status == SweepStatus.running)
    return query if user_id is None else query.where(Sweep.user_id == user_id)


def bind_ratings(db: Session, run: AgentRun) -> Mapping[str, Any] | None:
    """What a run rates: `{"jobs": [id, ...], "texts": [digest, ...]}`, the postings of its input
    that are still the run's person's and hold a text, in the input's order, and the digest of
    each one's text as the run starts. None when none is left or a search of the person runs: the
    run then fails before any model call (and gives its attempts back, `settle`)."""
    try:
        given = MatchInput.model_validate(run.input or {})
    except ValidationError:
        return None
    if db.scalar(_sweeping(run.user_id).limit(1)) is not None:
        return None
    wanted = list(dict.fromkeys(given.job_ids))
    held = {
        row.id: reader._digest(row.posting_text or "")
        for row in db.execute(
            sa.select(Job.id, Job.posting_text).where(
                Job.user_id == run.user_id, Job.id.in_(wanted), reader._readable()
            )
        )
    }
    jobs = [job_id for job_id in wanted if job_id in held]
    if not jobs:
        return None
    return {"jobs": [str(job_id) for job_id in jobs], "texts": [held[job_id] for job_id in jobs]}


JOBS_MATCH = definitions.register(
    definitions.AgentDefinition(
        name=NAME,
        description="Reads how each posting matches the career and the culture your profile "
        "states, ten at a time, and stores two scores with its strengths and gaps.",
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=(READ, SUBMIT),
        input_model=MatchInput,
        model=MODEL,
        max_turns=MAX_TURNS,
        wall_time_s=WALL_TIME_S,
        effort="low",
        default_message=FIRST_MESSAGE,
        internal=True,
        bind=bind_ratings,
        private_io=True,
        live_text=False,
    )
)


# --- what the model is shown -----------------------------------------------------------------


def _search(data: Any) -> SearchSection:
    try:
        return SearchSection.model_validate(dict(data) if isinstance(data, Mapping) else {})
    except ValueError:
        return SearchSection()


def _terms(listed: TermList) -> list[str]:
    """A list's plain terms; an advanced one holds patterns, not words: none."""
    return [] if listed.advanced_regex else list(listed.terms)


def _facts(data: Any) -> str:
    """The fact base as `title` then `body` blocks, cut to FACTS_SHOWN_MAX characters."""
    blocks = data.get("blocks") if isinstance(data, Mapping) else None
    parts = [
        f"{block.get('title', '')}\n{block.get('body', '')}".strip()
        for block in (blocks if isinstance(blocks, list) else [])
        if isinstance(block, Mapping)
    ]
    return "\n\n".join(part for part in parts if part)[:FACTS_SHOWN_MAX]


def profile_view(sections: Mapping[str, Any]) -> dict[str, Any]:
    """The profile's parts a batch is rated against, in this order: `wants`, what the search
    section says the person wants (roles, seniority, industries, company types, remote stance,
    pay, the person's instructions to the search); `now`, where the person stands (years of
    experience, recent roles, fit_summary's); `facts`, the fact base, bounded. Empty parts are
    left out."""
    search = _search(sections.get("search"))
    pay = search.compensation.model_dump(mode="json", exclude_none=True)
    wants = {
        "roles": _terms(search.title_include),
        "seniority": list(search.seniority),
        "industries": _terms(search.industries),
        "company_types": list(search.company_types),
        "remote": search.remote_stance,
        "pay": pay,
        "instructions": (search.interactive_instructions or "")[:INSTRUCTIONS_SHOWN_MAX],
    }
    summary = fit_summary.summary(sections)
    now = {
        "years_experience": summary.get("years_experience"),
        "recent_roles": summary.get("recent_roles", []),
    }
    out = {
        "wants": {k: v for k, v in wants.items() if v not in (None, "", [], {})},
        "now": {k: v for k, v in now.items() if v not in (None, [])},
        "facts": _facts(sections.get("facts")),
    }
    return {k: v for k, v in out.items() if v}


def _ids(binding: Mapping[str, Any] | None) -> list[uuid.UUID]:
    return [uuid.UUID(str(one)) for one in (binding or {}).get("jobs", ())]


def _pay(job: Job) -> dict[str, Any] | None:
    if job.pay_min is None or job.pay_period is None:
        return None
    return {
        "min": job.pay_min,
        "max": job.pay_max,
        "currency": job.pay_currency,
        "period": job.pay_period.value,
    }


def batch(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    binding: Mapping[str, Any] | None,
    redact: Callable[[Any], Any],
) -> str:
    """What read_rating_batch answers: the profile's parts, then the bound postings by index with
    the text the posting reader is shown (`reader.shown`)."""
    ids = _ids(binding)
    with sessions() as db:
        sections = db.scalar(sa.select(Profile.sections).where(Profile.user_id == user_id))
        found = {
            job.id: job
            for job in db.scalars(
                service.jobs_of(user_id).where(Job.id.in_(ids)).options(undefer(Job.posting_text))
            )
        }
        listed = []
        for index, job_id in enumerate(ids):
            job = found.get(job_id)
            if job is None:
                continue
            text, shortened = reader.shown(job.posting_text or "", redact)
            posting = {
                "index": index,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "work_mode": job.work_mode.value if job.work_mode else None,
                "contract_type": job.contract_type.value,
                "pay": _pay(job),
                # Written by third parties: data for the model, never instructions.
                "untrusted_posting_text": text,
                "posting_text_truncated": shortened,
            }
            listed.append({k: v for k, v in posting.items() if v is not None})
    profile = redact(profile_view(sections if isinstance(sections, Mapping) else {}))
    return json.dumps({"profile": profile, "postings": listed}, ensure_ascii=False)


# --- the checks of what the model submits ----------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Row(_Strict):
    """One posting's rating, as submit_ratings takes it."""

    index: int = Field(ge=0, lt=BATCH_MAX)
    career: int = Field(ge=0, le=100)
    culture: int = Field(ge=0, le=100)
    strengths: list[str] = Field(min_length=1, max_length=POINTS_MAX)
    gaps: list[str] = Field(min_length=1, max_length=POINTS_MAX)


class Submission(_Strict):
    ratings: list[Any] = Field(min_length=1, max_length=BATCH_MAX)


_POINTS = {
    "type": "array",
    "minItems": 1,
    "maxItems": POINTS_MAX,
    "items": {"type": "string", "maxLength": WORDS_MAX},
}
_SCORE = {"type": "integer", "minimum": 0, "maximum": 100}
SUBMIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ratings": {
            "type": "array",
            "minItems": 1,
            "maxItems": BATCH_MAX,
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 0, "maximum": BATCH_MAX - 1},
                    "career": _SCORE,
                    "culture": _SCORE,
                    "strengths": _POINTS,
                    "gaps": _POINTS,
                },
                "required": ["index", "career", "culture", "strengths", "gaps"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ratings"],
    "additionalProperties": False,
}


def _words(points: list[str]) -> list[str] | None:
    """Each point as one line of plain text (`reader._line`) with the dash rule applied; None
    when one fails."""
    out = []
    for point in points:
        line = reader._line(point, WORDS_MAX)
        plain = assess._undash(line) if line is not None else None
        # A point of dashes alone is empty once undashed; a glued dash becomes ", ", one longer.
        if not plain or len(plain) > WORDS_MAX:
            return None
        out.append(plain)
    return out


@dataclass
class Submitted:
    text: str
    error: bool = False
    stored: list[int] = field(default_factory=list)
    dropped: dict[str, list[str]] = field(default_factory=dict)

    @property
    def refused(self) -> int:
        return sum(len(rows) for rows in self.dropped.values())

    def drop(self, why: str, row: object) -> None:
        index = row.get("index") if isinstance(row, Mapping) else None
        where = f"posting {index}" if isinstance(index, int) else "a row"
        self.dropped.setdefault(why, []).append(where)


def _answer(done: Submitted, size: int, before: frozenset[int]) -> str:
    parts = []
    if done.stored:
        parts.append(f"Stored: postings {', '.join(str(i) for i in sorted(done.stored))}.")
    for why, rows in done.dropped.items():
        parts.append(f"Dropped, {why}: {', '.join(rows)}.")
    left = sorted(set(range(size)) - before - set(done.stored))
    if left:
        parts.append(
            f"Not rated yet: postings {', '.join(str(i) for i in left)}. Submit them, "
            "fixing what was dropped."
        )
    else:
        parts.append("Every posting is rated: answer with one line.")
    return " ".join(parts)


def submit(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    binding: Mapping[str, Any] | None,
    raw: object,
    now: datetime,
    before: frozenset[int] = frozenset(),
) -> Submitted:
    """What submit_ratings does: each row checked alone (`Row`, the binding's index, the words),
    then stored in its own transaction under the posting's row lock when the posting is still
    the person's with the text the run was bound to; any other row dropped and counted. `before`
    holds the indexes this run already stored."""
    try:
        rows = Submission.model_validate(raw).ratings
    except ValidationError:
        return Submitted("Not stored: give ratings, a list of 1 to 10 rows. Fix it.", error=True)
    ids = _ids(binding)
    digests = list((binding or {}).get("texts", ()))
    done = Submitted("")
    for raw_row in rows:
        try:
            row = Row.model_validate(raw_row)
        except ValidationError:
            done.drop(INVALID, raw_row)
            continue
        if row.index >= len(ids) or row.index >= len(digests):
            done.drop(NO_INDEX, raw_row)
            continue
        if row.index in before or row.index in done.stored:
            done.drop(TWICE, raw_row)
            continue
        strengths, gaps = _words(row.strengths), _words(row.gaps)
        if strengths is None or gaps is None:
            done.drop(WORDS, raw_row)
            continue
        why = _store(
            sessions, user_id, ids[row.index], digests[row.index], row, strengths, gaps, now
        )
        if why:
            done.drop(why, raw_row)
        else:
            done.stored.append(row.index)
    done.text = _answer(done, len(ids), before)
    done.error = not done.stored
    return done


def _store(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    digest: str,
    row: Row,
    strengths: list[str],
    gaps: list[str],
    now: datetime,
) -> str | None:
    """One row's reading written on its posting, `updated_at` left as it is; why not, else None."""
    with sessions() as db:
        try:
            job = service.lock_job(db, user_id, job_id)
        except service.JobBusyError:
            return BUSY
        if job is None or not (job.posting_text or "").strip():
            return GONE
        if reader._digest(job.posting_text or "") != digest:
            return CHANGED
        held = dict(job.match) if isinstance(job.match, Mapping) else {}
        held.update(
            career=row.career,
            culture=row.culture,
            strengths=strengths,
            gaps=gaps,
            rated_at=now.isoformat(),
            text=digest,
        )
        db.execute(
            sa.update(Job)
            .where(Job.id == job.id)
            .values(match=held, updated_at=Job.updated_at)
            .execution_options(synchronize_session=False)
        )
        db.commit()
    return None


class Reading(BaseModel):
    """The reading a posting holds, as `_store` writes it."""

    career: int = Field(ge=0, le=100)
    culture: int = Field(ge=0, le=100)
    strengths: list[str] = Field(max_length=POINTS_MAX)
    gaps: list[str] = Field(max_length=POINTS_MAX)
    rated_at: datetime
    text: str


def reading(stored: Any) -> Reading | None:
    """The reading of a posting's `match`; None when it holds none or one that cannot be read."""
    if not isinstance(stored, Mapping) or "career" not in stored:
        return None
    try:
        return Reading.model_validate(stored)
    except ValidationError:
        return None


def is_current(read: Reading, text: str | None) -> bool:
    """Whether the reading is of `text`, the posting's text now."""
    return read.text == reader._digest(text or "")


# --- starting runs, settling them ------------------------------------------------------------


def _active(user_id: uuid.UUID | None = None) -> sa.Select[tuple[int]]:
    query = (
        sa.select(sa.func.count())
        .select_from(AgentRun)
        .where(AgentRun.agent == NAME, AgentRun.status.in_((RunStatus.queued, RunStatus.running)))
    )
    return query if user_id is None else query.where(AgentRun.user_id == user_id)


def active(db: Session) -> int:
    """The rating runs queued or running in the whole app."""
    return db.scalar(_active()) or 0


def _attempts(held: Mapping[str, Any]) -> int:
    count = held.get("attempts")
    return count if isinstance(count, int) and not isinstance(count, bool) else 0


def begin(
    db: Session,
    user_id: uuid.UUID,
    ids: list[uuid.UUID],
    now: datetime,
    floor: int,
    script: str | None = None,
) -> tuple[uuid.UUID, int] | str:
    """A queued run of the postings of `ids` still to rate and not held by another transaction,
    each counted as an attempt on its current text held by the run: its id and how many it
    holds, or why none was made ("no_credential", "busy", "none"). The caller commits."""
    if credentials.get(db, user_id) is None:
        return "no_credential"
    held = db.execute(
        sa.select(Job.id, Job.match, _text_digest().label("digest"))
        .where(Job.user_id == user_id, Job.id.in_(ids), _ratable(floor))
        .order_by(Job.id)
        .with_for_update(key_share=True, skip_locked=True)
    ).all()
    if not held:
        return "none"
    order = {job_id: at for at, job_id in enumerate(ids)}
    chosen = sorted(held, key=lambda row: order.get(row.id, len(order)))
    data = MatchInput(job_ids=[row.id for row in chosen], script=script).model_dump(
        mode="json", exclude_none=True
    )
    flow = runs.WorkflowRef("posting_rating", trigger="backlog")
    try:
        run = runs.create_run(db, user_id, NAME, None, data, MODEL, now, flow)
    except runs.ActiveRunsError:
        return "busy"
    for row in chosen:
        match = dict(row.match) if isinstance(row.match, Mapping) else {}
        tries = _attempts(match) if match.get("tried") == row.digest else 0
        match.update(attempts=tries + 1, tried=row.digest, run=str(run.id))
        db.execute(
            sa.update(Job)
            .where(Job.id == row.id)
            .values(match=match, updated_at=Job.updated_at)
            .execution_options(synchronize_session=False)
        )
    return run.id, len(chosen)


def gives_back(error: str | None) -> bool:
    """The reader's rule (`reader.gives_back`), and a run that ended at its binding: it made no
    model call (a search started, or its postings are gone)."""
    return reader.gives_back(error) or error == failures.BINDING_GONE


def settle(db: Session, run_id: uuid.UUID) -> str | None:
    """Once a rating run ended, its postings let go of it, each not rated for the text tried
    getting its attempt back when `gives_back`. Returns `reader.failed_for` of a failed run, else
    None (a run not ended yet changes nothing). The caller commits."""
    row = db.execute(
        sa.select(AgentRun.status, AgentRun.error).where(AgentRun.id == run_id)
    ).one_or_none()
    if row is None or row.status in (RunStatus.queued, RunStatus.running):
        return None
    failed = row.status is RunStatus.failed
    back = failed and gives_back(row.error)
    for job_id, held in db.execute(
        sa.select(Job.id, Job.match)
        .where(Job.match["run"].astext == str(run_id))
        .with_for_update(key_share=True)
    ).all():
        match = dict(held)
        match.pop("run", None)
        if back and match.get("text") != match.get("tried") and _attempts(match) > 0:
            match["attempts"] = _attempts(match) - 1
        db.execute(
            sa.update(Job)
            .where(Job.id == job_id)
            .values(match=match, updated_at=Job.updated_at)
            .execution_options(synchronize_session=False)
        )
    return reader.failed_for(row.error) if failed else None


def settle_ended(db: Session) -> int:
    """`settle` every rating run that ended and still holds a posting (a worker that stopped
    under it included). The caller commits."""
    holding = sa.select(Job.match["run"].astext).where(Job.match.has_key("run"))
    ended = list(
        db.scalars(
            sa.select(AgentRun.id).where(
                AgentRun.agent == NAME,
                sa.cast(AgentRun.id, sa.Text).in_(holding),
                AgentRun.status.not_in((RunStatus.queued, RunStatus.running)),
            )
        )
    )
    for run_id in ended:
        settle(db, run_id)
    return len(ended)


def paused(db: Session, user_id: uuid.UUID, now: datetime) -> bool:
    """Whether the person's last rating run failed for the credential, the provider or the
    worker less than reader.BACKLOG_PAUSE before `now` (the reader's own pause is read beside)."""
    last = db.execute(
        sa.select(AgentRun.status, AgentRun.error, AgentRun.finished_at, AgentRun.created_at)
        .where(AgentRun.agent == NAME, AgentRun.user_id == user_id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
    ).one_or_none()
    if last is None or last.status is not RunStatus.failed or reader.failed_for(last.error) is None:
        return False
    return now - (last.finished_at or last.created_at) < reader.BACKLOG_PAUSE


def given(db: Session, user_id: uuid.UUID, now: datetime) -> int:
    """The postings given to the person's rating runs in the 24 hours before `now`."""
    return (
        db.scalar(
            sa.select(
                sa.func.coalesce(
                    sa.func.sum(sa.func.jsonb_array_length(AgentRun.input["job_ids"])), 0
                )
            ).where(
                AgentRun.agent == NAME,
                AgentRun.user_id == user_id,
                AgentRun.created_at > now - timedelta(hours=24),
            )
        )
        or 0
    )


def start(
    db: Session, now: datetime, floor: int | None = None, daily: int | None = None
) -> list[uuid.UUID]:
    """The rating run to queue now, called by `reader.start_backlog` under its lock once the
    reader started nothing and no run of either agent is active: one batch of the person whose
    last rating is the oldest, among those with a posting to rate, no posting the reader still
    has to read, no search running, a credential, no pause and room in the day. `floor` and
    `daily` default to the settings. The caller commits, then queues it."""
    if floor is None or daily is None:
        settings = get_settings()
        floor = settings.jobs_match_fit_floor if floor is None else floor
        daily = settings.jobs_match_daily_max if daily is None else daily
    if daily <= 0:
        return []
    last = (
        sa.select(sa.func.max(AgentRun.created_at))
        .where(AgentRun.agent == NAME, AgentRun.user_id == Job.user_id)
        .scalar_subquery()
    )
    unread = sa.select(Job.user_id).where(reader._unread())
    people = db.execute(
        sa.select(Job.user_id, sa.func.min(last).label("last"))
        .where(_ratable(floor), Job.user_id.not_in(_sweeping()), Job.user_id.not_in(unread))
        .group_by(Job.user_id)
        .order_by(sa.nulls_first(sa.func.min(last).asc()), Job.user_id)
    ).all()
    for person, _ in people:
        if (
            (db.scalar(_active(person)) or 0) > 0
            or reader.paused(db, person, now)
            or paused(db, person, now)
        ):
            continue
        room = min(BATCH_MAX, daily - given(db, person, now))
        if room <= 0:
            continue
        ids = list(
            db.scalars(
                sa.select(Job.id)
                .where(Job.user_id == person, _ratable(floor))
                .order_by(Job.found_on.desc(), Job.created_at.desc(), Job.id)
                .limit(room)
            )
        )
        made = begin(db, person, ids, now, floor)
        if isinstance(made, str):
            continue
        logger.info(
            "postings given to a rating",
            extra={"user_id": str(person), "run_id": str(made[0]), "postings": made[1]},
        )
        return [made[0]]
    return []


if importlib.util.find_spec("claude_agent_sdk") is not None:
    # The worker, which runs the tools: importing them registers them with the runner.
    from app.jobs import match_tools as _tools  # noqa: F401
